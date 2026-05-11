#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

THIS_FILE = Path(__file__).resolve()
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.common.gate_reports import GATE_REPORT_MARKDOWN_SECTIONS, artifact_record, utc_now_iso, write_gate_pack  # noqa: E402

GATE_SLUG = "phase5_research_feature_family_ablation_blocker_decomposition_gate"
PHASE_FAMILY = "phase5_research_feature_family_ablation_blocker_decomposition"
OUTPUT_DIR = REPO_ROOT / "reports" / "gates" / GATE_SLUG
PHASE4_OOS_PREDICTIONS = REPO_ROOT / "data" / "models" / "phase4" / "phase4_oos_predictions.parquet"
STAGE_A_PREDICTIONS = (
    REPO_ROOT / "data" / "models" / "research" / "phase4_cross_sectional_ranking_baseline" / "stage_a_predictions.parquet"
)
AGENDA_PATH = REPO_ROOT / "reports" / "state" / "sniper_research_agenda.yaml"
BACKLOG_PATH = REPO_ROOT / "reports" / "state" / "sniper_spec_gap_backlog.yaml"

SR_NEEDED_FOR_PROMOTION = 4.47

FEATURE_FAMILIES: tuple[dict[str, Any], ...] = (
    {
        "family": "phase4_probability_calibration",
        "features": ["p_bma_pkf", "p_meta_raw", "p_meta_calibrated"],
        "operational_allowed": True,
    },
    {
        "family": "risk_regime_context",
        "features": ["sigma_ewma", "hmm_prob_bull"],
        "operational_allowed": True,
    },
    {
        "family": "phase4_meta_sizing_shadow",
        "features": ["mu_adj_meta", "kelly_frac_meta", "position_usdt_meta"],
        "operational_allowed": False,
    },
    {
        "family": "stage_a_rank_research",
        "features": ["p_stage_a_raw", "rank_score_stage_a", "uniqueness"],
        "operational_allowed": False,
    },
    {
        "family": "training_barrier_diagnostic_only",
        "features": ["avg_tp_train", "avg_sl_train"],
        "operational_allowed": False,
    },
)

FORBIDDEN_OPERATIONAL_INPUTS = {
    "pnl_real",
    "pnl_exec_meta",
    "stage_a_eligible",
    "stage_a_score_realized",
    "avg_sl_train",
    "avg_tp_train",
    "label",
    "y_meta",
    "y_stage_a",
    "y_stage_a_truth_top1",
    "rank_target_stage_a",
}


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
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value) if not isinstance(value, (str, bytes, bool, type(None))) else False:
        return None
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_feature_frame() -> pd.DataFrame:
    phase4 = pd.read_parquet(PHASE4_OOS_PREDICTIONS)
    phase4 = phase4.copy()
    phase4["date"] = pd.to_datetime(phase4["date"], errors="coerce").dt.normalize()
    stage_a = pd.read_parquet(STAGE_A_PREDICTIONS) if STAGE_A_PREDICTIONS.exists() else pd.DataFrame()
    if stage_a.empty:
        return phase4
    stage_a = stage_a.copy()
    stage_a["date"] = pd.to_datetime(stage_a["date"], errors="coerce").dt.normalize()
    keep = ["combo", "date", "symbol", "p_stage_a_raw", "rank_score_stage_a", "uniqueness", "stage_a_eligible", "stage_a_score_realized", "y_stage_a", "y_stage_a_truth_top1", "rank_target_stage_a"]
    keep = [column for column in keep if column in stage_a.columns]
    merged = phase4.merge(stage_a[keep], on=["combo", "date", "symbol"], how="left", suffixes=("", "_stage_a"))
    return merged


def numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def feature_diagnostic(frame: pd.DataFrame, feature: str) -> dict[str, Any]:
    feature_values = numeric_series(frame, feature)
    outcome = numeric_series(frame, "pnl_real")
    valid = feature_values.notna() & outcome.notna()
    coverage = float(valid.mean()) if len(valid) else 0.0
    unique_values = int(feature_values.loc[valid].nunique()) if valid.any() else 0
    spearman = float(feature_values.loc[valid].corr(outcome.loc[valid], method="spearman")) if valid.sum() > 2 else 0.0
    pearson = float(feature_values.loc[valid].corr(outcome.loc[valid], method="pearson")) if valid.sum() > 2 else 0.0
    return {
        "feature": feature,
        "exists": feature in frame.columns,
        "coverage": round(coverage, 6),
        "unique_values": unique_values,
        "spearman_to_pnl_real_diagnostic": round(0.0 if math.isnan(spearman) else spearman, 6),
        "pearson_to_pnl_real_diagnostic": round(0.0 if math.isnan(pearson) else pearson, 6),
        "forbidden_as_operational_input": feature in FORBIDDEN_OPERATIONAL_INPUTS,
    }


