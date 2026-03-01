# DESTINO: services/execution_engine/main.py
# v2: aioredis → redis.asyncio (compatível com Python 3.11)
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone

import structlog
from decouple import config

from binance.executor import BinanceExecutor, OrderSide
from risk.pre_trade_check import run_pre_trade_check

log = structlog.get_logger(__name__)

REDIS_SIGNAL_CHANNEL = "sniper:signals"
REDIS_URL            = config("REDIS_URL", default="redis://redis:6379")
CAPITAL_INITIAL      = float(config("CAPITAL_INITIAL_USDT", default="200000"))


class SignalConsumer:
    def __init__(self) -> None:
        self.executor     = BinanceExecutor()
        self.capital_hwm  = CAPITAL_INITIAL
        self.capital_curr = CAPITAL_INITIAL
        self._redis       = None

    async def start(self) -> None:
        # CORREÇÃO: redis.asyncio em vez de aioredis
        from redis.asyncio import from_url
        self._redis = await from_url(REDIS_URL, decode_responses=True)
        await self.executor.connect()

        log.info("signal_consumer.start",
                 channel=REDIS_SIGNAL_CHANNEL,
                 testnet=self.executor.testnet)

        pubsub = self._redis.pubsub()
        await pubsub.subscribe(REDIS_SIGNAL_CHANNEL)

        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                signal = json.loads(message["data"])
                await self._process_signal(signal)
            except Exception as e:
                log.error("signal_consumer.error", error=str(e))

    async def _process_signal(self, signal: dict) -> None:
        symbol       = signal.get("symbol", "")
        pos_usdt     = float(signal.get("position_usdt", 0))
        side_str     = signal.get("side", "BUY")
        p_cal        = float(signal.get("p_calibrated", 0.5))

        log.info("signal.received",
                 symbol=symbol, pos_usdt=pos_usdt,
                 p_cal=round(p_cal, 4), side=side_str)

        hmm_state    = signal.get("hmm_state",    {"hmm_is_bull": True, "hmm_prob_bull": 0.5})
        cs_state     = signal.get("cs_state",     {"status": "CLEAR"})
        drift_state  = signal.get("drift_state",  {"severity": "NONE"})
        cvar_state   = signal.get("cvar_state",   {"cvar_stress_rho1": 0.0})
        global_drift = signal.get("global_drift", {"global_alert": False})
        portfolio_st = signal.get("portfolio_positions", {})

        pre = await run_pre_trade_check(
            symbol=symbol,
            position_usdt=pos_usdt,
            capital_total=self.capital_curr,
            capital_hwm=self.capital_hwm,
            portfolio_state=portfolio_st,
            hmm_state=hmm_state,
            cs_state=cs_state,
            drift_state=drift_state,
            cvar_state=cvar_state,
            global_drift=global_drift,
        )

        if not pre.approved:
            log.warning("signal.rejected",
                        symbol=symbol, reason=pre.reason,
                        alarm_level=pre.alarm_level)
            await self._publish_rejection(symbol, pre.reason, pre.alarm_level)
            return

        side   = OrderSide.BUY if side_str == "BUY" else OrderSide.SELL
        result = await self.executor.execute(
            symbol=symbol,
            side=side,
            order_size_usdt=pre.position_usdt,
            pre_trade=pre,
        )

        log.info("signal.executed",
                 symbol=symbol,
                 status=result.status,
                 filled_usdt=round(result.notional_usdt, 2),
                 slip_real_pct=round(result.slippage_real * 100, 3))

    async def _publish_rejection(self, symbol: str, reason: str, level: int) -> None:
        if self._redis:
            payload = json.dumps({
                "type":        "rejection",
                "symbol":      symbol,
                "reason":      reason,
                "alarm_level": level,
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            })
            await self._redis.publish("sniper:executions", payload)


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ]
    )
    consumer = SignalConsumer()
    try:
        await consumer.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("execution_engine.shutdown")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
