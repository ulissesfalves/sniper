# DESTINO: services/data_inserter/Dockerfile
# FIX: cria /data com permissões corretas ANTES de trocar para user sniper
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

LABEL service="sniper-data-inserter" version="10.10"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

FROM base AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

FROM base AS final
WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# FIX: cria user + diretórios de dados com permissões corretas
RUN useradd -m -u 1000 sniper \
    && mkdir -p /app/logs /data/parquet/ohlcv_daily /data/parquet/funding \
               /data/parquet/ohlcv_4h /data/parquet/basis /data/parquet/stablecoin \
               /data/parquet/unlocks /data/parquet/unlock_diagnostics \
               /data/parquet/unlock_market /data/parquet/unlock_wayback /data/sqlite \
    && chown -R sniper:sniper /app /data

USER sniper
COPY --chown=sniper:sniper . .

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import aiohttp, polars; print('OK')" || exit 1

ENTRYPOINT ["python"]
CMD ["-m", "main"]
