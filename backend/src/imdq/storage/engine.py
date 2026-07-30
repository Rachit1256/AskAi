"""A minimal SQL engine abstraction.

Production runs on DuckDB over Parquet: columnar, vectorised, and able to scan
partitioned files far larger than memory. Tests run on stdlib SQLite so the
whole stack is exercisable with no compiled dependency. Both accept the same
parameterised SQL, which is the point -- the query builder is never allowed to
emit engine-specific syntax.
"""

from __future__ import annotations

import contextlib
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from imdq.errors import QueryFailed, StorageBusy


class SqlEngine(ABC):
    """Read/write access to the warehouse. Implementations are not thread-safe;
    obtain one per request via the API dependency."""

    name: str = "abstract"

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

    @abstractmethod
    def fetch(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]: ...

    @abstractmethod
    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    def identity(self) -> str:
        """Which database this engine talks to. Part of every cache key, so two
        warehouses of the same shape cannot serve each other's answers."""
        return f"{self.name}:{getattr(self, '_identity', '?')}"

    def fetch_dicts(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        columns, rows = self.fetch(sql, params)
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        _, rows = self.fetch(sql, params)
        return rows[0][0] if rows else None

    def table_exists(self, name: str) -> bool:
        raise NotImplementedError

    def __enter__(self) -> SqlEngine:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SQLiteEngine(SqlEngine):
    name = "sqlite"

    def __init__(self, path: Path | str = ":memory:") -> None:
        import sqlite3

        # check_same_thread=False because FastAPI runs sync endpoints in a
        # threadpool; busy_timeout so a concurrent writer waits instead of
        # raising "database is locked".
        self._identity = str(path)
        # check_same_thread=False permits use from another thread; it does NOT
        # make one connection safe for *concurrent* use. Twelve threads calling
        # executemany at once corrupts the connection's internal state and raises
        # "bad parameter or other API misuse". The lock below serialises access.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15.0)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=15000")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        with self._lock:
            try:
                self._conn.execute(sql, tuple(params))
                self._conn.commit()
            except Exception as exc:
                raise QueryFailed(str(exc), remedy="Check the generated SQL.", sql=sql) from exc

    def fetch(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
        with self._lock:
            try:
                cursor = self._conn.execute(sql, tuple(params))
            except Exception as exc:
                raise QueryFailed(str(exc), remedy="Check the generated SQL.", sql=sql) from exc
            columns = [d[0] for d in cursor.description] if cursor.description else []
            return columns, cursor.fetchall()

    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> None:
        if not rows:
            return
        placeholders = ", ".join("?" for _ in columns)
        column_list = ", ".join(f'"{c}"' for c in columns)
        sql = f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders})'
        with self._lock:
            try:
                self._conn.executemany(sql, rows)
                self._conn.commit()
            except Exception as exc:
                raise QueryFailed(str(exc), remedy="Check the insert payload.", sql=sql) from exc

    def table_exists(self, name: str) -> bool:
        return bool(
            self.scalar("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,))
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


#: One DuckDB connection per database file per process, created on first use.
#:
#: DuckDB takes an exclusive lock on the database file, so opening a fresh
#: connection per request -- which is what this module did originally -- fails as
#: soon as two requests overlap, with "the process cannot access the file because
#: it is being used by another process" on Windows. The documented pattern is one
#: connection per process and a cursor per thread, which is what happens here.
_DUCKDB_POOL: dict[str, Any] = {}
_POOL_LOCK = threading.Lock()

LOCK_RETRIES = 5
LOCK_BACKOFF_S = 0.4


def _open_duckdb(key: str) -> Any:
    """Open a connection, waiting out a transient lock before giving up."""
    import duckdb

    last: Exception | None = None
    for attempt in range(LOCK_RETRIES):
        try:
            connection = duckdb.connect(key)
            connection.execute("SET enable_progress_bar=false")
            return connection
        except Exception as exc:  # duckdb.IOException and friends
            last = exc
            if "used by another process" not in str(exc) and "lock" not in str(exc).lower():
                raise
            time.sleep(LOCK_BACKOFF_S * (attempt + 1))

    raise StorageBusy(
        "The warehouse database is locked by another process.",
        remedy=(
            "Stop any other running server, then start one instance. In "
            "development, run uvicorn with --reload-dir src so writes to the "
            "database do not trigger a reload."
        ),
        path=key,
        detail=str(last),
    )


def _duckdb_connection(path: str) -> tuple[Any, bool]:
    """Return ``(connection, owned)`` for this database path.

    A file-backed database is pooled: one connection per file per process, shared
    by every request, because DuckDB locks the file exclusively.

    ``:memory:`` is NEVER pooled. Each in-memory connect creates a *separate*
    database, so caching it under one key hands every caller the same warehouse --
    which silently merged the whole test suite into a single database and made
    three tests fail in ways that looked unrelated to storage.
    """
    if path == ":memory:":
        return _open_duckdb(":memory:"), True

    key = str(Path(path).resolve())
    with _POOL_LOCK:
        existing = _DUCKDB_POOL.get(key)
        if existing is not None:
            return existing, False
        connection = _open_duckdb(key)
        _DUCKDB_POOL[key] = connection
        return connection, False


def close_connection(path: Path | str) -> None:
    """Close and forget one pooled database. Used by tests and by /catalog clear."""
    key = str(Path(path).resolve()) if str(path) != ":memory:" else ":memory:"
    with _POOL_LOCK:
        connection = _DUCKDB_POOL.pop(key, None)
    if connection is not None:
        with contextlib.suppress(Exception):
            connection.close()


def close_all_duckdb() -> None:
    """Close the process's database connections. Called on application shutdown."""
    with _POOL_LOCK:
        connections = list(_DUCKDB_POOL.values())
        _DUCKDB_POOL.clear()
    for connection in connections:
        with contextlib.suppress(Exception):
            connection.close()


class DuckDBEngine(SqlEngine):
    """A cursor onto the process-wide connection for this database file."""

    name = "duckdb"

    def __init__(self, path: Path | str = ":memory:", read_only: bool = False) -> None:
        del read_only  # the shared connection owns the mode
        self._identity = str(path)
        connection, owned = _duckdb_connection(str(path))
        self._owned = connection if owned else None
        # A cursor is DuckDB's unit of thread-safe concurrent access. FastAPI runs
        # sync endpoints in a threadpool, so each request needs its own.
        self._conn = connection.cursor()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        try:
            self._conn.execute(sql, list(params))
        except Exception as exc:
            raise QueryFailed(str(exc), remedy="Check the generated SQL.", sql=sql) from exc

    def fetch(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[str], list[tuple]]:
        try:
            cursor = self._conn.execute(sql, list(params))
        except Exception as exc:
            raise QueryFailed(str(exc), remedy="Check the generated SQL.", sql=sql) from exc
        columns = [d[0] for d in cursor.description] if cursor.description else []
        return columns, cursor.fetchall()

    def insert_many(self, table: str, columns: list[str], rows: list[tuple]) -> None:
        if not rows:
            return
        placeholders = ", ".join("?" for _ in columns)
        column_list = ", ".join(f'"{c}"' for c in columns)
        self._conn.executemany(
            f'INSERT INTO "{table}" ({column_list}) VALUES ({placeholders})', rows
        )

    def table_exists(self, name: str) -> bool:
        return bool(
            self.scalar(
                "SELECT 1 FROM information_schema.tables WHERE table_name = ?", (name,)
            )
        )

    def close(self) -> None:
        # A pooled connection outlives the request: only the cursor is closed,
        # because closing the connection would break every other request. An
        # in-memory database has no other user, so it is closed outright.
        with contextlib.suppress(Exception):
            self._conn.close()
        if self._owned is not None:
            with contextlib.suppress(Exception):
                self._owned.close()
            self._owned = None


def create_engine(path: Path | str, prefer: str = "duckdb") -> SqlEngine:
    """DuckDB when installed, SQLite otherwise. Both satisfy the same contract.

    A missing DuckDB falls through to SQLite. A *locked* DuckDB does not: silently
    switching engines would write the same data to two different files, so
    :class:`StorageBusy` is raised instead.
    """
    if prefer == "duckdb":
        try:
            return DuckDBEngine(path)
        except ImportError:
            pass
    suffix = Path(path).with_suffix(".sqlite") if str(path) != ":memory:" else ":memory:"
    return SQLiteEngine(suffix)
