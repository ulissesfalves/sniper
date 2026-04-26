## Resumo executivo

- status=FAIL
- decision=abandon
- selected_target=A3-q60

## Baseline congelado

- baseline_cross_sectional_current uses geometry locked in Fase 4 closure.
- baseline target_definition: rank_target_stage_a = pnl_real / avg_sl_train for eligible = (pnl_real > avg_sl_train); proxy selection = top1(rank_score_stage_a) within (date, cluster_name) among eligible rows; fallback to top1(date-universe) when eligible_count(date, cluster_name) < 2

## Mudanças implementadas

- Added research-only two-stage Stage A3 runner path.
- Recovered minimal gate writer source from compiled contract.
- Materialized research-only integrity, comparison, and reliability artifacts.

## Artifacts gerados

- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\stage_a3_candidates.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\stage_a3_selected_target.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\stage_a3_comparison.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\stage_a3_summary.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\stage_a3_reliability_diagram.png
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\stage_a3_failure_modes.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\official_artifacts_integrity.json

## Resultados

- positive_rate_oos=0.0743
- latest_active_count_decision_space=0
- recent_live_dates_decision_space=0
- sharpe_operational=0.0
- dsr_honest=0.0

## Avaliação contra gates

- no_leakage_proof_pass: PASS (True vs PASS)
- research_only_isolation_pass: PASS (True vs PASS)
- official_artifacts_unchanged: PASS (True vs PASS)
- target_is_non_circular: PASS (True vs PASS)
- decision_space_metrics_computed: PASS (True vs PASS)
- positive_rate_oos: PASS (0.0743 vs >= 0.05)
- latest_active_count_decision_space: FAIL (0 vs >= 1)
- headroom_decision_space: FAIL (False vs == true)
- recent_live_dates_decision_space: FAIL (0 vs >= 5 of 8)
- historical_active_events_decision_space: FAIL (0 vs >= 120)
- sharpe_operational: FAIL (0.0 vs > 0)
- dsr_honest: FAIL (0.0 vs > 0)
- n_eff_mean: PASS (2777.1667 vs > 120)
- cpcv_trajectories: PASS (15 vs == 15)
- pbo: PASS (0.0 vs < 0.10)
- ece_calibrated: PASS (0.017 vs < 0.05)
- reliability_diagram_present: PASS (True vs PASS)
- subperiods_positive: FAIL (0 vs >= 4 of 6 for promotion)

## Riscos residuais

- Latest decision-space activity can still remain sparse even if label-side prevalence survives.
- If stage2 activated-train support falls below threshold in future reruns, CPCV validity can drop below 15 trajectories.

## Veredito final: advance / correct / abandon

- abandon
