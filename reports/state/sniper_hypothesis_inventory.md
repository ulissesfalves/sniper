# SNIPER Research Hypothesis Inventory

Updated: 2026-05-03T21:20:00Z

## Falsified Or Frozen Families

| Family | Status | Evidence |
| --- | --- | --- |
| Stage A safe top1 | Abandoned | `phase5_research_only_stage_a_nonzero_exposure_falsification_gate`; safe ex-ante median Sharpe was negative and the strong result required realized `stage_a_eligible`. |
| rank_score_threshold | Abandoned | `phase5_research_rank_score_threshold_sizing_falsification_gate` and stability correction; weak median alpha with negative min Sharpe. |
| alternative_exante_p_bma_sigma_hmm long-only | Abandoned | `phase5_research_alternative_exante_family_gate`; no positive safe median alpha across tested long-only families. |
| signal_polarity_short_high | Falsified | `short_high_p_bma_k3_p60_h70` survived initial correction but failed temporal and 20 bps cost falsification. |
| cluster_conditioned_polarity | Falsified | `cluster_2_long_high_short_low_p60_h70_k3` failed temporal, cost, parameter and universe falsification. |
| governed freeze after reaudit | Pending agenda expansion | `FULL_FREEZE_AFTER_REAUDIT` was reached, but final freeze requires autonomous agenda expansion first. |

## New Agenda

| Id | Hypothesis | Priority | Mode | Can Become Research Candidate | First Gate |
| --- | --- | --- | --- | --- | --- |
| AGENDA-H01 | `meta_calibration_disagreement_abstention` | HIGH | long/short sandbox | yes | `phase5_research_meta_disagreement_abstention_gate` |
| AGENDA-H02 | `meta_uncertainty_abstention_long_only` | MEDIUM | long-only | yes | `phase5_research_meta_uncertainty_abstention_gate` |
| AGENDA-H03 | `cvar_constrained_meta_sizing` | MEDIUM | research sizing | yes | `phase5_research_cvar_constrained_meta_sizing_gate` |
| AGENDA-H04 | `regime_specific_meta_disagreement` | MEDIUM | long/short sandbox | yes | `phase5_research_regime_specific_meta_disagreement_gate` |
| AGENDA-H05 | `feature_family_ablation_blocker_decomposition` | MEDIUM | diagnostic | no | `phase5_research_feature_family_ablation_blocker_decomposition_gate` |
| AGENDA-H06 | `unlock_shadow_feature_ablation` | LOW | diagnostic | no | `phase5_research_unlock_shadow_feature_ablation_gate` |

## Selected For Execution

`AGENDA-H01` was selected because it is executable with artifacts already in the
repo, materially differs from the falsified Stage A/rank/short-high/cluster
families, and can generate nonzero research/sandbox exposure without promoting
official.

Policy constraints:

- use `p_bma_pkf`, `p_meta_calibrated`, `sigma_ewma` and optional
  `hmm_prob_bull` as ex-ante inputs;
- use `pnl_real` only as realized backtest outcome;
- do not use `stage_a_eligible`, `avg_sl_train`, `pnl_real` or any realized
  target as a selection rule;
- short exposure remains research/sandbox only;
- no official promotion, paper readiness, A3/A4 reopening or threshold
  relaxation is allowed.

## Execution Result

`phase5_research_meta_disagreement_abstention_gate` was executed and returned
`PASS/advance`.

| Metric | Value |
| --- | --- |
| Classification | `META_DISAGREEMENT_RESEARCH_CANDIDATE_NOT_PROMOTABLE` |
| Best policy | `short_bma_high_meta_low_p60_m40_k3` |
| Median Sharpe | `0.855486` |
| Min Sharpe | `0.220622` |
| Median active days | `322.0` |
| Max CVaR95 | `0.00455141` |
| Promotion allowed | `false` |
| Paper readiness allowed | `false` |

Next required gate:
`phase5_research_meta_disagreement_stability_falsification_gate`.
