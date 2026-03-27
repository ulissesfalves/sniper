## Resumo executivo
Rodada concluida com status `PASS`, decision `correct` e classificacao `LATEST_CHOKE_IDENTIFIED`.

## Baseline congelado
- `branch`: `codex/phase4-cross-sectional-latest-choke-audit`
- `baseline_commit`: `e0aac7a379e6a639ee3109aea448c547382ea970`
- `working_tree_dirty_before`: `False`
- `baseline_gate_path`: `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_ranking_baseline\gate_report.json`
- `baseline_predictions_path`: `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_ranking_baseline\cross_sectional_predictions.parquet`
- `baseline_snapshot_path`: `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_ranking_baseline\cross_sectional_latest_snapshot.parquet`

## Mudanças implementadas
- auditoria research-only do latest da familia cross-sectional
- reconstruido o aggregated frame do baseline para rastrear contestos, sizing e posicao
- materializados funil do latest, trace dos candidatos e comparacao com janela recente

## Artifacts gerados
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_latest_choke_audit\latest_choke_funnel.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_latest_choke_audit\latest_candidate_trace.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_latest_choke_audit\cross_sectional_latest_choke_diagnostics.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_latest_choke_audit\cross_sectional_latest_choke_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_latest_choke_audit\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_latest_choke_audit\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_latest_choke_audit\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase4_cross_sectional_latest_choke_audit\gate_metrics.parquet`

## Resultados
- `latest_date=2026-03-20`
- `latest_rows=9`
- `latest_eligible_count=0`
- `latest_selected_proxy_count=0`
- `latest_position_gt_0=0`
- `latest_date_with_eligible=2026-03-16`

## Avaliação contra gates
- `official_artifacts_unchanged` = `True` vs `true` -> `PASS`
- `no_official_research_mixing` = `True` vs `true` -> `PASS`
- `latest_funnel_measured` = `True` vs `true` -> `PASS`
- `latest_candidate_trace_materialized` = `True` vs `true` -> `PASS`
- `recent_window_compared` = `8` vs `>=8` -> `PASS`
- `final_classification_assigned` = `LATEST_CHOKE_IDENTIFIED` vs `one_of(LATEST_CHOKE_IDENTIFIED,NO_LATEST_HEADROOM_STRUCTURAL,INCONCLUSIVE_LATEST_AUDIT)` -> `PASS`
- `tests_passed` = `True` vs `true` -> `PASS`

## Riscos residuais
- o latest pode morrer por ausencia de contestos elegiveis mesmo quando a familia tem historico forte
- isso precisa ser distinguido de falha de ranking propriamente dita antes de qualquer endurecimento da familia

## Veredito final: advance / correct / abandon
correct
