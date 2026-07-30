"""FastAPI application factory.

Every failure leaves through the same envelope -- ``{code, message, remedy}`` --
whether it is a typed application error, a request-validation failure, or an
unhandled exception. The previous build returned some errors as HTTP 200 with an
``{"error": ...}`` body, which meant the frontend could not tell success from
failure without inspecting the payload.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from imdq.api.deps import close_lexicon
from imdq.api.routes import catalog, dashboard, health, ingest, query, report
from imdq.config import Settings, get_settings
from imdq.errors import ImdqError
from imdq.logging_setup import configure_logging, get_logger, new_request_id, request_id_var
from imdq.storage.catalog import init_catalog
from imdq.storage.engine import close_all_duckdb, create_engine

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Schema creation happens once here, not on every request. Opening the
        # connection at startup also surfaces a locked database file immediately
        # instead of on the first query.
        settings.ensure_dirs()
        engine = create_engine(settings.warehouse_path)
        try:
            init_catalog(engine)
            log.info(
                "warehouse ready",
                extra={"extra_fields": {"engine": engine.name,
                                        "path": str(settings.warehouse_path)}},
            )
        finally:
            engine.close()
        try:
            yield
        finally:
            close_lexicon()
            close_all_duckdb()

    app = FastAPI(
        lifespan=lifespan,
        title="IMD SATMET data query service",
        version="1.2.0",
        description=(
            "Multi-workbook ingestion, cross-table query and reporting. "
            "The query path calls no external model."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
        expose_headers=["X-Request-Id"],
    )

    @app.middleware("http")
    async def correlate(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation = request.headers.get("x-request-id") or new_request_id()
        token = request_id_var.set(correlation)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Logged here so the entry still carries the correlation id; the
            # exception handlers below turn it into a response.
            log.exception(
                "request failed",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
            raise
        else:
            response.headers["x-request-id"] = correlation
            # Must be logged BEFORE the context var is reset, or every line reads
            # request_id "-" -- which is exactly what the first deployment did.
            log.info(
                "request complete",
                extra={
                    "extra_fields": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    def envelope(status: int, code: str, message: str, remedy: str | None = None, **context):
        body: dict[str, object] = {"code": code, "message": message}
        if remedy:
            body["remedy"] = remedy
        if context:
            body["context"] = context
        return JSONResponse(status_code=status, content=body)

    @app.exception_handler(ImdqError)
    async def handle_known(_: Request, exc: ImdqError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"field": ".".join(str(p) for p in err.get("loc", [])[1:]), "issue": err.get("msg")}
            for err in exc.errors()
        ]
        first = problems[0] if problems else {"field": "", "issue": "invalid request"}
        return envelope(
            422,
            "invalid_request",
            f"{first['field'] or 'Request'}: {first['issue']}.",
            "Correct the highlighted field and send the request again.",
            problems=problems,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 405: "method_not_allowed", 413: "file_too_large"}
        return envelope(
            exc.status_code,
            codes.get(exc.status_code, "request_failed"),
            str(exc.detail),
            "Check the request path and method against /docs.",
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Already logged by the middleware with its correlation id; the client
        # gets that id and nothing about the internals.
        return envelope(
            500,
            "internal_error",
            "The request failed inside the service.",
            "Report the request id shown alongside this message.",
            request_id=request_id_var.get(),
        )

    for module in (health, ingest, catalog, query, dashboard, report):
        app.include_router(module.router)

    return app


app = create_app()
