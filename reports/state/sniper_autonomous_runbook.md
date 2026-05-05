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
15. Use internal strategic decision rubrics before any human-decision stop.
16. Continue through materially different research-only hypotheses, candidate
    audit/falsification gates, global reaudits, state updates and draft PR
    updates until budget exhaustion or a real stop condition.
17. Do not stop merely because the next recommendation is technical and safe;
    execute it automatically.
18. Before accepting final freeze after reaudit, execute
    `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`.
19. If `reports/state/sniper_next_autonomous_mission.md` defines a safe
    `Current next gate`, execute that gate automatically.

## Current Recommended Mode

`START_RESEARCH_ONLY_THESIS`

Next logical skill: `sniper-autonomous-implementation-manager`.

Next mode: `CVAR_CONSTRAINED_META_SIZING_GATE`.

Next gate: `phase5_research_cvar_constrained_meta_sizing_gate`.

Current candidate chain:

- family: `meta_uncertainty_abstention_long_only`;
- candidate: none preserved;
- gate: `phase5_research_meta_uncertainty_abstention_gate`;
- status: `FAIL/abandon`;
- final status: `META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS`;
- best policy: `long_bma_meta_agree_p65_m50_s10_k3`;
- hard falsifier count: `19`;
- promotion allowed: `false`;
- paper readiness allowed: `false`.

`Pode continuar autonomamente: sim` is not a stop condition.
`Próximo gate recomendado` is not a stop condition.
The H01 initial PASS was not final. H01 and H02 have now been falsified and
abandoned, so the next materially different agenda hypothesis is `AGENDA-H03`.

Allowed mission modes:

- `START_RESEARCH_ONLY_THESIS`
- `CONTINUE_AUTONOMOUS`
- `RUN_GLOBAL_REAUDIT`
- `RUN_GLOBAL_REAUDIT_CANDIDATE`
- `POST_CANDIDATE_FALSIFICATION_GLOBAL_REAUDIT`
- `CANDIDATE_STABILITY_GATE`
- `CANDIDATE_FALSIFICATION_GATE`
- `CANDIDATE_DECISION_GATE`
- `UPDATE_STATE`
- `UPDATE_DRAFT_PR`
- `OPEN_RESEARCH_GATE`
- `NEXT_GATE_CHAIN_EXECUTION`
- `CHECKPOINT_CONTINUE_AUTONOMOUS`
- `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`
- `GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`
- `META_DISAGREEMENT_STABILITY_FALSIFICATION_GATE`
- `META_UNCERTAINTY_ABSTENTION_GATE`
- `CVAR_CONSTRAINED_META_SIZING_GATE`
- `FREEZE_LINE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED` only after agenda
  expansion generates no HIGH/MEDIUM executable hypothesis.

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

- PASS research-only without a functional survivor: register as candidate,
  never promote;
- PASS research-only with a surviving candidate: run autonomous candidate
  audit/falsification before considering freeze or human decision;
- PARTIAL/correct: attempt up to 2 internal corrections when defensible;
- FAIL/abandon: mark falsified and choose the next materially new hypothesis;
- INCONCLUSIVE external artifact: stop and request artifact;
- INCONCLUSIVE internal environment: repair environment;
- external blocker: stop.

## Closed-Loop Autonomous Flow

Closed-loop execution is active. A mission must not end only because it produced
a next recommendation. If the next action is safe, internal to the repo and
governance-allowed, execute it automatically.

Automatically execute:

- `RUN_GLOBAL_REAUDIT`
- `RUN_GLOBAL_REAUDIT_CANDIDATE`
- `POST_CANDIDATE_FALSIFICATION_GLOBAL_REAUDIT`
- `START_RESEARCH_ONLY_THESIS`
- `CONTINUE_AUTONOMOUS`
- `CANDIDATE_STABILITY_GATE`
- `CANDIDATE_FALSIFICATION_GATE`
- `CANDIDATE_DECISION_GATE`
- `UPDATE_STATE`
- `UPDATE_DRAFT_PR`
- `OPEN_RESEARCH_GATE`
- `NEXT_GATE_CHAIN_EXECUTION`
- `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`
- `GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`
- `FREEZE_LINE` only after freeze criteria are satisfied.

