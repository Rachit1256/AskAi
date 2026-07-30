"""Answer rendering.

Deterministic templates, not generation. Consistency is what reads as
professional: the same question always produces the same wording, units are
never dropped, and every figure carries the provenance needed to check it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from imdq.domain.imd import (
    NORMALS_PERIOD,
    RAINFALL_DAY_NOTE,
    departure_category,
)
from imdq.nlq.planner import Intent, QueryPlan
from imdq.storage.catalog import TableInfo

AGGREGATION_WORDS = {
    "sum": "Total", "avg": "Average", "max": "Maximum", "min": "Minimum", "count": "Count"
}
UNIT_SUFFIX = {"mm": "mm", "degC": "\u00b0C", "percent": "%", "tonnes": "t", "hPa": "hPa"}


def format_number(value: Any, unit: str | None = None, indian_grouping: bool = True) -> str:
    """Indian digit grouping by default: 1,20,000 rather than 120,000."""
    if value is None:
        return "not available"
    if not isinstance(value, (int, float)):
        return str(value)
    number = float(value)
    decimals = 0 if abs(number) >= 1000 and float(number).is_integer() else 1
    text = f"{abs(number):,.{decimals}f}"
    if indian_grouping and abs(number) >= 100_000:
        whole, _, fraction = text.replace(",", "").partition(".")
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        text = ",".join([*groups, tail]) + (f".{fraction}" if fraction else "")
    if number < 0:
        text = f"-{text}"
    suffix = UNIT_SUFFIX.get(unit or "", unit or "")
    return f"{text} {suffix}".strip()


@dataclass(slots=True)
class Answer:
    headline: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    provenance: str = ""
    sql: str = ""
    departure: dict[str, Any] | None = None

    def to_text(self) -> str:
        parts = [self.headline]
        if self.rows and len(self.rows) > 1:
            parts.append("")
            parts.append(_render_table(self.columns, self.rows))
        if self.assumptions:
            parts.append("")
            parts.append(" ".join(self.assumptions))
        if self.notes:
            parts.append(" ".join(self.notes))
        if self.provenance:
            parts.append(f"Source: {self.provenance}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "columns": self.columns,
            "rows": self.rows,
            "assumptions": self.assumptions,
            "notes": self.notes,
            "provenance": self.provenance,
            "departure": self.departure,
            "sql": self.sql,
            "text": self.to_text(),
        }


def _render_table(columns: list[str], rows: list[dict[str, Any]], limit: int = 25) -> str:
    shown = rows[:limit]
    widths = {
        c: max(len(c), *(len(str(r.get(c, ""))) for r in shown)) if shown else len(c)
        for c in columns
    }
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    divider = "  ".join("-" * widths[c] for c in columns)
    body = [
        "  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns) for row in shown
    ]
    if len(rows) > limit:
        body.append(f"... {len(rows) - limit} further rows")
    return "\n".join([header, divider, *body])


def _filters_phrase(plan: QueryPlan) -> str:
    if not plan.filters:
        return ""
    return " for " + " and ".join(f.describe() for f in plan.filters)


def _time_phrase(plan: QueryPlan) -> str:
    if not plan.time_label or plan.time_label == "all available periods":
        return ""
    return f" in {plan.time_label}"


def narrate(
    plan: QueryPlan,
    table: TableInfo,
    columns: list[str],
    rows: list[tuple[Any, ...]],
    sql: str,
    normal_value: float | None = None,
) -> Answer:
    records = [dict(zip(columns, row, strict=True)) for row in rows]
    unit = plan.measure.unit if plan.measure else None
    measure_label = plan.measure.display if plan.measure else "value"
    word = AGGREGATION_WORDS.get(plan.aggregation or "", "Value")
    # "Maximum max temp" reads like a bug. If the column name already carries the
    # aggregation, do not say it twice.
    if measure_label.lower().startswith(word.lower()[:3]):
        word = ""

    answer = Answer(headline="", sql=sql)
    answer.assumptions = list(plan.assumptions)

    if not records:
        answer.headline = (
            f"No observations of {measure_label}{_filters_phrase(plan)}{_time_phrase(plan)} "
            f"were found in {table.filename}."
        )
        answer.provenance = _provenance(table, 0)
        return answer

    if plan.intent is Intent.AGGREGATE:
        value = records[0].get("metric_value")
        count = records[0].get("row_count", 0)
        traces = records[0].get("trace_rows", 0) or 0
        subject = f"{word} {measure_label.lower()}" if word else measure_label
        answer.headline = (
            f"{subject}{_filters_phrase(plan)}{_time_phrase(plan)} "
            f"is {format_number(value, unit)}."
        )
        answer.notes.append(f"Computed from {count} observation(s).")
        if traces:
            answer.notes.append(f"{traces} trace day(s) counted as 0.0 mm.")
        if normal_value:
            departure = (value - normal_value) / normal_value * 100.0
            answer.departure = {
                "normal": normal_value,
                "departure_pct": round(departure, 1),
                "category": departure_category(departure),
                "period": NORMALS_PERIOD,
            }
            answer.headline += (
                f" That is a departure of {departure:+.0f}% from the {NORMALS_PERIOD} "
                f"normal of {format_number(normal_value, unit)}"
                f" (category: {answer.departure['category']})."
            )
    elif plan.intent is Intent.RANKING:
        direction = "Top" if plan.order_desc else "Lowest"
        group = plan.group_by[0].display if plan.group_by else "group"
        answer.headline = (
            f"{direction} {len(records)} {group.lower()} by {word.lower()} "
            f"{measure_label.lower()}{_time_phrase(plan)}:"
        )
    elif plan.intent is Intent.TREND:
        first, last = records[0], records[-1]
        first_value = first.get("metric_value")
        last_value = last.get("metric_value")
        answer.headline = (
            f"{measure_label} {word.lower()} across {len(records)} periods"
            f"{_filters_phrase(plan)}{_time_phrase(plan)}: "
            f"{format_number(first_value, unit)} to {format_number(last_value, unit)}."
        )
    elif plan.intent is Intent.BREAKDOWN:
        group = ", ".join(g.display for g in plan.group_by)
        answer.headline = (
            f"{word} {measure_label.lower()} by {group.lower()}"
            f"{_filters_phrase(plan)}{_time_phrase(plan)}:"
        )
    else:
        answer.headline = (
            f"{len(records)} matching row(s) for {measure_label.lower()}"
            f"{_filters_phrase(plan)}{_time_phrase(plan)}."
        )

    display_columns = [c for c in columns if c not in ("trace_rows",)]
    answer.columns = display_columns
    answer.rows = [
        {
            c: format_number(r[c], unit) if c == "metric_value" else r[c]
            for c in display_columns
        }
        for r in records
    ]
    if unit == "mm":
        answer.notes.append(RAINFALL_DAY_NOTE + ".")
    answer.provenance = _provenance(table, len(records))
    return answer


def _provenance(table: TableInfo, row_count: int) -> str:
    return (
        f"{table.filename} \u00b7 {table.sheet} \u00b7 {table.physical_name} \u00b7 "
        f"{row_count} row(s) \u00b7 as of {table.as_of_date}"
    )
