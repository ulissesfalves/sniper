# DESTINO: services/execution_engine/binance/executor.py
# v3: aioredis → redis.asyncio (compatível com Python 3.11)
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import aiohttp
import structlog
from decouple import config

from risk.pre_trade_check import PreTradeResult

log = structlog.get_logger(__name__)

BINANCE_SPOT_LIVE    = "https://api.binance.com"
BINANCE_SPOT_TESTNET = "https://testnet.binance.vision"
LIMIT_ORDER_TIMEOUT  = 30
SLIP_LIMIT_THRESHOLD = 0.005
MIN_NOTIONAL_USDT    = 10.0
MAX_RETRIES          = 3


class OrderSide(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"


@dataclass
class ExecutionResult:
    symbol:        str
    side:          str
    order_id:      str
    status:        str
    qty_ordered:   float
    qty_filled:    float
    price_avg:     float
    notional_usdt: float
    slippage_est:  float
    slippage_real: float
    pnl_real:      float
    timestamp:     str
    is_testnet:    bool
    order_type:    str
    error:         Optional[str] = None


@dataclass
class MarketSnapshot:
    symbol:         str
    mid_price:      float
    volume_24h:     float
    volume_bar:     float
    sigma_intraday: float
    timestamp:      str


class BinanceExecutor:
    def __init__(self) -> None:
        self.api_key    = config("BINANCE_API_KEY",    default="")
        self.api_secret = config("BINANCE_API_SECRET", default="")
        self.testnet    = config("BINANCE_TESTNET",    default="true").lower() == "true"
        self.base_url   = BINANCE_SPOT_TESTNET if self.testnet else BINANCE_SPOT_LIVE
        self.eta        = float(config("MARKET_IMPACT_ETA", default="0.10"))
        self._redis     = None

        if not self.testnet:
            log.critical("executor.LIVE_MODE",
                         msg="BINANCE_TESTNET=false — execuções REAIS ativas.")

    async def connect(self) -> None:
        try:
            # CORREÇÃO: redis.asyncio em vez de aioredis
            from redis.asyncio import from_url
            self._redis = await from_url(
                config("REDIS_URL", default="redis://redis:6379"),
                decode_responses=True,
            )
            log.info("executor.redis_connected", testnet=self.testnet)
        except Exception as e:
            log.warning("executor.redis_unavailable", error=str(e))

    def _sign_request(self, params: dict) -> dict:
        import hashlib, hmac, time
        params["timestamp"] = int(time.time() * 1000)
        query     = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        signature = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = signature
        return params

    async def get_market_snapshot(
        self, session: aiohttp.ClientSession, symbol: str
    ) -> MarketSnapshot:
        headers = {"X-MBX-APIKEY": self.api_key}
        async with session.get(f"{self.base_url}/api/v3/ticker/24hr",
                               params={"symbol": symbol},
                               headers=headers) as r:
            t = await r.json()

        mid_price = (float(t.get("bidPrice", 0)) + float(t.get("askPrice", 0))) / 2.0
        vol_24h   = float(t.get("quoteVolume", 0))

        async with session.get(f"{self.base_url}/api/v3/klines",
                               params={"symbol": symbol, "interval": "4h", "limit": 2},
                               headers=headers) as r:
            klines = await r.json()

        vol_bar        = float(klines[-1][7]) if klines else vol_24h / 6
        h              = float(klines[-1][2]) if klines else mid_price * 1.02
        l              = float(klines[-1][3]) if klines else mid_price * 0.98
        mid            = (h + l) / 2.0
        sigma_intraday = (h - l) / max(mid, 1e-10)

        return MarketSnapshot(symbol=symbol, mid_price=mid_price,
                              volume_24h=vol_24h, volume_bar=vol_bar,
                              sigma_intraday=sigma_intraday,
                              timestamp=datetime.now(timezone.utc).isoformat())

    def estimate_slippage(self, order_size_usdt: float,
                          snapshot: MarketSnapshot) -> float:
        participation = order_size_usdt / max(snapshot.volume_bar, 1e-10)
        return float(min(self.eta * snapshot.sigma_intraday * math.sqrt(participation), 0.50))

    async def _send_market_order(self, session, symbol, side, qty) -> dict:
        headers = {"X-MBX-APIKEY": self.api_key}
        params  = self._sign_request({"symbol": symbol, "side": side.value,
                                      "type": "MARKET", "quantity": f"{qty:.8f}"})
        async with session.post(f"{self.base_url}/api/v3/order",
                                params=params, headers=headers) as r:
            return await r.json()

    async def _send_limit_order(self, session, symbol, side, qty, price) -> dict:
        headers = {"X-MBX-APIKEY": self.api_key}
        params  = self._sign_request({"symbol": symbol, "side": side.value,
                                      "type": "LIMIT", "timeInForce": "GTC",
                                      "quantity": f"{qty:.8f}",
                                      "price": f"{price:.8f}"})
        async with session.post(f"{self.base_url}/api/v3/order",
                                params=params, headers=headers) as r:
            return await r.json()

    async def _wait_order_fill(self, session, symbol, order_id,
                                timeout=LIMIT_ORDER_TIMEOUT) -> dict:
        headers  = {"X-MBX-APIKEY": self.api_key}
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            params = self._sign_request({"symbol": symbol, "orderId": order_id})
            async with session.get(f"{self.base_url}/api/v3/order",
                                   params=params, headers=headers) as r:
                data = await r.json()
            if data.get("status") == "FILLED":
                return data
            await asyncio.sleep(2.0)
        cancel = self._sign_request({"symbol": symbol, "orderId": order_id})
        async with session.delete(f"{self.base_url}/api/v3/order",
                                  params=cancel, headers=headers) as r:
            return {**(await r.json()), "status": "CANCELED"}

    async def execute(self, symbol: str, side: OrderSide,
                      order_size_usdt: float,
                      pre_trade: PreTradeResult) -> ExecutionResult:
        ts = datetime.now(timezone.utc).isoformat()

        if not pre_trade.approved or order_size_usdt < MIN_NOTIONAL_USDT:
            reason = pre_trade.reason if not pre_trade.approved else \
                     f"Notional {order_size_usdt:.2f} < {MIN_NOTIONAL_USDT}"
            return ExecutionResult(symbol=symbol, side=side.value, order_id="",
                                   status="FAILED", qty_ordered=0, qty_filled=0,
                                   price_avg=0, notional_usdt=0, slippage_est=0,
                                   slippage_real=0, pnl_real=0, timestamp=ts,
                                   is_testnet=self.testnet, order_type="NONE",
                                   error=reason)

        async with aiohttp.ClientSession() as session:
            snapshot = await self.get_market_snapshot(session, symbol)
            slip_est = self.estimate_slippage(order_size_usdt, snapshot)
            mid      = snapshot.mid_price
            qty      = math.floor(
                order_size_usdt / max(mid * (1.0 + slip_est), 1e-10) * 1e6
            ) / 1e6

            raw: dict = {}
            error_msg = ""
            order_type_used = OrderType.MARKET

            for attempt in range(MAX_RETRIES):
                try:
                    if slip_est >= SLIP_LIMIT_THRESHOLD:
                        lp  = mid * (0.997 if side == OrderSide.BUY else 1.003)
                        raw = await self._send_limit_order(session, symbol, side, qty, lp)
                        order_type_used = OrderType.LIMIT
                        if raw.get("status") not in ("FILLED", "PARTIALLY_FILLED"):
                            raw = await self._wait_order_fill(
                                session, symbol, str(raw.get("orderId", "")))
                    else:
                        raw             = await self._send_market_order(session, symbol, side, qty)
                        order_type_used = OrderType.MARKET
                    if raw.get("status") in ("FILLED", "PARTIALLY_FILLED", "CANCELED"):
                        break
                except aiohttp.ClientError as e:
                    error_msg = str(e)
                    await asyncio.sleep(2 ** attempt)

            qty_filled = float(raw.get("executedQty", 0))
            fills      = raw.get("fills", [])
            price_avg  = (
                sum(float(f["price"]) * float(f["qty"]) for f in fills)
                / max(qty_filled, 1e-10)
            ) if fills else float(raw.get("price", mid))
            notional   = price_avg * qty_filled
            slip_real  = abs(price_avg - mid) / max(mid, 1e-10)

            result = ExecutionResult(
                symbol=symbol, side=side.value,
                order_id=str(raw.get("orderId", "")),
                status=raw.get("status", "FAILED"),
                qty_ordered=qty, qty_filled=qty_filled,
                price_avg=round(price_avg, 8),
                notional_usdt=round(notional, 4),
                slippage_est=round(slip_est, 6),
                slippage_real=round(slip_real, 6),
                pnl_real=0.0, timestamp=ts,
                is_testnet=self.testnet,
                order_type=order_type_used.value,
                error=error_msg or None,
            )
            await self._publish_execution(result)
            return result

    async def _publish_execution(self, result: ExecutionResult) -> None:
        if self._redis:
            try:
                import json
                await self._redis.publish("sniper:executions",
                                          json.dumps(asdict(result)))
            except Exception as e:
                log.warning("executor.redis_publish_fail", error=str(e))

    async def cancel_all_open_orders(self, symbol: str) -> int:
        headers = {"X-MBX-APIKEY": self.api_key}
        async with aiohttp.ClientSession() as session:
            params = self._sign_request({"symbol": symbol})
            async with session.delete(f"{self.base_url}/api/v3/openOrders",
                                      params=params, headers=headers) as r:
                data = await r.json()
        return len(data) if isinstance(data, list) else 0
