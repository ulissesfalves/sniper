## Resumo executivo

- Status: `PARTIAL` / decision `correct` / classificacao `ALIVE_BUT_NOT_PROMOTABLE`.
- Baseline soberana congelada latest/headroom: `2` / `True`.
- Plausibilidade do fix recente: `RECENT_REGIME_POLICY_FIX_PLAUSIBLE`; melhor challenger: `challenger_recent_edge_gate_plus_top2` com sharpe `-0.0471` e DSR `0.0`.

## Baseline congelado

- Baseline research-only obrigatoria: `phase5_cross_sectional_sovereign_closure_restored`.
- Restore equivalence de referencia: `EXACT_RESTORE`.
- Sem alteracao de official, modelo, target, features, geometria ou regua soberana.

## Mudanças implementadas

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_recent_regime\recent_regime_decomposition.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_recent_regime\recent_regime_challengers.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_recent_regime\recent_regime_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_recent_regime\official_artifacts_integrity.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_recent_regime_policy_falsification\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_recent_regime_policy_falsification\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_recent_regime_policy_falsification\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_recent_regime_policy_falsification\gate_metrics.parquet`

## Resultados

- Baseline: `{'scenario': 'frozen_sovereign_baseline', 'scenario_type': 'recent_regime_policy', 'source': 'phase5_cross_sectional_sovereign_closure_restored', 'scenario_status': 'ok', 'blocked_reason': None, 'latest_active_count_decision_space': 2, 'headroom_decision_space': True, 'recent_live_dates_decision_space': 8, 'historical_active_events_decision_space': 3939, 'sharpe_operational': -0.5693, 'dsr_honest': 0.0, 'subperiods_positive': 3, 'equity_final': 113503.15, 'turnover_proxy_mean': 0.812, 'turnover_proxy_p95': 2.5032, 'turnover_active_dates': 2112, 'concentration_proxy_mean': 0.7407, 'concentration_proxy_p95': 1.0, 'max_active_events_per_day': 3, 'active_dates': 2112, 'capacity_reference_order_mean': 10000.0, 'capacity_reference_order_p95': 10000.0, 'capacity_slippage_mean': 0.012984, 'capacity_capped_ref_rate': 0.0259, 'capacity_gross_exposure_mean': 5957.56, 'capacity_gross_exposure_p95': 17989.45, 'latest_365d_sharpe': -1.7119, 'pre_latest_365d_sharpe': 0.0776, 'subperiods_tested': 6, 'regime_recent_loss_share': 0.4201, 'negative_slices': '["2020-H1", "2023", "2024+"]', 'official_artifacts_unchanged': True, 'research_only_isolation_pass': True, 'reproducibility_pass': True}`.
- Recent regime summary: `{'latest_date': '2026-03-20', 'regime_recent_loss_share': 0.4201, 'recent_active_dates': ['2026-02-22', '2026-02-24', '2026-02-28', '2026-03-04', '2026-03-08', '2026-03-12', '2026-03-16', '2026-03-20'], 'recent_active_dates_count': 8}`.
- Challenger rows: `{'challenger_recent_edge_gate': {'latest_active_count_decision_space': 1, 'headroom_decision_space': True, 'sharpe_operational': -0.0866, 'dsr_honest': 0.0, 'latest_365d_sharpe': -1.6469}, 'challenger_recent_edge_gate_plus_top2': {'latest_active_count_decision_space': 1, 'headroom_decision_space': True, 'sharpe_operational': -0.0471, 'dsr_honest': 0.0, 'latest_365d_sharpe': -1.44}}`.

## Avaliação contra gates

## Riscos residuais

- `latest_365d_sharpe` do baseline continua em `-1.7119`.
- Melhor challenger ainda deixa `dsr_honest=0.0`.
- O filtro hmm_prob_bull >= 0.999 da RC4 piorou o regime recente e por isso foi tratado apenas como evidencia contextual.

## Veredito final: advance / correct / abandon

- `ALIVE_BUT_NOT_PROMOTABLE` -> decision `correct`
