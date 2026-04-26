## Resumo executivo

- Status: `PASS` / decision `advance` / classificação `SOVEREIGN_BASELINE_RESTORED_AND_VALID`.
- Equivalence classification: `EXACT_RESTORE`.
- Replay soberano revalidado em `2026-03-20` com latest/headroom `2` / `True`.

## Baseline congelado

- Historical commit soberano: `cb692cc4e37ec897d5265d7af0881a0f8986821a`.
- Frozen baseline preservada usada para replay: `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase4_cross_sectional_ranking_baseline`.
- Restored bundle namespace: `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_closure_restored`.

## Mudanças implementadas

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_restore\sovereign_restore_lineage.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_restore\sovereign_restore_equivalence.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_restore\sovereign_restore_replay_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_restore\sovereign_restore_bundle_inventory.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_restore\official_artifacts_integrity.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_closure_restored\restored_bundle_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate\gate_metrics.parquet`

## Resultados

- Historical target metrics: `{'latest_date': '2026-03-20', 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'latest_selected_symbols': ['ENA', 'TAO'], 'latest_rows_total': 9, 'latest_rows_available': 9, 'latest_rows_selected': 2, 'latest_rows_position_gt_0': 2, 'max_position_usdt_latest': 2755.347917}`.
- Restored exact bundle metrics: `{'latest_date': '2026-03-20', 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'latest_selected_symbols': ['ENA', 'TAO'], 'latest_rows_total': 9, 'latest_rows_available': 9, 'latest_rows_selected': 2, 'latest_rows_position_gt_0': 2, 'max_position_usdt_latest': 2755.347917}`.
- Current regenerated replay metrics: `{'latest_date': '2026-03-20', 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'latest_selected_symbols': ['ENA', 'TAO'], 'latest_rows_total': 9, 'latest_rows_available': 9, 'latest_rows_selected': 2, 'latest_rows_position_gt_0': 2, 'max_position_usdt_latest': 2755.347917}`.
- Replay summary: `{'gate_slug': 'phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate', 'timestamp_utc': '2026-04-05T01:39:28+00:00', 'historical_commit': 'cb692cc4e37ec897d5265d7af0881a0f8986821a', 'reproducibility': {'frame_hash_run1': 'C10621BC6AD4A11ADD7148ABEC26D7C603C103848913678A587DC3B0F726F668', 'frame_hash_run2': 'C10621BC6AD4A11ADD7148ABEC26D7C603C103848913678A587DC3B0F726F668', 'metrics_run1': {'latest_date': '2026-03-20', 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'latest_selected_symbols': ['ENA', 'TAO'], 'latest_rows_total': 9, 'latest_rows_available': 9, 'latest_rows_selected': 2, 'latest_rows_position_gt_0': 2, 'max_position_usdt_latest': 2755.347917}, 'metrics_run2': {'latest_date': '2026-03-20', 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'latest_selected_symbols': ['ENA', 'TAO'], 'latest_rows_total': 9, 'latest_rows_available': 9, 'latest_rows_selected': 2, 'latest_rows_position_gt_0': 2, 'max_position_usdt_latest': 2755.347917}, 'pass': True}, 'no_fallback': {'pass': True, 'detail': 'Replay used only the preserved stage_a_predictions bundle and the restored git-history sovereign closure bundle.', 'current_predictions_path': 'C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_predictions.parquet'}, 'stale_contamination': {'pass': True, 'detail': 'Current preserved stage_a_predictions remain semantically aligned with the historical cross_sectional_predictions on decision-space inputs.'}, 'bundle_completeness': {'pass': True, 'restored_files_present': 8, 'required_restored_files': 8, 'missing_restored_files': []}, 'snapshot_report_predictions_consistency': {'current_predictions_path': 'C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_predictions.parquet', 'historical_predictions_git_path': 'data/models/research/phase4_cross_sectional_ranking_baseline/cross_sectional_predictions.parquet', 'current_predictions_rows': 94930, 'historical_predictions_rows': 94930, 'same_shape': True, 'same_columns': True, 'unexpected_diff_columns': [], 'p_stage_a_raw_max_abs_diff': 2.7755575615628914e-17, 'rank_score_stage_a_max_abs_diff': 2.7755575615628914e-17, 'semantic_prediction_match': True, 'current_snapshot_path': 'C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_snapshot_proxy.parquet', 'historical_snapshot_git_path': 'data/models/research/phase4_cross_sectional_ranking_baseline/cross_sectional_latest_snapshot.parquet', 'current_snapshot_sha256': 'FE1E050820ABE77C7DF35318F13301F2A7443AE45B75B2D322CAC0AF233032B7', 'historical_snapshot_sha256': 'FE1E050820ABE77C7DF35318F13301F2A7443AE45B75B2D322CAC0AF233032B7', 'snapshot_byte_exact_match': True, 'current_manifest_head': 'cb692cc4e37ec897d5265d7af0881a0f8986821a', 'current_manifest_branch': 'codex/phase4-cross-sectional-closure-gate', 'current_report_problem_type': 'cross_sectional_ranking', 'current_report_target_name': 'cross_sectional_relative_rank_score'}, 'equivalence_classification': 'EXACT_RESTORE'}`.

## Avaliação contra gates

## Riscos residuais

- Historical closure lineage still depends on git-history rehydration instead of a preserved current worktree bundle.
- The workspace remains dirty from pre-existing untracked docs/reports outside this round.

## Veredito final: advance / correct / abandon

- `SOVEREIGN_BASELINE_RESTORED_AND_VALID` -> decision `advance`
