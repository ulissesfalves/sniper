# Runbook: Nautilus Bridge Paper

## Pre-requisitos
- Docker Desktop em execucao
- Arquivo `.env` presente na raiz do projeto
- Snapshot oficial em `data/models/phase4/phase4_execution_snapshot.parquet`
- Snapshot de teste opcional em `data/models/phase4/phase4_execution_snapshot_test_nonzero.parquet`

## Build
```powershell
docker compose --profile paper build nautilus_bridge
```

## Suite automatizada da etapa 12
```powershell
python services/nautilus_bridge/tests_integration_312/run_bridge_validation.py
```

## Suite pura 3.11-safe
```powershell
docker run --rm -v "${PWD}:/workspace" -w /workspace python:3.11-slim sh -lc "pip install --quiet pytest redis && PYTHONPATH=/workspace pytest tests/unit/test_nautilus_bridge_acceptance.py tests/unit/test_nautilus_bridge_consumer.py tests/unit/test_nautilus_bridge_contract.py tests/unit/test_nautilus_bridge_phase4_publisher.py tests/unit/test_nautilus_bridge_reconciler.py"
```

## Testes 3.12 do runtime do bridge
```powershell
docker compose --profile paper run --rm nautilus_bridge python -m pytest /app/services/nautilus_bridge/tests_integration_312/test_paper_executor.py /app/services/nautilus_bridge/tests_integration_312/test_status_flow.py
```

## Subir so Redis
```powershell
docker compose up -d redis
```

## Subir o bridge via compose
```powershell
docker compose --profile paper up -d nautilus_bridge
```

## Publicar com mock_publisher
```powershell
docker compose --profile paper run --rm nautilus_bridge python -m services.nautilus_bridge.mock_publisher
```

## Publicar com phase4_publisher
Snapshot oficial:
```powershell
docker compose --profile paper run --rm nautilus_bridge python -m services.nautilus_bridge.phase4_publisher
```

Snapshot de teste com delta nao zero:
```powershell
docker compose --profile paper run --rm -e SNIPER_BRIDGE_PHASE4_SNAPSHOT=/app/data/models/phase4/phase4_execution_snapshot_test_nonzero.parquet nautilus_bridge python -m services.nautilus_bridge.phase4_publisher
```

## Ciclo paper real em um comando
Pre-condicao: `nautilus_bridge` ja esta rodando via compose.

```powershell
docker compose --profile paper exec -T nautilus_bridge python -m services.nautilus_bridge.run_phase4_paper_once
```

Saida esperada com snapshot oficial:
```text
RESULT=SUCCESS
MESSAGE_ID=<uuid>
FINAL_STATUSES=received,accepted,noop_band
```

Saida esperada quando houver delta real:
```text
RESULT=SUCCESS
MESSAGE_ID=<uuid>
FINAL_STATUSES=received,accepted,submitted,filled
```

## Validar status stream
```powershell
docker compose exec redis redis-cli -n 0 XRANGE sniper:portfolio_status:v1 - +
```

## Ver logs do bridge
```powershell
docker compose logs -f nautilus_bridge
```

## Comportamento esperado
- Snapshot oficial com conta flat: `received -> accepted -> noop_band`
- Snapshot de teste com delta nao zero: `received -> accepted -> submitted -> filled`

## Limpar estado minimo do Redis
```powershell
docker compose exec redis redis-cli -n 0 DEL sniper:portfolio_targets:v1 sniper:portfolio_status:v1 sniper:portfolio_state:v1:bridge01:paper:stream_cursor sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_revision_accepted sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_accepted_target sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_revision_applied sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:last_applied_target sniper:portfolio_state:v1:sniper-paper-binance-spot-main:paper:deferred_target sniper:portfolio_revision:v1:sniper-paper-binance-spot-main:paper
```
