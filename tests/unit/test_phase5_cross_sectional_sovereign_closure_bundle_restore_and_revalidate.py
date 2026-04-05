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

import phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate as restore


def test_classify_equivalence_exact_vs_semantic_vs_failed() -> None:
    historical = {
        "latest_date": "2026-03-20",
        "latest_active_count_decision_space": 2,
        "headroom_decision_space": True,
        "recent_live_dates_decision_space": 8,
        "historical_active_events_decision_space": 3939,
        "latest_selected_symbols": ["ENA", "TAO"],
    }

    assert (
        restore._classify_equivalence(
            restore_all_byte_exact=True,
            restored_metrics=historical,
            replay_metrics=historical,
            historical_metrics=historical,
        )
        == "EXACT_RESTORE"
    )
    assert (
        restore._classify_equivalence(
            restore_all_byte_exact=False,
            restored_metrics=historical,
            replay_metrics=historical,
            historical_metrics=historical,
        )
        == "SEMANTICALLY_EQUIVALENT_RESTORE"
    )
    failed = dict(historical)
    failed["latest_active_count_decision_space"] = 1
    assert (
        restore._classify_equivalence(
            restore_all_byte_exact=True,
            restored_metrics=failed,
            replay_metrics=historical,
            historical_metrics=historical,
        )
        == "FAILED_RESTORE"
    )


def test_build_historical_decision_space_frame_respects_local_and_fallback_selection() -> None:
    predictions = pd.DataFrame(
        {
            "date": ["2026-03-20"] * 5,
            "symbol": ["AAA", "AAB", "BBB", "CCC", "CCD"],
            "cluster_name": ["cluster_1", "cluster_1", "cluster_2", "cluster_3", "cluster_3"],
            "y_stage_a": [0, 1, 1, 0, 0],
            "p_stage_a_raw": [0.4, 0.9, 0.8, 0.3, 0.2],
            "p_stage_a_calibrated": [0.4, 0.9, 0.8, 0.3, 0.2],
            "avg_tp_train": [0.10] * 5,
            "avg_sl_train": [0.05] * 5,
            "pnl_exec_stage_a": [0.0] * 5,
            "mu_adj_stage_a": [0.0] * 5,
            "kelly_frac_stage_a": [0.0] * 5,
            "position_usdt_stage_a": [0.0] * 5,
        }
    )

    rebuilt = restore._build_historical_decision_space_frame(predictions)

    aab = rebuilt.loc[rebuilt["symbol"] == "AAB"].iloc[0]
    bbb = rebuilt.loc[rebuilt["symbol"] == "BBB"].iloc[0]

    assert bool(aab["decision_selected"]) is True
    assert aab["decision_selection_mode"] == "cluster_local_top1"
    assert bool(aab["decision_selected_local"]) is True
    assert bool(bbb["decision_selected"]) is True
    assert bbb["decision_selection_mode"] == "date_universe_fallback"
    assert bool(bbb["decision_selected_fallback"]) is True


def test_build_bundle_inventory_flags_missing_files(tmp_path: Path) -> None:
    present_path = tmp_path / "present.json"
    present_path.write_text("{}", encoding="utf-8")
    missing_path = tmp_path / "missing.json"
    restore_payload = {
        "entries": [
            {
                "historical_blob_sha256": "ABC",
                "restored_local_path": str(present_path),
            },
            {
                "historical_blob_sha256": "DEF",
                "restored_local_path": str(missing_path),
            },
        ],
        "all_byte_exact": False,
        "manifest_path": str(tmp_path / "restored_bundle_manifest.json"),
    }

    inventory = restore._build_bundle_inventory(tmp_path, restore_payload)

    assert inventory["restored_files_present"] == 1
    assert inventory["missing_restored_files"] == [str(missing_path)]
    assert inventory["all_byte_exact"] is False


def test_compute_sovereign_metrics_uses_decision_selected_and_decision_position_usdt() -> None:
    frame = pd.DataFrame(
        {
            "date": ["2026-03-19", "2026-03-19", "2026-03-20"],
            "symbol": ["AAA", "BBB", "CCC"],
            "decision_selected": [True, True, True],
            "decision_position_usdt": [100.0, 0.0, 0.0],
        }
    )

    metrics = restore._compute_sovereign_metrics(frame)

    assert metrics["latest_date"] == "2026-03-20"
    assert metrics["latest_active_count_decision_space"] == 0
    assert metrics["headroom_decision_space"] is False
    assert metrics["recent_live_dates_decision_space"] == 1
    assert metrics["historical_active_events_decision_space"] == 1
