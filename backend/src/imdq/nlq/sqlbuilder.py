"""QueryPlan -> parameterised SQL.

Two invariants, both load-bearing:

* every identifier is validated against the catalog before it reaches the
  string, so a crafted question cannot name a table it was not given; and
* every literal travels as a bound parameter, never concatenated.
"""

from __future__ import annotations

import re
from typing import Any

from imdq.domain.imd import MONTH_NAMES
from imdq.errors import QueryFailed, Unanswerable
from imdq.nlq.planner import Intent, QueryPlan
from imdq.storage.catalog import TableInfo

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MONTH_LOWER = tuple(m.lower() for m in MONTH_NAMES)
AGGREGATION_SQL = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN", "count": "COUNT"}


def _ident(name: str, allowed: set[str], what: str) -> str:
    if not _IDENT.match(name) or name not in allowed:
        raise QueryFailed(
            f"Unknown {what} {name!r}.",
            remedy="This usually means the catalog is stale; re-run ingestion.",
        )
    return f'"{name}"'


def _month_text_column(table: TableInfo, columns: set[str]) -> str | None:
    """A table may express months as text ("January") rather than an integer."""
    for candidate in ("period", "month"):
        if candidate not in columns:
            continue
        spec = next((c for c in table.columns if c["slug"] == candidate), None)
        if spec is None:
            continue
        sample = {str(v).strip().lower() for v in spec.get("distinct_sample", [])}
        if sample and any(name[:3] in {s[:3] for s in sample} for name in MONTH_LOWER):
            return candidate
    return None


def _apply_time(
    plan: QueryPlan,
    table: TableInfo,
    columns: set[str],
    where: list[str],
    params: list[Any],
) -> None:
    """Apply the time filter, or refuse.

    Silently dropping a time filter because the table has no matching column is
    the most dangerous failure available here: the answer looks right and covers
    the wrong period. So an unapplicable filter raises instead.
    """
    if plan.month_filter:
        if "period_month" in columns:
            where.append(f"period_month IN ({', '.join('?' for _ in plan.month_filter)})")
            params.extend(plan.month_filter)
        elif text_column := _month_text_column(table, columns):
            wanted = [MONTH_NAMES[m - 1].lower() for m in plan.month_filter]
            placeholders = ", ".join("?" for _ in wanted)
            where.append(f'LOWER("{text_column}") IN ({placeholders})')
            params.extend(wanted)
        else:
            raise Unanswerable(
                f"'{table.sheet}' has no month column, so the period "
                f"'{plan.time_label}' cannot be applied.",
                remedy="Ask without the month, or query a table that carries a monthly series.",
            )

    if plan.year_filter or plan.year_range:
        if "year" not in columns:
            raise Unanswerable(
                f"'{table.sheet}' has no year column, so the period "
                f"'{plan.time_label}' cannot be applied.",
                remedy="Ask without the year, or query a table that carries a yearly series.",
            )
        if plan.year_filter:
            where.append(f"year IN ({', '.join('?' for _ in plan.year_filter)})")
            params.extend(plan.year_filter)
        if plan.year_range:
            where.append("year BETWEEN ? AND ?")
            params.extend(plan.year_range)


def _order_expression(slug: str, table: TableInfo, columns: set[str]) -> str:
    """Months stored as text sort alphabetically; a trend needs calendar order."""
    if slug == _month_text_column(table, columns):
        cases = " ".join(
            f"WHEN LOWER(\"{slug}\") = '{name}' THEN {i}" for i, name in enumerate(MONTH_LOWER, 1)
        )
        return f"CASE {cases} ELSE 99 END"
    return _ident(slug, columns, "column")


def build(plan: QueryPlan, table: TableInfo) -> tuple[str, list[Any], list[str]]:
    """Return ``(sql, params, output_columns)``."""
    columns = {c["slug"] for c in table.columns} | {"is_trace", "_source_row", "_as_of_date"}
    table_sql = _ident(table.physical_name, {table.physical_name}, "table")

    select: list[str] = []
    outputs: list[str] = []
    for group in plan.group_by:
        select.append(_ident(group.slug, columns, "column"))
        outputs.append(group.slug)

    if plan.measure is not None and plan.aggregation:
        function = AGGREGATION_SQL[plan.aggregation]
        measure_sql = _ident(plan.measure.slug, columns, "column")
        select.append(f"{function}({measure_sql}) AS metric_value")
        outputs.append("metric_value")
        select.append("COUNT(*) AS row_count")
        outputs.append("row_count")
        select.append("SUM(CASE WHEN is_trace THEN 1 ELSE 0 END) AS trace_rows")
        outputs.append("trace_rows")
    elif plan.measure is not None:
        select.append(_ident(plan.measure.slug, columns, "column"))
        outputs.append(plan.measure.slug)

    where: list[str] = []
    params: list[Any] = []
    for filter_ in plan.filters:
        column_sql = _ident(filter_.column.slug, columns, "column")
        if filter_.op == "=" and isinstance(filter_.value, str):
            # Case-insensitive equality: archives are inconsistently capitalised.
            where.append(f"LOWER({column_sql}) = LOWER(?)")
        else:
            where.append(f"{column_sql} {filter_.op} ?")
        params.append(filter_.value)

    _apply_time(plan, table, columns, where, params)

    # Missing observations must never be silently read as zero.
    if plan.measure is not None and plan.aggregation != "count":
        where.append(f"{_ident(plan.measure.slug, columns, 'column')} IS NOT NULL")

    sql = f"SELECT {', '.join(select)} FROM {table_sql}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if plan.group_by:
        sql += " GROUP BY " + ", ".join(_ident(g.slug, columns, "column") for g in plan.group_by)
    if plan.intent in (Intent.RANKING, Intent.BREAKDOWN) and plan.aggregation:
        sql += f" ORDER BY metric_value {'DESC' if plan.order_desc else 'ASC'}"
    elif plan.intent is Intent.TREND and plan.group_by:
        sql += " ORDER BY " + ", ".join(
            _order_expression(g.slug, table, columns) for g in plan.group_by
        )
    sql += " LIMIT ?"
    params.append(plan.limit)
    return sql, params, outputs
