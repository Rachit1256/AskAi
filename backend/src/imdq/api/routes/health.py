from __future__ import annotations

from fastapi import APIRouter, Depends

from imdq.api.deps import engine_dep, settings_dep
from imdq.config import Settings
from imdq.storage.engine import SqlEngine

router = APIRouter(tags=["health"])


@router.get("/health")
def health(
    settings: Settings = Depends(settings_dep), engine: SqlEngine = Depends(engine_dep)
) -> dict[str, object]:
    datasets = engine.scalar("SELECT COUNT(*) FROM cat_dataset WHERE status = 'active'") or 0
    rows = engine.scalar("SELECT COALESCE(SUM(row_count), 0) FROM cat_dataset") or 0
    return {
        "status": "ok",
        "environment": settings.env,
        "engine": engine.name,
        "active_datasets": datasets,
        "total_rows": rows,
        "llm_calls_in_query_path": 0,
    }
