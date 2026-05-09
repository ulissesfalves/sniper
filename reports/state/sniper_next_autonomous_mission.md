# SNIPER Next Autonomous Mission

Mode: `FINAL_FREEZE_RESOURCE_AND_OPPORTUNITY_AUDIT`

Previous gate executed: `phase5_research_feature_family_ablation_blocker_decomposition_gate`

Previous family: `feature_family_ablation_blocker_decomposition`

Previous candidate: `diagnostic_only_no_candidate`

Previous result: `FEATURE_FAMILY_ABLATION_COMPLETE_NO_HIGH_MEDIUM_EXECUTABLE_FAMILY`

Current next gate: `phase5_final_freeze_resource_and_opportunity_audit_gate`

Intermediate classification: `FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED`

Final freeze accepted: `false`

Autonomous can continue: `true`

Human decision required: `false`

## Rationale

The checkpoint continuation mission executed H03, H04 and H05 after the prior
H02 falsification.

- H03 `cvar_constrained_meta_sizing` produced nonzero research/sandbox exposure
  and research CVaR within bound, but stayed `PARTIAL/correct` because min
  Sharpe and sensitivity were unstable.
- H04 `regime_specific_meta_disagreement` produced nonzero research/sandbox
  exposure and research CVaR within bound, but stayed `PARTIAL/correct` because
  exposure was sparse and min Sharpe/sensitivity were unstable.
- H05 `feature_family_ablation_blocker_decomposition` completed diagnostic-only
  decomposition and found no remaining HIGH/MEDIUM executable in-repo research
  family.

H06 `unlock_shadow_feature_ablation` remains LOW priority and requires final
opportunity audit:

- if `data/parquet/unlocks/**` and
  `data/parquet/unlock_diagnostics/unlock_quality_daily.parquet` exist, Codex
  may execute a diagnostic/preflight gate;
- if they are absent, Codex must generate an external resource manifest and must
  not fabricate unlock artifacts or transform shadow artifacts into official
  evidence.

## Next Mission

Execute `phase5_final_freeze_resource_and_opportunity_audit_gate`.

The gate must:

1. Confirm no HIGH/MEDIUM executable in-repo hypothesis remains.
2. Evaluate LOW/preflight options, especially H06.
3. List external artifacts that would unlock future research, with expected
   paths and `Test-Path` verification commands.
4. Check whether a non-promotional internal module remains useful:
   artifact registry, replay/falsification dashboard, report generator,
   validation runner, reproducibility pack, drift/C2ST monitor research-only,
   feature availability audit or data quality gate.
5. Create or update:
   - `reports/state/sniper_external_resource_manifest.md`;
   - `reports/state/sniper_final_freeze_opportunity_audit.md`;
   - `reports/state/sniper_next_material_evidence_request.md`.
6. If a safe internal action exists, execute it through a gate.
7. If no safe internal action exists, classify
   `FULL_FREEZE_AFTER_REAUDIT_AND_OPPORTUNITY_AUDITED`, update the PR draft and
   stop with a clean worktree.

## Stop Conditions Satisfied

- Agenda expansion was executed after freeze.
- H01-H04 were executed and failed, were falsified or remained only partial.
- H05 diagnostic completed.
- No HIGH/MEDIUM executable research-only family remains in the current agenda.
- `FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED` is only intermediate until
  the final opportunity/resource audit is complete.

## Stop Conditions For This Gate

Stop only if:

- the audit confirms a required external artifact/data path;
- no LOW/preflight or non-promotional internal module remains useful;
- continuation would require specification change, official promotion, paper
  readiness, merge, A3/A4 reopening, threshold relaxation, fabricated artifact,
  credential/API, external access or real capital;
- the PR draft and `reports/state/**` are updated and the worktree is clean.

Do not stop merely because the HIGH/MEDIUM agenda is exhausted.

## Restrictions

- No official promotion.
- No paper readiness.
- No A3/A4 reopening.
- No threshold relaxation.
- No fabricated artifacts.
- No realized variable as ex-ante rule.
- No merge.
- No final freeze until `reports/state/sniper_external_resource_manifest.md`,
  `reports/state/sniper_final_freeze_opportunity_audit.md` and
  `reports/state/sniper_next_material_evidence_request.md` exist.
