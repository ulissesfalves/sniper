from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import phase4_cpcv as phase4


class Phase4UnlockAblationTest(unittest.TestCase):
    def test_get_unlock_model_feature_columns_exposes_expected_scenarios(self) -> None:
        self.assertEqual(phase4.get_unlock_model_feature_columns("baseline"), [])
        self.assertEqual(
            phase4.get_unlock_model_feature_columns("proxies"),
            [
                "unlock_overhang_proxy_rank_full",
                "unlock_fragility_proxy_rank_fallback",
            ],
        )
        self.assertEqual(
            phase4.get_unlock_model_feature_columns("full"),
            [
                "unlock_pressure_rank_observed",
                "unlock_pressure_rank_reconstructed",
                "unlock_overhang_proxy_rank_full",
                "unlock_fragility_proxy_rank_fallback",
            ],
        )

    def test_select_features_respects_baseline_vs_proxy_unlock_modes(self) -> None:
        df = pd.DataFrame(
            {
                "p_bma_pkf": np.linspace(0.1, 0.9, 120),
                "ret_1d": np.linspace(-0.2, 0.2, 120),
                "ret_5d": np.linspace(-0.3, 0.3, 120),
                "ret_20d": np.linspace(-0.4, 0.4, 120),
                "vol_ratio": np.linspace(0.8, 1.4, 120),
                "sigma_ewma": np.linspace(0.1, 0.3, 120),
                "stablecoin_chg30": np.linspace(-0.05, 0.05, 120),
                "btc_ma200_flag": np.where(np.arange(120) % 2 == 0, 1.0, 0.0),
                "dvol_zscore": np.linspace(-1.0, 1.0, 120),
                "close_fracdiff": np.linspace(-0.2, 0.2, 120),
                "unlock_pressure_rank_observed": np.linspace(0.1, 0.9, 120),
                "unlock_pressure_rank_reconstructed": np.linspace(0.2, 0.8, 120),
                "unlock_overhang_proxy_rank_full": np.linspace(0.3, 0.7, 120),
                "unlock_fragility_proxy_rank_fallback": np.linspace(0.4, 0.6, 120),
                "y_meta": np.where(np.arange(120) % 3 == 0, 1, 0),
            }
        )

        with patch.object(phase4, "_load_vi_cluster_map", return_value=None):
            with patch.object(phase4, "UNLOCK_MODEL_FEATURE_SET", "baseline"):
                baseline_features = phase4.select_features(df)
            with patch.object(phase4, "UNLOCK_MODEL_FEATURE_SET", "proxies"):
                proxy_features = phase4.select_features(df)

        self.assertNotIn("unlock_pressure_rank_observed", baseline_features)
        self.assertNotIn("unlock_pressure_rank_reconstructed", baseline_features)
        self.assertNotIn("unlock_overhang_proxy_rank_full", baseline_features)
        self.assertIn("unlock_overhang_proxy_rank_full", proxy_features)
        self.assertIn("unlock_fragility_proxy_rank_fallback", proxy_features)
        self.assertNotIn("unlock_pressure_rank_observed", proxy_features)
        self.assertNotIn("unlock_pressure_rank_reconstructed", proxy_features)

    def test_select_features_allows_full_unlock_set_when_enabled(self) -> None:
        df = pd.DataFrame(
            {
                "p_bma_pkf": np.linspace(0.1, 0.9, 120),
                "unlock_pressure_rank_observed": np.linspace(0.1, 0.9, 120),
                "unlock_pressure_rank_reconstructed": np.linspace(0.2, 0.8, 120),
                "unlock_overhang_proxy_rank_full": np.linspace(0.3, 0.7, 120),
                "unlock_fragility_proxy_rank_fallback": np.linspace(0.4, 0.6, 120),
                "close_fracdiff": np.linspace(-0.2, 0.2, 120),
                "y_meta": np.where(np.arange(120) % 3 == 0, 1, 0),
            }
        )

        with patch.object(
            phase4,
            "_load_vi_cluster_map",
            return_value={
                "u_obs": ["unlock_pressure_rank_observed"],
                "u_rec": ["unlock_pressure_rank_reconstructed"],
                "u_full": ["unlock_overhang_proxy_rank_full"],
                "u_fb": ["unlock_fragility_proxy_rank_fallback"],
            },
        ):
            with patch.object(phase4, "UNLOCK_MODEL_FEATURE_SET", "full"):
                features = phase4.select_features(df)

        self.assertIn("unlock_pressure_rank_observed", features)
        self.assertIn("unlock_pressure_rank_reconstructed", features)
        self.assertIn("unlock_overhang_proxy_rank_full", features)
        self.assertIn("unlock_fragility_proxy_rank_fallback", features)

    def test_symbol_cluster_artifact_is_derived_and_loaded_without_missing_artifact_fallback(self) -> None:
        symbols = [f"S{i:02d}" for i in range(8)]
        rows = []
        for idx, symbol in enumerate(symbols):
            for step in range(60):
                rows.append(
                    {
                        "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=step),
                        "symbol": symbol,
                        "p_bma_pkf": 0.2 + idx * 0.05 + step * 0.0005,
                        "ret_1d": (-0.02 if idx < 4 else 0.03) + step * 0.0002,
                        "stablecoin_chg30": (0.01 if idx < 4 else -0.01),
                        "unlock_overhang_proxy_rank_full": 0.1 * idx,
                    }
                )
        pooled_df = pd.DataFrame(rows)

        with tempfile.TemporaryDirectory() as tmp_dir:
            model_path = Path(tmp_dir)
            (model_path / "calibration").mkdir(parents=True, exist_ok=True)
            with (
                patch.object(phase4, "MODEL_PATH", model_path),
                patch.object(phase4, "UNLOCK_MODEL_FEATURE_SET", "proxies"),
            ):
                artifact_path = phase4._ensure_symbol_vi_cluster_artifact(
                    pooled_df,
                    ["ret_1d", "stablecoin_chg30", "unlock_overhang_proxy_rank_full"],
                )
                clusters, symbol_to_cluster, mode, loaded_path = phase4._load_symbol_vi_clusters(symbols)
                self.assertIsNotNone(artifact_path)
                self.assertTrue(Path(artifact_path).exists())
                self.assertEqual(mode, "artifact")
                self.assertEqual(loaded_path, artifact_path)
                self.assertGreaterEqual(len(clusters), 2)
                self.assertEqual(set(symbol_to_cluster), set(symbols))


if __name__ == "__main__":
    unittest.main()
