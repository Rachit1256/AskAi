"""Dashboard specs and report rendering."""

from __future__ import annotations

from imdq.analytics.dashboard import build_dashboard
from imdq.nlq.service import ask
from imdq.report.builder import ReportSession, render_html
from imdq.storage.catalog import list_tables


def test_dashboard_emits_vega_specs_not_images(warehouse):
    engine, _, _ = warehouse
    charts = build_dashboard(engine, list_tables(engine))
    assert charts
    non_kpi = [c for c in charts if c.kind != "kpi"]
    assert non_kpi and "$schema" in non_kpi[0].spec


def test_dashboard_deduplicates_titles(warehouse):
    engine, _, _ = warehouse
    charts = build_dashboard(engine, list_tables(engine), max_charts=8)
    titles = [(c.kind, c.title) for c in charts]
    assert len(titles) == len(set(titles))


def test_report_only_contains_computed_figures(warehouse):
    engine, lexicon, _ = warehouse
    session = ReportSession(title="Monsoon review")
    session.datasets = [
        {"filename": t.filename, "sheet": t.sheet, "rows": t.row_count, "as_of": t.as_of_date}
        for t in list_tables(engine)
    ]
    session.add(ask("monsoon rainfall", engine, lexicon))
    html = render_html(session)
    assert "591.3" in html
    assert "Monsoon review" in html
    assert "SATMET" in html


def test_profile_reports_statistics_and_observations(warehouse):
    from imdq.storage.catalog import get_table, list_tables
    from imdq.storage.profile import profile_table

    engine, _, _ = warehouse
    table = next(t for t in list_tables(engine) if any(
        c["slug"] == "rainfall_mm" for c in t.columns
    ))
    profile = profile_table(engine, get_table(engine, table.table_id)).to_dict()
    stats = profile["statistics"]["Rainfall (mm)"]
    assert stats["count"] == 12
    assert round(stats["sum"], 1) == 758.8
    assert stats["median"] is not None
    assert any("PUNE" in line for line in profile["observations"])
    assert any("totals row" in line for line in profile["observations"])


def test_incremental_indexing_does_not_drop_other_datasets(warehouse, workbook, tmp_path):
    import datetime as dt

    from tests.fixtures.make_workbook import build
    from imdq.storage.warehouse import ingest_file

    engine, lexicon, _ = warehouse
    before = len(lexicon.search("rainfall", limit=50))
    second = build(tmp_path / "second.xlsx", years=5)
    report = ingest_file(engine, second, as_of=dt.date(2026, 7, 30))
    lexicon.index_dataset(engine, report.dataset_id)
    assert len(lexicon.search("rainfall", limit=50)) >= before


def test_clear_all_drops_physical_tables(warehouse):
    from imdq.storage.catalog import clear_all, list_tables

    engine, _, _ = warehouse
    assert list_tables(engine)
    dropped = clear_all(engine)
    assert dropped > 0
    assert list_tables(engine) == []
