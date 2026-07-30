"""Slots -> an executable plan. The intent is read off the *shape* of the slots.

No classifier and no model: if a measure, an aggregation and a group-by are
present it is a breakdown; add a limit and it is a ranking. This is fully
inspectable, which matters when a forecaster asks why a number came out the way
it did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from imdq.errors import Unanswerable
from imdq.nlq.resolver import ColumnRef, Filter, Slots


class Intent(StrEnum):
    AGGREGATE = "aggregate"
    BREAKDOWN = "breakdown"
    RANKING = "ranking"
    TREND = "trend"
    LOOKUP = "lookup"


@dataclass(slots=True)
class QueryPlan:
    intent: Intent
    table: str
    measure: ColumnRef | None
    aggregation: str | None
    group_by: list[ColumnRef] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    month_filter: tuple[int, ...] = ()
    year_filter: tuple[int, ...] = ()
    year_range: tuple[int, int] | None = None
    order_desc: bool = True
    limit: int = 200
    assumptions: list[str] = field(default_factory=list)
    time_label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": str(self.intent),
            "table": self.table,
            "measure": self.measure.to_dict() if self.measure else None,
            "aggregation": self.aggregation,
            "group_by": [g.slug for g in self.group_by],
            "filters": [f.describe() for f in self.filters],
            "months": list(self.month_filter),
            "years": list(self.year_filter),
            "year_range": list(self.year_range) if self.year_range else None,
            "limit": self.limit,
            "assumptions": list(self.assumptions),
        }


def plan(slots: Slots, *, row_limit: int = 5_000, time_columns: tuple[str, ...] = ()) -> QueryPlan:
    if slots.table_id is None or slots.measure is None:
        raise Unanswerable(
            "I could not match this question to a measure in the uploaded data.",
            remedy="Name the quantity you want (for example 'rainfall' or 'rainy days'), "
            "or upload the file that contains it.",
            question=slots.question,
        )

    table = slots.measure.physical_name
    group_by = [g for g in slots.group_by if g.table_id == slots.measure.table_id]
    filters = [f for f in slots.filters if f.column.table_id == slots.measure.table_id]

    if slots.limit and group_by:
        intent = Intent.RANKING
        limit = min(slots.limit, row_limit)
    elif group_by:
        intent = Intent.BREAKDOWN
        limit = row_limit
    elif slots.aggregation:
        intent = Intent.AGGREGATE
        limit = 1
    else:
        intent = Intent.LOOKUP
        limit = min(200, row_limit)

    # A time grouping turns a breakdown into a trend, which is rendered and
    # narrated differently even though the SQL is the same shape.
    if intent is Intent.BREAKDOWN and any(g.role == "time" for g in group_by):
        intent = Intent.TREND

    return QueryPlan(
        intent=intent,
        table=table,
        measure=slots.measure,
        aggregation=slots.aggregation,
        group_by=group_by,
        filters=filters,
        month_filter=slots.time.months,
        year_filter=slots.time.years,
        year_range=(
            (slots.time.year_from, slots.time.year_to)
            if slots.time.year_from and slots.time.year_to
            else None
        ),
        order_desc=slots.descending,
        limit=limit,
        assumptions=list(slots.assumptions),
        time_label=slots.time.label,
    )
