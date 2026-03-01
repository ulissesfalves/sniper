# DESTINO: services/api/Dockerfile
# CORREÇÃO: ENTRYPOINT removido. O docker-compose.yml já define o comando
# completo via "command:". Com ENTRYPOINT ["uvicorn"], o compose executaria
# "uvicorn uvicorn main:app ..." — primeiro argumento duplicado.
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

LABEL service="sniper-api" version="10.10"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
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

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

# CMD fallback — sobrescrito pelo "command:" do docker-compose
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
