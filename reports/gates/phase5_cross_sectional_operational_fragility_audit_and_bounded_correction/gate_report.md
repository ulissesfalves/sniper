## Resumo executivo

- Status: `PARTIAL` / decision `correct` / classificação `OPERATIONAL_FRAGILITY_PERSISTS`.
- Baseline soberana congelada latest/headroom: `2` / `True`.
- Fragilidade dominante: `REGIME_DEPENDENCE_DOMINANT`; melhor challenger: `challenger_edge_buffer_plus_concentration_cap` com sharpe `-0.2843`.

## Baseline congelado

- Baseline research-only obrigatória: `phase5_cross_sectional_sovereign_closure_restored`.
- Sem alteração de official, target, features, modelo ou régua soberana.
- Restore equivalence de referência: `EXACT_RESTORE`.

## Mudanças implementadas

- Runner research-only para decompor fragilidade operacional e testar challengers bounded sobre o frame soberano restaurado.
- Challengers bounded: filtro leve de regime, buffer de edge e combinação buffer+cap top-2 por data.
- Reuso dos helpers já existentes de métricas soberanas, fricção, regime slices e gate pack.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_operational_fragility\operational_fragility_decomposition.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_operational_fragility\operational_fragility_challengers.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_operational_fragility\operational_fragility_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_operational_fragility\official_artifacts_integrity.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_operational_fragility_audit_and_bounded_correction\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_operational_fragility_audit_and_bounded_correction\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_operational_fragility_audit_and_bounded_correction\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_operational_fragility_audit_and_bounded_correction\gate_metrics.parquet`

## Resultados

- Baseline: `{'scenario': 'frozen_sovereign_baseline', 'scenario_type': 'operational_policy', 'source': 'phase5_cross_sectional_sovereign_closure_restored', 'scenario_status': 'ok', 'blocked_reason': None, 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'sharpe_operational': -0.5693, 'dsr_honest': 0.0, 'subperiods_positive': 3, 'equity_final': 113503.15, 'turnover_proxy_mean': 0.812, 'turnover_proxy_p95': 2.5032, 'turnover_active_dates': 2112, 'concentration_proxy_mean': 0.7407, 'concentration_proxy_p95': 1.0, 'max_active_events_per_day': 3, 'active_dates': 2112, 'capacity_reference_order_mean': 10000.0, 'capacity_reference_order_p95': 10000.0, 'capacity_slippage_mean': 0.012984, 'capacity_capped_ref_rate': 0.0259, 'capacity_gross_exposure_mean': 5957.56, 'capacity_gross_exposure_p95': 17989.45, 'regime_slice_results': '{"latest_365d_sharpe": -1.7119, "negative_slices": ["2020-H1", "2023", "2024+"], "pre_latest_365d_sharpe": 0.0776, "scenario": "frozen_sovereign_baseline", "subperiods_positive": 3, "subperiods_tested": 6}', 'slippage_stress_impact': '{"frozen_sovereign_baseline_friction_hard": {"delta_equity_final": -13110.7, "delta_historical_active_events_decision_space": 0, "delta_sharpe_operational": -0.1263}, "frozen_sovereign_baseline_friction_medium": {"delta_equity_final": -6756.33, "delta_historical_active_events_decision_space": 0, "delta_sharpe_operational": -0.0631}, "frozen_sovereign_baseline_friction_mild": {"delta_equity_final": -3429.97, "delta_historical_active_events_decision_space": 0, "delta_sharpe_operational": -0.0315}}', 'official_artifacts_unchanged': True, 'research_only_isolation_pass': True, 'reproducibility_pass': True}`.
- Dominant fragility stats: `{'gross_sharpe': -0.5693, 'net_sharpe': -0.5693, 'friction_sharpe_delta': 0.0, 'total_loss_abs': 158.113999, 'regime_recent_loss_abs': 66.422542, 'regime_recent_loss_share': 0.4201, 'sparse_contest_loss_abs': 64.015609, 'sparse_contest_loss_share': 0.4049, 'concentration_loss_abs': 31.800096, 'concentration_loss_share': 0.2011, 'turnover_loss_abs': 38.994916, 'turnover_loss_share': 0.2466, 'latest_date': '2026-03-20'}`.
- Challenger rows: `{'challenger_regime_light_filter': {'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'sharpe_operational': -0.4456, 'dsr_honest': 0.0, 'subperiods_positive': 3}, 'challenger_edge_after_friction_buffer': {'latest_active_count_decision_space': 1, 'headroom_decision_space': True, 'sharpe_operational': -0.3606, 'dsr_honest': 0.0, 'subperiods_positive': 4}, 'challenger_edge_buffer_plus_concentration_cap': {'latest_active_count_decision_space': 1, 'headroom_decision_space': True, 'sharpe_operational': -0.2843, 'dsr_honest': 0.0, 'subperiods_positive': 4}}`.

## Avaliação contra gates

- official_artifacts_unchanged = `PASS`
- research_only_isolation_pass = `PASS`
- reproducibility_pass = `PASS`
- sovereign_metric_definitions_unchanged = `PASS`
- dominant_operational_fragility_classified = `PASS`
- bounded_challengers_executed = `PASS`

## Riscos residuais

- Melhor melhoria bounded ainda deixa `dsr_honest=0.0`.
- Regime slice baseline: `{'scenario': 'frozen_sovereign_baseline', 'subperiods_positive': 3, 'subperiods_tested': 6, 'negative_slices': ['2020-H1', '2023', '2024+'], 'latest_365d_sharpe': -1.7119, 'pre_latest_365d_sharpe': 0.0776}`.
- Fricção adicional continua negativa em todos os comparadores.

## Veredito final: advance / correct / abandon

- `OPERATIONAL_FRAGILITY_PERSISTS` -> decision `correct`
