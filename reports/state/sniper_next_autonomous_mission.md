# SNIPER Next Autonomous Mission

Mode: `START_RESEARCH_ONLY_THESIS`

Previous gate executed: `phase5_research_meta_disagreement_stability_falsification_gate`

Previous decision gate executed: `phase5_research_meta_disagreement_candidate_decision_gate`

Previous family: `meta_calibration_disagreement_abstention`

Previous candidate: `short_bma_high_meta_low_p60_m40_k3`

Previous result: `META_DISAGREEMENT_RESEARCH_CANDIDATE_FALSIFIED`

Current next gate: `phase5_research_meta_uncertainty_abstention_gate`

## Rationale

The agenda expansion selected `AGENDA-H01` first and the initial
meta-disagreement gate returned `PASS/advance`. The mandatory chained
stability/falsification gate then found 25 hard falsifiers, including temporal
subperiod failures, 20 bps cost stress, parameter sensitivity failures and
universe stress. The candidate decision gate recorded `PASS/abandon`.

The next materially different executable agenda hypothesis is `AGENDA-H02`:
`meta_uncertainty_abstention_long_only`. It is long-only research/sandbox, uses
Phase4 BMA and calibrated meta agreement rather than short-high disagreement,
and remains non-promotional.

## Execution Scope

Implement a research/sandbox gate that:

- reads `data/models/phase4/phase4_oos_predictions.parquet`;
- predeclares long-only meta uncertainty/agreement abstention policies;
- selects only with ex-ante columns such as `p_bma_pkf`,
  `p_meta_calibrated` and `sigma_ewma`;
- does not use `pnl_real`, `stage_a_eligible`, `avg_sl_train` or realized
  labels as selection inputs;
- produces nonzero research/sandbox positions, daily returns, trade log and
  metrics if the hypothesis has exposure;
- preserves `dsr_honest=0.0`, official zero exposure and non-promotability
  blockers.

## Next Mission

Mode: `META_UNCERTAINTY_ABSTENTION_GATE`

Gate: `phase5_research_meta_uncertainty_abstention_gate`

Required tests:

- ex-ante selection input validation;
- long-only nonzero research exposure;
- median/min combo Sharpe;
- active days;
- CVaR research;
- turnover and drawdown when available;
- basic cost and parameter sensitivity if an initial candidate appears.

## Criteria

PASS / advance:

- at least one predeclared long-only research policy has nonzero exposure;
- median combo Sharpe > 0;
- min combo Sharpe > 0;
- median active days >= 120;
- max CVaR95 <= 0.15;
- no realized variable is used as selection input;
- no official promotion or paper readiness is claimed.

PARTIAL / correct:

- positive median Sharpe exists but min Sharpe, active days, sensitivity or
  CVaR criteria are incomplete.

FAIL / abandon:

- no predeclared long-only policy produces positive median Sharpe with nonzero
  exposure, or the policy depends on forbidden realized variables.

INCONCLUSIVE:

- required Phase4 OOS predictions are missing or schema is incompatible.

## Stop Conditions

Stop only if the gate requires external artifacts, specification change,
official promotion, paper readiness, A3/A4 reopening, real capital, credentials
or force push. If the selected hypothesis fails but another HIGH/MEDIUM agenda
hypothesis remains executable, continue in a later autonomous gate.

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
