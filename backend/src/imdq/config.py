"""Application settings. All configuration is explicit, validated and typed.

Nothing reads ``os.environ`` outside this module, and there are no import-time
side effects, so tests can instantiate ``Settings`` with overrides directly.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IMDQ_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"

    data_dir: Path = Path("./var/data")
    warehouse_path: Path = Path("./var/warehouse.duckdb")

    max_upload_mb: int = Field(default=256, ge=1, le=4096)
    query_row_limit: int = Field(default=5_000, ge=1, le=1_000_000)
    query_timeout_s: float = Field(default=30.0, gt=0)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # Optional local model used ONLY when deterministic slot resolution fails.
    # No external API is ever called; this points at a self-hosted runtime.
    enable_local_llm_fallback: bool = False
    local_llm_url: str = "http://127.0.0.1:11434"
    local_llm_model: str = "sqlcoder:7b"

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard(cls, v: list[str]) -> list[str]:
        if "*" in v:
            raise ValueError(
                "Wildcard CORS origin is not permitted; list the frontend origins explicitly."
            )
        return v

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def parquet_dir(self) -> Path:
        return self.data_dir / "parquet"

    @property
    def lexicon_path(self) -> Path:
        return self.data_dir / "lexicon.sqlite"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.parquet_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.warehouse_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
