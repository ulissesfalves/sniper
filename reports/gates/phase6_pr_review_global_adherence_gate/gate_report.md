# Gate Report - phase6_pr_review_global_adherence_gate

## Verdict

Status: `PARTIAL`

Decision: `correct`

Review result: `DRAFT_PR_GOVERNANCE_REVIEW_VALIDATED_WITH_BLOCKERS`

PR #1 remains valid as a draft governance/reproducibility review artifact. It is not operational readiness, paper readiness, merge approval, or official promotion.

## PR State

- PR: `https://github.com/ulissesfalves/sniper/pull/1`
- State: `open`
- Draft: `True`
- Merged: `False`
- Base: `codex/openclaw-sniper-handoff`
- Head: `codex/autonomous-sniper-implementation`
- Head SHA: `a85b543c5b68777b342446a3a5d9d3ff6e292979`

## Confirmations

| Check | Result |
| --- | --- |
| PR remains draft and unmerged | PASS |
| Local branch matches PR head | PASS |
| Base remains `codex/openclaw-sniper-handoff` | PASS |
| No official promotion | PASS |
| A3/A4 not reopened | PASS |
| RiskLabAI remains oracle/shadow | PASS |
| Clean regeneration proven | PASS |
| Phase4 official artifacts found and hashed | PASS |
| Research baseline artifacts found and hashed | PASS |
| Honest DSR remains 0.0 and blocks promotion | CONFIRMED BLOCKER |
| Official CVaR remains zero exposure and not economic robustness | CONFIRMED BLOCKER |
| Cross-sectional remains ALIVE_BUT_NOT_PROMOTABLE | CONFIRMED BLOCKER |

## Evidence

- Latest clean regeneration gate: `reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/gate_report.json`
- Clean regeneration: `clean_clone_or_equivalent=true`, `returncode=0`, Phase5 clean gate `PASS/advance`.
- Phase4 artifacts: `artifact_integrity_status=PASS`, `missing_required_artifacts=[]`.
- Research baseline preflight: `missing_regeneration_baseline_artifacts=[]`.
- DSR: `dsr_honest=0.0`, `dsr_passed=false`, `promotion_status=BLOCKED_DSR_HONEST_ZERO`.
- CVaR: `positions={}`, `n_positions=0`, `total_exposure_pct=0.0`, `economic_robustness_status=NOT_PROVEN_ZERO_EXPOSURE`.
- Global audit verdict: `GLOBAL_PARTIAL`.

## Drift Since Previous Local Gate Report

- `?? reports/audits/global_spec_adherence/next_step_codex_prompt.md`
- `?? reports/gates/phase6_pr_review_global_adherence_gate/`

## Stabilization Triage

- `reports/audits/autonomous_stop_review/autonomous_stop_review.json`: `DESCARTAR`, superseded by later Phase6 clean-regeneration evidence.
- `reports/audits/autonomous_stop_review/autonomous_stop_review.md`: `DESCARTAR`, superseded by later Phase6 clean-regeneration evidence.
- `reports/audits/autonomous_stop_review/next_action_prompt.md`: `DESCARTAR`, superseded by later Phase6 clean-regeneration evidence.

## Metrics

| Metric | Value | Threshold | Status |
| --- | --- | --- | --- |
| `pr_is_open` | `True` | `True` | `PASS` |
| `pr_is_draft` | `True` | `True` | `PASS` |
| `pr_is_not_merged` | `True` | `True` | `PASS` |
| `branch_matches_pr_head` | `True` | `True` | `PASS` |
| `base_branch_confirmed` | `codex/openclaw-sniper-handoff` | `codex/openclaw-sniper-handoff` | `PASS` |
| `no_official_promotion` | `True` | `True` | `PASS` |
| `a3_a4_not_reopened` | `True` | `True` | `PASS` |
| `risklabai_shadow_oracle_only` | `True` | `True` | `PASS` |
| `clean_regeneration_proven` | `True` | `True` | `PASS` |
| `phase5_clean_gate_status` | `PASS` | `PASS` | `PASS` |
| `phase4_artifact_integrity` | `PASS` | `PASS` | `PASS` |
| `research_baseline_missing_artifacts` | `0` | `0` | `PASS` |
| `dsr_honest` | `0.0` | `> 0.95 for promotion` | `FAIL` |
| `dsr_passed` | `False` | `True` | `FAIL` |
| `cvar_zero_exposure` | `True` | `False` | `INCONCLUSIVE` |
| `cvar_economic_robustness` | `NOT_PROVEN_ZERO_EXPOSURE` | `MEASURED_NONZERO_EXPOSURE_OR_APPROVED_STRESS` | `INCONCLUSIVE` |
| `cross_sectional_promotability` | `ALIVE_BUT_NOT_PROMOTABLE` | `PROMOTABLE for official` | `FAIL` |

## Blockers

- `dsr_honest_zero_blocks_promotion`
- `cvar_zero_exposure_not_economic_robustness`
- `cross_sectional_alive_but_not_promotable`

## Governance Position

The PR should remain draft. This gate does not approve merge, paper readiness, operational readiness, official promotion, A3/A4 reopening, or any real-capital action.

## Recommendation

Keep PR #1 open as draft for human review of governance and reproducibility evidence. Any next change should be documentation/audit only unless the user explicitly starts a separate research-only thesis with falsification criteria.
