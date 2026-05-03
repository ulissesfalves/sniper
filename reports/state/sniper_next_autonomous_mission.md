# SNIPER Next Autonomous Mission

Mode: `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION_AND_EXECUTION`

Initial gate executed: `phase5_research_meta_disagreement_abstention_gate`

Selected family: `meta_calibration_disagreement_abstention`

Current next gate: `phase5_research_meta_disagreement_stability_falsification_gate`

## Rationale

The previous closed-loop mission reached `FULL_FREEZE_AFTER_REAUDIT` after
falsifying `short_high_p_bma_k3_p60_h70` and
`cluster_2_long_high_short_low_p60_h70_k3`. Final freeze is not accepted until a
new research-only agenda is generated and evaluated.

The selected hypothesis is materially different because it uses disagreement
between Phase4 BMA probability and Phase4 calibrated meta probability. It does
not repeat Stage A safe top1, rank-score thresholding, pure short-high BMA
polarity or cluster-conditioned polarity.

## Execution Scope

Implement a research/sandbox gate that:

- reads `data/models/phase4/phase4_oos_predictions.parquet`;
- predeclares meta-disagreement policies;
- selects only with ex-ante columns;
- produces nonzero research/sandbox positions, daily returns, trade log and
  metrics;
- classifies the best policy as PASS/PARTIAL/FAIL/INCONCLUSIVE;
- preserves DSR=0.0, official zero exposure and non-promotability blockers.

## Execution Result

`phase5_research_meta_disagreement_abstention_gate` returned `PASS/advance`.

Best policy:

- `short_bma_high_meta_low_p60_m40_k3`
- median Sharpe: `0.855486`
- min Sharpe: `0.220622`
- median active days: `322.0`
- max CVaR95: `0.00455141`
- classification: `META_DISAGREEMENT_RESEARCH_CANDIDATE_NOT_PROMOTABLE`

The policy remains research/sandbox only and below `sr_needed=4.47`. It does
not change `dsr_honest=0.0`, does not prove paper readiness, and does not
promote official.

## Next Mission

Mode: `META_DISAGREEMENT_STABILITY_FALSIFICATION_GATE`

Gate: `phase5_research_meta_disagreement_stability_falsification_gate`

Required tests:

- temporal subperiod stability;
- 5/10/20 bps cost stress;
- p-threshold and meta-threshold sensitivity;
- top-k sensitivity;
- universe stress;
- leakage controls proving no realized variable is used as selection input.

## Criteria

PASS / advance:

- at least one predeclared policy has nonzero exposure;
- median combo Sharpe > 0;
- min combo Sharpe > 0;
- median active days >= 120;
- max CVaR95 <= 0.15;
- no realized variable is used as selection input;
- no official promotion or paper readiness is claimed.

PARTIAL / correct:

- positive median Sharpe exists but stability, min Sharpe, active days or CVaR
  criteria are incomplete.

FAIL / abandon:

- no predeclared policy produces positive median Sharpe with nonzero exposure,
  or the policy depends on forbidden realized variables.

INCONCLUSIVE:

- required Phase4 OOS predictions are missing or schema is incompatible.

## Stop Conditions

Stop only if the gate requires external artifacts, specification change,
official promotion, paper readiness, A3/A4 reopening, real capital, credentials
or force push. If the selected hypothesis fails but another HIGH/MEDIUM agenda
hypothesis remains executable, continue in a later autonomous gate.

## Required Skills

- `sniper-autonomous-implementation-manager`
- `sniper-autonomous-research-agenda-synthesizer`
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
