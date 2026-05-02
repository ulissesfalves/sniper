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

import phase5_research_deep_quant_diagnostic as gate


def test_describe_return_series_reports_risk_shape() -> None:
    summary = gate.describe_return_series(
        pd.Series([0.01, -0.02, 0.03, -0.01]),
        pd.Series([0.01, 0.02, 0.00, 0.02]),
    )

    assert summary["total_days"] == 4
    assert summary["active_days"] == 3
    assert summary["max_drawdown_proxy"] > 0.0
    assert summary["mean_turnover_proxy"] > 0.0


def test_build_diagnostic_payload_preserves_dsr_blocker() -> None:
    payload = gate.build_diagnostic_payload(
        {"dsr": {"sharpe_is": 0.88, "sr_needed": 4.47, "dsr_honest": 0.0, "passed": False}},
        {},
        [
            {
                "combo": "(0, 1)",
                "active_days": 120,
                "annualized_sharpe_proxy": -0.5,
                "skew_proxy": 0.1,
                "kurtosis_proxy": 1.2,
                "max_drawdown_proxy": 0.04,
                "mean_turnover_proxy": 0.01,
            }
        ],
        [{"combo": "(0, 1)", "subperiod": 1, "annualized_sharpe_proxy": -0.2}],
        [{"policy": "p", "median_combo_sharpe": 0.2, "min_combo_sharpe": -1.0, "median_active_days": 120}],
    )

    assert payload["dsr"]["dsr_honest"] == 0.0
    assert payload["dsr"]["sharpe_gap_to_sr_needed"] == 3.59
    assert payload["sensitivity"]["positive_and_stable_policy_count"] == 0


def test_classify_diagnostic_passes_as_diagnostic_only() -> None:
    status, decision, classification = gate.classify_diagnostic(
        {
            "dsr": {"dsr_honest": 0.0, "dsr_passed": False},
            "research_nonzero_exposure_returns": {"combo_count": 2},
            "subperiods": {"rows": 6},
            "sensitivity": {"policy_rows_scanned": 2},
        }
    )

    assert (status, decision) == ("PASS", "advance")
    assert classification == "DEEP_QUANT_DSR_AND_STABILITY_DIAGNOSTIC_COMPLETE"
