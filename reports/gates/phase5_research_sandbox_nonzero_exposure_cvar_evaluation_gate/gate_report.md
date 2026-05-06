## Resumo executivo

Research/sandbox nonzero-exposure CVaR gate result: `PARTIAL/correct`. Classification: `NONZERO_EXPOSURE_RESEARCH_CVAR_PASS_BUT_ALPHA_DSR_BLOCKERS_REMAIN`.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation`, commit `bfb170d8512f3610227c75f518c4117e44445f66`. Official promotion remains forbidden because `dsr_honest=0.0` and official CVaR is still zero exposure.

## Mudanças implementadas

Added a research-only CVaR evaluator for a fixed-fraction sandbox policy. The policy selects top1 by `rank_score_stage_a` per combo/date and never uses realized `stage_a_eligible` as an ex-ante rule.

## Artifacts gerados

- `reports\gates\phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate\research_sandbox_nonzero_exposure_cvar_summary.json`
- `reports\gates\phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate\research_sandbox_nonzero_exposure_daily_returns.parquet`
- `reports\gates\phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate\research_sandbox_nonzero_exposure_cvar_combo_metrics.parquet`
- `gate_report.json`
- `gate_report.md`
- `gate_manifest.json`
- `gate_metrics.parquet`

## Resultados

Active combo-days: `10576`. Median active days: `712.0`. Max empirical CVaR95 loss fraction: `0.01072844` versus limit `0.15`. Median combo Sharpe remains `-0.727203`.

## Avaliação contra gates

The research CVaR module measured nonzero sandbox exposure and all combos remained inside the 15% CVaR limit. The gate remains PARTIAL when alpha merit is negative, DSR honest is zero, and official exposure is zero.

## Riscos residuais

This is not official economic robustness. Official Phase4 exposure remains zero, DSR remains 0.0, and cross-sectional remains `ALIVE_BUT_NOT_PROMOTABLE`.

## Veredito final: advance / correct / abandon

`correct`. Keep the CVaR evaluator as research/sandbox evidence and continue with a materially different blocker, preferably DSR-zero diagnostics or alternate ex-ante sizing.
