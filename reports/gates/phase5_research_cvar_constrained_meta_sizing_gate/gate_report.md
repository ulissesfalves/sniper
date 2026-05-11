## Resumo executivo

AGENDA-H03 evaluated CVaR-constrained meta sizing. Status=PARTIAL, decision=correct, classification=CVAR_CONSTRAINED_META_SIZING_CVAR_PASS_ALPHA_UNSTABLE.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation` at `4a1bb099cf2c34cafbfe646b21b6475432d9ea64`. Input is Phase4 OOS predictions; no official artifact is promoted.

## Mudanças implementadas

Added a research/sandbox runner for risk-budgeted meta sizing. Selection and target weights use only ex-ante probability edges and sigma.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\cvar_constrained_meta_positions.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\cvar_constrained_meta_daily_returns.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\cvar_constrained_meta_trade_log.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\cvar_constrained_meta_metrics.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\cvar_constrained_meta_scenarios.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\portfolio_cvar_research_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_cvar_constrained_meta_sizing_gate\cvar_constrained_meta_snapshot_proxy.parquet`

## Resultados

classification=CVAR_CONSTRAINED_META_SIZING_CVAR_PASS_ALPHA_UNSTABLE
selected_agenda_id=AGENDA-H03
best_policy=signed_meta_edge_t52_s15_k5_g04
best_median_combo_sharpe=2.040444
best_min_combo_sharpe=-0.903026
best_median_active_days=698.0
best_max_cvar_95_loss_fraction=0.00356911
best_max_drawdown_proxy=0.17842784
best_median_turnover_fraction=0.02376094
best_max_exposure_fraction=0.04000000
hard_falsifier_count=20
research/sandbox only
official_promotion_allowed=false
paper_readiness_allowed=false
no realized variable used as selection input

## Avaliação contra gates

- selected_agenda_id: AGENDA-H03 / AGENDA-H03 => PASS
- policies_tested: 6 / >= 5 => PASS
- forbidden_selection_input_count: 0 / 0 => PASS
- best_policy: signed_meta_edge_t52_s15_k5_g04 / predeclared policy only => PASS
- best_max_exposure_fraction: 0.04 / > 0 => PASS
- best_median_combo_sharpe: 2.040444 / > 0 research alpha => PASS
- best_min_combo_sharpe: -0.903026 / > 0 stable alpha => FAIL
- best_median_active_days: 698.0 / >= 120 => PASS
- best_max_cvar_95_loss_fraction: 0.00356911 / <= 0.01 => PASS
- best_median_turnover_fraction: 0.02376094 / <= 0.08 => PASS
- scenario_count: 21 / >= 15 => PASS
- hard_falsifier_count: 20 / 0 for robust survivor => FAIL
- candidate_below_sr_needed: True / true => PASS
- official_promotion_allowed: False / false => PASS
- paper_readiness_allowed: False / false => PASS

## Riscos residuais

- Research CVaR evidence remains sandbox-only and cannot support official promotion.
- The gate does not change DSR=0.0 or the official zero-exposure CVaR blocker.
- Positive median Sharpe without positive min Sharpe or stable sensitivity is not promotability.

## Veredito final: advance / correct / abandon

PARTIAL/correct. Next: phase5_research_regime_specific_meta_disagreement_gate.
