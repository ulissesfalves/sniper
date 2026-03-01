ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

FROM base AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip && pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

FROM base AS final
WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels
RUN useradd -m -u 1000 sniper
USER sniper
COPY --chown=sniper:sniper . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["uvicorn"]
CMD ["main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2",
     "--loop", "uvloop", "--http", "h11"]
