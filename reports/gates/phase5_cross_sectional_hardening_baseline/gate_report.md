## Resumo executivo

- Status: `PASS` / decision `abandon` / classificação `HARDENING_BASELINE_FAILS`
- Baseline congelada latest/headroom soberanos: `0` / `False`
- Replay limpo reprodutível: `True`; stale dependency check: `True`

## Baseline congelado

- Artifact soberano: `phase4_cross_sectional_ranking_baseline`
- Proveniência congelada: branch `codex/phase4-cross-sectional-closure-gate` / head `cb692cc4e37ec897d5265d7af0881a0f8986821a` / worktree `dirty`
- Baseline_provenance_discrepancy foi registrada, mas a baseline não foi reinterpretada.

## Mudanças implementadas

- Correção mínima do `stage2_payload` para permitir replay limpo do `cross_sectional_ranking`.
- Runner research-only de hardening quantitativo e de codificação para a baseline cross-sectional.
- Hooks default-off para neutralização determinística de feature e redução de universo via replay research-only.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_hardening\hardening_stress_matrix.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_hardening\hardening_regime_slices.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_hardening\hardening_failure_modes.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_hardening\phase5_cross_sectional_hardening_summary.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\data\models\research\phase5_cross_sectional_hardening\official_artifacts_integrity.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_hardening_baseline\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_hardening_baseline\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_hardening_baseline\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper\reports\gates\phase5_cross_sectional_hardening_baseline\gate_metrics.parquet`

## Resultados

- Frozen baseline: latest_active_count_decision_space=`0`, headroom_decision_space=`False`, sharpe_operational=`16.6648`, dsr_honest=`1.0`
- Clean replay metrics: `{'scenario': 'clean_replay_current_sources', 'scenario_type': 'replay', 'source': 'clean_replay_current_sources', 'scenario_status': 'ok', 'blocked_reason': None, 'latest_active_count_decision_space': 0, 'headroom_decision_space': False, 'recent_live_dates_decision_space': 7, 'historical_active_events_decision_space': 1300, 'sharpe_operational': 16.6648, 'dsr_honest': 1.0, 'subperiods_positive': 6, 'equity_final': 739874.35, 'turnover_proxy_mean': 1.2476, 'turnover_proxy_p95': 3.6761, 'turnover_active_dates': 1142, 'concentration_proxy_mean': 0.9523, 'concentration_proxy_p95': 1.0, 'max_active_events_per_day': 3, 'active_dates': 1142, 'capacity_reference_order_mean': 10000.0, 'capacity_reference_order_p95': 10000.0, 'capacity_slippage_mean': 0.0, 'capacity_capped_ref_rate': 0.0, 'capacity_gross_exposure_mean': 2590.21, 'capacity_gross_exposure_p95': 5683.98}`
- Slippage stress impact: `{'friction_mild': {'delta_sharpe_operational': 0.014, 'delta_historical_active_events_decision_space': 0, 'delta_equity_final': -5442.38}, 'friction_medium': {'delta_sharpe_operational': 0.0279, 'delta_historical_active_events_decision_space': 0, 'delta_equity_final': -10844.76}, 'friction_hard': {'delta_sharpe_operational': 0.055, 'delta_historical_active_events_decision_space': 0, 'delta_equity_final': -21530.73}}`
- Regime slice results: `{'scenario': 'clean_replay_current_sources', 'subperiods_positive': 6, 'subperiods_tested': 6, 'negative_slices': [], 'latest_365d_sharpe': 20.1343, 'pre_latest_365d_sharpe': 16.1084}`

## Avaliação contra gates

- official_artifacts_unchanged = `True`
- research_only_isolation_pass = `True`
- reproducibility_pass = `True`
- stale_dependency_check_pass = `True`
- sovereign_metric_definitions_unchanged = `True`

## Riscos residuais

- Frozen baseline artifact was generated from a dirty worktree.
- Frozen winner is already dead on the sovereign latest/headroom ruler.
- Legacy gate report still stores auxiliary headroom proxy text; sovereign metrics were recomputed from decision_selected and position_usdt_stage_a > 0.

## Veredito final: advance / correct / abandon

- `HARDENING_BASELINE_FAILS` -> decision `abandon`
