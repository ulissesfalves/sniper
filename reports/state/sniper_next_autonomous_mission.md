# SNIPER Next Autonomous Mission

Mode: `FEATURE_FAMILY_ABLATION_BLOCKER_DECOMPOSITION_GATE`

Previous gate executed: `phase5_research_regime_specific_meta_disagreement_gate`

Previous family: `regime_specific_meta_disagreement`

Previous candidate: `neutral_short_meta_low_m40_k3`

Previous result: `REGIME_SPECIFIC_META_DISAGREEMENT_POSITIVE_BUT_UNSTABLE`

Current next gate: `phase5_research_feature_family_ablation_blocker_decomposition_gate`

Checkpoint classification: `CHECKPOINT_CONTINUE_AUTONOMOUS`

Autonomous can continue: `true`

Human decision required: `false`

## Rationale

The checkpoint continuation mission executed `AGENDA-H04`
`regime_specific_meta_disagreement` in
`phase5_research_regime_specific_meta_disagreement_gate`.

The gate produced nonzero research/sandbox exposure and measured research CVaR
with `best_policy=neutral_short_meta_low_m40_k3`, median Sharpe `0.726729`,
median active days `82.0`, max CVaR95 `0.00315145`, median turnover
`0.07050847` and max exposure `0.04`.

The gate is only `PARTIAL/correct` because min combo Sharpe is `-0.911080`,
active days are sparse and there are 13 hard sensitivity/falsification failures.
This is research/sandbox evidence, not promotability.

The next materially different executable agenda hypothesis is `AGENDA-H05`:
`feature_family_ablation_blocker_decomposition`. It is a diagnostic gate, not a
trading policy, and should decompose which ex-ante feature families contribute
to the Sharpe/DSR/CVaR blockers before any freeze or new agenda expansion.

## Execution Scope

Implement a research/sandbox diagnostic gate that:

- reads `data/models/phase4/phase4_oos_predictions.parquet`;
- reads `data/models/research/phase4_cross_sectional_ranking_baseline/stage_a_predictions.parquet`
  if available;
- evaluates ex-ante feature families without treating diagnostics as an
  operational signal;
- reports coverage, rank/score correlations, realized outcome association as
  diagnostic-only evidence, and blocker decomposition;
- identifies whether any spec-safe next family remains;
- never uses diagnostic output as official promotion evidence;
- preserves `dsr_honest=0.0`, official zero exposure and non-promotability
  blockers.

## Next Mission

Mode: `FEATURE_FAMILY_ABLATION_BLOCKER_DECOMPOSITION_GATE`

Gate: `phase5_research_feature_family_ablation_blocker_decomposition_gate`

Required tests:

- artifact availability and schema validation;
- no diagnostic variable is treated as operational signal;
- no realized variable is used as ex-ante rule;
- feature-family coverage summary;
- blocker decomposition output;
- recommendation of next family, freeze, or external-resource requirement.

## Criteria

PASS / advance:

- produces reproducible feature-family decomposition;
- identifies at least one spec-safe next family or a governed reason no such
  family remains;
- does not promote official or declare paper readiness.

PARTIAL / correct:

- produces decomposition but cannot identify an actionable in-repo family.

FAIL / abandon:

- diagnostic cannot load required artifacts or produces contradictory
  governance recommendations.

INCONCLUSIVE:

- required artifacts are missing or schema is incompatible.

## Stop Conditions

Stop only if the gate requires external artifacts, specification change,
official promotion, paper readiness, A3/A4 reopening, real capital, credentials
or force push. If the diagnostic finds no HIGH/MEDIUM executable family after
H05, a governed freeze/update-draft-PR decision may be valid.

## Required Skills

- `sniper-autonomous-implementation-manager`
- `sniper-quant-research-implementation`
- `sniper-gate-governance`

## Restrictions

- Diagnostic/research only.
- No operational signal from diagnostic output.
- No official promotion.
- No paper readiness.
- No A3/A4 reopening.
- No threshold relaxation.
- No fabricated artifacts.
- No realized variable as ex-ante rule.
