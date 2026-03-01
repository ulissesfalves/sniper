#!/usr/bin/env python3
"""
=============================================================================
DESTINO: services/data_inserter/bootstrap_historical.py
SNIPER v10.10 — Bootstrap de Dados Históricos 2019-2025

Baixa klines diários de TODOS os ativos do universo via Binance Spot API.
Fallback para CoinGecko /market_chart para ativos delistados (LUNA, FTT, CEL).
Salva em Parquet compatível com o pipeline existente.

USO:
  docker-compose exec data_inserter python bootstrap_historical.py

NOTA: Binance public endpoints NÃO requerem API key para klines.
      Rate limit: 1200 req/min (spot). Script usa ~5 req/s = muito seguro.
=============================================================================
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import aiohttp
import polars as pl
import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger("bootstrap")

# ─── CONFIG ──────────────────────────────────────────────────────────────────
PARQUET_BASE    = Path("/data/parquet")
OHLCV_DIR       = PARQUET_BASE / "ohlcv_daily"
BINANCE_SPOT    = "https://api.binance.com"
COINGECKO_FREE  = "https://api.coingecko.com/api/v3"

START_DATE      = datetime(2019, 1, 1, tzinfo=timezone.utc)
END_DATE        = datetime.now(timezone.utc)
KLINES_LIMIT    = 1000  # max por request Binance

# Rate limits (segundos entre requests)
BINANCE_DELAY   = 0.15   # ~6.6 req/s (Binance permite 1200/min = 20/s)
COINGECKO_DELAY = 2.5    # Demo: ~30 req/min


# ─── UNIVERSO: symbol interno → Binance spot symbol + CoinGecko ID ───────────
# Mapeamento completo para os 51 ativos do universo construído pelo data_inserter.
# Binance symbol = None → ativo não existe no Binance spot (usar CoinGecko).
UNIVERSE = [
    # ── Top ativos por market cap (ativos no universo atual) ─────────────
    {"symbol": "XRP",    "binance": "XRPUSDT",    "coingecko": "ripple"},
    {"symbol": "BNB",    "binance": "BNBUSDT",     "coingecko": "binancecoin"},
    {"symbol": "SOL",    "binance": "SOLUSDT",     "coingecko": "solana"},
    {"symbol": "TRX",    "binance": "TRXUSDT",     "coingecko": "tron"},
    {"symbol": "DOGE",   "binance": "DOGEUSDT",    "coingecko": "dogecoin"},
    {"symbol": "ADA",    "binance": "ADAUSDT",     "coingecko": "cardano"},
    {"symbol": "LINK",   "binance": "LINKUSDT",    "coingecko": "chainlink"},
    {"symbol": "AVAX",   "binance": "AVAXUSDT",    "coingecko": "avalanche-2"},
    {"symbol": "XLM",    "binance": "XLMUSDT",     "coingecko": "stellar"},
    {"symbol": "HBAR",   "binance": "HBARUSDT",    "coingecko": "hedera-hashgraph"},
    {"symbol": "SUI",    "binance": "SUIUSDT",     "coingecko": "sui"},
    {"symbol": "TON",    "binance": "TONUSDT",     "coingecko": "the-open-network"},
    {"symbol": "DOT",    "binance": "DOTUSDT",     "coingecko": "polkadot"},
    {"symbol": "BCH",    "binance": "BCHUSDT",     "coingecko": "bitcoin-cash"},
    {"symbol": "LTC",    "binance": "LTCUSDT",     "coingecko": "litecoin"},
    {"symbol": "UNI",    "binance": "UNIUSDT",     "coingecko": "uniswap"},
    {"symbol": "NEAR",   "binance": "NEARUSDT",    "coingecko": "near"},
    {"symbol": "AAVE",   "binance": "AAVEUSDT",    "coingecko": "aave"},
    {"symbol": "ICP",    "binance": "ICPUSDT",     "coingecko": "internet-computer"},
    {"symbol": "ETC",    "binance": "ETCUSDT",     "coingecko": "ethereum-classic"},
    {"symbol": "ATOM",   "binance": "ATOMUSDT",    "coingecko": "cosmos"},
    {"symbol": "FIL",    "binance": "FILUSDT",     "coingecko": "filecoin"},
    {"symbol": "APT",    "binance": "APTUSDT",     "coingecko": "aptos"},
    {"symbol": "ARB",    "binance": "ARBUSDT",     "coingecko": "arbitrum"},
    {"symbol": "RENDER", "binance": "RENDERUSDT",  "coingecko": "render-token"},
    {"symbol": "PEPE",   "binance": "PEPEUSDT",    "coingecko": "pepe"},
    {"symbol": "TAO",    "binance": "TAOUSDT",     "coingecko": "bittensor"},
    {"symbol": "WLD",    "binance": "WLDUSDT",     "coingecko": "worldcoin-wld"},
    {"symbol": "ENA",    "binance": "ENAUSDT",     "coingecko": "ethena"},
    {"symbol": "ONDO",   "binance": "ONDOUSDT",    "coingecko": "ondo-finance"},
    {"symbol": "POL",    "binance": "POLUSDT",     "coingecko": "polygon-ecosystem-token"},
    {"symbol": "BONK",   "binance": "BONKUSDT",    "coingecko": "bonk"},
    {"symbol": "SHIB",   "binance": "SHIBUSDT",    "coingecko": "shiba-inu"},
    {"symbol": "XMR",    "binance": "XMRUSDT",     "coingecko": "monero"},
    {"symbol": "ZEC",    "binance": "ZECUSDT",     "coingecko": "zcash"},
    {"symbol": "TRUMP",  "binance": "TRUMPUSDT",   "coingecko": "official-trump"},
    {"symbol": "MORPHO", "binance": "MORPHOUSDT",  "coingecko": "morpho"},
    {"symbol": "PIPPIN", "binance": "PIPPINUSDT",  "coingecko": "pippin"},
    {"symbol": "PUMP",   "binance": "PUMPUSDT",    "coingecko": "pump"},

    # Ativos que podem não ter par USDT no Binance spot — fallback CoinGecko
    {"symbol": "WBT",    "binance": None,           "coingecko": "whitebit"},
    {"symbol": "HYPE",   "binance": "HYPEUSDT",     "coingecko": "hyperliquid"},
    {"symbol": "RAIN",   "binance": None,            "coingecko": "rainmaker-games"},
    {"symbol": "WLFI",   "binance": None,            "coingecko": "world-liberty-financial"},
    {"symbol": "XAUT",   "binance": None,            "coingecko": "tether-gold"},
    {"symbol": "PAXG",   "binance": "PAXGUSDT",     "coingecko": "pax-gold"},
    {"symbol": "ASTER",  "binance": "ASTERUSDT",    "coingecko": "aster-defi"},

    # ── COLAPSADOS OBRIGATÓRIOS (anti-survivorship bias) ─────────────────
    # Binance delistou estes pares. Dados históricos via CoinGecko.
    {"symbol": "LUNA",   "binance": "LUNAUSDT",     "coingecko": "terra-luna"},
    {"symbol": "LUNC",   "binance": "LUNCUSDT",     "coingecko": "terra-luna"},
    {"symbol": "LUNA2",  "binance": "LUNA2USDT",    "coingecko": "terra-luna-2"},
    {"symbol": "FTT",    "binance": "FTTUSDT",      "coingecko": "ftx-token"},
    {"symbol": "CEL",    "binance": "CELUSDT",      "coingecko": "celsius-degree-token"},
]


# ─── BINANCE KLINES DOWNLOADER ──────────────────────────────────────────────

async def fetch_binance_klines_page(
    session: aiohttp.ClientSession,
    symbol: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """Busca até 1000 klines diários de um par no Binance spot."""
    url = f"{BINANCE_SPOT}/api/v3/klines"
    params = {
        "symbol":    symbol,
        "interval":  "1d",
        "startTime": start_ms,
        "endTime":   end_ms,
        "limit":     KLINES_LIMIT,
    }
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status == 400:
                return []  # par não existe
            if r.status == 429:
                log.warning("binance.rate_limit", symbol=symbol)
                await asyncio.sleep(30)
                return await fetch_binance_klines_page(session, symbol, start_ms, end_ms)
            if r.status != 200:
                log.warning("binance.http_error", symbol=symbol, status=r.status)
                return []
            return await r.json()
    except Exception as e:
        log.error("binance.exception", symbol=symbol, error=str(e))
        return []


async def download_binance_full_history(
    session: aiohttp.ClientSession,
    binance_symbol: str,
    internal_symbol: str,
) -> pl.DataFrame | None:
    """
    Pagina pela API Binance para baixar TODO o histórico diário.
    Binance retorna max 1000 candles/request. Pagina via startTime.
    """
    all_rows: list[list] = []
    start_ms = int(START_DATE.timestamp() * 1000)
    end_ms   = int(END_DATE.timestamp() * 1000)

    page = 0
    while start_ms < end_ms:
        page += 1
        data = await fetch_binance_klines_page(session, binance_symbol, start_ms, end_ms)

        if not data:
            break  # sem mais dados ou par não existe

        all_rows.extend(data)

        # Próxima página: último timestamp + 1ms
        last_ts = int(data[-1][0])
        if last_ts <= start_ms:
            break  # evita loop infinito
        start_ms = last_ts + 1

        if page % 5 == 0:
            log.debug("binance.paging", symbol=internal_symbol,
                      page=page, rows_so_far=len(all_rows))

        await asyncio.sleep(BINANCE_DELAY)

    if not all_rows:
        return None

    return _parse_klines_to_df(all_rows, internal_symbol)


# ─── COINGECKO FALLBACK ─────────────────────────────────────────────────────

async def download_coingecko_history(
    session: aiohttp.ClientSession,
    coingecko_id: str,
    internal_symbol: str,
    api_key: str = "",
) -> pl.DataFrame | None:
    """
    Fallback: baixa histórico via CoinGecko /market_chart.
    API free/demo: max=max retorna desde o início do ativo.
    Retorna OHLC aproximado (CoinGecko market_chart retorna price, não OHLC).
    Para OHLC real: usa /coins/{id}/ohlc com days=max (Pro) ou 365 (free).
    """
    headers = {}
    if api_key and api_key.startswith("CG-"):
        headers = {"x-cg-demo-api-key": api_key}

    # Tenta /ohlc primeiro (retorna OHLC real, mas limitado a 365d free)
    ohlc_df = await _try_coingecko_ohlc(session, coingecko_id, internal_symbol, headers)
    if ohlc_df is not None and len(ohlc_df) > 100:
        return ohlc_df

    # Fallback: /market_chart com days=max (retorna preço, sem H/L reais)
    url = f"{COINGECKO_FREE}/coins/{coingecko_id}/market_chart"
    params = {"vs_currency": "usd", "days": "max", "interval": "daily"}

    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=60)) as r:
            if r.status == 429:
                log.warning("coingecko.rate_limit", symbol=internal_symbol)
                await asyncio.sleep(60)
                return await download_coingecko_history(
                    session, coingecko_id, internal_symbol, api_key)
            if r.status != 200:
                log.warning("coingecko.market_chart_fail",
                            symbol=internal_symbol, status=r.status)
                return None
            data = await r.json()
    except Exception as e:
        log.error("coingecko.exception", symbol=internal_symbol, error=str(e))
        return None

    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])

    if not prices:
        return None

    # market_chart retorna [timestamp_ms, price]. Sem OHLC real.
    # Aproximação: open=close=price, high=price*1.01, low=price*0.99
    # NOTA: dados de backtest devem preferir Binance klines (OHLC real).
    ts = [datetime.fromtimestamp(p[0] / 1000, tz=timezone.utc) for p in prices]
    close_prices = [float(p[1]) for p in prices]

    vol_map = {int(v[0]): float(v[1]) for v in volumes} if volumes else {}

    df = pl.DataFrame({
        "timestamp":    ts,
        "symbol":       [internal_symbol] * len(prices),
        "open":         pl.Series(close_prices, dtype=pl.Float32),
        "high":         pl.Series(close_prices, dtype=pl.Float32),
        "low":          pl.Series(close_prices, dtype=pl.Float32),
        "close":        pl.Series(close_prices, dtype=pl.Float32),
        "volume":       pl.Series(
            [vol_map.get(int(p[0]), 0.0) for p in prices], dtype=pl.Float64
        ),
        "quote_volume": pl.Series(
            [vol_map.get(int(p[0]), 0.0) for p in prices], dtype=pl.Float64
        ),
        "source":       ["coingecko_market_chart"] * len(prices),
    }).sort("timestamp").unique(subset=["timestamp"], keep="last")

    return df


async def _try_coingecko_ohlc(
    session: aiohttp.ClientSession,
    coingecko_id: str,
    symbol: str,
    headers: dict,
) -> pl.DataFrame | None:
    """Tenta CoinGecko /ohlc com days=max."""
    url = f"{COINGECKO_FREE}/coins/{coingecko_id}/ohlc"
    params = {"vs_currency": "usd", "days": "max"}

    try:
        async with session.get(url, params=params, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=30)) as r:
            if r.status != 200:
                return None
            data = await r.json()
    except Exception:
        return None

    if not data or len(data) < 10:
        return None

    ts = [datetime.fromtimestamp(r[0] / 1000, tz=timezone.utc) for r in data]
    return pl.DataFrame({
        "timestamp":    ts,
        "symbol":       [symbol] * len(data),
        "open":         pl.Series([float(r[1]) for r in data], dtype=pl.Float32),
        "high":         pl.Series([float(r[2]) for r in data], dtype=pl.Float32),
        "low":          pl.Series([float(r[3]) for r in data], dtype=pl.Float32),
        "close":        pl.Series([float(r[4]) for r in data], dtype=pl.Float32),
        "volume":       pl.Series([0.0] * len(data), dtype=pl.Float64),
        "quote_volume": pl.Series([0.0] * len(data), dtype=pl.Float64),
        "source":       ["coingecko_ohlc"] * len(data),
    }).sort("timestamp").unique(subset=["timestamp"], keep="last")


# ─── PARSING & SAVING ───────────────────────────────────────────────────────

def _parse_klines_to_df(raw: list[list], symbol: str) -> pl.DataFrame:
    """Parse Binance klines → Polars DataFrame com schema padrão."""
    ts = [datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc) for k in raw]
    return pl.DataFrame({
        "timestamp":    ts,
        "symbol":       [symbol] * len(raw),
        "open":         pl.Series([float(k[1]) for k in raw], dtype=pl.Float32),
        "high":         pl.Series([float(k[2]) for k in raw], dtype=pl.Float32),
        "low":          pl.Series([float(k[3]) for k in raw], dtype=pl.Float32),
        "close":        pl.Series([float(k[4]) for k in raw], dtype=pl.Float32),
        "volume":       pl.Series([float(k[5]) for k in raw], dtype=pl.Float64),
        "quote_volume": pl.Series([float(k[7]) for k in raw], dtype=pl.Float64),
        "source":       ["binance_spot"] * len(raw),
    }).sort("timestamp").unique(subset=["timestamp"], keep="last")


def save_parquet(df: pl.DataFrame, symbol: str) -> None:
    """Salva/merge com Parquet existente (dedup por timestamp)."""
    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    path = OHLCV_DIR / f"{symbol}.parquet"

    if path.exists():
        existing = pl.read_parquet(path)
        # Se existente não tem coluna 'source', adicionar
        if "source" not in existing.columns:
            existing = existing.with_columns(pl.lit("legacy").alias("source"))
        if "volume" not in existing.columns:
            existing = existing.with_columns(pl.lit(0.0).alias("volume").cast(pl.Float64))
        if "quote_volume" not in existing.columns:
            existing = existing.with_columns(pl.lit(0.0).alias("quote_volume").cast(pl.Float64))

        # Merge: dados Binance (OHLC real) têm prioridade sobre CoinGecko
        df = pl.concat([existing, df], how="diagonal").unique(
            subset=["timestamp"], keep="last"
        ).sort("timestamp")

    df.write_parquet(path, compression="zstd")


# ─── MAIN PIPELINE ──────────────────────────────────────────────────────────

async def bootstrap_asset(
    session: aiohttp.ClientSession,
    asset: dict,
    semaphore: asyncio.Semaphore,
    cg_api_key: str = "",
) -> dict:
    """Baixa histórico completo de um ativo. Binance → CoinGecko fallback."""
    symbol  = asset["symbol"]
    bn_sym  = asset.get("binance")
    cg_id   = asset.get("coingecko", "")

    async with semaphore:
        df = None
        source = "none"

        # ── Tentativa 1: Binance spot klines ──────────────────────────────
        if bn_sym:
            log.info("bootstrap.trying_binance", symbol=symbol, pair=bn_sym)
            df = await download_binance_full_history(session, bn_sym, symbol)
            if df is not None and len(df) > 30:
                source = "binance"
                log.info("bootstrap.binance_ok", symbol=symbol,
                         rows=len(df),
                         start=str(df["timestamp"].min()),
                         end=str(df["timestamp"].max()))

        # ── Tentativa 2: CoinGecko fallback ──────────────────────────────
        if df is None or len(df) < 30:
            if cg_id:
                log.info("bootstrap.trying_coingecko", symbol=symbol, cg_id=cg_id)
                await asyncio.sleep(COINGECKO_DELAY)
                cg_df = await download_coingecko_history(
                    session, cg_id, symbol, cg_api_key)
                if cg_df is not None and len(cg_df) > 0:
                    # Merge: Binance data (se existir) tem prioridade
                    if df is not None and len(df) > 0:
                        df = pl.concat([cg_df, df], how="diagonal").unique(
                            subset=["timestamp"], keep="last").sort("timestamp")
                    else:
                        df = cg_df
                    source = "binance+coingecko" if source == "binance" else "coingecko"
                    log.info("bootstrap.coingecko_ok", symbol=symbol, rows=len(df))
                await asyncio.sleep(COINGECKO_DELAY)

        # ── Salvar ────────────────────────────────────────────────────────
        if df is not None and len(df) > 0:
            save_parquet(df, symbol)
            return {"symbol": symbol, "source": source, "rows": len(df),
                    "start": str(df["timestamp"].min()),
                    "end": str(df["timestamp"].max()), "status": "ok"}
        else:
            log.warning("bootstrap.no_data", symbol=symbol)
            return {"symbol": symbol, "source": "none", "rows": 0,
                    "status": "failed"}


async def main() -> None:
    log.info("bootstrap.start",
             n_assets=len(UNIVERSE),
             start_date=str(START_DATE.date()),
             end_date=str(END_DATE.date()))

    OHLCV_DIR.mkdir(parents=True, exist_ok=True)

    # Ler CoinGecko API key se disponível
    cg_key = ""
    try:
        from decouple import config
        cg_key = config("COINGECKO_API_KEY", default="")
    except Exception:
        pass

    # Semaphore: 3 concurrent para Binance (conservador)
    semaphore = asyncio.Semaphore(3)
    results = []

    async with aiohttp.ClientSession() as session:
        tasks = [
            bootstrap_asset(session, asset, semaphore, cg_key)
            for asset in UNIVERSE
        ]
        results = await asyncio.gather(*tasks)

    # ── Relatório final ──────────────────────────────────────────────────
    ok      = [r for r in results if r["status"] == "ok"]
    failed  = [r for r in results if r["status"] != "ok"]

    log.info("=" * 60)
    log.info("bootstrap.complete",
             total=len(results), success=len(ok), failed=len(failed))

    total_rows = 0
    for r in sorted(ok, key=lambda x: x["rows"], reverse=True):
        total_rows += r["rows"]
        log.info("bootstrap.asset_ok",
                 symbol=r["symbol"], source=r["source"],
                 rows=r["rows"], range=f"{r['start']} → {r['end']}")

    if failed:
        log.warning("bootstrap.failed_assets",
                    symbols=[r["symbol"] for r in failed])

    log.info("bootstrap.summary",
             total_rows=total_rows,
             parquet_dir=str(OHLCV_DIR),
             msg="Dados prontos para Fase 2 (FracDiff + HMM)")


if __name__ == "__main__":
    print("=" * 60)
    print("SNIPER v10.10 — Bootstrap Histórico 2019-2025")
    print(f"Ativos: {len(UNIVERSE)} | Destino: {OHLCV_DIR}")
    print("=" * 60)
    asyncio.run(main())
