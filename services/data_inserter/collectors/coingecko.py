# =============================================================================
# DESTINO: services/data_inserter/collectors/coingecko.py
# v4: CORREÇÕES:
#   BUG 2: CG- prefix = Demo key → x-cg-demo-api-key (não x-cg-pro-api-key)
#   BUG 5: __init__ aceita parquet_base (main.py passa esse parâmetro)
#   BUG 6: fetch_and_store() implementado (main.py chama esse método)
#   BUG 8: Detecção correta de Pro vs Demo vs Free
# =============================================================================
from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime, timezone

import aiohttp
import structlog
from decouple import config

log = structlog.get_logger(__name__)

COINGECKO_FREE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_URL  = "https://pro-api.coingecko.com/api/v3"

STABLECOIN_IDS = {
    "tether", "usd-coin", "binance-usd", "dai", "true-usd",
    "frax", "usdd", "gemini-dollar", "paxos-standard",
    "paypal-usd",  # PYUSD — adicionado
}

# Rate limit: Demo ~30 req/min → 2.5s entre chamadas
RATE_LIMIT_DELAY = 2.5


def _detect_coingecko_config(api_key: str) -> tuple[str, dict, str]:
    """
    Detecta tipo de API key e retorna (base_url, headers, mode).
    - Sem key → free
    - CG- prefix → Demo (x-cg-demo-api-key, URL free)
    - Outro → Pro (x-cg-pro-api-key, URL pro)
    """
    if not api_key or api_key == "your_coingecko_pro_key_here":
        return COINGECKO_FREE_URL, {}, "free"
    if api_key.startswith("CG-"):
        return COINGECKO_FREE_URL, {"x-cg-demo-api-key": api_key}, "demo"
    return COINGECKO_PRO_URL, {"x-cg-pro-api-key": api_key}, "pro"


