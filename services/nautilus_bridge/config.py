from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


TARGET_STREAM_KEY = "sniper:portfolio_targets:v1"
STATUS_STREAM_KEY = "sniper:portfolio_status:v1"
STATE_PREFIX = "sniper:portfolio_state:v1"
REVISION_PREFIX = "sniper:portfolio_revision:v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def revision_key(portfolio_id: str, environment: str) -> str:
    return f"{REVISION_PREFIX}:{portfolio_id}:{environment}"


def stream_cursor_key(bridge_id: str, environment: str) -> str:
    return f"{STATE_PREFIX}:{bridge_id}:{environment}:stream_cursor"


def portfolio_state_key(portfolio_id: str, environment: str, suffix: str) -> str:
    return f"{STATE_PREFIX}:{portfolio_id}:{environment}:{suffix}"


def daemon_lock_key(portfolio_id: str, environment: str) -> str:
    return portfolio_state_key(portfolio_id, environment, "paper_daemon_lock")


def heartbeat_key(portfolio_id: str, environment: str) -> str:
    return portfolio_state_key(portfolio_id, environment, "paper_daemon_heartbeat")


def run_summary_key(portfolio_id: str, environment: str) -> str:
    return portfolio_state_key(portfolio_id, environment, "paper_daemon_summary")


@dataclass(frozen=True)
class ManagedUniverse:
    version: str
    venue: str
    quote_currency: str
    instruments_by_symbol: dict[str, str]

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self.instruments_by_symbol.values()))

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted(self.instruments_by_symbol.keys()))

    def instrument_id_for_symbol(self, symbol: str) -> str:
        normalized = symbol.strip().upper()
        try:
            return self.instruments_by_symbol[normalized]
        except KeyError as exc:
            raise KeyError(f"Unknown managed symbol: {symbol}") from exc


def load_managed_universe(path: Path | None = None) -> ManagedUniverse:
    universe_path = path or Path(__file__).with_name("instrument_map.json")
    payload = json.loads(universe_path.read_text(encoding="utf-8"))
    instruments = {
        str(symbol).strip().upper(): str(instrument_id).strip()
        for symbol, instrument_id in payload.get("instruments", {}).items()
        if str(symbol).strip() and str(instrument_id).strip()
    }
    if not instruments:
        raise ValueError(f"Managed universe is empty: {universe_path}")
    version = str(payload.get("managed_universe_version", "")).strip()
    venue = str(payload.get("venue", "")).strip()
    quote_currency = str(payload.get("quote_currency", "")).strip()
    if not version or not venue or not quote_currency:
        raise ValueError(f"Invalid managed universe metadata: {universe_path}")
    return ManagedUniverse(
        version=version,
        venue=venue,
        quote_currency=quote_currency,
        instruments_by_symbol=instruments,
    )


@dataclass(frozen=True)
class BridgeConfig:
    bridge_id: str = os.getenv("SNIPER_BRIDGE_ID", "sniper-nautilus-paper")
    portfolio_id: str = os.getenv(
        "SNIPER_BRIDGE_PORTFOLIO_ID",
        "sniper-paper-binance-spot-main",
    )
    environment: str = os.getenv("SNIPER_BRIDGE_ENVIRONMENT", "paper")
    redis_url: str = os.getenv("SNIPER_BRIDGE_REDIS_URL", "redis://localhost:6379/0")
    target_stream_key: str = TARGET_STREAM_KEY
    status_stream_key: str = STATUS_STREAM_KEY
    max_signal_age_secs: int = int(os.getenv("SNIPER_BRIDGE_MAX_SIGNAL_AGE_SECS", "86400"))
    status_timeout_secs: float = float(os.getenv("SNIPER_BRIDGE_STATUS_TIMEOUT_SECS", "30.0"))
    poll_block_ms: int = int(os.getenv("SNIPER_BRIDGE_POLL_BLOCK_MS", "5000"))
    poll_interval_secs: float = float(os.getenv("SNIPER_BRIDGE_POLL_INTERVAL_SECS", "1.0"))
    daemon_interval_secs: float = float(os.getenv("SNIPER_BRIDGE_DAEMON_INTERVAL_SECS", "300.0"))
    daemon_lock_ttl_secs: int = int(os.getenv("SNIPER_BRIDGE_DAEMON_LOCK_TTL_SECS", "120"))
    heartbeat_interval_secs: float = float(os.getenv("SNIPER_BRIDGE_HEARTBEAT_INTERVAL_SECS", "15.0"))
    rebalance_band_bps: int = int(os.getenv("SNIPER_BRIDGE_REBALANCE_BAND_BPS", "25"))
    min_order_notional_usd: float = float(
        os.getenv("SNIPER_BRIDGE_MIN_ORDER_NOTIONAL_USD", "10.0"),
    )
    default_max_gross_weight: float = float(
        os.getenv("SNIPER_BRIDGE_MAX_GROSS_WEIGHT", "0.98"),
    )
    managed_universe_path: Path = Path(
        os.getenv(
            "SNIPER_BRIDGE_INSTRUMENT_MAP",
            str(Path(__file__).with_name("instrument_map.json")),
        ),
    )
    phase4_snapshot_path: Path = Path(
        os.getenv(
            "SNIPER_BRIDGE_PHASE4_SNAPSHOT",
            str(_repo_root() / "data" / "models" / "phase4" / "phase4_execution_snapshot.parquet"),
        ),
    )
    phase4_capital_reference_notional: float = float(
        os.getenv("SNIPER_BRIDGE_PHASE4_CAPITAL_REFERENCE", "200000.0"),
    )
    phase4_snapshot_max_asof_age_secs: int = int(
        os.getenv("SNIPER_BRIDGE_PHASE4_MAX_ASOF_AGE_SECS", str(72 * 3600)),
    )
    paper_starting_balances: tuple[str, ...] = (
        os.getenv("SNIPER_BRIDGE_PAPER_BALANCE", "200000 USDT"),
    )

    def cursor_key(self) -> str:
        return stream_cursor_key(self.bridge_id, self.environment)

    def daemon_lock_key(self) -> str:
        return daemon_lock_key(self.portfolio_id, self.environment)

    def heartbeat_key(self) -> str:
        return heartbeat_key(self.portfolio_id, self.environment)

    def run_summary_key(self) -> str:
        return run_summary_key(self.portfolio_id, self.environment)

    def managed_universe(self) -> ManagedUniverse:
        return load_managed_universe(self.managed_universe_path)
