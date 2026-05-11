## Resumo executivo

Rank-score threshold sizing falsification result: `PARTIAL/correct`. Classification: `WEAK_POSITIVE_MEDIAN_ALPHA_UNSTABLE_NOT_PROMOTABLE`.

## Baseline congelado

Branch `codex/autonomous-sniper-implementation`, commit `1ba7f9499dfda35bab2de8d0929872516d67aca5`. This gate remains research-only and does not modify official policy.

## Mudanças implementadas

Added a predeclared research-only threshold-family evaluator for `rank_score_stage_a`, with optional HMM bull filter, fixed 1% sandbox exposure, CVaR measurement and cross-combo stability checks.

## Artifacts gerados

- `reports\gates\phase5_research_rank_score_threshold_sizing_falsification_gate\rank_score_threshold_family_summary.json`
- `reports\gates\phase5_research_rank_score_threshold_sizing_falsification_gate\rank_score_threshold_family_combo_metrics.parquet`
- `reports\gates\phase5_research_rank_score_threshold_sizing_falsification_gate\rank_score_threshold_family_policy_metrics.parquet`
- `gate_report.json`
- `gate_report.md`
- `gate_manifest.json`
- `gate_metrics.parquet`

## Resultados

Best policy `top1_score_ge_0_50` had median combo Sharpe `0.331124`, min combo Sharpe `-3.357339`, median active days `177.0`, and max CVaR95 loss fraction `0.01070472`.

## Avaliação contra gates

The best policy improved median alpha above zero and stayed within the sandbox CVaR limit, but it remains far below the DSR Sharpe requirement and has negative cross-combo stability. It cannot be promoted.

## Riscos residuais

DSR remains 0.0, official CVaR remains zero exposure, and cross-sectional remains `ALIVE_BUT_NOT_PROMOTABLE`. The result is a weak research candidate only.

## Veredito final: advance / correct / abandon

`correct`. Use the allowed PARTIAL correction to test a stability-preserving variant, otherwise freeze this threshold-family line.
