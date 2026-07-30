# Backend image. Deliberately host-agnostic: the same image runs on Render,
# Railway, Fly, or a departmental VM behind nginx.
FROM python:3.12-slim

# Build tools are needed for a handful of wheels; removed in the same layer so
# they do not ship in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --upgrade pip && pip install -e ".[fuzzy]" \
    && apt-get purge -y build-essential && apt-get autoremove -y

# The warehouse must live on a mounted volume. On an ephemeral filesystem every
# redeploy silently discards every ingested workbook.
ENV IMDQ_DATA_DIR=/data \
    IMDQ_WAREHOUSE_PATH=/data/warehouse.duckdb \
    IMDQ_ENV=production
RUN mkdir -p /data && useradd -m -u 10001 imdq && chown -R imdq /data /app
USER imdq

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# ONE worker, deliberately. DuckDB permits a single writing process per database
# file, so a second worker would fail to open the warehouse at all.
CMD ["uvicorn", "imdq.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
