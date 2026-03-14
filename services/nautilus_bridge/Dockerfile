FROM python:3.12-slim

WORKDIR /app

COPY services/nautilus_bridge/requirements.txt /tmp/nautilus_bridge_requirements.txt
RUN pip install --no-cache-dir -r /tmp/nautilus_bridge_requirements.txt

COPY . /app

ENV PYTHONPATH=/app

CMD ["python", "-m", "services.nautilus_bridge.main"]
