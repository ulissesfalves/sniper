# SNIPER Next Autonomous Mission

Mode: `REGIME_SPECIFIC_META_DISAGREEMENT_GATE`

Previous gate executed: `phase5_research_cvar_constrained_meta_sizing_gate`

Previous family: `cvar_constrained_meta_sizing`

Previous candidate: `signed_meta_edge_t52_s15_k5_g04`

Previous result: `CVAR_CONSTRAINED_META_SIZING_CVAR_PASS_ALPHA_UNSTABLE`

Current next gate: `phase5_research_regime_specific_meta_disagreement_gate`

Checkpoint classification: `CHECKPOINT_CONTINUE_AUTONOMOUS`

Autonomous can continue: `true`

Human decision required: `false`

## Rationale

The checkpoint continuation mission executed `AGENDA-H03`
`cvar_constrained_meta_sizing` in
`phase5_research_cvar_constrained_meta_sizing_gate`.

The gate produced nonzero research/sandbox exposure and measured research CVaR
with `best_policy=signed_meta_edge_t52_s15_k5_g04`,
median Sharpe `2.040444`, median active days `698.0`, max CVaR95
`0.00356911`, median turnover `0.02376094` and max exposure `0.04`.

The gate is only `PARTIAL/correct` because min combo Sharpe is `-0.903026`,
there are 20 hard sensitivity/falsification failures, DSR honest remains `0.0`
and official CVaR remains zero exposure. This is useful research/sandbox CVaR
evidence, not promotability.

The next materially different executable agenda hypothesis is `AGENDA-H04`:
`regime_specific_meta_disagreement`. It tests whether the meta-disagreement
signal only has value under ex-ante HMM bull-probability regimes, rather than
repeating global disagreement, long-only uncertainty or risk-budget sizing.

## Execution Scope

Implement a research/sandbox gate that:

- reads `data/models/phase4/phase4_oos_predictions.parquet`;
- uses only ex-ante `p_bma_pkf`, `p_meta_calibrated`, `sigma_ewma` and
  `hmm_prob_bull`;
- defines predeclared HMM-regime buckets and thresholds;
- produces research/sandbox positions, daily returns, trade log and metrics
  if exposure is possible;
- evaluates median/min combo Sharpe, active days, CVaR, turnover, drawdown,
  costs, regime stability and leakage controls;
- never uses `pnl_real`, `stage_a_eligible`, `avg_sl_train`, `label` or
  `y_meta` as selection inputs;
- preserves `dsr_honest=0.0`, official zero exposure and non-promotability
  blockers.

## Next Mission

Mode: `REGIME_SPECIFIC_META_DISAGREEMENT_GATE`

Gate: `phase5_research_regime_specific_meta_disagreement_gate`

Required tests:

- ex-ante selection input validation;
- nonzero research exposure;
- regime-specific split coverage;
- median/min combo Sharpe;
- active days;
- CVaR research;
- turnover and drawdown;
- cost sensitivity;
- parameter sensitivity;
- universe stress;
- leakage controls.

## Criteria

PASS / advance:

- at least one predeclared regime-specific research/sandbox policy has nonzero
  exposure;
- max CVaR95 stays within predeclared research bound;
- median combo Sharpe > 0;
- min combo Sharpe > 0;
- median active days >= 120;
- cost and universe stress do not create hard falsifiers;
- no realized variable is used as selection input;
- no official promotion or paper readiness is claimed.

PARTIAL / correct:

- CVaR bound and nonzero exposure hold but alpha, active days or sensitivity
  remain weak.

FAIL / abandon:

- no predeclared regime-specific policy has nonzero exposure or median Sharpe
  remains nonpositive after stress checks.

INCONCLUSIVE:

- required Phase4 OOS predictions, sigma, HMM or calibrated probability columns
  are missing or schema is incompatible.

## Stop Conditions

Stop only if the gate requires external artifacts, specification change,
official promotion, paper readiness, A3/A4 reopening, real capital, credentials
or force push. If the selected hypothesis fails but another HIGH/MEDIUM agenda
hypothesis remains executable, continue in a later autonomous gate if mission
budget and review size remain safe.

## Required Skills

- `sniper-autonomous-implementation-manager`
- `sniper-quant-research-implementation`
- `sniper-gate-governance`

## Restrictions

- Research/sandbox only.
- No official promotion.
- No paper readiness.
- No A3/A4 reopening.
- No threshold relaxation.
- No fabricated artifacts.
- No realized variable as ex-ante rule.
