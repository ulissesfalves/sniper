# SNIPER Global Implementation Checklist

Audit date: 2026-04-27

Branch: `codex/autonomous-sniper-implementation`

Verdict: `GLOBAL_PARTIAL`

This checklist reflects the PR draft branch after the Phase 6 autonomous closure. It is a review checklist, not a promotion checklist.

## Phase 0 - Foundation, Repo, Environment, Reproducibility

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| Work on isolated PR branch | Done | branch `codex/autonomous-sniper-implementation`; PR #1 draft | none | low | high | Keep PR draft. |
| Preserve base branch | Done | base `codex/openclaw-sniper-handoff` | none | low | high | Do not merge automatically. |
| Gate pack convention | Done | Phase6 gates include report, manifest, metrics where produced | none | low | high | Continue gate pack format. |
| Clean regeneration | Done for Phase6 | latest gate has `clean_clone_or_equivalent=true`, returncode 0, Phase5 clean status PASS | no promotion from this evidence | medium | high | Review clean regeneration report. |
| Environment tests | Done for focused subset | `26 passed` reported for Phase5/Phase6 subset | full CI still not equivalent to readiness | medium | medium | Rerun focused tests during review if needed. |
| Worktree hygiene | Done after stabilization | stale autonomous stop-review scratch files were triaged as superseded; intended audit/gate artifacts are committed separately | no remaining untracked audit scratch files expected after commits | low | medium | Keep future gate artifacts committed or explicitly discarded before new missions. |

## Phase 1 - Data, Universe Point-In-Time, Anti-Survivorship

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| Phase4 official artifacts available for audit | Done for this clone | latest Phase6 artifact integrity report hashes required files | artifacts are ignored/external, not versioned official outputs | medium | high | Keep hashes in gate packs. |
| Research baseline artifacts available | Done for this clone | clean regeneration preflight reports no missing baseline artifacts | artifacts remain external/ignored | medium | high | Verify paths before any rerun. |
| Cemetery/PIT unlock handling | Partial | unlock rev5 docs and data_inserter source preserve PIT/reconstruction rules | live coverage and provider quality remain ongoing concerns | medium | medium | Keep data quality diagnostics before model promotion. |
| Anti-survivorship controls | Partial | validators and handoff docs exist | not revalidated by this PR-specific audit | medium | medium | Revalidate only in a future data gate if needed. |

## Phase 2 - Feature Store And Feature Engineering

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| `unlock_pressure_rank` rev5 architecture | Satisfactory | `docs/unlock_pressure_rank_technical.md`; store/token unlock code | live observed coverage can still be shadow-limited | medium | medium | Keep audit fields outside X. |
| Fracdiff log-space | Satisfactory | `services/ml_engine/fracdiff/transform.py`; `optimal_d.py` | no new issue in this PR | low | medium | Preserve log-space tests. |
| Volatility and market features | Partial | `services/ml_engine/features/volatility.py` | not rerun end-to-end in this audit | medium | medium | Require full model gate for readiness. |
| Feature diagnostics | Partial | `services/ml_engine/phase2_diagnostic.py` | latest quality snapshot not reviewed here | medium | low | Review in future data/feature gate. |

## Phase 3 - Regime, Labels, Meta-Labeling, Calibration

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| HMM/regime gating | Partial | `tests/unit/test_hmm_regime_alignment.py`; drift and pre-trade hooks | full regime retraining not rerun here | medium | medium | Keep as implemented, not readiness evidence. |
| Triple-barrier labels | Partial | meta-labeling and uniqueness modules reference triple-barrier outputs | current PR did not rerun label pipeline | medium | medium | Require full model gate before promotion. |
| CPCV | Satisfactory as implementation | `services/ml_engine/meta_labeling/cpcv.py`; Phase4 report checks | DSR still fails | high | high | Do not override DSR with CPCV/PBO. |
| Calibration/ECE | Satisfactory as artifact check | Phase4 artifact report has ECE check true | not sufficient for promotion | medium | medium | Keep as supporting evidence only. |

## Phase 4 - CPCV, Statistical Gates, Decision-Space

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| PBO check | Done | Phase4 artifact report has `CPCV PBO < 10% [9]` true | none as isolated check | medium | medium | Preserve in report. |
| N_eff check | Done | Phase4 artifact report has `N_eff >= 120 [17]` true | none as isolated check | medium | medium | Preserve in report. |
| Honest DSR | Failed merit | `dsr_honest=0.0`, `dsr_passed=false` | promotion blocked | critical | critical | Do not promote; decide freeze or research-only thesis. |
| Decision-space ruler | Partial/research-only | handoff says family alive/latest/headroom | cannot become promotable while DSR zero | high | high | Keep as veto/diagnostic, not promotion. |

