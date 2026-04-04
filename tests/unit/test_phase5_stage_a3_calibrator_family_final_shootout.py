from __future__ import annotations

import numpy as np

from services.ml_engine.phase5_stage_a3_calibrator_family_final_shootout import (
    _beta_calibration_predict,
    _resolve_final_a3_decision,
)


def test_beta_calibration_predict_is_monotonic_when_inputs_are_sorted():
    raw = np.array([0.05, 0.10, 0.20, 0.40, 0.70, 0.90], dtype=float)
    labels = np.array([0, 0, 0, 1, 1, 1], dtype=int)
    weights = np.ones_like(raw)
    calibrated, diag = _beta_calibration_predict(raw, labels, weights)

    assert diag["monotone_increasing"] is True
    assert np.all(np.diff(calibrated) >= -1e-12)
    assert calibrated.min() >= 0.0
    assert calibrated.max() <= 1.0


def test_resolve_final_a3_decision_ignores_diagnostic_only_identity_for_honest_fix():
    conclusion, cause, honest_fix_exists = _resolve_final_a3_decision(
        [
            {
                "variant": "challenger_platt_global_after_aggregate",
                "diagnostic_only": False,
                "promotable_low_regret_candidate": False,
                "live_signal_minimum_pass": False,
            },
            {
                "variant": "challenger_beta_global_after_aggregate",
                "diagnostic_only": False,
                "promotable_low_regret_candidate": False,
                "live_signal_minimum_pass": False,
            },
            {
                "variant": "challenger_identity_no_calibration_after_aggregate",
                "diagnostic_only": True,
                "promotable_low_regret_candidate": False,
                "live_signal_minimum_pass": True,
            },
        ]
    )

    assert conclusion == "A3_STRUCTURAL_CHOKE_CONFIRMED"
    assert honest_fix_exists is False
    assert "DIAGNOSTIC_ONLY identity" in cause


def test_resolve_final_a3_decision_accepts_non_diagnostic_live_fix():
    conclusion, _, honest_fix_exists = _resolve_final_a3_decision(
        [
            {
                "variant": "challenger_platt_global_after_aggregate",
                "diagnostic_only": False,
                "promotable_low_regret_candidate": True,
                "live_signal_minimum_pass": True,
            }
        ]
    )

    assert conclusion == "LOW_REGRET_CALIBRATOR_FIX_EXISTS"
    assert honest_fix_exists is True
