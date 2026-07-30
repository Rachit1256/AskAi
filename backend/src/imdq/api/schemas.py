"""Request and response models for the HTTP edge."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    code: str
    message: str
    remedy: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    dataset_id: str
    filename: str
    as_of_date: str
    layout_fingerprint: str
    reused_layout: bool
    already_ingested: bool
    total_rows: int
    tables: list[dict[str, Any]]
    warnings: list[str] = Field(default_factory=list)


class ColumnOut(BaseModel):
    slug: str
    name: str
    role: str
    unit: str | None = None
    sql_type: str


class TableOut(BaseModel):
    table_id: str
    dataset_id: str
    filename: str
    sheet: str
    kind: str
    rows: int
    as_of_date: str
    context: dict[str, Any]
    columns: list[ColumnOut]


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=500)
    limit: int | None = Field(default=None, ge=1, le=100_000)
    #: Sent back after a 409 to confine resolution to the table the user picked.
    table_id: str | None = None


class AskResponse(BaseModel):
    answer: dict[str, Any]
    plan: dict[str, Any]
    slots: dict[str, Any]


class ReportRequest(BaseModel):
    title: str = "Data analysis report"
    questions: list[str] = Field(default_factory=list, max_length=50)
    include_dashboard: bool = True
    table_id: str | None = None
    download: bool = False


class PreviewRequest(BaseModel):
    table_id: str
    limit: int = Field(default=50, ge=1, le=1000)
    as_of: date | None = None
