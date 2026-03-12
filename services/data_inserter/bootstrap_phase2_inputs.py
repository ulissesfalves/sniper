#!/usr/bin/env python3
"""
Bootstrap explícito dos insumos upstream da Fase 2.

Baixa:
- basis_3m via Binance público
- stablecoin_chg30 via CoinGecko basket ou DefiLlama público
- pacote unlock_pressure_rank via Mobula/CoinGecko/Wayback

Uso:
  python bootstrap_phase2_inputs.py --check
  python bootstrap_phase2_inputs.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog
from decouple import config

from collectors.binance import BinanceCollector
from collectors.stablecoin import StablecoinCollector
from collectors.token_unlocks import TokenUnlocksCollector
from main import _load_symbols_from_existing_ohlcv

PARQUET_BASE = config("PARQUET_BASE_PATH", default="/data/parquet")


def _key_state(env_name: str) -> str:
    value = config(env_name, default="").strip()
    if not value:
        return "missing"
    if "your_" in value.lower() or "<" in value or "changeme" in value.lower():
        return "placeholder"
    return "configured"


def _preflight() -> tuple[list[str], dict[str, str]]:
    symbols = _load_symbols_from_existing_ohlcv(PARQUET_BASE)
    statuses = {
        "COINGECKO_API_KEY": _key_state("COINGECKO_API_KEY"),
        "MOBULA_API_KEY": _key_state("MOBULA_API_KEY"),
        "DEFILLAMA_UNLOCKS_ENDPOINT": config("DEFILLAMA_UNLOCKS_ENDPOINT", default="").strip() or "not_configured",
        "BINANCE_API_KEY": _key_state("BINANCE_API_KEY"),
        "BINANCE_API_SECRET": _key_state("BINANCE_API_SECRET"),
        "STABLECOIN_SOURCE": config("STABLECOIN_SOURCE", default="auto").strip().lower() or "auto",
        "PARQUET_BASE_PATH": PARQUET_BASE,
    }
    return symbols, statuses


async def _run() -> int:
    symbols, statuses = _preflight()
    if not symbols:
        print("ERRO: nenhum parquet em ohlcv_daily foi encontrado; rode o bootstrap histórico antes.")
        return 2

    print(f"Parquet base: {statuses['PARQUET_BASE_PATH']}")
    print(f"Universe local detectado: {len(symbols)} simbolos")
    print(f"Stablecoin source: {statuses['STABLECOIN_SOURCE']}")
    print(f"CoinGecko key: {statuses['COINGECKO_API_KEY']}")
    print(f"Mobula key: {statuses['MOBULA_API_KEY']}")
    print(f"DefiLlama endpoint: {statuses['DEFILLAMA_UNLOCKS_ENDPOINT']}")
    print("Iniciando downloads...")

    bn = BinanceCollector(parquet_base=PARQUET_BASE)
    await bn.fetch_and_store(symbols)

    stable = StablecoinCollector(parquet_base=PARQUET_BASE)
    stable_path = await stable.fetch_and_store()
    print(f"stablecoin_chg30: {'ok' if stable_path else 'falhou'}")

    unlocks = TokenUnlocksCollector(
        parquet_base=PARQUET_BASE,
        runtime_history_mode="full",
    )
    written = await unlocks.fetch_and_store(symbols)
    print(f"unlock_pressure_rank bundle: ok ({len(written)} arquivos)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap dos insumos upstream da Fase 2.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exibe preflight das dependencias sem baixar dados.",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )

    symbols, statuses = _preflight()
    print(f"Parquet base: {statuses['PARQUET_BASE_PATH']}")
    print(f"Universe local detectado: {len(symbols)} simbolos")
    print(f"Stablecoin source: {statuses['STABLECOIN_SOURCE']}")
    print(f"CoinGecko key: {statuses['COINGECKO_API_KEY']}")
    print(f"Mobula key: {statuses['MOBULA_API_KEY']}")
    print(f"DefiLlama endpoint: {statuses['DEFILLAMA_UNLOCKS_ENDPOINT']}")
    print("Funding/basis/4h via Binance publico: pronto")
    print("Stablecoin via CoinGecko/DefiLlama: pronto")
    print("Unlocks via Mobula/CoinGecko/Wayback: pronto")

    if args.check:
        return 0

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
