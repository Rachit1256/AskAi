"""Connection lifecycle.

These cover the failure that took the first deployment offline: a DuckDB
connection opened per request, which fails the moment two requests overlap
because DuckDB holds an exclusive lock on the database file.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from imdq.errors import StorageBusy
from imdq.storage import engine as engine_module
from imdq.storage.engine import DuckDBEngine, SQLiteEngine, create_engine


class _FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.closed = False

    def execute(self, *_args, **_kwargs):
        return self

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, path):
        self.path = path
        self.cursors = 0
        self.closed = False

    def execute(self, *_args, **_kwargs):
        return self

    def cursor(self):
        self.cursors += 1
        return _FakeCursor(self)

    def close(self):
        self.closed = True


def _install_fake_duckdb(monkeypatch, connect):
    module = types.ModuleType("duckdb")
    module.connect = connect
    monkeypatch.setitem(sys.modules, "duckdb", module)
    engine_module.close_all_duckdb()


def test_one_connection_per_process_many_cursors(monkeypatch, tmp_path):
    """Twenty overlapping requests must open the file once, not twenty times."""
    opened: list[str] = []

    def connect(path):
        opened.append(path)
        return _FakeConnection(path)

    _install_fake_duckdb(monkeypatch, connect)
    target = tmp_path / "warehouse.duckdb"

    engines = []
    errors: list[Exception] = []

    def worker():
        try:
            engines.append(DuckDBEngine(target))
        except Exception as exc:      
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert len(opened) == 1, f"opened the database file {len(opened)} times"
    assert len(engines) == 20

    # Releasing a request closes its cursor, never the shared connection.
    connection = engines[0]._conn.owner
    for item in engines:
        item.close()
    assert connection.closed is False
    engine_module.close_all_duckdb()
    assert connection.closed is True


def test_relative_and_absolute_paths_share_one_connection(monkeypatch, tmp_path, ):
    opened: list[str] = []

    def connect(path):
        opened.append(path)
        return _FakeConnection(path)

    _install_fake_duckdb(monkeypatch, connect)
    target = tmp_path / "warehouse.duckdb"

    DuckDBEngine(target)
    DuckDBEngine(str(target))
    DuckDBEngine(target.resolve())

    assert len(opened) == 1
    engine_module.close_all_duckdb()


def test_in_memory_databases_are_never_shared(monkeypatch, tmp_path):
    """Every :memory: connect creates a separate database.

    Pooling them under one key handed every caller the same warehouse. In the
    test suite that merged thirty tests into one database and surfaced as three
    unrelated-looking assertion failures.
    """
    del tmp_path
    opened: list[str] = []

    def connect(path):
        opened.append(path)
        return _FakeConnection(path)

    _install_fake_duckdb(monkeypatch, connect)

    first = DuckDBEngine(":memory:")
    second = DuckDBEngine(":memory:")

    assert len(opened) == 2, "in-memory databases must not be pooled"
    assert first._conn.owner is not second._conn.owner


def test_in_memory_engine_closes_its_own_connection(monkeypatch, tmp_path):
    del tmp_path

    def connect(path):
        return _FakeConnection(path)

    _install_fake_duckdb(monkeypatch, connect)

    engine = DuckDBEngine(":memory:")
    connection = engine._conn.owner
    engine.close()
    assert connection.closed is True, "an in-memory database has no other user"


def test_locked_database_raises_rather_than_switching_engines(monkeypatch, tmp_path):
    """Falling back to SQLite here would write the same data to two files."""

    def connect(_path):
        raise OSError(
            "IO Error: Cannot open file: The process cannot access the file "
            "because it is being used by another process."
        )

    _install_fake_duckdb(monkeypatch, connect)
    monkeypatch.setattr(engine_module, "LOCK_BACKOFF_S", 0.0)

    with pytest.raises(StorageBusy) as caught:
        create_engine(tmp_path / "warehouse.duckdb")

    assert caught.value.remedy and "reload-dir" in caught.value.remedy
    engine_module.close_all_duckdb()


def test_missing_duckdb_does_fall_back_to_sqlite(monkeypatch, tmp_path):
    def connect(_path):
        raise ImportError("no duckdb")

    _install_fake_duckdb(monkeypatch, connect)
    built = create_engine(tmp_path / "warehouse.duckdb")
    assert built.name == "sqlite"
    built.close()


def test_sqlite_engine_survives_threadpool_use(tmp_path):
    """FastAPI runs sync endpoints in a threadpool, so the connection must allow it.

    ``check_same_thread=False`` permits use from another thread but does NOT make
    one connection safe for concurrent use: simultaneous executemany calls
    corrupt its internal state and raise "bad parameter or other API misuse".
    """
    engine = SQLiteEngine(tmp_path / "w.sqlite")
    engine.execute("CREATE TABLE t (n INTEGER)")

    errors: list[Exception] = []

    def worker(value: int):
        try:
            engine.insert_many("t", ["n"], [(value,)])
        except Exception as exc:     
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert engine.scalar("SELECT COUNT(*) FROM t") == 12
    engine.close()


def test_sqlite_engine_survives_interleaved_reads_and_writes(tmp_path):
    """The realistic pattern: requests reading while an ingest writes."""
    engine = SQLiteEngine(tmp_path / "w.sqlite")
    engine.execute("CREATE TABLE t (n INTEGER)")

    errors: list[Exception] = []

    def worker(value: int):
        try:
            for _ in range(20):
                engine.insert_many("t", ["n"], [(value,)])
                engine.fetch("SELECT COUNT(*) FROM t")
        except Exception as exc:     
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert engine.scalar("SELECT COUNT(*) FROM t") == 160
    engine.close()


def test_lexicon_search_is_concurrent_safe(tmp_path):
    """One lexicon serves the whole process, so searches overlap constantly.

    Reads share the connection with writes and the search cache is a plain
    OrderedDict; neither was guarded.
    """
    from imdq.nlq.lexicon import Lexicon

    lexicon = Lexicon(tmp_path / "lex.sqlite")
    errors: list[Exception] = []

    def worker(_: int):
        try:
            for _ in range(50):
                lexicon.search("rainfall pune monsoon station", limit=5)
        except Exception as exc:     
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    lexicon.close()
