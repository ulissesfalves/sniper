# SNIPER Regeneration Guide

## Objetivo

Documentar como regenerar, em um clone novo, os artifacts research-only minimos para continuar a linha soberana correta da familia cross-sectional.

## O que vai no GitHub

- Codigo dos runners e testes desta trilha
- Documentacao de handoff
- Historico de commits da trilha consolidada

## O que fica fora do GitHub

- Outputs pesados de `data/models/research/**`
- Gate packs em `reports/gates/**`
- Parquets e snapshots research-only gerados durante as rodadas

## Ordem minima de regeneracao

## Preflight obrigatorio antes da regeneracao

Antes de executar os runners de Phase 5, verificar se os artifacts official de Phase 4 existem no ambiente atual:

- `data/models/phase4/phase4_report_v4.json`
- `data/models/phase4/phase4_execution_snapshot.parquet`

Se `data/models/phase4` estiver ausente, classificar a rodada como `MISSING_OFFICIAL_PHASE4_ARTIFACTS` ou `INCONCLUSIVE`, e nao como PASS. Essa falha e blocker de artifact/ambiente para clean regeneration; nao deve ser mascarada por stacktrace, narrativa ou copia manual nao auditada.

### 1. Recriar a baseline preservada de predictions

Usar o runner do Stage A cross-sectional ranking para recriar `phase4_cross_sectional_ranking_baseline`.

```bash
set SNIPER_MODEL_PATH=C:\caminho\para\sniper\data\models
set STAGE_A_EXPERIMENT_NAME=phase4_cross_sectional_ranking_baseline
set STAGE_A_REFERENCE_EXPERIMENT_NAME=phase4_cross_sectional_ranking_baseline
set STAGE_A_BASELINE_EXPERIMENT_NAMES=phase4_cross_sectional_ranking_baseline
set STAGE_A_PROBLEM_TYPE=cross_sectional_ranking
set STAGE_A_TARGET_MODE=cross_sectional_relative_activation
python services/ml_engine/phase4_stage_a_experiment.py
```

Resultado esperado:

- `data/models/research/phase4_cross_sectional_ranking_baseline/stage_a_predictions.parquet`
- bundle research-only equivalente usado pelo restore soberano

### 2. Restaurar o bundle soberano historico correto

```bash
python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py
```

Resultado esperado:

- `data/models/research/phase5_cross_sectional_sovereign_closure_restored/**`
- `data/models/research/phase5_cross_sectional_sovereign_restore/**`

Checks minimos esperados:

- `equivalence_classification = EXACT_RESTORE`
- `latest_date = 2026-03-20`
- `latest_active_count_decision_space = 2`
- `headroom_decision_space = true`
- `recent_live_dates_decision_space = 8`
- `historical_active_events_decision_space = 3939`

### 3. Revalidar a trilha recente

```bash
python services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py
python services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py
python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py
```

Resultado esperado:

- baseline soberana viva em `latest/headroom`
- classificacao final recente em `ALIVE_BUT_NOT_PROMOTABLE`

## O que NAO fazer

- Nao usar a baseline preservada antiga como ancora soberana se ela divergir do restore correto.
- Nao forcar commit de outputs research-only pesados.
- Nao reabrir A3/A4 sem evidencia nova forte.
