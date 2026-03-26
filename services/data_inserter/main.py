# =============================================================================
# DESTINO: services/data_inserter/main.py
# Entry point do serviço de ingestão de dados.
# APScheduler executa os coletores em cron. Roda continuamente no container.
# =============================================================================
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from decouple import config

from collectors.coingecko import CoinGeckoCollector
from collectors.binance import BinanceCollector
from collectors.stablecoin import StablecoinCollector
from collectors.token_unlocks import TokenUnlocksCollector
from validators.anti_survivorship import AntiSurvivorshipValidator, AssetRecord

log = structlog.get_logger(__name__)

PARQUET_BASE = config("PARQUET_BASE_PATH", default="/data/parquet")
SQLITE_PATH = config("SQLITE_PATH", default="/data/sqlite/sniper.db")
SCHEDULE = config("SCHEDULE_CRON", default="0 */4 * * *")   # every 4h
REFERENCE_ONLY_ASSET_SPECS = (
    {"symbol": "BTC", "coingecko_id": "bitcoin"},
)
REFERENCE_ONLY_SYMBOLS = {spec["symbol"] for spec in REFERENCE_ONLY_ASSET_SPECS}


def _probe_runtime_dir(path: Path) -> dict[str, object]:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise RuntimeError(f"Runtime path is not a directory: {path}")

    writable = os.access(path, os.W_OK)
    probe_path = path / ".sniper_write_probe.tmp"
    probe_result = "ok"
    try:
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except Exception as exc:  # noqa: BLE001
        probe_result = str(exc)
        writable = False

    return {
        "path": str(path),
        "exists": path.exists(),
        "is_dir": path.is_dir(),
        "writable": writable,
        "probe_result": probe_result,
    }


def audit_runtime_storage() -> None:
    targets = {
        "parquet_base": Path(PARQUET_BASE),
        "ohlcv_daily": Path(PARQUET_BASE) / "ohlcv_daily",
        "funding": Path(PARQUET_BASE) / "funding",
        "basis": Path(PARQUET_BASE) / "basis",
        "stablecoin": Path(PARQUET_BASE) / "stablecoin",
        "unlocks": Path(PARQUET_BASE) / "unlocks",
        "unlock_market": Path(PARQUET_BASE) / "unlock_market",
        "sqlite_dir": Path(SQLITE_PATH).parent,
        "logs": Path("/app/logs"),
    }
    failures: dict[str, dict[str, object]] = {}
    for label, target in targets.items():
        status = _probe_runtime_dir(target)
        log.info("runtime.storage_probe", label=label, **status)
        if not bool(status["writable"]):
            failures[label] = status

    if failures:
        raise RuntimeError(f"Runtime storage is not writable: {failures}")


def _load_symbols_from_existing_ohlcv(parquet_base: str) -> list[str]:
    base = Path(parquet_base) / "ohlcv_daily"
    if not base.exists():
        return []
    symbols = sorted({p.stem.upper() for p in base.glob("*.parquet")})
    for mandatory in ["LUNA", "LUNC", "LUNA2", "FTT", "CEL"]:
        if mandatory not in symbols:
            symbols.append(mandatory)
    return sorted(set(symbols))


def _build_reference_only_assets() -> list[AssetRecord]:
    return [
        AssetRecord(
            symbol=spec["symbol"],
            coingecko_id=spec["coingecko_id"],
            market_cap_usd=0.0,
            volume_24h_usd=0.0,
            age_months=999,
            ups_score=0.0,
            ups_data_available=False,
            is_collapsed=False,
        )
        for spec in REFERENCE_ONLY_ASSET_SPECS
    ]


def _extend_with_reference_assets(universe: list[AssetRecord]) -> list[AssetRecord]:
    seen = {asset.symbol.upper() for asset in universe}
    additions = [
        asset for asset in _build_reference_only_assets()
        if asset.symbol.upper() not in seen
    ]
    if additions:
        log.info(
            "ingest.reference_assets_added",
            symbols=[asset.symbol for asset in additions],
        )
    return universe + additions


