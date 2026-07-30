"""Warehouse metadata: what was ingested, what shape it has, and where it came from.

This is the only thing the query planner reads. It never touches the data
itself, which is what keeps schema retrieval cheap and keeps observations out of
any prompt that might later be sent to a model.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from imdq.errors import NotFound
from imdq.ingest.pipeline import ExtractionRecipe, FilePlan
from imdq.storage.engine import SqlEngine

CATALOG_DDL = (
    """
    CREATE TABLE IF NOT EXISTS cat_dataset (
        dataset_id          VARCHAR PRIMARY KEY,
        filename            VARCHAR NOT NULL,
        content_hash        VARCHAR NOT NULL,
        layout_fingerprint  VARCHAR NOT NULL,
        as_of_date          VARCHAR NOT NULL,
        ingest_version      BIGINT  NOT NULL,
        ingested_at         VARCHAR NOT NULL,
        row_count           BIGINT  NOT NULL DEFAULT 0,
        status              VARCHAR NOT NULL DEFAULT 'active'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cat_table (
        table_id        VARCHAR PRIMARY KEY,
        dataset_id      VARCHAR NOT NULL,
        sheet           VARCHAR NOT NULL,
        block_id        VARCHAR NOT NULL,
        kind            VARCHAR NOT NULL,
        physical_name   VARCHAR NOT NULL,
        cell_range      VARCHAR,
        row_count       BIGINT NOT NULL DEFAULT 0,
        confidence      DOUBLE NOT NULL DEFAULT 0,
        context_json    VARCHAR NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cat_column (
        table_id        VARCHAR NOT NULL,
        ordinal         BIGINT  NOT NULL,
        name            VARCHAR NOT NULL,
        slug            VARCHAR NOT NULL,
        role            VARCHAR NOT NULL,
        sql_type        VARCHAR NOT NULL,
        unit            VARCHAR,
        period_label    VARCHAR,
        is_derived      BOOLEAN NOT NULL DEFAULT FALSE,
        derived_of      VARCHAR NOT NULL DEFAULT '[]',
        null_fraction   DOUBLE  NOT NULL DEFAULT 0,
        distinct_sample VARCHAR NOT NULL DEFAULT '[]'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cat_checksum (
        table_id      VARCHAR NOT NULL,
        column_slug   VARCHAR NOT NULL,
        stated_total  DOUBLE,
        parsed_total  DOUBLE,
        agrees        BOOLEAN NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_table_dataset ON cat_table (dataset_id)",
    "CREATE INDEX IF NOT EXISTS ix_column_table ON cat_column (table_id)",
    "CREATE INDEX IF NOT EXISTS ix_dataset_hash ON cat_dataset (content_hash)",
)


@dataclass(slots=True)
class TableInfo:
    table_id: str
    dataset_id: str
    filename: str
    sheet: str
    kind: str
    physical_name: str
    row_count: int
    as_of_date: str
    context: dict[str, Any]
    columns: list[dict[str, Any]]

    def measures(self) -> list[dict[str, Any]]:
        return [c for c in self.columns if c["role"] == "measure" and not c["is_derived"]]

    def dimensions(self) -> list[dict[str, Any]]:
        return [c for c in self.columns if c["role"] in ("dimension", "identifier")]

    def time_columns(self) -> list[dict[str, Any]]:
        return [c for c in self.columns if c["role"] == "time"]


def init_catalog(engine: SqlEngine) -> None:
    for statement in CATALOG_DDL:
        engine.execute(statement)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def find_dataset_by_hash(engine: SqlEngine, content_hash: str) -> dict[str, Any] | None:
    rows = engine.fetch_dicts(
        "SELECT * FROM cat_dataset WHERE content_hash = ? AND status = 'active'",
        (content_hash,),
    )
    return rows[0] if rows else None


def next_ingest_version(engine: SqlEngine, filename: str, as_of: str) -> int:
    current = engine.scalar(
        "SELECT MAX(ingest_version) FROM cat_dataset WHERE filename = ? AND as_of_date = ?",
        (filename, as_of),
    )
    return int(current or 0) + 1


def register_dataset(engine: SqlEngine, plan: FilePlan, as_of: date, ingest_version: int) -> str:
    dataset_id = uuid.uuid4().hex[:16]
    engine.execute(
        """
        INSERT INTO cat_dataset
            (dataset_id, filename, content_hash, layout_fingerprint,
             as_of_date, ingest_version, ingested_at, row_count, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'active')
        """,
        (
            dataset_id,
            plan.filename,
            plan.content_hash,
            plan.layout_fingerprint,
            as_of.isoformat(),
            ingest_version,
            _now(),
        ),
    )
    # A corrected re-send supersedes the earlier version for the same day.
    engine.execute(
        """
        UPDATE cat_dataset SET status = 'superseded'
        WHERE filename = ? AND as_of_date = ? AND ingest_version < ? AND status = 'active'
        """,
        (plan.filename, as_of.isoformat(), ingest_version),
    )
    return dataset_id


def register_table(
    engine: SqlEngine,
    dataset_id: str,
    recipe: ExtractionRecipe,
    physical_name: str,
    cell_range: str,
    specs: list[Any],
) -> str:
    table_id = uuid.uuid4().hex[:16]
    engine.execute(
        """
        INSERT INTO cat_table
            (table_id, dataset_id, sheet, block_id, kind, physical_name,
             cell_range, row_count, confidence, context_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
        """,
        (
            table_id,
            dataset_id,
            recipe.sheet,
            recipe.block_id,
            str(recipe.kind),
            physical_name,
            cell_range,
            recipe.confidence,
            json.dumps(recipe.context, default=str),
        ),
    )
    rows = [
        (
            table_id,
            ordinal,
            spec.source_name,
            spec.slug,
            str(spec.role),
            spec.sql_type,
            spec.unit,
            spec.period_label,
            spec.is_derived,
            json.dumps(spec.derived_of),
            spec.null_fraction,
            json.dumps(spec.distinct_sample[:20], default=str),
        )
        for ordinal, spec in enumerate(specs)
    ]
    engine.insert_many(
        "cat_column",
        [
            "table_id",
            "ordinal",
            "name",
            "slug",
            "role",
            "sql_type",
            "unit",
            "period_label",
            "is_derived",
            "derived_of",
            "null_fraction",
            "distinct_sample",
        ],
        rows,
    )
    return table_id


def set_row_count(engine: SqlEngine, table_id: str, dataset_id: str, count: int) -> None:
    engine.execute("UPDATE cat_table SET row_count = ? WHERE table_id = ?", (count, table_id))
    engine.execute(
        "UPDATE cat_dataset SET row_count = row_count + ? WHERE dataset_id = ?",
        (count, dataset_id),
    )


def record_checksum(
    engine: SqlEngine, table_id: str, column_slug: str, stated: float, parsed: float
) -> bool:
    agrees = abs(stated - parsed) <= max(0.01, abs(stated) * 1e-6)
    engine.insert_many(
        "cat_checksum",
        ["table_id", "column_slug", "stated_total", "parsed_total", "agrees"],
        [(table_id, column_slug, stated, parsed, agrees)],
    )
    return agrees


def list_tables(
    engine: SqlEngine,
    dataset_id: str | None = None,
    table_ids: list[str] | None = None,
) -> list[TableInfo]:
    clause = "WHERE d.status = 'active'"
    params: list[Any] = []
    if dataset_id:
        clause += " AND t.dataset_id = ?"
        params.append(dataset_id)
    if table_ids:
        clause += f" AND t.table_id IN ({', '.join('?' for _ in table_ids)})"
        params.extend(table_ids)

    rows = engine.fetch_dicts(
        f"""
        SELECT t.table_id, t.dataset_id, d.filename, t.sheet, t.kind, t.physical_name,
               t.row_count, d.as_of_date, t.context_json
        FROM cat_table t JOIN cat_dataset d ON d.dataset_id = t.dataset_id
        {clause}
        ORDER BY d.ingested_at DESC, t.sheet, t.block_id
        """,
        params,
    )
    if not rows:
        return []

    wanted = [row["table_id"] for row in rows]
    columns = engine.fetch_dicts(
        f"""
        SELECT * FROM cat_column
        WHERE table_id IN ({", ".join("?" for _ in wanted)})
        ORDER BY table_id, ordinal
        """,
        wanted,
    )
    by_table: dict[str, list[dict[str, Any]]] = {}
    for column in columns:
        column["is_derived"] = bool(column["is_derived"])
        column["derived_of"] = json.loads(column["derived_of"])
        column["distinct_sample"] = json.loads(column["distinct_sample"])
        by_table.setdefault(column["table_id"], []).append(column)

    return [
        TableInfo(
            table_id=row["table_id"],
            dataset_id=row["dataset_id"],
            filename=row["filename"],
            sheet=row["sheet"],
            kind=row["kind"],
            physical_name=row["physical_name"],
            row_count=row["row_count"],
            as_of_date=row["as_of_date"],
            context=json.loads(row["context_json"]),
            columns=by_table.get(row["table_id"], []),
        )
        for row in rows
    ]


def get_table(engine: SqlEngine, table_id: str) -> TableInfo:
    """Direct lookup. The previous implementation listed every table and scanned,
    which loaded the whole column catalog to answer one question."""
    tables = list_tables(engine, table_ids=[table_id])
    if not tables:
        raise NotFound(
            f"No table with id {table_id!r}.", remedy="Call /catalog/tables to list ids."
        )
    return tables[0]


def catalog_version(engine: SqlEngine) -> str:
    """Cheap fingerprint of catalog state, used to invalidate query caches.

    Any ingest or retirement changes it, so a cached answer can never outlive the
    data it was computed from.
    """
    row = engine.fetch_dicts(
        "SELECT COUNT(*) AS n, COALESCE(MAX(ingested_at), '') AS latest, "
        "COALESCE(SUM(row_count), 0) AS rows FROM cat_dataset WHERE status = 'active'"
    )[0]
    return f"{row['n']}:{row['latest']}:{row['rows']}"


def clear_all(engine: SqlEngine) -> int:
    """Drop every physical table and empty the catalog. Used by the Clear action."""
    names = [
        row["physical_name"]
        for row in engine.fetch_dicts("SELECT DISTINCT physical_name FROM cat_table")
    ]
    for name in names:
        engine.execute(f'DROP TABLE IF EXISTS "{name}"')
    for table in ("cat_checksum", "cat_column", "cat_table", "cat_dataset"):
        engine.execute(f"DELETE FROM {table}")
    return len(names)


def dataset_summary(engine: SqlEngine) -> list[dict[str, Any]]:
    return engine.fetch_dicts(
        """
        SELECT dataset_id, filename, as_of_date, ingested_at, row_count,
               layout_fingerprint
        FROM cat_dataset WHERE status = 'active'
        ORDER BY ingested_at DESC
        """
    )
