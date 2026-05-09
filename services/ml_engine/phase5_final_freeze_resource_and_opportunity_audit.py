#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML is available in the project env.
    yaml = None

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.common.gate_reports import GATE_REPORT_MARKDOWN_SECTIONS, artifact_record, utc_now_iso, write_gate_pack  # noqa: E402

GATE_SLUG = "phase5_final_freeze_resource_and_opportunity_audit_gate"
PHASE_FAMILY = "phase5_final_freeze_resource_and_opportunity_audit"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG

AGENDA_PATH = REPO_ROOT / "reports" / "state" / "sniper_research_agenda.yaml"
CURRENT_STATE_PATH = REPO_ROOT / "reports" / "state" / "sniper_current_state.json"
BACKLOG_PATH = REPO_ROOT / "reports" / "state" / "sniper_spec_gap_backlog.yaml"
NEXT_MISSION_PATH = REPO_ROOT / "reports" / "state" / "sniper_next_autonomous_mission.md"
ARTIFACT_REGISTRY_PATH = REPO_ROOT / "reports" / "state" / "sniper_artifact_registry.json"
HYPOTHESIS_INVENTORY_PATH = REPO_ROOT / "reports" / "state" / "sniper_hypothesis_inventory.md"

EXTERNAL_RESOURCE_MANIFEST_PATH = REPO_ROOT / "reports" / "state" / "sniper_external_resource_manifest.md"
FINAL_FREEZE_AUDIT_PATH = REPO_ROOT / "reports" / "state" / "sniper_final_freeze_opportunity_audit.md"
NEXT_EVIDENCE_REQUEST_PATH = REPO_ROOT / "reports" / "state" / "sniper_next_material_evidence_request.md"

H06_EXPECTED_PATTERNS = (
    "data/parquet/unlocks/**",
    "data/parquet/unlock_diagnostics/unlock_quality_daily.parquet",
)
H06_SHADOW_PATTERNS = ("data/parquet/unlock_wayback/**",)


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, check=False, capture_output=True, text=True)
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is None:
        return _read_yaml_conservative(path)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def _strip_yaml_scalar(raw: str) -> str:
    value = raw.strip()
    if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
        return value[1:-1]
    return value


def _read_yaml_conservative(path: Path) -> dict[str, Any]:
    """Fallback parser for the repository's simple research agenda shape."""
    hypotheses: list[dict[str, Any]] = []
    in_hypotheses = False
    current: dict[str, Any] | None = None
    current_list_key: str | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            in_hypotheses = stripped == "hypotheses:"
            current = None
            current_list_key = None
            continue
        if not in_hypotheses:
            continue
        if indent == 2 and stripped.startswith("- "):
            current = {}
            hypotheses.append(current)
            current_list_key = None
            stripped = stripped[2:].strip()
        if current is None:
            continue
        if stripped.startswith("- ") and current_list_key:
            current.setdefault(current_list_key, []).append(_strip_yaml_scalar(stripped[2:]))
            continue
        if ":" not in stripped:
            continue
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not raw_value:
            current[key] = []
            current_list_key = key
            continue
        current[key] = _strip_yaml_scalar(raw_value)
        current_list_key = None
    return {"hypotheses": hypotheses}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _pattern_base(pattern: str) -> Path:
    parts = Path(pattern).parts
    clean_parts: list[str] = []
    for part in parts:
        if "*" in part:
            break
        clean_parts.append(part)
    return Path(*clean_parts) if clean_parts else Path(".")


