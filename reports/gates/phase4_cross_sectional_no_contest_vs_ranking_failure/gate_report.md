## Resumo executivo
Rodada concluida com status `PASS`, decision `correct` e classificacao `LATEST_EVALUATION_MISALIGNED`.

## Baseline congelado
- `branch`: `codex/phase4-cross-sectional-no-contest-vs-ranking-failure`
- `baseline_commit`: `4b81a3b9d1a98e8e986cd0f88c3eec6235b9a531`
- `working_tree_dirty_before`: `False`
- `baseline_gate_path`: `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_ranking_baseline\gate_report.json`
- `latest_choke_gate_path`: `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_latest_choke_audit\gate_report.json`
- `baseline_predictions_path`: `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_ranking_baseline\cross_sectional_predictions.parquet`

## Mudanças implementadas
- auditoria research-only separando label-space realizado de decision-space ex-ante
- reconstruido o proxy cross-sectional vigente e um proxy decision-space causal com o mesmo score/rank e a mesma politica local/fallback
- materializados trace do latest e frequencias da janela recente

## Artifacts gerados
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_no_contest_vs_ranking_failure\latest_label_vs_operational_trace.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_no_contest_vs_ranking_failure\no_contest_frequency.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_no_contest_vs_ranking_failure\latest_definition_alignment.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_no_contest_vs_ranking_failure\cross_sectional_no_contest_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_no_contest_vs_ranking_failure\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_no_contest_vs_ranking_failure\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_no_contest_vs_ranking_failure\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_no_contest_vs_ranking_failure\gate_metrics.parquet`

## Resultados
- `latest_date=2026-03-20`
- `label_space_eligible_count=0`
- `decision_space_available_count=9`
- `decision_space_selected_count=2`
- `decision_space_position_gt_0_count=2`
- `recent_window_frequency=[{'classification': 'aligned_live_contest', 'n_dates': 6}, {'classification': 'ranking_failure', 'n_dates': 1}, {'classification': 'metric_misalignment', 'n_dates': 1}]`

## Avaliação contra gates
- `official_artifacts_unchanged` = `True` vs `true` -> `PASS`
- `no_official_research_mixing` = `True` vs `true` -> `PASS`
- `latest_definition_audited` = `True` vs `true` -> `PASS`
- `label_vs_decision_space_separated` = `metric_misalignment` vs `non_empty_classification` -> `PASS`
- `latest_trace_materialized` = `True` vs `true` -> `PASS`
- `recent_window_frequency_measured` = `8` vs `>=8` -> `PASS`
- `final_classification_assigned` = `LATEST_EVALUATION_MISALIGNED` vs `one_of(NO_CONTEST_CONFIRMED,RANKING_FAILURE_CONFIRMED,LATEST_EVALUATION_MISALIGNED)` -> `PASS`
- `tests_passed` = `True` vs `true` -> `PASS`

## Riscos residuais
- a avaliacao atual de latest/headroom para esta familia depende de um filtro realizado (`pnl_real > avg_sl_train`) e pode subestimar disponibilidade operacional ex-ante
- a separacao causal foi feita apenas em research; nenhum ajuste operacional real foi aplicado nesta rodada

## Veredito final: advance / correct / abandon
correct
