from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from services.nautilus_bridge.contract import SignalPayload
from services.nautilus_bridge.status import STATUS_NOOP_BAND
from services.nautilus_bridge.status import STATUS_SUBMITTED


def _to_decimal(value: object) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class ReadinessReport:
    is_ready: bool
    missing: tuple[str, ...]


@dataclass(frozen=True)
class RebalanceIntent:
    instrument_id: str
    target_weight: Decimal
    current_weight: Decimal
    target_notional: Decimal
    current_notional: Decimal
    delta_notional: Decimal
    price: Decimal
    order_side: str
    close_only: bool


@dataclass(frozen=True)
class ReconcileSkip:
    instrument_id: str
    reason: str
    delta_weight: Decimal
    delta_notional: Decimal


@dataclass(frozen=True)
class ReconcileResult:
    status: str
    nav: Decimal
    intents: tuple[RebalanceIntent, ...]
    skips: tuple[ReconcileSkip, ...]


def build_readiness_report(
    payload: SignalPayload,
    *,
    loaded_instruments: set[str],
    portfolio_snapshot_loaded: bool,
    quantities: Mapping[str, object],
    prices: Mapping[str, object | None],
    executor_healthy: bool,
) -> ReadinessReport:
    missing: list[str] = []
    if not executor_healthy:
        missing.append("executor_unhealthy")
    if not portfolio_snapshot_loaded:
        missing.append("portfolio_snapshot_missing")
    actionable_instruments = sorted(
        target.instrument_id
        for target in payload.targets
        if target.target_weight > Decimal("0")
        or _to_decimal(quantities.get(target.instrument_id, "0")) != Decimal("0")
    )
    missing_instruments = sorted(
        instrument_id
        for instrument_id in actionable_instruments
        if instrument_id not in loaded_instruments
    )
    if missing_instruments:
        missing.append("instruments_missing:" + ",".join(missing_instruments))
    missing_prices = sorted(
        instrument_id
        for instrument_id in actionable_instruments
        if prices.get(instrument_id) in (None, "")
    )
    if missing_prices:
        missing.append("prices_missing:" + ",".join(missing_prices))
    return ReadinessReport(is_ready=not missing, missing=tuple(missing))


def reconcile_target_weights(
    payload: SignalPayload,
    *,
    nav: object,
    quantities: Mapping[str, object],
    prices: Mapping[str, object],
    default_rebalance_band_bps: int,
    default_min_order_notional_usd: object,
) -> ReconcileResult:
    nav_decimal = _to_decimal(nav)
    if nav_decimal <= Decimal("0"):
        raise ValueError("nav must be > 0")
    band_bps = payload.risk_envelope.rebalance_band_bps or default_rebalance_band_bps
    band = Decimal(band_bps) / Decimal("10000")
    min_order_notional = (
        payload.risk_envelope.min_order_notional_usd
        if payload.risk_envelope.min_order_notional_usd is not None
        else _to_decimal(default_min_order_notional_usd)
    )
    intents: list[RebalanceIntent] = []
    skips: list[ReconcileSkip] = []
    for target in payload.sorted_targets():
        quantity = _to_decimal(quantities.get(target.instrument_id, "0"))
        if target.target_weight == Decimal("0"):
            if quantity == Decimal("0"):
                skips.append(
                    ReconcileSkip(
                        instrument_id=target.instrument_id,
                        reason="already_flat",
                        delta_weight=Decimal("0"),
                        delta_notional=Decimal("0"),
                    ),
                )
                continue
        price = _to_decimal(prices[target.instrument_id])
        current_notional = quantity * price
        current_weight = current_notional / nav_decimal
        target_notional = target.target_weight * nav_decimal
        delta_notional = target_notional - current_notional
        delta_weight = target.target_weight - current_weight
        if target.target_weight == Decimal("0"):
            if abs(current_notional) < min_order_notional:
                skips.append(
                    ReconcileSkip(
                        instrument_id=target.instrument_id,
                        reason="dust_close",
                        delta_weight=delta_weight,
                        delta_notional=delta_notional,
                    ),
                )
                continue
        else:
            if abs(delta_weight) < band:
                skips.append(
                    ReconcileSkip(
                        instrument_id=target.instrument_id,
                        reason="band",
                        delta_weight=delta_weight,
                        delta_notional=delta_notional,
                    ),
                )
                continue
            if abs(delta_notional) < min_order_notional:
                skips.append(
                    ReconcileSkip(
                        instrument_id=target.instrument_id,
                        reason="min_notional",
                        delta_weight=delta_weight,
                        delta_notional=delta_notional,
                    ),
                )
                continue
        intents.append(
            RebalanceIntent(
                instrument_id=target.instrument_id,
                target_weight=target.target_weight,
                current_weight=current_weight,
                target_notional=target_notional,
                current_notional=current_notional,
                delta_notional=delta_notional,
                price=price,
                order_side="BUY" if delta_notional > 0 else "SELL",
                close_only=target.target_weight == Decimal("0"),
            ),
        )
    return ReconcileResult(
        status=STATUS_SUBMITTED if intents else STATUS_NOOP_BAND,
        nav=nav_decimal,
        intents=tuple(intents),
        skips=tuple(skips),
    )