Stop only for external artifacts/data, credentials or paid APIs, access outside
the repo, merge, official promotion, paper readiness, specification change, real
capital or non-technical business risk acceptance.

## Next Gate Chain Execution

When `reports/state/sniper_next_autonomous_mission.md` contains `Current next
gate`, `Next Mission`, `Required tests`, `Criteria` and `Stop Conditions`, treat
it as executable mission context. If the next gate is safe, internal to the
repo, research/sandbox only, does not require external artifacts, does not
promote official and does not declare paper readiness, run it automatically.

Mandatory live-candidate sequence:

1. initial research-only `PASS/advance`;
2. stability/falsification gate;
3. candidate decision gate;
4. `reports/state/**` update;
5. draft PR update when reviewable.

Current completed next gate:
`phase5_research_meta_uncertainty_abstention_gate`.

Current required next agenda gate:
`phase5_research_cvar_constrained_meta_sizing_gate`.

Do not stop after `Pode continuar autonomamente: sim`, after
`Próximo gate recomendado`, or after a candidate falsification while a
materially different HIGH/MEDIUM agenda hypothesis remains safe and executable.

## Checkpoint Continuation

Large reviewable packages are checkpoints, not final stops, when a safe next
gate remains in `reports/state/sniper_next_autonomous_mission.md`.

Use `CHECKPOINT_CONTINUE_AUTONOMOUS` when:

- research/sandbox work was delivered;
- the package is large but reviewable;
- JSON/YAML/parquet/gate-pack validation passed;
- relevant tests passed;
- the worktree can be left clean;
- the existing PR draft can be updated;
- the next gate is internal to the repo and does not require external resource,
  official promotion, paper readiness or merge.

Checkpoint sequence:

1. consolidate files;
2. validate JSON/YAML/parquet/gate packs;
3. run relevant tests;
4. commit a coherent package;
5. push to `origin/codex/autonomous-sniper-implementation`;
6. update the existing PR draft when reviewable;
7. update `reports/state/**`;
8. reread `reports/state/sniper_next_autonomous_mission.md`;
9. continue automatically if the next gate remains safe.

`FUNCTIONAL_RESEARCH_MODULE_DELIVERED` is not final when a safe next gate
exists. The current safe next gate is
`phase5_research_cvar_constrained_meta_sizing_gate` from `AGENDA-H03`.

Closed-loop budget:

- up to 25 gates per mission;
- up to 5 materially different hypothesis families;
- up to 4 gates per family;
- up to 2 intermediate global audits;
- up to 2 draft PR updates;
- up to 3 automatic checkpoints per long mission;
- up to 50 total campaign gates;
- up to 10 materially different hypothesis families per campaign;
- up to 3 draft PR updates per long mission;
- stop if changes remain too large for reasonable human review after checkpoint
  handling.

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
- any surviving research-only candidate was audited/falsified.
- the last candidate was falsified;
- post-falsification global reaudit was executed;
- autonomous research agenda expansion was executed after the latest
  falsification;
- no HIGH/MEDIUM priority agenda hypothesis remains executable inside the repo.

## Human Decision Last Resort

Human decision is allowed only for external artifacts/data, credentials, paid or
private API/access, operation outside the authorized repo, merge, official
promotion, paper readiness, specification change, real capital, or explicit
non-technical product/business risk acceptance.

Do not stop for human decision while any internal path remains:

- surviving research-only candidate;
- `RUN_GLOBAL_REAUDIT` or `RUN_GLOBAL_REAUDIT_CANDIDATE`;
- `START_RESEARCH_ONLY_THESIS` or `CONTINUE_AUTONOMOUS`;
- candidate stability or falsification gate;
- candidate decision gate;
- post-falsification global reaudit;
- state update or draft PR update;
- autonomous research agenda expansion;
- governed freeze review;
- internal strategic decision rubric.

