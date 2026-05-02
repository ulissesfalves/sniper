# SNIPER Decision Ledger

Updated: 2026-05-02T14:11:27Z

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
| Autonomous research-only continuation | Authorized | A failed research-only thesis does not require human decision by itself. Continue through materially new backlog hypotheses within the exploration budget and governance limits. |
| Research sandbox nonzero-exposure CVaR evaluator | Partial research diagnostic | `phase5_research_sandbox_nonzero_exposure_cvar_evaluation_gate` measured nonzero research exposure with max CVaR95 loss fraction `0.01072844`, but the safe policy still had median combo Sharpe `-0.727203`, official exposure remained zero, and DSR stayed blocked. |
| DSR zero root cause diagnostic | Complete | `phase5_research_dsr_zero_diagnostic_gate` confirmed `dsr_honest=0.0`, `sharpe_is=0.8808`, `sr_needed=4.47`, gap `3.5892`, and best observed diagnostic DSR `0.6938`; no threshold relaxation or promotion is allowed. |
| Rank-score threshold family | Partial research-only | `phase5_research_rank_score_threshold_sizing_falsification_gate` found weak median alpha for `top1_score_ge_0_50` (`0.331124`) with sandbox CVaR within limit, but min combo Sharpe `-3.357339` and DSR blockers prevent promotion. |
| Rank-score threshold stability correction | Abandoned | `phase5_research_rank_score_stability_correction_gate` consumed the single PARTIAL correction. Best correction `score_0_60` improved min Sharpe to `-2.553324` but reduced median Sharpe to `0.195832`; instability and DSR gap remain. |
| Current research hypothesis space | Frozen | `phase5_research_hypothesis_space_freeze_gate` considered five gates, found zero promotable candidates, and froze the current autonomous line. Surviving modules are CVaR evaluation and DSR diagnostics only. |
| Autonomous full phase execution policy | Authorized | Future autonomous missions may run up to 15 research-only gates, 3 materially different hypothesis families, 3 gates per family, 2 corrections per PARTIAL gate, 1 intermediate global audit and 1 final PR draft update. FAIL/abandon does not stop the mission while materially different defensible hypotheses remain. |

## Current Decision

The next safe mode is `CONTINUE_AUTONOMOUS` under the
`AUTONOMOUS FULL PHASE EXECUTION POLICY`, constrained to research/sandbox work
inside the repo. The previous freeze remains historical evidence for the
Stage A/rank_score line, but future freeze decisions require at least 2
materially different families, explicit DSR diagnostics, research CVaR with
nonzero exposure when applicable, family comparison/falsification, and this
ledger updated. Do not promote, merge, declare paper readiness, reopen A3/A4 or
relax thresholds.

## Required Review Before Promotion

Any future promotion attempt must first prove:

- honest DSR passes without relaxed thresholds;
- nonzero-exposure CVaR or approved stress evidence exists;
- research artifacts have an explicit promotion gate;
- A3/A4 remain closed unless strong new evidence is documented;
- human review accepts the relevant PR.
