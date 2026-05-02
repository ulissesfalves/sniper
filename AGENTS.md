# SNIPER Agent Operating Notes

This repository is governed by gate evidence. Codex agents must preserve the
official/research/sandbox split and update `reports/state/**` after each
autonomous mission.

## Sources Of Truth

- Current git branch, commit, status and PR state.
- `docs/**`
- `.agents/skills/**`
- `reports/state/**`
- `reports/audits/**`
- `reports/gates/**`
- `data/models/**`
- `data/models/research/**`
- `data/parquet/**`
- `tests/**`

## Skills

- `sniper-autonomous-implementation-manager`: long controlled implementation
  missions through gates.
- `sniper-operating-memory-maintainer`: create, update and validate persistent
  operating memory.
- `sniper-strategic-decision-governor`: choose the next strategic action.
- `sniper-gate-governance`: any gate, branch, commit, PR, validation or result
  review.
- `sniper-global-spec-adherence-audit`: global audit against specification.
- `sniper-gate-result-reviewer`: review a completed gate.
- `sniper-next-step-prompt-builder`: produce the next safe Codex prompt.
- `sniper-quant-research-implementation`: research-only quantitative work.
- `sniper-paper-execution-hardening`: paper bridge/daemon hardening, only when
  readiness blockers permit it.

## Main Test Commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase6_global_reproducibility_source_alignment_gate.py tests/unit/test_phase5_cross_sectional_latest_headroom_reconciliation_audit.py tests/unit/test_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py tests/unit/test_phase5_cross_sectional_sovereign_hardening_recheck.py tests/unit/test_gate_reports.py tests/unit/test_hmm_regime_alignment.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit/test_gate_reports.py -q
```

## Docker Commands

```powershell
docker compose config
docker compose ps
```

Do not run live trading, store credentials or operate real capital.

## Gate Pack Rules

Every gate must create or update `reports/gates/<gate_slug>/` with:

- `gate_report.json`
- `gate_report.md`
- `gate_manifest.json`
- `gate_metrics.parquet` when tabular metrics exist

Gate reports must state status, decision, branch, commit, artifacts used,
metrics, blockers, risks and objective recommendation.

## Branch And PR Rules

- Work branch: `codex/autonomous-sniper-implementation`.
- Historical base: `codex/openclaw-sniper-handoff`.
- PR #1 is draft governance/reproducibility evidence only.
- Do not merge automatically.
- Do not mark ready unless the user explicitly asks.
- Do not force push.

## Autonomy Limits

Allowed without a new user decision:

- execute autonomous full phase research missions inside this repository;
- create gates;
- choose the next open gap from `reports/state/sniper_spec_gap_backlog.yaml`;
- choose up to 3 materially different research-only hypothesis families per mission;
- create a new falsifiable research-only hypothesis;
- implement in sandbox/research only;
- falsify and abandon a research-only hypothesis;
- choose the next materially different hypothesis;
- implement research/sandbox corrections;
- repair test environment inside the repo;
- run tests;
- generate gate packs;
- commit per gate;
- update the existing draft PR branch;
- update `reports/state/**`;
- push the working branch when a clean gate commit is useful.

Must stop before:

- official promotion;
- paper readiness;
- merge;
- real capital;
- credentials or paid/private APIs;
- specification changes;
- threshold relaxation;
- A3/A4 reopening;
- artifact fabrication;
- external resources not already available in the repo;
- removing DSR/CVaR blockers by narrative.

## Research Exploration Budget

Default budget per autonomous mission:

- up to 15 research-only gates;
- up to 3 materially different hypothesis families;
- up to 3 gates per hypothesis family;
- up to 2 correction attempts per `PARTIAL/correct` gate;
- up to 1 intermediate global audit when needed;
- up to 1 draft PR update at the end of the mission.

A failed research-only hypothesis consumes budget but does not automatically
require a human decision. Record `FAIL/abandon`, update `reports/state/**`, mark
the hypothesis falsified, then choose the next defensible backlog hypothesis.

Freeze is allowed only after at least 2 materially different families were
tested, explicit DSR diagnostics exist, research CVaR with nonzero exposure was
evaluated when research exposure exists, family comparison/falsification was
recorded, and `reports/state/sniper_decision_ledger.md` was updated.

## Stop Conditions

Stop if the next step needs external/private artifacts, credentials or paid API,
operation outside the repo, real capital, merge, a specification change,
official promotion, paper readiness, A3/A4 reopening, budget exhaustion, no
materially new hypothesis, changes too large for reasonable review, or any
governance violation.

Do not use human strategy as a stop condition while there is an open gap,
defensible research-only hypothesis, internal correction, unfinished
quantitative diagnostic, or possible sandbox/research module inside the repo.

## State Update Obligation

At the end of every autonomous mission, update:

- `reports/state/sniper_current_state.json`
- `reports/state/sniper_decision_ledger.md`
- `reports/state/sniper_artifact_registry.json`

Update `reports/state/sniper_spec_gap_backlog.yaml` and
`reports/state/sniper_autonomous_runbook.md` whenever the next safe mode or
operating rules change.
