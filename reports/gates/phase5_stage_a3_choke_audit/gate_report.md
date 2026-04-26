## Resumo executivo

- status=PASS
- decision=advance
- choke_dominant_stage=stage1_raw_to_calibrated_activation_gate

## Baseline congelado

- baseline geometry remains top1 within (date, cluster_name) with date-universe fallback under support < 2.
- sovereign metrics are still derived from decision_selected and position_usdt_stage_a > 0.

## Mudanças implementadas

- Added a research-only choke audit runner on top of existing phase5 Stage A3 artifacts.
- Added unit coverage for sovereign metrics, funnel instrumentation, contest geometry, and Stage 2 choke localization.

## Artifacts gerados

- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\choke_audit_funnel.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\choke_audit_latest_recent.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\choke_audit_cluster_breakdown.parquet
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\choke_audit_mu_adj_diagnostics.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\choke_audit_metric_definition_check.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\choke_audit_summary.json
- C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_stage_a3\official_artifacts_integrity_audit.json

## Resultados

- baseline historical positions_gt_0=1300
- a3 historical calibrated_gt_050=0
- a3 dominant_choke=stage1_raw_to_calibrated_activation_gate

## Avaliação contra gates

- official_artifacts_unchanged: PASS (True)
- research_only_isolation_pass: PASS (True)
- metric_definition_audit_complete: PASS (True)
- choke_stage_localized: PASS (True)

## Riscos residuais

- The prior stage_a_report still carries an auxiliary proxy headroom field that must not be mistaken for the sovereign ruler.
- Any future correction round must preserve the same contest geometry and sovereign decision-space definitions.

## Veredito final: advance / correct / abandon

- advance