If the next step is ambiguous, apply a strategic decision rubric equivalent to
`sniper-strategic-decision-governor` and choose among:
`RUN_GLOBAL_REAUDIT`, `RUN_GLOBAL_REAUDIT_CANDIDATE`,
`POST_CANDIDATE_FALSIFICATION_GLOBAL_REAUDIT`, `CONTINUE_AUTONOMOUS`,
`START_RESEARCH_ONLY_THESIS`, `FREEZE_LINE`, `UPDATE_DRAFT_PR`, `UPDATE_STATE`,
`OPEN_RESEARCH_GATE`, `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`,
`GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`, or `STOP_FOR_EXTERNAL_RESOURCE`.

## Research Agenda Expansion Before Final Freeze

The current mission reached `FULL_FREEZE_AFTER_REAUDIT` after falsifying
`cluster_conditioned_polarity`, but final permanent freeze is not accepted until
the agenda is expanded from specification, code, gates, falsifications,
blockers and existing modules.

Run `sniper-autonomous-research-agenda-synthesizer` in mode
`GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`.

Required outputs:

- `reports/state/sniper_research_agenda.yaml`
- `reports/state/sniper_hypothesis_inventory.md`
- `reports/state/sniper_next_autonomous_mission.md`

If the agenda produces at least one HIGH/MEDIUM priority hypothesis executable
inside the repo, select the highest expected-value hypothesis, open a new
research-only gate, implement only in research/sandbox, validate, falsify or
preserve, and continue closed-loop execution.

If the agenda produces no HIGH/MEDIUM executable hypothesis, classify
`FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED`, update
`reports/state/**`, update the existing draft PR when reviewable, and stop.

Agenda expansion result:

- generated `reports/state/sniper_research_agenda.yaml`;
- generated `reports/state/sniper_hypothesis_inventory.md`;
- generated `reports/state/sniper_next_autonomous_mission.md`;
- selected `AGENDA-H01`:
  `meta_calibration_disagreement_abstention`;
- executed `phase5_research_meta_disagreement_abstention_gate`;
- result: `PASS/advance`;
- classification:
  `META_DISAGREEMENT_RESEARCH_CANDIDATE_NOT_PROMOTABLE`;
- best policy: `short_bma_high_meta_low_p60_m40_k3`;
- median Sharpe: `0.855486`;
- min Sharpe: `0.220622`;
- median active days: `322.0`;
- max CVaR95: `0.00455141`;
- still below `sr_needed=4.47`;
- research/sandbox only.

The meta-disagreement stability/falsification step has now run and falsified
the candidate. Do not treat the initial PASS as promotion, readiness, or robust
candidate survival.

Meta-disagreement chain execution result:

- `phase5_research_meta_disagreement_stability_falsification_gate`:
  `FAIL/abandon`;
- `phase5_research_meta_disagreement_candidate_decision_gate`: `PASS/abandon`;
- final classification: `META_DISAGREEMENT_RESEARCH_CANDIDATE_FALSIFIED`;
- hard falsifier count: `25`;
- next agenda hypothesis: `AGENDA-H02`;
- next gate: `phase5_research_meta_uncertainty_abstention_gate`.

Meta-uncertainty agenda result:

- `phase5_research_meta_uncertainty_abstention_gate`: `FAIL/abandon`;
- final classification: `META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS`;
- best policy: `long_bma_meta_agree_p65_m50_s10_k3`;
- median Sharpe: `0.447334`;
- min Sharpe: `-0.375889`;
- median active days: `55.0`;
- max CVaR95: `0.00284763`;
- hard falsifier count: `19`;
- next agenda hypothesis: `AGENDA-H03`;
- next gate: `phase5_research_cvar_constrained_meta_sizing_gate`.

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

Current candidate audit/falsification update:

- `phase5_research_candidate_global_reaudit_gate` passed as research-only
  governance/ex-ante reaudit.
- `phase5_research_candidate_stability_gate` returned `PARTIAL/correct` after
  29 of 49 scenarios failed.
- `phase5_research_candidate_falsification_gate` returned `FAIL/abandon` with
  hard falsifiers `temporal_subperiod_min_sharpe=-1.160839` and
  `extra_cost_20bps_min_sharpe=-0.12201`.
