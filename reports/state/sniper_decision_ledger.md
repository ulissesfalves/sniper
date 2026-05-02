# SNIPER Decision Ledger

Updated: 2026-05-02T10:42:42Z

## Closed Decisions

| Decision | Status | Evidence |
| --- | --- | --- |
| A3 structural choke | Closed | Historical handoff and Phase5 gates. Do not reopen without strong new evidence. |
| A4 reopen | Closed by default | No new strong evidence in current branch. |
| RiskLabAI | Oracle/shadow only | Global audit and PR review gate. Never official in this branch. |
| Fast path | Remains official | Global governance context. |
| Cross-sectional family | `ALIVE_BUT_NOT_PROMOTABLE` | Phase5/Phase6 gate summaries and global audit. |
| Phase6 clean regeneration | Proven | `phase6_research_baseline_rehydration_clean_regeneration_gate` with clean clone/equivalent and Phase5 clean status PASS. |
| PR draft | Open draft, not merged | PR #1 `https://github.com/ulissesfalves/sniper/pull/1`, head `68087363d6d5a4d2343c62a8b27d9636b0f74b36`. |
| DSR blocker | Active | `dsr_honest=0.0`, `dsr_passed=false`; no promotion allowed. |
| CVaR blocker | Active | `PASS_ZERO_EXPOSURE`; economic robustness not proven. |
| Operating memory | Bootstrapped | `phase6_operating_memory_bootstrap_gate` created persistent memory files. |
| Stage A nonzero-exposure thesis | Abandoned | `phase5_research_only_stage_a_nonzero_exposure_falsification_gate` failed because safe ex-ante top1 had median combo Sharpe `-0.727203`; high Sharpe required realized `stage_a_eligible` and is diagnostic only. |

## Current Decision

The next safe mode is `FREEZE_LINE` or `STOP_FOR_HUMAN_DECISION`. A new
research-only thesis should require a materially different hypothesis; do not
iterate the same Stage A nonzero-exposure blocker.

## Required Review Before Promotion

Any future promotion attempt must first prove:

- honest DSR passes without relaxed thresholds;
- nonzero-exposure CVaR or approved stress evidence exists;
- research artifacts have an explicit promotion gate;
- A3/A4 remain closed unless strong new evidence is documented;
- human review accepts the relevant PR.
