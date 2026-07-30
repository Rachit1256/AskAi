"""Ingestion: block discovery, typing, derived columns and idempotency."""

from __future__ import annotations

import datetime as dt

from imdq.ingest.normalize import classify_columns, detect_derived_columns, parse_number
from imdq.ingest.pipeline import analyse
from imdq.storage.catalog import list_tables
from imdq.storage.engine import create_engine
from imdq.storage.warehouse import ingest_file


def test_trace_is_an_observation_not_a_gap():
    parsed = parse_number("T")
    assert parsed.value == 0.0 and parsed.is_trace and not parsed.is_missing


def test_missing_sentinels_never_become_zero():
    for sentinel in (-999.0, 999.9, "-999"):
        assert parse_number(sentinel).value is None
        assert parse_number(sentinel).is_missing


def test_indian_digit_grouping_and_bracket_negatives():
    assert parse_number("1,20,000").value == 120_000.0
    assert parse_number("(45.5)").value == -45.5


def test_seasonal_and_annual_columns_are_flagged_as_derived():
    header = ["Year", "Jun", "Jul", "Aug", "Sep", "JJAS", "Annual", "Total Stations"]
    rows = [
        (1991, 10.0, 20.0, 30.0, 40.0, 100.0, 100.0, 12),
        (1992, 1.0, 2.0, 3.0, 4.0, 10.0, 10.0, 12),
        (1993, 5.0, 5.0, 5.0, 5.0, 20.0, 20.0, 13),
        (1994, 2.0, 4.0, 6.0, 8.0, 20.0, 20.0, 13),
    ]
    specs = classify_columns(header, rows)
    detect_derived_columns(specs, rows)
    by_slug = {s.slug: s for s in specs}
    assert by_slug["jjas"].is_derived, "seasonal aggregate must not be melted in"
    assert by_slug["annual"].is_derived
    # A name containing 'total' is not enough; the numbers have to agree.
    assert not by_slug["total_stations"].is_derived


def test_blocks_are_discovered_without_a_template(workbook):
    plan = analyse(workbook)
    kinds = [r.kind for sheet in plan.sheets for r in sheet.recipes]
    assert len(kinds) >= 3, "side-by-side and stacked blocks should be separate"


def test_context_block_is_lifted_onto_the_tables_below(warehouse):
    engine, _, _ = warehouse
    normals = next(t for t in list_tables(engine)
        if "rainfall_mm" in {c["slug"] for c in t.columns})
    assert normals.context["station"] == "PUNE"
    assert "station" in {c["slug"] for c in normals.columns}


def test_reingesting_identical_content_writes_nothing(workbook):
    engine = create_engine(":memory:")
    first = ingest_file(engine, workbook, as_of=dt.date(2026, 7, 28))
    second = ingest_file(engine, workbook, as_of=dt.date(2026, 7, 28))
    assert first.total_rows > 0
    assert second.already_ingested and second.total_rows == 0


def test_totals_row_is_used_as_a_checksum(warehouse):
    _, _, report = warehouse
    checksums = [c for table in report.tables for c in table.checksums.values()]
    assert checksums and all(checksums), "parsed rows must agree with the stated total"


def test_layout_fingerprint_ignores_row_count(tmp_path):
    from tests.fixtures.make_workbook import build

    small = analyse(build(tmp_path / "a.xlsx", years=8))
    large = analyse(build(tmp_path / "b.xlsx", years=44))
    assert small.layout_fingerprint == large.layout_fingerprint
