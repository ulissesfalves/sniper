# SNIPER Autonomous Runbook

## Standard Flow

1. Read `AGENTS.md`.
2. Read `docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md`.
3. Read `reports/state/sniper_current_state.json`.
4. Read `reports/state/sniper_spec_gap_backlog.yaml`.
5. Read `reports/state/sniper_decision_ledger.md`.
6. Read `reports/state/sniper_artifact_registry.json`.
7. Choose only a mode listed in `allowed_next_modes`.
8. Confirm the intended action is not listed in `forbidden_next_modes`.
9. Execute the next scoped gate in the current full-phase mission.
10. Generate the complete gate pack.
11. Run relevant tests and validations.
12. Update `reports/state/**`.
13. Commit one coherent gate.
14. Push the current branch only when useful for the existing draft PR.
15. Continue through materially different research-only hypotheses until budget
    exhaustion or a real stop condition.

## Current Recommended Mode

`CONTINUE_AUTONOMOUS`

Allowed mission modes:

- `START_RESEARCH_ONLY_THESIS`
- `CONTINUE_AUTONOMOUS`
- `RUN_GLOBAL_REAUDIT`
- `FREEZE_LINE` only after full freeze requirements are satisfied.
- `STOP_FOR_HUMAN_DECISION` only for external/governance cases.

Forbidden modes:

- `OFFICIAL_PROMOTION`
- `PAPER_READINESS`
- `A3_REOPEN`
- `A4_REOPEN`
- `THRESHOLD_RELAXATION`
- `REAL_TRADING`

## Phased Autonomous Flow

FASE A - State and memory:

- read `AGENTS.md`;
- read `docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md`;
- read `reports/state/**`;
- read relevant `reports/gates/**`;
- identify open blockers.

FASE B - Hypothesis selection:

- choose the highest expected-value open gap;
- create a falsifiable research-only hypothesis;
- declare why it does not reopen A3/A4 or promote official;
- declare abandon and advance criteria.

FASE C - Research/sandbox implementation:

- implement only in research/sandbox;
- do not touch official;
- do not relax thresholds;
- do not fabricate artifacts;
- do not use realized information as an ex-ante rule.

FASE D - Validation and gate:

- run tests;
- generate the full gate pack;
- classify PASS/PARTIAL/FAIL/INCONCLUSIVE;
- update `reports/state/**`;
- commit incrementally.

FASE E - Autonomous decision:

- PASS research-only: register as candidate, never promote;
- PARTIAL/correct: attempt up to 2 internal corrections when defensible;
- FAIL/abandon: mark falsified and choose the next materially new hypothesis;
- INCONCLUSIVE external artifact: stop and request artifact;
- INCONCLUSIVE internal environment: repair environment;
- external blocker: stop.

## Full Phase Budget

- Up to 15 research-only gates per mission.
- Up to 3 materially different hypothesis families.
- Up to 3 gates per family.
- Up to 2 correction attempts per `PARTIAL/correct` gate.
- Up to 1 intermediate global audit when needed.
- Up to 1 draft PR update at the end of the mission.

FAIL/abandon for one hypothesis does not end the mission while a materially
different defensible research-only hypothesis remains inside the repo.

Freeze is allowed only after:

- at least 2 materially different families were tested;
- explicit DSR diagnostics exist;
- research CVaR with nonzero exposure was evaluated when research exposure is
  available;
- family comparison/falsification was recorded;
- `reports/state/sniper_decision_ledger.md` was updated.

## Functional Phase Order

1. Deep quantitative diagnostics:
   decompose SR, SR_needed, skew/kurtosis, n_trials, subperiods, drawdown,
   turnover and sensitivity.
2. Ex-ante research exposure generation:
   create research/sandbox snapshot proxy, positions, target weights, trade log
   and metrics using only ex-ante features.
3. Research CVaR with nonzero exposure:
   compute CVaR and rho=1 stress on the research portfolio.
