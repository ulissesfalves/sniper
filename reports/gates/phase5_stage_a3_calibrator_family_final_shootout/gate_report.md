## Resumo executivo

- Frozen A3-q60 was reproduced exactly before the calibrator-family shootout.
- Platt scaling recovers aggregate calibrated mass but still leaves latest/headroom sovereign metrics dead; beta calibration remains dead as well.
- Only the DIAGNOSTIC_ONLY identity proxy revives latest/headroom, so the line closes as A3_STRUCTURAL_CHOKE_CONFIRMED rather than a low-regret calibrated fix.

## Baseline congelado

- raw_hits_gt_050_row_level=13458
- calibrated_hits_gt_050_row_level=9
- calibrated_hits_gt_050_cpcv_aggregated=0
- latest_active_count_decision_space=0
- headroom_decision_space=False

## Mudanças implementadas

- frozen_a3_q60_current reproduction
- challenger_platt_global_after_aggregate
- challenger_beta_global_after_aggregate
- challenger_identity_no_calibration_after_aggregate (DIAGNOSTIC_ONLY)

## Artifacts gerados

- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\calibrator_family_shootout.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\calibrator_family_summary.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\calibrator_family_integrity.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\calibrator_family_decision.json

## Resultados

- frozen_a3_q60_current calibrated_hits_gt_050_cpcv_aggregated=0, latest_active_count_decision_space=0, headroom_decision_space=False
- challenger_platt_global_after_aggregate calibrated_hits_gt_050_cpcv_aggregated=64, latest_active_count_decision_space=0, headroom_decision_space=False
- challenger_beta_global_after_aggregate calibrated_hits_gt_050_cpcv_aggregated=0, latest_active_count_decision_space=0, headroom_decision_space=False
- challenger_identity_no_calibration_after_aggregate calibrated_hits_gt_050_cpcv_aggregated=2123, latest_active_count_decision_space=1, headroom_decision_space=True
- final_a3_conclusion=A3_STRUCTURAL_CHOKE_CONFIRMED

## Avaliação contra gates

- official_artifacts_unchanged: PASS (True)
- research_only_isolation_pass: PASS (True)
- no_leakage_proof_pass: PASS (True)
- sovereign_metric_definitions_unchanged: PASS (True)
- bounded_fix_only: PASS (True)
- final_a3_decision_reached: PASS (True)

## Riscos residuais

- Identity revives live sovereign activation only by dropping calibration discipline, so it is diagnostic and not promotable.
- The Platt challenger recovers some historical sovereign events but keeps latest/headroom at zero, which is insufficient to keep the A3 line alive under the frozen contract.

## Veredito final: advance / correct / abandon

- advance
