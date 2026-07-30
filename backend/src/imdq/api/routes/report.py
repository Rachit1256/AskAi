from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from imdq.analytics.dashboard import build_dashboard
from imdq.api.deps import engine_dep, lexicon_dep, settings_dep
from imdq.api.schemas import ReportRequest
from imdq.config import Settings
from imdq.errors import ImdqError
from imdq.nlq.lexicon import Lexicon
from imdq.nlq.service import ask
from imdq.report.builder import ReportSession, render_html
from imdq.storage.catalog import list_tables
from imdq.storage.engine import SqlEngine

router = APIRouter(prefix="/report", tags=["report"])


def _build(
    payload: ReportRequest, settings: Settings, engine: SqlEngine, lexicon: Lexicon
) -> str:
    tables = list_tables(engine, table_ids=[payload.table_id] if payload.table_id else None)
    session = ReportSession(title=payload.title)
    session.datasets = [
        {"filename": t.filename, "sheet": t.sheet, "rows": t.row_count, "as_of": t.as_of_date}
        for t in tables
    ]
    for question in payload.questions:
        try:
            session.add(
                ask(
                    question, engine, lexicon,
                    row_limit=settings.query_row_limit,
                    table_hint=payload.table_id,
                )
            )
        except ImdqError as exc:
            # A question that cannot be answered is recorded as a caveat rather
            # than failing the whole report. A partial report with its gaps named
            # is more useful than no report.
            session.caveats.append(f"'{question}' could not be answered: {exc.message}")
    if payload.include_dashboard:
        session.charts = build_dashboard(engine, tables)
    return render_html(session)


@router.post("", response_class=HTMLResponse)
def report(
    payload: ReportRequest,
    settings: Settings = Depends(settings_dep),
    engine: SqlEngine = Depends(engine_dep),
    lexicon: Lexicon = Depends(lexicon_dep),
) -> HTMLResponse:
    html = _build(payload, settings, engine, lexicon)
    headers = {}
    if payload.download:
        stamp = date.today().isoformat()
        slug = "".join(c if c.isalnum() else "-" for c in payload.title.lower())[:48]
        headers["Content-Disposition"] = f'attachment; filename="{slug}-{stamp}.html"'
    return HTMLResponse(html, headers=headers)
