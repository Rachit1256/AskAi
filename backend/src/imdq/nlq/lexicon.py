"""The searchable index of *what the user can ask about*.

Built once at ingest from catalog metadata plus the distinct values of every
low-cardinality dimension. Retrieval is BM25 over SQLite FTS5 -- in the standard
library, so there is no vector database, no embedding model and no external
service anywhere in the query path.

The value index is what lets "rainfall at Pune" find the right table without the
user naming it: the term "pune" is indexed against the column that contains it.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from imdq.domain.imd import STATION_NAME_ALIASES, normalise_station_name
from imdq.storage.catalog import TableInfo, list_tables
from imdq.storage.engine import SqlEngine

MAX_DISTINCT_VALUES = 2_000
HIGH_CARDINALITY_LIMIT = 5_000
_TOKEN = re.compile(r"[a-z0-9]+")

#: Vocabulary users bring that never appears in a column header.
MEASURE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "rainfall": ("rain", "precipitation", "rf", "precip", "showers"),
    "temperature": ("temp", "thermal"),
    "max_temp": ("maximum temperature", "highest temperature", "tmax"),
    "min_temp": ("minimum temperature", "lowest temperature", "tmin"),
    "humidity": ("rh", "relative humidity"),
    "pressure": ("mslp", "barometric"),
    "wind": ("windspeed", "gust"),
    "olr": ("outgoing longwave radiation",),
    "sst": ("sea surface temperature",),
    "observations": ("obs", "count", "records"),
    "rainy_days": ("wet days", "rain days"),
}

LEXICON_DDL = (
    """
    CREATE TABLE IF NOT EXISTS lex_entry (
        entry_id       INTEGER PRIMARY KEY,
        kind           TEXT NOT NULL,
        dataset_id     TEXT NOT NULL DEFAULT '',
        column_display TEXT,
        table_id      TEXT NOT NULL,
        physical_name TEXT NOT NULL,
        column_slug   TEXT,
        role          TEXT,
        unit          TEXT,
        display       TEXT NOT NULL,
        value         TEXT
    )
    """,
    "CREATE VIRTUAL TABLE IF NOT EXISTS lex_fts USING fts5(terms, entry_id UNINDEXED)",
    "CREATE INDEX IF NOT EXISTS ix_lex_kind ON lex_entry (kind)",
    "CREATE INDEX IF NOT EXISTS ix_lex_dataset ON lex_entry (dataset_id)",
    "CREATE INDEX IF NOT EXISTS ix_lex_table ON lex_entry (table_id)",
)


@dataclass(slots=True)
class LexHit:
    entry_id: int
    kind: str
    table_id: str
    physical_name: str
    column_slug: str | None
    role: str | None
    unit: str | None
    display: str
    column_display: str
    value: str | None
    score: float

    def key(self) -> tuple[str, str | None, str | None]:
        return (self.table_id, self.column_slug, self.value)


def tokenise(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _terms_for_column(table: TableInfo, column: dict[str, Any]) -> str:
    parts = [column["name"], column["slug"], table.sheet, table.filename]
    for canonical, synonyms in MEASURE_SYNONYMS.items():
        if canonical in column["slug"] or canonical.replace("_", "") in column["slug"]:
            parts.extend(synonyms)
            parts.append(canonical)
    if column.get("unit"):
        parts.append(str(column["unit"]))
    return " ".join(tokenise(" ".join(str(p) for p in parts if p)))


def _terms_for_value(raw: str) -> str:
    normalised = normalise_station_name(str(raw))
    parts = [str(raw), normalised]
    parts.extend(
        old for old, new in STATION_NAME_ALIASES.items() if new == normalised
    )
    return " ".join(dict.fromkeys(tokenise(" ".join(parts))))


class Lexicon:
    """Owns the FTS index. Cheap to rebuild; rebuild after every ingest."""

    def __init__(self, path: Path | str = ":memory:", cache_size: int = 512) -> None:
        self._search_cache: OrderedDict[tuple[str, tuple[str, ...], int], list[LexHit]] = (
            OrderedDict()
        )
        self._cache_size = cache_size
        # One Lexicon serves the whole process, and FastAPI runs sync endpoints
        # in a threadpool, so the connection must tolerate multiple threads and
        # wait out a concurrent writer rather than raising.
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        self._lock = threading.RLock()
        for statement in LEXICON_DDL:
            self._conn.execute(statement)
        self._conn.commit()

    def clear(self) -> None:
        self._conn.execute("DELETE FROM lex_entry")
        self._conn.execute("DELETE FROM lex_fts")
        self._conn.commit()
        self._search_cache.clear()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _add(self, terms: str, **fields: Any) -> None:
        cursor = self._conn.execute(
            """
            INSERT INTO lex_entry
                (kind, dataset_id, table_id, physical_name, column_slug, role, unit,
                 display, column_display, value)
            VALUES (:kind, :dataset_id, :table_id, :physical_name, :column_slug, :role,
                    :unit, :display, :column_display, :value)
            """,
            {
                "column_slug": None, "role": None, "unit": None, "value": None,
                "column_display": None, "dataset_id": "", **fields
            },
        )
        self._conn.execute(
            "INSERT INTO lex_fts (terms, entry_id) VALUES (?, ?)", (terms, cursor.lastrowid)
        )

    def build(self, engine: SqlEngine,
        tables: Iterable[TableInfo] | None = None) -> int:  # noqa: D401
        """Full rebuild. Prefer :meth:`index_dataset` after an ingest -- a full
        rebuild re-reads every distinct value of every dimension in the
        warehouse, which is wasted work when only one file changed."""
        with self._lock:
            self.clear()
            return self._index(
                engine, list(tables) if tables is not None else list_tables(engine)
            )

    def index_dataset(self, engine: SqlEngine, dataset_id: str) -> int:
        """Index just-ingested tables, leaving existing entries untouched."""
        with self._lock:
            self._remove_dataset(dataset_id)
            return self._index(
                engine, list_tables(engine, dataset_id=dataset_id), dataset_id
            )

    def remove_dataset(self, dataset_id: str) -> None:
        with self._lock:
            self._remove_dataset(dataset_id)

    def _remove_dataset(self, dataset_id: str) -> None:
        ids = [
            row["entry_id"]
            for row in self._conn.execute(
                "SELECT entry_id FROM lex_entry WHERE dataset_id = ?", (dataset_id,)
            ).fetchall()
        ]
        if not ids:
            return
        marks = ", ".join("?" for _ in ids)
        self._conn.execute(f"DELETE FROM lex_fts WHERE entry_id IN ({marks})", ids)
        self._conn.execute("DELETE FROM lex_entry WHERE dataset_id = ?", (dataset_id,))
        self._conn.commit()
        self._search_cache.clear()

    def _index(
        self, engine: SqlEngine, tables: list[TableInfo], dataset_id: str = ""
    ) -> int:
        count = 0
        for table in tables:
            owner = dataset_id or table.dataset_id
            self._add(
                " ".join(tokenise(f"{table.sheet} {table.filename} {table.kind}")),
                kind="table", dataset_id=owner, table_id=table.table_id,
                physical_name=table.physical_name,
                display=f"{table.filename} / {table.sheet}",
                column_display=table.sheet,
            )
            count += 1
            for column in table.columns:
                if column["is_derived"]:
                    continue
                self._add(
                    _terms_for_column(table, column),
                    kind=column["role"], dataset_id=owner, table_id=table.table_id,
                    physical_name=table.physical_name, column_slug=column["slug"],
                    role=column["role"], unit=column["unit"],
                    display=f"{column['name']} ({table.sheet})",
                    column_display=str(column["name"]),
                )
                count += 1
                if column["role"] in ("dimension", "identifier"):
                    count += self._index_values(engine, table, column, owner)
        self._conn.commit()
        self._search_cache.clear()
        return count

    def _index_values(
        self, engine: SqlEngine, table: TableInfo, column: dict[str, Any], owner: str = ""
    ) -> int:
        slug = column["slug"]
        cardinality = engine.scalar(
            f'SELECT COUNT(DISTINCT "{slug}") FROM "{table.physical_name}"'
        )
        if not cardinality or cardinality > HIGH_CARDINALITY_LIMIT:
            return 0
        _, rows = engine.fetch(
            f'SELECT DISTINCT "{slug}" FROM "{table.physical_name}" '
            f'WHERE "{slug}" IS NOT NULL LIMIT {MAX_DISTINCT_VALUES}'
        )
        added = 0
        for (raw,) in rows:
            text = str(raw).strip()
            if not text:
                continue
            self._add(
                _terms_for_value(text),
                kind="value", dataset_id=owner, table_id=table.table_id,
                physical_name=table.physical_name,
                column_slug=slug, role=column["role"], unit=column["unit"],
                display=f"{text} ({column['name']})",
                column_display=str(column["name"]), value=text,
            )
            added += 1
        return added

    def search(
        self, text: str, kinds: tuple[str, ...] | None = None, limit: int = 12
    ) -> list[LexHit]:
        key = (text.lower(), kinds or (), limit)
        cached = self._search_cache.get(key)
        if cached is not None:
            self._search_cache.move_to_end(key)
            return cached

        tokens = tokenise(text)
        hits: list[LexHit] = []
        if tokens:
            hits = self._run_search(tokens, kinds, limit)
        self._search_cache[key] = hits
        if len(self._search_cache) > self._cache_size:
            self._search_cache.popitem(last=False)
        return hits

    def _run_search(
        self, tokens: list[str], kinds: tuple[str, ...] | None, limit: int
    ) -> list[LexHit]:
        # Reads share the connection with writes and were unguarded. One SQLite
        # connection is not safe for concurrent use, whatever check_same_thread
        # says, and the search cache is a plain OrderedDict.
        # Prefix matching so "rain" finds "rainfall", plus a naive singular so
        # "months" finds a column called "month". FTS5 prefix search matches
        # forward only, so the plural form alone would never hit the singular.
        variants: list[str] = []
        for token in tokens:
            if len(token) <= 1:
                continue
            variants.append(f"{token}*")
            if len(token) > 3 and token.endswith("s"):
                variants.append(f"{token[:-1]}*")
        if not variants:
            return []

        clause = ""
        params: list[Any] = [" OR ".join(dict.fromkeys(variants))]
        if kinds:
            clause = f"AND e.kind IN ({', '.join('?' for _ in kinds)})"
            params.extend(kinds)
        params.append(limit * 3)

        with self._lock:
            rows = self._conn.execute(
                f"""
            SELECT e.*, bm25(lex_fts) AS score
            FROM lex_fts JOIN lex_entry e ON e.entry_id = lex_fts.entry_id
            WHERE lex_fts MATCH ? {clause}
            ORDER BY score LIMIT ?
            """,
                params,
            ).fetchall()

        token_set = set(tokens)
        hits: list[LexHit] = []
        for row in rows:
            hit = LexHit(
                entry_id=row["entry_id"], kind=row["kind"], table_id=row["table_id"],
                physical_name=row["physical_name"], column_slug=row["column_slug"],
                role=row["role"], unit=row["unit"], display=row["display"],
                column_display=row["column_display"] or row["display"],
                value=row["value"], score=-float(row["score"]),
            )
            # BM25 alone ranks a long fuzzy match above a short exact one. An
            # exact whole-token hit on the name or the value is what the user
            # almost always meant, so it is boosted explicitly.
            target = tokenise(hit.value or hit.column_display or "")
            if target and set(target) <= token_set:
                hit.score += 4.0
            elif target and token_set & set(target):
                hit.score += 1.0
            hits.append(hit)

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def match_values(self, text: str, limit: int = 24) -> list[LexHit]:
        """Find dimension values named anywhere in the question, in ONE query.

        The resolver previously issued one FTS query per token, so a ten-word
        question cost ten round trips and could match a value on an unrelated
        stray token. Here the whole question is searched once and each candidate
        is then verified against the question's own tokens.
        """
        tokens = set(tokenise(text))
        if not tokens:
            return []
        candidates = self.search(text, kinds=("value",), limit=limit * 2)
        confirmed = [
            hit
            for hit in candidates
            if (value_tokens := set(tokenise(hit.value or ""))) and value_tokens <= tokens
        ]
        return confirmed[:limit]
