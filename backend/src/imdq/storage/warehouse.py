"""Physical storage of ingested blocks, and the ingest orchestrator.

Every physical table carries provenance (`_dataset_id`, `_as_of_date`,
`_source_row`, `_row_hash`) so any figure the chatbot reports can be traced back
to the exact cell range and worksheet row it came from.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from imdq.errors import IngestFailed
from imdq.ingest.normalize import ColumnRole, ColumnSpec
from imdq.ingest.pipeline import ExtractionRecipe, FilePlan, analyse, stream
from imdq.logging_setup import get_logger
from imdq.storage import catalog
from imdq.storage.engine import SqlEngine

log = get_logger(__name__)

#: Added by the warehouse. ``_source_row`` is supplied by the pipeline as the
#: final data column, so it is deliberately absent here.
META_COLUMNS: list[tuple[str, str]] = [
    ("_dataset_id", "VARCHAR"),
    ("_as_of_date", "VARCHAR"),
    ("_row_hash", "VARCHAR"),
]
_SAFE_IDENT = re.compile(r"[^a-z0-9_]")


@dataclass(slots=True)
class TableReport:
    table_id: str
    physical_name: str
    sheet: str
    kind: str
    cell_range: str
    rows_written: int
    confidence: float
    context: dict[str, Any]
    checksums: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IngestReport:
    dataset_id: str
    filename: str
    as_of_date: str
    layout_fingerprint: str
    reused_layout: bool
    already_ingested: bool
    tables: list[TableReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_rows(self) -> int:
        return sum(t.rows_written for t in self.tables)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "filename": self.filename,
            "as_of_date": self.as_of_date,
            "layout_fingerprint": self.layout_fingerprint,
            "reused_layout": self.reused_layout,
            "already_ingested": self.already_ingested,
            "total_rows": self.total_rows,
            "warnings": list(self.warnings),
            "tables": [
                {
                    "table_id": t.table_id,
                    "sheet": t.sheet,
                    "kind": t.kind,
                    "range": t.cell_range,
                    "rows": t.rows_written,
                    "confidence": round(t.confidence, 3),
                    "context": t.context,
                    "checksums": t.checksums,
                    "warnings": t.warnings,
                }
                for t in self.tables
            ],
        }


def safe_identifier(*parts: str) -> str:
    joined = "_".join(_SAFE_IDENT.sub("_", p.lower()) for p in parts if p)
    return re.sub(r"_+", "_", joined).strip("_")[:60] or "t"


def physical_table_name(dataset_id: str, recipe: ExtractionRecipe) -> str:
    return safe_identifier("f", dataset_id[:8], recipe.block_id)


def _create_physical_table(
    engine: SqlEngine, name: str, recipe: ExtractionRecipe, data_columns: list[str]
) -> None:
    types = {spec.slug: spec.sql_type for spec in recipe.stored_columns}
    types.update(
        {
            "period": "VARCHAR", "period_month": "BIGINT", "value": "DOUBLE",
            "is_trace": "BOOLEAN", "_source_row": "BIGINT",
        }
    )
    definitions = [f'"{c}" {types.get(c, "VARCHAR")}' for c in data_columns]
    definitions += [f'"{c}" {t}' for c, t in META_COLUMNS]
    engine.execute(f'DROP TABLE IF EXISTS "{name}"')
    engine.execute(f'CREATE TABLE "{name}" ({", ".join(definitions)})')


def physical_specs(recipe: ExtractionRecipe, data_columns: list[str]) -> list[ColumnSpec]:
    """Describe the table as it is actually stored, not as it appeared on the sheet.

    After melting, ``jan``..``dec`` no longer exist as columns -- registering them
    would let the planner emit SQL against columns that are not there.
    """
    by_slug = {spec.slug: spec for spec in recipe.stored_columns}
    period_units = [s.unit for s in recipe.stored_columns if s.period_label and s.unit]
    melted_unit = period_units[0] if period_units else recipe.context.get("units")

    synthetic = {
        "period": ColumnSpec("period", "period", ColumnRole.DIMENSION),
        "period_month": ColumnSpec("period_month", "period_month", ColumnRole.TIME,
                                   sql_type="BIGINT"),
        "value": ColumnSpec("value", "value", ColumnRole.MEASURE,
                            unit=str(melted_unit) if melted_unit else None,
                            sql_type="DOUBLE"),
    }
    out: list[ColumnSpec] = []
    for column in data_columns:
        if column in ("is_trace", "_source_row"):
            continue
        spec = by_slug.get(column) or synthetic.get(column)
        if spec is not None:
            out.append(spec)
    return out


def _row_hash(row: tuple[Any, ...]) -> str:
    return hashlib.sha1("\x1f".join(str(v) for v in row).encode()).hexdigest()[:16]


def ingest_file(
    engine: SqlEngine,
    path: Path,
    *,
    as_of: date | None = None,
    probe_rows: int = 2_000,
) -> IngestReport:
    """Analyse a workbook, then stream every detected block into the warehouse.

    Re-uploading a byte-identical file is a no-op. Re-uploading a corrected file
    for the same day supersedes the previous version rather than duplicating it.
    """
    catalog.init_catalog(engine)
    plan: FilePlan = analyse(path, probe_rows=probe_rows)
    as_of = as_of or date.today()

    existing = catalog.find_dataset_by_hash(engine, plan.content_hash)
    if existing:
        log.info("ingest skipped, identical content already present")
        return IngestReport(
            dataset_id=existing["dataset_id"],
            filename=plan.filename,
            as_of_date=existing["as_of_date"],
            layout_fingerprint=plan.layout_fingerprint,
            reused_layout=True,
            already_ingested=True,
            warnings=["Identical file already ingested; no rows were written."],
        )

    known_layout = engine.scalar(
        "SELECT 1 FROM cat_dataset WHERE layout_fingerprint = ? LIMIT 1",
        (plan.layout_fingerprint,),
    )
    version = catalog.next_ingest_version(engine, plan.filename, as_of.isoformat())
    dataset_id = catalog.register_dataset(engine, plan, as_of, version)

    report = IngestReport(
        dataset_id=dataset_id,
        filename=plan.filename,
        as_of_date=as_of.isoformat(),
        layout_fingerprint=plan.layout_fingerprint,
        reused_layout=bool(known_layout),
        already_ingested=False,
    )

    for sheet in plan.sheets:
        if sheet.truncated_probe:
            report.warnings.append(
                f"Sheet '{sheet.sheet}' exceeds the probe window; layout was inferred "
                f"from the first {probe_rows} rows."
            )
        for recipe in sheet.recipes:
            report.tables.append(
                _ingest_block(engine, path, dataset_id, as_of, recipe)
            )

    if not report.tables:
        raise IngestFailed(
            "No blocks could be written.", remedy="Inspect the analysis report for this file."
        )
    return report


def _ingest_block(
    engine: SqlEngine,
    path: Path,
    dataset_id: str,
    as_of: date,
    recipe: ExtractionRecipe,
) -> TableReport:
    name = physical_table_name(dataset_id, recipe)
    cell_range = f"row {recipe.header_row + 1}, cols {recipe.first_col + 1}-{recipe.last_col + 1}"
    written = 0
    data_columns: list[str] = []
    running_totals: dict[str, float] = {}

    for batch in stream(path, recipe):
        if not data_columns:
            data_columns = list(batch.columns)
            _create_physical_table(engine, name, recipe, data_columns)
        rows = [
            (*row, dataset_id, as_of.isoformat(), _row_hash(row)) for row in batch.rows
        ]
        engine.insert_many(name, data_columns + [c for c, _ in META_COLUMNS], rows)
        written += len(batch.rows)
        for index, column in enumerate(data_columns):
            if column in recipe.stated_totals:
                running_totals[column] = running_totals.get(column, 0.0) + sum(
                    r[index] for r in batch.rows if isinstance(r[index], float)
                )

    if not data_columns:
        data_columns = [s.slug for s in recipe.stored_columns] + ["is_trace", "_source_row"]
        _create_physical_table(engine, name, recipe, data_columns)

    table_id = catalog.register_table(
        engine, dataset_id, recipe, name, cell_range, physical_specs(recipe, data_columns)
    )
    catalog.set_row_count(engine, table_id, dataset_id, written)

    checksums: dict[str, bool] = {}
    warnings: list[str] = []
    for slug, stated in recipe.stated_totals.items():
        parsed = running_totals.get(slug)
        if parsed is None:
            continue
        agrees = catalog.record_checksum(engine, table_id, slug, stated, parsed)
        checksums[slug] = agrees
        if not agrees:
            warnings.append(
                f"Checksum mismatch on '{slug}': sheet states {stated:g}, "
                f"parsed rows sum to {parsed:g}."
            )
    if recipe.confidence < 0.5:
        warnings.append("Low layout confidence; review this block before relying on it.")

    return TableReport(
        table_id=table_id,
        physical_name=name,
        sheet=recipe.sheet,
        kind=str(recipe.kind),
        cell_range=cell_range,
        rows_written=written,
        confidence=recipe.confidence,
        context=dict(recipe.context),
        checksums=checksums,
        warnings=warnings,
    )
