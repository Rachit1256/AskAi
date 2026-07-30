"""Per-table statistics and plain-language observations.

This is what fills the dashboard's statistics and insight panels. Everything
here is computed in SQL from the stored rows -- there is no generation step, so a
figure shown in a panel is the same figure a query would return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from imdq.domain.imd import RAINFALL_DAY_NOTE, season_for_month
from imdq.storage.catalog import TableInfo
from imdq.storage.engine import SqlEngine

TOP_CATEGORIES = 8

#: Units are stored in an ASCII-safe form and rendered for reading here.
UNIT_TEXT = {"degC": "\u00b0C", "percent": "%", "kmph": "km/h"}


@dataclass(slots=True)
class MeasureStats:
    slug: str
    name: str
    unit: str | None
    count: int
    missing: int
    total: float | None
    mean: float | None
    median: float | None
    minimum: float | None
    maximum: float | None
    trace_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "unit": self.unit,
            "count": self.count,
            "missing": self.missing,
            "sum": self.total,
            "average": self.mean,
            "median": self.median,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "trace_rows": self.trace_rows,
        }


@dataclass(slots=True)
class TableProfile:
    table: TableInfo
    measures: list[MeasureStats] = field(default_factory=list)
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    checksums: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table.table_id,
            "filename": self.table.filename,
            "sheet": self.table.sheet,
            "kind": self.table.kind,
            "rows": self.table.row_count,
            "as_of_date": self.table.as_of_date,
            "context": self.table.context,
            "columns": [c["name"] for c in self.table.columns],
            "statistics": {m.name: m.to_dict() for m in self.measures},
            "dimensions": self.dimensions,
            "summary": self.summary,
            "observations": self.observations,
            "checksums": self.checksums,
        }


def _median(engine: SqlEngine, physical: str, slug: str, count: int) -> float | None:
    """Median without a percentile function, so DuckDB and SQLite agree."""
    if count == 0:
        return None
    offset = (count - 1) // 2
    take = 2 - (count % 2)
    _, rows = engine.fetch(
        f'SELECT "{slug}" FROM "{physical}" WHERE "{slug}" IS NOT NULL '
        f'ORDER BY "{slug}" LIMIT ? OFFSET ?',
        (take, offset),
    )
    values = [r[0] for r in rows if r[0] is not None]
    return sum(values) / len(values) if values else None


def profile_table(engine: SqlEngine, table: TableInfo) -> TableProfile:
    profile = TableProfile(table=table)
    physical = table.physical_name
    has_trace = any(c["slug"] == "is_trace" for c in table.columns) or True

    for column in table.measures():
        slug = column["slug"]
        trace_expr = (
            ", SUM(CASE WHEN is_trace THEN 1 ELSE 0 END) AS traces"
            if has_trace
            else ", 0 AS traces"
        )
        try:
            row = engine.fetch_dicts(
                f'SELECT COUNT("{slug}") AS n, COUNT(*) AS total_rows, '
                f'SUM("{slug}") AS total, AVG("{slug}") AS mean, '
                f'MIN("{slug}") AS lo, MAX("{slug}") AS hi{trace_expr} '
                f'FROM "{physical}"'
            )[0]
        except Exception:
            continue
        count = int(row["n"] or 0)
        profile.measures.append(
            MeasureStats(
                slug=slug,
                name=column["name"],
                unit=column["unit"],
                count=count,
                missing=int((row["total_rows"] or 0) - count),
                total=row["total"],
                mean=row["mean"],
                median=_median(engine, physical, slug, count),
                minimum=row["lo"],
                maximum=row["hi"],
                trace_rows=int(row["traces"] or 0),
            )
        )

    for column in table.dimensions():
        slug = column["slug"]
        rows = engine.fetch_dicts(
            f'SELECT "{slug}" AS value, COUNT(*) AS n FROM "{physical}" '
            f'WHERE "{slug}" IS NOT NULL GROUP BY "{slug}" '
            f"ORDER BY n DESC LIMIT {TOP_CATEGORIES}"
        )
        distinct = engine.scalar(f'SELECT COUNT(DISTINCT "{slug}") FROM "{physical}"') or 0
        profile.dimensions.append(
            {
                "slug": slug,
                "name": column["name"],
                "distinct": int(distinct),
                "top": [{"value": r["value"], "count": r["n"]} for r in rows],
            }
        )

    profile.checksums = engine.fetch_dicts(
        "SELECT column_slug, stated_total, parsed_total, agrees "
        "FROM cat_checksum WHERE table_id = ?",
        (table.table_id,),
    )
    profile.summary, profile.observations = _narrative(profile)
    return profile


def _narrative(profile: TableProfile) -> tuple[list[str], list[str]]:
    """Two lists: what the table *is*, and what is notable about its contents.

    Splitting them lets the summary panel stay short while the detail panel
    carries the caveats -- rather than one undifferentiated wall of bullets.
    """
    table = profile.table
    summary: list[str] = []
    lines = [
        f"{table.row_count} rows across {len(table.columns)} columns, "
        f"read from {table.filename} / {table.sheet}."
    ]

    summary.append(lines.pop(0))
    if station := table.context.get("station"):
        index = table.context.get("index")
        summary.append(f"All rows carry station {station}{f' ({index})' if index else ''}.")
    if profile.measures:
        names = ", ".join(m.name for m in profile.measures[:4])
        summary.append(f"Measures available: {names}.")
    if table.as_of_date:
        summary.append(f"Observation date recorded as {table.as_of_date}.")

    for measure in profile.measures[:4]:
        if measure.count == 0:
            lines.append(f"{measure.name} has no usable observations.")
            continue
        unit = f" {UNIT_TEXT.get(measure.unit or '', measure.unit or '')}".rstrip()
        lines.append(
            f"{measure.name} ranges {measure.minimum:g}{unit} to {measure.maximum:g}{unit}, "
            f"mean {measure.mean:.1f}{unit}."
        )
        if measure.missing:
            share = measure.missing / max(1, table.row_count) * 100
            lines.append(
                f"{measure.name} is missing in {measure.missing} row(s) ({share:.0f}%); "
                f"those rows are excluded from every aggregate rather than read as zero."
            )
        if measure.trace_rows:
            lines.append(
                f"{measure.trace_rows} trace observation(s) recorded for {measure.name}, "
                f"stored as 0.0 with a flag."
            )
        if measure.unit == "mm":
            lines.append(f"{RAINFALL_DAY_NOTE} applies to these totals.")

    for dimension in profile.dimensions[:3]:
        count = dimension["distinct"]
        if count and count <= TOP_CATEGORIES:
            values = ", ".join(str(t["value"]) for t in dimension["top"])
            noun = "value" if count == 1 else "values"
            lines.append(f"{dimension['name']} takes {count} {noun}: {values}.")
        elif count:
            lines.append(f"{dimension['name']} has {count} distinct values.")

    if any(c["slug"] == "period_month" for c in table.columns):
        months = [c for c in table.columns if c["slug"] == "period_month"]
        if months:
            lines.append(
                "Monthly series: seasonal questions resolve to "
                f"{season_for_month(7).label if season_for_month(7) else 'the monsoon'} "
                "and the other IMD seasons automatically."
            )

    failed = [c for c in profile.checksums if not c["agrees"]]
    if failed:
        lines.append(
            f"Checksum disagreement on {len(failed)} column(s) against the sheet's own "
            f"totals row -- treat those figures as unverified."
        )
    elif profile.checksums:
        lines.append("Parsed totals agree with the sheet's own totals row.")

    return summary, lines
