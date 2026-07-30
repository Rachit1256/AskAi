"""Development server.

Use this rather than a bare ``uvicorn --reload``.

The reason is specific: the warehouse and lexicon live under ``var/``, inside the
directory the reloader watches by default. Every ingest writes to those files, the
reloader sees the change and restarts the server, and the restarting process then
fights the dying one for DuckDB's exclusive lock on the database file. The result
is a storm of "changes detected" followed by "the process cannot access the file
because it is being used by another process".

Watching only ``src`` fixes it: code changes still reload, data writes do not.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    uvicorn.run(
        "imdq.api.app:app",
        host=os.environ.get("IMDQ_HOST", "127.0.0.1"),
        port=int(os.environ.get("IMDQ_PORT", "8000")),
        reload=True,
        reload_dirs=[str(ROOT / "src")],
        reload_excludes=["var/*", "*.duckdb", "*.duckdb.wal", "*.sqlite*", "*.xlsx"],
        log_config=None,          # the app configures structured JSON logging
    )


if __name__ == "__main__":
    main()
