## Resumo executivo

AGENDA-H04 evaluated regime-specific meta disagreement. Status=PARTIAL, decision=correct, classification=REGIME_SPECIFIC_META_DISAGREEMENT_POSITIVE_BUT_UNSTABLE.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation` at `f45f54578f18ff24ef39d6226d45b1efd7c9fc69`. Input is Phase4 OOS predictions; no official artifact is promoted.

## Mudanças implementadas

Added a research/sandbox runner for HMM-regime-specific meta disagreement and agreement policies.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_positions.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_daily_returns.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_trade_log.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_metrics.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_scenarios.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_research_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_regime_specific_meta_disagreement_gate\regime_specific_meta_snapshot_proxy.parquet`

## Resultados

classification=REGIME_SPECIFIC_META_DISAGREEMENT_POSITIVE_BUT_UNSTABLE
selected_agenda_id=AGENDA-H04
best_policy=neutral_short_meta_low_m40_k3
best_median_combo_sharpe=0.726729
best_min_combo_sharpe=-0.911080
best_median_active_days=82.0
best_max_cvar_95_loss_fraction=0.00315145
best_max_drawdown_proxy=0.03586914
best_median_turnover_fraction=0.07050847
best_max_exposure_fraction=0.04000000
hard_falsifier_count=13
research/sandbox only
official_promotion_allowed=false
paper_readiness_allowed=false
no realized variable used as selection input

## Avaliação contra gates

- selected_agenda_id: AGENDA-H04 / AGENDA-H04 => PASS
- policies_tested: 5 / >= 5 => PASS
- forbidden_selection_input_count: 0 / 0 => PASS
- best_policy: neutral_short_meta_low_m40_k3 / predeclared policy only => PASS
- best_max_exposure_fraction: 0.04 / > 0 => PASS
- best_median_combo_sharpe: 0.726729 / > 0 research alpha => PASS
- best_min_combo_sharpe: -0.91108 / > 0 stable alpha => FAIL
- best_median_active_days: 82.0 / >= 120 => FAIL
- best_max_cvar_95_loss_fraction: 0.00315145 / <= 0.01 => PASS
- scenario_count: 14 / >= 10 => PASS
- hard_falsifier_count: 13 / 0 for robust survivor => FAIL
- candidate_below_sr_needed: True / true => PASS
- official_promotion_allowed: False / false => PASS
- paper_readiness_allowed: False / false => PASS

## Riscos residuais

- Regime-specific research evidence remains sandbox-only and cannot support official promotion.
- The gate does not change DSR=0.0 or the official zero-exposure CVaR blocker.
- Regime split can reduce or increase sparsity; positive median Sharpe is not promotability.

## Veredito final: advance / correct / abandon

PARTIAL/correct. Next: phase5_research_feature_family_ablation_blocker_decomposition_gate.
