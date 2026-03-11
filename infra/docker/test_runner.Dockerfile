ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

LABEL service="sniper-test-runner" version="10.10"

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    libgomp1 \
    libopenblas-dev \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY services/data_inserter/requirements.txt /tmp/data_inserter-requirements.txt
COPY services/ml_engine/requirements.txt /tmp/ml_engine-requirements.txt
RUN pip install --upgrade pip \
    && pip install --no-cache-dir \
        -r /tmp/data_inserter-requirements.txt \
        -r /tmp/ml_engine-requirements.txt

CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py", "-v"]
