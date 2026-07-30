from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile

from imdq.api.deps import engine_dep, lexicon_dep, settings_dep
from imdq.api.schemas import IngestResponse
from imdq.config import Settings
from imdq.errors import FileTooLarge, UnsupportedFile
from imdq.ingest.pipeline import SUPPORTED_SUFFIXES, analyse
from imdq.nlq import service
from imdq.nlq.lexicon import Lexicon
from imdq.storage.engine import SqlEngine
from imdq.storage.warehouse import ingest_file

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _persist(upload: UploadFile, settings: Settings) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise UnsupportedFile(
            f"Unsupported file type '{suffix}'.",
            remedy=f"Supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}",
        )
    settings.ensure_dirs()
    target = settings.uploads_dir / Path(upload.filename or "upload").name
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with target.open("wb") as handle:
        while chunk := upload.file.read(1 << 20):
            written += len(chunk)
            if written > limit:
                handle.close()
                target.unlink(missing_ok=True)
                raise FileTooLarge(
                    f"File exceeds the {settings.max_upload_mb} MB limit.",
                    remedy="Split the workbook or raise IMDQ_MAX_UPLOAD_MB.",
                )
            handle.write(chunk)
    return target


@router.post("", response_model=IngestResponse)
def ingest(
    file: UploadFile = File(...),
    as_of: date | None = Form(default=None),
    settings: Settings = Depends(settings_dep),
    engine: SqlEngine = Depends(engine_dep),
    lexicon: Lexicon = Depends(lexicon_dep),
) -> IngestResponse:
    """Ingest a workbook. Idempotent on file content; supersedes on re-send."""
    path = _persist(file, settings)
    report = ingest_file(engine, path, as_of=as_of)
    if not report.already_ingested:
        # Incremental: index only this dataset. A full rebuild would re-read every
        # distinct value of every dimension already in the warehouse.
        lexicon.index_dataset(engine, report.dataset_id)
        service.clear_cache()
    return IngestResponse(**report.to_dict())


@router.post("/analyse")
def analyse_only(
    file: UploadFile = File(...), settings: Settings = Depends(settings_dep)
) -> dict[str, object]:
    """Dry run: report the detected blocks without writing anything.

    Useful for checking layout detection against real files before committing
    them to the warehouse.
    """
    path = _persist(file, settings)
    return analyse(path).to_dict()