## Phase 5 - Quantitative Hardening

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| Correct sovereign baseline restored | Done | clean Phase5 restore `EXACT_RESTORE`; `SOVEREIGN_BASELINE_RESTORED_AND_VALID` | no promotion implied | medium | high | Use only as research baseline. |
| Cross-sectional family state | Partial | `ALIVE_BUT_NOT_PROMOTABLE` in handoff and gate review | quantitative merit unresolved | high | high | Freeze or start separate research-only thesis. |
| A3 structural choke | Closed | handoff documents A3 closed | no new evidence to reopen | high | high | Do not reopen A3/A4. |
| Bounded policy tweaks | Exhausted for promotion | handoff says small recent tweaks did not solve DSR | no material advance in current line | high | high | Avoid another generic tweak loop. |

## Phase 6 - Bridge Paper/Nautilus And Continuous Execution

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| Nautilus bridge source exists | Partial | `services/nautilus_bridge/**` | not readiness because quantitative blockers remain | high | medium | Review only as source/governance. |
| FULL_SNAPSHOT contract | Satisfactory as source | `services/nautilus_bridge/contract.py` | runtime gate not executed here | medium | medium | Keep contract tests. |
| Stale/duplicate/out-of-order handling | Satisfactory as source | `services/nautilus_bridge/acceptance.py` statuses | runtime paper gate not run here | medium | medium | Validate in later paper gate if blockers clear. |
| Redis status/consumer path | Partial | `services/nautilus_bridge/consumer.py`; execution Redis usage | live Redis not exercised in this audit | medium | low | Do not treat as paper readiness. |
| CVaR persistence | Partial | latest `portfolio_cvar_report.json` persisted | zero exposure means no economic robustness | critical | critical | Require nonzero exposure or approved stress evidence before readiness. |

## Phase 7 - Prolonged Paper/Testnet

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| Start prolonged paper/testnet | Not implemented / blocked | no valid readiness gate | DSR and CVaR blockers | critical | critical | Do not start readiness from this PR. |
| Paper metrics over time | Not implemented / blocked | no prolonged paper evidence in this PR | needs prior Phase4/5/6 gates to pass | critical | high | Only after merit blockers clear. |
| Continuous daemon acceptance | Partial source only | bridge daemon source exists | runtime evidence missing and blocked | high | medium | Keep out of promotion review. |

## Phase 8 - Real Capital Readiness

| Item | Status | Evidence | Pending | Risk | Priority | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| Real capital operation | Not applicable for this branch | governance says no real capital; no orders/credentials used | all gates would need to pass first | critical | critical | Do not operate capital. |
| Credential creation/storage | Not applicable | no new credentials reported | none | critical | critical | Do not create or store secrets. |
| Merge/readiness approval | Blocked | PR is draft and not merged | human review only | critical | high | Keep PR draft. |

## What Is Satisfactory

- Clean regeneration proof is now present and usable for review.
- Required Phase4 and research baseline artifacts were found and hashed.
- Source-doc alignment is restored to `ALIGNED`.
- Focused Phase5/Phase6 tests passed.
- Governance boundaries were preserved.

## What Was Done But Failed On Merit

- The cross-sectional family remains alive in latest/headroom terms, but it fails promotion because honest DSR is `0.0`.
- CVaR persistence exists, but official exposure is zero and cannot prove economic robustness.

## What Is Partial Or Inconclusive

- Paper/Nautilus bridge source exists, but paper readiness is not proven.
- HMM/regime and label/calibration code exists, but this audit did not rerun a full modeling gate.
- Data/models artifacts are available in this clone but remain ignored/external base artifacts.

## What Blocks Operational Advance

- `dsr_honest=0.0`
- `dsr_passed=false`
- `cvar_economic_status=NOT_PROVEN_ZERO_EXPOSURE`
- cross-sectional `ALIVE_BUT_NOT_PROMOTABLE`
- no Phase7 paper/readiness gate can honestly pass from this state.

## What Is Research Only

- `phase5_cross_sectional_sovereign_closure_restored`
- copied research baseline artifacts in `data/models/research/**`
- any future attempt to repair DSR/CVaR via a new hypothesis.

## Recommended Next Action

Keep PR #1 as draft and review it as a governance/reproducibility PR. Do not merge as ready. Do not promote official. If further work is needed, it should be either:

- documentation-only PR review corrections; or
- a separate research-only thesis with explicit falsification criteria and no official promotion.
