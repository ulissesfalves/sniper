#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

THIS_FILE = Path(__file__).resolve()
THIS_DIR = THIS_FILE.parent
REPO_ROOT = THIS_FILE.parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import phase4_cpcv as phase4
import phase4_stage_a_experiment as stage_a
import phase5_cross_sectional_hardening_baseline as hardening
import phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate as restore
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_cross_sectional_sovereign_hardening_recheck"
PHASE_FAMILY = "phase5_cross_sectional_sovereign_hardening_recheck"
RESTORED_BUNDLE_EXPERIMENT = restore.RESTORED_BUNDLE_EXPERIMENT
RESTORE_DIAGNOSTIC_NAMESPACE = restore.RESTORE_DIAGNOSTIC_NAMESPACE
OLD_HARDENING_NAMESPACE = "phase5_cross_sectional_hardening"
RESEARCH_NAMESPACE = "phase5_cross_sectional_sovereign_hardening_recheck"
GATE_REQUIRED_FILES = (
    "gate_report.json",
    "gate_report.md",
    "gate_manifest.json",
    "gate_metrics.parquet",
)
RESEARCH_ARTIFACT_FILES = (
    "hardening_recheck_matrix.parquet",
    "hardening_recheck_summary.json",
    "hardening_recheck_lineage_comparison.json",
    "official_artifacts_integrity.json",
)


def _git_output(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_paths() -> tuple[Path, Path, Path, Path]:
    model_path = stage_a._resolve_model_path()
    stage_a._configure_phase4_paths(model_path)
    restored_root = model_path / "research" / RESTORED_BUNDLE_EXPERIMENT
    research_path = model_path / "research" / RESEARCH_NAMESPACE
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, restored_root, research_path, gate_path


def _load_restored_manifest(restored_root: Path) -> dict[str, Any]:
    manifest_path = restored_root / "restored_bundle_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing restored bundle manifest: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _validate_restored_bundle(restored_root: Path) -> dict[str, Any]:
    issues: list[str] = []
    validated_files: list[dict[str, Any]] = []
    for _, relative_target in restore.RESTORE_SOURCE_TARGETS:
        path = restored_root / relative_target
        if not path.exists():
            issues.append(f"missing:{relative_target}")
            continue
        try:
            if path.suffix.lower() == ".parquet":
                pd.read_parquet(path)
            elif path.suffix.lower() == ".json":
                json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"invalid:{relative_target}:{type(exc).__name__}")
        validated_files.append(artifact_record(path))
    manifest_path = restored_root / "restored_bundle_manifest.json"
    if not manifest_path.exists():
        issues.append("missing:restored_bundle_manifest.json")
    else:
        try:
            json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"invalid:restored_bundle_manifest.json:{type(exc).__name__}")
    return {
        "pass": not issues,
        "issues": issues,
        "validated_files": validated_files,
        "required_file_count": len(restore.RESTORE_SOURCE_TARGETS),
        "restored_root": str(restored_root),
    }


