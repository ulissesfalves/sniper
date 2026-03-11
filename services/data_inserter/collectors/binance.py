# =============================================================================
# DESTINO: services/data_inserter/collectors/binance.py
# Coleta dados de derivativos da Binance: funding rate, basis 3m, OHLCV 4h.
# =============================================================================
from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import aiohttp
import polars as pl
import structlog
from decouple import config

log = structlog.get_logger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_DELIVERY_BASE = "https://dapi.binance.com"
BINANCE_SPOT_BASE = "https://api.binance.com"
START_DATE_MS = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
DELIVERY_START_DATE_MS = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
CONTINUOUS_KLINES_LIMIT = 200
DELIVERY_PROBE_STEP_MS = 365 * 24 * 60 * 60 * 1000

FUNDING_SCHEMA = {
    "timestamp": pl.Date,
    "symbol": pl.Utf8,
    "funding_rate": pl.Float32,
    "funding_rate_ma7d": pl.Float32,
}

BASIS_SCHEMA = {
    "timestamp": pl.Date,
    "symbol": pl.Utf8,
    "spot": pl.Float32,
    "futures_3m": pl.Float32,
    "basis_3m": pl.Float32,
    "basis_source": pl.Utf8,
}


class BinanceCollector:
    RATE_LIMIT_DELAY = 0.20

    def __init__(self, parquet_base: str) -> None:
        self.parquet_base = Path(parquet_base)
        self.api_key = config("BINANCE_API_KEY", default="")
        self.api_secret = config("BINANCE_API_SECRET", default="")
        self.testnet = config("BINANCE_TESTNET", default="true").lower() == "true"

        (self.parquet_base / "funding").mkdir(parents=True, exist_ok=True)
        (self.parquet_base / "basis").mkdir(parents=True, exist_ok=True)
        (self.parquet_base / "ohlcv_4h").mkdir(parents=True, exist_ok=True)

    def _extract_symbol(self, asset: Any) -> str:
        return getattr(asset, "symbol", str(asset)).upper()

    def _binance_symbol(self, symbol: str) -> str:
        if symbol.endswith("USDT"):
            return symbol
        return f"{symbol}USDT"

    def _delivery_pair(self, symbol: str) -> str:
        return f"{symbol}USD"

    def _delivery_history_start_ms(self, spot_df: pl.DataFrame) -> int:
        if spot_df.is_empty() or "timestamp" not in spot_df.columns:
            return DELIVERY_START_DATE_MS

        try:
            min_value = spot_df.get_column("timestamp").min()
        except Exception:
            return DELIVERY_START_DATE_MS

        if min_value is None:
            return DELIVERY_START_DATE_MS
        if isinstance(min_value, datetime):
            min_dt = min_value.astimezone(timezone.utc)
        elif isinstance(min_value, date):
            min_dt = datetime.combine(min_value, time.min, tzinfo=timezone.utc)
        else:
            return DELIVERY_START_DATE_MS

        return max(int(min_dt.timestamp() * 1000), DELIVERY_START_DATE_MS)

    async def _get_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict[str, Any],
    ) -> Any:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            if resp.status == 429:
                log.warning("binance.rate_limited", url=url, params=params, action="sleep_30s")
                await asyncio.sleep(30)
                return await self._get_json(session, url, params)
            if resp.status in {400, 404}:
                return None
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_funding_history(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        limit: int = 1000,
    ) -> list[dict]:
        url = f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate"
        all_rows: list[dict] = []
        start_ms = START_DATE_MS

        while True:
            params = {
                "symbol": self._binance_symbol(symbol),
                "limit": limit,
                "startTime": start_ms,
            }
            data = await self._get_json(session, url, params)
            if not data:
                break

            all_rows.extend(data)
            last_ts = int(data[-1]["fundingTime"])
            if last_ts <= start_ms:
                break
            start_ms = last_ts + 1

            if len(data) < limit:
                break
            await asyncio.sleep(self.RATE_LIMIT_DELAY)

        return all_rows

    async def _fetch_klines_4h(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        limit: int = 500,
    ) -> list[list]:
        url = f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
        params = {"symbol": self._binance_symbol(symbol), "interval": "4h", "limit": limit}
        data = await self._get_json(session, url, params)
        return data or []

    async def _fetch_continuous_quarter_daily(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        start_ms: int | None = None,
    ) -> list[list]:
        """
        Tenta obter a série histórica diária do contrato contínuo CURRENT_QUARTER
        via USDⓈ-M futures. Não usa perpetual como proxy.
        """
        url = f"{BINANCE_DELIVERY_BASE}/dapi/v1/continuousKlines"
        pair = self._delivery_pair(symbol)
        all_rows: list[list] = []
        requested_start_ms = max(start_ms or DELIVERY_START_DATE_MS, DELIVERY_START_DATE_MS)
        next_start_ms = requested_start_ms
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        first_page = True

        while True:
            params = {
                "pair": pair,
                "contractType": "CURRENT_QUARTER",
                "interval": "1d",
                "limit": CONTINUOUS_KLINES_LIMIT,
                "startTime": next_start_ms,
            }
            data = await self._get_json(session, url, params)
            if not data:
                if first_page and next_start_ms + DELIVERY_PROBE_STEP_MS <= now_ms:
                    next_start_ms += DELIVERY_PROBE_STEP_MS
                    continue
                break
            if first_page and next_start_ms != requested_start_ms:
                log.info(
                    "binance.delivery_start_shifted",
                    symbol=symbol,
                    requested_start_ms=requested_start_ms,
                    effective_start_ms=next_start_ms,
                )
            all_rows.extend(data)
            first_page = False
            last_ts = int(data[-1][0])
            if last_ts <= next_start_ms:
                break
            next_start_ms = last_ts + 1
            if len(data) < CONTINUOUS_KLINES_LIMIT:
                break
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return all_rows

    def _parse_funding(self, raw: list[dict], symbol: str) -> pl.DataFrame:
        if not raw:
            return pl.DataFrame(schema=FUNDING_SCHEMA)

        df = pl.DataFrame(
            {
                "timestamp": [
                    datetime.fromtimestamp(int(r["fundingTime"]) / 1000, tz=timezone.utc).date()
                    for r in raw
                ],
                "symbol": [symbol] * len(raw),
                "funding_rate": pl.Series([float(r["fundingRate"]) for r in raw], dtype=pl.Float32),
            }
        )
        df = (
            df.group_by(["timestamp", "symbol"])
            .agg(pl.col("funding_rate").mean().cast(pl.Float32))
            .sort("timestamp")
            .with_columns(
                pl.col("funding_rate")
                .rolling_mean(window_size=7, min_periods=1)
                .alias("funding_rate_ma7d")
                .cast(pl.Float32)
            )
        )
        return df.select(list(FUNDING_SCHEMA.keys()))

    def _parse_klines_4h(self, raw: list[list], symbol: str) -> pl.DataFrame:
        if not raw:
            return pl.DataFrame()

        ts = [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc) for k in raw]
        return pl.DataFrame(
            {
                "timestamp": ts,
                "symbol": [symbol] * len(raw),
                "open": pl.Series([float(k[1]) for k in raw], dtype=pl.Float32),
                "high": pl.Series([float(k[2]) for k in raw], dtype=pl.Float32),
                "low": pl.Series([float(k[3]) for k in raw], dtype=pl.Float32),
                "close": pl.Series([float(k[4]) for k in raw], dtype=pl.Float32),
                "volume": pl.Series([float(k[5]) for k in raw], dtype=pl.Float32),
            }
        )

    def _load_spot_daily_from_parquet(self, symbol: str) -> pl.DataFrame:
        p = self.parquet_base / "ohlcv_daily" / f"{symbol}.parquet"
        if not p.exists():
            return pl.DataFrame()
        df = pl.read_parquet(p)
        return (
            df.select(
                pl.col("timestamp").cast(pl.Date),
                pl.col("close").cast(pl.Float32).alias("spot"),
            )
            .sort("timestamp")
            .unique(subset=["timestamp"], keep="last")
        )

    def _parse_quarter_daily(self, raw: list[list]) -> pl.DataFrame:
        if not raw:
            return pl.DataFrame()
        return (
            pl.DataFrame(
                {
                    "timestamp": [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).date() for k in raw],
                    "futures_3m": pl.Series([float(k[4]) for k in raw], dtype=pl.Float32),
                }
            )
            .sort("timestamp")
            .unique(subset=["timestamp"], keep="last")
        )

    def _build_basis_frame(
        self,
        symbol: str,
        spot_df: pl.DataFrame,
        futures_df: pl.DataFrame,
    ) -> pl.DataFrame:
        if spot_df.is_empty():
            return pl.DataFrame(schema=BASIS_SCHEMA)

        if futures_df.is_empty():
            return spot_df.with_columns(
                pl.lit(symbol).alias("symbol"),
                pl.lit(None, dtype=pl.Float32).alias("futures_3m"),
                pl.lit(None, dtype=pl.Float32).alias("basis_3m"),
                pl.lit("missing_current_quarter_delivery").alias("basis_source"),
            ).select(list(BASIS_SCHEMA.keys()))

        df = spot_df.join(futures_df, on="timestamp", how="left")
        df = df.with_columns(
            pl.lit(symbol).alias("symbol"),
            (
                pl.when((pl.col("spot") > 0) & pl.col("futures_3m").is_not_null())
                .then(pl.col("futures_3m") / pl.col("spot") - 1.0)
                .otherwise(None)
            )
            .cast(pl.Float32)
            .alias("basis_3m"),
            pl.when(pl.col("futures_3m").is_not_null())
            .then(pl.lit("binance_coinm_current_quarter"))
            .otherwise(pl.lit("missing_current_quarter_delivery"))
            .alias("basis_source"),
        )
        return df.select(list(BASIS_SCHEMA.keys()))

    async def fetch_basis_only(self, universe: list[Any]) -> None:
        async with aiohttp.ClientSession() as session:
            for asset in universe:
                symbol = self._extract_symbol(asset)
                spot_df = self._load_spot_daily_from_parquet(symbol)
                quarter_raw = await self._fetch_continuous_quarter_daily(
                    session,
                    symbol,
                    start_ms=self._delivery_history_start_ms(spot_df),
                )
                futures_df = self._parse_quarter_daily(quarter_raw)
                basis_df = self._build_basis_frame(symbol, spot_df, futures_df)
                output = self.parquet_base / "basis" / f"{symbol}.parquet"
                basis_df.write_parquet(output)
                log.info(
                    "binance.basis_saved",
                    symbol=symbol,
                    n_rows=len(basis_df),
                    with_futures=not futures_df.is_empty(),
                    basis_source="binance_coinm_current_quarter" if not futures_df.is_empty() else "missing_current_quarter_delivery",
                    path=str(output),
                )
                await asyncio.sleep(self.RATE_LIMIT_DELAY)

    async def fetch_and_store(self, universe: list[Any]) -> None:
        async with aiohttp.ClientSession() as session:
            for asset in universe:
                symbol = self._extract_symbol(asset)
                try:
                    funding_raw = await self._fetch_funding_history(session, symbol)
                    funding_df = self._parse_funding(funding_raw, symbol)
                    if not funding_df.is_empty():
                        funding_df.write_parquet(self.parquet_base / "funding" / f"{symbol}.parquet")

                    k4h_raw = await self._fetch_klines_4h(session, symbol)
                    k4h_df = self._parse_klines_4h(k4h_raw, symbol)
                    if not k4h_df.is_empty():
                        k4h_df.write_parquet(self.parquet_base / "ohlcv_4h" / f"{symbol}.parquet")

                    spot_df = self._load_spot_daily_from_parquet(symbol)
                    quarter_raw = await self._fetch_continuous_quarter_daily(
                        session,
                        symbol,
                        start_ms=self._delivery_history_start_ms(spot_df),
                    )
                    futures_df = self._parse_quarter_daily(quarter_raw)
                    basis_df = self._build_basis_frame(symbol, spot_df, futures_df)
                    basis_df.write_parquet(self.parquet_base / "basis" / f"{symbol}.parquet")

                    log.info(
                        "binance.symbol_done",
                        symbol=symbol,
                        funding_rows=len(funding_df),
                        ohlcv_4h_rows=len(k4h_df),
                        basis_rows=len(basis_df),
                        basis_with_futures=not futures_df.is_empty(),
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("binance.symbol_error", symbol=symbol, error=str(exc), exc_info=True)
                await asyncio.sleep(self.RATE_LIMIT_DELAY)
