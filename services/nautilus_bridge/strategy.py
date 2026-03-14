from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import redis.asyncio as redis_async

from services.nautilus_bridge.config import BridgeConfig
from services.nautilus_bridge.config import load_managed_universe
from services.nautilus_bridge.consumer import RedisSignalConsumer
from services.nautilus_bridge.consumer import SignalApplyResult
from services.nautilus_bridge.contract import StoredSignal
from services.nautilus_bridge.reconciler import build_readiness_report
from services.nautilus_bridge.reconciler import reconcile_target_weights
from services.nautilus_bridge.state import BridgeStateStore
from services.nautilus_bridge.status import RedisStatusPublisher
from services.nautilus_bridge.status import STATUS_FAILED
from services.nautilus_bridge.status import STATUS_FILLED

try:
    from nautilus_trader.common.enums import LogColor
    from nautilus_trader.core.nautilus_pyo3 import OrderFilled
    from nautilus_trader.core.nautilus_pyo3 import OrderRejected
    from nautilus_trader.model.enums import OrderSide
    from nautilus_trader.model.enums import TimeInForce
    from nautilus_trader.model.identifiers import InstrumentId
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.trading import Strategy
    from nautilus_trader.trading.config import StrategyConfig

    NAUTILUS_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - used only in the isolated 3.12 runtime
    NAUTILUS_IMPORT_ERROR = exc


if NAUTILUS_IMPORT_ERROR is not None:  # pragma: no cover - import guard for 3.11
    @dataclass(frozen=True)
    class NautilusBridgeStrategyConfig:
        bridge_id: str
        portfolio_id: str
        environment: str
        redis_url: str
        managed_universe_path: str
        poll_block_ms: int = 5000
        rebalance_band_bps: int = 25
        min_order_notional_usd: float = 10.0


    class NautilusBridgeStrategy:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("nautilus_trader is required for strategy.py") from NAUTILUS_IMPORT_ERROR