- `phase5_research_candidate_decision_gate` classified
  `short_high_p_bma_k3_p60_h70` as `RESEARCH_CANDIDATE_FALSIFIED`.

Historical post-candidate recommendation:
`none_until_materially_new_hypothesis_or_external_resource`.

That recommendation was superseded by post-falsification reaudit, governed
freeze review, autonomous agenda expansion, the meta-disagreement candidate
chain, and the meta-uncertainty long-only gate. The current recommendation is no
longer human review as a stopping point; it is
`CVAR_CONSTRAINED_META_SIZING_GATE` from `AGENDA-H03`. Do not promote the
abandoned candidates: they used sandbox/research exposure, remained below
`sr_needed=4.47`, and failed autonomous falsification while `dsr_honest=0.0`.

Post-falsification protocol:

1. `phase5_post_candidate_falsification_global_reaudit_gate` was executed and
   passed.
2. `phase5_research_cluster_conditioned_polarity_gate` tested a materially new
   in-repo family and found `cluster_2_long_high_short_low_p60_h70_k3`.
3. `phase5_research_cluster_conditioned_polarity_falsification_gate` falsified
   that candidate with 13 hard falsifiers.
4. `phase5_research_cluster_conditioned_polarity_decision_gate` recorded the
   candidate as falsified.
5. `phase5_post_candidate_falsification_governed_freeze_gate` passed with
   `FULL_FREEZE_AFTER_REAUDIT`.
6. Execute `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION` before accepting permanent
   final freeze.
7. Update `reports/state/**` and the existing draft PR. Do not open PR ready.

## Current Closed-Loop Result

Current classification: `FULL_FREEZE_AFTER_REAUDIT` pending research agenda
expansion.

Gates added in the closed-loop mission:

- `phase5_post_candidate_falsification_global_reaudit_gate`: `PASS/advance`;
- `phase5_research_cluster_conditioned_polarity_gate`: `PASS/advance`;
- `phase5_research_cluster_conditioned_polarity_falsification_gate`:
  `FAIL/abandon`;
- `phase5_research_cluster_conditioned_polarity_decision_gate`:
  `PASS/abandon`;
- `phase5_post_candidate_falsification_governed_freeze_gate`: `PASS/freeze`.

The current autonomous loop executed `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`,
falsified H01 in the explicit next stability/falsification chain, and then
falsified H02 in a long-only research/sandbox gate. Permanent final freeze is
still not legitimate while agenda hypotheses remain. The next safe gate is
`phase5_research_cvar_constrained_meta_sizing_gate`. This is not promotion, not
paper readiness and not merge approval.

## Forbidden Interpretations

- Clean regeneration is reproducibility evidence, not economic merit.
- `PASS_ZERO_EXPOSURE` is not economic CVaR robustness.
- Research baseline is not official.
- PR #1 is not merge/readiness approval.
- Cross-sectional remains not promotable.
- `short_high_p_bma_k3_p60_h70` is not official, does not clear DSR, and has
  been abandoned after temporal/cost falsification.

## Stop Conditions

Stop if the next step requires:

- human product/strategy decision only after internal strategic decision and
  only when no internal research/sandbox/candidate audit path remains;
- exploration budget exhaustion;
- no materially new HIGH/MEDIUM hypothesis after candidate falsification,
  post-falsification global reaudit, governed freeze and autonomous research
  agenda expansion;
- external artifact or private data not present;
- credential, paid API or real capital;
- merge or ready PR transition;
- specification change;
- promotion while DSR remains 0.0;
- paper readiness while CVaR remains zero exposure;
- A3/A4 reopening without strong new evidence.

Do not stop for human decision while there is an open gap, defensible
research-only hypothesis, internal correction, unfinished quantitative
diagnostic, possible sandbox/research module, post-falsification global reaudit,
autonomous research agenda expansion, state update, draft PR update or governed
freeze review inside the repo. The prior surviving candidate and the
cluster-conditioned candidate, the meta-disagreement candidate and the
meta-uncertainty long-only line have now been audited/falsified. The current
line requires the next materially different agenda hypothesis before any new
freeze.
