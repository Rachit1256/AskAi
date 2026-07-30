"""Query path: resolution, planning, SQL safety and answer rendering."""

from __future__ import annotations

import pytest

from imdq.errors import Unanswerable
from imdq.nlq.nlg import format_number
from imdq.nlq.service import ask
from imdq.nlq.timeparse import parse_time


def test_imd_seasons_resolve_to_their_months():
    spec, _ = parse_time("monsoon rainfall")
    assert spec.season_code == "JJAS" and spec.months == (6, 7, 8, 9)
    spec, _ = parse_time("post monsoon cyclones")
    assert spec.season_code == "OND"


def test_time_expression_is_removed_from_the_residue():
    _, residue = parse_time("total rainfall in july 2019")
    assert "july" not in residue and "2019" not in residue


def test_aggregate_uses_the_right_month(warehouse):
    engine, lexicon, _ = warehouse
    result = ask("total rainfall in july", engine, lexicon)
    assert result.plan.month_filter == (7,)
    assert result.answer.rows[0]["metric_value"].startswith("187.3")


def test_season_aggregate_sums_only_its_months(warehouse):
    engine, lexicon, _ = warehouse
    result = ask("monsoon rainfall", engine, lexicon)
    # 134.7 + 187.3 + 116.5 + 152.8
    assert result.answer.rows[0]["metric_value"].startswith("591.3")


def test_ranking_orders_and_limits(warehouse):
    engine, lexicon, _ = warehouse
    result = ask("top 3 months by rainfall", engine, lexicon)
    assert len(result.answer.rows) == 3
    assert result.answer.rows[0]["month"] == "July"


def test_inapplicable_time_filter_refuses_rather_than_dropping(warehouse):
    """The dangerous failure is answering for the wrong period, not refusing."""
    engine, lexicon, _ = warehouse
    with pytest.raises(Unanswerable):
        ask("rainfall in 1995 at Pune", engine, lexicon)


def test_unmatched_measure_is_a_clean_refusal(warehouse):
    engine, lexicon, _ = warehouse
    with pytest.raises(Unanswerable):
        ask("wind speed at 850 hPa", engine, lexicon)


def test_partial_match_states_its_interpretation(warehouse):
    """SST is not air temperature.

    The resolver will match "sea surface temperature" to Max Temp (C) because
    they share a token. That guess is defensible; making it silently is not.
    """
    engine, lexicon, _ = warehouse
    result = ask("what is the sea surface temperature", engine, lexicon)
    assert any("Interpreted" in line for line in result.answer.assumptions)
    assert "Max Temp (C)" in " ".join(result.answer.assumptions)


def test_exact_match_says_nothing_extra(warehouse):
    engine, lexicon, _ = warehouse
    result = ask("total rainfall in july", engine, lexicon)
    assert not any("Interpreted" in line for line in result.answer.assumptions)


def test_temperature_is_averaged_not_summed(warehouse):
    """Twelve monthly mean temperatures added together is a number with no
    physical meaning, and it looks plausible enough that nobody questions it."""
    engine, lexicon, _ = warehouse
    assert ask("max temperature", engine, lexicon).plan.aggregation != "sum"
    assert ask("rainfall", engine, lexicon).plan.aggregation == "sum"


def test_ambiguous_query_returns_serialisable_candidates():
    """Candidate uses slots, so ``.__dict__`` raised AttributeError and turned
    every 409 into a 500. The disambiguation path was unreachable."""
    from imdq.errors import AmbiguousQuery
    from imdq.nlq.lexicon import LexHit
    from imdq.nlq.resolver import _check_ambiguous

    def hit(name, table, score):
        return LexHit(
            entry_id=1,
            kind="measure",
            table_id=table,
            physical_name=f"t_{table}",
            column_slug="rainfall_mm",
            role="measure",
            unit="mm",
            display=name,
            column_display=name,
            value=None,
            score=score,
        )

    with pytest.raises(AmbiguousQuery) as caught:
        _check_ambiguous(
            [hit("Rainfall A", "t1", 5.0), hit("Rainfall B", "t2", 4.95)], "measure", "rainfall"
        )

    candidates = caught.value.context["candidates"]
    assert len(candidates) == 2
    assert candidates[0]["label"] == "Rainfall A"
    import json

    json.dumps(candidates)  # must survive JSON encoding for the 409 body


def test_literals_are_bound_not_concatenated(warehouse):
    engine, lexicon, _ = warehouse
    result = ask("average rainy days at Pune", engine, lexicon)
    assert "PUNE" not in result.answer.sql
    assert "?" in result.answer.sql


def test_answer_carries_provenance_and_units(warehouse):
    engine, lexicon, _ = warehouse
    result = ask("total rainfall in july", engine, lexicon)
    text = result.answer.to_text()
    assert "mm" in text
    assert "Source:" in text
    assert "0830-0830 IST" in text


def test_indian_number_grouping():
    assert format_number(120000) == "1,20,000"
    assert format_number(4.25, "mm") == "4.2 mm"
    assert format_number(None) == "not available"


def test_repeat_question_is_served_from_cache(warehouse):
    """Identical question, identical figure -- it matters when someone re-runs
    yesterday's question in a meeting."""
    engine, lexicon, _ = warehouse
    first = ask("monsoon rainfall", engine, lexicon)
    second = ask("monsoon rainfall", engine, lexicon)
    assert first is second


def test_ingest_invalidates_the_cache(warehouse, workbook):
    import datetime as dt

    from imdq.nlq import service
    from imdq.storage.warehouse import ingest_file

    engine, lexicon, _ = warehouse
    before = ask("monsoon rainfall", engine, lexicon)
    ingest_file(engine, workbook, as_of=dt.date(2026, 7, 29))
    service.clear_cache()
    after = ask("monsoon rainfall", engine, lexicon)
    assert after is not before


def test_table_hint_resolves_an_ambiguous_question(warehouse):
    from imdq.storage.catalog import list_tables

    engine, lexicon, _ = warehouse
    target = next(
        t for t in list_tables(engine) if any(c["slug"] == "rainfall_mm" for c in t.columns)
    )
    result = ask("total rainfall", engine, lexicon, table_hint=target.table_id)
    assert result.plan.measure.table_id == target.table_id


def test_values_named_in_the_question_resolve_in_one_pass(warehouse):
    engine, lexicon, _ = warehouse
    hits = lexicon.match_values("average rainy days at Pune please")
    assert hits and hits[0].value == "PUNE"
    # A value must not match on an unrelated stray token.
    assert lexicon.match_values("average rainy days") == []
