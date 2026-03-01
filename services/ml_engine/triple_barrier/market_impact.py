# =============================================================================
# DESTINO: services/ml_engine/triple_barrier/market_impact.py
# Slippage pela Lei da Raiz Quadrada do Impacto de Mercado (v10.10).
# ΔP = η × σ_intraday × √(order_size / volume)
#
# CORREÇÃO v10.10: slippage linear (v10.9) viola Kyle/Almgren-Chriss.
# Liquidez esgota de forma sub-linear: dobrar ordem → slippage × √2, não × 2.
# Em flash crash (volume colapsado), √(Q/V) cresce rapidamente capturando
# o custo real de forçar capital através de funil de liquidez estreito.
# Referência: Almgren-Chriss (2001), Bouchaud et al. (2010), SNIPER v10.10 Parte 5.
# =============================================================================
from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Parâmetro de proporcionalidade (literatura: η ∈ [0.05, 0.15])
DEFAULT_ETA       = 0.10
MAX_SLIPPAGE_CAP  = 0.50   # cap conservador: slippage nunca > 50%
MIN_ETA           = 0.05   # NUNCA usar η < 0.05 (subestima crash environments)


def compute_intraday_vol_parkinson(
    high:     pd.Series | np.ndarray,
    low:      pd.Series | np.ndarray,
    ewm_span: int = 20,
) -> pd.Series:
    """
    Volatilidade intradiária pelo estimador de Parkinson (High-Low range).
    Proxy para σ_intraday sem dados tick.

    σ_intraday = EWMA((H - L) / ((H + L) / 2))

    Mais eficiente que close-to-close para capturar volatilidade real da sessão.
    Usado como σ_intraday na fórmula ΔP = η × σ × √(Q/V).
    """
    h = np.asarray(high,  dtype=np.float64)
    l = np.asarray(low,   dtype=np.float64)
    mid      = (h + l) / 2.0
    hl_range = np.where(mid > 0, (h - l) / mid, 0.0)

    return (
        pd.Series(hl_range)
        .ewm(span=ewm_span, min_periods=5)
        .mean()
    )


def compute_sqrt_market_impact(
    order_size_usdt: float,
    volume_usdt:     float,
    sigma_intraday:  float,
    eta:             float = DEFAULT_ETA,
) -> float:
    """
    Slippage fracionário pela Lei da Raiz Quadrada do Impacto de Mercado.

    Fórmula: ΔP = η × σ_intraday × √(order_size / volume)

    Interpretação física:
        √(Q/V) = participação de mercado fracionária.
        Q/V = 1%  → fator 0.10  (mercado normal)
        Q/V = 10% → fator 0.316 (stress moderado)
        Q/V = 50% → fator 0.707 (pânico extremo — book esvaziado)

    Diferença crítica vs linear:
        Linear: slippage = k × Q/V  → dobrar ordem dobra slippage
        √:      slippage = η×σ×√(Q/V) → dobrar ordem aumenta √2 × slippage

    Em flash crash (LUNA Mai/2022): volume colapsou 95% nas primeiras 6h.
        Normal: √(Q / V_normal)  = √(0.005) ≈ 0.07
        Crash:  √(Q / 0.05×V_n) = √(0.10)  ≈ 0.32  → 4.5× mais slippage

    Args:
        order_size_usdt: Tamanho da posição em USDT (do sizing).
        volume_usdt:     Volume do bar em USDT = Close × Volume.
        sigma_intraday:  Volatilidade intradiária (High-Low / Mid).
        eta:             Parâmetro de proporcionalidade ∈ [0.05, 0.15].
                         Default 0.10. NUNCA < 0.05.

    Returns:
        float: fração de slippage ∈ [0, 0.50]. Cap conservador aplicado.
    """
    if eta < MIN_ETA:
        log.warning("market_impact.eta_too_low",
                    eta=eta, min_eta=MIN_ETA,
                    msg="Subestima crash environments. Usando MIN_ETA.")
        eta = MIN_ETA

    volume_safe  = max(volume_usdt, 1e-10)
    participation = order_size_usdt / volume_safe
    slippage_raw  = eta * sigma_intraday * np.sqrt(participation)

    slippage = float(min(slippage_raw, MAX_SLIPPAGE_CAP))

    log.debug("market_impact.computed",
              Q=round(order_size_usdt, 2),
              V=round(volume_usdt, 2),
              participation_pct=round(participation * 100, 4),
              sigma_intraday=round(sigma_intraday, 6),
              slippage_pct=round(slippage * 100, 4),
              eta=eta)

    return slippage


def slippage_table(
    order_size_usdt:  float,
    volume_scenarios: dict[str, float],
    sigma_intraday:   float,
    eta:              float = DEFAULT_ETA,
) -> dict[str, dict]:
    """
    Tabela diagnóstica de slippage para diferentes cenários de volume.
    Útil para calibrar η comparando com dados reais de execução.

    Exemplo:
        slippage_table(
            order_size_usdt=5000,
            volume_scenarios={
                "normal":       10_000_000,
                "stress":        1_000_000,
                "flash_crash":     500_000,
                "luna_like":        50_000,
            },
            sigma_intraday=0.04,
        )
    """
    results = {}
    for label, vol in volume_scenarios.items():
        slip = compute_sqrt_market_impact(order_size_usdt, vol, sigma_intraday, eta)
        results[label] = {
            "volume_usdt":     vol,
            "participation":   round(order_size_usdt / max(vol, 1e-10) * 100, 4),
            "slippage_pct":    round(slip * 100, 4),
            "exit_multiplier": round(1.0 - slip, 6),
        }
    return results
