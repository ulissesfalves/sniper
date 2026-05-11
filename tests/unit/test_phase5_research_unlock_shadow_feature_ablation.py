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

import phase5_research_unlock_shadow_feature_ablation as gate


def _write_unlock_fixture(root: Path) -> None:
    unlock_dir = root / "data" / "parquet" / "unlocks"
    quality_dir = root / "data" / "parquet" / "unlock_diagnostics"
    phase4_dir = root / "data" / "models" / "phase4"
    unlock_dir.mkdir(parents=True)
    quality_dir.mkdir(parents=True)
    phase4_dir.mkdir(parents=True)

    dates = pd.date_range("2025-01-01", periods=80, freq="D", tz="UTC")
    unlock = pd.DataFrame(
        {
            "timestamp": dates,
            "symbol": "AAA",
            "unlock_pressure_rank_observed": [None] * 80,
            "unlock_pressure_rank_reconstructed": [None] * 80,
            "unlock_overhang_proxy_rank_full": [None] * 80,
            "unlock_fragility_proxy_rank_fallback": [idx / 79 for idx in range(80)],
            "unlock_pressure_rank_selected_for_reporting": [idx / 79 for idx in range(80)],
            "unlock_feature_state": ["PROXY_FALLBACK"] * 80,
            "quality_flag": ["proxy_fallback"] * 80,
        }
    )
    unlock.to_parquet(unlock_dir / "AAA.parquet", index=False)
    pd.DataFrame(
        {
            "date": dates.tz_localize(None),
            "n_assets": [20] * 80,
            "observed_coverage": [0.0] * 80,
            "reconstructed_coverage": [0.0] * 80,
            "proxy_full_coverage": [0.0] * 80,
            "proxy_fallback_coverage": [1.0] * 80,
            "missing_rate": [0.0] * 80,
            "shadow_mode_flag": [1] * 80,
            "promotion_blocked_count": [0] * 80,
        }
    ).to_parquet(quality_dir / "unlock_quality_daily.parquet", index=False)
    pd.DataFrame(
        {
            "combo": ["(0, 1)"] * 80,
            "date": dates.tz_localize(None),
            "symbol": ["AAA"] * 80,
            "pnl_real": [(-1) ** idx * 0.01 for idx in range(80)],
            "p_bma_pkf": [0.5] * 80,
        }
    ).to_parquet(phase4_dir / "phase4_oos_predictions.parquet", index=False)


def test_required_artifact_status_detects_missing_h06_inputs(tmp_path: Path) -> None:
    status = gate.required_artifact_status(tmp_path)

    assert status["required_artifacts_present"] is False
    assert status["unlock_file_count"] == 0
    assert status["quality_exists"] is False


def test_unlock_shadow_diagnostic_loads_canonical_artifacts(tmp_path: Path) -> None:
    _write_unlock_fixture(tmp_path)

    artifact_status = gate.required_artifact_status(tmp_path)
    unlock_frame, inventory, errors = gate.load_unlock_frame(tmp_path)
    quality_frame, quality_error = gate.load_quality_summary(tmp_path)
    joined, overlap = gate.phase4_overlap_diagnostics(unlock_frame, tmp_path)
    metrics = gate.feature_ablation_metrics(joined)
    status, decision, classification = gate.classify_h06_gate(
        artifact_status,
        gate.quality_diagnostics(quality_frame),
        overlap,
        metrics,
    )

    assert artifact_status["required_artifacts_present"] is True
    assert len(inventory) == 1
    assert errors == []
    assert quality_error is None
    assert overlap["joined_rows_with_selected_unlock"] == 80
    assert status == "PASS"
    assert decision == "advance"
    assert classification == "H06_UNLOCK_SHADOW_DIAGNOSTIC_COMPLETE_NOT_PROMOTABLE"


def test_classification_blocks_when_phase4_overlap_is_absent(tmp_path: Path) -> None:
    _write_unlock_fixture(tmp_path)
    artifact_status = gate.required_artifact_status(tmp_path)
    unlock_frame, _, _ = gate.load_unlock_frame(tmp_path)
    quality_frame, _ = gate.load_quality_summary(tmp_path)
    empty_joined, overlap = pd.DataFrame(), {"phase4_exists": True, "joined_rows_with_selected_unlock": 0}
    metrics = gate.feature_ablation_metrics(empty_joined)

    status, decision, classification = gate.classify_h06_gate(
        artifact_status,
        gate.quality_diagnostics(quality_frame),
        overlap,
        metrics,
    )

    assert not unlock_frame.empty
    assert (status, decision) == ("INCONCLUSIVE", "correct")
    assert classification == "H06_NO_PHASE4_OVERLAP"


def test_feature_ablation_metrics_use_outcome_as_diagnostic_only(tmp_path: Path) -> None:
    _write_unlock_fixture(tmp_path)
    unlock_frame, _, _ = gate.load_unlock_frame(tmp_path)
    joined, _ = gate.phase4_overlap_diagnostics(unlock_frame, tmp_path)

    metrics = gate.feature_ablation_metrics(joined)
    row = metrics.loc[metrics["feature"].eq("unlock_pressure_rank_selected_for_reporting")].iloc[0]

    assert bool(row["exists"]) is True
    assert row["joined_valid_rows"] == 80
    assert bool(row["forbidden_as_operational_input"]) is False
    assert "pnl_real" in gate.FORBIDDEN_OPERATIONAL_INPUTS
