# =============================================================================
# SNIPER v10.10 — ML Engine / Data Inserter Dockerfile
# Python 3.11-slim: ~200MB base. Installs via pip with --no-cache-dir.
# Build arg PYTHON_VERSION for future-proofing.
# =============================================================================
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim AS base

LABEL maintainer="CryptoQuantLab"
LABEL service="sniper-ml"
LABEL version="10.10"

# System deps: libgomp (LightGBM OpenMP), libhdf5 (optional data),
# build-essential for Cython extensions, curl for healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libatlas-base-dev \
    build-essential \
    curl \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── BUILDER stage: compile wheels for heavy packages ─────────────────────────
FROM base AS builder
WORKDIR /build

COPY requirements.txt .

RUN pip install --upgrade pip setuptools wheel \
    && pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt

# ── FINAL stage: minimal runtime image ───────────────────────────────────────
FROM base AS final
WORKDIR /app

# Copy pre-built wheels from builder
COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Non-root user for security
RUN useradd -m -u 1000 sniper
USER sniper

# Copy application code
COPY --chown=sniper:sniper . .

# Ensure logs dir exists
RUN mkdir -p /app/logs

# Default entrypoint — overridden in docker-compose per service
ENTRYPOINT ["python"]
CMD ["-m", "main"]
