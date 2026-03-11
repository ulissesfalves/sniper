from __future__ import annotations

import asyncio
import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd
import polars as pl

from tests import _path_setup  # noqa: F401
from services.data_inserter.collectors.token_unlocks import LayerComputation, TokenUnlocksCollector
from services.data_inserter.collectors.unlock_support.providers import AssetDescriptor


def _make_collector(tmp_path: Path) -> TokenUnlocksCollector:
    parquet_base = tmp_path / "parquet"
    sqlite_path = tmp_path / "sqlite" / "sniper.db"
    return TokenUnlocksCollector(parquet_base=str(parquet_base), sqlite_path=str(sqlite_path))


class TokenUnlocksCollectorTest(unittest.TestCase):
    def test_compute_observed_layer_invalid_when_circ_is_non_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            observed = collector._compute_observed_layer(
                descriptor=AssetDescriptor(asset_id="ABC", symbol="ABC", coingecko_id=None),
                as_of_date=pd.Timestamp("2024-01-01").date(),
                mobula_payload={
                    "release_schedule": [
                        {"date": "2024-01-10", "allocationName": "team", "unlockAmount": 10},
                    ],
                    "circulating_supply": 0,
                },
                mobula_snapshot_ts="2024-01-01T00:00:00+00:00",
                market_row={"circulating_supply": 0},
            )

        self.assertFalse(observed.is_valid)
        self.assertIsNone(observed.raw)
        self.assertEqual(observed.quality_flag, "invalid_circ")

    def test_compute_observed_layer_rejects_unknown_bucket_ratio_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            observed = collector._compute_observed_layer(
                descriptor=AssetDescriptor(asset_id="ABC", symbol="ABC", coingecko_id=None),
                as_of_date=pd.Timestamp("2024-01-01").date(),
                mobula_payload={
                    "release_schedule": [
                        {"date": "2024-01-10", "allocationName": "team", "unlockAmount": 80},
                        {"date": "2024-01-12", "allocationName": "mystery tranche", "unlockAmount": 20},
                    ],
                    "circulating_supply": 100,
                    "total_supply": 200,
                },
                mobula_snapshot_ts="2024-01-01T00:00:00+00:00",
                market_row={"circulating_supply": 100, "total_supply": 200},
            )

        self.assertFalse(observed.is_valid)
        self.assertIsNone(observed.raw)
        self.assertEqual(observed.unknown_ratio, 0.2)
        self.assertEqual(observed.quality_flag, "review_required")

    def test_proxy_full_and_fallback_use_distinct_formulas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            market_row = pd.Series({"circ_approx": 100.0, "market_cap": 400.0, "avg_30d_volume": 100.0})
            proxy_full = collector._compute_proxy_full(market_row, total_supply=150.0, insider_share=0.25)
            proxy_fallback = collector._compute_proxy_fallback(market_row, insider_share=0.25)

        self.assertTrue(proxy_full.is_valid)
        self.assertTrue(proxy_fallback.is_valid)
        self.assertEqual(proxy_full.raw, math.log1p(0.5) * 1.25 * 2.0)
        self.assertEqual(proxy_fallback.raw, 2.0 * 1.25)

    def test_proxy_full_requires_total_supply_and_proxy_fallback_requires_liquidity_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            proxy_full_row = pd.Series({"circ_approx": 100.0, "market_cap": 400.0, "avg_30d_volume": 100.0})
            proxy_fallback_row = pd.Series({"circ_approx": 100.0, "market_cap": 400.0, "avg_30d_volume": 0.0})
            proxy_full_missing_supply = collector._compute_proxy_full(proxy_full_row, total_supply=None, insider_share=0.10)
            proxy_fallback_missing_volume = collector._compute_proxy_fallback(proxy_fallback_row, insider_share=0.10)

        self.assertFalse(proxy_full_missing_supply.is_valid)
        self.assertEqual(proxy_full_missing_supply.quality_flag, "proxy_full_total_supply_missing")
        self.assertFalse(proxy_fallback_missing_volume.is_valid)
        self.assertEqual(proxy_fallback_missing_volume.quality_flag, "proxy_fallback_inputs_missing")

    def test_choose_total_supply_prefers_observed_then_reconstructed_then_market(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            market_row = pd.Series({"total_supply": 300.0})

            self.assertEqual(
                collector._choose_total_supply(
                    market_row,
                    LayerComputation(total_supply=200.0),
                    LayerComputation(total_supply=250.0),
                ),
                200.0,
            )
            self.assertEqual(
                collector._choose_total_supply(
                    market_row,
                    LayerComputation(),
                    LayerComputation(total_supply=250.0),
                ),
                250.0,
            )
            self.assertEqual(
                collector._choose_total_supply(
                    market_row,
                    LayerComputation(),
                    LayerComputation(),
                ),
                300.0,
            )

    def test_missing_long_tail_insider_share_zeroes_pti_lite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            insider_share = collector._choose_insider_share(LayerComputation(), LayerComputation())
            mode = collector._insider_share_mode(LayerComputation(), LayerComputation(), insider_share)

        self.assertEqual(insider_share, 0.0)
        self.assertEqual(mode, "missing_zeroed")

    def test_selected_for_reporting_prefers_observed_then_reconstructed_then_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            rows = [
                {
                    "date": "2024-01-01",
                    "asset_id": "OBS",
                    "ups_raw_observed": 2.0,
                    "unlock_pressure_rank_observed": None,
                    "ups_raw_reconstructed": 0.9,
                    "unlock_pressure_rank_reconstructed": None,
                    "proxy_full_raw": 0.3,
                    "unlock_overhang_proxy_rank_full": None,
                    "proxy_fallback_raw": 0.2,
                    "unlock_fragility_proxy_rank_fallback": None,
                    "unlock_pressure_rank_selected_for_reporting": None,
                    "unlock_feature_state": "MISSING",
                    "unknown_bucket_ratio": 0.0,
                    "quality_flag": "ok",
                    "reconstruction_confidence": 0.95,
                    "_obs_quality": "ok",
                    "_rec_quality": "ok",
                },
                {
                    "date": "2024-01-01",
                    "asset_id": "REC",
                    "ups_raw_observed": None,
                    "unlock_pressure_rank_observed": None,
                    "ups_raw_reconstructed": 1.0,
                    "unlock_pressure_rank_reconstructed": None,
                    "proxy_full_raw": 0.4,
                    "unlock_overhang_proxy_rank_full": None,
                    "proxy_fallback_raw": 0.3,
                    "unlock_fragility_proxy_rank_fallback": None,
                    "unlock_pressure_rank_selected_for_reporting": None,
                    "unlock_feature_state": "MISSING",
                    "unknown_bucket_ratio": 0.0,
                    "quality_flag": "ok",
                    "reconstruction_confidence": 0.90,
                    "_obs_quality": "missing_schedule",
                    "_rec_quality": "ok",
                },
                {
                    "date": "2024-01-01",
                    "asset_id": "PROXY",
                    "ups_raw_observed": None,
                    "unlock_pressure_rank_observed": None,
                    "ups_raw_reconstructed": 0.8,
                    "unlock_pressure_rank_reconstructed": None,
                    "proxy_full_raw": 0.6,
                    "unlock_overhang_proxy_rank_full": None,
                    "proxy_fallback_raw": 0.4,
                    "unlock_fragility_proxy_rank_fallback": None,
                    "unlock_pressure_rank_selected_for_reporting": None,
                    "unlock_feature_state": "MISSING",
                    "unknown_bucket_ratio": 0.0,
                    "quality_flag": "ok",
                    "reconstruction_confidence": 0.84,
                    "_obs_quality": "missing_schedule",
                    "_rec_quality": "ok",
                },
            ]
            ranked = {row["asset_id"]: row for row in collector._apply_ranking_and_state(rows)}

        self.assertEqual(ranked["OBS"]["unlock_feature_state"], "OBSERVED")
        self.assertEqual(
            ranked["OBS"]["unlock_pressure_rank_selected_for_reporting"],
            ranked["OBS"]["unlock_pressure_rank_observed"],
        )
        self.assertEqual(ranked["REC"]["unlock_feature_state"], "RECONSTRUCTED")
        self.assertEqual(
            ranked["REC"]["unlock_pressure_rank_selected_for_reporting"],
            ranked["REC"]["unlock_pressure_rank_reconstructed"],
        )
        self.assertEqual(ranked["PROXY"]["unlock_feature_state"], "PROXY_FULL")
        self.assertEqual(
            ranked["PROXY"]["unlock_pressure_rank_selected_for_reporting"],
            ranked["PROXY"]["unlock_overhang_proxy_rank_full"],
        )

    def test_selected_for_reporting_blocks_review_required_reconstructed_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            rows = [
                {
                    "date": "2024-01-01",
                    "asset_id": "REC_BLOCKED",
                    "ups_raw_observed": None,
                    "unlock_pressure_rank_observed": None,
                    "ups_raw_reconstructed": None,
                    "unlock_pressure_rank_reconstructed": None,
                    "proxy_full_raw": 0.5,
                    "unlock_overhang_proxy_rank_full": None,
                    "proxy_fallback_raw": 0.4,
                    "unlock_fragility_proxy_rank_fallback": None,
                    "unlock_pressure_rank_selected_for_reporting": None,
                    "unlock_feature_state": "MISSING",
                    "unknown_bucket_ratio": 0.20,
                    "quality_flag": "review_required",
                    "reconstruction_confidence": 0.95,
                    "_obs_quality": "missing_schedule",
                    "_rec_quality": "review_required",
                },
            ]
            ranked = collector._apply_ranking_and_state(rows)[0]

        self.assertEqual(ranked["unlock_feature_state"], "PROXY_FULL")
        self.assertEqual(
            ranked["unlock_pressure_rank_selected_for_reporting"],
            ranked["unlock_overhang_proxy_rank_full"],
        )

    def test_compute_observed_layer_treats_invalid_payload_as_missing_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            observed = collector._compute_observed_layer(
                descriptor=AssetDescriptor(asset_id="BAD", symbol="BAD", coingecko_id=None),
                as_of_date=pd.Timestamp("2024-01-01").date(),
                mobula_payload={"release_schedule": "broken", "distribution": "broken", "circulating_supply": 100},
                mobula_snapshot_ts="2024-01-01T00:00:00+00:00",
                market_row={"circulating_supply": 100},
            )

        self.assertFalse(observed.is_valid)
        self.assertEqual(observed.quality_flag, "unparsed_schedule")

    def test_parse_schedule_rows_handles_mobula_allocation_details_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            events = collector._parse_schedule_rows(
                [
                    {
                        "unlock_date": 1744416000000,
                        "tokens_to_unlock": 4543838.151260505,
                        "allocation_details": {
                            "Community": 3209972.605042018,
                            "Foundation": 1333865.546218488,
                        },
                    }
                ],
                total_supply_hint=1_000_000_000.0,
            )

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_date, pd.Timestamp("2025-04-12").date())
        self.assertEqual(events[0].bucket, "Airdrop/Public")
        self.assertEqual(events[1].bucket, "Ecosystem")
        self.assertAlmostEqual(sum(event.tokens for event in events), 4543838.151260506, places=6)

    def test_parse_distribution_rows_accepts_name_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            items = collector._parse_distribution_rows(
                [
                    {"name": "Founders", "percentage": 20},
                    {"name": "Ripple Labs", "percentage": 25},
                    {"name": "XRP Escrow", "percentage": 55},
                ]
            )

        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].bucket, "Team/Founders")
        self.assertEqual(items[1].bucket, "Other")
        self.assertEqual(items[2].value, 0.55)

    def test_compute_observed_layer_parses_realistic_mobula_schedule_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            observed = collector._compute_observed_layer(
                descriptor=AssetDescriptor(asset_id="APT", symbol="APT", coingecko_id="aptos"),
                as_of_date=pd.Timestamp("2025-04-01").date(),
                mobula_payload={
                    "data": {
                        "release_schedule": [
                            {
                                "unlock_date": 1744416000000,
                                "tokens_to_unlock": 4543838.151260505,
                                "allocation_details": {
                                    "Community": 3209972.605042018,
                                    "Foundation": 1333865.546218488,
                                },
                            }
                        ],
                        "distribution": [
                            {"name": "Founders", "percentage": 19},
                            {"name": "Foundation", "percentage": 51},
                            {"name": "Community", "percentage": 30},
                        ],
                        "circulating_supply": 130000000.0,
                        "total_supply": 1000000000.0,
                    }
                },
                mobula_snapshot_ts="2025-04-01T00:00:00+00:00",
                market_row={"circulating_supply": 130000000.0, "total_supply": 1000000000.0},
            )

        self.assertTrue(observed.is_valid)
        self.assertEqual(observed.quality_flag, "ok")
        self.assertIsNotNone(observed.raw)
        self.assertGreater(observed.raw or 0.0, 0.0)
        self.assertAlmostEqual(observed.insider_share or 0.0, 0.19 / (0.19 + 0.51 + 0.30), places=6)

    def test_runtime_history_mode_latest_only_keeps_only_latest_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            history = pd.DataFrame(
                [
                    {"date": pd.Timestamp("2024-01-01").date(), "market_cap": 100.0},
                    {"date": pd.Timestamp("2024-01-02").date(), "market_cap": 110.0},
                ]
            )

            selected = collector._select_runtime_feature_history(history)

        self.assertEqual(selected["date"].tolist(), [pd.Timestamp("2024-01-02").date()])

    def test_collect_reconstructed_layers_respects_cemetery_and_anchor_scope(self) -> None:
        async def _run() -> list[str]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = _make_collector(Path(tmp_dir))
                descriptors = [
                    AssetDescriptor(asset_id="LUNA", symbol="LUNA", coingecko_id=None),
                    AssetDescriptor(asset_id="SOL", symbol="SOL", coingecko_id=None),
                    AssetDescriptor(asset_id="DOGE", symbol="DOGE", coingecko_id=None),
                ]
                history_row = pd.DataFrame(
                    [{"date": pd.Timestamp("2024-01-01").date(), "circ_approx": 100.0, "avg_30d_volume": 10.0, "market_cap": 100.0}]
                )
                history_map = {descriptor.asset_id: history_row for descriptor in descriptors}
                details_map = {descriptor.asset_id: {} for descriptor in descriptors}
                called_assets: list[str] = []

                async def fake_build(*args, **kwargs):
                    called_assets.append(kwargs["descriptor"].asset_id)
                    return {}

                with patch.object(collector, "_build_reconstructed_for_asset", AsyncMock(side_effect=fake_build)):
                    await collector._collect_reconstructed_layers(
                        session=None,  # type: ignore[arg-type]
                        descriptors=descriptors,
                        history_map=history_map,
                        details_map=details_map,
                    )
                return called_assets

        called_assets = asyncio.run(_run())
        self.assertEqual(sorted(called_assets), ["LUNA", "SOL"])

    def test_collect_reconstructed_layers_only_uses_latest_history_date_in_live_mode(self) -> None:
        async def _run() -> list[tuple[str, list[pd.Timestamp]]]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = _make_collector(Path(tmp_dir))
                descriptors = [AssetDescriptor(asset_id="SOL", symbol="SOL", coingecko_id=None)]
                history_row = pd.DataFrame(
                    [
                        {"date": pd.Timestamp("2024-01-01").date(), "circ_approx": 100.0, "avg_30d_volume": 10.0, "market_cap": 100.0},
                        {"date": pd.Timestamp("2024-01-02").date(), "circ_approx": 100.0, "avg_30d_volume": 10.0, "market_cap": 100.0},
                    ]
                )
                history_map = {"SOL": history_row}
                details_map = {"SOL": {}}
                seen: list[tuple[str, list[pd.Timestamp]]] = []

                async def fake_build(*args, **kwargs):
                    history = kwargs["history"]
                    seen.append((kwargs["descriptor"].asset_id, history["date"].tolist()))
                    return {}

                with patch.object(collector, "_build_reconstructed_for_asset", AsyncMock(side_effect=fake_build)):
                    await collector._collect_reconstructed_layers(
                        session=None,  # type: ignore[arg-type]
                        descriptors=descriptors,
                        history_map=history_map,
                        details_map=details_map,
                    )
                return seen

        seen = asyncio.run(_run())
        self.assertEqual(seen, [("SOL", [pd.Timestamp("2024-01-02").date()])])

    def test_select_reconstruction_urls_limits_low_priority_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            collector.reconstruction_max_urls = 4
            selected = collector._select_reconstruction_urls(
                [
                    {"url_type": "blog", "source_priority": 8, "url": "https://asset/blog"},
                    {"url_type": "official_forum", "source_priority": 9, "url": "https://asset/forum"},
                    {"url_type": "homepage", "source_priority": 1, "url": "https://asset/"},
                    {"url_type": "tokenomics", "source_priority": 2, "url": "https://asset/tokenomics"},
                    {"url_type": "docs", "source_priority": 5, "url": "https://asset/docs"},
                    {"url_type": "whitepaper", "source_priority": 6, "url": "https://asset/wp.pdf"},
                ]
            )

        self.assertEqual([row["url_type"] for row in selected], ["whitepaper", "docs", "tokenomics", "homepage"])

    def test_collect_reconstructed_layers_degrades_when_wayback_query_fails(self) -> None:
        async def _run() -> dict[str, dict[pd.Timestamp, LayerComputation]]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = _make_collector(Path(tmp_dir))
                descriptors = [AssetDescriptor(asset_id="SOL", symbol="SOL", coingecko_id=None)]
                history_row = pd.DataFrame(
                    [{"date": pd.Timestamp("2024-01-02").date(), "circ_approx": 100.0, "avg_30d_volume": 10.0, "market_cap": 100.0}]
                )
                history_map = {"SOL": history_row}
                details_map = {"SOL": {"links": {"homepage": ["https://solana.com"]}}}

                with patch.object(collector.wayback_client, "query_captures", AsyncMock(side_effect=Exception("503"))):
                    return await collector._collect_reconstructed_layers(
                        session=None,  # type: ignore[arg-type]
                        descriptors=descriptors,
                        history_map=history_map,
                        details_map=details_map,
                    )

        reconstructed = asyncio.run(_run())
        self.assertEqual(reconstructed["SOL"], {})

    def test_build_quality_rows_tracks_layer_coverage_and_provider_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = _make_collector(Path(tmp_dir))
            ranked_rows = [
                {
                    "date": "2024-01-31",
                    "asset_id": "OBS",
                    "ups_raw_observed": 0.1,
                    "unlock_pressure_rank_observed": 0.0,
                    "ups_raw_reconstructed": None,
                    "unlock_pressure_rank_reconstructed": None,
                    "proxy_full_raw": 0.2,
                    "unlock_overhang_proxy_rank_full": 0.2,
                    "proxy_fallback_raw": 0.3,
                    "unlock_fragility_proxy_rank_fallback": 0.3,
                    "unlock_pressure_rank_selected_for_reporting": 0.0,
                    "unlock_feature_state": "OBSERVED",
                    "unknown_bucket_ratio": 0.0,
                    "quality_flag": "ok",
                    "reconstruction_confidence": None,
                },
                {
                    "date": "2024-01-31",
                    "asset_id": "REC",
                    "ups_raw_observed": None,
                    "unlock_pressure_rank_observed": None,
                    "ups_raw_reconstructed": 0.2,
                    "unlock_pressure_rank_reconstructed": 1.0,
                    "proxy_full_raw": None,
                    "unlock_overhang_proxy_rank_full": None,
                    "proxy_fallback_raw": 0.4,
                    "unlock_fragility_proxy_rank_fallback": 1.0,
                    "unlock_pressure_rank_selected_for_reporting": 1.0,
                    "unlock_feature_state": "RECONSTRUCTED",
                    "unknown_bucket_ratio": 0.0,
                    "quality_flag": "ok",
                    "reconstruction_confidence": 0.90,
                },
                {
                    "date": "2024-01-31",
                    "asset_id": "MISS",
                    "ups_raw_observed": None,
                    "unlock_pressure_rank_observed": None,
                    "ups_raw_reconstructed": None,
                    "unlock_pressure_rank_reconstructed": None,
                    "proxy_full_raw": None,
                    "unlock_overhang_proxy_rank_full": None,
                    "proxy_fallback_raw": None,
                    "unlock_fragility_proxy_rank_fallback": None,
                    "unlock_pressure_rank_selected_for_reporting": None,
                    "unlock_feature_state": "MISSING",
                    "unknown_bucket_ratio": 0.20,
                    "quality_flag": "review_required",
                    "reconstruction_confidence": 0.80,
                },
            ]
            diagnostic_rows = [
                {"date": "2024-01-31", "asset_id": "OBS", "snapshot_ts": "2024-01-31T00:00:00+00:00"},
                {"date": "2024-01-31", "asset_id": "REC", "snapshot_ts": "2024-01-20T00:00:00+00:00"},
                {"date": "2024-01-31", "asset_id": "MISS", "snapshot_ts": None},
            ]
            descriptors = [
                AssetDescriptor(asset_id="OBS", symbol="OBS", coingecko_id=None),
                AssetDescriptor(asset_id="REC", symbol="REC", coingecko_id=None),
                AssetDescriptor(asset_id="LUNA", symbol="LUNA", coingecko_id=None),
            ]
            live_bundle = {
                "OBS": {"snapshot_ts": "2024-01-31T00:00:00+00:00", "mobula_status": 200, "market_row_present": True, "defillama_status": 404},
                "REC": {"snapshot_ts": "2024-01-31T00:00:00+00:00", "mobula_status": 500, "market_row_present": False, "defillama_status": 404},
                "LUNA": {"snapshot_ts": "2024-01-31T00:00:00+00:00", "mobula_status": 404, "market_row_present": True, "defillama_status": 404},
            }
            reconstructed_map = {
                "LUNA": {
                    pd.Timestamp("2024-01-31").date(): LayerComputation(quality_flag="no_capture"),
                }
            }

            quality_row = collector._build_quality_rows(ranked_rows, diagnostic_rows, descriptors, live_bundle, reconstructed_map)[0]

        self.assertEqual(quality_row["observed_count"], 1)
        self.assertEqual(quality_row["reconstructed_count"], 1)
        self.assertEqual(quality_row["missing_count"], 1)
        self.assertEqual(quality_row["review_required_count"], 1)
        self.assertEqual(quality_row["unknown_bucket_block_count"], 1)
        self.assertEqual(quality_row["promotion_blocked_count"], 1)
        self.assertEqual(quality_row["provider_failures_mobula"], 2)
        self.assertEqual(quality_row["provider_failures_coingecko"], 1)
        self.assertEqual(quality_row["provider_failures_wayback"], 1)
        self.assertGreaterEqual(quality_row["massive_ties_fraction"], 0.0)

    def test_fetch_current_ups_scores_preserves_existing_coingecko_ids(self) -> None:
        async def _run() -> dict[str, float | None]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = _make_collector(Path(tmp_dir))
                assets = [SimpleNamespace(symbol="BNB", coingecko_id="binancecoin")]

                async def fake_prepare(session, descriptors):
                    return descriptors

                async def fake_collect(session, descriptors):
                    self.assertEqual(descriptors[0].coingecko_id, "binancecoin")
                    future_date = (date.today() + timedelta(days=10)).isoformat()
                    return {
                        "BNB": {
                            "mobula_payload": {
                                "release_schedule": [
                                    {"date": future_date, "allocationName": "team", "unlockAmount": 10.0},
                                ],
                                "distribution": [{"allocationName": "team", "share": 0.30}],
                                "circulating_supply": 100.0,
                                "total_supply": 150.0,
                            },
                            "mobula_snapshot_ts": "2024-01-01T00:00:00+00:00",
                            "market_row": {"circulating_supply": 100.0, "total_supply": 150.0},
                            "market_row_present": True,
                            "mobula_status": 200,
                        }
                    }

                with (
                    patch.object(collector, "_prepare_descriptors", AsyncMock(side_effect=fake_prepare)),
                    patch.object(collector, "_collect_live_snapshots", AsyncMock(side_effect=fake_collect)),
                ):
                    return await collector.fetch_current_ups_scores(assets)

        scores = asyncio.run(_run())
        self.assertAlmostEqual(scores["BNB"], 0.15)

    def test_collect_live_snapshots_rejects_mismatched_mobula_payload_but_persists_raw_snapshot(self) -> None:
        async def _run() -> tuple[dict[str, dict[str, object]], int]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = _make_collector(Path(tmp_dir))
                descriptors = [AssetDescriptor(asset_id="BNB", symbol="BNB", coingecko_id="binancecoin")]
                market_payload = {
                    "id": "binancecoin",
                    "current_price": 300.0,
                    "market_cap": 1_000_000.0,
                    "total_volume": 100_000.0,
                    "circulating_supply": 1_000.0,
                    "total_supply": 2_000.0,
                }
                mobula_payload = {
                    "data": {
                        "symbol": "BTC",
                        "release_schedule": [{"date": "2024-01-10", "allocationName": "team", "unlockAmount": 10.0}],
                    }
                }

                with (
                    patch.object(collector.cg_client, "fetch_current_markets", AsyncMock(return_value={"binancecoin": market_payload})),
                    patch.object(collector.mobula_client, "fetch_metadata", AsyncMock(return_value=(200, mobula_payload, False))),
                    patch.object(collector.defillama_client, "fetch_asset_page", AsyncMock(return_value=(404, None))),
                ):
                    bundle = await collector._collect_live_snapshots(None, descriptors)  # type: ignore[arg-type]

                raw_rows = collector.store.load_table("raw_mobula_snapshots")
                return bundle, len(raw_rows)

        bundle, raw_count = asyncio.run(_run())
        self.assertEqual(raw_count, 1)
        self.assertIsNone(bundle["BNB"]["mobula_payload"])
        self.assertFalse(bundle["BNB"]["mobula_payload_matched"])

    def test_build_market_history_uses_live_row_minimal_fallback_when_provider_history_is_unavailable(self) -> None:
        async def _run() -> pd.DataFrame:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = _make_collector(Path(tmp_dir))
                descriptor = AssetDescriptor(asset_id="BNB", symbol="BNB", coingecko_id="binancecoin")
                live_row = {
                    "price_usd": 300.0,
                    "market_cap": 1_000_000.0,
                    "total_volume": 100_000.0,
                    "circulating_supply": 1_000.0,
                    "total_supply": 2_000.0,
                }
                with patch.object(collector.cg_client, "ensure_market_history", AsyncMock(return_value=pl.DataFrame())):
                    return await collector._build_market_history(None, descriptor, live_row)  # type: ignore[arg-type]

        history = asyncio.run(_run())
        self.assertEqual(len(history), 1)
        self.assertEqual(float(history.iloc[0]["avg_30d_volume"]), 100_000.0)
        self.assertEqual(float(history.iloc[0]["total_supply"]), 2_000.0)


if __name__ == "__main__":
    unittest.main()