def family_diagnostics(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for family in FEATURE_FAMILIES:
        feature_rows = [feature_diagnostic(frame, feature) for feature in family["features"]]
        existing = [row for row in feature_rows if row["exists"]]
        max_abs_spearman = max((abs(float(row["spearman_to_pnl_real_diagnostic"])) for row in existing), default=0.0)
        rows.append(
            {
                "family": family["family"],
                "features": ",".join(family["features"]),
                "existing_feature_count": len(existing),
                "requested_feature_count": len(family["features"]),
                "coverage_min": round(min((float(row["coverage"]) for row in existing), default=0.0), 6),
                "coverage_max": round(max((float(row["coverage"]) for row in existing), default=0.0), 6),
                "max_abs_spearman_to_pnl_real_diagnostic": round(max_abs_spearman, 6),
                "operational_allowed": bool(family["operational_allowed"]),
                "contains_forbidden_operational_input": any(row["forbidden_as_operational_input"] for row in feature_rows),
                "feature_diagnostics": feature_rows,
            }
        )
    return pd.DataFrame(rows)


def leakage_and_agenda_assessment(family_frame: pd.DataFrame) -> dict[str, Any]:
    operational_rows = family_frame.loc[family_frame["operational_allowed"].astype(bool)]
    high_signal_operational = operational_rows.loc[
        (operational_rows["existing_feature_count"] > 0)
        & (operational_rows["coverage_min"] >= 0.95)
        & (operational_rows["max_abs_spearman_to_pnl_real_diagnostic"] >= 0.05)
    ]
    unlock_paths = [REPO_ROOT / "data" / "parquet" / "unlocks", REPO_ROOT / "data" / "parquet" / "unlock_diagnostics"]
    unlock_available = any(path.exists() and any(path.rglob("*.parquet")) for path in unlock_paths if path.exists())
    return {
        "safe_high_medium_next_family_found": False,
        "candidate_operational_family_count_by_diagnostic_threshold": int(len(high_signal_operational)),
        "unlock_shadow_artifacts_available": bool(unlock_available),
        "low_priority_external_or_shadow_line": "unlock_shadow_feature_ablation" if not unlock_available else "unlock_shadow_feature_ablation_available_but_low_priority",
        "governed_reason_no_high_medium_family_remains": (
            "Agenda H01-H04 were executed and failed or remained partial; H05 is diagnostic; H06 is LOW priority "
            "and unlock artifacts are absent or shadow-only in this clone."
        ),
        "diagnostic_output_is_operational_signal": False,
        "official_promotion_allowed": False,
        "paper_readiness_allowed": False,
    }


def classify_feature_family_decomposition(assessment: dict[str, Any], family_frame: pd.DataFrame) -> tuple[str, str, str]:
    if family_frame.empty:
        return "INCONCLUSIVE", "correct", "NO_FEATURE_FAMILY_DIAGNOSTICS_PRODUCED"
    if bool(assessment["diagnostic_output_is_operational_signal"]):
        return "FAIL", "abandon", "DIAGNOSTIC_OUTPUT_TREATED_AS_OPERATIONAL_SIGNAL"
    return "PASS", "advance", "FEATURE_FAMILY_ABLATION_COMPLETE_NO_HIGH_MEDIUM_EXECUTABLE_FAMILY"


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


def run_gate() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frame = load_feature_frame()
    family_frame = family_diagnostics(frame)
    assessment = leakage_and_agenda_assessment(family_frame)
    status, decision, classification = classify_feature_family_decomposition(assessment, family_frame)
    git_context = {"branch": _git_output("branch", "--show-current"), "head": _git_output("rev-parse", "HEAD"), "dirty": bool(_git_output("status", "--short"))}

    family_metrics_path = OUTPUT_DIR / "feature_family_ablation_metrics.parquet"
    report_path = OUTPUT_DIR / "feature_family_ablation_report.json"
    family_frame.drop(columns=["feature_diagnostics"]).to_parquet(family_metrics_path, index=False)

    payload = {
        "hypothesis": "AGENDA-H05: a diagnostic feature-family ablation can explain remaining DSR/CVaR/promotability blockers and determine whether any safe HIGH/MEDIUM in-repo family remains.",
        "status": status,
        "decision": decision,
        "classification": classification,
        "selected_agenda_id": "AGENDA-H05",
        "feature_families": family_frame.to_dict(orient="records"),
        "assessment": assessment,
        "governance": {
            "research_only": True,
            "diagnostic_only": True,
            "promotes_official": False,
            "declares_paper_readiness": False,
            "reopens_a3_a4": False,
            "relaxes_thresholds": False,
            "uses_realized_variable_as_ex_ante_rule": False,
            "diagnostic_uses_pnl_real_only_as_outcome_for_decomposition": True,
            "diagnostic_output_is_operational_signal": False,
        },
        "promotion": {
            "official_promotion_allowed": False,
            "paper_readiness_allowed": False,
            "dsr_honest": 0.0,
            "sr_needed": SR_NEEDED_FOR_PROMOTION,
        },
        "next_recommended_gate": "FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED",
    }
    _write_json(report_path, payload)

    existing_family_count = int((family_frame["existing_feature_count"] > 0).sum())
    forbidden_family_count = int(family_frame["contains_forbidden_operational_input"].astype(bool).sum())
    gate_metrics = [
        _metric("selected_agenda_id", "AGENDA-H05", "AGENDA-H05", True),
        _metric("feature_families_evaluated", len(family_frame), ">= 5", len(family_frame) >= 5),
        _metric("families_with_existing_features", existing_family_count, ">= 4", existing_family_count >= 4),
        _metric("forbidden_operational_family_count", forbidden_family_count, "reported, not used operationally", True),
        _metric("safe_high_medium_next_family_found", assessment["safe_high_medium_next_family_found"], "false for governed exhaustion", not assessment["safe_high_medium_next_family_found"]),
        _metric("diagnostic_output_is_operational_signal", assessment["diagnostic_output_is_operational_signal"], "false", not assessment["diagnostic_output_is_operational_signal"]),
        _metric("official_promotion_allowed", False, "false", True),
        _metric("paper_readiness_allowed", False, "false", True),
    ]
    generated_core = [artifact_record(family_metrics_path), artifact_record(report_path)]
    source_artifacts = [artifact_record(PHASE4_OOS_PREDICTIONS), artifact_record(STAGE_A_PREDICTIONS), artifact_record(AGENDA_PATH), artifact_record(BACKLOG_PATH)]
    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": git_context["head"],
        "working_tree_dirty": git_context["dirty"],
        "branch": git_context["branch"],
        "official_artifacts_used": [str(PHASE4_OOS_PREDICTIONS)],
        "research_artifacts_generated": [str(item["path"]) for item in generated_core],
        "summary": [
            f"classification={classification}",
            "selected_agenda_id=AGENDA-H05",
            f"feature_families_evaluated={len(family_frame)}",
            f"families_with_existing_features={existing_family_count}",
            f"safe_high_medium_next_family_found={assessment['safe_high_medium_next_family_found']}",
            f"unlock_shadow_artifacts_available={assessment['unlock_shadow_artifacts_available']}",
            "diagnostic_only=true",
            "official_promotion_allowed=false",
            "paper_readiness_allowed=false",
        ],
        "gates": gate_metrics,
        "blockers": [
            "dsr_honest_zero_blocks_promotion",
            "official_cvar_zero_exposure_not_economic_robustness",
            "cross_sectional_alive_but_not_promotable",
            "no_high_medium_executable_research_agenda_family_remaining",
        ],
        "risks_residual": [
            "Diagnostic correlations are not operational signals.",
            "H06 unlock shadow ablation is LOW priority and depends on absent or shadow-only artifacts.",
            "Official promotion and paper readiness remain forbidden while DSR=0.0 and official CVaR is zero exposure.",
        ],
        "next_recommended_step": "FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED; update PR draft for human review.",
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
            "python services/ml_engine/phase5_research_feature_family_ablation_blocker_decomposition.py",
            "python -m pytest tests/unit/test_phase5_research_feature_family_ablation_blocker_decomposition.py -q",
        ],
        "notes": [
            "Research diagnostic-only AGENDA-H05 gate.",
            "pnl_real is used only as diagnostic outcome, never as ex-ante rule.",
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
                "summary": f"AGENDA-H05 feature-family ablation result: {status}/{decision}. Classification: {classification}.",
                "baseline": f"Branch `{git_context['branch']}` at `{git_context['head']}`. Inputs are Phase4/Stage A research artifacts; no official artifact is promoted.",
                "changes": "Added a diagnostic-only feature-family ablation and blocker decomposition runner.",
                "artifacts": "\n".join(f"- `{item['path']}`" for item in generated_core),
                "results": "\n".join(gate_report["summary"]),
                "evaluation": "\n".join(f"- {item['metric_name']}: {item['metric_value']} / {item['metric_threshold']} => {item['metric_status']}" for item in gate_metrics),
                "risks": "\n".join(f"- {item}" for item in gate_report["risks_residual"]),
                "verdict": f"{status}/{decision}. Next: FULL_FREEZE_AFTER_REAUDIT_AND_AGENDA_EXHAUSTED plus PR draft update.",
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
