"""Request-scoped resources over process-wide connections.

The distinction matters. Each request gets its own *cursor* -- cheap, isolated,
and safe to use from the threadpool -- but the underlying database connection is
opened once for the life of the process. Opening a DuckDB connection per request
fails as soon as two requests overlap, because DuckDB holds an exclusive lock on
the database file.

Schema creation happens once at startup (see ``api/app.py``), not on every
request as it did originally.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from imdq.config import Settings, get_settings
from imdq.nlq.lexicon import Lexicon
from imdq.storage.engine import SqlEngine, create_engine

_LEXICON_LOCK = threading.Lock()
_LEXICON: Lexicon | None = None


def settings_dep() -> Settings:
    return get_settings()


def engine_dep() -> Iterator[SqlEngine]:
    """A cursor onto the shared warehouse connection, released after the request."""
    engine = create_engine(get_settings().warehouse_path)
    try:
        yield engine
    finally:
        engine.close()


def get_lexicon() -> Lexicon:
    """One lexicon per process. SQLite handles the concurrency; see Lexicon."""
    global _LEXICON
    with _LEXICON_LOCK:
        if _LEXICON is None:
            settings = get_settings()
            settings.ensure_dirs()
            _LEXICON = Lexicon(settings.lexicon_path)
        return _LEXICON


def lexicon_dep() -> Lexicon:
    return get_lexicon()


def close_lexicon() -> None:
    global _LEXICON
    with _LEXICON_LOCK:
        if _LEXICON is not None:
            _LEXICON.close()
            _LEXICON = None
