# imdq — IMD SATMET data query service

A rebuilt backend for multi-workbook ingestion, cross-table querying and report
generation. **No external model is called anywhere in the query path.**

- Sub-tables inside a sheet are discovered automatically — no templates.
- Questions are answered by deterministic slot resolution and SQL, not by an LLM.
- Every figure carries units, conventions and provenance back to the source cell.

---

## Why this is a rewrite rather than a refactor

The previous backend had four structural problems that could not be patched out.

| Old behaviour | Consequence | Now |
|---|---|---|
| `state.py` module-level dicts holding every DataFrame | Unbounded memory, nothing survives restart, concurrent requests share state | DuckDB/Parquet warehouse; nothing global |
| Row samples sent to Gemini on every turn (`query_engine._describe_table`) | Token exhaustion after ~5 questions; arithmetic done by a language model | Zero model calls; SQL computes, templates narrate |
| ChromaDB + onnxruntime RAG over spreadsheet rows | 80 MB of native deps to retrieve chunks that cannot produce a correct `SUM` | SQLite FTS5 + BM25 over a *catalog*, stdlib only |
| matplotlib PNGs written to `charts/<uuid>.png` | Disk grows forever, images are dead artefacts | Vega-Lite specs; the browser renders |

Two further changes matter for correctness rather than cost:

1. **A time filter that cannot be applied now raises** instead of being dropped.
   Silently answering for the wrong period is the most dangerous bug available
   in meteorological reporting — it looks right.
2. **Ambiguity is surfaced, not guessed.** `Delhi` maps to four station indices;
   `rainfall` may exist on several sheets. The API returns `409` with the
   candidates so the caller can choose.

---

## Layout

```
src/imdq/
├── config.py            Typed settings; wildcard CORS is rejected at load
├── errors.py            Error codes + remedies; every failure is typed
├── logging_setup.py     JSON logs with a request id
├── domain/imd.py        Seasons, regions, departure bands, units, station aliases
├── ingest/
│   ├── grid.py          Cell feature planes over a bounded probe window
│   ├── blocks.py        The Block model
│   ├── segmenter.py     Banner peel → XY-cut → components → header → classify
│   ├── normalize.py     Trace/sentinel handling, roles, derived-column detection
│   └── pipeline.py      analyse() once, stream() forever
├── storage/
│   ├── engine.py        SqlEngine protocol; DuckDB + SQLite adapters
│   ├── catalog.py       Datasets, tables, columns, checksums
│   └── warehouse.py     Physical tables, provenance, idempotency
├── nlq/
│   ├── timeparse.py     IMD seasons, months, ranges, relative periods
│   ├── lexicon.py       FTS5 index over columns *and* dimension values
│   ├── resolver.py      Slot filling with fuzzy rescue and ambiguity detection
│   ├── planner.py       Intent from slot shape — no classifier
│   ├── sqlbuilder.py    Validated identifiers, bound parameters
│   ├── nlg.py           Deterministic professional answers
│   └── service.py       ask()
├── analytics/dashboard.py   Ranked, de-duplicated Vega-Lite specs
├── report/builder.py        Structured session → HTML
└── api/                     FastAPI edge
```

