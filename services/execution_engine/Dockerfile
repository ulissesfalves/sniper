# DESTINO: services/execution_engine/Dockerfile
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

LABEL service="sniper-execution-engine" version="10.10"

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

# CORREÇÃO: cria diretório como root ANTES de trocar para sniper
RUN useradd -m -u 1000 sniper \
    && mkdir -p /app/logs \
    && chown -R sniper:sniper /app

USER sniper
COPY --chown=sniper:sniper . .

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import aiohttp, aioredis; print('OK')" || exit 1

ENTRYPOINT ["python"]
CMD ["-m", "main"]
