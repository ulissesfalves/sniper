## Resumo executivo

- Dominant cause: `BASELINE_ARTIFACT_MISMATCH_CONFIRMED`.
- Closure sovereign metrics (historical git gate): latest=`2`, headroom=`True`, recent=`8`, historical=`3939`.
- Hardening baseline latest/headroom: `0` / `False`.

## Baseline congelado

- Historical phase4 closure gate was loaded from git history and compared against the preserved frozen stage_a bundle and the current clean replay.
- No official artifact was rewritten; the audit is research-only.

## Mudanças implementadas

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_latest_reconciliation\latest_headroom_reconciliation_table.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_latest_reconciliation\latest_choke_decomposition.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_latest_reconciliation\artifact_lineage_comparison.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_latest_reconciliation\latest_date_audit.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_latest_reconciliation\reconciliation_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_latest_reconciliation\official_artifacts_integrity.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_latest_headroom_reconciliation_audit\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_latest_headroom_reconciliation_audit\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_latest_headroom_reconciliation_audit\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_latest_headroom_reconciliation_audit\gate_metrics.parquet`

## Resultados

- Closure gate sovereign latest/headroom: `2` / `True`.
- Hardening baseline latest/headroom: `0` / `False`.
- Current replay latest decomposition: `{'latest_date': '2026-03-20', 'n_rows_latest_total': 9, 'n_rows_latest_eligible': 0, 'n_rows_latest_ranked': 0, 'n_rows_latest_selected': 0, 'n_rows_latest_position_gt_0': 0, 'max_position_usdt_latest': 0.0, 'principal_elimination_reason': 'eligibility_gate_zeroed_latest'}`.
- Latest-date audit: `{'closure_gate_latest_date': '2026-03-20', 'hardening_latest_date': '2026-03-20', 'current_replay_latest_date': '2026-03-20', 'closure_vs_hardening_same_latest_date': True, 'closure_recent_window_dates': 8, 'hardening_recent_window_dates': 8, 'closure_recent_live_dates_decision_space': 8, 'hardening_recent_live_dates_decision_space': 7, 'frozen_stage_a_snapshot_mode': {'mode': 'per_symbol_latest_snapshot', 'row_count': 36, 'date_min': '2024-12-16', 'date_max': '2026-03-20', 'n_dates': 21, 'rows_on_max_date': 9, 'stale_rows_vs_global_latest': 27}, 'closure_gate_latest_mode': {'mode': 'global_latest_date', 'row_count': 9, 'date_min': '2026-03-20', 'date_max': '2026-03-20', 'n_dates': 1, 'rows_on_max_date': 9, 'stale_rows_vs_global_latest': 0}, 'notes': ['The preserved stage_a snapshot proxy is a per-symbol latest snapshot and spans multiple dates.', 'The historical closure gate evaluated the global latest date 2026-03-20 under a separate sovereign decision-space bundle.']}`.

## Avaliação contra gates

## Riscos residuais

- Historical closure gate artifacts are only available in git history, not in the current worktree.
- The preserved stage_a snapshot proxy still spans multiple dates and can be misread as a global latest snapshot if used without audit.

## Veredito final: advance / correct / abandon

- status `PASS` / decision `advance` / dominant cause `BASELINE_ARTIFACT_MISMATCH_CONFIRMED`