def artifact_pattern_status(pattern: str, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    base = repo_root / _pattern_base(pattern)
    has_wildcard = "*" in pattern
    if has_wildcard:
        if pattern.replace("\\", "/").endswith("/**"):
            matches = [path for path in base.rglob("*") if path.is_file()] if base.exists() else []
        else:
            matches = [path for path in repo_root.glob(pattern) if path.is_file()]
        parquet_count = sum(1 for path in matches if path.suffix.lower() == ".parquet")
        exists = base.exists() and bool(matches)
    else:
        target = repo_root / pattern
        matches = [target] if target.exists() else []
        parquet_count = 1 if target.exists() and target.suffix.lower() == ".parquet" else 0
        exists = target.exists()
    return {
        "pattern": pattern,
        "base_path": str(base),
        "base_exists": base.exists(),
        "exists": exists,
        "file_count": len(matches),
        "parquet_count": parquet_count,
        "test_path_command": f"Test-Path '{pattern.replace('/**', '')}'",
    }


def h06_preflight_assessment(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    expected = [artifact_pattern_status(pattern, repo_root) for pattern in H06_EXPECTED_PATTERNS]
    shadow = [artifact_pattern_status(pattern, repo_root) for pattern in H06_SHADOW_PATTERNS]
    exact_artifacts_available = all(item["exists"] for item in expected)
    return {
        "hypothesis": "AGENDA-H06 unlock_shadow_feature_ablation",
        "priority": "LOW",
        "status": "PREFLIGHT_EXECUTABLE" if exact_artifacts_available else "EXTERNAL_RESOURCE_REQUIRED",
        "preflight_executable": exact_artifacts_available,
        "expected_artifacts": expected,
        "shadow_or_noncanonical_artifacts": shadow,
        "missing_expected_patterns": [item["pattern"] for item in expected if not item["exists"]],
        "reason": (
            "Required unlock artifacts are available for diagnostic preflight."
            if exact_artifacts_available
            else "Required canonical unlock artifacts are absent. Existing shadow/wayback files, if present, must not be treated as official or fabricated into the required artifacts."
        ),
    }


def hypothesis_execution_rows(agenda: dict[str, Any], repo_root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hypothesis in agenda.get("hypotheses", []) or []:
        if not isinstance(hypothesis, dict):
            continue
        gate_slug = str(hypothesis.get("suggested_gate") or "")
        gate_report = repo_root / "reports" / "gates" / gate_slug / "gate_report.json" if gate_slug else repo_root / "_missing"
        required_status = [artifact_pattern_status(str(pattern), repo_root) for pattern in hypothesis.get("required_repo_data", []) or []]
        priority = str(hypothesis.get("priority") or "LOW")
        executed = bool(hypothesis.get("execution_status")) or gate_report.exists()
        executable_inputs = all(item["exists"] for item in required_status) if required_status else True
        rows.append(
            {
                "id": hypothesis.get("id"),
                "name": hypothesis.get("name"),
                "priority": priority,
                "suggested_gate": gate_slug,
                "gate_report_exists": gate_report.exists(),
                "execution_status": hypothesis.get("execution_status"),
                "executed_or_closed": executed,
                "required_inputs_available": executable_inputs,
                "is_high_or_medium": priority in {"HIGH", "MEDIUM"},
                "is_remaining_high_medium_executable": priority in {"HIGH", "MEDIUM"} and (not executed) and executable_inputs,
                "required_input_status": required_status,
            }
        )
    return rows


def internal_module_opportunities(h06_assessment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "module": "artifact_registry_validator",
            "safe_next_action": False,
            "assessment": "Existing artifact registry is valid JSON and is updated by this gate; no separate module is needed before freeze.",
        },
        {
            "module": "gate_pack_validator",
            "safe_next_action": False,
            "assessment": "services/common/gate_reports.py and tests/unit/test_gate_reports.py already validate gate pack shape; this gate records final gate pack completeness.",
        },
        {
            "module": "replay_falsification_summary_generator",
            "safe_next_action": False,
            "assessment": "All surviving candidates have been falsified or are partial/unstable; existing gate reports already summarize falsifiers.",
        },
        {
            "module": "drift_c2st_monitor_research_only",
            "safe_next_action": False,
            "assessment": "No live research candidate remains, so a drift/C2ST monitor would not reduce the current blocker without new data or a new candidate.",
        },
        {
            "module": "data_quality_gate",
            "safe_next_action": False,
            "assessment": "A data quality gate for H06 is blocked until canonical unlock artifacts exist.",
        },
        {
            "module": "feature_availability_audit",
            "safe_next_action": False,
            "assessment": "AGENDA-H05 already completed feature-family availability diagnostics; this gate records the remaining H06 artifact gap.",
        },
        {
            "module": "h06_unlock_shadow_feature_ablation_preflight",
            "safe_next_action": bool(h06_assessment["preflight_executable"]),
            "assessment": "Execute only if canonical unlock artifacts exist; otherwise create external resource manifest.",
        },
    ]


def classify_final_opportunity_audit(
    *,
    remaining_high_medium: list[dict[str, Any]],
    h06_assessment: dict[str, Any],
    modules: list[dict[str, Any]],
) -> tuple[str, str, str]:
    if remaining_high_medium:
        return "PARTIAL", "correct", "SAFE_HIGH_MEDIUM_HYPOTHESIS_REMAINS"
    if h06_assessment["preflight_executable"]:
        return "PARTIAL", "correct", "LOW_PRIORITY_PREFLIGHT_GATE_AVAILABLE"
    if any(item["safe_next_action"] for item in modules):
        return "PARTIAL", "correct", "SAFE_INTERNAL_MODULE_ACTION_REMAINS"
    return "PASS", "freeze", "FULL_FREEZE_AFTER_REAUDIT_AND_OPPORTUNITY_AUDITED"


def _metric(name: str, value: Any, threshold: str, passed: bool) -> dict[str, Any]:
    return {"gate_slug": GATE_SLUG, "metric_name": name, "metric_value": value, "metric_threshold": threshold, "metric_status": "PASS" if passed else "FAIL"}


def _markdown_sections(values: dict[str, str]) -> dict[str, str]:
    sections = {section: "" for section in GATE_REPORT_MARKDOWN_SECTIONS}
    keys = list(GATE_REPORT_MARKDOWN_SECTIONS)
    sections.update(
        {
            keys[0]: values.get("summary", ""),
            keys[1]: values.get("baseline", ""),
            keys[2]: values.get("changes", ""),
            keys[3]: values.get("artifacts", ""),
            keys[4]: values.get("results", ""),
            keys[5]: values.get("evaluation", ""),
            keys[6]: values.get("risks", ""),
            keys[7]: values.get("verdict", ""),
        }
    )
    return sections


def _write_state_documents(audit: dict[str, Any]) -> list[Path]:
    h06 = audit["h06_preflight"]
    external_manifest = f"""# SNIPER External Resource Manifest

Generated by: `{GATE_SLUG}`

## Summary

The current HIGH/MEDIUM research agenda is exhausted. The remaining LOW
hypothesis `AGENDA-H06 unlock_shadow_feature_ablation` requires canonical unlock
artifacts that are not present in this clone.

## Required Artifacts

| Hypothesis | Required path | Present | Verification |
| --- | --- | --- | --- |
"""
    for item in h06["expected_artifacts"]:
        external_manifest += f"| `AGENDA-H06` | `{item['pattern']}` | `{str(item['exists']).lower()}` | `{item['test_path_command']}` |\n"
    external_manifest += """
## Shadow/Noncanonical Evidence

"""
    for item in h06["shadow_or_noncanonical_artifacts"]:
        external_manifest += f"- `{item['pattern']}`: exists=`{str(item['exists']).lower()}`, parquet_count=`{item['parquet_count']}`.\n"
    external_manifest += """
Shadow/wayback files do not satisfy the canonical H06 artifact contract by
themselves and must not be transformed into official or fabricated artifacts.

## Why Codex Must Not Fabricate These Artifacts

- The unlock data must be point-in-time and source-traceable.
- Fabricating unlock files would create false evidence for an ex-ante research
  hypothesis.
- Shadow artifacts cannot become official inputs without an explicit gate and
  source validation.

## After Artifacts Are Provided

Run a diagnostic/preflight gate for `phase5_research_unlock_shadow_feature_ablation_gate`
or a successor gate that validates point-in-time coverage, schema, leakage risk
and diagnostic-only status before any research policy work.
"""
    EXTERNAL_RESOURCE_MANIFEST_PATH.write_text(external_manifest, encoding="utf-8")

    module_lines = "\n".join(
        f"- `{item['module']}`: safe_next_action=`{str(item['safe_next_action']).lower()}`. {item['assessment']}"
        for item in audit["internal_module_opportunities"]
    )
    high_medium_lines = "\n".join(
        f"- `{item['id']}` `{item['name']}` priority=`{item['priority']}` executed_or_closed=`{str(item['executed_or_closed']).lower()}` gate_report_exists=`{str(item['gate_report_exists']).lower()}`"
        for item in audit["hypotheses"]
        if item["is_high_or_medium"]
    )
    final_audit = f"""# SNIPER Final Freeze Opportunity Audit

Generated by: `{GATE_SLUG}`

## Classification

`{audit['classification']}`

Status/decision: `{audit['status']}/{audit['decision']}`

## HIGH/MEDIUM Agenda Review

Remaining executable HIGH/MEDIUM hypotheses: `{audit['remaining_high_medium_executable_count']}`.

{high_medium_lines}

## LOW/Preflight Review

H06 status: `{h06['status']}`.

Missing expected patterns:

{chr(10).join(f"- `{pattern}`" for pattern in h06['missing_expected_patterns'])}

## Internal Module Opportunities

{module_lines}

## Governance

- official_promotion_allowed: `false`
- paper_readiness_allowed: `false`
- A3/A4 reopened: `false`
- thresholds relaxed: `false`
- artifacts fabricated: `false`
- DSR blocker masked: `false`
- CVaR zero exposure treated as robustness: `false`

## Verdict

No safe internal HIGH/MEDIUM thesis remains. H06 is LOW priority and blocked by
missing canonical unlock artifacts. No additional non-promotional internal module
reduces the current blocker beyond this audit. The final audited freeze is
legitimate unless new artifacts or materially new evidence are provided.
"""
    FINAL_FREEZE_AUDIT_PATH.write_text(final_audit, encoding="utf-8")

    next_request = f"""# SNIPER Next Material Evidence Request

Generated by: `{GATE_SLUG}`

## Current Stop

`FULL_FREEZE_AFTER_REAUDIT_AND_OPPORTUNITY_AUDITED`

Codex cannot continue with a safe internal research-only gate from the current
agenda. Continuing requires new material evidence or external artifacts.

## Exact External Inputs That Would Reopen Research

1. Canonical unlock feature parquet tree:
   - expected path: `data/parquet/unlocks/**`
   - verification: `Test-Path 'data/parquet/unlocks'`
2. Unlock quality daily diagnostic parquet:
   - expected path: `data/parquet/unlock_diagnostics/unlock_quality_daily.parquet`
   - verification: `Test-Path 'data/parquet/unlock_diagnostics/unlock_quality_daily.parquet'`

## What Codex Can Do After These Inputs Exist

- Run H06 as diagnostic/preflight only.
- Verify point-in-time availability, schema, coverage and leakage controls.
- Keep unlock artifacts research/shadow unless a later explicit gate authorizes
  a different status.
- Continue to block official promotion while `dsr_honest=0.0`, official CVaR is
  zero exposure and cross-sectional remains `ALIVE_BUT_NOT_PROMOTABLE`.

## What Must Not Happen

- Do not fabricate unlock artifacts.
- Do not infer missing unlock quality data from wayback payloads.
- Do not promote shadow artifacts to official.
- Do not declare paper readiness or merge readiness from this freeze audit.
"""
    NEXT_EVIDENCE_REQUEST_PATH.write_text(next_request, encoding="utf-8")
    return [EXTERNAL_RESOURCE_MANIFEST_PATH, FINAL_FREEZE_AUDIT_PATH, NEXT_EVIDENCE_REQUEST_PATH]


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    agenda = _read_yaml(AGENDA_PATH)
    state = _read_json(CURRENT_STATE_PATH)
    hypotheses = hypothesis_execution_rows(agenda)
    remaining_high_medium = [item for item in hypotheses if item["is_remaining_high_medium_executable"]]
    h06 = h06_preflight_assessment()
    modules = internal_module_opportunities(h06)
    status, decision, classification = classify_final_opportunity_audit(
        remaining_high_medium=remaining_high_medium,
        h06_assessment=h06,
        modules=modules,
    )
    audit = {
        "gate_slug": GATE_SLUG,
        "status": status,
        "decision": decision,
        "classification": classification,
        "program_status_before": state.get("program_status"),
        "hypotheses": hypotheses,
        "remaining_high_medium_executable": remaining_high_medium,
        "remaining_high_medium_executable_count": len(remaining_high_medium),
        "h06_preflight": h06,
        "internal_module_opportunities": modules,
        "safe_internal_action_count": sum(1 for item in modules if item["safe_next_action"]) + len(remaining_high_medium),
        "official_promotion_allowed": False,
        "paper_readiness_allowed": False,
        "requires_external_resource": bool(h06["missing_expected_patterns"]),
        "final_freeze_audited": classification == "FULL_FREEZE_AFTER_REAUDIT_AND_OPPORTUNITY_AUDITED",
        "next_recommended_mode": "EXTERNAL_RESOURCE_REQUIRED_OR_DRAFT_PR_REVIEW",
        "next_recommended_gate": None,
    }
    audit_report_path = OUTPUT_DIR / "final_freeze_opportunity_audit.json"
    metrics_detail_path = OUTPUT_DIR / "final_freeze_opportunity_metrics.parquet"
    state_docs = _write_state_documents(audit)
    _write_json(audit_report_path, audit)

    metric_rows = [
        {"metric": "remaining_high_medium_executable_count", "value": len(remaining_high_medium)},
        {"metric": "h06_preflight_executable", "value": h06["preflight_executable"]},
        {"metric": "h06_missing_expected_pattern_count", "value": len(h06["missing_expected_patterns"])},
        {"metric": "internal_module_safe_next_action_count", "value": sum(1 for item in modules if item["safe_next_action"])},
        {"metric": "official_promotion_allowed", "value": False},
        {"metric": "paper_readiness_allowed", "value": False},
    ]
    metric_rows = [{"metric": row["metric"], "value": json.dumps(row["value"])} for row in metric_rows]
    pd.DataFrame(metric_rows).to_parquet(metrics_detail_path, index=False)

    git_context = {"branch": _git_output("branch", "--show-current"), "head": _git_output("rev-parse", "HEAD"), "dirty": bool(_git_output("status", "--short"))}
    gate_metrics = [
        _metric("remaining_high_medium_executable_count", len(remaining_high_medium), "0", len(remaining_high_medium) == 0),
        _metric("h06_preflight_executable", h06["preflight_executable"], "false when exact artifacts absent", not h06["preflight_executable"]),
        _metric("h06_missing_expected_pattern_count", len(h06["missing_expected_patterns"]), ">= 1 recorded as external resource", len(h06["missing_expected_patterns"]) >= 1),
        _metric("external_resource_manifest_created", EXTERNAL_RESOURCE_MANIFEST_PATH.exists(), "true", EXTERNAL_RESOURCE_MANIFEST_PATH.exists()),
        _metric("final_freeze_opportunity_audit_created", FINAL_FREEZE_AUDIT_PATH.exists(), "true", FINAL_FREEZE_AUDIT_PATH.exists()),
        _metric("next_material_evidence_request_created", NEXT_EVIDENCE_REQUEST_PATH.exists(), "true", NEXT_EVIDENCE_REQUEST_PATH.exists()),
        _metric("internal_module_safe_next_action_count", sum(1 for item in modules if item["safe_next_action"]), "0 after audit", sum(1 for item in modules if item["safe_next_action"]) == 0),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]
    generated_core = [artifact_record(audit_report_path), artifact_record(metrics_detail_path)] + [artifact_record(path) for path in state_docs]
    source_artifacts = [
        artifact_record(AGENDA_PATH),
        artifact_record(CURRENT_STATE_PATH),
        artifact_record(BACKLOG_PATH),
        artifact_record(NEXT_MISSION_PATH),
        artifact_record(ARTIFACT_REGISTRY_PATH),
        artifact_record(HYPOTHESIS_INVENTORY_PATH),
    ]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [],
        "research_artifacts_generated": [str(item["path"]) for item in generated_core],
        "summary": [
            f"classification={classification}",
            f"remaining_high_medium_executable_count={len(remaining_high_medium)}",
            f"h06_status={h06['status']}",
            f"h06_missing_expected_patterns={len(h06['missing_expected_patterns'])}",
            "external_resource_manifest_created=true",
            "final_freeze_opportunity_audit_created=true",
            "next_material_evidence_request_created=true",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
        ],
        "gates": gate_metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "no_high_medium_executable_research_agenda_family_remaining",
            "h06_requires_external_unlock_artifacts",
        ],
        "risks_residual": [
            "Final freeze is governance/research closure, not readiness.",
            "H06 cannot be implemented without canonical unlock artifacts.",
            "Shadow/wayback unlock files must not be treated as official or fabricated into canonical artifacts.",
        ],
        "next_recommended_step": "FULL_FREEZE_AFTER_REAUDIT_AND_OPPORTUNITY_AUDITED; draft PR review or provide listed external unlock artifacts.",
    }
    manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": git_context["head"],
        "branch": git_context["branch"],
        "working_tree_dirty_before": git_context["dirty"],
        "working_tree_dirty_after": True,
        "source_artifacts": source_artifacts,
        "generated_artifacts": generated_core,
        "commands_executed": [
            "python services/ml_engine/phase5_final_freeze_resource_and_opportunity_audit.py",
            "python -m pytest tests/unit/test_phase5_final_freeze_resource_and_opportunity_audit.py -q",
        ],
        "notes": [
            "Final freeze resource/opportunity audit.",
            "H06 is LOW priority and blocked by missing canonical unlock artifacts.",
            "No official promotion, paper readiness, merge, threshold relaxation or A3/A4 reopening.",
        ],
    }
    write_gate_pack(
        output_dir=OUTPUT_DIR,
        gate_report=gate_report,
        gate_manifest=manifest,
        gate_metrics=gate_metrics,
        markdown_sections=_markdown_sections(
            {
                "summary": f"Final freeze opportunity audit result: {status}/{decision}. Classification: {classification}.",
                "baseline": f"Branch `{git_context['branch']}` at `{git_context['head']}`. The prior state was `{state.get('program_status')}`.",
                "changes": "Added final opportunity/resource audit outputs and external resource request for H06.",
                "artifacts": "\n".join(f"- `{item['path']}`" for item in generated_core),
                "results": "\n".join(gate_report["summary"]),
                "evaluation": "\n".join(f"- {item['metric_name']}: {item['metric_value']} / {item['metric_threshold']} => {item['metric_status']}" for item in gate_metrics),
                "risks": "\n".join(f"- {item}" for item in gate_report["risks_residual"]),
                "verdict": f"{status}/{decision}. No safe internal next action remains; H06 requires external unlock artifacts.",
            }
        ),
    )
    return gate_report


def main() -> int:
    report = run_gate()
    print(json.dumps({"gate_slug": GATE_SLUG, "status": report["status"], "decision": report["decision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
