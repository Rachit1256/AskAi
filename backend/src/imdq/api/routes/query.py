from __future__ import annotations

from fastapi import APIRouter, Depends

from imdq.api.deps import engine_dep, lexicon_dep, settings_dep
from imdq.api.schemas import AskRequest, AskResponse
from imdq.config import Settings
from imdq.nlq.lexicon import Lexicon
from imdq.nlq.resolver import resolve
from imdq.nlq.service import ask
from imdq.storage.engine import SqlEngine

router = APIRouter(prefix="/query", tags=["query"])


@router.post("/ask", response_model=AskResponse)
def ask_question(
    payload: AskRequest,
    settings: Settings = Depends(settings_dep),
    engine: SqlEngine = Depends(engine_dep),
    lexicon: Lexicon = Depends(lexicon_dep),
) -> AskResponse:
    """Answer a question. No model call.

    Ambiguity surfaces as HTTP 409 carrying the candidate referents; send the
    chosen ``table_id`` back with the same question to resolve it.
    """
    result = ask(
        payload.question,
        engine,
        lexicon,
        row_limit=payload.limit or settings.query_row_limit,
        table_hint=payload.table_id,
    )
    return AskResponse(**result.to_dict())


@router.post("/resolve")
def resolve_only(
    payload: AskRequest, lexicon: Lexicon = Depends(lexicon_dep)
) -> dict[str, object]:
    """Show what a question resolves to without running it.

    Useful when an answer looks wrong: it separates a resolution mistake from a
    data problem, which is otherwise guesswork.
    """
    return resolve(payload.question, lexicon, table_hint=payload.table_id).to_dict()


@router.get("/suggestions")
def suggestions(
    engine: SqlEngine = Depends(engine_dep), table_id: str | None = None
) -> dict[str, list[str]]:
    """Question shapes known to resolve against the current catalog."""
    from imdq.storage.catalog import list_tables

    tables = list_tables(engine, table_ids=[table_id] if table_id else None)
    out: list[str] = []
    for table in tables[:4]:
        measures = table.measures()
        if not measures:
            continue
        measure = measures[0]["name"].lower()
        dimensions = table.dimensions() + table.time_columns()
        out.append(f"total {measure}")
        if dimensions:
            label = dimensions[0]["name"].lower()
            out.extend([f"{measure} by {label}", f"top 5 {label} by {measure}"])
        if measures[0]["unit"] == "mm":
            out.append(f"monsoon {measure}")
        else:
            out.append(f"average {measure}")
    return {"suggestions": list(dict.fromkeys(out))[:8]}