## Running

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -e ".[dev,fuzzy]"
copy .env.example .env
python run_dev.py
pytest                      # or: python run_tests.py  (no pytest needed)
```

**Use `run_dev.py`, not a bare `uvicorn --reload`.** The warehouse lives under
`var/`, inside the directory the reloader watches by default. Every ingest writes
to it, the reloader sees the change and restarts, and the restarting process then
fights the dying one for DuckDB's exclusive lock on the file — which shows up as a
storm of `changes detected` followed by `the process cannot access the file
because it is being used by another process`. `run_dev.py` watches only `src`, so
code changes reload and data writes do not.

If you already hit that: stop every running server, then delete `backend/var/`
for a clean start. The lock is held by a process, not written into the file, so
nothing is corrupted — but a half-initialised warehouse is not worth debugging.

DuckDB is used when installed and SQLite otherwise; both satisfy the same
`SqlEngine` contract, so the test suite runs with zero compiled dependencies.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Status, engine in use, row counts |
| `POST` | `/ingest` | Ingest a workbook (idempotent on content hash) |
| `POST` | `/ingest/analyse` | Dry run — report detected blocks, write nothing |
| `GET` | `/catalog/tables` | Registered tables and their physical schema |
| `GET` | `/catalog/datasets` | Ingested files with row counts and dates |
| `GET` | `/catalog/tables/{id}/profile` | Statistics, dimensions, observations, checksums |
| `GET` | `/catalog/tables/{id}/preview` | Sample rows |
| `DELETE` | `/catalog/datasets/{id}` | Soft delete; history retained |
| `DELETE` | `/catalog` | Remove everything (irreversible) |
| `POST` | `/query/ask` | Answer a question |
| `POST` | `/query/resolve` | Show what a question resolves to, without running it |
| `GET` | `/query/suggestions` | Question shapes that resolve against this catalog |
| `GET` | `/dashboard` | Ranked chart specs plus suggested visualisations |
| `POST` | `/report` | HTML report from a list of questions |

### Errors

Every failure leaves through one envelope, with a real HTTP status:

```json
{ "code": "ambiguous_query", "message": "...", "remedy": "...", "context": {...} }
```

That covers typed application errors, request-validation failures (422 with a
per-field `problems` list), unmatched routes, and unhandled exceptions — which
return a correlation id and nothing about the internals. Nothing returns a
failure as HTTP 200 with an `{"error": ...}` body.

`409 ambiguous_query` carries `context.candidates`; send the chosen `table_id`
back with the same question to resolve it.

### Search

Retrieval is BM25 over SQLite FTS5, in the standard library. Four things it does
beyond a plain match:

- **Value indexing** — every distinct value of every low-cardinality dimension is
  indexed, so "rainfall at Pune" finds the right table without naming it.
- **Exact-match boosting** — BM25 alone ranks a long fuzzy match above a short
  exact one; a whole-token hit on a column name or a value is boosted explicitly.
- **Singular/plural** — FTS5 prefix search matches forward only, so "months"
  would never reach a column called "month" without an explicit variant.
- **One query per question, not per token.** `match_values()` searches the whole
  question once, then verifies each candidate against the question's own tokens.
  The previous version issued one query per word and could match a value on an
  unrelated stray token.

Indexing is incremental: an ingest indexes only that dataset instead of re-reading
every distinct value already in the warehouse. Search results and answers are both
cached, keyed on a catalog fingerprint so an ingest invalidates them.

`/query/ask` returns the answer, the plan that produced it, and the SQL that ran.
Showing the SQL is deliberate: auditability is what makes fuzzy matching
acceptable in a departmental tool.

## How a question is answered

```
question
  → parse_time()      IMD seasons, months, years, "last N years"
  → lexicon.search()  BM25 over column names, synonyms and dimension VALUES
  → resolve()         measure, group-by, filters, aggregation, ambiguity check
  → plan()            intent read off the slot shape
  → build()           validated identifiers, bound parameters
  → engine.fetch()    exact aggregates in DuckDB
  → narrate()         headline, table, assumptions, provenance
```

The value index is what lets *"rainfall at Pune"* find the right table without
the user naming it: `pune` is indexed against the column that contains it,
alongside its former names (`poona`, `calcutta`→`kolkata`, `allahabad`→`prayagraj`).

Coverage is roughly 70–85% of real operational questions. The tail is refused
cleanly with a remedy rather than answered badly. `IMDQ_ENABLE_LOCAL_LLM_FALLBACK`
reserves a hook for a **self-hosted** model (Ollama/sqlcoder) for that tail —
still schema-only, still no external API.

## IMD conventions enforced

- **Trace** (`T`) stored as `0.0` with `is_trace`, never null, never dropped.
- **Missing sentinels** (`-999`, `999.9`, …) never coerced to zero.
- **Seasons** JF / MAM / JJAS / OND per the Data Service Portal.
- **Derived columns** (`Annual`, `JJAS`) detected *numerically*, not by name, and
  excluded from the melt — otherwise every total roughly doubles.
- **Totals rows** removed from the data and reused as an ingest checksum.
- **Rainfall day** 0830–0830 IST stated on every rainfall aggregate.
- **Station identity** keyed on the five-digit index, with a former-name alias map.
- Normals period `1991-2020` and the departure bands live in `domain/imd.py`.
  **Verify the band cutoffs against the current departmental circular** before
  production use.

## Known gaps

- Geography rollups are modelled (`GEO_HIERARCHY`, `AREA_WEIGHTED_LEVELS`) but the
  station→district→subdivision master table is not populated. Until it is,
  subdivisional figures will not reproduce IMD's published values, which are
  area-weighted rather than simple means.
- Normals are ingested as ordinary tables; the automatic departure-from-normal
  join is stubbed in `nlg.narrate(normal_value=...)` and needs the normals
  registered as a first-class dimension.
- Raster products (INSAT granules) are out of scope here: catalog them separately
  and land per-subdivision zonal statistics as ordinary fact rows.
- `.csv`/`.tsv` are accepted by the API but the segmenter currently reads
  workbooks only; add a CSV grid loader before enabling them.
