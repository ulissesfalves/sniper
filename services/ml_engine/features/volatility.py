# =============================================================================
# DESTINO: services/ml_engine/features/volatility.py
# Volatilidade EWMA: base para os multiplicadores k_tp e k_sl do
# Triple-Barrier Method. Também alimenta o market impact √(Q/V).
# =============================================================================
from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)


def compute_sigma_ewma(
    returns:    pd.Series,
    span:       int   = 20,
    min_periods: int  = 5,
) -> pd.Series:
    """
    Volatilidade EWMA dos retornos diários.
    Base dos multiplicadores k_tp e k_sl no Triple-Barrier.

    sigma_ewma[t] = EWMA(returns²)[t]^0.5
    Usando span=20 → halflife ≈ 10 dias.

    Args:
        returns:     pd.Series de retornos diários (log-retornos ou simples).
        span:        Span do EWMA. Default 20 (≈ 1 mês).
        min_periods: Mínimo de obs para calcular. Default 5.

    Returns:
        pd.Series de desvio padrão EWMA (mesmo índice que returns).
    """
    sigma = (
        returns
        .ewm(span=span, min_periods=min_periods)
        .std()
        .rename("sigma_ewma")
    )
    log.debug("sigma_ewma.computed",
              span=span,
              mean_sigma=round(float(sigma.dropna().mean()), 4),
              max_sigma=round(float(sigma.dropna().max()), 4))
    return sigma


def compute_sigma_intraday_parkinson(
    high:  pd.Series,
    low:   pd.Series,
    ewm_span: int = 20,
) -> pd.Series:
    """
    Volatilidade intradiária pelo estimador de Parkinson (High-Low).
    Usada no market impact √(Q/V) — Parte 5 do SNIPER v10.10.

    sigma_intraday = EWMA((H - L) / Mid)
    Proxy para σ intraday sem dados tick. Mais eficiente que close-to-close
    para capturar a volatilidade real durante a sessão.

    Args:
        high, low: pd.Series de High e Low diários ou de barra.
        ewm_span:  Span do suavizamento EWMA. Default 20.

    Returns:
        pd.Series de volatilidade intradiária suavizada.
    """
    mid      = (high + low) / 2.0
    hl_range = (high - low) / mid.replace(0, np.nan)

    sigma_intr = (
        hl_range
        .ewm(span=ewm_span, min_periods=5)
        .mean()
        .rename("sigma_intraday")
    )
    log.debug("sigma_intraday.computed",
              ewm_span=ewm_span,
              mean=round(float(sigma_intr.dropna().mean()), 5))
    return sigma_intr


def compute_realized_vol(
    returns:    pd.Series,
    window:     int = 30,
    annualize:  bool = False,
) -> pd.Series:
    """
    Volatilidade realizada (rolling std) — feature do HMM e meta-modelo.

    Args:
        returns:   pd.Series de retornos.
        window:    Janela rolling. Default 30 dias.
        annualize: Se True, multiplica por sqrt(252). Default False.

    Returns:
        pd.Series de volatilidade realizada.
    """
    vol = returns.rolling(window, min_periods=max(5, window // 4)).std()
    if annualize:
        vol = vol * np.sqrt(252)
    return vol.rename(f"realized_vol_{window}d")
