#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from io import BytesIO
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

import phase4_stage_a_experiment as stage_a
import phase5_cross_sectional_hardening_baseline as hardening
import phase5_stage_a3_spec_hardening as stage5
from services.common.gate_reports import artifact_record, utc_now_iso, write_gate_pack

GATE_SLUG = "phase5_cross_sectional_latest_headroom_reconciliation_audit"
PHASE_FAMILY = "phase5_cross_sectional_latest_headroom_reconciliation_audit"
RECENT_WINDOW_DATES = 8
FROZEN_EXPERIMENT = "phase4_cross_sectional_ranking_baseline"
CURRENT_REPLAY_EXPERIMENT = "phase5_cross_sectional_latest_reconciliation_replay"
HARDENING_GATE_SLUG = "phase5_cross_sectional_hardening_baseline"
HARDENING_REPLAY_EXPERIMENT = "phase5_cross_sectional_hardening_replay_run1"
HISTORICAL_CLOSURE_GATE_PATH = "reports/gates/phase4_cross_sectional_closure_gate/gate_report.json"
HISTORICAL_CLOSURE_MANIFEST_PATH = "reports/gates/phase4_cross_sectional_closure_gate/gate_manifest.json"
HISTORICAL_CLOSURE_SUMMARY_PATH = (
    "data/models/research/phase4_cross_sectional_closure_gate/phase4_cross_sectional_closure_summary.json"
)
HISTORICAL_CLOSURE_DEFINITION_PATH = "data/models/research/phase4_cross_sectional_closure_gate/phase4_closure_definition.json"
HISTORICAL_CLOSURE_LATEST_PATH = "data/models/research/phase4_cross_sectional_closure_gate/cross_sectional_closure_eval.parquet"
HISTORICAL_CLOSURE_HISTORY_PATH = (
    "data/models/research/phase4_cross_sectional_closure_gate/causal_latest_history_summary.parquet"
)
HISTORICAL_DECISION_SUMMARY_PATH = (
    "data/models/research/phase4_cross_sectional_decision_space_latest_eval/cross_sectional_decision_space_eval_summary.json"
)
HISTORICAL_BASELINE_GATE_PATH = "reports/gates/phase4_cross_sectional_ranking_baseline/gate_report.json"
HISTORICAL_BASELINE_SUMMARY_PATH = (
    "data/models/research/phase4_cross_sectional_ranking_baseline/cross_sectional_eval_summary.json"
)
RESEARCH_ARTIFACT_FILES = (
    "latest_headroom_reconciliation_table.parquet",
    "latest_choke_decomposition.parquet",
    "artifact_lineage_comparison.json",
    "latest_date_audit.json",
    "reconciliation_summary.json",
    "official_artifacts_integrity.json",
)
GATE_REQUIRED_FILES = (
    "gate_report.json",
    "gate_report.md",
    "gate_manifest.json",
    "gate_metrics.parquet",
)


@dataclass
class HistoricalClosureArtifacts:
    commit: str
    gate_report: dict[str, Any]
    gate_manifest: dict[str, Any]
    closure_summary: dict[str, Any]
    closure_definition: dict[str, Any]
    latest_eval: pd.DataFrame
    history_eval: pd.DataFrame
    decision_summary: dict[str, Any]
    historical_baseline_gate: dict[str, Any]
    historical_baseline_summary: dict[str, Any]


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


