## Resumo executivo

- Status: `PARTIAL` / decision `correct` / classificação `SOVEREIGN_HARDENING_MIXED`.
- Baseline soberana restaurada latest/headroom: `2` / `True`.
- Hardening recheck atual latest/headroom/sharpe: `2` / `True` / `-0.5693`.

## Baseline congelado

- Baseline research-only obrigatória: `phase5_cross_sectional_sovereign_closure_restored`.
- Old hardening lineage recontextualized from `phase4_cross_sectional_ranking_baseline`.
- O caminho official permaneceu read-only.

## Mudanças implementadas

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_hardening_recheck\hardening_recheck_matrix.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_hardening_recheck\hardening_recheck_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_hardening_recheck\hardening_recheck_lineage_comparison.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_sovereign_hardening_recheck\official_artifacts_integrity.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_hardening_recheck\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_hardening_recheck\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_hardening_recheck\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_sovereign_hardening_recheck\gate_metrics.parquet`

## Resultados

- Baseline restaurada: `{'latest_date': '2026-03-20', 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'latest_selected_symbols': ['ENA', 'TAO'], 'latest_rows_total': 9, 'latest_rows_available': 9, 'latest_rows_selected': 2, 'latest_rows_position_gt_0': 2, 'max_position_usdt_latest': 2755.347917}`.
- Hardening recheck atual: `{'scenario': 'sovereign_hardening_recheck', 'scenario_type': 'baseline', 'source': 'phase5_cross_sectional_sovereign_closure_restored', 'scenario_status': 'ok', 'blocked_reason': None, 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'sharpe_operational': -0.5693, 'dsr_honest': 0.0, 'subperiods_positive': 3, 'equity_final': 113503.15, 'turnover_proxy_mean': 0.812, 'turnover_proxy_p95': 2.5032, 'turnover_active_dates': 2112, 'concentration_proxy_mean': 0.7407, 'concentration_proxy_p95': 1.0, 'max_active_events_per_day': 3, 'active_dates': 2112, 'capacity_reference_order_mean': 10000.0, 'capacity_reference_order_p95': 10000.0, 'capacity_slippage_mean': 0.012984, 'capacity_capped_ref_rate': 0.0259, 'capacity_gross_exposure_mean': 5957.56, 'capacity_gross_exposure_p95': 17989.45}`.
- Impacto da correção de lineage: `{'latest_active_count_delta_vs_old': 2, 'historical_active_events_delta_vs_old': 2639, 'headroom_flipped_true': True}`.
- Stress mínimo: `{'friction_mild': {'delta_sharpe_operational': -0.0315, 'delta_historical_active_events_decision_space': 0, 'delta_equity_final': -3429.97}, 'friction_medium': {'delta_sharpe_operational': -0.0631, 'delta_historical_active_events_decision_space': 0, 'delta_equity_final': -6756.33}, 'friction_hard': {'delta_sharpe_operational': -0.1263, 'delta_historical_active_events_decision_space': 0, 'delta_equity_final': -13110.7}}` / regime `{'scenario': 'sovereign_hardening_recheck', 'subperiods_positive': 3, 'subperiods_tested': 6, 'negative_slices': ['2020-H1', '2023', '2024+'], 'latest_365d_sharpe': -1.7119, 'pre_latest_365d_sharpe': 0.0776}`.

## Avaliação contra gates

## Riscos residuais

- negative_base_sharpe
- non_positive_base_dsr
- subperiod_majority_not_positive
- negative_regime_slices_present
- friction_stress_negative

## Veredito final: advance / correct / abandon

- `SOVEREIGN_HARDENING_MIXED` -> decision `correct`
