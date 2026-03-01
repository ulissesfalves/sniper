# =============================================================================
# DESTINO: services/data_inserter/collectors/binance.py
# Coleta dados de derivativos da Binance: funding rate, basis 3m, volume.
# Estes dados alimentam as features do HMM e do meta-modelo.
# =============================================================================
from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime, timezone

import aiohttp
import polars as pl
import structlog

log = structlog.get_logger(__name__)

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_SPOT_BASE    = "https://api.binance.com"

FUNDING_SCHEMA = {
    "timestamp":     pl.Date,
    "symbol":        pl.Utf8,
    "funding_rate":  pl.Float32,
    "funding_rate_ma7d": pl.Float32,
}

BASIS_SCHEMA = {
    "timestamp": pl.Date,
    "symbol":    pl.Utf8,
    "spot":      pl.Float32,
    "futures_3m": pl.Float32,
    "basis_3m":  pl.Float32,   # (futures - spot) / spot
}


class BinanceCollector:
    """
    Coleta dados de derivativos Binance Futures:
    - Funding Rate histórico (8h) → média 7d para feature
    - Basis 3M: diferença entre futuros trimestral e spot
    - Volume intraday em barras de 4h (para Corwin-Schultz e market impact)
    """

    def __init__(self, parquet_base: str) -> None:
        from decouple import config
        self.parquet_base  = Path(parquet_base)
        self.api_key       = config("BINANCE_API_KEY", default="")
        self.api_secret    = config("BINANCE_API_SECRET", default="")
        self.testnet       = config("BINANCE_TESTNET", default="true").lower() == "true"

        (self.parquet_base / "funding").mkdir(parents=True, exist_ok=True)
        (self.parquet_base / "basis").mkdir(parents=True, exist_ok=True)
        (self.parquet_base / "ohlcv_4h").mkdir(parents=True, exist_ok=True)

    def _binance_symbol(self, symbol: str) -> str:
        """Converte símbolo interno (SOL) para formato Binance (SOLUSDT)."""
        if symbol.endswith("USDT"):
            return symbol
        return f"{symbol}USDT"

    async def _fetch_funding_history(
        self,
        session: aiohttp.ClientSession,
        symbol:  str,
        limit:   int = 1000,
    ) -> list[dict]:
        """Busca histórico de funding rate (8h intervals)."""
        url    = f"{BINANCE_FUTURES_BASE}/fapi/v1/fundingRate"
        params = {"symbol": self._binance_symbol(symbol), "limit": limit}
        async with session.get(url, params=params) as resp:
            if resp.status in {400, 404}:
                return []   # símbolo não tem futuros perpetuais
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_klines_4h(
        self,
        session: aiohttp.ClientSession,
        symbol:  str,
        limit:   int = 500,
    ) -> list[list]:
        """
        Busca barras de 4h para Corwin-Schultz e market impact.
        Retorna OHLCV com High/Low reais (essenciais para CS spread).
        """
        url    = f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
        params = {"symbol": self._binance_symbol(symbol),
                  "interval": "4h", "limit": limit}
        async with session.get(url, params=params) as resp:
            if resp.status in {400, 404}:
                return []
            resp.raise_for_status()
            return await resp.json()

    async def _fetch_spot_and_futures_price(
        self,
        session: aiohttp.ClientSession,
        symbol:  str,
    ) -> tuple[float, float]:
        """Busca preço spot e futuro trimestral para cálculo do basis 3m."""
        bn_sym = self._binance_symbol(symbol)

        # Spot
        spot_url = f"{BINANCE_SPOT_BASE}/api/v3/ticker/price"
        async with session.get(spot_url, params={"symbol": bn_sym}) as r:
            spot_data = await r.json() if r.status == 200 else {}
        spot = float(spot_data.get("price", 0))

        # Futuro trimestral (_2503 = Mar/25, etc) — busca contrato mais próximo
        futures_url = f"{BINANCE_FUTURES_BASE}/fapi/v1/premiumIndex"
        async with session.get(futures_url, params={"symbol": bn_sym}) as r:
            fut_data = await r.json() if r.status == 200 else {}

        # markPrice do perpetual como proxy (trimestral requer descoberta de contrato)
        futures = float(fut_data.get("markPrice", spot))
        return spot, futures

    def _parse_funding(self, raw: list[dict], symbol: str) -> pl.DataFrame:
        """Parse da resposta de funding rate → DataFrame com MA 7d."""
        if not raw:
            return pl.DataFrame(schema=FUNDING_SCHEMA)

        timestamps = [
            datetime.fromtimestamp(int(r["fundingTime"]) / 1000, tz=timezone.utc).date()
            for r in raw
        ]
        rates = [float(r["fundingRate"]) for r in raw]

        df = pl.DataFrame({
            "timestamp":    timestamps,
            "symbol":       [symbol] * len(raw),
            "funding_rate": pl.Series(rates, dtype=pl.Float32),
        })

        # Média móvel 7 dias (3 readings/day × 7 = 21 observações)
        df = df.sort("timestamp").with_columns(
            pl.col("funding_rate")
              .rolling_mean(window_size=21, min_periods=1)
              .alias("funding_rate_ma7d")
              .cast(pl.Float32)
        )
        return df.unique(subset=["timestamp"], keep="last")

    def _parse_klines_4h(self, raw: list[list], symbol: str) -> pl.DataFrame:
        """
        Parse das barras 4h da Binance para Parquet.
        Colunas essenciais: timestamp, open, high, low, close, volume.
        High/Low reais são obrigatórios para Corwin-Schultz.
        """
        if not raw:
            return pl.DataFrame()

        ts   = [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc) for k in raw]
        return pl.DataFrame({
            "timestamp": ts,
            "symbol":    [symbol] * len(raw),
            "open":      pl.Series([float(k[1]) for k in raw], dtype=pl.Float32),
            "high":      pl.Series([float(k[2]) for k in raw], dtype=pl.Float32),
            "low":       pl.Series([float(k[3]) for k in raw], dtype=pl.Float32),
            "close":     pl.Series([float(k[4]) for k in raw], dtype=pl.Float32),
            "volume":    pl.Series([float(k[5]) for k in raw], dtype=pl.Float64),
            "quote_volume": pl.Series([float(k[7]) for k in raw], dtype=pl.Float64),
        }).sort("timestamp")

    def _save_parquet(self, df: pl.DataFrame, subfolder: str,
                      symbol: str) -> None:
        """Salva/atualiza Parquet com deduplicação por timestamp."""
        path = self.parquet_base / subfolder / f"{symbol}.parquet"
        if path.exists() and not df.is_empty():
            existing = pl.read_parquet(path)
            df = pl.concat([existing, df]).unique(
                subset=["timestamp"], keep="last"
            ).sort("timestamp")
        if not df.is_empty():
            df.write_parquet(path, compression="zstd")
            log.debug("binance.saved", symbol=symbol, subfolder=subfolder,
                      rows=len(df))

    async def fetch_and_store(self, universe: list) -> None:
        """Coleta funding + basis + 4h klines para todo o universo."""
        semaphore = asyncio.Semaphore(5)

        async def fetch_one(asset) -> None:
            if asset.is_collapsed:
                log.debug("binance.skip_collapsed", symbol=asset.symbol)
                return  # colapsados não têm dados de derivativos ativos

            async with semaphore:
                async with aiohttp.ClientSession() as session:
                    try:
                        # Funding
                        raw_f = await self._fetch_funding_history(session, asset.symbol)
                        df_f  = self._parse_funding(raw_f, asset.symbol)
                        self._save_parquet(df_f, "funding", asset.symbol)

                        # Klines 4h (High/Low para Corwin-Schultz)
                        raw_k = await self._fetch_klines_4h(session, asset.symbol)
                        df_k  = self._parse_klines_4h(raw_k, asset.symbol)
                        self._save_parquet(df_k, "ohlcv_4h", asset.symbol)

                        log.info("binance.stored", symbol=asset.symbol,
                                 funding_rows=len(df_f), klines_rows=len(df_k))
                        await asyncio.sleep(0.3)

                    except Exception as e:  # noqa: BLE001
                        log.error("binance.error", symbol=asset.symbol, error=str(e))

        await asyncio.gather(*[fetch_one(a) for a in universe])
        log.info("binance.batch_complete")

    def load_funding(self, symbol: str) -> pl.DataFrame:
        """Lê funding rate histórico de um ativo."""
        path = self.parquet_base / "funding" / f"{symbol}.parquet"
        return pl.read_parquet(path) if path.exists() else pl.DataFrame()

    def load_klines_4h(self, symbol: str) -> pl.DataFrame:
        """Lê barras 4h para Corwin-Schultz e market impact."""
        path = self.parquet_base / "ohlcv_4h" / f"{symbol}.parquet"
        return pl.read_parquet(path) if path.exists() else pl.DataFrame()
