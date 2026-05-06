# SNIPER Decision Ledger

Updated: 2026-05-04T02:36:05Z

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
| Deep quantitative diagnostic | Complete | `phase5_research_deep_quant_diagnostic_gate` passed as diagnostic only: `dsr_honest=0.0`, `sharpe_is=0.8808`, `sr_needed=4.47`, gap `3.5892`, median research Sharpe `-0.727203`, and `positive_and_stable_policy_count=0`. |
| Alternative p_bma/sigma/hmm family | Abandoned | `phase5_research_alternative_exante_family_gate` failed/abandoned after testing volatility-targeted, risk-budgeted, defensive ensemble and uncertainty-abstention long-only research policies; best policy `ensemble_top3` had median combo Sharpe `-0.632456`. |
| Signal-polarity research family | Partial then corrected | `phase5_research_signal_polarity_long_short_gate` found positive median alpha for `short_high_p_bma_k3` (`1.768215`) but required correction because min combo Sharpe was `-0.501947`. |
| Signal-polarity stability correction | Research-only survivor | `phase5_research_signal_polarity_stability_correction_gate` passed/advanced as sandbox research only: `short_high_p_bma_k3_p60_h70` had median combo Sharpe `1.361592`, min combo Sharpe `0.261111`, median active days `471.0`, and max CVaR95 `0.00344841`. It is not official and still below the DSR promotion bar. |
| Full phase family comparison | Research-only survivor selected | `phase5_research_full_phase_family_comparison_gate` compared six families/gates, abandoned Stage A/rank-score/alternative long-only lines, and preserved `short_high_p_bma_k3_p60_h70` as the only surviving research candidate. No official promotion, paper readiness, A3/A4 reopening or threshold relaxation occurred. |
| Human decision last resort | Active governance policy | Codex must use internal strategic decision rubrics before stopping for human decision. Human decision is allowed only for external artifacts/data, credentials/API/access, operation outside repo, merge, official promotion, paper readiness, specification change, real capital, or non-technical product/business risk acceptance. |
| Candidate global reaudit | Passed, research-only | `phase5_research_candidate_global_reaudit_gate` recomputed `short_high_p_bma_k3_p60_h70`, confirmed ex-ante selection uses `p_bma_pkf`/`hmm_prob_bull`, no realized variable drives selection, no official promotion occurred, and DSR/CVaR blockers remain. |
| Candidate stability | Partial/correct | `phase5_research_candidate_stability_gate` ran 49 scenarios and found 29 failures, including temporal thirds, parameter sensitivity, 20 bps cost stress and low-HMM regime fragility. |
| Candidate falsification | Failed/abandoned | `phase5_research_candidate_falsification_gate` found hard falsifiers: `temporal_subperiod_min_sharpe=-1.160839` and `extra_cost_20bps_min_sharpe=-0.12201`; leakage control passed. |
| Candidate decision | Falsified | `phase5_research_candidate_decision_gate` classified `short_high_p_bma_k3_p60_h70` as `RESEARCH_CANDIDATE_FALSIFIED` and `PASS/abandon`. It remains research/sandbox only and cannot support promotion, readiness or official short exposure. |
| Closed-loop autonomous execution | Authorized | `CLOSED_LOOP_AUTONOMOUS_EXECUTION_POLICY` authorizes Codex to execute safe technical next recommendations automatically inside the repo. `RUN_GLOBAL_REAUDIT`, `START_RESEARCH_ONLY_THESIS`, state updates, draft PR updates and governed freeze review are not valid human-decision stops when safe. |
| Post-candidate falsification global reaudit | Passed | `phase5_post_candidate_falsification_global_reaudit_gate` confirmed `short_high_p_bma_k3_p60_h70` was falsified and selected `cluster_conditioned_polarity` as a safe material in-repo research thesis. |
| Cluster-conditioned polarity family | Candidate found | `phase5_research_cluster_conditioned_polarity_gate` tested 24 policies and found `cluster_2_long_high_short_low_p60_h70_k3` with median Sharpe `1.183459`, min Sharpe `0.078586`, median active days `425.0`, and max CVaR95 `0.00151815`; research/sandbox only. |
| Cluster-conditioned polarity falsification | Failed/abandoned | `phase5_research_cluster_conditioned_polarity_falsification_gate` found 13 hard falsifiers, including temporal thirds, 5/10/20 bps cost stresses, parameter variants and universe stress. |
| Cluster-conditioned polarity decision | Falsified | `phase5_research_cluster_conditioned_polarity_decision_gate` classified `cluster_2_long_high_short_low_p60_h70_k3` as `CLUSTER_CONDITIONED_RESEARCH_CANDIDATE_FALSIFIED`; no promotion or readiness. |
| Closed-loop governed freeze | Frozen | `phase5_post_candidate_falsification_governed_freeze_gate` passed with classification `FULL_FREEZE_AFTER_REAUDIT` after 5 material families, DSR diagnostic, research CVaR, family comparison and no surviving research candidate. |
| Research agenda expansion before final freeze | Required | `FULL_FREEZE_AFTER_REAUDIT` is not accepted as permanent final freeze until `AUTONOMOUS_RESEARCH_AGENDA_EXPANSION` runs via `sniper-autonomous-research-agenda-synthesizer`. Next mode is `GENERATE_NEW_RESEARCH_AGENDA_FROM_SPEC`; human decision is not required. |
| Research agenda expansion | Completed with candidate | `reports/state/sniper_research_agenda.yaml`, `sniper_hypothesis_inventory.md` and `sniper_next_autonomous_mission.md` were generated with six new research-only hypotheses. AGENDA-H01 was selected. |
| Meta calibration disagreement abstention | Research candidate alive, not promotable | `phase5_research_meta_disagreement_abstention_gate` passed/advanced. Best policy `short_bma_high_meta_low_p60_m40_k3` had median Sharpe `0.855486`, min Sharpe `0.220622`, median active days `322.0`, max CVaR95 `0.00455141`; research/sandbox only, below `sr_needed=4.47`, no official promotion. |
| Next gate chain execution | Active governance policy | If `reports/state/sniper_next_autonomous_mission.md` defines a safe next gate, Codex must execute it automatically. `Pode continuar autonomamente: sim` and `Próximo gate recomendado` are not stop conditions. The current live candidate `short_bma_high_meta_low_p60_m40_k3` requires `phase5_research_meta_disagreement_stability_falsification_gate` and then a decision gate; no promotion or paper readiness is allowed. |
| Meta disagreement stability/falsification | Failed/abandoned | `phase5_research_meta_disagreement_stability_falsification_gate` executed 38 scenarios and found 25 hard falsifiers, including all temporal thirds, 20 bps cost stress, parameter sensitivity failures and universe stress. Leakage control passed. |
| Meta disagreement candidate decision | Falsified | `phase5_research_meta_disagreement_candidate_decision_gate` classified `short_bma_high_meta_low_p60_m40_k3` as `META_DISAGREEMENT_RESEARCH_CANDIDATE_FALSIFIED`. Next agenda hypothesis is `AGENDA-H02` / `phase5_research_meta_uncertainty_abstention_gate`. |
| Meta uncertainty abstention long-only | Failed/abandoned | `phase5_research_meta_uncertainty_abstention_gate` tested AGENDA-H02 and classified it as `META_UNCERTAINTY_FALSIFIED_BY_STABILITY_STRESS`. Best policy `long_bma_meta_agree_p65_m50_s10_k3` had median Sharpe `0.447334`, min Sharpe `-0.375889`, median active days `55.0`, max CVaR95 `0.00284763`, and 19 hard falsifiers. Next agenda hypothesis is `AGENDA-H03` / `phase5_research_cvar_constrained_meta_sizing_gate`. |
| Autonomous checkpoint continuation | Active governance policy | Large reviewable packages are checkpoints, not final stops, when validation passes, the branch can be clean, the PR draft can be updated and `reports/state/sniper_next_autonomous_mission.md` defines a safe next gate. Classification `CHECKPOINT_CONTINUE_AUTONOMOUS` is allowed. `FUNCTIONAL_RESEARCH_MODULE_DELIVERED` is not terminal while `phase5_research_cvar_constrained_meta_sizing_gate` remains safe and executable. |
| CVaR-constrained meta sizing | Partial/correct | `phase5_research_cvar_constrained_meta_sizing_gate` tested AGENDA-H03 and classified `signed_meta_edge_t52_s15_k5_g04` as `CVAR_CONSTRAINED_META_SIZING_CVAR_PASS_ALPHA_UNSTABLE`. The runner produced nonzero research/sandbox exposure and max CVaR95 `0.00356911`, with median Sharpe `2.040444`, but min Sharpe `-0.903026` and 20 hard falsifiers block preservation. Next agenda hypothesis is `AGENDA-H04` / `phase5_research_regime_specific_meta_disagreement_gate`. |
| Regime-specific meta disagreement | Partial/correct | `phase5_research_regime_specific_meta_disagreement_gate` tested AGENDA-H04 and classified `neutral_short_meta_low_m40_k3` as `REGIME_SPECIFIC_META_DISAGREEMENT_POSITIVE_BUT_UNSTABLE`. The runner produced nonzero research/sandbox exposure and max CVaR95 `0.00315145`, with median Sharpe `0.726729`, but min Sharpe `-0.911080`, median active days `82.0` and 13 hard falsifiers block preservation. Next agenda hypothesis is diagnostic H05 / `phase5_research_feature_family_ablation_blocker_decomposition_gate`. |