else:
    class NautilusBridgeStrategyConfig(StrategyConfig, frozen=True):
        bridge_id: str
        portfolio_id: str
        environment: str
        redis_url: str
        managed_universe_path: str
        poll_block_ms: int = 5000
        rebalance_band_bps: int = 25
        min_order_notional_usd: float = 10.0


    class NautilusBridgeStrategy(Strategy):
        def __init__(self, config: NautilusBridgeStrategyConfig) -> None:
            super().__init__(config=config)
            self._bridge_config: BridgeConfig | None = None
            self._managed_universe = None
            self._redis = None
            self._state_store: BridgeStateStore | None = None
            self._status_publisher: RedisStatusPublisher | None = None
            self._consumer: RedisSignalConsumer | None = None
            self._bridge_task: asyncio.Task[None] | None = None
            self._submitted_orders: dict[str, StoredSignal] = {}

        def on_start(self) -> None:
            self._managed_universe = load_managed_universe(Path(self.config.managed_universe_path))
            self._bridge_config = BridgeConfig(
                bridge_id=self.config.bridge_id,
                portfolio_id=self.config.portfolio_id,
                environment=self.config.environment,
                redis_url=self.config.redis_url,
                poll_block_ms=self.config.poll_block_ms,
                rebalance_band_bps=self.config.rebalance_band_bps,
                min_order_notional_usd=self.config.min_order_notional_usd,
                managed_universe_path=Path(self.config.managed_universe_path),
            )
            self._redis = redis_async.from_url(self.config.redis_url)
            self._state_store = BridgeStateStore(redis=self._redis, config=self._bridge_config)
            self._status_publisher = RedisStatusPublisher(redis=self._redis, config=self._bridge_config)
            self._consumer = RedisSignalConsumer(
                redis=self._redis,
                config=self._bridge_config,
                managed_universe=self._managed_universe,
                state_store=self._state_store,
                status_publisher=self._status_publisher,
                accepted_handler=self._handle_signal,
            )
            for instrument_id in self._managed_universe.instrument_ids:
                self.subscribe_quote_ticks(InstrumentId.from_str(instrument_id))
            self._bridge_task = asyncio.get_running_loop().create_task(self._bridge_loop())
            self.log.info("Nautilus bridge strategy started", color=LogColor.GREEN)

        def on_stop(self) -> None:
            if self._bridge_task is not None:
                self._bridge_task.cancel()
            if self._managed_universe is not None:
                for instrument_id in self._managed_universe.instrument_ids:
                    self.unsubscribe_quote_ticks(InstrumentId.from_str(instrument_id))
            if self._redis is not None:
                asyncio.get_running_loop().create_task(self._redis.aclose())

        def on_order_filled(self, event: OrderFilled) -> None:
            signal = self._submitted_orders.get(str(event.client_order_id))
            if signal is None or self._status_publisher is None:
                return
            details = {
                "instrument_id": str(event.instrument_id),
                "client_order_id": str(event.client_order_id),
                "last_qty": str(event.last_qty.as_decimal()),
                "last_px": str(event.last_px.as_decimal()),
            }
            asyncio.get_running_loop().create_task(
                self._status_publisher.publish_for_signal(
                    status=STATUS_FILLED,
                    signal=signal,
                    details=details,
                ),
            )
            order = self.cache.order(event.client_order_id) if hasattr(self.cache, "order") else None
            if order is None or order.is_closed:
                self._submitted_orders.pop(str(event.client_order_id), None)

        def on_order_rejected(self, event: OrderRejected) -> None:
            signal = self._submitted_orders.pop(str(event.client_order_id), None)
            if signal is None or self._status_publisher is None:
                return
            asyncio.get_running_loop().create_task(
                self._status_publisher.publish_for_signal(
                    status=STATUS_FAILED,
                    signal=signal,
                    details={
                        "instrument_id": str(event.instrument_id),
                        "client_order_id": str(event.client_order_id),
                        "reason": event.reason,
                    },
                ),
            )

        async def _bridge_loop(self) -> None:
            assert self._consumer is not None
            while True:
                try:
                    await self._consumer.process_deferred_target(
                        self.config.portfolio_id,
                        self.config.environment,
                    )
                    await self._consumer.consume_once(block_ms=self.config.poll_block_ms)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.log.error(f"Bridge loop failure: {exc}")
                    await asyncio.sleep(1.0)

        async def _handle_signal(self, signal: StoredSignal) -> SignalApplyResult:
            readiness = self._build_readiness(signal)
            if not readiness.is_ready:
                return SignalApplyResult.deferred({"missing": list(readiness.missing)})
            try:
                prices = self._current_prices()
                quantities = self._current_quantities()
                nav = self._current_nav(prices)
                reconcile_result = reconcile_target_weights(
                    signal.payload,
                    nav=nav,
                    quantities=quantities,
                    prices=prices,
                    default_rebalance_band_bps=self.config.rebalance_band_bps,
                    default_min_order_notional_usd=self.config.min_order_notional_usd,
                )
                if not reconcile_result.intents:
                    return SignalApplyResult.noop_band(
                        {"skips": [skip.reason for skip in reconcile_result.skips]},
                    )
                submitted_orders: list[dict[str, str]] = []
                for intent in reconcile_result.intents:
                    client_order_id = self._submit_intent(intent)
                    self._submitted_orders[client_order_id] = signal
                    submitted_orders.append(
                        {
                            "client_order_id": client_order_id,
                            "instrument_id": intent.instrument_id,
                            "delta_notional": str(intent.delta_notional),
                            "order_side": intent.order_side,
                        },
                    )
                return SignalApplyResult.submitted(
                    {
                        "nav": str(nav),
                        "order_count": len(submitted_orders),
                        "orders": submitted_orders,
                    },
                )
            except Exception as exc:
                return SignalApplyResult.failed({"reason": str(exc)})

        def _build_readiness(self, signal: StoredSignal):
            prices = self._current_prices()
            quantities = self._current_quantities()
            loaded_instruments = {
                instrument_id
                for instrument_id in self._managed_universe.instrument_ids
                if self.cache.instrument(InstrumentId.from_str(instrument_id)) is not None
            }
            account = self._account()
            return build_readiness_report(
                signal.payload,
                loaded_instruments=loaded_instruments,
                portfolio_snapshot_loaded=account is not None,
                quantities=quantities,
                prices=prices,
                executor_healthy=account is not None,
            )

        def _current_prices(self) -> dict[str, Decimal]:
            prices: dict[str, Decimal] = {}
            for instrument_id in self._managed_universe.instrument_ids:
                quote = self._latest_quote_tick(InstrumentId.from_str(instrument_id))
                if quote is None:
                    continue
                midpoint = self._quote_mid_price(quote)
                if midpoint is None:
                    continue
                prices[instrument_id] = midpoint
            return prices

        def _latest_quote_tick(self, instrument_id: InstrumentId):
            quote_tick_getter = getattr(self.cache, "quote_tick", None)
            if callable(quote_tick_getter):
                return quote_tick_getter(instrument_id)
            quote_getter = getattr(self.cache, "quote", None)
            if callable(quote_getter):
                return quote_getter(instrument_id)
            return None

        def _quote_mid_price(self, quote) -> Decimal | None:
            bid_price = getattr(quote, "bid_price", None)
            ask_price = getattr(quote, "ask_price", None)
            bid_decimal = self._price_to_decimal(bid_price)
            ask_decimal = self._price_to_decimal(ask_price)
            if bid_decimal is not None and ask_decimal is not None:
                return (bid_decimal + ask_decimal) / Decimal("2")
            if bid_decimal is not None:
                return bid_decimal
            if ask_decimal is not None:
                return ask_decimal
            return None

        def _price_to_decimal(self, price) -> Decimal | None:
            if price is None:
                return None
            as_decimal = getattr(price, "as_decimal", None)
            if callable(as_decimal):
                return Decimal(str(as_decimal()))
            return Decimal(str(price))

        def _current_quantities(self) -> dict[str, Decimal]:
            quantities: dict[str, Decimal] = {}
            for instrument_id in self._managed_universe.instrument_ids:
                net_position = self.portfolio.net_position(InstrumentId.from_str(instrument_id))
                quantities[instrument_id] = Decimal(str(net_position or 0))
            return quantities

        def _current_nav(self, prices: dict[str, Decimal]) -> Decimal:
            account = self._account()
            if account is None:
                raise RuntimeError("Portfolio account snapshot is not available")
            quote_currency = None
            for instrument_id in self._managed_universe.instrument_ids:
                instrument = self.cache.instrument(InstrumentId.from_str(instrument_id))
                if instrument is not None:
                    quote_currency = instrument.quote_currency
                    break
            if quote_currency is None:
                raise RuntimeError("Managed instruments are not loaded")
            cash_balance = account.balance_total(quote_currency)
            nav = cash_balance.as_decimal() if cash_balance is not None else Decimal("0")
            for instrument_id, price in prices.items():
                quantity = Decimal(str(self.portfolio.net_position(InstrumentId.from_str(instrument_id)) or 0))
                nav += quantity * price
            return nav

        def _submit_intent(self, intent) -> str:
            instrument_id = InstrumentId.from_str(intent.instrument_id)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                raise RuntimeError(f"Instrument not loaded: {intent.instrument_id}")
            quantity_decimal = abs(intent.delta_notional) / intent.price
            quantity = instrument.make_qty(float(quantity_decimal))
            if quantity.as_decimal() <= Decimal("0"):
                raise RuntimeError(f"Rounded quantity is zero for {intent.instrument_id}")
            order = self.order_factory.market(
                instrument_id=instrument_id,
                order_side=OrderSide.BUY if intent.order_side == "BUY" else OrderSide.SELL,
                quantity=quantity,
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.log.info(
                f"Submitted rebalance order {intent.order_side} {quantity.as_decimal()} {intent.instrument_id}",
                color=LogColor.BLUE,
            )
            return str(order.client_order_id)

        def _account(self):
            return self.portfolio.account(venue=Venue(self._managed_universe.venue))
