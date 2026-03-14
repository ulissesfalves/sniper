from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from tests import _path_setup  # noqa: F401
from features.onchain import UNLOCK_AUDIT_COLUMNS, UNLOCK_MODEL_FEATURE_COLUMNS
from services.data_inserter.collectors.token_unlocks import LayerComputation, TokenUnlocksCollector
from services.data_inserter.collectors.unlock_support.providers import AssetDescriptor
from services.ml_engine import main as ml_main


FIXTURE_DATE = pd.Timestamp("2024-01-31").date()
FIXTURE_TS = "2024-01-31T00:00:00+00:00"


class FixtureTokenUnlocksCollector(TokenUnlocksCollector):
    async def _prepare_descriptors(self, session, descriptors):  # type: ignore[override]
        return [
            AssetDescriptor(asset_id=descriptor.asset_id, symbol=descriptor.symbol, coingecko_id=descriptor.asset_id.lower())
            for descriptor in descriptors
        ]

    async def _collect_live_snapshots(self, session, descriptors):  # type: ignore[override]
        bundle = {}
        cg_rows = []
        mobula_rows = []
        for descriptor in descriptors:
            asset_id = descriptor.asset_id
            market_row = self._fixture_market_row(asset_id)
            if market_row:
                cg_rows.append(
                    {
                        "snapshot_ts": FIXTURE_TS,
                        "cg_id": descriptor.coingecko_id,
                        "payload_json": market_row,
                        "payload_hash": f"cg-{asset_id}",
                        "http_status": 200,
                    }
                )

            mobula_payload = None
            mobula_status = 404
            if asset_id == "OBS":
                mobula_status = 200
                mobula_payload = {
                    "release_schedule": [
                        {"date": "2024-02-10", "allocationName": "team", "unlockAmount": 10.0},
                    ],
                    "distribution": [
                        {"allocationName": "team", "share": 0.30},
                        {"allocationName": "seed", "share": 0.20},
                    ],
                    "circulating_supply": 100.0,
                    "total_supply": 180.0,
                }
                mobula_rows.append(
                    {
                        "snapshot_ts": FIXTURE_TS,
                        "asset_id": asset_id,
                        "payload_json": mobula_payload,
                        "payload_hash": f"mobula-{asset_id}",
                        "http_status": mobula_status,
                    }
                )

            bundle[asset_id] = {
                "snapshot_ts": FIXTURE_TS,
                "coingecko_status": 200 if market_row else 404,
                "market_row": market_row,
                "market_row_present": bool(market_row),
                "mobula_payload": mobula_payload,
                "mobula_status": mobula_status,
                "mobula_snapshot_ts": FIXTURE_TS if mobula_payload else None,
                "defillama_payload": None,
                "defillama_status": 404,
            }

        self.store.persist_raw_json("raw_coingecko_snapshots", cg_rows)
        self.store.persist_raw_json("raw_mobula_snapshots", mobula_rows)
        return bundle

    async def _collect_coin_details(self, session, descriptors):  # type: ignore[override]
        return {descriptor.asset_id: {} for descriptor in descriptors}

    async def _build_market_history(self, session, descriptor, live_row):  # type: ignore[override]
        row = self._fixture_market_row(descriptor.asset_id)
        frame = pd.DataFrame(
            [
                {
                    "asset_id": descriptor.asset_id,
                    "date": FIXTURE_DATE,
                    "price_usd": row.get("price_usd"),
                    "market_cap": row.get("market_cap"),
                    "total_volume": row.get("total_volume"),
                    "circ_approx": row.get("circulating_supply"),
                    "total_supply": row.get("total_supply"),
                    "avg_30d_volume": row.get("total_volume"),
                }
            ]
        )
        return frame

    async def _collect_reconstructed_layers(self, session, descriptors, history_map, details_map):  # type: ignore[override]
        return {
            "REC": {
                FIXTURE_DATE: LayerComputation(
                    raw=0.24,
                    is_valid=True,
                    confidence=0.90,
                    unknown_ratio=0.0,
                    insider_share=0.40,
                    quality_flag="ok",
                    source_primary="wayback",
                    snapshot_ts="2024-01-20T00:00:00+00:00",
                    circ=100.0,
                    total_supply=180.0,
                )
            }
        }

    @staticmethod
    def _fixture_market_row(asset_id: str) -> dict[str, float | None]:
        mapping = {
            "OBS": {"price_usd": 4.0, "market_cap": 400.0, "total_volume": 100.0, "circulating_supply": 100.0, "total_supply": 180.0},
            "REC": {"price_usd": 4.0, "market_cap": 400.0, "total_volume": 100.0, "circulating_supply": 100.0, "total_supply": None},
            "PFULL": {"price_usd": 4.0, "market_cap": 400.0, "total_volume": 100.0, "circulating_supply": 100.0, "total_supply": 160.0},
            "PFB": {"price_usd": 4.0, "market_cap": 400.0, "total_volume": 100.0, "circulating_supply": 100.0, "total_supply": None},
            "MISS": {"price_usd": 4.0, "market_cap": None, "total_volume": None, "circulating_supply": 100.0, "total_supply": None},
        }
        return mapping[asset_id]