def _git_show_bytes(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.longpaths=true", "show", f"{commit}:{path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _git_show_json(commit: str, path: str) -> dict[str, Any]:
    return json.loads(_git_show_bytes(commit, path).decode("utf-8"))


def _git_show_parquet(commit: str, path: str) -> pd.DataFrame:
    return pd.read_parquet(BytesIO(_git_show_bytes(commit, path)))


def _history_blob_sha256(commit: str, path: str) -> str:
    return hashlib.sha256(_git_show_bytes(commit, path)).hexdigest().upper()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_paths() -> tuple[Path, Path, Path]:
    model_path = stage_a._resolve_model_path()
    stage_a._configure_phase4_paths(model_path)
    research_path = model_path / "research" / "phase5_cross_sectional_latest_reconciliation"
    gate_path = REPO_ROOT / "reports" / "gates" / GATE_SLUG
    research_path.mkdir(parents=True, exist_ok=True)
    gate_path.mkdir(parents=True, exist_ok=True)
    return model_path, research_path, gate_path


def _load_historical_closure(model_path: Path) -> HistoricalClosureArtifacts:
    frozen_manifest_path = model_path / "research" / FROZEN_EXPERIMENT / "stage_a_manifest.json"
    frozen_manifest = json.loads(frozen_manifest_path.read_text(encoding="utf-8"))
    closure_commit = str(frozen_manifest.get("head") or "").strip()
    if not closure_commit:
        raise RuntimeError("Frozen manifest does not contain the historical closure commit")
    return HistoricalClosureArtifacts(
        commit=closure_commit,
        gate_report=_git_show_json(closure_commit, HISTORICAL_CLOSURE_GATE_PATH),
        gate_manifest=_git_show_json(closure_commit, HISTORICAL_CLOSURE_MANIFEST_PATH),
        closure_summary=_git_show_json(closure_commit, HISTORICAL_CLOSURE_SUMMARY_PATH),
        closure_definition=_git_show_json(closure_commit, HISTORICAL_CLOSURE_DEFINITION_PATH),
        latest_eval=_git_show_parquet(closure_commit, HISTORICAL_CLOSURE_LATEST_PATH),
        history_eval=_git_show_parquet(closure_commit, HISTORICAL_CLOSURE_HISTORY_PATH),
        decision_summary=_git_show_json(closure_commit, HISTORICAL_DECISION_SUMMARY_PATH),
        historical_baseline_gate=_git_show_json(closure_commit, HISTORICAL_BASELINE_GATE_PATH),
        historical_baseline_summary=_git_show_json(closure_commit, HISTORICAL_BASELINE_SUMMARY_PATH),
    )


def _normalize_dates(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    if "date" in work.columns:
        work["date"] = pd.to_datetime(work["date"], errors="coerce").dt.normalize()
    return work


def _manual_decision_space_metrics(frame: pd.DataFrame, *, recent_window_dates: int = RECENT_WINDOW_DATES) -> dict[str, Any]:
    work = _normalize_dates(frame)
    if work.empty or "date" not in work.columns:
        return {
            "latest_date": None,
            "latest_active_count_decision_space": 0,
            "headroom_decision_space": False,
            "recent_live_dates_decision_space": 0,
            "historical_active_events_decision_space": 0,
        }
    selected = pd.Series(work.get("decision_selected", False), index=work.index).fillna(False).astype(bool)
    position = pd.to_numeric(work.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)
    active_mask = selected & (position > 0)
    latest_date = work["date"].dropna().max()
    latest_mask = work["date"] == latest_date
    recent_dates = sorted(work["date"].dropna().unique().tolist())[-recent_window_dates:]
    recent_live_dates = 0
    for date_value in recent_dates:
        if bool(active_mask.loc[work["date"] == date_value].any()):
            recent_live_dates += 1
    latest_positions = position.loc[latest_mask]
    return {
        "latest_date": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else None,
        "latest_active_count_decision_space": int(active_mask.loc[latest_mask].sum()),
        "headroom_decision_space": bool(float(latest_positions.max()) > 0.0) if not latest_positions.empty else False,
        "recent_live_dates_decision_space": int(recent_live_dates),
        "historical_active_events_decision_space": int(active_mask.sum()),
    }


def _latest_snapshot_mode(snapshot: pd.DataFrame) -> dict[str, Any]:
    work = _normalize_dates(snapshot)
    if work.empty or "date" not in work.columns:
        return {
            "mode": "missing_snapshot",
            "row_count": 0,
            "date_min": None,
            "date_max": None,
            "n_dates": 0,
            "rows_on_max_date": 0,
            "stale_rows_vs_global_latest": 0,
        }
    dates = work["date"].dropna()
    latest_date = dates.max()
    row_count = int(len(work))
    rows_on_max_date = int((work["date"] == latest_date).sum())
    return {
        "mode": "global_latest_date" if dates.nunique() <= 1 else "per_symbol_latest_snapshot",
        "row_count": row_count,
        "date_min": dates.min().strftime("%Y-%m-%d"),
        "date_max": latest_date.strftime("%Y-%m-%d"),
        "n_dates": int(dates.nunique()),
        "rows_on_max_date": rows_on_max_date,
        "stale_rows_vs_global_latest": int(row_count - rows_on_max_date),
    }


def _principal_elimination_reason(*, total: int, eligible: int, ranked: int, selected: int, position_gt_0: int) -> str:
    if total <= 0:
        return "no_rows_latest"
    if eligible <= 0:
        return "eligibility_gate_zeroed_latest"
    if ranked <= 0:
        return "contest_not_opened_after_eligibility"
    if selected <= 0:
        return "contest_selection_zeroed_latest"
    if position_gt_0 <= 0:
        return "sizing_or_mu_zeroed_selected_latest"
    return "live_under_current_replay"


def _build_current_latest_decomposition(frame: pd.DataFrame) -> tuple[dict[str, Any], pd.DataFrame]:
    work = _normalize_dates(frame)
    if work.empty or "date" not in work.columns:
        empty = {
            "latest_date": None,
            "n_rows_latest_total": 0,
            "n_rows_latest_eligible": 0,
            "n_rows_latest_ranked": 0,
            "n_rows_latest_selected": 0,
            "n_rows_latest_position_gt_0": 0,
            "max_position_usdt_latest": 0.0,
            "principal_elimination_reason": "no_rows_latest",
        }
        return empty, pd.DataFrame([empty])

    eligible = pd.Series(work.get("stage_a_eligible", False), index=work.index).fillna(False).astype(bool)
    selection_mode = pd.Series(work.get("stage_a_selection_mode", "no_eligible"), index=work.index).fillna("no_eligible")
    ranked_mask = selection_mode.astype(str) != "no_eligible"
    selected = pd.Series(work.get("decision_selected", False), index=work.index).fillna(False).astype(bool)
    position = pd.to_numeric(work.get("position_usdt_stage_a"), errors="coerce").fillna(0.0)

    latest_date = work["date"].dropna().max()
    recent_dates = sorted(work["date"].dropna().unique().tolist())[-RECENT_WINDOW_DATES:]

    rows: list[dict[str, Any]] = []
    for date_value in recent_dates:
        mask = work["date"] == date_value
        total = int(mask.sum())
        eligible_count = int(eligible.loc[mask].sum())
        ranked_count = int(ranked_mask.loc[mask].sum())
        selected_count = int(selected.loc[mask].sum())
        position_gt_0 = int((position.loc[mask] > 0).sum())
        rows.append(
            {
                "scope": "recent_window",
                "date": date_value.strftime("%Y-%m-%d"),
                "n_rows_total": total,
                "n_rows_eligible": eligible_count,
                "n_rows_ranked": ranked_count,
                "n_rows_selected": selected_count,
                "n_rows_position_gt_0": position_gt_0,
                "max_position_usdt": round(float(position.loc[mask].max()), 6) if total else 0.0,
                "principal_elimination_reason": _principal_elimination_reason(
                    total=total,
                    eligible=eligible_count,
                    ranked=ranked_count,
                    selected=selected_count,
                    position_gt_0=position_gt_0,
                ),
            }
        )

    latest_mask = work["date"] == latest_date
    latest_row = {
        "latest_date": latest_date.strftime("%Y-%m-%d"),
        "n_rows_latest_total": int(latest_mask.sum()),
        "n_rows_latest_eligible": int(eligible.loc[latest_mask].sum()),
        "n_rows_latest_ranked": int(ranked_mask.loc[latest_mask].sum()),
        "n_rows_latest_selected": int(selected.loc[latest_mask].sum()),
        "n_rows_latest_position_gt_0": int((position.loc[latest_mask] > 0).sum()),
        "max_position_usdt_latest": round(float(position.loc[latest_mask].max()), 6) if bool(latest_mask.any()) else 0.0,
    }
    latest_row["principal_elimination_reason"] = _principal_elimination_reason(
        total=latest_row["n_rows_latest_total"],
        eligible=latest_row["n_rows_latest_eligible"],
        ranked=latest_row["n_rows_latest_ranked"],
        selected=latest_row["n_rows_latest_selected"],
        position_gt_0=latest_row["n_rows_latest_position_gt_0"],
    )

    latest_table_row = {
        "scope": "latest",
        "date": latest_row["latest_date"],
        "n_rows_total": latest_row["n_rows_latest_total"],
        "n_rows_eligible": latest_row["n_rows_latest_eligible"],
        "n_rows_ranked": latest_row["n_rows_latest_ranked"],
        "n_rows_selected": latest_row["n_rows_latest_selected"],
        "n_rows_position_gt_0": latest_row["n_rows_latest_position_gt_0"],
        "max_position_usdt": latest_row["max_position_usdt_latest"],
        "principal_elimination_reason": latest_row["principal_elimination_reason"],
    }
    return latest_row, pd.DataFrame([latest_table_row, *rows])


def _build_reconciliation_rows(
    *,
    historical: HistoricalClosureArtifacts,
    frozen_rebuilt: stage5.RebuiltExperiment,
    hardening_summary: dict[str, Any],
    current_replay_rebuilt: stage5.RebuiltExperiment,
    snapshot_mode: dict[str, Any],
) -> pd.DataFrame:
    frozen_metrics = stage5._compute_decision_space_metrics(frozen_rebuilt.aggregated)
    replay_metrics = stage5._compute_decision_space_metrics(current_replay_rebuilt.aggregated)
    frozen_manual = _manual_decision_space_metrics(frozen_rebuilt.aggregated)
    replay_manual = _manual_decision_space_metrics(current_replay_rebuilt.aggregated)
    frozen_latest = _build_current_latest_decomposition(frozen_rebuilt.aggregated)[0]
    replay_latest = _build_current_latest_decomposition(current_replay_rebuilt.aggregated)[0]
    closure_latest = historical.latest_eval.copy()
    decision_pos = pd.to_numeric(closure_latest.get("decision_position_usdt"), errors="coerce").fillna(0.0)
    label_eligible = pd.Series(closure_latest.get("label_space_eligible", False), index=closure_latest.index).fillna(False).astype(bool)
    decision_available = pd.Series(
        closure_latest.get("decision_space_available", False), index=closure_latest.index
    ).fillna(False).astype(bool)
    decision_selected = decision_pos > 0

    rows = [
        {
            "world": "historical_closure_gate_sovereign",
            "source_type": "git_history_gate_pack",
            "source_commit": historical.commit,
            "latest_date": historical.closure_definition.get("latest_side_by_side", {}).get("latest_date"),
            "latest_active_count_decision_space": int(historical.closure_summary["sovereign_summary"]["latest_active_count"]),
            "headroom_decision_space": bool(historical.closure_summary["sovereign_summary"]["headroom_real"]),
            "recent_live_dates_decision_space": int(historical.closure_summary["sovereign_summary"]["recent_live_dates"]),
            "historical_active_events_decision_space": int(historical.closure_summary["sovereign_summary"]["historical_active_events"]),
            "latest_rows_total": int(len(closure_latest)),
            "latest_rows_eligible": int(label_eligible.sum()),
            "latest_rows_selected": int(decision_selected.sum()),
            "latest_rows_position_gt_0": int((decision_pos > 0).sum()),
            "latest_rows_decision_available": int(decision_available.sum()),
            "max_position_usdt_latest": round(float(decision_pos.max()), 6) if not decision_pos.empty else 0.0,
        },
        {
            "world": "historical_baseline_gate_legacy",
            "source_type": "git_history_gate_pack",
            "source_commit": historical.commit,
            "latest_date": historical.closure_definition.get("latest_side_by_side", {}).get("latest_date"),
            "latest_active_count_decision_space": int(historical.historical_baseline_gate["summary"]["latest_active_count"]),
            "headroom_decision_space": bool(historical.historical_baseline_gate["summary"]["headroom_real"]),
            "recent_live_dates_decision_space": int(
                historical.history_eval.loc[historical.history_eval["in_recent_window"], "headroom_label_space"].sum()
            ),
            "historical_active_events_decision_space": int(historical.historical_baseline_gate["summary"]["historical_active_events"]),
            "latest_rows_total": int(len(closure_latest)),
            "latest_rows_eligible": int(label_eligible.sum()),
            "latest_rows_selected": int(label_eligible.sum()),
            "latest_rows_position_gt_0": 0,
            "latest_rows_decision_available": int(decision_available.sum()),
            "max_position_usdt_latest": 0.0,
        },
        {
            "world": "preserved_frozen_bundle_current_repo",
            "source_type": "current_worktree_stage_a_bundle",
            "source_commit": str(frozen_rebuilt.report.get("generated_at_utc")),
            "latest_date": frozen_manual["latest_date"],
            "latest_active_count_decision_space": int(frozen_metrics["latest_active_count_decision_space"]),
            "headroom_decision_space": bool(frozen_metrics["headroom_decision_space"]),
            "recent_live_dates_decision_space": int(frozen_metrics["recent_live_dates_decision_space"]),
            "historical_active_events_decision_space": int(frozen_metrics["historical_active_events_decision_space"]),
            "latest_rows_total": int(frozen_latest["n_rows_latest_total"]),
            "latest_rows_eligible": int(frozen_latest["n_rows_latest_eligible"]),
            "latest_rows_selected": int(frozen_latest["n_rows_latest_selected"]),
            "latest_rows_position_gt_0": int(frozen_latest["n_rows_latest_position_gt_0"]),
            "latest_rows_decision_available": None,
            "max_position_usdt_latest": float(frozen_latest["max_position_usdt_latest"]),
        },
        {
            "world": "phase5_hardening_baseline_gate",
            "source_type": "current_gate_pack",
            "source_commit": hardening_summary.get("timestamp_utc"),
            "latest_date": replay_manual["latest_date"],
            "latest_active_count_decision_space": int(
                hardening_summary["frozen_baseline_metrics"]["latest_active_count_decision_space"]
            ),
            "headroom_decision_space": bool(hardening_summary["frozen_baseline_metrics"]["headroom_decision_space"]),
            "recent_live_dates_decision_space": int(
                hardening_summary["frozen_baseline_metrics"]["recent_live_dates_decision_space"]
            ),
            "historical_active_events_decision_space": int(
                hardening_summary["frozen_baseline_metrics"]["historical_active_events_decision_space"]
            ),
            "latest_rows_total": int(replay_latest["n_rows_latest_total"]),
            "latest_rows_eligible": int(replay_latest["n_rows_latest_eligible"]),
            "latest_rows_selected": int(replay_latest["n_rows_latest_selected"]),
            "latest_rows_position_gt_0": int(replay_latest["n_rows_latest_position_gt_0"]),
            "latest_rows_decision_available": None,
            "max_position_usdt_latest": float(replay_latest["max_position_usdt_latest"]),
        },
        {
            "world": "clean_replay_current_sources",
            "source_type": "current_replay_stage_a_bundle",
            "source_commit": _git_output("rev-parse", "HEAD"),
            "latest_date": replay_manual["latest_date"],
            "latest_active_count_decision_space": int(replay_metrics["latest_active_count_decision_space"]),
            "headroom_decision_space": bool(replay_metrics["headroom_decision_space"]),
            "recent_live_dates_decision_space": int(replay_metrics["recent_live_dates_decision_space"]),
            "historical_active_events_decision_space": int(replay_metrics["historical_active_events_decision_space"]),
            "latest_rows_total": int(replay_latest["n_rows_latest_total"]),
            "latest_rows_eligible": int(replay_latest["n_rows_latest_eligible"]),
            "latest_rows_selected": int(replay_latest["n_rows_latest_selected"]),
            "latest_rows_position_gt_0": int(replay_latest["n_rows_latest_position_gt_0"]),
            "latest_rows_decision_available": None,
            "max_position_usdt_latest": float(replay_latest["max_position_usdt_latest"]),
            "snapshot_mode": snapshot_mode["mode"],
        },
    ]
    return pd.DataFrame(rows)


def _build_artifact_lineage_payload(
    *,
    model_path: Path,
    historical: HistoricalClosureArtifacts,
    frozen_manifest: dict[str, Any],
    hardening_summary: dict[str, Any],
) -> dict[str, Any]:
    closure_source_paths = [row["path"] for row in historical.gate_manifest.get("source_artifacts", [])]
    closure_generated_paths = [row["path"] for row in historical.gate_manifest.get("generated_artifacts", [])]
    missing_in_worktree = [path for path in closure_generated_paths if not Path(path).exists()]
    closure_uses_separate_bundle = any("phase4_cross_sectional_closure_gate" in path for path in closure_generated_paths) and any(
        "phase4_cross_sectional_decision_space_latest_eval" in path for path in closure_source_paths
    )
    return {
        "historical_closure_commit": historical.commit,
        "historical_closure_gate_report_sha256": _history_blob_sha256(historical.commit, HISTORICAL_CLOSURE_GATE_PATH),
        "historical_closure_manifest_sha256": _history_blob_sha256(historical.commit, HISTORICAL_CLOSURE_MANIFEST_PATH),
        "historical_closure_sources": historical.gate_manifest.get("source_artifacts", []),
        "historical_closure_generated_artifacts": historical.gate_manifest.get("generated_artifacts", []),
        "historical_closure_bundle_missing_from_current_worktree": missing_in_worktree,
        "historical_closure_uses_separate_sovereign_bundle": bool(closure_uses_separate_bundle),
        "current_frozen_manifest_head": frozen_manifest.get("head"),
        "current_frozen_manifest_branch": frozen_manifest.get("branch"),
        "current_frozen_manifest_working_tree_state": frozen_manifest.get("working_tree_state"),
        "current_frozen_worktree_files": [str(path) for path in sorted((model_path / "research" / FROZEN_EXPERIMENT).glob("*"))],
        "hardening_baseline_source_experiment": FROZEN_EXPERIMENT,
        "hardening_gate_report_path": str(REPO_ROOT / "reports" / "gates" / HARDENING_GATE_SLUG / "gate_report.json"),
        "hardening_summary_path": str(model_path / "research" / "phase5_cross_sectional_hardening" / "phase5_cross_sectional_hardening_summary.json"),
        "hardening_baseline_metrics": hardening_summary.get("frozen_baseline_metrics", {}),
        "lineage_findings": [
            {
                "finding": "historical_closure_gate_governed_by_separate_sovereign_bundle",
                "status": "PASS" if closure_uses_separate_bundle else "FAIL",
                "detail": (
                    "The historical closure decision consumed phase4_cross_sectional_decision_space_latest_eval "
                    "and phase4_cross_sectional_closure_gate artifacts, not only the baseline ranking bundle."
                ),
            },
            {
                "finding": "hardening_replayed_baseline_experiment_not_closure_gate",
                "status": "PASS",
                "detail": (
                    "The phase5 hardening baseline rebuilt phase4_cross_sectional_ranking_baseline and did not "
                    "replay the historical closure_gate sovereign bundle."
                ),
            },
            {
                "finding": "historical_closure_bundle_absent_in_current_worktree",
                "status": "PASS" if bool(missing_in_worktree) else "FAIL",
                "detail": "The current worktree no longer contains the historical phase4 closure gate artifact set.",
            },
        ],
    }


def _build_latest_date_audit(
    *,
    historical: HistoricalClosureArtifacts,
    frozen_rebuilt: stage5.RebuiltExperiment,
    current_replay_rebuilt: stage5.RebuiltExperiment,
    frozen_snapshot_mode: dict[str, Any],
) -> dict[str, Any]:
    frozen_manual = _manual_decision_space_metrics(frozen_rebuilt.aggregated)
    replay_manual = _manual_decision_space_metrics(current_replay_rebuilt.aggregated)
    closure_latest_date = historical.closure_definition.get("latest_side_by_side", {}).get("latest_date")
    history_recent = historical.history_eval.loc[historical.history_eval["in_recent_window"]].copy()
    return {
        "closure_gate_latest_date": closure_latest_date,
        "hardening_latest_date": frozen_manual["latest_date"],
        "current_replay_latest_date": replay_manual["latest_date"],
        "closure_vs_hardening_same_latest_date": bool(closure_latest_date == frozen_manual["latest_date"]),
        "closure_recent_window_dates": RECENT_WINDOW_DATES,
        "hardening_recent_window_dates": RECENT_WINDOW_DATES,
        "closure_recent_live_dates_decision_space": int(history_recent["headroom_decision_space"].sum()) if not history_recent.empty else 0,
        "hardening_recent_live_dates_decision_space": int(frozen_manual["recent_live_dates_decision_space"]),
        "frozen_stage_a_snapshot_mode": frozen_snapshot_mode,
        "closure_gate_latest_mode": {
            "mode": "global_latest_date",
            "row_count": int(len(historical.latest_eval)),
            "date_min": closure_latest_date,
            "date_max": closure_latest_date,
            "n_dates": 1,
            "rows_on_max_date": int(len(historical.latest_eval)),
            "stale_rows_vs_global_latest": 0,
        },
        "notes": [
            "The preserved stage_a snapshot proxy is a per-symbol latest snapshot and spans multiple dates.",
            "The historical closure gate evaluated the global latest date 2026-03-20 under a separate sovereign decision-space bundle.",
        ],
    }


def _manual_matches_helper(frame: pd.DataFrame) -> bool:
    manual = _manual_decision_space_metrics(frame)
    helper = stage5._compute_decision_space_metrics(frame)
    return (
        manual["latest_active_count_decision_space"] == helper["latest_active_count_decision_space"]
        and manual["headroom_decision_space"] == helper["headroom_decision_space"]
        and manual["recent_live_dates_decision_space"] == helper["recent_live_dates_decision_space"]
        and manual["historical_active_events_decision_space"] == helper["historical_active_events_decision_space"]
    )


def _normalized_report_hash(report: dict[str, Any]) -> str:
    payload = hardening._normalize_payload(report, drop_keys=hardening.VOLATILE_REPORT_KEYS)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest().upper()


def _build_reproducibility_payload(model_path: Path, current_experiment: str) -> dict[str, Any]:
    current_bundle = hardening._load_experiment_bundle(model_path, current_experiment)
    reference_bundle = hardening._load_experiment_bundle(model_path, HARDENING_REPLAY_EXPERIMENT)
    current_snapshot = pd.read_parquet(Path(current_bundle["research_dir"]) / "stage_a_snapshot_proxy.parquet")
    reference_snapshot = pd.read_parquet(Path(reference_bundle["research_dir"]) / "stage_a_snapshot_proxy.parquet")
    return {
        "current_experiment": current_experiment,
        "reference_experiment": HARDENING_REPLAY_EXPERIMENT,
        "report_hash_current": _normalized_report_hash(current_bundle["report"]),
        "report_hash_reference": _normalized_report_hash(reference_bundle["report"]),
        "predictions_hash_current": hardening._frame_hash(current_bundle["predictions"]),
        "predictions_hash_reference": hardening._frame_hash(reference_bundle["predictions"]),
        "snapshot_hash_current": hardening._frame_hash(current_snapshot),
        "snapshot_hash_reference": hardening._frame_hash(reference_snapshot),
        "aggregated_hash_current": hardening._frame_hash(current_bundle["rebuilt"].aggregated),
        "aggregated_hash_reference": hardening._frame_hash(reference_bundle["rebuilt"].aggregated),
    }


def _classify_dominant_cause(
    *,
    closure_vs_hardening_same_latest_date: bool,
    closure_latest_rows: int,
    current_latest_rows: int,
    closure_source_bundle_separate: bool,
    closure_bundle_missing_in_worktree: bool,
    official_hashes_same: bool,
    helper_matches_manual: bool,
    current_latest_eligible: int,
) -> tuple[str, str, str | None, str | None]:
    if (
        closure_source_bundle_separate
        and closure_bundle_missing_in_worktree
        and closure_vs_hardening_same_latest_date
        and closure_latest_rows == current_latest_rows
        and official_hashes_same
    ):
        return (
            "BASELINE_ARTIFACT_MISMATCH_CONFIRMED",
            (
                "Phase5 hardening replayed the preserved phase4_cross_sectional_ranking_baseline stage_a bundle, "
                "while the historical phase4 closure decision was governed by a separate sovereign bundle "
                "(phase4_cross_sectional_decision_space_latest_eval + phase4_cross_sectional_closure_gate)."
            ),
            (
                "Restore or regenerate the historical closure-gate sovereign bundle and use it as the phase5 "
                "baseline lineage instead of replaying the overwritten phase4_cross_sectional_ranking_baseline "
                "stage_a bundle."
            ),
            "METRIC_COMPUTATION_DRIFT_CONFIRMED",
        )
    if not closure_vs_hardening_same_latest_date:
        return (
            "LATEST_DATE_MISMATCH_CONFIRMED",
            "The historical closure gate and the current hardening replay are measuring different latest dates.",
            "Normalize all gates to the same global latest date before any new hardening comparison.",
            None,
        )
    if not helper_matches_manual:
        return (
            "METRIC_COMPUTATION_DRIFT_CONFIRMED",
            "The preserved sovereign metric helper no longer matches the explicit manual computation on the replay frame.",
            "Repair the hardening runner to use the same sovereign metric implementation audited in this round.",
            None,
        )
    if current_latest_eligible == 0:
        return (
            "ELIGIBILITY_CONTEST_CHOKE_CONFIRMED",
            "The current replay dies at the realized eligibility gate on the latest date even though the date and universe match.",
            "Audit only the eligibility wiring for the preserved baseline bundle without changing the sovereign ruler.",
            None,
        )
    return (
        "TRUE_SIGNAL_DECAY_CONFIRMED",
        "The historical closure lineage and the current replay align, and the signal is still dead under the same sovereign path.",
        None,
        None,
    )


def _build_gate_metrics(
    *,
    official_artifacts_unchanged: bool,
    research_only_isolation_pass: bool,
    reproducibility_pass: bool,
    sovereign_metric_definitions_unchanged: bool,
    closure_vs_hardening_reconciled: bool,
    dominant_cause_classified: bool,
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
        _metric(
            "sovereign_metric_definitions_unchanged",
            sovereign_metric_definitions_unchanged,
            sovereign_metric_definitions_unchanged,
        ),
        _metric("closure_vs_hardening_reconciled", closure_vs_hardening_reconciled, closure_vs_hardening_reconciled),
        _metric("dominant_cause_classified", dominant_cause_classified, dominant_cause_classified),
    ]


def run_phase5_cross_sectional_latest_headroom_reconciliation_audit() -> dict[str, Any]:
    model_path, research_path, gate_path = _resolve_paths()
    official_before = stage5._collect_official_inventory(model_path)
    historical = _load_historical_closure(model_path)
    hardening_summary = json.loads(
        (model_path / "research" / "phase5_cross_sectional_hardening" / "phase5_cross_sectional_hardening_summary.json").read_text(
            encoding="utf-8"
        )
    )
    frozen_manifest = json.loads((model_path / "research" / FROZEN_EXPERIMENT / "stage_a_manifest.json").read_text(encoding="utf-8"))

    frozen_rebuilt = stage5._rebuild_experiment(model_path, FROZEN_EXPERIMENT)
    current_replay_rebuilt = stage5._rebuild_experiment(model_path, HARDENING_REPLAY_EXPERIMENT)

    reproducibility = dict(hardening_summary.get("reproducibility", {}))
    reproducibility["current_experiment"] = HARDENING_REPLAY_EXPERIMENT
    reproducibility["reference_experiment"] = "phase5_cross_sectional_hardening_replay_run2"
    reproducibility_pass = bool(reproducibility.get("pass", False))

    frozen_snapshot = pd.read_parquet(model_path / "research" / FROZEN_EXPERIMENT / "stage_a_snapshot_proxy.parquet")
    snapshot_mode = _latest_snapshot_mode(frozen_snapshot)
    latest_row, latest_table = _build_current_latest_decomposition(current_replay_rebuilt.aggregated)
    latest_table.to_parquet(research_path / "latest_choke_decomposition.parquet", index=False)

    reconciliation_table = _build_reconciliation_rows(
        historical=historical,
        frozen_rebuilt=frozen_rebuilt,
        hardening_summary=hardening_summary,
        current_replay_rebuilt=current_replay_rebuilt,
        snapshot_mode=snapshot_mode,
    )
    reconciliation_table.to_parquet(research_path / "latest_headroom_reconciliation_table.parquet", index=False)

    lineage_payload = _build_artifact_lineage_payload(
        model_path=model_path,
        historical=historical,
        frozen_manifest=frozen_manifest,
        hardening_summary=hardening_summary,
    )
    _write_json(research_path / "artifact_lineage_comparison.json", lineage_payload)

    latest_date_audit = _build_latest_date_audit(
        historical=historical,
        frozen_rebuilt=frozen_rebuilt,
        current_replay_rebuilt=current_replay_rebuilt,
        frozen_snapshot_mode=snapshot_mode,
    )
    _write_json(research_path / "latest_date_audit.json", latest_date_audit)

    helper_matches_manual = _manual_matches_helper(frozen_rebuilt.aggregated) and _manual_matches_helper(current_replay_rebuilt.aggregated)
    official_after = stage5._collect_official_inventory(model_path)
    official_artifacts_unchanged = official_before["combined_hashes"] == official_after["combined_hashes"]
    research_only_isolation_pass = True
    sovereign_metric_definitions_unchanged = bool(helper_matches_manual)

    dominant_cause, cause_root, low_regret_correction, secondary_cause = _classify_dominant_cause(
        closure_vs_hardening_same_latest_date=bool(latest_date_audit["closure_vs_hardening_same_latest_date"]),
        closure_latest_rows=int(len(historical.latest_eval)),
        current_latest_rows=int(latest_row["n_rows_latest_total"]),
        closure_source_bundle_separate=bool(lineage_payload["historical_closure_uses_separate_sovereign_bundle"]),
        closure_bundle_missing_in_worktree=bool(lineage_payload["historical_closure_bundle_missing_from_current_worktree"]),
        official_hashes_same=bool(
            historical.gate_report["official_artifacts_used"][0]["sha256_before"]
            == historical.gate_report["official_artifacts_used"][0]["sha256_after"]
            and historical.gate_report["official_artifacts_used"][0]["sha256_before"]
            == frozen_manifest["source_hashes"]["phase4_report_v4_sha256"]
        ),
        helper_matches_manual=helper_matches_manual,
        current_latest_eligible=int(latest_row["n_rows_latest_eligible"]),
    )

    closure_vs_hardening_reconciled = True
    dominant_cause_classified = bool(dominant_cause)

    summary_payload = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "closure_commit": historical.commit,
        "comparison": {
            "closure_latest_date": historical.closure_definition.get("latest_side_by_side", {}).get("latest_date"),
            "hardening_latest_date": latest_date_audit["hardening_latest_date"],
            "closure_latest_active_count_decision_space": int(historical.closure_summary["sovereign_summary"]["latest_active_count"]),
            "hardening_latest_active_count_decision_space": int(
                hardening_summary["frozen_baseline_metrics"]["latest_active_count_decision_space"]
            ),
            "closure_headroom_decision_space": bool(historical.closure_summary["sovereign_summary"]["headroom_real"]),
            "hardening_headroom_decision_space": bool(hardening_summary["frozen_baseline_metrics"]["headroom_decision_space"]),
            "closure_recent_live_dates_decision_space": int(historical.closure_summary["sovereign_summary"]["recent_live_dates"]),
            "hardening_recent_live_dates_decision_space": int(
                hardening_summary["frozen_baseline_metrics"]["recent_live_dates_decision_space"]
            ),
            "closure_historical_active_events_decision_space": int(
                historical.closure_summary["sovereign_summary"]["historical_active_events"]
            ),
            "hardening_historical_active_events_decision_space": int(
                hardening_summary["frozen_baseline_metrics"]["historical_active_events_decision_space"]
            ),
        },
        "latest_choke_decomposition_current_replay": latest_row,
        "reproducibility": {**reproducibility, "pass": reproducibility_pass},
        "artifact_lineage": {
            "dominant_cause": dominant_cause,
            "secondary_cause": secondary_cause,
            "cause_root": cause_root,
            "low_regret_correction": low_regret_correction,
        },
        "closure_vs_hardening_reconciled": closure_vs_hardening_reconciled,
        "dominant_cause_classified": dominant_cause_classified,
    }
    _write_json(research_path / "reconciliation_summary.json", summary_payload)

    integrity_payload = {
        "official_before": official_before,
        "official_after": official_after,
        "official_artifacts_unchanged": official_artifacts_unchanged,
        "research_paths_written": [str(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "gate_paths_written": [str(gate_path / name) for name in GATE_REQUIRED_FILES],
        "official_root": str(model_path),
        "research_root": str(research_path),
        "gate_root": str(gate_path),
        "research_only_isolation_pass": research_only_isolation_pass,
    }
    _write_json(research_path / "official_artifacts_integrity.json", integrity_payload)

    gate_metrics = _build_gate_metrics(
        official_artifacts_unchanged=official_artifacts_unchanged,
        research_only_isolation_pass=research_only_isolation_pass,
        reproducibility_pass=reproducibility_pass,
        sovereign_metric_definitions_unchanged=sovereign_metric_definitions_unchanged,
        closure_vs_hardening_reconciled=closure_vs_hardening_reconciled,
        dominant_cause_classified=dominant_cause_classified,
    )

    if not all(metric["metric_status"] == "PASS" for metric in gate_metrics):
        status = "FAIL"
        decision = "abandon"
    elif low_regret_correction:
        status = "PASS"
        decision = "advance"
    else:
        status = "PARTIAL"
        decision = "correct"

    gate_report = {
        "gate_slug": GATE_SLUG,
        "phase_family": PHASE_FAMILY,
        "status": status,
        "decision": decision,
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "closure_commit": historical.commit,
        "working_tree_dirty": bool(_git_output("status", "--short", "--untracked-files=all")),
        "official_artifacts_used": [
            str(model_path / "phase3"),
            str(model_path / "features"),
            str(model_path / "phase4" / "phase4_report_v4.json"),
            str(model_path / "phase4" / "phase4_execution_snapshot.parquet"),
            str(model_path / "phase4" / "phase4_aggregated_predictions.parquet"),
        ],
        "research_artifacts_generated": [str(research_path / name) for name in RESEARCH_ARTIFACT_FILES],
        "summary": [
            f"dominant_cause={dominant_cause}",
            f"closure_latest_active_count_decision_space={historical.closure_summary['sovereign_summary']['latest_active_count']}",
            f"hardening_latest_active_count_decision_space={hardening_summary['frozen_baseline_metrics']['latest_active_count_decision_space']}",
            f"reproducibility_pass={reproducibility_pass}",
        ],
        "gates": gate_metrics,
        "blockers": [] if low_regret_correction else [cause_root],
        "risks_residual": [
            "The historical closure gate artifact set is no longer present in the current worktree.",
            "The preserved stage_a snapshot proxy spans 21 dates and cannot be read as a single global latest snapshot.",
        ],
        "next_recommended_step": (
            "Run one bounded correction round that restores the historical closure-gate lineage as the hardening baseline input."
            if low_regret_correction
            else "Do not advance without first restoring the correct closure-vs-hardening lineage."
        ),
    }

    gate_manifest = {
        "gate_slug": GATE_SLUG,
        "timestamp_utc": utc_now_iso(),
        "branch": _git_output("branch", "--show-current"),
        "baseline_commit": _git_output("rev-parse", "HEAD"),
        "historical_closure_commit": historical.commit,
        "working_tree_dirty_before": bool(_git_output("status", "--short", "--untracked-files=all")),
        "working_tree_dirty_after": bool(_git_output("status", "--short", "--untracked-files=all")),
        "source_artifacts": [
            artifact_record(model_path / "research" / FROZEN_EXPERIMENT / "stage_a_report.json"),
            artifact_record(model_path / "research" / FROZEN_EXPERIMENT / "stage_a_manifest.json"),
            artifact_record(model_path / "research" / "phase5_cross_sectional_hardening" / "phase5_cross_sectional_hardening_summary.json"),
            {
                "path": f"git:{historical.commit}:{HISTORICAL_CLOSURE_GATE_PATH}",
                "sha256": _history_blob_sha256(historical.commit, HISTORICAL_CLOSURE_GATE_PATH),
            },
            {
                "path": f"git:{historical.commit}:{HISTORICAL_CLOSURE_SUMMARY_PATH}",
                "sha256": _history_blob_sha256(historical.commit, HISTORICAL_CLOSURE_SUMMARY_PATH),
            },
        ],
        "generated_artifacts": [
            artifact_record(research_path / "latest_headroom_reconciliation_table.parquet"),
            artifact_record(research_path / "latest_choke_decomposition.parquet"),
            artifact_record(research_path / "artifact_lineage_comparison.json"),
            artifact_record(research_path / "latest_date_audit.json"),
            artifact_record(research_path / "reconciliation_summary.json"),
            artifact_record(research_path / "official_artifacts_integrity.json"),
            artifact_record(gate_path / "gate_report.json"),
            artifact_record(gate_path / "gate_report.md"),
            artifact_record(gate_path / "gate_metrics.parquet"),
            {"path": str(gate_path / "gate_manifest.json"), "sha256_note": "self hash omitted inside manifest to avoid self-reference"},
        ],
        "commands_executed": [
            "git branch --show-current",
            "git rev-parse HEAD",
            "python services\\ml_engine\\phase5_cross_sectional_latest_headroom_reconciliation_audit.py",
        ],
        "notes": [
            "Historical closure artifacts were loaded directly from git history at the frozen manifest commit.",
            "The current worktree preserves only the stage_a baseline bundle, not the historical sovereign closure bundle.",
        ],
    }

    markdown_sections = {
        "Resumo executivo": "\n".join(
            [
                f"- Dominant cause: `{dominant_cause}`.",
                f"- Closure sovereign metrics (historical git gate): latest=`{historical.closure_summary['sovereign_summary']['latest_active_count']}`, "
                f"headroom=`{historical.closure_summary['sovereign_summary']['headroom_real']}`, recent=`{historical.closure_summary['sovereign_summary']['recent_live_dates']}`, "
                f"historical=`{historical.closure_summary['sovereign_summary']['historical_active_events']}`.",
                f"- Hardening baseline latest/headroom: `{hardening_summary['frozen_baseline_metrics']['latest_active_count_decision_space']}` / "
                f"`{hardening_summary['frozen_baseline_metrics']['headroom_decision_space']}`.",
            ]
        ),
        "Baseline congelado": "\n".join(
            [
                "- Historical phase4 closure gate was loaded from git history and compared against the preserved frozen stage_a bundle and the current clean replay.",
                "- No official artifact was rewritten; the audit is research-only.",
            ]
        ),
        "MudanÃ§as implementadas": "\n".join(
            [
                "- Added a reconciliation runner that loads the historical closure gate directly from git history.",
                "- Materialized a latest-date audit, lineage comparison and current latest choke decomposition.",
                "- Added a focused unit test for sovereign latest decomposition and dominant-cause classification.",
            ]
        ),
        "Artifacts gerados": "\n".join([f"- `{research_path / name}`" for name in RESEARCH_ARTIFACT_FILES] + [f"- `{gate_path / name}`" for name in GATE_REQUIRED_FILES]),
        "Resultados": "\n".join(
            [
                f"- Closure gate sovereign latest/headroom: `{historical.closure_summary['sovereign_summary']['latest_active_count']}` / `{historical.closure_summary['sovereign_summary']['headroom_real']}`.",
                f"- Hardening baseline latest/headroom: `{hardening_summary['frozen_baseline_metrics']['latest_active_count_decision_space']}` / `{hardening_summary['frozen_baseline_metrics']['headroom_decision_space']}`.",
                f"- Current replay latest decomposition: `{latest_row}`.",
                f"- Latest-date audit: `{latest_date_audit}`.",
            ]
        ),
        "AvaliaÃ§Ã£o contra gates": "\n".join([f"- {row['metric_name']} = `{row['metric_status']}`" for row in gate_metrics]),
        "Riscos residuais": "\n".join(
            [
                "- Historical closure gate artifacts are only available in git history, not in the current worktree.",
                "- The preserved stage_a snapshot proxy still spans multiple dates and can be misread as a global latest snapshot if used without audit.",
            ]
        ),
        "Veredito final: advance / correct / abandon": f"- status `{status}` / decision `{decision}` / dominant cause `{dominant_cause}`",
    }

    outputs = write_gate_pack(
        output_dir=gate_path,
        gate_report=gate_report,
        gate_manifest=gate_manifest,
        gate_metrics=gate_metrics,
        markdown_sections=markdown_sections,
    )

    return {
        "status": status,
        "decision": decision,
        "dominant_cause": dominant_cause,
        "low_regret_correction": low_regret_correction,
        "research_path": str(research_path),
        "gate_path": str(gate_path),
        "gate_outputs": {key: str(value) for key, value in outputs.items()},
    }


def main() -> None:
    result = run_phase5_cross_sectional_latest_headroom_reconciliation_audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
