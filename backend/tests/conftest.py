from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from imdq.nlq.lexicon import Lexicon                                  
from imdq.storage.engine import close_connection, create_engine       
from imdq.storage.warehouse import ingest_file    
from tests.fixtures.make_workbook import build    

AS_OF = dt.date(2026, 7, 28)


@pytest.fixture(scope="session")
def workbook(tmp_path_factory) -> Path:
    return build(tmp_path_factory.mktemp("data") / "imd.xlsx")


@pytest.fixture()
def warehouse(workbook, tmp_path):
    """A file-backed warehouse, unique to each test.

    Deliberately not ``:memory:``. A file exercises the pooled connection path
    the server actually uses, and a unique path per test keeps them isolated --
    the combination that was missing when a pooled ``:memory:`` key quietly
    merged every test into one database.
    """
    path = tmp_path / "warehouse.duckdb"
    engine = create_engine(path)
    report = ingest_file(engine, workbook, as_of=AS_OF)
    lexicon = Lexicon()
    lexicon.build(engine)
    yield engine, lexicon, report
    lexicon.close()
    engine.close()
    close_connection(path)
