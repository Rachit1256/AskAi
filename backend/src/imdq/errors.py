"""Typed application errors.

Every failure the user can trigger has a stable ``code`` so the frontend can
branch on it, and a ``remedy`` describing what the caller should do next. This
replaces returning ``{"error": "..."}`` dictionaries with HTTP 200.
"""

from __future__ import annotations


class ImdqError(Exception):
    code = "internal_error"
    http_status = 500

    def __init__(self, message: str, *, remedy: str | None = None, **context: object) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy
        self.context = context

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        if self.remedy:
            payload["remedy"] = self.remedy
        if self.context:
            payload["context"] = self.context
        return payload


class UnsupportedFile(ImdqError):
    code = "unsupported_file"
    http_status = 415


class FileTooLarge(ImdqError):
    code = "file_too_large"
    http_status = 413


class IngestFailed(ImdqError):
    code = "ingest_failed"
    http_status = 422


class NotFound(ImdqError):
    code = "not_found"
    http_status = 404


class AmbiguousQuery(ImdqError):
    """Raised when slot resolution finds several equally plausible referents.

    Asking is the correct behaviour: a silently chosen station or column is a
    wrong answer that looks right.
    """

    code = "ambiguous_query"
    http_status = 409


class Unanswerable(ImdqError):
    code = "unanswerable"
    http_status = 422


class QueryFailed(ImdqError):
    code = "query_failed"
    http_status = 400


class StorageBusy(ImdqError):
    """The warehouse file is locked by another process.

    DuckDB permits one writing process per database file. In development this
    almost always means a reloader started a second server while the first was
    still shutting down.
    """

    code = "storage_busy"
    http_status = 503
