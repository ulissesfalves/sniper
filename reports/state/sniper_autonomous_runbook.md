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

`START_RESEARCH_ONLY_THESIS`

The first research-only thesis should attack at least one active blocker:

- `dsr_honest=0.0`;
- official CVaR zero exposure;
- cross-sectional `ALIVE_BUT_NOT_PROMOTABLE`;
- absence of robust recent operational signal.

## Forbidden Interpretations

- Clean regeneration is reproducibility evidence, not economic merit.
- `PASS_ZERO_EXPOSURE` is not economic CVaR robustness.
- Research baseline is not official.
- PR #1 is not merge/readiness approval.
- Cross-sectional remains not promotable.

## Stop Conditions

Stop if the next step requires:

- human product/strategy decision;
- external artifact or private data not present;
- credential, paid API or real capital;
- merge or ready PR transition;
- specification change;
- promotion while DSR remains 0.0;
- paper readiness while CVaR remains zero exposure;
- A3/A4 reopening without strong new evidence.
