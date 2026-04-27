# Next Step Recommendation

## Estado atual

A ultima rodada executada foi `phase6_phase4_artifact_rehydration_and_dsr_stop_gate`.

Resultado: `PARTIAL/correct`

Classificacao da revisao: `INCONCLUSIVE_EVIDENCE`

## O que foi resolvido

- Os artifacts official `data/models/phase4/**` foram encontrados e registrados com hashes no gate pack.
- A divergencia source-doc Phase 4-R4 permanece resolvida: `source_doc_alignment=ALIGNED`.
- O preflight agora separa artifact official ausente de baseline research ausente, sem stacktrace.
- O ambiente de teste em `.venv` passou na bateria relevante de Phase 5/Phase 6.
- CVaR foi persistido a partir do snapshot official, com caveat explicito de exposicao zero.
- `dsr_honest=0.0` foi registrado como blocker de promocao.

## Blockers remanescentes

- `research_regeneration_baseline_artifacts_missing`
- `clean_regeneration_not_proven_in_clean_clone_or_equivalent`
- `cvar_zero_exposure_not_economic_robustness`
- `dsr_honest_zero_blocks_promotion`

## Artifacts externos necessarios para continuar

Copiar ou fornecer estes arquivos em:

`data/models/research/phase4_cross_sectional_ranking_baseline/`

- `stage_a_predictions.parquet`
- `stage_a_report.json`
- `stage_a_manifest.json`
- `stage_a_snapshot_proxy.parquet`

Comandos de verificacao:

```powershell
Test-Path data\models\research\phase4_cross_sectional_ranking_baseline\stage_a_predictions.parquet
Test-Path data\models\research\phase4_cross_sectional_ranking_baseline\stage_a_report.json
Test-Path data\models\research\phase4_cross_sectional_ranking_baseline\stage_a_manifest.json
Test-Path data\models\research\phase4_cross_sectional_ranking_baseline\stage_a_snapshot_proxy.parquet
```

## Recomendacao objetiva

Parar a missao autonoma ate que o baseline research acima exista no clone. O Codex nao deve fabricar `stage_a_manifest.json` nem declarar clean regeneration PASS sem clone limpo ou ambiente equivalente documentado.

Mesmo apos fornecer esses artifacts, nao promover nada para official enquanto `dsr_honest=0.0` e o snapshot official continuar com exposicao zero.

## Proximo gate sugerido se os artifacts forem fornecidos

`phase6_research_baseline_rehydration_clean_regeneration_gate`

Objetivo: verificar os artifacts research baseline, registrar hashes, executar clean regeneration em ambiente isolado/equivalente e manter DSR/CVaR como blockers quando aplicavel.

## O que nao deve ser feito

- Nao declarar clean regeneration PASS sem clone limpo ou equivalente.
- Nao tratar `PASS_ZERO_EXPOSURE` como robustez economica.
- Nao promover `phase5_cross_sectional_sovereign_closure_restored` ou qualquer research para official.
- Nao reabrir A3/A4.
- Nao mascarar DSR honesto igual a 0.0.
- Nao relaxar thresholds quantitativos.

## Skill recomendada caso o usuario forneca insumo

`sniper-autonomous-implementation-manager`
