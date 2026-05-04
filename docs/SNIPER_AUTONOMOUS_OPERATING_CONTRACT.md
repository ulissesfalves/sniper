# SNIPER Autonomous Operating Contract

This contract defines what Codex can do autonomously inside the SNIPER repo.

## Autonomous Actions

Codex may act without an additional user prompt when the action remains inside
the current repository and preserves governance:

- execute closed-loop autonomous missions inside this repository;
- automatically execute safe technical next recommendations inside the repo;
- automatically execute a safe next gate defined in
  `reports/state/sniper_next_autonomous_mission.md`;
- automatically run `RUN_GLOBAL_REAUDIT` when recommended;
- automatically run `POST_CANDIDATE_FALSIFICATION_GLOBAL_REAUDIT` after a
  candidate is falsified;
- automatically run `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION` before accepting a
  final freeze after reaudit;
- automatically start a materially new research-only thesis when the backlog
  contains an executable ex-ante hypothesis;
- create a branch with the `codex/` prefix when needed;
- execute autonomous full phase research missions inside this repository;
- use internal strategic decision rubrics when the next action is ambiguous;
- choose the next open gap from the backlog;
- choose a materially different research-only hypothesis family;
- create a new falsifiable research-only hypothesis;
- create or update a gate pack;
- implement research-only or sandbox corrections;
- repair the local test environment;
- execute tests and validation commands;
- generate reproducible gate evidence;
- falsify and abandon a failed research-only hypothesis;
- choose the next materially different hypothesis;
- commit one coherent gate at a time;
- push the active working branch;
- update the existing draft PR branch when review-ready, at most once at the end
  of a mission unless explicitly requested otherwise;
- abandon a failed research hypothesis;
- update `reports/state/**`.

## Human Decision Is Last Resort

Codex may stop for human decision only when the next step requires external
artifacts/data, credentials, paid/private APIs, access outside the authorized
repo, merge, official promotion, paper readiness, specification change, real
capital, or explicit product/business risk acceptance that is not technical.

Codex must not stop for human decision while any safe internal path remains:

- a surviving research-only candidate;
- a candidate audit gate;
- a candidate stability gate;
- a candidate falsification gate;
- a candidate decision gate;
- a post-candidate-falsification global reaudit;
- an autonomous research agenda expansion;
- a state update or draft PR update;
- a governed freeze review;
- `RUN_GLOBAL_REAUDIT`;
- `RUN_GLOBAL_REAUDIT_CANDIDATE`;
- `POST_CANDIDATE_FALSIFICATION_GLOBAL_REAUDIT`;
- `START_RESEARCH_ONLY_THESIS`;
- `CONTINUE_AUTONOMOUS`;
- an internal strategic decision rubric equivalent to
  `sniper-strategic-decision-governor`.

## Surviving Research Candidate Protocol

Historical surviving candidate after autonomous audit/falsification:

- policy: `short_high_p_bma_k3_p60_h70`;
- scope: research/sandbox only;
- median Sharpe: `1.361592`;
- min Sharpe: `0.261111`;
- median active days: `471.0`;
- max CVaR95: `0.00344841`;
- below `sr_needed=4.47`;
- failed temporal and 20 bps cost falsification;
- not promotable.

That candidate has already been audited and falsified. Do not revive it without
materially new evidence.

Latest research/sandbox candidate chain:

- family: `meta_calibration_disagreement_abstention`;
- candidate: `short_bma_high_meta_low_p60_m40_k3`;
- initial gate: `phase5_research_meta_disagreement_abstention_gate`;
- status: initial `PASS/advance`;
- stability/falsification gate: `phase5_research_meta_disagreement_stability_falsification_gate`;
- decision gate: `phase5_research_meta_disagreement_candidate_decision_gate`;
- final status: `META_DISAGREEMENT_RESEARCH_CANDIDATE_FALSIFIED`;
- promotion allowed: `false`;
- paper readiness allowed: `false`.

The current recommended mode is
`START_RESEARCH_ONLY_THESIS`; the next gate is
`phase5_research_meta_uncertainty_abstention_gate` from `AGENDA-H02`. The
falsified meta-disagreement candidate does not prove robustness, promotion
eligibility or paper readiness.

## Closed-Loop Autonomous Policy

Codex must not terminate a mission merely because it produced a next
recommendation. If the recommendation is safe, internal to the repo and
governance-allowed, Codex must execute it automatically.

Automatically executable recommendations include:

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
- `FREEZE_LINE` only after all freeze criteria are satisfied.

Stop only for external artifacts/data, credentials or paid APIs, access outside
the repo, merge, official promotion, paper readiness, specification change, real
capital or non-technical business risk acceptance.

## Next Gate Chain Execution

If `reports/state/sniper_next_autonomous_mission.md` defines a safe next gate,
Codex must execute it automatically. The file is executable mission context when
it contains:

- `Current next gate`;
- `Next Mission`;
- `Required tests`;
- `Criteria`;
- `Stop Conditions`.

`Pode continuar autonomamente: sim` is not a stop condition.
`Próximo gate recomendado` is not a stop condition.

A live research-only candidate with an initial `PASS/advance` must go through:

1. stability/falsification gate;
2. candidate decision gate;
3. state update;
4. draft PR update when reviewable.

Codex may stop with a live candidate only if the next gate needs external
artifacts, credentials/API/access, operation outside the repo, a specification
change, official promotion, paper readiness, merge, real capital, budget beyond
the allowed mission, or changes too large for reasonable review.

