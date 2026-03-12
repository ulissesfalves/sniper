from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
import polars as pl

from tests import _path_setup  # noqa: F401
from services.data_inserter import bootstrap_phase2_inputs
from services.data_inserter.collectors.binance import BinanceCollector
from services.data_inserter.collectors.coingecko import CoinGeckoCollector, OHLCV_DAILY_COLUMNS
from services.data_inserter.collectors.stablecoin import StablecoinCollector
from services.data_inserter.collectors.unlock_support.providers import AssetDescriptor, CoinGeckoUnlockClient


def _client_response_error(status: int) -> aiohttp.ClientResponseError:
    request_info = SimpleNamespace(real_url="https://api.coingecko.com/test")
    return aiohttp.ClientResponseError(request_info=request_info, history=(), status=status, message="boom", headers=None)


class CollectorsRuntimeTest(unittest.TestCase):
    def test_phase2_bootstrap_uses_full_binance_phase2_fetch(self) -> None:
        async def _run() -> int:
            unlock_instances: list[object] = []

            class UnlockCollectorSpy:
                def __init__(self, *args, **kwargs):
                    unlock_instances.append(self)
                    self.kwargs = kwargs

                async def fetch_and_store(self, symbols):
                    return ["BNB.parquet"]

            with (
                patch.object(bootstrap_phase2_inputs, "_preflight", return_value=(["BNB"], {
                    "PARQUET_BASE_PATH": "/tmp",
                    "STABLECOIN_SOURCE": "auto",
                    "COINGECKO_API_KEY": "missing",
                    "MOBULA_API_KEY": "missing",
                    "DEFILLAMA_UNLOCKS_ENDPOINT": "not_configured",
                })),
                patch.object(bootstrap_phase2_inputs.BinanceCollector, "fetch_and_store", AsyncMock()) as fetch_all_mock,
                patch.object(bootstrap_phase2_inputs.StablecoinCollector, "fetch_and_store", AsyncMock(return_value=Path("/tmp/stablecoin.parquet"))),
                patch.object(bootstrap_phase2_inputs, "TokenUnlocksCollector", UnlockCollectorSpy),
            ):
                result = await bootstrap_phase2_inputs._run()
                self.assertEqual(fetch_all_mock.await_count, 1)
                self.assertEqual(len(unlock_instances), 1)
                self.assertEqual(unlock_instances[0].kwargs.get("runtime_history_mode"), "full")
                return result

        result = asyncio.run(_run())
        self.assertEqual(result, 0)

    def test_binance_fetch_funding_history_paginates(self) -> None:
        async def _run() -> list[dict]:
            collector = BinanceCollector(parquet_base=tempfile.mkdtemp())
            calls: list[int] = []
            base_ts = 1_700_000_000_000

            async def fake_get_json(session, url, params):
                calls.append(int(params["startTime"]))
                if len(calls) == 1:
                    return [
                        {"fundingTime": str(base_ts), "fundingRate": "0.0010"},
                        {"fundingTime": str(base_ts + 1000), "fundingRate": "0.0015"},
                    ]
                return [{"fundingTime": str(base_ts + 2000), "fundingRate": "0.0020"}]

            with patch.object(collector, "_get_json", AsyncMock(side_effect=fake_get_json)):
                data = await collector._fetch_funding_history(None, "BNB", limit=2)  # type: ignore[arg-type]

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1], base_ts + 1001)
            return data

        data = asyncio.run(_run())
        self.assertEqual(len(data), 3)

    def test_binance_parse_funding_aggregates_daily_and_rolling_7d(self) -> None:
        collector = BinanceCollector(parquet_base=tempfile.mkdtemp())
        raw = []
        for day_offset, rates in enumerate([[0.01, 0.02], [0.03], [0.05]], start=0):
            base = datetime(2024, 1, 1 + day_offset, tzinfo=timezone.utc)
            for idx, rate in enumerate(rates):
                raw.append(
                    {
                        "fundingTime": str(int(base.timestamp() * 1000) + idx),
                        "fundingRate": str(rate),
                    }
                )

        frame = collector._parse_funding(raw, "BNB").sort("timestamp")
        self.assertEqual(frame.height, 3)
        self.assertEqual(frame["timestamp"].to_list(), [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)])
        self.assertAlmostEqual(frame["funding_rate"][0], 0.015, places=6)
        self.assertAlmostEqual(frame["funding_rate_ma7d"][-1], (0.015 + 0.03 + 0.05) / 3, places=6)

    def test_binance_delivery_history_start_ms_uses_valid_floor(self) -> None:
        collector = BinanceCollector(parquet_base=tempfile.mkdtemp())

        old_spot = pl.DataFrame(
            {
                "timestamp": [date(2018, 1, 1), date(2018, 1, 2)],
                "spot": [1.0, 1.1],
            }
        )
        recent_spot = pl.DataFrame(
            {
                "timestamp": [date(2024, 2, 1), date(2024, 2, 2)],
                "spot": [10.0, 10.1],
            }
        )

        self.assertEqual(
            collector._delivery_history_start_ms(old_spot),
            int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        )
        self.assertEqual(
            collector._delivery_history_start_ms(recent_spot),
            int(datetime(2024, 2, 1, tzinfo=timezone.utc).timestamp() * 1000),
        )

    def test_binance_fetch_continuous_quarter_daily_paginates_from_provided_start(self) -> None:
        async def _run() -> tuple[list[list], list[int]]:
            collector = BinanceCollector(parquet_base=tempfile.mkdtemp())
            calls: list[int] = []
            first_ts = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            page = [
                [first_ts + idx * 86_400_000, "1", "1", "1", "1", "1"]
                for idx in range(200)
            ]

            async def fake_get_json(session, url, params):
                calls.append(int(params["startTime"]))
                if len(calls) == 1:
                    return page
                return [[page[-1][0] + 86_400_000, "1", "1", "1", "1", "1"]]

            with patch.object(collector, "_get_json", AsyncMock(side_effect=fake_get_json)):
                rows = await collector._fetch_continuous_quarter_daily(
                    None,  # type: ignore[arg-type]
                    "BNB",
                    start_ms=first_ts,
                )
            return rows, calls

        rows, calls = asyncio.run(_run())
        self.assertEqual(len(rows), 201)
        self.assertEqual(calls[0], int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertEqual(
            calls[1],
            int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000) + 199 * 86_400_000 + 1,
        )

    def test_binance_fetch_continuous_quarter_daily_shifts_start_when_first_page_is_empty(self) -> None:
        async def _run() -> tuple[list[list], list[int]]:
            collector = BinanceCollector(parquet_base=tempfile.mkdtemp())
            calls: list[int] = []
            initial_start = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            recovered_ts = int(datetime(2021, 7, 15, tzinfo=timezone.utc).timestamp() * 1000)

            async def fake_get_json(session, url, params):
                calls.append(int(params["startTime"]))
                if len(calls) == 1:
                    return []
                return [[recovered_ts, "1", "1", "1", "1", "1"]]

            with patch.object(collector, "_get_json", AsyncMock(side_effect=fake_get_json)):
                rows = await collector._fetch_continuous_quarter_daily(
                    None,  # type: ignore[arg-type]
                    "BNB",
                    start_ms=initial_start,
                )
            return rows, calls

        rows, calls = asyncio.run(_run())
        self.assertEqual(len(rows), 1)
        self.assertEqual(calls[0], int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000))
        self.assertEqual(calls[1], int(datetime(2020, 12, 31, tzinfo=timezone.utc).timestamp() * 1000))

    def test_coingecko_save_ohlcv_parquet_harmonizes_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            collector = CoinGeckoCollector(parquet_base=tmp_dir)
            path = Path(tmp_dir) / "ohlcv_daily" / "BNB.parquet"
            existing = pl.DataFrame(
                {
                    "timestamp": [datetime(2024, 1, 1, tzinfo=timezone.utc)],
                    "symbol": ["BNB"],
                    "open": [1.0],
                    "high": [2.0],
                    "low": [0.5],
                    "close": [1.5],
                    "source": ["legacy"],
                    "volume": [10.0],
                    "quote_volume": [20.0],
                }
            )
            existing.write_parquet(path)

            raw = [[int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000), 2.0, 3.0, 1.0, 2.5]]
            collector._save_ohlcv_parquet(raw, "BNB")

            written = pl.read_parquet(path).sort("timestamp")
            self.assertEqual(written.columns, OHLCV_DAILY_COLUMNS)
            self.assertEqual(written.height, 2)
            self.assertIn("coingecko_ohlc", written.get_column("source").to_list())

    def test_stablecoin_auto_prefers_defillama_on_demo_mode(self) -> None:
        async def _run() -> tuple[Path | None, int]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = StablecoinCollector(parquet_base=tmp_dir)
                collector.cg_mode = "demo"
                collector.source_mode = "auto"
                collector.stablecoin_ids = ["tether"]

                with (
                    patch.object(
                        collector,
                        "_fetch_defillama_market_cap_history",
                        AsyncMock(return_value={date(2024, 1, 1): 100.0, date(2024, 1, 31): 110.0}),
                    ),
                    patch.object(collector, "_fetch_market_caps", AsyncMock(return_value={})) as market_caps_mock,
                ):
                    output = await collector.fetch_and_store()
                return output, market_caps_mock.await_count

        output, market_caps_calls = asyncio.run(_run())
        self.assertIsNotNone(output)
        self.assertEqual(market_caps_calls, 0)

    def test_stablecoin_non_pro_uses_chunked_history_fetch(self) -> None:
        async def _run() -> dict[date, float]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                collector = StablecoinCollector(parquet_base=tmp_dir)
                collector.cg_mode = "demo"
                with patch.object(
                    collector,
                    "_fetch_market_caps_chunked",
                    AsyncMock(return_value={date(2024, 1, 1): 1.0}),
                ) as chunked_mock:
                    result = await collector._fetch_market_caps(None, "tether", 1, 2)  # type: ignore[arg-type]
                    self.assertEqual(chunked_mock.await_count, 1)
                    return result

        result = asyncio.run(_run())
        self.assertEqual(result, {date(2024, 1, 1): 1.0})

    def test_unlock_market_history_uses_public_horizon_in_demo_mode(self) -> None:
        async def _run() -> tuple[pl.DataFrame, list[dict[str, object]]]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                client = CoinGeckoUnlockClient(tmp_dir)
                client.api_mode = "demo"
                client.RATE_LIMIT_DELAY = 0
                asset = AssetDescriptor(asset_id="BNB", symbol="BNB", coingecko_id="binancecoin")
                payload = {
                    "prices": [[1704067200000, 300.0]],
                    "market_caps": [[1704067200000, 1_000_000.0]],
                    "total_volumes": [[1704067200000, 100_000.0]],
                }
                calls: list[dict[str, object]] = []

                async def fake_get_json(session, url, params=None, allow_404=False):
                    calls.append(dict(params or {}))
                    return payload

                with patch.object(client, "_get_json", AsyncMock(side_effect=fake_get_json)):
                    history = await client.ensure_market_history(None, asset)  # type: ignore[arg-type]
                return history, calls

        history, calls = asyncio.run(_run())
        self.assertFalse(history.is_empty())
        self.assertEqual(calls[0]["days"], "90")

    def test_unlock_market_history_falls_back_to_range_after_401(self) -> None:
        async def _run() -> tuple[pl.DataFrame, list[dict[str, object]]]:
            with tempfile.TemporaryDirectory() as tmp_dir:
                client = CoinGeckoUnlockClient(tmp_dir)
                client.api_mode = "demo"
                client.RATE_LIMIT_DELAY = 0
                asset = AssetDescriptor(asset_id="BNB", symbol="BNB", coingecko_id="binancecoin")
                payload = {
                    "prices": [[1704067200000, 300.0]],
                    "market_caps": [[1704067200000, 1_000_000.0]],
                    "total_volumes": [[1704067200000, 100_000.0]],
                }
                calls: list[dict[str, object]] = []

                async def fake_get_json(session, url, params=None, allow_404=False):
                    calls.append(dict(params or {}))
                    if params and params.get("days") == "90":
                        raise _client_response_error(401)
                    return payload

                with patch.object(client, "_get_json", AsyncMock(side_effect=fake_get_json)):
                    history = await client.ensure_market_history(None, asset)  # type: ignore[arg-type]
                return history, calls

        history, calls = asyncio.run(_run())
        self.assertFalse(history.is_empty())
        self.assertEqual(calls[0]["days"], "90")
        self.assertIn("from", calls[1])
        self.assertIn("to", calls[1])


if __name__ == "__main__":
    unittest.main()
