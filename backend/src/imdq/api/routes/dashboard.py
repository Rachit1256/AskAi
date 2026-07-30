from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from imdq.analytics.dashboard import build_dashboard, suggest_visualisations
from imdq.api.deps import engine_dep
from imdq.storage.catalog import list_tables
from imdq.storage.engine import SqlEngine

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(
    dataset_id: str | None = None,
    table_id: str | None = None,
    max_charts: int = Query(default=8, ge=1, le=24),
    engine: SqlEngine = Depends(engine_dep),
) -> dict[str, object]:
    """Ranked chart specifications plus the shapes the data would also support.

    Specifications, not images: the browser renders them, so nothing accumulates
    on disk and the charts stay interactive.
    """
    tables = list_tables(engine, dataset_id=dataset_id, table_ids=[table_id] if table_id else None)
    charts = build_dashboard(engine, tables, max_charts=max_charts)
    return {
        "tables": len(tables),
        "charts": [c.to_dict() for c in charts],
        "suggested": [s.to_dict() for s in suggest_visualisations(tables)],
    }