## Stop Required

Codex must stop before:

- merging any PR;
- promoting research to official;
- declaring paper readiness;
- operating real capital;
- creating or storing credentials;
- using paid/private APIs or private data not already available;
- changing the specification;
- relaxing quantitative thresholds;
- reopening A3/A4;
- fabricating artifacts;
- removing a quantitative blocker by narrative;
- treating zero-exposure CVaR as economic robustness;
- treating `ALIVE_BUT_NOT_PROMOTABLE` as promotable.
- using an external resource not already available in the repo.

## Research-Only Failure Policy

A failed research-only hypothesis does not automatically require a human
decision when there is still a materially different defensible hypothesis inside
the repo. Codex must:

- emit `FAIL/abandon`;
- update `reports/state/sniper_decision_ledger.md`;
- update `reports/state/sniper_spec_gap_backlog.yaml`;
- mark the hypothesis falsified;
- feed the result into the next hypothesis selection;
- consume exploration budget.
- continue until the full phase budget is exhausted or a real stop condition is
  reached.

Codex must not repeat the same thesis with another name, use realized variables
as ex-ante rules, treat diagnostics as operational signals, or use a failed
research gate as promotion evidence.

## Exploration Budget

Closed-loop autonomous budget per mission:

- up to 25 total gates;
- up to 5 materially different hypothesis families;
- up to 4 gates per family;
- up to 2 intermediate global audits;
- up to 2 draft PR updates;
- stop when the amount of change is too large for reasonable human review.

Default budget per autonomous mission:

- up to 15 research-only gates;
- up to 3 materially different hypothesis families;
- up to 3 gates per family before abandoning that family;
- up to 2 correction attempts per `PARTIAL/correct` gate;
- up to 1 intermediate global audit when needed;
- up to 1 draft PR update at the end of the mission.

Freeze is permitted only after:

- at least 2 materially different families were tested;
- explicit DSR diagnostics exist;
- research CVaR with nonzero exposure was evaluated when research exposure is
  available;
- comparison/falsification across families was recorded;
- `reports/state/sniper_decision_ledger.md` was updated.
- any surviving research-only candidate was audited/falsified.
- the last candidate was falsified;
- a post-falsification global reaudit was executed;
- `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION` was executed after the latest
  falsification;
- no HIGH/MEDIUM priority agenda hypothesis remains executable inside the repo.

## Research Agenda Expansion Before Final Freeze

The current mission reached `FULL_FREEZE_AFTER_REAUDIT` after falsifying
`cluster_conditioned_polarity`. That state is not accepted as final permanent
freeze until Codex runs an agenda expansion from specification, code, gates,
falsifications, blockers and existing modules.

Next safe action: `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`.
Next logical skill: `sniper-autonomous-research-agenda-synthesizer`.
Next mode: `GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`.

The agenda expansion must create or update:

- `reports/state/sniper_research_agenda.yaml`
- `reports/state/sniper_hypothesis_inventory.md`
- `reports/state/sniper_next_autonomous_mission.md`

If the agenda produces at least one HIGH or MEDIUM priority hypothesis that is
executable inside the repo, Codex must select the highest expected-value
hypothesis, open a research-only gate, implement only in research/sandbox,
validate, falsify or preserve, and continue closed-loop execution.

If the agenda produces no materially new HIGH/MEDIUM executable hypothesis,
Codex must classify the line as
`FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED`, update
`reports/state/**`, update the existing draft PR when reviewable, and stop.

Human decision is not required merely because the previous backlog ended.
External-resource hypotheses must be classified as `EXTERNAL_RESOURCE_REQUIRED`
or `REQUEST_EXTERNAL_ARTIFACTS`. Specification-change hypotheses must be
classified as `SPEC_CHANGE_REQUIRED`.

Human decision is required only for external resources, specification changes,
official promotion, paper readiness, credentials or paid APIs, operation outside
the repo, merge, or real capital.

## Modes

Allowed modes:

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
- `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION`
- `GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`
- `FREEZE_LINE` only after the full freeze requirements are satisfied.
- `STOP_FOR_HUMAN_DECISION` only for the explicit external/governance cases
  listed above.

Forbidden modes:

- `OFFICIAL_PROMOTION`
- `PAPER_READINESS`
- `A3_REOPEN`
- `A4_REOPEN`
- `THRESHOLD_RELAXATION`
- `REAL_TRADING`

## Current Governance Locks

- A3 is closed as structural choke.
- A4 remains closed unless strong new evidence appears.
- RiskLabAI remains oracle/shadow.
- Fast path remains official.
- Cross-sectional remains `ALIVE_BUT_NOT_PROMOTABLE`.
- `dsr_honest=0.0` blocks promotion.
- `PASS_ZERO_EXPOSURE` is technical persistence only.
- Research does not become official without explicit gate.

## Required State Reads

Before any autonomous cycle, Codex must read:

- `AGENTS.md`
- `docs/SNIPER_AUTONOMOUS_OPERATING_CONTRACT.md`
- `reports/state/sniper_current_state.json`
- `reports/state/sniper_spec_gap_backlog.yaml`
- `reports/state/sniper_decision_ledger.md`
- `reports/state/sniper_artifact_registry.json`
- `reports/state/sniper_autonomous_runbook.md`

If these files are absent, the first safe gate is operating-memory bootstrap,
not quantitative promotion work.
