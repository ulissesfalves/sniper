from __future__ import annotations

import pickle
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

import numpy as np
import pandas as pd
from sklearn.preprocessing import RobustScaler

from tests import _path_setup  # noqa: F401

if "regime" not in sys.modules:
    regime_stub = types.ModuleType("regime")
    pca_robust_stub = types.ModuleType("regime.pca_robust")
    hmm_filter_stub = types.ModuleType("regime.hmm_filter")
    pca_robust_stub.transform_robust_pca = lambda values, pipeline: values
    hmm_filter_stub.build_regime_target = lambda returns: (pd.Series(returns).fillna(0) < 0).astype(int)
    regime_stub.pca_robust = pca_robust_stub
    regime_stub.hmm_filter = hmm_filter_stub
    sys.modules["regime"] = regime_stub
    sys.modules["regime.pca_robust"] = pca_robust_stub
    sys.modules["regime.hmm_filter"] = hmm_filter_stub

from services.ml_engine import phase2_diagnostic as phase2_diag


class UnlockPhase2DiagnosticTest(unittest.TestCase):
    def test_audit_feature_store_uses_eligible_ohlcv_set_and_surfaces_collapsed_threshold_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            parquet_base = base / "parquet"
            features_path = base / "models" / "features"
            ohlcv_dir = parquet_base / "ohlcv_daily"
            features_path.mkdir(parents=True, exist_ok=True)
            ohlcv_dir.mkdir(parents=True, exist_ok=True)

            dense_cols = {
                "ret_1d": [0.01] * 400,
                "ret_5d": [0.02] * 400,
                "ret_20d": [0.03] * 400,
                "realized_vol_30d": [0.2] * 400,
                "vol_ratio": [1.1] * 400,
                "funding_rate_ma7d": [np.nan] * 400,
                "basis_3m": [np.nan] * 400,
                "stablecoin_chg30": [0.0] * 400,
                "btc_ma200_flag": [1.0] * 400,
                "dvol_zscore": [0.1] * 400,
                "unlock_pressure_rank_observed": [np.nan] * 400,
                "unlock_pressure_rank_reconstructed": [np.nan] * 400,
                "unlock_overhang_proxy_rank_full": [0.4] * 400,
                "unlock_fragility_proxy_rank_fallback": [0.5] * 400,
            }

            for symbol, periods in [("AAA", 400), ("BTC", 400), ("LUNA", 400), ("FTT", 400), ("LUNA2", 90), ("CEL", 90)]:
                pd.DataFrame(
                    {
                        "timestamp": pd.date_range("2024-01-01", periods=periods, freq="D", tz="UTC"),
                        "open": [1.0] * periods,
                        "high": [1.1] * periods,
                        "low": [0.9] * periods,
                        "close": [1.0] * periods,
                    }
                ).to_pickle(ohlcv_dir / f"{symbol}.parquet")

            for symbol in ["AAA", "LUNA", "FTT"]:
                pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=400, freq="D", tz="UTC"), **dense_cols}).to_pickle(
                    features_path / f"{symbol}.parquet"
                )

            with (
                patch.object(phase2_diag, "PARQUET_BASE", parquet_base),
                patch.object(phase2_diag, "FEATURES_PATH", features_path),
            ):
                report = phase2_diag.audit_feature_store()

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["expected_assets"], 3)
        self.assertEqual(report["missing_eligible_assets"], [])
        self.assertEqual(set(report["collapsed_below_history_threshold"]), {"LUNA2", "CEL"})
        self.assertNotIn("BTC", report["eligible_symbols"])

    def test_audit_hmm_outputs_counts_degraded_inputs_without_marking_hard_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            features_path = base / "models" / "features"
            hmm_path = base / "models" / "hmm" / "AAA"
            features_path.mkdir(parents=True, exist_ok=True)
            hmm_path.mkdir(parents=True, exist_ok=True)

            rows = 180
            feature_frame = pd.DataFrame(
                {
                    "timestamp": pd.date_range("2024-01-01", periods=rows, freq="D", tz="UTC"),
                    "ret_1d": np.where(np.arange(rows) % 2 == 0, 0.02, -0.01),
                    "ret_5d": np.linspace(-0.02, 0.04, rows),
                    "realized_vol_30d": np.linspace(0.2, 0.3, rows),
                    "vol_ratio": np.linspace(0.8, 1.4, rows),
                    "funding_rate_ma7d": [np.nan] * rows,
                    "basis_3m": [np.nan] * rows,
                    "stablecoin_chg30": np.linspace(-0.01, 0.02, rows),
                    "btc_ma200_flag": np.where(np.arange(rows) % 2 == 0, 1.0, 0.0),
                    "dvol_zscore": np.linspace(-1.0, 1.0, rows),
                    "hmm_prob_bull": np.where(np.arange(rows) % 2 == 0, 0.8, 0.2),
                    "hmm_is_bull": np.where(np.arange(rows) % 2 == 0, True, False),
                }
            )
            feature_frame.to_pickle(features_path / "AAA.parquet")

            fitted = SimpleNamespace(
                pca_pipeline=SimpleNamespace(scaler=RobustScaler(), winsorizer=object(), feature_names=[]),
                var_explained=0.85,
                n_pca_components=2,
                threshold=0.5,
                train_end_date="2024-06-01",
            )
            with open(hmm_path / "hmm_t150.pkl", "wb") as fout:
                pickle.dump(fitted, fout)

            with (
                patch.object(phase2_diag, "FEATURES_PATH", features_path),
                patch.object(phase2_diag, "HMM_PATH", base / "models" / "hmm"),
            ):
                report = phase2_diag.audit_hmm_outputs()

        self.assertEqual(report["assets_with_missing_hmm_inputs"], 0)
        self.assertEqual(report["assets_with_degraded_inputs"], 1)
        self.assertEqual(report["assets_pass"], 1)

    def test_audit_hmm_outputs_treats_predictive_quality_as_advisory_when_hard_gate_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            features_path = base / "models" / "features"
            hmm_path = base / "models" / "hmm" / "AAA"
            features_path.mkdir(parents=True, exist_ok=True)
            hmm_path.mkdir(parents=True, exist_ok=True)

            rows = 180
            feature_frame = pd.DataFrame(
                {
                    "timestamp": pd.date_range("2024-01-01", periods=rows, freq="D", tz="UTC"),
                    "ret_1d": np.where(np.arange(rows) % 3 == 0, 0.03, -0.02),
                    "ret_5d": np.linspace(-0.02, 0.04, rows),
                    "realized_vol_30d": np.linspace(0.2, 0.3, rows),
                    "vol_ratio": np.linspace(0.8, 1.4, rows),
                    "funding_rate_ma7d": [0.001] * rows,
                    "basis_3m": [0.01] * rows,
                    "stablecoin_chg30": np.linspace(-0.01, 0.02, rows),
                    "btc_ma200_flag": [1.0] * rows,
                    "dvol_zscore": np.linspace(-1.0, 1.0, rows),
                    "hmm_prob_bull": [0.9] * rows,
                    "hmm_is_bull": [False] * rows,
                }
            )
            feature_frame.to_pickle(features_path / "AAA.parquet")

            fitted = SimpleNamespace(
                pca_pipeline=SimpleNamespace(scaler=RobustScaler(), winsorizer=object(), feature_names=[]),
                var_explained=0.85,
                n_pca_components=2,
                threshold=0.5,
                train_end_date="2024-06-01",
            )
            with open(hmm_path / "hmm_t150.pkl", "wb") as fout:
                pickle.dump(fitted, fout)

            with (
                patch.object(phase2_diag, "FEATURES_PATH", features_path),
                patch.object(phase2_diag, "HMM_PATH", base / "models" / "hmm"),
                patch.object(phase2_diag, "HMM_HARD_GATE_MODE", "off"),
            ):
                report = phase2_diag.audit_hmm_outputs()

        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["quality_checks_blocking"])
        self.assertEqual(report["assets_with_low_f1_oos"], 1)
        self.assertEqual(report["assets_with_advisory_low_f1_oos"], 1)
        self.assertEqual(report["assets_with_blocking_low_f1_oos"], 0)
        self.assertEqual(report["assets_pass"], 1)

    def test_audit_unlock_shadow_mode_reports_coverage_and_quality_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            parquet_base = base / "parquet"
            features_path = base / "models" / "features"
            unlock_dir = parquet_base / "unlocks"
            diagnostics_dir = parquet_base / "unlock_diagnostics"
            unlock_dir.mkdir(parents=True, exist_ok=True)
            diagnostics_dir.mkdir(parents=True, exist_ok=True)
            features_path.mkdir(parents=True, exist_ok=True)

            unlock_frame = pd.DataFrame(
                {
                    "timestamp": pd.date_range("2024-01-01", periods=12, freq="D", tz="UTC"),
                    "unlock_pressure_rank_observed": [0.1, 0.2, 0.3, 0.4, 0.5, None, None, None, None, None, None, None],
                    "unlock_pressure_rank_reconstructed": [None, None, None, None, None, 0.2, 0.3, 0.4, None, None, None, None],
                    "unlock_overhang_proxy_rank_full": [0.3] * 12,
                    "unlock_fragility_proxy_rank_fallback": [0.4, 0.5, 0.6, 0.7, 0.8, None, None, 0.4, 0.5, 0.6, 0.7, 0.8],
                    "unlock_pressure_rank_selected_for_reporting": [0.1] * 8 + [None] * 4,
                    "unlock_feature_state": ["OBSERVED"] * 5 + ["RECONSTRUCTED"] * 3 + ["PROXY_FULL"] * 2 + ["PROXY_FALLBACK"] * 2,
                }
            )
            unlock_frame.to_pickle(unlock_dir / "OBS.parquet")

            feature_frame = pd.DataFrame(
                {
                    "timestamp": pd.date_range("2024-01-01", periods=12, freq="D", tz="UTC"),
                    "ret_1d": [0.01] * 12,
                    "ret_5d": [0.02] * 12,
                    "ret_20d": [0.03] * 12,
                    "realized_vol_30d": [0.2] * 12,
                    "vol_ratio": [1.1] * 12,
                    "funding_rate_ma7d": [0.001] * 12,
                    "basis_3m": [0.01] * 12,
                    "stablecoin_chg30": [0.0] * 12,
                    "btc_ma200_flag": [1.0] * 12,
                    "dvol_zscore": [0.1] * 12,
                    "unlock_pressure_rank_observed": unlock_frame["unlock_pressure_rank_observed"].tolist(),
                    "unlock_pressure_rank_reconstructed": unlock_frame["unlock_pressure_rank_reconstructed"].tolist(),
                    "unlock_overhang_proxy_rank_full": unlock_frame["unlock_overhang_proxy_rank_full"].tolist(),
                    "unlock_fragility_proxy_rank_fallback": unlock_frame["unlock_fragility_proxy_rank_fallback"].tolist(),
                    "unlock_pressure_rank_selected_for_reporting": unlock_frame["unlock_pressure_rank_selected_for_reporting"].tolist(),
                }
            )
            feature_frame.to_pickle(features_path / "OBS.parquet")

            quality_frame = pd.DataFrame(
                [
                    {
                        "date": "2024-01-12",
                        "n_assets": 64,
                        "observed_coverage": 0.40,
                        "reconstructed_coverage": 0.25,
                        "proxy_full_coverage": 1.00,
                        "proxy_fallback_coverage": 0.75,
                        "missing_rate": 0.0,
                        "unknown_bucket_block_rate": 0.0,
                        "massive_ties_fraction": 0.5,
                        "shadow_mode_flag": 1,
                    },
                    {
                        "date": "2024-01-13",
                        "n_assets": 1,
                        "observed_coverage": 0.0,
                        "reconstructed_coverage": 0.0,
                        "proxy_full_coverage": 0.0,
                        "proxy_fallback_coverage": 1.0,
                        "missing_rate": 0.0,
                        "unknown_bucket_block_rate": 0.0,
                        "massive_ties_fraction": 1.0,
                        "shadow_mode_flag": 1,
                    },
                ]
            )
            quality_frame.to_pickle(diagnostics_dir / "unlock_quality_daily.parquet")

            with (
                patch.object(phase2_diag, "PARQUET_BASE", parquet_base),
                patch.object(phase2_diag, "FEATURES_PATH", features_path),
                patch.object(phase2_diag, "UNLOCK_DIAGNOSTICS_PATH", diagnostics_dir / "unlock_quality_daily.parquet"),
            ):
                report = phase2_diag.audit_unlock_shadow_mode()

        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["quality_summary_present"])
        self.assertIn("unlock_pressure_rank_observed", report["coverage_by_feature"])
        self.assertIn("unlock_pressure_rank_selected_for_reporting", report["audit_only_coverage"])
        self.assertGreater(report["baseline_vs_augmented"]["unlock_any_valid_row_ratio_avg"], 0.0)
        self.assertGreater(report["baseline_vs_augmented"]["augmented_complete_row_ratio_avg"], 0.0)
        self.assertEqual(report["baseline_vs_augmented"]["legacy_all_unlock_columns_nonnull_row_ratio_avg"], 0.0)
        self.assertEqual(report["latest_quality"]["shadow_mode_flag"], 1)
        self.assertEqual(report["latest_quality"]["date"], "2024-01-12")
        self.assertEqual(report["raw_latest_quality"]["date"], "2024-01-13")
        self.assertTrue(report["latest_quality_metadata"]["partial_latest_filtered"])


if __name__ == "__main__":
    unittest.main()
