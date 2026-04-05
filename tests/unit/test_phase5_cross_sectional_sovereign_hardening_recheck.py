from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_ENGINE = REPO_ROOT / "services" / "ml_engine"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ML_ENGINE) not in sys.path:
    sys.path.insert(0, str(ML_ENGINE))

import phase5_cross_sectional_sovereign_hardening_recheck as recheck


def test_explicit_sovereign_metrics_follow_decision_position_ruler() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-03-19", "2026-03-19", "2026-03-20"],
            "decision_selected": [True, True, True],
            "decision_position_usdt": [100.0, 0.0, 0.0],
            "position_usdt_stage_a": [100.0, 0.0, 0.0],
        }
    )

    metrics = recheck._explicit_sovereign_metrics(frame)

    assert metrics["latest_active_count_decision_space"] == 0
    assert metrics["headroom_decision_space"] is False
    assert metrics["recent_live_dates_decision_space"] == 1
    assert metrics["historical_active_events_decision_space"] == 1


def test_validate_restored_bundle_flags_missing_files(tmp_path: Path) -> None:
    root = tmp_path / "restored"
    for _, relative_target in recheck.restore.RESTORE_SOURCE_TARGETS:
        path = root / relative_target
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix.lower() == ".parquet":
            pd.DataFrame({"x": [1]}).to_parquet(path, index=False)
        else:
            path.write_text("{}", encoding="utf-8")
    (root / "restored_bundle_manifest.json").write_text("{}", encoding="utf-8")

    missing_target = root / "phase4_cross_sectional_closure_gate" / "cross_sectional_closure_eval.parquet"
    missing_target.unlink()
    result = recheck._validate_restored_bundle(root)

    assert result["pass"] is False
    assert any(item.startswith("missing:phase4_cross_sectional_closure_gate/cross_sectional_closure_eval.parquet") for item in result["issues"])


def test_classify_recheck_returns_mixed_for_alive_but_negative_sharpe() -> None:
    status, decision, classification, fragilities = recheck._classify_recheck(
        integrity_pass=True,
        lineage_anchor_correct=True,
        recheck_row={
            "latest_active_count_decision_space": 2,
            "headroom_decision_space": True,
            "sharpe_operational": -0.2,
            "dsr_honest": 0.0,
            "subperiods_positive": 3,
        },
        regime_summary={"subperiods_tested": 6, "negative_slices": ["2024+"]},
        threshold_rows=[{"latest_active_count_decision_space": 1, "headroom_decision_space": True}],
        friction_rows=[{"sharpe_operational": -0.3}],
        snapshot_guard_pass=True,
        gate_pack_complete=True,
    )

    assert status == "PARTIAL"
    assert decision == "correct"
    assert classification == "SOVEREIGN_HARDENING_MIXED"
    assert "negative_base_sharpe" in fragilities


def test_classify_recheck_returns_fail_when_latest_headroom_dead() -> None:
    status, decision, classification, fragilities = recheck._classify_recheck(
        integrity_pass=True,
        lineage_anchor_correct=True,
        recheck_row={
            "latest_active_count_decision_space": 0,
            "headroom_decision_space": False,
            "sharpe_operational": 1.0,
            "dsr_honest": 1.0,
            "subperiods_positive": 6,
        },
        regime_summary={"subperiods_tested": 6, "negative_slices": []},
        threshold_rows=[],
        friction_rows=[],
        snapshot_guard_pass=True,
        gate_pack_complete=True,
    )

    assert status == "FAIL"
    assert decision == "abandon"
    assert classification == "SOVEREIGN_HARDENING_FAILS"
    assert fragilities == ["latest_headroom_dead_even_with_sovereign_lineage"]
