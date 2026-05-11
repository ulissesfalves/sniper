## Resumo executivo

AGENDA-H02 evaluated long-only meta uncertainty/agreement abstention. Status=FAIL, decision=abandon, classification=META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS. Best policy `long_bma_meta_agree_p65_m50_s10_k3` has median Sharpe 0.447334, min Sharpe -0.375889, active days 55.0, max CVaR95 0.00284763 and 19 hard falsifiers.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation` at `b3263393bf95e001e6878f1a43a2fa44b3b819eb`. Input is Phase4 OOS predictions as base artifact; no official artifact is promoted.

## Mudanças implementadas

Added a research/sandbox runner for long-only meta uncertainty abstention. Selection is based on ex-ante `p_bma_pkf`, `p_meta_calibrated` and `sigma_ewma`; `pnl_real` is used only as realized backtest outcome.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_meta_uncertainty_abstention_gate\meta_uncertainty_positions.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_meta_uncertainty_abstention_gate\meta_uncertainty_daily_returns.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_meta_uncertainty_abstention_gate\meta_uncertainty_trade_log.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_meta_uncertainty_abstention_gate\meta_uncertainty_metrics.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_meta_uncertainty_abstention_gate\meta_uncertainty_scenarios.parquet`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase5_research_meta_uncertainty_abstention_gate\meta_uncertainty_research_report.json`

## Resultados

classification=META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS
selected_agenda_id=AGENDA-H02
best_policy=long_bma_meta_agree_p65_m50_s10_k3
best_median_combo_sharpe=0.447334
best_min_combo_sharpe=-0.375889
best_median_active_days=55.0
best_max_cvar_95_loss_fraction=0.00284763
best_max_drawdown_proxy=0.02955821
best_median_turnover_fraction=0.04703030
hard_falsifier_count=19
long-only research/sandbox only
official_promotion_allowed=false
paper_readiness_allowed=false
no realized variable used as selection input

## Avaliação contra gates

- selected_agenda_id: AGENDA-H02 / AGENDA-H02 => PASS
- policies_tested: 6 / >= 5 => PASS
- forbidden_selection_input_count: 0 / 0 => PASS
- best_policy: long_bma_meta_agree_p65_m50_s10_k3 / predeclared policy only => PASS
- best_median_combo_sharpe: 0.447334 / > 0 research alpha => PASS
- best_min_combo_sharpe: -0.375889 / > 0 stable alpha => FAIL
- best_median_active_days: 55.0 / >= 120 => FAIL
- best_max_cvar_95_loss_fraction: 0.00284763 / <= 0.15 => PASS
- scenario_count: 20 / >= 10 => PASS
- hard_falsifier_count: 19 / 0 => FAIL
- candidate_below_sr_needed: True / true => PASS
- official_promotion_allowed: False / false => PASS
- paper_readiness_allowed: False / false => PASS

## Riscos residuais

- Long-only research exposure remains sandbox evidence only and cannot support official promotion.
- The gate does not change DSR=0.0 or the official zero-exposure CVaR blocker.
- Any survivor would still require stability/falsification and a decision gate before preservation.

## Veredito final: advance / correct / abandon

FAIL/abandon. Next: phase5_research_cvar_constrained_meta_sizing_gate.
