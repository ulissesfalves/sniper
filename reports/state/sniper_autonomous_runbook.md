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
9. Execute one scoped gate.
10. Generate the complete gate pack.
11. Run relevant tests and validations.
12. Update `reports/state/**`.
13. Commit one coherent gate.
14. Push the current branch only when useful for the existing draft PR.
15. Stop on any stop condition.

## Current Recommended Mode

`STOP_FOR_HUMAN_DECISION`

Allowed mission modes:

- `START_RESEARCH_ONLY_THESIS`
- `CONTINUE_AUTONOMOUS`
- `RUN_GLOBAL_REAUDIT`
- `FREEZE_LINE` only after budget exhaustion or no materially new hypothesis.
- `STOP_FOR_HUMAN_DECISION` after a freeze gate.

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
- PARTIAL/correct: attempt one internal correction;
- FAIL/abandon: mark falsified and choose the next materially new hypothesis;
- INCONCLUSIVE external artifact: stop and request artifact;
- INCONCLUSIVE internal environment: repair environment;
- external blocker: stop.

## Exploration Budget

- Up to 5 research-only gates per mission.
- Up to 2 consecutive failures in the same hypothesis type.
- Up to 1 correction attempt per `PARTIAL/correct` gate.
- Stop if the next step requires an external resource or specification change.
- Stop if there is no materially new hypothesis.

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
with zero promotable candidates. The next research-only thesis must be supplied
or approved as materially new before another autonomous implementation mission:

- `dsr_honest=0.0`;
- official CVaR zero exposure;
- cross-sectional `ALIVE_BUT_NOT_PROMOTABLE`;
- absence of robust recent operational signal.

Do not reuse `stage_a_eligible` as an ex-ante decision rule; it is treated as a
realized diagnostic field.

Current recommended next gate: none. Recommended next action is human strategic
decision or updating the existing draft PR with the new evidence.

## Forbidden Interpretations

- Clean regeneration is reproducibility evidence, not economic merit.
- `PASS_ZERO_EXPOSURE` is not economic CVaR robustness.
- Research baseline is not official.
- PR #1 is not merge/readiness approval.
- Cross-sectional remains not promotable.

## Stop Conditions

Stop if the next step requires:

- human product/strategy decision;
- exploration budget exhaustion;
- no materially new hypothesis;
- external artifact or private data not present;
- credential, paid API or real capital;
- merge or ready PR transition;
- specification change;
- promotion while DSR remains 0.0;
- paper readiness while CVaR remains zero exposure;
- A3/A4 reopening without strong new evidence.
