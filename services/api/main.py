# DESTINO: services/api/main.py
# v2: aioredis → redis.asyncio (compatível com Python 3.11)
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

import structlog
from decouple import config
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware

log = structlog.get_logger(__name__)

REDIS_URL    = config("REDIS_URL", default="redis://redis:6379")
CORS_ORIGINS = config("CORS_ORIGINS",
                       default="http://localhost:3000,http://localhost:80").split(",")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        log.info("ws.connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def broadcast(self, message: str) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections.remove(ws)


manager = ConnectionManager()


async def redis_listener() -> None:
    """Escuta canais Redis e faz broadcast para WebSocket clients."""
    try:
        # CORREÇÃO: redis.asyncio em vez de aioredis
        from redis.asyncio import from_url
        r      = await from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe(
            "sniper:executions", "sniper:alarms", "sniper:regime"
        )
        log.info("api.redis_listener_started")

        async for message in pubsub.listen():
            if message["type"] == "message":
                channel = message["channel"]
                data    = message["data"]
                try:
                    payload = json.dumps({"channel": channel,
                                          "data": json.loads(data)})
                except (json.JSONDecodeError, TypeError):
                    payload = json.dumps({"channel": channel, "data": data})
                await manager.broadcast(payload)

    except Exception as e:
        log.error("api.redis_listener_error", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    asyncio.create_task(redis_listener())
    log.info("api.startup", cors_origins=CORS_ORIGINS)
    yield
    log.info("api.shutdown")


app = FastAPI(title="SNIPER v10.10 API", version="10.10.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.get("/api/portfolio/summary")
async def portfolio_summary() -> dict:
    return {
        "capital_total_usdt": 200_000,
        "capital_hwm_usdt":   200_000,
        "drawdown_pct":       0.0,
        "n_open_positions":   0,
        "pnl_total_pct":      0.0,
        "last_updated":       datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/portfolio/equity_curve")
async def equity_curve() -> dict:
    return {"dates": [], "equity": [], "benchmark": []}


@app.get("/api/portfolio/positions")
async def open_positions() -> dict:
    return {"positions": []}


@app.get("/api/signals/recent")
async def recent_signals(limit: int = 50) -> dict:
    return {"signals": [], "total": 0}


@app.get("/api/signals/performance")
async def signal_performance() -> dict:
    return {"win_rate": 0.0, "avg_pnl_tp": 0.0, "avg_pnl_sl": 0.0,
            "avg_pnl_ts": 0.0, "total_signals": 0}


@app.get("/api/risk/dashboard")
async def risk_dashboard() -> dict:
    return {
        "cvar_stress":      0.0,
        "cvar_limit":       0.15,
        "cvar_ok":          True,
        "drawdown_scalar":  1.0,
        "global_alarm":     "CLEAR",
        "n_assets_bear":    0,
        "n_assets_blocked": 0,
        "last_updated":     datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/risk/alarms")
async def alarm_history(limit: int = 100) -> dict:
    try:
        from pathlib import Path
        alarm_path = Path("/data/logs/alarms.jsonl")
        if not alarm_path.exists():
            return {"alarms": [], "total": 0}
        alarms = []
        with open(alarm_path) as f:
            for line in f.readlines()[-limit:]:
                try:
                    alarms.append(json.loads(line.strip()))
                except Exception:
                    continue
        return {"alarms": list(reversed(alarms)), "total": len(alarms)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/risk/calibration/{symbol}")
async def calibration_data(symbol: str) -> dict:
    return {"symbol": symbol, "ece_raw": None, "ece_cal": None,
            "bins": [], "last_fit": None}


@app.get("/api/regime/{symbol}")
async def regime_state(symbol: str) -> dict:
    return {"symbol": symbol, "available": False}
