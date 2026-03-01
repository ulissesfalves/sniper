# DESTINO: services/ml_engine/Dockerfile
# CORREÇÃO: ENTRYPOINT removido. O docker-compose.yml define "command: [python, -m, main]".
# Com ENTRYPOINT ["python"], o compose executaria "python python -m main".
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

LABEL service="sniper-ml-engine" version="10.10"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libopenblas-dev \
    build-essential \
    curl \
    ca-certificates \
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

RUN useradd -m -u 1000 sniper \
    && mkdir -p /app/logs \
    && chown -R sniper:sniper /app

USER sniper
COPY --chown=sniper:sniper . .

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import lightgbm, hmmlearn, scipy; print('OK')" || exit 1

# CMD fallback — sobrescrito pelo "command:" do docker-compose
CMD ["python", "-m", "main"]
