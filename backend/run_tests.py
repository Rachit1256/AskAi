"""Minimal runner for environments without pytest installed."""
from __future__ import annotations

import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import datetime as dt

from imdq.nlq.lexicon import Lexicon
from imdq.storage.engine import create_engine
from imdq.storage.warehouse import ingest_file
from tests.fixtures.make_workbook import build


class _Raises:
    def __init__(self, exc): self.exc = exc
    def __enter__(self): return self
    def __exit__(self, t, v, tb): return t is not None and issubclass(t, self.exc)


class _MonkeyPatch:
    """Enough of pytest's monkeypatch for the storage tests."""

    def __init__(self):
        self._undo = []

    def setitem(self, mapping, key, value):
        had = key in mapping
        old = mapping.get(key)
        self._undo.append(lambda: mapping.__setitem__(key, old) if had else mapping.pop(key, None))
        mapping[key] = value

    def setattr(self, target, name, value):
        old = getattr(target, name)
        self._undo.append(lambda: setattr(target, name, old))
        setattr(target, name, value)

    def close(self):
        for undo in reversed(self._undo):
            undo()


class _RaisesCtx(_Raises):
    def __init__(self, exc):
        super().__init__(exc)
        self.value = None

    def __exit__(self, t, v, tb):
        if t is not None and issubclass(t, self.exc):
            self.value = v
            return True
        return False


class _Pytest:
    raises = staticmethod(lambda exc: _RaisesCtx(exc))


sys.modules["pytest"] = _Pytest  # type: ignore[assignment]

tmp = Path(tempfile.mkdtemp())
WORKBOOK = build(tmp / "imd.xlsx")

# State the engine plainly. This runner once reported "34 passed" on SQLite while
# three tests failed under DuckDB, because the fallback was invisible.
_probe = create_engine(":memory:")
print(f"storage engine: {_probe.name}")
if _probe.name != "duckdb":
    print("  NOTE: DuckDB is not installed, so the pooled-connection path is")
    print("  NOT exercised here. Run `pytest` in the real environment before")
    print("  trusting a green result.")
_probe.close()
print()


def make_warehouse():
    engine = create_engine(Path(tempfile.mkdtemp()) / "warehouse.duckdb")
    report = ingest_file(engine, WORKBOOK, as_of=dt.date(2026, 7, 28))
    lexicon = Lexicon()
    lexicon.build(engine)
    return engine, lexicon, report


import tests.test_ingest as t_ingest      # noqa: E402
import tests.test_storage as t_storage    # noqa: E402
import tests.test_query as t_query        # noqa: E402
import tests.test_reporting as t_report   # noqa: E402

passed = failed = 0
for module in (t_ingest, t_query, t_report, t_storage):
    for name in sorted(n for n in dir(module) if n.startswith("test_")):
        func = getattr(module, name)
        params = func.__code__.co_varnames[: func.__code__.co_argcount]
        kwargs = {}
        if "warehouse" in params:
            kwargs["warehouse"] = make_warehouse()
        if "workbook" in params:
            kwargs["workbook"] = WORKBOOK
        if "tmp_path" in params:
            kwargs["tmp_path"] = Path(tempfile.mkdtemp())
        if "workbook" in params and "warehouse" not in params:
            kwargs.setdefault("workbook", WORKBOOK)
        patcher = None
        if "monkeypatch" in params:
            patcher = _MonkeyPatch()
            kwargs["monkeypatch"] = patcher
        try:
            func(**kwargs)
            passed += 1
            print(f"  PASS  {module.__name__}.{name}")
        except Exception:
            failed += 1
            print(f"  FAIL  {module.__name__}.{name}")
            traceback.print_exc(limit=3)
        finally:
            if patcher is not None:
                patcher.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
