from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests import _path_setup  # noqa: F401
from features.onchain import UNLOCK_AUDIT_COLUMNS, UNLOCK_MODEL_FEATURE_COLUMNS
from services.ml_engine import main as ml_main


def _flatten_cluster_map(cluster_map: dict[str, Iterable[str]]) -> set[str]:
    return {feature for features in cluster_map.values() for feature in features}


class UnlockMlIntegrationTest(unittest.TestCase):
    def test_discover_symbols_excludes_reference_only_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ohlcv_dir = Path(tmp_dir) / "ohlcv_daily"
            ohlcv_dir.mkdir(parents=True, exist_ok=True)
            (ohlcv_dir / "BTC.parquet").write_bytes(b"0" * 1100)
            (ohlcv_dir / "SOL.parquet").write_bytes(b"0" * 1100)

            with patch.object(ml_main, "PARQUET_BASE", tmp_dir):
                symbols = ml_main.discover_symbols()

        self.assertEqual(symbols, ["SOL"])

    def test_run_kelly_sizing_keeps_mu_adj_column_for_zero_allocation_rows(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        barrier_df = pd.DataFrame({"pnl_real": [0.04, -0.02]}, index=index)
        p_calibrated = pd.Series([0.40, 0.45], index=index)
        sigma_ewma = pd.Series([0.20, 0.25], index=index)

        sizing_df = ml_main.run_kelly_sizing_for_symbol(barrier_df, p_calibrated, sigma_ewma, "TEST")

        self.assertIn("mu_adj", sizing_df.columns)
        self.assertTrue((sizing_df["kelly_frac"] == 0.0).all())
        self.assertAlmostEqual(float(sizing_df.iloc[0]["mu_adj"]), 0.004, places=6)

    def test_save_phase3_results_removes_stale_sizing_when_current_sizing_is_empty(self) -> None:
        index = pd.date_range("2024-01-01", periods=2, freq="D")
        barrier_df = pd.DataFrame(
            {
                "t_touch": index + pd.Timedelta(days=1),
                "label": [1, 0],
                "barrier_tp": [1.0, 1.0],
                "barrier_sl": [1.0, 1.0],
                "exit_price": [1.0, 1.0],
                "pnl_real": [0.05, 0.0],
                "slippage_frac": [0.0, 0.0],
                "sigma_at_entry": [0.2, 0.2],
                "p0": [1.0, 1.0],
                "holding_days": [1, 1],
            },
            index=index,
        )
        meta_result = {
            "p_bma": pd.Series([0.6, 0.55], index=index),
            "p_calibrated": pd.Series([0.6, 0.55], index=index),
            "y_target": pd.Series([1, 0], index=index),
            "uniqueness": pd.Series([1.0, 1.0], index=index),
        }
        stale_sizing_df = pd.DataFrame(
            {
                "date": index,
                "kelly_frac": [0.1, 0.1],
                "position_usdt": [1000.0, 1000.0],
                "p_cal": [0.6, 0.55],
                "sigma": [0.2, 0.2],
                "mu_adj": [0.01, 0.01],
            }
        ).set_index("date")

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.object(ml_main, "MODEL_PATH", tmp_dir):
                ml_main.save_phase3_results("TEST", barrier_df, meta_result, stale_sizing_df)
                sizing_path = Path(tmp_dir) / "phase3" / "TEST_sizing.parquet"
                self.assertTrue(sizing_path.exists())

                ml_main.save_phase3_results("TEST", barrier_df, meta_result, pd.DataFrame())

                self.assertFalse(sizing_path.exists())

    def test_run_hmm_for_symbol_degrades_sparse_derivative_inputs_explicitly(self) -> None:
        index = pd.date_range("2024-01-01", periods=220, freq="D")
        features = pd.DataFrame(
            {
                "ret_1d": np.linspace(-0.02, 0.03, len(index)),
                "ret_5d": np.linspace(-0.01, 0.05, len(index)),
                "realized_vol_30d": np.linspace(0.2, 0.4, len(index)),
                "vol_ratio": np.linspace(0.8, 1.5, len(index)),
                "funding_rate_ma7d": [np.nan] * len(index),
                "basis_3m": [np.nan] * len(index),
                "stablecoin_chg30": np.linspace(-0.03, 0.03, len(index)),
                "btc_ma200_flag": np.where(np.arange(len(index)) % 2 == 0, 1.0, 0.0),
                "dvol_zscore": np.linspace(-1.0, 1.0, len(index)),
            },
            index=index,
        )
        returns = features["ret_1d"]
        captured: dict[str, object] = {}

        def fake_run_hmm_walk_forward(**kwargs) -> pd.DataFrame:
            captured["columns"] = kwargs["feature_df"].columns.tolist()
            return pd.DataFrame(
                {
                    "hmm_prob_bull": np.linspace(0.2, 0.8, len(kwargs["feature_df"])),
                    "hmm_is_bull": np.where(np.arange(len(kwargs["feature_df"])) % 2 == 0, True, False),
                },
                index=kwargs["feature_df"].index,
            )

        def fake_validate_hmm_diagnostics(*args, **kwargs) -> dict[str, object]:
            return {"status": "PASS"}

        hmm_filter_stub = types.ModuleType("regime.hmm_filter")
        hmm_filter_stub.run_hmm_walk_forward = fake_run_hmm_walk_forward
        hmm_filter_stub.validate_hmm_diagnostics = fake_validate_hmm_diagnostics

        with (
            patch.dict(sys.modules, {"regime.hmm_filter": hmm_filter_stub}),
        ):
            result = ml_main.run_hmm_for_symbol(features, returns, "TEST")

        self.assertIn("columns", captured)
        self.assertNotIn("funding_rate_ma7d", captured["columns"])
        self.assertNotIn("basis_3m", captured["columns"])
        self.assertTrue(result["hmm_prob_bull"].notna().any())

    def test_compute_base_features_preserves_unlock_audit_metadata_outside_training_vector(self) -> None:
        index = pd.date_range("2024-01-31", periods=5, freq="D", tz="UTC")
        ohlcv = pd.DataFrame(
            {
                "open": np.linspace(10.0, 14.0, len(index)),
                "high": np.linspace(11.0, 15.0, len(index)),
                "low": np.linspace(9.0, 13.0, len(index)),
                "close": np.linspace(10.5, 14.5, len(index)),
                "volume": np.linspace(1000.0, 1400.0, len(index)),
            },
            index=index,
        )
        unlock_frame = pd.DataFrame(
            {
                "unlock_pressure_rank_observed": [0.2],
                "unlock_pressure_rank_reconstructed": [np.nan],
                "unlock_overhang_proxy_rank_full": [0.4],
                "unlock_fragility_proxy_rank_fallback": [0.5],
                "unlock_feature_state": ["OBSERVED"],
                "quality_flag": ["ok"],
                "source_primary": ["mobula"],
                "snapshot_ts": ["2024-01-31T00:00:00+00:00"],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2024-01-31", tz="UTC")]),
        )

        features = ml_main.compute_base_features(ohlcv, "TEST", unlock_frame=unlock_frame)

        self.assertEqual(features["unlock_feature_state"].iloc[-1], "OBSERVED")
        self.assertEqual(features["quality_flag"].iloc[-1], "ok")
        self.assertEqual(features["source_primary"].iloc[-1], "mobula")
        self.assertEqual(features["snapshot_ts"].iloc[-1], "2024-01-31T00:00:00+00:00")
        self.assertAlmostEqual(float(features["unlock_pressure_rank_observed"].iloc[-1]), 0.2)

    def test_run_meta_labeling_excludes_unlock_audit_columns_from_training_vector(self) -> None:
        captured: dict[str, pd.DataFrame] = {}
        index = pd.date_range("2024-01-01", periods=5, freq="D")
        features = pd.DataFrame(
            {
                "ret_1d": np.linspace(0.01, 0.05, len(index)),
                "ret_5d": np.linspace(0.02, 0.10, len(index)),
                "ret_20d": np.linspace(0.03, 0.15, len(index)),
                "realized_vol_30d": np.linspace(0.20, 0.24, len(index)),
                "vol_ratio": np.linspace(1.0, 1.4, len(index)),
                "funding_rate_ma7d": np.linspace(0.001, 0.005, len(index)),
                "basis_3m": np.linspace(0.01, 0.03, len(index)),
                "stablecoin_chg30": np.linspace(-0.02, 0.02, len(index)),
                "dvol_zscore": np.linspace(-1.0, 1.0, len(index)),
                "btc_ma200_flag": [0.0, 1.0, 0.0, 1.0, 1.0],
                "close_fracdiff": np.linspace(-0.5, 0.5, len(index)),
                "unlock_pressure_rank_observed": np.linspace(0.1, 0.5, len(index)),
                "unlock_pressure_rank_reconstructed": np.linspace(0.2, 0.6, len(index)),
                "unlock_overhang_proxy_rank_full": np.linspace(0.3, 0.7, len(index)),
                "unlock_fragility_proxy_rank_fallback": np.linspace(0.4, 0.8, len(index)),
                "unlock_feature_state": ["OBSERVED"] * len(index),
                "reconstruction_confidence": np.linspace(0.9, 0.95, len(index)),
                "unlock_pressure_rank_selected_for_reporting": np.linspace(0.2, 0.9, len(index)),
                "quality_flag": ["ok"] * len(index),
            },
            index=index,
        )
        barrier_df = pd.DataFrame(
            {
                "label": [1, -1, 1, 0, 1],
                "t_touch": index + pd.Timedelta(days=2),
            },
            index=index,
        )
        hmm_result = pd.DataFrame({"hmm_prob_bull": np.linspace(0.6, 0.9, len(index))}, index=index)
        sigma_ewma = pd.Series(np.linspace(0.1, 0.3, len(index)), index=index)

        def fake_compute_label_uniqueness(barrier: pd.DataFrame) -> pd.Series:
            return pd.Series(1.0, index=barrier.index)

        def fake_compute_effective_n(barrier: pd.DataFrame, uniqueness: pd.Series):
            return 40.0, uniqueness, "LOGISTIC_ONLY"

        def fake_compute_meta_sample_weights(barrier: pd.DataFrame, uniqueness: pd.Series, halflife_days: int, sl_penalty: float) -> pd.Series:
            return pd.Series(1.0, index=barrier.index)

        def fake_generate_pbma_purged_kfold(**kwargs) -> pd.Series:
            captured["feature_df"] = kwargs["feature_df"].copy()
            return pd.Series(0.6, index=kwargs["feature_df"].index, name="p_bma_pkf")

        def fake_run_isotonic_walk_forward(**kwargs) -> pd.Series:
            return kwargs["p_raw_series"]

        with (
            patch.object(ml_main, "MODEL_PATH", "tests_tmp_models"),
            patch.object(ml_main, "UNLOCK_MODEL_FEATURE_SET", "full"),
            patch("meta_labeling.uniqueness.compute_label_uniqueness", side_effect=fake_compute_label_uniqueness),
            patch("meta_labeling.uniqueness.compute_effective_n", side_effect=fake_compute_effective_n),
            patch("meta_labeling.uniqueness.compute_meta_sample_weights", side_effect=fake_compute_meta_sample_weights),
            patch("meta_labeling.pbma_purged.generate_pbma_purged_kfold", side_effect=fake_generate_pbma_purged_kfold),
            patch("meta_labeling.isotonic_calibration.run_isotonic_walk_forward", side_effect=fake_run_isotonic_walk_forward),
        ):
            result = ml_main.run_meta_labeling_for_symbol(features, barrier_df, hmm_result, sigma_ewma, "TEST")

        self.assertIsNotNone(result)
        self.assertIn("feature_df", captured)
        self.assertTrue(set(UNLOCK_MODEL_FEATURE_COLUMNS).issubset(captured["feature_df"].columns))
        self.assertTrue(set(UNLOCK_AUDIT_COLUMNS).isdisjoint(captured["feature_df"].columns))
        self.assertNotIn("unlock_pressure_rank_selected_for_reporting", captured["feature_df"].columns)
        self.assertNotIn("unlock_feature_state", captured["feature_df"].columns)
        self.assertNotIn("reconstruction_confidence", captured["feature_df"].columns)
        self.assertNotIn("quality_flag", captured["feature_df"].columns)

    def test_run_meta_labeling_respects_proxy_only_unlock_mode(self) -> None:
        captured: dict[str, pd.DataFrame] = {}
        index = pd.date_range("2024-01-01", periods=5, freq="D")
        features = pd.DataFrame(
            {
                "ret_1d": np.linspace(0.01, 0.05, len(index)),
                "ret_5d": np.linspace(0.02, 0.10, len(index)),
                "ret_20d": np.linspace(0.03, 0.15, len(index)),
                "realized_vol_30d": np.linspace(0.20, 0.24, len(index)),
                "vol_ratio": np.linspace(1.0, 1.4, len(index)),
                "funding_rate_ma7d": np.linspace(0.001, 0.005, len(index)),
                "basis_3m": np.linspace(0.01, 0.03, len(index)),
                "stablecoin_chg30": np.linspace(-0.02, 0.02, len(index)),
                "dvol_zscore": np.linspace(-1.0, 1.0, len(index)),
                "btc_ma200_flag": [0.0, 1.0, 0.0, 1.0, 1.0],
                "close_fracdiff": np.linspace(-0.5, 0.5, len(index)),
                "unlock_pressure_rank_observed": np.linspace(0.1, 0.5, len(index)),
                "unlock_pressure_rank_reconstructed": np.linspace(0.2, 0.6, len(index)),
                "unlock_overhang_proxy_rank_full": np.linspace(0.3, 0.7, len(index)),
                "unlock_fragility_proxy_rank_fallback": np.linspace(0.4, 0.8, len(index)),
            },
            index=index,
        )
        barrier_df = pd.DataFrame({"label": [1, -1, 1, 0, 1], "t_touch": index + pd.Timedelta(days=2)}, index=index)
        hmm_result = pd.DataFrame({"hmm_prob_bull": np.linspace(0.6, 0.9, len(index))}, index=index)
        sigma_ewma = pd.Series(np.linspace(0.1, 0.3, len(index)), index=index)

        def fake_compute_label_uniqueness(barrier: pd.DataFrame) -> pd.Series:
            return pd.Series(1.0, index=barrier.index)

        def fake_compute_effective_n(barrier: pd.DataFrame, uniqueness: pd.Series):
            return 40.0, uniqueness, "LOGISTIC_ONLY"

        def fake_compute_meta_sample_weights(barrier: pd.DataFrame, uniqueness: pd.Series, halflife_days: int, sl_penalty: float) -> pd.Series:
            return pd.Series(1.0, index=barrier.index)

        def fake_generate_pbma_purged_kfold(**kwargs) -> pd.Series:
            captured["feature_df"] = kwargs["feature_df"].copy()
            return pd.Series(0.6, index=kwargs["feature_df"].index, name="p_bma_pkf")

        def fake_run_isotonic_walk_forward(**kwargs) -> pd.Series:
            return kwargs["p_raw_series"]

        with (
            patch.object(ml_main, "UNLOCK_MODEL_FEATURE_SET", "proxies"),
            patch("meta_labeling.uniqueness.compute_label_uniqueness", side_effect=fake_compute_label_uniqueness),
            patch("meta_labeling.uniqueness.compute_effective_n", side_effect=fake_compute_effective_n),
            patch("meta_labeling.uniqueness.compute_meta_sample_weights", side_effect=fake_compute_meta_sample_weights),
            patch("meta_labeling.pbma_purged.generate_pbma_purged_kfold", side_effect=fake_generate_pbma_purged_kfold),
            patch("meta_labeling.isotonic_calibration.run_isotonic_walk_forward", side_effect=fake_run_isotonic_walk_forward),
        ):
            result = ml_main.run_meta_labeling_for_symbol(features, barrier_df, hmm_result, sigma_ewma, "TEST")

        self.assertIsNotNone(result)
        self.assertIn("unlock_overhang_proxy_rank_full", captured["feature_df"].columns)
        self.assertIn("unlock_fragility_proxy_rank_fallback", captured["feature_df"].columns)
        self.assertNotIn("unlock_pressure_rank_observed", captured["feature_df"].columns)
        self.assertNotIn("unlock_pressure_rank_reconstructed", captured["feature_df"].columns)

    def test_run_vi_clustering_excludes_unlock_audit_columns(self) -> None:
        captured: dict[str, pd.DataFrame] = {}
        base_index = pd.date_range("2024-01-01", periods=60, freq="D")
        all_features: dict[str, pd.DataFrame] = {}

        for idx, symbol in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"], start=1):
            frame = pd.DataFrame(
                {
                    "ret_1d": np.linspace(0.01 * idx, 0.05 * idx, len(base_index)),
                    "ret_5d": np.linspace(0.02 * idx, 0.06 * idx, len(base_index)),
                    "ret_20d": np.linspace(0.03 * idx, 0.08 * idx, len(base_index)),
                    "realized_vol_30d": np.linspace(0.2, 0.5, len(base_index)),
                    "vol_ratio": np.linspace(1.0, 2.0, len(base_index)),
                    "funding_rate_ma7d": np.linspace(0.001, 0.005, len(base_index)),
                    "basis_3m": np.linspace(0.01, 0.03, len(base_index)),
                    "stablecoin_chg30": np.linspace(-0.02, 0.03, len(base_index)),
                    "dvol_zscore": np.linspace(-1.0, 1.0, len(base_index)),
                    "btc_ma200_flag": np.where(np.arange(len(base_index)) % 2 == 0, 1.0, 0.0),
                    "close_fracdiff": np.linspace(-0.5, 0.5, len(base_index)),
                    "unlock_pressure_rank_observed": np.linspace(0.1, 0.5, len(base_index)),
                    "unlock_pressure_rank_reconstructed": np.linspace(0.2, 0.6, len(base_index)),
                    "unlock_overhang_proxy_rank_full": np.linspace(0.3, 0.7, len(base_index)),
                    "unlock_fragility_proxy_rank_fallback": np.linspace(0.4, 0.8, len(base_index)),
                    "unlock_feature_state": ["OBSERVED"] * len(base_index),
                    "reconstruction_confidence": np.linspace(0.9, 0.95, len(base_index)),
                    "quality_flag": ["ok"] * len(base_index),
                },
                index=base_index,
            )
            all_features[symbol] = frame

        def fake_compute_vi_distance_matrix(df: pd.DataFrame, n_bins: int = 10) -> pd.DataFrame:
            captured["pooled_df"] = df.copy()
            size = len(df.columns)
            return pd.DataFrame(np.eye(size), index=df.columns, columns=df.columns)

        def fake_cluster_features(vi_matrix: pd.DataFrame, vi_threshold: float, save_path: str) -> dict:
            feature_to_cluster = {
                column: f"cluster_{idx}" for idx, column in enumerate(vi_matrix.columns, start=1)
            }
            return {
                "status": "OK",
                "n_clusters": len(vi_matrix.columns),
                "cluster_map": {f"cluster_{idx}": [column] for idx, column in enumerate(vi_matrix.columns, start=1)},
                "feature_to_cluster": feature_to_cluster,
                "redundant_pairs": [],
            }

        with (
            patch.object(ml_main, "MODEL_PATH", "tests_tmp_models"),
            patch.object(ml_main, "UNLOCK_MODEL_FEATURE_SET", "full"),
            patch("vi_cfi.vi.compute_vi_distance_matrix", side_effect=fake_compute_vi_distance_matrix),
            patch("vi_cfi.vi.cluster_features", side_effect=fake_cluster_features),
        ):
            result = ml_main.run_vi_clustering(all_features)

        self.assertEqual(result["status"], "OK")
        self.assertIn("pooled_df", captured)
        self.assertTrue(set(UNLOCK_AUDIT_COLUMNS).isdisjoint(captured["pooled_df"].columns))
        self.assertTrue(set(UNLOCK_MODEL_FEATURE_COLUMNS).issubset(captured["pooled_df"].columns))
        self.assertTrue(set(UNLOCK_AUDIT_COLUMNS).isdisjoint(_flatten_cluster_map(result["cluster_map"])))


if __name__ == "__main__":
    unittest.main()
