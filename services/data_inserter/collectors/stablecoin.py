from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import polars as pl
import structlog
from decouple import config

log = structlog.get_logger(__name__)

COINGECKO_FREE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_URL = "https://pro-api.coingecko.com/api/v3"
DEFILLAMA_STABLECOINS_URL = "https://stablecoins.llama.fi/stablecoincharts/all"

# Basket explícito e auditável. Não é proxy de outro indicador; é a soma do market cap
# histórico dos principais stablecoins relevantes para o período.
DEFAULT_STABLECOIN_IDS = [
    "tether",
    "usd-coin",
    "dai",
    "binance-usd",
    "true-usd",
    "pax-dollar",
    "frax",
    "usdd",
    "first-digital-usd",
    "paypal-usd",
]


def _detect_coingecko_config(api_key: str) -> tuple[str, dict[str, str], str]:
    if not api_key or api_key == "your_coingecko_pro_key_here":
        return COINGECKO_FREE_URL, {}, "free"
    if api_key.startswith("CG-"):
        return COINGECKO_FREE_URL, {"x-cg-demo-api-key": api_key}, "demo"
    return COINGECKO_PRO_URL, {"x-cg-pro-api-key": api_key}, "pro"


class StablecoinCollector:
    """
    Gera a série macro stablecoin_chg30 a partir da soma do market cap histórico
    de uma cesta auditável de stablecoins via CoinGecko.

    Fallback opcional: DefiLlama público com market cap agregado do universo de
    stablecoins. Útil para reduzir dependência operacional do CoinGecko sem
    alterar a fonte do universo point-in-time da especificação.
    """

    RATE_LIMIT_DELAY = 2.5
    PUBLIC_RANGE_CHUNK_DAYS = 365

    def __init__(self, parquet_base: str) -> None:
        self.parquet_base = Path(parquet_base)
        self.output_dir = self.parquet_base / "stablecoin"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_path = self.output_dir / "stablecoin_chg30.parquet"

        self.cg_api_key = config("COINGECKO_API_KEY", default="")
        self.base_url, self.headers, self.cg_mode = _detect_coingecko_config(self.cg_api_key)
        self.source_mode = config("STABLECOIN_SOURCE", default="auto").strip().lower()

        raw_ids = config("STABLECOIN_IDS", default="")
        if raw_ids.strip():
            self.stablecoin_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
        else:
            self.stablecoin_ids = DEFAULT_STABLECOIN_IDS

    async def _fetch_defillama_market_cap_history(
        self,
        session: aiohttp.ClientSession,
    ) -> dict[datetime.date, float]:
        try:
            async with session.get(
                DEFILLAMA_STABLECOINS_URL,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 429:
                    log.warning("stablecoin.defillama_rate_limited", action="sleep_30s")
                    await asyncio.sleep(30)
                    return await self._fetch_defillama_market_cap_history(session)
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(
                        "stablecoin.defillama_http_error",
                        status=resp.status,
                        body=body[:200],
                    )
                    return {}
                payload = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("stablecoin.defillama_exception", error=str(exc))
            return {}

        out: dict[datetime.date, float] = {}
        for row in payload:
            raw_ts = row.get("date")
            totals = row.get("totalCirculatingUSD") or row.get("totalCirculating") or {}
            total_usd = totals.get("peggedUSD")
            if raw_ts is None or total_usd is None:
                continue
            try:
                ts = int(raw_ts)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                out[dt] = float(total_usd)
            except Exception:  # noqa: BLE001
                continue
        return out

    def _build_output_frame(
        self,
        total_caps_by_day: dict[datetime.date, float],
        counts_by_day: dict[datetime.date, int] | None,
        source: str,
    ) -> pl.DataFrame:
        if not total_caps_by_day:
            return pl.DataFrame()

        dates = sorted(total_caps_by_day)
        total_caps = [total_caps_by_day[d] for d in dates]
        counts = [counts_by_day.get(d) if counts_by_day else None for d in dates]

        df = pl.DataFrame(
            {
                "timestamp": dates,
                "stablecoin_market_cap_usd": total_caps,
                "source_coin_count": counts,
                "source_provider": [source] * len(dates),
            }
        ).sort("timestamp")

        return df.with_columns(
            (
                pl.when(pl.col("stablecoin_market_cap_usd").shift(30) > 0)
                .then(pl.col("stablecoin_market_cap_usd") / pl.col("stablecoin_market_cap_usd").shift(30) - 1.0)
                .otherwise(None)
            )
            .cast(pl.Float32)
            .alias("stablecoin_chg30")
        )

    async def _fetch_market_caps(
        self,
        session: aiohttp.ClientSession,
        coin_id: str,
        start_ts: int,
        end_ts: int,
    ) -> dict[datetime.date, float]:
        if self.cg_mode != "pro":
            return await self._fetch_market_caps_chunked(session, coin_id, start_ts, end_ts)

        url = f"{self.base_url}/coins/{coin_id}/market_chart/range"
        params = {
            "vs_currency": "usd",
            "from": start_ts,
            "to": end_ts,
        }
        try:
            async with session.get(
                url,
                params=params,
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status == 429:
                    log.warning("stablecoin.rate_limited", coin_id=coin_id, action="sleep_60s")
                    await asyncio.sleep(60)
                    return await self._fetch_market_caps(session, coin_id, start_ts, end_ts)
                if resp.status != 200:
                    body = await resp.text()
                    log.warning(
                        "stablecoin.marketcap_http_error",
                        coin_id=coin_id,
                        status=resp.status,
                        body=body[:200],
                    )
                    return {}
                payload = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("stablecoin.marketcap_exception", coin_id=coin_id, error=str(exc))
            return {}

        out: dict[datetime.date, float] = {}
        for ts_ms, market_cap in payload.get("market_caps", []):
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
            out[dt] = float(market_cap)
        return out

    async def _fetch_market_caps_chunked(
        self,
        session: aiohttp.ClientSession,
        coin_id: str,
        start_ts: int,
        end_ts: int,
    ) -> dict[datetime.date, float]:
        out: dict[datetime.date, float] = {}
        chunk_start = datetime.fromtimestamp(start_ts, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)

        while chunk_start < end_dt:
            chunk_end = min(chunk_start + timedelta(days=self.PUBLIC_RANGE_CHUNK_DAYS), end_dt)
            url = f"{self.base_url}/coins/{coin_id}/market_chart/range"
            params = {
                "vs_currency": "usd",
                "from": int(chunk_start.timestamp()),
                "to": int(chunk_end.timestamp()),
            }
            try:
                async with session.get(
                    url,
                    params=params,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 429:
                        log.warning("stablecoin.rate_limited", coin_id=coin_id, action="sleep_60s")
                        await asyncio.sleep(60)
                        continue
                    if resp.status in {401, 403}:
                        body = await resp.text()
                        log.warning(
                            "stablecoin.marketcap_range_blocked",
                            coin_id=coin_id,
                            status=resp.status,
                            chunk_start=chunk_start.date().isoformat(),
                            chunk_end=chunk_end.date().isoformat(),
                            body=body[:200],
                        )
                        return {}
                    if resp.status != 200:
                        body = await resp.text()
                        log.warning(
                            "stablecoin.marketcap_http_error",
                            coin_id=coin_id,
                            status=resp.status,
                            body=body[:200],
                            chunk_start=chunk_start.date().isoformat(),
                            chunk_end=chunk_end.date().isoformat(),
                        )
                        return {}
                    payload = await resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("stablecoin.marketcap_exception", coin_id=coin_id, error=str(exc))
                return {}

            for ts_ms, market_cap in payload.get("market_caps", []):
                dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
                out[dt] = float(market_cap)
            chunk_start = chunk_end + timedelta(seconds=1)
            await asyncio.sleep(self.RATE_LIMIT_DELAY)
        return out

    async def fetch_and_store(self) -> Path | None:
        start_dt = datetime(2019, 1, 1, tzinfo=timezone.utc)
        end_dt = datetime.now(timezone.utc)
        start_ts = int(start_dt.timestamp())
        end_ts = int(end_dt.timestamp())

        if self.source_mode not in {"coingecko_basket", "defillama_public", "auto"}:
            raise ValueError(
                "STABLECOIN_SOURCE invalido. Use coingecko_basket, defillama_public ou auto."
            )

        async with aiohttp.ClientSession() as session:
            df = pl.DataFrame()
            agg: defaultdict[datetime.date, float] = defaultdict(float)
            coin_count: defaultdict[datetime.date, int] = defaultdict(int)

            prefer_defillama = self.source_mode == "auto" and self.cg_mode != "pro"
            if prefer_defillama:
                log.info("stablecoin.auto_prefers_defillama", coingecko_mode=self.cg_mode)

            should_try_coingecko = self.source_mode == "coingecko_basket" or (
                self.source_mode == "auto" and not prefer_defillama
            )
            if should_try_coingecko:
                for coin_id in self.stablecoin_ids:
                    market_caps = await self._fetch_market_caps(session, coin_id, start_ts, end_ts)
                    if market_caps:
                        for dt, value in market_caps.items():
                            agg[dt] += value
                            coin_count[dt] += 1
                    await asyncio.sleep(self.RATE_LIMIT_DELAY)

                if agg:
                    df = self._build_output_frame(dict(agg), dict(coin_count), "coingecko_basket")

            should_try_defillama = self.source_mode == "defillama_public" or (
                self.source_mode == "auto" and (prefer_defillama or df.is_empty())
            )
            if should_try_defillama:
                agg_defillama = await self._fetch_defillama_market_cap_history(session)
                if agg_defillama:
                    df = self._build_output_frame(agg_defillama, None, "defillama_public")

        if df.is_empty():
            log.error(
                "stablecoin.empty_series",
                source_mode=self.source_mode,
                msg="Nenhum market cap historico foi obtido.",
            )
            return None

        df.write_parquet(self.output_path)
        log.info(
            "stablecoin.saved",
            path=str(self.output_path),
            n_rows=len(df),
            source_mode=self.source_mode,
            basket=self.stablecoin_ids,
        )
        return self.output_path