class UnlockPipelineE2ETest(unittest.TestCase):
    def test_unlock_pipeline_e2e_uses_orthogonal_columns_only_for_training(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            collector = FixtureTokenUnlocksCollector(
                parquet_base=str(tmp_path / "parquet"),
                sqlite_path=str(tmp_path / "sqlite" / "sniper.db"),
            )

            exported = asyncio.run(collector.fetch_and_store(["OBS", "REC", "PFULL", "PFB", "MISS"]))
            self.assertEqual(len(exported), 5)

            daily = collector.store.load_table("feature_unlock_daily").set_index("asset_id")
            self.assertEqual(daily.loc["OBS", "unlock_feature_state"], "OBSERVED")
            self.assertEqual(daily.loc["REC", "unlock_feature_state"], "RECONSTRUCTED")
            self.assertEqual(daily.loc["PFULL", "unlock_feature_state"], "PROXY_FULL")
            self.assertEqual(daily.loc["PFB", "unlock_feature_state"], "PROXY_FALLBACK")
            self.assertEqual(daily.loc["MISS", "unlock_feature_state"], "MISSING")

            self.assertTrue(pd.notna(daily.loc["PFULL", "unlock_overhang_proxy_rank_full"]))
            self.assertTrue(pd.isna(daily.loc["PFB", "unlock_overhang_proxy_rank_full"]))
            self.assertTrue(pd.notna(daily.loc["PFB", "unlock_fragility_proxy_rank_fallback"]))
            self.assertTrue(daily.loc["MISS", UNLOCK_MODEL_FEATURE_COLUMNS].isna().all())

            quality = collector.store.load_table("feature_unlock_quality_daily")
            self.assertEqual(int(quality.iloc[0]["n_assets"]), 5)
            self.assertTrue((tmp_path / "parquet" / "unlock_diagnostics" / "unlock_quality_daily.parquet").exists())

            captured = {}
            ohlcv_index = pd.date_range("2024-01-31", periods=5, freq="D", tz="UTC")
            ohlcv = pd.DataFrame(
                {
                    "open": np.linspace(10.0, 14.0, len(ohlcv_index)),
                    "high": np.linspace(11.0, 15.0, len(ohlcv_index)),
                    "low": np.linspace(9.0, 13.0, len(ohlcv_index)),
                    "close": np.linspace(10.5, 14.5, len(ohlcv_index)),
                    "volume": np.linspace(1000.0, 1400.0, len(ohlcv_index)),
                },
                index=ohlcv_index,
            )

            with (
                patch.object(ml_main, "PARQUET_BASE", str(tmp_path / "parquet")),
                patch.object(ml_main, "UNLOCK_MODEL_FEATURE_SET", "full"),
            ):
                for symbol in ["OBS", "REC", "PFULL", "PFB", "MISS"]:
                    unlock_frame = ml_main.load_unlock_feature_frame(symbol)
                    self.assertTrue(set(UNLOCK_MODEL_FEATURE_COLUMNS).issubset(unlock_frame.columns))
                    self.assertTrue(set(UNLOCK_AUDIT_COLUMNS).issubset(unlock_frame.columns))
                    features = ml_main.compute_base_features(ohlcv, symbol, unlock_frame=unlock_frame)
                    self.assertTrue(set(UNLOCK_MODEL_FEATURE_COLUMNS).issubset(features.columns))
                    self.assertTrue(set(UNLOCK_AUDIT_COLUMNS).issubset(features.columns))

                unlock_frame_training = pd.DataFrame(
                    {
                        "unlock_pressure_rank_observed": np.linspace(0.1, 0.5, len(ohlcv_index)),
                        "unlock_pressure_rank_reconstructed": np.linspace(0.2, 0.6, len(ohlcv_index)),
                        "unlock_overhang_proxy_rank_full": np.linspace(0.3, 0.7, len(ohlcv_index)),
                        "unlock_fragility_proxy_rank_fallback": np.linspace(0.4, 0.8, len(ohlcv_index)),
                        "unlock_feature_state": ["OBSERVED"] * len(ohlcv_index),
                        "quality_flag": ["ok"] * len(ohlcv_index),
                        "source_primary": ["mobula"] * len(ohlcv_index),
                        "snapshot_ts": [FIXTURE_TS] * len(ohlcv_index),
                    },
                    index=ohlcv_index,
                )
                features_obs = ml_main.compute_base_features(ohlcv, "OBS", unlock_frame=unlock_frame_training)
                barrier_df = pd.DataFrame(
                    {"label": [1, 0, 1, 0, 1], "t_touch": ohlcv_index + pd.Timedelta(days=1)},
                    index=ohlcv_index,
                )
                hmm_result = pd.DataFrame({"hmm_prob_bull": np.linspace(0.6, 0.9, len(ohlcv_index))}, index=ohlcv_index)
                sigma_ewma = pd.Series(np.linspace(0.1, 0.3, len(ohlcv_index)), index=ohlcv_index)

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
                    patch("meta_labeling.uniqueness.compute_label_uniqueness", side_effect=fake_compute_label_uniqueness),
                    patch("meta_labeling.uniqueness.compute_effective_n", side_effect=fake_compute_effective_n),
                    patch("meta_labeling.uniqueness.compute_meta_sample_weights", side_effect=fake_compute_meta_sample_weights),
                    patch("meta_labeling.pbma_purged.generate_pbma_purged_kfold", side_effect=fake_generate_pbma_purged_kfold),
                    patch("meta_labeling.isotonic_calibration.run_isotonic_walk_forward", side_effect=fake_run_isotonic_walk_forward),
                ):
                    result = ml_main.run_meta_labeling_for_symbol(features_obs, barrier_df, hmm_result, sigma_ewma, "OBS")

            self.assertIsNotNone(result)
            self.assertEqual(set(UNLOCK_MODEL_FEATURE_COLUMNS) & set(captured["feature_df"].columns), set(UNLOCK_MODEL_FEATURE_COLUMNS))
            self.assertTrue(set(UNLOCK_AUDIT_COLUMNS).isdisjoint(captured["feature_df"].columns))


if __name__ == "__main__":
    unittest.main()