4. Alternative signal/sizing family:
   test a materially different family such as volatility-targeted rank
   portfolio, risk-budgeted top-k, drawdown-aware activation, regime-filtered
   portfolio, CVaR-constrained sizing, defensive ensemble or uncertainty-based
   abstention.
5. Falsification and selection:
   compare families, select a survivor or freeze only when freeze requirements
   are satisfied.

The Stage A nonzero-exposure thesis was abandoned. The research sandbox CVaR
evaluator then measured nonzero sandbox exposure with max CVaR95 loss fraction
`0.01072844`, but stayed `PARTIAL/correct` because alpha merit, DSR and official
zero exposure remain blockers. The DSR diagnostic then passed as root-cause
diagnostic only: `dsr_honest=0.0`, `sr_needed=4.47`, and Sharpe gap `3.5892`.
The rank-score threshold family found a weak research-only candidate
`top1_score_ge_0_50` with median combo Sharpe `0.331124` and max CVaR95 loss
fraction `0.01070472`, but min combo Sharpe stayed `-3.357339`.
The one allowed stability correction then failed/abandoned the threshold-family
line: best correction `score_0_60` improved min Sharpe to `-2.553324`, but
reduced median Sharpe to `0.195832` and did not clear DSR.
The hypothesis-space freeze gate then passed/froze the current autonomous line
with zero promotable candidates under the previous 5-gate budget. Under the full
phase policy, a future mission may continue autonomously if it selects a
materially different family inside the repo:

- `dsr_honest=0.0`;
- official CVaR zero exposure;
- cross-sectional `ALIVE_BUT_NOT_PROMOTABLE`;
- absence of robust recent operational signal.

Do not reuse `stage_a_eligible` as an ex-ante decision rule; it is treated as a
realized diagnostic field.

Current mission update:

- `phase5_research_deep_quant_diagnostic_gate` completed deeper DSR/SR,
  subperiod, drawdown, turnover and sensitivity diagnostics.
- `phase5_research_alternative_exante_family_gate` abandoned the long-only
  p_bma/sigma/hmm family because no tested policy produced positive median
  safe alpha.
- `phase5_research_signal_polarity_long_short_gate` found a positive
  research-only signal-polarity candidate but required correction.
- `phase5_research_signal_polarity_stability_correction_gate` produced the
  surviving sandbox candidate `short_high_p_bma_k3_p60_h70` with median combo
  Sharpe `1.361592`, min combo Sharpe `0.261111`, median active days `471.0`,
  and max CVaR95 loss fraction `0.00344841`.
- `phase5_research_full_phase_family_comparison_gate` selected that candidate
  as research-only and non-promotable.

Current recommended next gate:
`phase5_research_signal_polarity_candidate_global_review_gate`.

Recommended next mode: `RUN_GLOBAL_REAUDIT` or draft PR update/review. Do not
promote the survivor: it uses short sandbox exposure and remains below
`sr_needed=4.47` while `dsr_honest=0.0`.

## Forbidden Interpretations

- Clean regeneration is reproducibility evidence, not economic merit.
- `PASS_ZERO_EXPOSURE` is not economic CVaR robustness.
- Research baseline is not official.
- PR #1 is not merge/readiness approval.
- Cross-sectional remains not promotable.

## Stop Conditions

Stop if the next step requires:

- human product/strategy decision only when no internal research/sandbox path remains;
- exploration budget exhaustion;
- no materially new hypothesis;
- external artifact or private data not present;
- credential, paid API or real capital;
- merge or ready PR transition;
- specification change;
- promotion while DSR remains 0.0;
- paper readiness while CVaR remains zero exposure;
- A3/A4 reopening without strong new evidence.

Do not stop for human decision while there is an open gap, defensible
research-only hypothesis, internal correction, unfinished quantitative
diagnostic, or possible sandbox/research module inside the repo.
