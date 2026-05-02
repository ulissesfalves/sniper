# SNIPER Autonomous Operating Contract

This contract defines what Codex can do autonomously inside the SNIPER repo.

## Autonomous Actions

Codex may act without an additional user prompt when the action remains inside
the current repository and preserves governance:

- create a branch with the `codex/` prefix when needed;
- choose the next open gap from the backlog;
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
- update the existing draft PR branch when review-ready;
- abandon a failed research hypothesis;
- update `reports/state/**`.

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
decision. Codex must:

- emit `FAIL/abandon`;
- update `reports/state/sniper_decision_ledger.md`;
- update `reports/state/sniper_spec_gap_backlog.yaml`;
- mark the hypothesis falsified;
- feed the result into the next hypothesis selection;
- consume exploration budget.

Codex must not repeat the same thesis with another name, use realized variables
as ex-ante rules, treat diagnostics as operational signals, or use a failed
research gate as promotion evidence.

## Exploration Budget

Default budget per autonomous mission:

- up to 5 research-only gates;
- up to 2 consecutive failures in the same hypothesis type;
- up to 1 correction attempt per `PARTIAL/correct` gate;
- stop if the next step requires an external resource or specification change.

## Modes

Allowed modes:

- `START_RESEARCH_ONLY_THESIS`
- `CONTINUE_AUTONOMOUS`
- `RUN_GLOBAL_REAUDIT`
- `FREEZE_LINE` only after budget exhaustion or no materially new hypothesis.

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
