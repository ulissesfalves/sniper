from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tests import _path_setup  # noqa: F401
from services.data_inserter.collectors.unlock_support.store import UnlockFeatureStore


def _read_local_table(path: Path) -> pd.DataFrame:
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.read_pickle(path)


class UnlockStoreTest(unittest.TestCase):
    def test_store_persists_sqlite_exports_parquet_and_closes_connections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            sqlite_path = base / "sqlite" / "sniper.db"
            parquet_base = base / "parquet"
            store = UnlockFeatureStore(str(sqlite_path), str(parquet_base))

            store.persist_feature_rows(
                [
                    {
                        "date": "2024-01-31",
                        "asset_id": "OBS",
                        "ups_raw_observed": 0.1,
                        "unlock_pressure_rank_observed": 0.0,
                        "ups_raw_reconstructed": None,
                        "unlock_pressure_rank_reconstructed": None,
                        "proxy_full_raw": 0.2,
                        "unlock_overhang_proxy_rank_full": 0.1,
                        "proxy_fallback_raw": 0.3,
                        "unlock_fragility_proxy_rank_fallback": 0.2,
                        "unlock_pressure_rank_selected_for_reporting": 0.0,
                        "unlock_feature_state": "OBSERVED",
                        "unknown_bucket_ratio": 0.0,
                        "quality_flag": "ok",
                        "reconstruction_confidence": None,
                    }
                ]
            )
            store.persist_diagnostic_rows(
                [
                    {
                        "date": "2024-01-31",
                        "asset_id": "OBS",
                        "circ": 100.0,
                        "total_supply": 150.0,
                        "insider_share_pti": 0.25,
                        "insider_share_mode": "observed",
                        "avg_30d_volume": 100.0,
                        "market_cap": 400.0,
                        "source_primary": "mobula",
                        "snapshot_ts": "2024-01-31T00:00:00+00:00",
                        "supply_history_mode": "observed_supply",
                    }
                ]
            )
            store.persist_quality_rows(
                [
                    {
                        "date": "2024-01-31",
                        "n_assets": 1,
                        "observed_count": 1,
                        "reconstructed_count": 0,
                        "proxy_full_count": 1,
                        "proxy_fallback_count": 1,
                        "missing_count": 0,
                        "observed_coverage": 1.0,
                        "reconstructed_coverage": 0.0,
                        "proxy_full_coverage": 1.0,
                        "proxy_fallback_coverage": 1.0,
                        "missing_rate": 0.0,
                        "unknown_bucket_block_count": 0,
                        "unknown_bucket_block_rate": 0.0,
                        "raw_zero_count": 0,
                        "raw_zero_fraction": 0.0,
                        "massive_ties_fraction": 1.0,
                        "review_required_count": 0,
                        "promotion_blocked_count": 0,
                        "shadow_mode_flag": 1,
                        "snapshot_lag_days_avg": 0.0,
                        "snapshot_lag_days_max": 0.0,
                        "provider_failures_mobula": 0,
                        "provider_failures_coingecko": 0,
                        "provider_failures_defillama": None,
                        "provider_failures_wayback": 0,
                        "rank_distribution_json": {"selected_for_reporting": {"n": 1, "p50": 0.0}},
                    }
                ]
            )

            exported = store.export_feature_parquet(["OBS"])
            quality_path = store.export_quality_parquet()

            self.assertEqual(len(exported), 1)
            self.assertTrue(exported[0].exists())
            self.assertIsNotNone(quality_path)
            self.assertTrue(quality_path.exists())

            feature_sql = store.load_table("feature_unlock_daily")
            diag_sql = store.load_table("feature_unlock_diagnostics")
            quality_sql = store.load_table("feature_unlock_quality_daily")

            self.assertEqual(len(feature_sql), 1)
            self.assertEqual(len(diag_sql), 1)
            self.assertEqual(len(quality_sql), 1)

            feature_parquet = _read_local_table(exported[0])
            quality_parquet = _read_local_table(quality_path)

            self.assertIn("unlock_pressure_rank_observed", feature_parquet.columns)
            self.assertIn("source_primary", feature_parquet.columns)
            self.assertIn("rank_distribution_json", quality_parquet.columns)

            renamed = sqlite_path.with_name("sniper_renamed.db")
            sqlite_path.rename(renamed)
            self.assertTrue(renamed.exists())


if __name__ == "__main__":
    unittest.main()
