from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

DEFAULT_ETA = 0.10
MAX_SLIPPAGE_CAP = 0.50
MIN_ETA = 0.05


def compute_intraday_vol_parkinson(
    high: pd.Series | np.ndarray,
    low: pd.Series | np.ndarray,
    ewm_span: int = 20,
) -> pd.Series:
    """Volatilidade intradiária pelo estimador de Parkinson preservando o índice."""
    idx = high.index if isinstance(high, pd.Series) else None

    h = np.asarray(high, dtype=np.float64)
    l = np.asarray(low, dtype=np.float64)
    mid = (h + l) / 2.0
    hl_range = np.where(mid > 0, (h - l) / mid, 0.0)

    return pd.Series(hl_range, index=idx).ewm(span=ewm_span, min_periods=5).mean()


def compute_sqrt_market_impact(
    order_size_usdt: float,
    volume_usdt: float,
    sigma_intraday: float,
    eta: float = DEFAULT_ETA,
) -> float:
    """Lei da raiz quadrada do impacto de mercado: ΔP = η × σ × √(Q/V)."""
    if eta < MIN_ETA:
        log.warning(
            "market_impact.eta_too_low",
            eta=eta,
            min_eta=MIN_ETA,
            msg="Subestima crash environments. Usando MIN_ETA.",
        )
        eta = MIN_ETA

    volume_safe = max(float(volume_usdt), 1e-10)
    participation = max(float(order_size_usdt), 0.0) / volume_safe
    sigma_safe = max(float(sigma_intraday), 0.0)
    slippage_raw = eta * sigma_safe * np.sqrt(participation)
    slippage = float(min(slippage_raw, MAX_SLIPPAGE_CAP))

    log.debug(
        "market_impact.computed",
        Q=round(float(order_size_usdt), 2),
        V=round(float(volume_usdt), 2),
        participation_pct=round(participation * 100, 4),
        sigma_intraday=round(sigma_safe, 6),
        slippage_pct=round(slippage * 100, 4),
        eta=eta,
    )
    return slippage


# Compatibilidade retroativa: código legado ainda importa este nome.
def compute_market_impact(
    order_size_usdt: float,
    volume_usdt: float,
    sigma_intraday: float,
    eta: float = DEFAULT_ETA,
) -> float:
    return compute_sqrt_market_impact(
        order_size_usdt=order_size_usdt,
        volume_usdt=volume_usdt,
        sigma_intraday=sigma_intraday,
        eta=eta,
    )


__all__ = [
    "DEFAULT_ETA",
    "MAX_SLIPPAGE_CAP",
    "MIN_ETA",
    "compute_intraday_vol_parkinson",
    "compute_sqrt_market_impact",
    "compute_market_impact",
]