## Current Decision

The current mission executed the checkpoint continuation gate for the next
materially different agenda hypothesis `AGENDA-H04`.

Current classification:
`REGIME_SPECIFIC_META_DISAGREEMENT_POSITIVE_BUT_UNSTABLE`.

Human decision is not required. `phase5_research_regime_specific_meta_disagreement_gate`
was executed as research/sandbox, used only ex-ante HMM and probability inputs,
and kept the line at PARTIAL/correct because the best policy has negative min
Sharpe, sparse active days and 13 hard falsifiers despite nonzero exposure and
research CVaR within bound. Do not promote, merge, declare paper readiness,
reopen A3/A4, relax thresholds, or treat research CVaR as official.

The next materially different agenda hypothesis is diagnostic `AGENDA-H05`
(`phase5_research_feature_family_ablation_blocker_decomposition_gate`) if continuing within the
autonomous budget and review scope remains safe.

Large reviewable output from the prior round is now a governed checkpoint, not a
final stop. Current checkpoint classification:
`CHECKPOINT_CONTINUE_AUTONOMOUS`.

The next recommended mode is `FEATURE_FAMILY_ABLATION_BLOCKER_DECOMPOSITION_GATE`; autonomous can
continue, human decision is not required, and official promotion/paper readiness
remain forbidden.

## Required Review Before Promotion

Any future promotion attempt must first prove:

- honest DSR passes without relaxed thresholds;
- nonzero-exposure CVaR or approved stress evidence exists;
- research artifacts have an explicit promotion gate;
- A3/A4 remain closed unless strong new evidence is documented;
- human review accepts the relevant PR.
