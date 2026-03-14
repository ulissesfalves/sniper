from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests import _path_setup  # noqa: F401
from services.ml_engine import main as ml_main
from services.ml_engine import phase4_cpcv as phase4


class BaselineCoreAblationTest(unittest.TestCase):
    def test_build_meta_target_can_use_realized_positive_pnl(self) -> None:
        index = pd.date_range("2024-01-01", periods=4, freq="D")
        barrier_df = pd.DataFrame(
            {
                "label": [0, 1, 0, -1],
                "pnl_real": [0.01, 0.05, -0.02, -0.08],
            },
            index=index,
        )

        with patch.object(ml_main, "META_TARGET_MODE", "pnl_positive"):
            target = ml_main._build_meta_target(barrier_df, "TEST")

        self.assertEqual(target.tolist(), [1, 1, 0, 0])

    def test_compute_realized_trade_buckets_uses_all_positive_and_negative_pnl(self) -> None:
        barrier_df = pd.DataFrame(
            {
                "label": [0, 1, 0, -1],
                "pnl_real": [0.04, 0.06, -0.02, -0.08],
            }
        )

        avg_gain, avg_loss = ml_main._compute_realized_trade_buckets(barrier_df)

        self.assertAlmostEqual(avg_gain, 0.05, places=6)
        self.assertAlmostEqual(avg_loss, 0.05, places=6)

    def test_run_meta_labeling_can_keep_hmm_as_feature_without_hard_gate(self) -> None:
        captured: dict[str, object] = {}
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
            },
            index=index,
        )
        barrier_df = pd.DataFrame(
            {
                "label": [1, -1, 0, 0, 1],
                "pnl_real": [0.05, -0.04, 0.01, -0.01, 0.06],
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
            captured["feature_columns"] = kwargs["feature_df"].columns.tolist()
            captured["hmm_series"] = kwargs["hmm_series"]
            captured["drop_hmm_feature"] = kwargs["drop_hmm_feature"]
            return pd.Series(0.6, index=kwargs["feature_df"].index, name="p_bma_pkf")

        def fake_run_isotonic_walk_forward(**kwargs) -> pd.Series:
            return kwargs["p_raw_series"]

        with (
            patch.object(ml_main, "MODEL_PATH", "tests_tmp_models"),
            patch.object(ml_main, "HMM_META_FEATURE_MODE", "include"),
            patch.object(ml_main, "HMM_HARD_GATE_MODE", "off"),
            patch("meta_labeling.uniqueness.compute_label_uniqueness", side_effect=fake_compute_label_uniqueness),
            patch("meta_labeling.uniqueness.compute_effective_n", side_effect=fake_compute_effective_n),
            patch("meta_labeling.uniqueness.compute_meta_sample_weights", side_effect=fake_compute_meta_sample_weights),
            patch("meta_labeling.pbma_purged.generate_pbma_purged_kfold", side_effect=fake_generate_pbma_purged_kfold),
            patch("meta_labeling.isotonic_calibration.run_isotonic_walk_forward", side_effect=fake_run_isotonic_walk_forward),
        ):
            result = ml_main.run_meta_labeling_for_symbol(features, barrier_df, hmm_result, sigma_ewma, "TEST")

        self.assertIsNotNone(result)
        self.assertIn("hmm_prob_bull", captured["feature_columns"])
        self.assertIsNone(captured["hmm_series"])
        self.assertFalse(captured["drop_hmm_feature"])

    def test_phase4_select_features_can_include_hmm_probability(self) -> None:
        df = pd.DataFrame(
            {
                "p_bma_pkf": np.linspace(0.1, 0.9, 120),
                "hmm_prob_bull": np.linspace(0.2, 0.8, 120),
                "ret_1d": np.linspace(-0.2, 0.2, 120),
                "close_fracdiff": np.linspace(-0.2, 0.2, 120),
                "y_meta": np.where(np.arange(120) % 3 == 0, 1, 0),
            }
        )

        with patch.object(phase4, "_load_vi_cluster_map", return_value=None):
            with patch.object(phase4, "HMM_META_FEATURE_MODE", "include"):
                selected = phase4.select_features(df)
            with patch.object(phase4, "HMM_META_FEATURE_MODE", "exclude"):
                selected_excluded = phase4.select_features(df)

        self.assertIn("hmm_prob_bull", selected)
        self.assertNotIn("hmm_prob_bull", selected_excluded)

    def test_phase4_prepare_feature_matrix_uses_neutral_fill_for_hmm_probability(self) -> None:
        df = pd.DataFrame(
            {
                "hmm_prob_bull": [np.nan, 0.8],
                "ret_1d": [np.nan, 0.02],
            }
        )

        matrix = phase4._prepare_feature_matrix(df, ["hmm_prob_bull", "ret_1d"])

        self.assertAlmostEqual(float(matrix[0, 0]), 0.5, places=6)
        self.assertAlmostEqual(float(matrix[0, 1]), 0.0, places=6)

    def test_phase4_trade_stats_use_realized_pnl_sign(self) -> None:
        df = pd.DataFrame(
            {
                "symbol": ["AAA", "AAA", "AAA", "BBB"],
                "label": [0, 1, -1, 0],
                "pnl_real": [0.04, 0.06, -0.02, -0.08],
            }
        )

        stats, global_tp, global_sl = phase4._compute_symbol_trade_stats(df)

        self.assertAlmostEqual(global_tp, 0.05, places=6)
        self.assertAlmostEqual(global_sl, 0.05, places=6)
        self.assertAlmostEqual(stats["AAA"]["avg_tp"], 0.05, places=6)
        self.assertAlmostEqual(stats["AAA"]["avg_sl"], 0.02, places=6)

    def test_phase4_execution_pnl_scales_stop_slippage_with_actual_position(self) -> None:
        df = pd.DataFrame(
            {
                "label": [-1, 1],
                "pnl_real": [-0.28, 0.12],
                "slippage_frac": [0.20, 0.0],
                "barrier_sl": [90.0, 0.0],
                "p0": [100.0, 100.0],
                "position_usdt_policy": [2500.0, 2500.0],
            }
        )

        with patch.object(phase4, "CAPITAL_INITIAL", 200_000), patch.object(phase4, "TB_REFERENCE_POSITION_FRAC", 0.05):
            repriced = phase4._attach_execution_pnl(df, "position_usdt_policy", "pnl_exec_policy")

        self.assertAlmostEqual(float(repriced.loc[0, "pnl_exec_policy"]), -0.19, places=6)
        self.assertAlmostEqual(float(repriced.loc[0, "slippage_exec_policy"]), 0.10, places=6)
        self.assertAlmostEqual(float(repriced.loc[1, "pnl_exec_policy"]), 0.12, places=6)

    def test_phase4_friction_stress_pnl_worsens_with_higher_cost(self) -> None:
        df = pd.DataFrame(
            {
                "label": [-1, 1],
                "pnl_exec_base": [-0.19, 0.12],
                "slippage_base": [0.10, 0.00],
                "barrier_sl": [90.0, np.nan],
                "p0": [100.0, 100.0],
                "position_usdt_policy": [2000.0, 2000.0],
            }
        )

        mild = phase4._attach_friction_stress_pnl(
            df,
            base_pnl_col="pnl_exec_base",
            base_slippage_col="slippage_base",
            position_col="position_usdt_policy",
            output_col="pnl_exec_mild",
            slippage_mult=1.25,
            extra_cost_bps=5.0,
        )
        hard = phase4._attach_friction_stress_pnl(
            df,
            base_pnl_col="pnl_exec_base",
            base_slippage_col="slippage_base",
            position_col="position_usdt_policy",
            output_col="pnl_exec_hard",
            slippage_mult=2.0,
            extra_cost_bps=20.0,
        )

        self.assertLess(float(hard.loc[0, "pnl_exec_hard"]), float(mild.loc[0, "pnl_exec_mild"]))
        self.assertLess(float(hard.loc[1, "pnl_exec_hard"]), float(mild.loc[1, "pnl_exec_mild"]))

    def test_phase4_build_policy_signal_can_apply_top_n_and_regime_filter(self) -> None:
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    [
                        "2024-01-01",
                        "2024-01-01",
                        "2024-01-02",
                        "2024-01-02",
                    ]
                ),
                "p_bma_pkf_exec": [0.81, 0.79, 0.84, 0.83],
                "hmm_prob_bull": [0.60, 0.70, 0.40, 0.90],
            }
        )

        signal = phase4._build_policy_signal(
            df,
            score_col="p_bma_pkf_exec",
            threshold=0.80,
            top_n_per_day=1,
            regime_col="hmm_prob_bull",
            regime_min=0.55,
        )

        self.assertEqual(signal.tolist(), [0.81, 0.0, 0.0, 0.83])

    def test_phase4_build_policy_signal_can_apply_cooldown_and_daily_cap(self) -> None:
        df = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    [
                        "2024-01-01",
                        "2024-01-01",
                        "2024-01-01",
                        "2024-01-02",
                        "2024-01-03",
                        "2024-01-05",
                    ]
                ),
                "symbol": ["AAA", "BBB", "CCC", "AAA", "AAA", "AAA"],
                "p_bma_pkf_exec": [0.90, 0.88, 0.86, 0.91, 0.92, 0.93],
            }
        )

        signal = phase4._build_policy_signal(
            df,
            score_col="p_bma_pkf_exec",
            threshold=0.80,
            cooldown_days=3,
            max_daily_exposure_frac=0.02,
            alloc_frac=0.01,
        )

        self.assertEqual(signal.tolist(), [0.90, 0.88, 0.0, 0.0, 0.0, 0.93])

    def test_phase4_score_bucket_diagnostics_reports_tail_bucket(self) -> None:
        df = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=6, freq="D"),
                "symbol": ["AAA"] * 6,
                "score": [0.56, 0.62, 0.68, 0.74, 0.78, 0.84],
                "pnl_exec": [0.01, -0.02, 0.03, -0.01, 0.02, 0.04],
                "position": [2000.0] * 6,
            }
        )

        buckets = phase4._build_score_bucket_diagnostics(
            df,
            score_col="score",
            pnl_col="pnl_exec",
            position_col="position",
        )

        tail_bucket = next(row for row in buckets if row["bucket"] == ">0.80")
        self.assertEqual(tail_bucket["n_trades"], 1)
        self.assertAlmostEqual(float(tail_bucket["mean_score"]), 0.84, places=6)

    def test_phase4_policy_ablation_reports_expected_scenarios(self) -> None:
        index = pd.date_range("2024-01-01", periods=12, freq="D")
        pooled = pd.DataFrame(
            {
                "date": index,
                "symbol": ["AAA"] * len(index),
                "p_bma_pkf": np.linspace(0.60, 0.82, len(index)),
                "pnl_real": np.linspace(-0.05, 0.08, len(index)),
                "label": np.where(np.arange(len(index)) % 4 == 0, -1, 1),
                "slippage_frac": np.where(np.arange(len(index)) % 4 == 0, 0.20, 0.0),
                "barrier_sl": np.where(np.arange(len(index)) % 4 == 0, 90.0, np.nan),
                "p0": 100.0,
                "sigma_ewma": np.linspace(0.10, 0.18, len(index)),
                "hmm_prob_bull": np.linspace(0.55, 0.80, len(index)),
            }
        )

        with (
            patch.object(phase4, "CAPITAL_INITIAL", 200_000),
            patch.object(phase4, "PHASE4_DECISION_POLICY", "fixed_small_080"),
            patch.object(phase4, "PHASE4_TOP_N_PER_DAY", 1),
            patch.object(phase4, "PHASE4_REGIME_MIN", 0.55),
        ):
            result = phase4.evaluate_fallback(pooled)

        self.assertIn("current_kelly_065", result["policy_ablation"])
        self.assertIn("fixed_small_075", result["policy_ablation"])
        self.assertIn("fixed_small_080", result["policy_ablation"])
        self.assertIn("fixed_small_075_top1", result["policy_ablation"])
        self.assertIn("fixed_small_080_hmm55", result["policy_ablation"])
        self.assertEqual(result["policy"], "fixed_small_080")
        self.assertIn("score_bucket_diagnostics", result)
        self.assertIn("fixed_small_078", result["local_threshold_sensitivity"])
        self.assertIn("fixed_small_085", result["local_threshold_sensitivity"])
        self.assertIn("fixed_small_080", result["temporal_robustness"])
        self.assertIn("stress_hard", result["friction_stress"])
        self.assertIn("operational_robustness", result)
        self.assertIn("holdout_validation", result)
        self.assertIn("forward_validation", result)
        self.assertIn("rolling_stability", result)
        self.assertEqual(result["execution_repricing"]["mode"], "scaled_from_reference_slippage")
        self.assertGreaterEqual(
            result["friction_stress"]["stress_base"]["cum_return"],
            result["friction_stress"]["stress_hard"]["cum_return"],
        )

    def test_phase4_policy_holdout_and_rolling_can_run_on_sufficient_history(self) -> None:
        index = pd.date_range("2022-01-01", periods=500, freq="D")
        score = np.where(np.arange(len(index)) % 11 == 0, 0.86, np.where(np.arange(len(index)) % 7 == 0, 0.81, 0.76))
        pnl = np.where(score > 0.82, 0.08, np.where(score > 0.79, 0.03, -0.02))
        df = pd.DataFrame(
            {
                "date": index,
                "symbol": ["AAA"] * len(index),
                "p_bma_pkf_exec": score,
                "position_usdt_policy_tail": 2000.0,
                "pnl_exec_selective": pnl,
                "label": np.where(pnl > 0, 1, -1),
                "barrier_sl": np.where(pnl > 0, np.nan, 90.0),
                "p0": 100.0,
                "slippage_selective": np.where(pnl > 0, 0.0, 0.05),
            }
        )
        candidate_specs = [
            {
                "label": f"fixed_small_{int(round(thr * 100)):03d}",
                "threshold": thr,
                "position_col": "position_usdt_policy_tail",
                "pnl_col": "pnl_exec_selective",
                "signal_kwargs": {},
            }
            for thr in phase4.PHASE4_LOCAL_THRESHOLD_GRID
        ]

        holdout = phase4._evaluate_policy_holdout_validation(
            df,
            candidate_specs=candidate_specs,
            benchmark_label="fixed_small_080",
        )
        forward = phase4._evaluate_policy_forward_validation(
            df,
            candidate_specs=candidate_specs,
            benchmark_label="fixed_small_080",
        )
        rolling = phase4._build_rolling_policy_stability(
            df,
            label="fixed_small_080",
            threshold=0.80,
            position_col="position_usdt_policy_tail",
            pnl_col="pnl_exec_selective",
        )

        self.assertEqual(holdout["status"], "ok")
        self.assertEqual(forward["status"], "ok")
        self.assertEqual(rolling["status"], "ok")
        self.assertIn("fixed_small_080", holdout["benchmark_label"])
        self.assertGreaterEqual(forward["n_folds"], 1)
        self.assertGreaterEqual(rolling["n_valid_windows"], 1)


if __name__ == "__main__":
    unittest.main()
