# =============================================================================
# DESTINO: services/data_inserter/main.py
# Entry point do serviço de ingestão de dados.
# APScheduler executa os coletores em cron. Roda continuamente no container.
# =============================================================================
import asyncio
import sys
from datetime import datetime

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from decouple import config

from collectors.coingecko import CoinGeckoCollector
from collectors.binance import BinanceCollector
from validators.anti_survivorship import AntiSurvivorshipValidator

log = structlog.get_logger(__name__)

PARQUET_BASE = config("PARQUET_BASE_PATH", default="/data/parquet")
SQLITE_PATH  = config("SQLITE_PATH", default="/data/sqlite/sniper.db")
SCHEDULE     = config("SCHEDULE_CRON", default="0 */4 * * *")   # every 4h


async def run_full_ingest() -> None:
    """
    Pipeline completo de ingestão:
    1. Valida universo (anti-survivorship)
    2. Coleta OHLCV + derivativos via CoinGecko + Binance
    3. Salva em Parquet particionado por ativo/ano
    """
    start = datetime.utcnow()
    log.info("ingest.start", timestamp=start.isoformat())

    try:
        # ── 1. Universo point-in-time ────────────────────────────────────
        validator = AntiSurvivorshipValidator()
        universe  = await validator.build_universe_point_in_time(
            top_n=50,
            min_volume_usd=20_000_000,
            min_age_months=18,
        )
        log.info("ingest.universe", n_assets=len(universe))

        # ── 2. OHLCV diário via CoinGecko ────────────────────────────────
        cg = CoinGeckoCollector(parquet_base=PARQUET_BASE)
        await cg.fetch_and_store(universe)

        # ── 3. Funding rate + basis via Binance ──────────────────────────
        bn = BinanceCollector(parquet_base=PARQUET_BASE)
        await bn.fetch_and_store(universe)

        elapsed = (datetime.utcnow() - start).total_seconds()
        log.info("ingest.complete", elapsed_s=round(elapsed, 1))

    except Exception as exc:  # noqa: BLE001
        log.error("ingest.error", error=str(exc), exc_info=True)
        # Não re-raise: scheduler continua rodando na próxima janela


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )

    log.info("data_inserter.boot", schedule=SCHEDULE, parquet_base=PARQUET_BASE)

    # Executa imediatamente ao subir (não espera o próximo cron)
    await run_full_ingest()

    # Agenda execuções futuras
    scheduler = AsyncIOScheduler()
    # Parse cron string do .env: "0 */4 * * *"
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
        log.warning("ingest.schedule_invalid", raw=SCHEDULE,
                    fallback="every 4h")
        scheduler.add_job(run_full_ingest, "interval", hours=4)

    scheduler.start()
    log.info("data_inserter.scheduler_started")

    try:
        await asyncio.Event().wait()  # bloqueia para sempre
    except (KeyboardInterrupt, SystemExit):
        log.info("data_inserter.shutdown")
        scheduler.shutdown()
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
