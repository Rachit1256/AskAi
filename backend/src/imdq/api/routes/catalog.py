from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from imdq.api.deps import engine_dep, lexicon_dep
from imdq.api.schemas import ColumnOut, TableOut
from imdq.nlq import service
from imdq.nlq.lexicon import Lexicon
from imdq.storage import catalog
from imdq.storage.engine import SqlEngine
from imdq.storage.profile import profile_table

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/tables", response_model=list[TableOut])
def tables(
    dataset_id: str | None = None, engine: SqlEngine = Depends(engine_dep)
) -> list[TableOut]:
    return [
        TableOut(
            table_id=t.table_id,
            dataset_id=t.dataset_id,
            filename=t.filename,
            sheet=t.sheet,
            kind=t.kind,
            rows=t.row_count,
            as_of_date=t.as_of_date,
            context=t.context,
            columns=[
                ColumnOut(
                    slug=c["slug"],
                    name=c["name"],
                    role=c["role"],
                    unit=c["unit"],
                    sql_type=c["sql_type"],
                )
                for c in t.columns
            ],
        )
        for t in catalog.list_tables(engine, dataset_id)
    ]


@router.get("/datasets")
def datasets(engine: SqlEngine = Depends(engine_dep)) -> list[dict[str, object]]:
    return catalog.dataset_summary(engine)


@router.get("/tables/{table_id}/profile")
def profile(table_id: str, engine: SqlEngine = Depends(engine_dep)) -> dict[str, object]:
    """Statistics, dimension breakdowns and plain observations for one table.

    Everything here is computed in SQL from the stored rows, so a figure shown in
    a panel is the same figure a query would return.
    """
    return profile_table(engine, catalog.get_table(engine, table_id)).to_dict()


@router.get("/tables/{table_id}/preview")
def preview(
    table_id: str,
    limit: int = Query(default=50, ge=1, le=1000),
    engine: SqlEngine = Depends(engine_dep),
) -> dict[str, object]:
    table = catalog.get_table(engine, table_id)
    columns, rows = engine.fetch(f'SELECT * FROM "{table.physical_name}" LIMIT ?', (limit,))
    return {
        "table_id": table_id,
        "columns": columns,
        "rows": [dict(zip(columns, row, strict=True)) for row in rows],
        "total_rows": table.row_count,
    }


@router.delete("/datasets/{dataset_id}")
def retire(
    dataset_id: str,
    engine: SqlEngine = Depends(engine_dep),
    lexicon: Lexicon = Depends(lexicon_dep),
) -> dict[str, str]:
    """Soft delete. History is retained so earlier reports remain reproducible."""
    engine.execute("UPDATE cat_dataset SET status = 'retired' WHERE dataset_id = ?", (dataset_id,))
    lexicon.remove_dataset(dataset_id)
    service.clear_cache()
    return {"status": "retired", "dataset_id": dataset_id}


@router.delete("")
def clear(
    engine: SqlEngine = Depends(engine_dep), lexicon: Lexicon = Depends(lexicon_dep)
) -> dict[str, object]:
    """Remove everything. Irreversible, unlike retiring a single dataset."""
    dropped = catalog.clear_all(engine)
    lexicon.clear()
    service.clear_cache()
    return {"status": "cleared", "tables_dropped": dropped}
