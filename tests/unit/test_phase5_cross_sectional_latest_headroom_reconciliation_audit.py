from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_cross_sectional_latest_headroom_reconciliation_audit as audit


@dataclass
class _DummyRebuilt:
    aggregated: pd.DataFrame
    report: dict


def test_git_show_bytes_enables_windows_longpaths(monkeypatch) -> None:
    calls = []

    class _Result:
        stdout = b"{}"

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _Result()

    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    assert audit._git_show_bytes("abc123", "very/long/path.json") == b"{}"
    assert calls[0][0] == ["git", "-c", "core.longpaths=true", "show", "abc123:very/long/path.json"]
    assert calls[0][1]["check"] is True


def test_manual_decision_space_metrics_match_latest_and_recent_window() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-03-18", "2026-03-18", "2026-03-19", "2026-03-20"],
            "decision_selected": [True, False, True, False],
            "position_usdt_stage_a": [100.0, 0.0, 50.0, 0.0],
        }
    )

    metrics = audit._manual_decision_space_metrics(frame, recent_window_dates=3)

    assert metrics["latest_date"] == "2026-03-20"
    assert metrics["latest_active_count_decision_space"] == 0
    assert metrics["headroom_decision_space"] is False
    assert metrics["recent_live_dates_decision_space"] == 2
    assert metrics["historical_active_events_decision_space"] == 2


def test_build_current_latest_decomposition_flags_eligibility_choke() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-03-19", "2026-03-19", "2026-03-20", "2026-03-20"],
            "stage_a_eligible": [True, True, False, False],
            "stage_a_selection_mode": ["cluster_local_top1", "not_selected", "no_eligible", "no_eligible"],
            "decision_selected": [True, False, False, False],
            "position_usdt_stage_a": [100.0, 0.0, 0.0, 0.0],
        }
    )

    latest_row, table = audit._build_current_latest_decomposition(frame)

    assert latest_row["latest_date"] == "2026-03-20"
    assert latest_row["n_rows_latest_total"] == 2
    assert latest_row["n_rows_latest_eligible"] == 0
    assert latest_row["n_rows_latest_ranked"] == 0
    assert latest_row["n_rows_latest_selected"] == 0
    assert latest_row["n_rows_latest_position_gt_0"] == 0
    assert latest_row["principal_elimination_reason"] == "eligibility_gate_zeroed_latest"
    assert set(table["scope"]) == {"latest", "recent_window"}


def test_classify_dominant_cause_prefers_artifact_mismatch_when_lineage_diverges() -> None:
    dominant, cause_root, correction, secondary = audit._classify_dominant_cause(
        closure_vs_hardening_same_latest_date=True,
        closure_latest_rows=9,
        current_latest_rows=9,
        closure_source_bundle_separate=True,
        closure_bundle_missing_in_worktree=True,
        official_hashes_same=True,
        helper_matches_manual=True,
        current_latest_eligible=0,
    )

    assert dominant == "BASELINE_ARTIFACT_MISMATCH_CONFIRMED"
    assert "separate sovereign bundle" in cause_root
    assert correction is not None
    assert secondary == "METRIC_COMPUTATION_DRIFT_CONFIRMED"


def test_build_reconciliation_rows_uses_manual_latest_date_not_helper_field() -> None:
    historical = audit.HistoricalClosureArtifacts(
        commit="cb692cc4e37ec897d5265d7af0881a0f8986821a",
        gate_report={},
        gate_manifest={"source_artifacts": [], "generated_artifacts": []},
        closure_summary={
            "sovereign_summary": {
                "latest_active_count": 2,
                "headroom_real": True,
                "recent_live_dates": 8,
                "historical_active_events": 3939,
            }
        },
        closure_definition={"latest_side_by_side": {"latest_date": "2026-03-20"}},
        latest_eval=pd.DataFrame(
            {
                "date": ["2026-03-20", "2026-03-20"],
                "label_space_eligible": [False, False],
                "decision_space_available": [True, True],
                "decision_position_usdt": [100.0, 0.0],
            }
        ),
        history_eval=pd.DataFrame({"in_recent_window": [True, True], "headroom_label_space": [False, True]}),
        decision_summary={},
        historical_baseline_gate={"summary": {"latest_active_count": 0, "headroom_real": False, "historical_active_events": 1290}},
        historical_baseline_summary={},
    )
    aggregated = pd.DataFrame(
        {
            "date": ["2026-03-19", "2026-03-20"],
            "decision_selected": [True, False],
            "position_usdt_stage_a": [10.0, 0.0],
            "stage_a_eligible": [True, False],
            "stage_a_selection_mode": ["cluster_local_top1", "no_eligible"],
        }
    )
    hardening_summary = {
        "timestamp_utc": "2026-04-04T00:00:00+00:00",
        "frozen_baseline_metrics": {
            "latest_active_count_decision_space": 0,
            "headroom_decision_space": False,
            "recent_live_dates_decision_space": 1,
            "historical_active_events_decision_space": 1,
        },
    }

    rows = audit._build_reconciliation_rows(
        historical=historical,
        frozen_rebuilt=_DummyRebuilt(aggregated=aggregated, report={"generated_at_utc": "2026-04-04T00:00:00+00:00"}),
        hardening_summary=hardening_summary,
        current_replay_rebuilt=_DummyRebuilt(aggregated=aggregated, report={}),
        snapshot_mode={"mode": "per_symbol_latest_snapshot"},
    )

    assert rows.loc[rows["world"] == "preserved_frozen_bundle_current_repo", "latest_date"].iat[0] == "2026-03-20"
    assert rows.loc[rows["world"] == "clean_replay_current_sources", "latest_date"].iat[0] == "2026-03-20"
