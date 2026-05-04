# SNIPER Next Autonomous Mission

Mode: `START_RESEARCH_ONLY_THESIS`

Previous gate executed: `phase5_research_meta_uncertainty_abstention_gate`

Previous family: `meta_uncertainty_abstention_long_only`

Previous candidate: `long_bma_meta_agree_p65_m50_s10_k3`

Previous result: `META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS`

Current next gate: `phase5_research_cvar_constrained_meta_sizing_gate`

## Rationale

The mandatory next-gate chain first falsified
`short_bma_high_meta_low_p60_m40_k3` in
`phase5_research_meta_disagreement_stability_falsification_gate` and recorded
the abandonment in `phase5_research_meta_disagreement_candidate_decision_gate`.

The next agenda hypothesis `AGENDA-H02` was then executed in
`phase5_research_meta_uncertainty_abstention_gate`. It stayed long-only and
research/sandbox, used only ex-ante `p_bma_pkf`, `p_meta_calibrated` and
`sigma_ewma`, but failed stability stress. The best policy
`long_bma_meta_agree_p65_m50_s10_k3` had median Sharpe `0.447334`, min Sharpe
`-0.375889`, median active days `55.0`, max CVaR95 `0.00284763` and 19 hard
falsifiers.

The next materially different executable agenda hypothesis is `AGENDA-H03`:
`cvar_constrained_meta_sizing`. It focuses on risk-first sizing rather than
signal polarity, disagreement or pure long-only agreement.

## Execution Scope

Implement a research/sandbox gate that:

- reads `data/models/phase4/phase4_oos_predictions.parquet`;
- predeclares CVaR/risk-budgeted sizing policies using ex-ante `sigma_ewma`,
  `p_bma_pkf` and/or `p_meta_calibrated`;
- produces nonzero research/sandbox positions, daily returns, trade log and
  metrics if exposure is possible;
- computes CVaR research and stress evidence;
- does not use `pnl_real`, `stage_a_eligible`, `avg_sl_train` or realized
  labels as selection inputs;
- preserves `dsr_honest=0.0`, official zero exposure and non-promotability
  blockers.

## Next Mission

Mode: `CVAR_CONSTRAINED_META_SIZING_GATE`

Gate: `phase5_research_cvar_constrained_meta_sizing_gate`

Required tests:

- ex-ante selection input validation;
- nonzero research exposure;
- CVaR research under rho/stress assumptions where supported by existing data;
- median/min combo Sharpe;
- active days;
- turnover and drawdown when available;
- cost and parameter sensitivity if an initial candidate appears.

## Criteria

PASS / advance:

- at least one predeclared research sizing policy has nonzero exposure;
- max CVaR95 stays within predeclared research bound;
- median combo Sharpe > 0;
- min combo Sharpe > 0;
- median active days >= 120;
- no realized variable is used as selection input;
- no official promotion or paper readiness is claimed.

PARTIAL / correct:

- CVaR bound holds but alpha, active days or sensitivity remain weak.

FAIL / abandon:

- no predeclared sizing policy has nonzero exposure or median Sharpe remains
  nonpositive after cost/sensitivity checks.

INCONCLUSIVE:

- required Phase4 OOS predictions, sigma or calibrated probability columns are
  missing or schema is incompatible.

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
