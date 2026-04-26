## Resumo executivo

- Frozen A3-q60 was reproduced exactly at row level, CPCV aggregate and sovereign final path.
- The primary choke is the calibrator fit/mapping; mean CPCV aggregation is a secondary collapse that removes the few surviving >0.50 row-level hits.
- No bounded challenger restored live sovereign activation on the latest date while keeping the frozen contest geometry, Stage 2 and sizing unchanged.

## Baseline congelado

- raw_hits_gt_050_row_level=13458
- calibrated_hits_gt_050_row_level=9
- calibrated_hits_gt_050_cpcv_aggregated=0
- latest_active_count_decision_space=0

## Mudanças implementadas

- faithful frozen reproduction across row-level, CPCV aggregate and sovereign final path
- challenger_1: aggregate then cluster-specific isotonic calibration
- challenger_2: aggregate then global isotonic calibration

## Artifacts gerados

- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\activation_calibration_reconciliation.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\activation_calibration_fold_vs_aggregate.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\activation_calibration_challengers.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\activation_calibration_summary.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\activation_calibration_integrity.json

## Resultados

- dominant_choke_stage: calibrator_fit_mapping_primary__cpcv_mean_aggregation_secondary
- dominant_cause: Primary collapse happens inside the calibrator fit/mapping at row level; mean CPCV aggregation then removes the few surviving >0.50 hits before the sovereign threshold.
- challenger_1 calibrated_hits_gt_050_cpcv_aggregated=16, latest_active_count_decision_space=0
- challenger_2 calibrated_hits_gt_050_cpcv_aggregated=0, latest_active_count_decision_space=0

## Avaliação contra gates

- official_artifacts_unchanged: PASS (True)
- research_only_isolation_pass: PASS (True)
- no_leakage_proof_pass: PASS (True)
- sovereign_metric_definitions_unchanged: PASS (True)
- counter_reconciliation_complete: PASS (True)
- dominant_choke_confirmed: PASS (True)
- bounded_fix_only: PASS (True)

## Riscos residuais

- The best bounded challenger recovered some historical aggregate mass but still left the latest sovereign path structurally dead.
- Any future correction round that changes the 0.50 sovereign threshold, contest geometry, Stage 2 or sizing would exceed the frozen scope of this micro-round.

## Veredito final: advance / correct / abandon

- correct