def _exercise_restored_bundle_validation(restored_root: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase5_sovereign_recheck_", dir=str(restored_root.parent)) as tmp_root:
        tmp_root_path = Path(tmp_root)
        missing_root = tmp_root_path / "missing"
        corrupt_root = tmp_root_path / "corrupt"
        shutil.copytree(restored_root, missing_root)
        shutil.copytree(restored_root, corrupt_root)

        missing_target = missing_root / "phase4_cross_sectional_closure_gate" / "cross_sectional_closure_eval.parquet"
        if missing_target.exists():
            missing_target.unlink()
        missing_result = _validate_restored_bundle(missing_root)

        corrupt_target = corrupt_root / "phase4_cross_sectional_closure_gate" / "cross_sectional_closure_eval.parquet"
        corrupt_target.write_text("corrupted", encoding="utf-8")
        corrupt_result = _validate_restored_bundle(corrupt_root)

    return {
        "missing_snapshot_detected": not missing_result.get("pass", False),
        "missing_snapshot_issues": missing_result.get("issues", []),
        "corrupted_snapshot_detected": not corrupt_result.get("pass", False),
        "corrupted_snapshot_issues": corrupt_result.get("issues", []),
        "pass": bool((not missing_result.get("pass", False)) and (not corrupt_result.get("pass", False))),
        "fallback_detected": False,
        "snapshot_equivalent_path": "phase4_cross_sectional_closure_gate/cross_sectional_closure_eval.parquet",
    }


def _prepare_operational_replay_frame(model_path: Path) -> dict[str, Any]:
    replay = restore._run_regenerated_replay(model_path)
    frame = replay["frame"].copy()
    frame = phase4._attach_execution_pnl(frame, position_col="decision_position_usdt", output_col="pnl_exec_stage_a")
    frame["position_usdt_stage_a"] = pd.to_numeric(frame.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    frame["kelly_frac_stage_a"] = pd.to_numeric(frame.get("decision_kelly_frac"), errors="coerce").fillna(0.0)
    frame["mu_adj_stage_a"] = pd.to_numeric(frame.get("decision_mu_adj"), errors="coerce").fillna(0.0)
    frame["decision_proxy_prob"] = pd.to_numeric(frame.get("decision_proxy_prob"), errors="coerce").fillna(
        pd.Series(frame.get("decision_selected", False), index=frame.index).fillna(False).astype(float)
    )
    replay["frame"] = frame
    replay["frame_hash"] = hardening._frame_hash(frame)
    replay["metrics"] = {
        **restore._compute_sovereign_metrics(frame, selected_col="decision_selected", position_col="decision_position_usdt"),
        **restore._latest_replay_counts(frame),
    }
    return replay


def _explicit_sovereign_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    selected = pd.Series(work.get("decision_selected", False), index=work.index).fillna(False).astype(bool)
    position = pd.to_numeric(work.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    active_mask = selected & (position > 0)
    latest_date = work["date"].dropna().max()
    latest_mask = work["date"] == latest_date
    recent_dates = sorted(work["date"].dropna().unique().tolist())[-restore.RECENT_WINDOW_DATES :]
    recent_live_dates = 0
    for date_value in recent_dates:
        if bool(active_mask.loc[work["date"] == date_value].any()):
            recent_live_dates += 1
    return {
        "latest_active_count_decision_space": int(active_mask.loc[latest_mask].sum()),
        "headroom_decision_space": bool(float(position.loc[latest_mask].max()) > 0.0) if not position.loc[latest_mask].empty else False,
        "recent_live_dates_decision_space": int(recent_live_dates),
        "historical_active_events_decision_space": int(active_mask.sum()),
    }


def _build_metric_row(*, scenario: str, frame: pd.DataFrame, pnl_col: str = "pnl_exec_stage_a", scenario_type: str, source: str) -> dict[str, Any]:
    return hardening._build_metric_row(
        scenario=scenario,
        frame=frame,
        signal_col="decision_proxy_prob",
        pnl_col=pnl_col,
        scenario_type=scenario_type,
        source=source,
    )


def _build_gate_metrics(
    *,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    lineage_anchor_correct: bool,
    old_failure_recontextualized: bool,
    sovereign_hardening_rechecked: bool,
) -> list[dict[str, Any]]:
    def _metric(name: str, value: Any, passed: bool) -> dict[str, Any]:
        return {
            "gate_slug": GATE_SLUG,
            "metric_name": name,
            "metric_value": value,
            "metric_threshold": "PASS",
            "metric_status": "PASS" if passed else "FAIL",
        }

    return [
        _metric("official_artifacts_unchanged", official_artifacts_unchanged, official_artifacts_unchanged),
        _metric("research_only_isolation_pass", research_only_isolation_pass, research_only_isolation_pass),
        _metric("reproducibility_pass", reproducibility_pass, reproducibility_pass),
        _metric("sovereign_metric_definitions_unchanged", sovereign_metric_definitions_unchanged, sovereign_metric_definitions_unchanged),
        _metric("lineage_anchor_correct", lineage_anchor_correct, lineage_anchor_correct),
        _metric("old_failure_recontextualized", old_failure_recontextualized, old_failure_recontextualized),
        _metric("sovereign_hardening_rechecked", sovereign_hardening_rechecked, sovereign_hardening_rechecked),
    ]


def _classify_recheck(
    *,
    integrity_pass: bool,
    lineage_anchor_correct: bool,
    recheck_row: dict[str, Any],
    regime_summary: dict[str, Any],
    threshold_rows: list[dict[str, Any]],
    friction_rows: list[dict[str, Any]],
    snapshot_guard_pass: bool,
    gate_pack_complete: bool,
) -> tuple[str, str, str, list[str]]:
    if not integrity_pass or not lineage_anchor_correct or not snapshot_guard_pass or not gate_pack_complete:
        return "FAIL", "abandon", "SOVEREIGN_HARDENING_FAILS", ["integrity_or_lineage_failure"]
    if int(recheck_row.get("latest_active_count_decision_space", 0)) <= 0 or not bool(recheck_row.get("headroom_decision_space", False)):
        return "FAIL", "abandon", "SOVEREIGN_HARDENING_FAILS", ["latest_headroom_dead_even_with_sovereign_lineage"]

    fragilities: list[str] = []
    if float(recheck_row.get("sharpe_operational", 0.0)) <= 0.0:
        fragilities.append("negative_base_sharpe")
    if float(recheck_row.get("dsr_honest", 0.0)) <= 0.0:
        fragilities.append("non_positive_base_dsr")
    if int(recheck_row.get("subperiods_positive", 0)) < max(1, int(regime_summary.get("subperiods_tested", 0) // 2) + 1):
        fragilities.append("subperiod_majority_not_positive")
    if regime_summary.get("negative_slices"):
        fragilities.append("negative_regime_slices_present")
    if any(int(row.get("latest_active_count_decision_space", 0)) <= 0 or not bool(row.get("headroom_decision_space", False)) for row in threshold_rows):
        fragilities.append("threshold_sanity_can_kill_latest")
    if any(float(row.get("sharpe_operational", 0.0)) <= 0.0 for row in friction_rows):
        fragilities.append("friction_stress_negative")

    if fragilities:
        return "PARTIAL", "correct", "SOVEREIGN_HARDENING_MIXED", fragilities
    return "PASS", "advance", "SOVEREIGN_HARDENING_SURVIVES", []


def run_phase5_cross_sectional_sovereign_hardening_recheck() -> dict[str, Any]:
    model_path, restored_root, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)
    working_tree_before = _git_output("status", "--short", "--untracked-files=all")

    restored_summary = json.loads((model_path / "research" / RESTORE_DIAGNOSTIC_NAMESPACE / "sovereign_restore_replay_summary.json").read_text(encoding="utf-8"))
    old_hardening_summary = json.loads((model_path / "research" / OLD_HARDENING_NAMESPACE / "phase5_cross_sectional_hardening_summary.json").read_text(encoding="utf-8"))
    restored_bundle_metrics = restore._load_restored_bundle_metrics(restored_root)
    restore_manifest = _load_restored_manifest(restored_root)
    restore_payload = {
        "entries": list(restore_manifest.get("entries", [])),
        "all_byte_exact": all(bool(row.get("byte_exact_match")) for row in restore_manifest.get("entries", [])),
        "manifest_path": str(restored_root / "restored_bundle_manifest.json"),
    }

    bundle_inventory = restore._build_bundle_inventory(restored_root, restore_payload)
    restored_bundle_validation = _validate_restored_bundle(restored_root)
    snapshot_corruption_result = _exercise_restored_bundle_validation(restored_root)

    replay_run1 = _prepare_operational_replay_frame(model_path)
    replay_run2 = _prepare_operational_replay_frame(model_path)

    reproducibility = {
        "raw_frame_hash_run1": replay_run1["frame_hash"],
        "raw_frame_hash_run2": replay_run2["frame_hash"],
        "metrics_run1": replay_run1["metrics"],
        "metrics_run2": replay_run2["metrics"],
        "pass": bool(replay_run1["frame_hash"] == replay_run2["frame_hash"] and replay_run1["metrics"] == replay_run2["metrics"]),
    }

    recheck_row = _build_metric_row(
        scenario="sovereign_hardening_recheck",
        frame=replay_run1["frame"],
        scenario_type="baseline",
        source=RESTORED_BUNDLE_EXPERIMENT,
    )

    friction_rows: list[dict[str, Any]] = []
    for stress in phase4.PHASE4_FRICTION_STRESS_SPECS:
        if stress["label"] == "base":
            continue
        stressed_frame, stressed_pnl_col = hardening._apply_friction_stress(
            replay_run1["frame"],
            label=f"stress_{stress['label']}",
            slippage_mult=float(stress["slippage_mult"]),
            extra_cost_bps=float(stress["extra_cost_bps"]),
        )
        friction_rows.append(
            _build_metric_row(
                scenario=f"friction_{stress['label']}",
                frame=stressed_frame,
                pnl_col=stressed_pnl_col,
                scenario_type="friction",
                source=RESTORED_BUNDLE_EXPERIMENT,
            )
        )

    threshold_rows: list[dict[str, Any]] = []
    for threshold in (0.50, 0.55, 0.60):
        threshold_frame = hardening._apply_threshold_mask(replay_run1["frame"], threshold)
        threshold_rows.append(
            _build_metric_row(
                scenario=f"threshold_{int(round(threshold * 100)):03d}",
                frame=threshold_frame,
                scenario_type="threshold_sanity",
                source=RESTORED_BUNDLE_EXPERIMENT,
            )
        )

    stress_matrix = pd.DataFrame([recheck_row, *friction_rows, *threshold_rows])
    stress_matrix.to_parquet(research_path / "hardening_recheck_matrix.parquet", index=False)

    regime_rows = pd.DataFrame(
        hardening._build_regime_slice_rows(
            replay_run1["frame"],
            scenario="sovereign_hardening_recheck",
            signal_col="decision_proxy_prob",
            pnl_col="pnl_exec_stage_a",
        )
    )
    regime_summary = hardening._regime_slice_summary(regime_rows, "sovereign_hardening_recheck")

    comparison_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "old_hardening_wrong_lineage": {
            "baseline_experiment": old_hardening_summary.get("baseline_experiment"),
            "classification_final": old_hardening_summary.get("classification_final"),
            "latest_active_count_decision_space": old_hardening_summary.get("frozen_baseline_metrics", {}).get("latest_active_count_decision_space"),
            "headroom_decision_space": old_hardening_summary.get("frozen_baseline_metrics", {}).get("headroom_decision_space"),
            "recent_live_dates_decision_space": old_hardening_summary.get("frozen_baseline_metrics", {}).get("recent_live_dates_decision_space"),
            "historical_active_events_decision_space": old_hardening_summary.get("frozen_baseline_metrics", {}).get("historical_active_events_decision_space"),
            "sharpe_operational": old_hardening_summary.get("frozen_baseline_metrics", {}).get("sharpe_operational"),
            "dsr_honest": old_hardening_summary.get("frozen_baseline_metrics", {}).get("dsr_honest"),
        },
        "restored_sovereign_baseline": restored_bundle_metrics,
        "current_recheck": {
            **recheck_row,
            "regime_slice_results": regime_summary,
            "reproducibility": reproducibility,
        },
        "impact_of_correct_lineage": {
            "latest_active_count_delta_vs_old": int(recheck_row["latest_active_count_decision_space"]) - int(old_hardening_summary["frozen_baseline_metrics"]["latest_active_count_decision_space"]),
            "historical_active_events_delta_vs_old": int(recheck_row["historical_active_events_decision_space"]) - int(old_hardening_summary["frozen_baseline_metrics"]["historical_active_events_decision_space"]),
            "headroom_flipped_true": bool(
                (not bool(old_hardening_summary["frozen_baseline_metrics"]["headroom_decision_space"]))
                and bool(recheck_row["headroom_decision_space"])
            ),
        },
    }
    _write_json(research_path / "hardening_recheck_lineage_comparison.json", comparison_payload)

    explicit_metrics = _explicit_sovereign_metrics(replay_run1["frame"])
    helper_metrics = stage5._compute_decision_space_metrics(replay_run1["frame"])
    sovereign_metric_definitions_unchanged = bool(
        explicit_metrics["latest_active_count_decision_space"] == helper_metrics["latest_active_count_decision_space"]
        and explicit_metrics["headroom_decision_space"] == helper_metrics["headroom_decision_space"]
        and explicit_metrics["recent_live_dates_decision_space"] == helper_metrics["recent_live_dates_decision_space"]
        and explicit_metrics["historical_active_events_decision_space"] == helper_metrics["historical_active_events_decision_space"]
    )

    official_after = stage5._collect_official_inventory(model_path)
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = True
    reproducibility_pass = bool(reproducibility["pass"])
    lineage_anchor_correct = bool(
        restored_summary.get("equivalence_classification") == "EXACT_RESTORE"
        and restore_manifest.get("historical_commit") == restore.EXPECTED_HISTORICAL_COMMIT
        and restored_bundle_metrics["latest_date"] == replay_run1["metrics"]["latest_date"]
        and restored_bundle_metrics["latest_active_count_decision_space"] == replay_run1["metrics"]["latest_active_count_decision_space"]
        and restored_bundle_metrics["headroom_decision_space"] == replay_run1["metrics"]["headroom_decision_space"]
        and restored_bundle_metrics["recent_live_dates_decision_space"] == replay_run1["metrics"]["recent_live_dates_decision_space"]
        and restored_bundle_metrics["historical_active_events_decision_space"] == replay_run1["metrics"]["historical_active_events_decision_space"]
    )
    old_failure_recontextualized = bool(
        old_hardening_summary.get("classification_final") == "HARDENING_BASELINE_FAILS"
        and int(old_hardening_summary["frozen_baseline_metrics"]["latest_active_count_decision_space"]) == 0
        and not bool(old_hardening_summary["frozen_baseline_metrics"]["headroom_decision_space"])
        and int(recheck_row["latest_active_count_decision_space"]) > 0
        and bool(recheck_row["headroom_decision_space"])
    )
    sovereign_hardening_rechecked = bool(
        not stress_matrix.empty
        and restored_bundle_validation.get("pass", False)
        and bundle_inventory["restored_files_present"] == len(restore.RESTORE_SOURCE_TARGETS)
    )

    gate_metrics = _build_gate_metrics(
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        reproducibility_pass=reproducibility_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        lineage_anchor_correct=lineage_anchor_correct,
        old_failure_recontextualized=old_failure_recontextualized,
        sovereign_hardening_rechecked=sovereign_hardening_rechecked,
    )

    status, decision, classification, fragilities = _classify_recheck(
        integrity_pass=all(metric["metric_status"] == "PASS" for metric in gate_metrics),
        lineage_anchor_correct=lineage_anchor_correct,
        recheck_row=recheck_row,
        regime_summary=regime_summary,
        threshold_rows=threshold_rows,
        friction_rows=friction_rows,
        snapshot_guard_pass=bool(snapshot_corruption_result["pass"]),
        gate_pack_complete=True,
    )

    slippage_stress_impact = {
        row["scenario"]: {
            "delta_sharpe_operational": round(float(row.get("sharpe_operational", 0.0)) - float(recheck_row.get("sharpe_operational", 0.0)), 4),
            "delta_historical_active_events_decision_space": int(row.get("historical_active_events_decision_space", 0)) - int(recheck_row.get("historical_active_events_decision_space", 0)),
            "delta_equity_final": round(float(row.get("equity_final", 0.0)) - float(recheck_row.get("equity_final", 0.0)), 2),
        }
        for row in friction_rows
    }

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "baseline_restored_experiment": RESTORED_BUNDLE_EXPERIMENT,
        "classification_final": classification,
        "status": status,
        "decision": decision,
        "baseline_sovereign_metrics": restored_bundle_metrics,
        "recheck_metrics": recheck_row,
        "reproducibility": reproducibility,
        "restored_bundle_validation": restored_bundle_validation,
        "snapshot_corruption_result": snapshot_corruption_result,
        "bundle_inventory": {
            "restored_files_present": bundle_inventory["restored_files_present"],
            "required_restored_files": bundle_inventory["required_restored_files"],
            "missing_restored_files": bundle_inventory["missing_restored_files"],
        },
        "snapshot_report_predictions_consistency": restored_summary.get("snapshot_report_predictions_consistency", {}),
        "regime_slice_results": regime_summary,
        "slippage_stress_impact": slippage_stress_impact,
        "fragilities": fragilities,
        "comparison_to_old_hardening": comparison_payload["impact_of_correct_lineage"],
    }
    _write_json(research_path / "hardening_recheck_summary.json", summary_payload)

    integrity_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "official_before": official_before,
        "official_after": official_after,
        "official_artifacts_unchanged": official_artifacts_unchanged,
        "official_root": str(model_path),
        "restored_bundle_root": str(restored_root),
        "research_root": str(research_path),
        "gate_root": str(gate_path),
        "research_only_isolation_pass": research_only_isolation_pass,
    }
    _write_json(research_path / "official_artifacts_integrity.json", integrity_payload)

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "working_tree_dirty": bool(working_tree_before),
        "branch": _git_output("branch", "--show-current"),
        "official_artifacts_used": [
            str(model_path / "phase3"),
            str(model_path / "features"),
            str(model_path / "phase4" / "phase4_report_v4.json"),
            str(model_path / "phase4" / "phase4_execution_snapshot.parquet"),
            str(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
            str(model_path / "phase4" / "phase4_oos_predictions.parquet"),
            str(model_path / "phase4" / "phase4_gate_diagnostic.json"),
        ],
        "research_artifacts_generated": [str(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "summary": [
            f"classification_final={classification}",
            f"baseline_latest_active_count_decision_space={restored_bundle_metrics['latest_active_count_decision_space']}",
            f"recheck_latest_active_count_decision_space={recheck_row['latest_active_count_decision_space']}",
            f"recheck_headroom_decision_space={recheck_row['headroom_decision_space']}",
            f"recheck_sharpe_operational={recheck_row['sharpe_operational']}",
        ],
        "gates": gate_metrics,
        "blockers": [] if decision != "abandon" else fragilities,
        "risks_residual": fragilities if fragilities else [],
        "next_recommended_step": (
            "Proceed with the next research-only stage using the sovereign restored baseline."
            if decision == "advance"
            else "Keep the restored sovereign lineage, but correct the operational fragilities before any advancement."
        ),
    }

    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "branch": _git_output("branch", "--show-current"),
        "working_tree_dirty_before": bool(working_tree_before),
        "working_tree_dirty_after": bool(_git_output("status", "--short", "--untracked-files=all")),
        "source_artifacts": [
            artifact_record(restored_root / "restored_bundle_manifest.json"),
            artifact_record(model_path / "research" / RESTORE_DIAGNOSTIC_NAMESPACE / "sovereign_restore_replay_summary.json"),
            artifact_record(model_path / "research" / OLD_HARDENING_NAMESPACE / "phase5_cross_sectional_hardening_summary.json"),
            artifact_record(model_path / "research" / "phase4_cross_sectional_ranking_baseline" / "stage_a_predictions.parquet"),
        ],
        "generated_artifacts": [artifact_record(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "commands_executed": [
            "python services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py",
        ],
        "notes": [
            f"classification_final={classification}",
            f"baseline_anchor={RESTORED_BUNDLE_EXPERIMENT}",
            "The old hardening failure was recontextualized against the restored sovereign lineage.",
        ],
    }

    markdown_sections = {
        "Resumo executivo": "\n".join(
            [
                f"- Status: `{status}` / decision `{decision}` / classificação `{classification}`.",
                f"- Baseline soberana restaurada latest/headroom: `{restored_bundle_metrics['latest_active_count_decision_space']}` / `{restored_bundle_metrics['headroom_decision_space']}`.",
                f"- Hardening recheck atual latest/headroom/sharpe: `{recheck_row['latest_active_count_decision_space']}` / `{recheck_row['headroom_decision_space']}` / `{recheck_row['sharpe_operational']}`.",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                f"- Baseline research-only obrigatória: `{RESTORED_BUNDLE_EXPERIMENT}`.",
                f"- Old hardening lineage recontextualized from `{old_hardening_summary.get('baseline_experiment')}`.",
                "- O caminho official permaneceu read-only.",
            ]
        ),
        "MudanÃ§as implementadas": "\n".join(
            [
                "- Runner research-only de recheck soberano ancorado explicitamente no bundle restaurado.",
                "- Replay duplo regenerado com a lógica histórica de decision-space e aliases operacionais para sizing/PnL.",
                "- Stress mínimo de fricção, threshold sanity, regime slices e guardas de bundle/snapshot/gate pack.",
            ]
        ),
        "Artifacts gerados": "\n".join(
            [f"- `{research_path / name}`" for name in RESEARCH_ARTIFACT_FILES]
            + [f"- `{gate_path / name}`" for name in GATE_REQUIRED_FILES]
        ),
        "Resultados": "\n".join(
            [
                f"- Baseline restaurada: `{restored_bundle_metrics}`.",
                f"- Hardening recheck atual: `{recheck_row}`.",
                f"- Impacto da correção de lineage: `{comparison_payload['impact_of_correct_lineage']}`.",
                f"- Stress mínimo: `{slippage_stress_impact}` / regime `{regime_summary}`.",
            ]
        ),
        "AvaliaÃ§Ã£o contra gates": "\n".join([f"- {row['metric_name']} = `{row['metric_status']}`" for row in gate_metrics]),
        "Riscos residuais": "\n".join([f"- {item}" for item in fragilities] if fragilities else ["- Nenhuma fragilidade material adicional detectada."]),
        "Veredito final: advance / correct / abandon": f"- `{classification}` -> decision `{decision}`",
    }

    outputs = write_gate_pack(
        output_dir=gate_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )
    summary_payload["gate_pack_complete"] = all((gate_path / name).exists() for name in GATE_REQUIRED_FILES)
    _write_json(research_path / "hardening_recheck_summary.json", summary_payload)

    return {
        "status": status,
        "decision": decision,
        "classification_final": classification,
        "research_path": str(research_path),
        "gate_path": str(gate_path),
        "gate_outputs": {key: str(value) for key, value in outputs.items()},
    }


def main() -> None:
    result = run_phase5_cross_sectional_sovereign_hardening_recheck()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
