# SNIPER Autonomous Operating Contract

This contract defines what Codex can do autonomously inside the SNIPER repo.

## Autonomous Actions

Codex may act without an additional user prompt when the action remains inside
the current repository and preserves governance:

- create a branch with the `codex/` prefix when needed;
- create or update a gate pack;
- implement research-only or sandbox corrections;
- repair the local test environment;
- execute tests and validation commands;
- generate reproducible gate evidence;
- commit one coherent gate at a time;
- push the active working branch;
- open or update a draft PR when review-ready;
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
