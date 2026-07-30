"""The query service: question in, professional answer out. No model involved."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from imdq.logging_setup import get_logger
from imdq.nlq.lexicon import Lexicon
from imdq.nlq.nlg import Answer, narrate
from imdq.nlq.planner import QueryPlan
from imdq.nlq.planner import plan as build_plan
from imdq.nlq.resolver import Slots, resolve
from imdq.nlq.sqlbuilder import build as build_sql
from imdq.storage.catalog import catalog_version, get_table
from imdq.storage.engine import SqlEngine

log = get_logger(__name__)


@dataclass(slots=True)
class QueryResult:
    answer: Answer
    plan: QueryPlan
    slots: Slots

    def to_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer.to_dict(),
            "plan": self.plan.to_dict(),
            "slots": self.slots.to_dict(),
        }


#: Answers are deterministic for a given question and catalog state, so an
#: identical question re-asked in a meeting returns the identical figure without
#: touching the warehouse. The catalog version is part of the key, so an ingest
#: invalidates every cached answer.
_CACHE: OrderedDict[tuple[str, str, str, int], QueryResult] = OrderedDict()
_CACHE_MAX = 256


def clear_cache() -> None:
    _CACHE.clear()


def ask(
    question: str,
    engine: SqlEngine,
    lexicon: Lexicon,
    row_limit: int = 5_000,
    table_hint: str | None = None,
) -> QueryResult:
    key = (
        f"{engine.identity}|{catalog_version(engine)}",
        question.strip().lower(),
        table_hint or "",
        row_limit,
    )
    cached = _CACHE.get(key)
    if cached is not None:
        _CACHE.move_to_end(key)
        return cached

    result = _run(question, engine, lexicon, row_limit, table_hint)
    _CACHE[key] = result
    if len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return result


def _run(
    question: str,
    engine: SqlEngine,
    lexicon: Lexicon,
    row_limit: int,
    table_hint: str | None,
) -> QueryResult:
    slots = resolve(question, lexicon, table_hint=table_hint)
    plan = build_plan(slots, row_limit=row_limit)
    table = get_table(engine, plan.measure.table_id) if plan.measure else None
    if table is None:                                   # pragma: no cover - guarded by planner
        raise AssertionError("planner guarantees a measure")

    sql, params, outputs = build_sql(plan, table)
    log.info("executing plan", extra={"extra_fields": {"sql": sql, "intent": str(plan.intent)}})
    columns, rows = engine.fetch(sql, params)

    answer = narrate(plan, table, columns or outputs, rows, sql)
    return QueryResult(answer=answer, plan=plan, slots=slots)