class CoinGeckoCollector:
    """
    Coleta OHLCV e metadados de mercado via CoinGecko.
    Detecta automaticamente tipo de API key (free/demo/pro).
    Salva em Parquet particionado por ativo.
    """

    def __init__(self, parquet_base: str = "/data/parquet") -> None:
        api_key = config("COINGECKO_API_KEY", default="")
        self._base, self._headers, self._mode = _detect_coingecko_config(api_key)
        self._parquet_base = Path(parquet_base)
        (self._parquet_base / "ohlcv_daily").mkdir(parents=True, exist_ok=True)

        log.info("coingecko.init",
                 api_mode=self._mode,
                 base_url=self._base)

    async def fetch_top_coins(
        self,
        session:     aiohttp.ClientSession,
        vs_currency: str = "usd",
        per_page:    int = 100,
        page:        int = 1,
    ) -> list[dict]:
        """
        Busca top coins por market cap.
        price_change_percentage omitido — causa 400 na API free/demo.
        """
        params: dict = {
            "vs_currency": vs_currency,
            "order":       "market_cap_desc",
            "per_page":    per_page,
            "page":        page,
            "sparkline":   "false",
        }

        try:
            async with session.get(
                f"{self._base}/coins/markets",
                params=params,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 429:
                    log.warning("coingecko.rate_limited", action="wait_60s")
                    await asyncio.sleep(60)
                    return await self.fetch_top_coins(session, vs_currency, per_page, page)
                if r.status != 200:
                    text = await r.text()
                    log.error("coingecko.fetch_error",
                              status=r.status, body=text[:200])
                    return []
                data = await r.json()
                log.info("coingecko.fetch_ok",
                         n_coins=len(data), page=page, mode=self._mode)
                return data

        except asyncio.TimeoutError:
            log.error("coingecko.timeout")
            return []
        except Exception as e:
            log.error("coingecko.exception", error=str(e))
            return []

    async def fetch_ohlcv(
        self,
        session:     aiohttp.ClientSession,
        coin_id:     str,
        vs_currency: str = "usd",
        days:        int = 365,
    ) -> list[list]:
        """Busca OHLCV diário: [timestamp_ms, open, high, low, close]."""
        try:
            async with session.get(
                f"{self._base}/coins/{coin_id}/ohlc",
                params={"vs_currency": vs_currency, "days": days},
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                if r.status == 429:
                    log.warning("coingecko.ohlcv_rate_limit", coin=coin_id)
                    await asyncio.sleep(60)
                    return await self.fetch_ohlcv(session, coin_id, vs_currency, days)
                if r.status != 200:
                    log.warning("coingecko.ohlcv_error",
                                coin=coin_id, status=r.status)
                    return []
                return await r.json()
        except Exception as e:
            log.error("coingecko.ohlcv_exception", coin=coin_id, error=str(e))
            return []

    async def fetch_and_store(self, universe: list) -> None:
        """
        Coleta OHLCV diário para todos os ativos do universo e salva em Parquet.
        CoinGecko OHLC: máx 365 dias na API free/demo.
        Para dados históricos 2019-2025: usar Binance klines (mais completo).
        """
        semaphore = asyncio.Semaphore(1)  # 1 concurrent — respeitar rate limit

        async def fetch_one(asset) -> None:
            async with semaphore:
                async with aiohttp.ClientSession() as session:
                    try:
                        # CoinGecko OHLC: max 365 dias free, max=max para pro
                        days = 365 if self._mode != "pro" else "max"
                        raw = await self.fetch_ohlcv(session, asset.coingecko_id,
                                                     days=days)
                        if not raw:
                            log.warning("coingecko.no_ohlcv", symbol=asset.symbol,
                                        coin_id=asset.coingecko_id)
                            return

                        self._save_ohlcv_parquet(raw, asset.symbol)
                        log.info("coingecko.stored_ohlcv", symbol=asset.symbol,
                                 rows=len(raw))
                        await asyncio.sleep(RATE_LIMIT_DELAY)

                    except Exception as e:
                        log.error("coingecko.fetch_store_error",
                                  symbol=asset.symbol, error=str(e))

        await asyncio.gather(*[fetch_one(a) for a in universe])
        log.info("coingecko.batch_complete", n_assets=len(universe))

    def _save_ohlcv_parquet(self, raw: list[list], symbol: str) -> None:
        """Salva OHLCV CoinGecko em Parquet. Dedup por timestamp."""
        try:
            import polars as pl

            ts    = [datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc) for r in raw]
            df = pl.DataFrame({
                "timestamp": ts,
                "symbol":    [symbol] * len(raw),
                "open":      pl.Series([float(r[1]) for r in raw], dtype=pl.Float32),
                "high":      pl.Series([float(r[2]) for r in raw], dtype=pl.Float32),
                "low":       pl.Series([float(r[3]) for r in raw], dtype=pl.Float32),
                "close":     pl.Series([float(r[4]) for r in raw], dtype=pl.Float32),
            }).sort("timestamp").unique(subset=["timestamp"], keep="last")

            path = self._parquet_base / "ohlcv_daily" / f"{symbol}.parquet"
            if path.exists():
                existing = pl.read_parquet(path)
                df = pl.concat([existing, df]).unique(
                    subset=["timestamp"], keep="last"
                ).sort("timestamp")

            df.write_parquet(path, compression="zstd")
        except Exception as e:
            log.error("coingecko.parquet_save_error", symbol=symbol, error=str(e))

    async def get_top_symbols(self, n: int = 30) -> list[str]:
        """Retorna top N símbolos filtrados (ex: ['SOLUSDT', 'AVAXUSDT', ...])."""
        async with aiohttp.ClientSession() as session:
            coins = await self.fetch_top_coins(session, per_page=min(n * 2, 250))

        symbols = []
        for c in coins:
            if c.get("id") in STABLECOIN_IDS:
                continue
            sym = c.get("symbol", "").upper()
            if sym and c.get("market_cap", 0) > 50_000_000:
                symbols.append(f"{sym}USDT")
            if len(symbols) >= n:
                break

        log.info("coingecko.symbols_selected",
                 n=len(symbols), sample=symbols[:5])
        return symbols