def _exclude_reference_only_symbols(symbols: list[str]) -> list[str]:
    return [symbol for symbol in symbols if symbol.upper() not in REFERENCE_ONLY_SYMBOLS]


async def _resolve_universe() -> list:
    validator = AntiSurvivorshipValidator()
    universe = await validator.build_universe_point_in_time(
        top_n=50,
        min_volume_usd=20_000_000,
        min_age_months=18,
    )
    log.info("ingest.universe", n_assets=len(universe))
    return universe


async def run_missing_feature_collectors() -> None:
    """
    Regera apenas os insumos faltantes da especificação a partir do universo já existente
    no notebook. Útil quando a Fase 1 já está pronta e o foco é basis/stablecoin/unlocks.
    """
    start = datetime.utcnow()
    symbols = _load_symbols_from_existing_ohlcv(PARQUET_BASE)
    if not symbols:
        raise RuntimeError("Nenhum parquet em /data/parquet/ohlcv_daily encontrado.")
    unlock_symbols = _exclude_reference_only_symbols(symbols)

    log.info(
        "ingest.missing_features_start",
        n_symbols=len(symbols),
        unlock_symbols=len(unlock_symbols),
        reference_only_symbols=sorted(REFERENCE_ONLY_SYMBOLS),
    )
    bn = BinanceCollector(parquet_base=PARQUET_BASE)
    await bn.fetch_and_store(symbols)

    stable = StablecoinCollector(parquet_base=PARQUET_BASE)
    await stable.fetch_and_store()

    unlocks = TokenUnlocksCollector(parquet_base=PARQUET_BASE)
    await unlocks.fetch_and_store(unlock_symbols)

    elapsed = (datetime.utcnow() - start).total_seconds()
    log.info("ingest.missing_features_complete", elapsed_s=round(elapsed, 1))


async def run_full_ingest() -> None:
    """
    Pipeline completo de ingestão:
    1. Valida universo (anti-survivorship + UPS atual quando disponível)
    2. Coleta OHLCV diário via CoinGecko
    3. Coleta funding, OHLCV 4h e basis 3m via Binance
    4. Coleta stablecoin_chg30
    5. Coleta o pacote unlock_pressure_rank via Mobula/CoinGecko/Wayback
    """
    start = datetime.utcnow()
    log.info("ingest.start", timestamp=start.isoformat())

    try:
        audit_runtime_storage()
        universe = await _resolve_universe()
        market_data_universe = _extend_with_reference_assets(universe)

        cg = CoinGeckoCollector(parquet_base=PARQUET_BASE)
        await cg.fetch_and_store(market_data_universe)

        bn = BinanceCollector(parquet_base=PARQUET_BASE)
        await bn.fetch_and_store(market_data_universe)

        stable = StablecoinCollector(parquet_base=PARQUET_BASE)
        await stable.fetch_and_store()

        unlocks = TokenUnlocksCollector(parquet_base=PARQUET_BASE)
        await unlocks.fetch_and_store(universe)

        elapsed = (datetime.utcnow() - start).total_seconds()
        log.info("ingest.complete", elapsed_s=round(elapsed, 1))

    except Exception as exc:  # noqa: BLE001
        log.error("ingest.error", error=str(exc), exc_info=True)


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )

    log.info("data_inserter.boot", schedule=SCHEDULE, parquet_base=PARQUET_BASE)

    await run_full_ingest()

    scheduler = AsyncIOScheduler()
    cron_parts = SCHEDULE.split()
    if len(cron_parts) == 5:
        minute, hour, day, month, day_of_week = cron_parts
        scheduler.add_job(
            run_full_ingest,
            trigger="cron",
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            misfire_grace_time=300,
        )
    else:
        log.warning("ingest.schedule_invalid", raw=SCHEDULE, fallback="every 4h")
        scheduler.add_job(run_full_ingest, "interval", hours=4)

    scheduler.start()
    log.info("data_inserter.scheduler_started")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        log.info("data_inserter.shutdown")
        scheduler.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
