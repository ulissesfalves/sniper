# =============================================================================
# DESTINO: services/ml_engine/triple_barrier/labeler.py
# Triple-Barrier Method v10.10 com:
#   - Detecção HLC (v10.8): Low[t] <= SL verificado ANTES de High[t] >= TP
#   - Market impact √(Q/V) no exit price do SL (v10.10)
#   - pnl_real (com slippage) alimenta CVaR — não pnl_teórico (v10.9)
#
# CRÍTICA CORRIGIDA v10.8: verificar apenas Close[t] para SL/TP é errado.
# No mesmo bar, Low pode ter tocado SL E High pode ter tocado TP.
# Regra HLC: Low[t] <= SL é verificado PRIMEIRO. Se SL e TP tocam no mesmo
# bar, o SL vence (conservadorismo). Sem isso, o backtest superestima retornos.
# Referência: SNIPER v10.10, Parte 5.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from .market_impact import compute_sqrt_market_impact, compute_intraday_vol_parkinson

log = structlog.get_logger(__name__)

DEFAULT_K_TP            = 1.75
DEFAULT_K_SL            = 1.0
DEFAULT_MAX_HOLDING     = 4
DEFAULT_ETA             = 0.10


@dataclass
class BarrierResult:
    """Resultado de uma observação do Triple-Barrier."""
    event_date:   pd.Timestamp
    t_touch:      pd.Timestamp
    label:        int              # +1 TP, -1 SL, 0 time-stop
    barrier_tp:   float
    barrier_sl:   float
    exit_price:   float
    pnl_real:     float            # P&L com slippage — alimenta CVaR
    slippage_frac: float
    sigma_at_entry: float
    p0:           float
    holding_days: int


@dataclass
class TripleBarrierConfig:
    k_tp:            float = DEFAULT_K_TP
    k_sl:            float = DEFAULT_K_SL
    max_holding_days: int  = DEFAULT_MAX_HOLDING
    eta:             float = DEFAULT_ETA


def apply_triple_barrier(
    close_prices:   pd.Series,
    high_prices:    pd.Series,
    low_prices:     pd.Series,
    volume_series:  pd.Series,
    events:         pd.DatetimeIndex,
    sigma_ewma:     pd.Series,
    sigma_intraday: pd.Series,
    position_sizes: pd.Series,
    config:         TripleBarrierConfig = TripleBarrierConfig(),
) -> pd.DataFrame:
    """
    Triple-Barrier Method v10.10.

    Barreiras:
        TP: p0 × (1 + k_tp × σ_ewma)   — take-profit
        SL: p0 × (1 - k_sl × σ_ewma)   — stop-loss
        TS: max_holding_days            — time-stop

    Lógica de verificação HLC (v10.8) — REGRA INVIOLÁVEL:
        Para cada bar t após entrada:
            1. Verifica Low[t] <= barrier_sl  → SL hit (prioridade máxima)
            2. Se não SL: verifica High[t] >= barrier_tp → TP hit
            3. Se ambos no mesmo bar: SL vence (conservadorismo)

    Slippage no SL (v10.10 — Lei da Raiz Quadrada):
        exit_price = barrier_sl × (1 - η × σ_intraday × √(Q / V))
        Fisicamente correto segundo Kyle/Almgren-Chriss.
        pnl_real inclui este custo — alimenta CVaR (nunca pnl_teórico).

    TP sem slippage:
        exit_price = barrier_tp (limit order executada exatamente no preço).

    Args:
        close_prices:   pd.Series de Close com DatetimeIndex.
        high_prices:    pd.Series de High (essencial para detecção HLC de TP).
        low_prices:     pd.Series de Low (essencial para detecção HLC de SL).
        volume_series:  pd.Series de Volume em unidade base (não USDT).
        events:         DatetimeIndex das datas de entrada (sinais).
        sigma_ewma:     pd.Series de volatilidade EWMA (base das barreiras).
        sigma_intraday: pd.Series de volatilidade intradiária (Parkinson).
        position_sizes: pd.Series de USDT alocado por data de entrada.
        config:         TripleBarrierConfig com k_tp, k_sl, max_holding, eta.

    Returns:
        pd.DataFrame com uma linha por evento. Index = event_date.
        Colunas: label, barrier_tp, barrier_sl, exit_price, pnl_real,
                 slippage_frac, sigma_at_entry, p0, t_touch, holding_days.
    """
    # Validações de alinhamento
    if not close_prices.index.equals(high_prices.index):
        raise ValueError("close_prices e high_prices devem ter o mesmo índice.")
    if not close_prices.index.equals(low_prices.index):
        raise ValueError("close_prices e low_prices devem ter o mesmo índice.")
    if not close_prices.index.equals(volume_series.index):
        raise ValueError("close_prices e volume_series devem ter o mesmo índice.")

    price_idx = close_prices.index
    results: list[BarrierResult] = []
    n_sl = n_tp = n_ts = 0

    for event_date in events:
        if event_date not in price_idx:
            continue

        t0  = price_idx.get_loc(event_date)
        p0  = float(close_prices.iloc[t0])

        sigma_t = float(sigma_ewma.get(event_date, np.nan))
        if np.isnan(sigma_t) or sigma_t <= 0:
            log.warning("triple_barrier.invalid_sigma",
                        event=str(event_date), sigma=sigma_t)
            continue

        barrier_tp = p0 * (1.0 + config.k_tp * sigma_t)
        barrier_sl = p0 * (1.0 - config.k_sl * sigma_t)

        # Time limit dinâmico: em regimes mais voláteis, encurta a janela.
        # Isso aumenta a separação entre breakout verdadeiro e ruído prolongado.
        vol_ref = 0.03
        hold_scale = float(np.clip(vol_ref / max(sigma_t, 1e-6), 0.50, 1.25))
        dyn_holding = int(np.clip(round(config.max_holding_days * hold_scale), 2, config.max_holding_days))
        t_end      = min(t0 + dyn_holding, len(close_prices) - 1)
        q_usdt     = float(position_sizes.get(event_date, p0 * 1_000))

        label         = 0          # default: time-stop
        t_touch       = price_idx[t_end]
        exit_price    = float(close_prices.iloc[t_end])
        slippage_frac = 0.0

        # ── Scan bar a bar com prioridade HLC ──────────────────────────────
        for t in range(t0 + 1, t_end + 1):
            high_t  = float(high_prices.iloc[t])
            low_t   = float(low_prices.iloc[t])
            close_t = float(close_prices.iloc[t])
            vol_t   = float(volume_series.iloc[t])

            # ── REGRA HLC: Low primeiro, depois High ───────────────────────
            sl_touched = low_t  <= barrier_sl
            tp_touched = high_t >= barrier_tp

            if sl_touched:
                # Slippage real pela Lei da Raiz Quadrada (v10.10)
                vol_usdt     = max(close_t * vol_t, 1e-6)
                sig_intr     = float(sigma_intraday.iloc[t]) \
                               if t < len(sigma_intraday) else sigma_t
                slippage_frac = compute_sqrt_market_impact(
                    q_usdt, vol_usdt, sig_intr, config.eta
                )
                exit_price = barrier_sl * (1.0 - slippage_frac)
                label      = -1
                t_touch    = price_idx[t]
                n_sl      += 1
                break

            elif tp_touched:
                # TP: limit order — sem slippage
                exit_price = barrier_tp
                label      = +1
                t_touch    = price_idx[t]
                n_tp      += 1
                break
        else:
            # Time-stop: saiu no fechamento do último bar
            n_ts += 1

        pnl_real     = (exit_price / p0) - 1.0
        holding_days = (t_touch - event_date).days

        results.append(BarrierResult(
            event_date=event_date,
            t_touch=t_touch,
            label=label,
            barrier_tp=round(barrier_tp, 6),
            barrier_sl=round(barrier_sl, 6),
            exit_price=round(exit_price, 6),
            pnl_real=round(pnl_real, 6),
            slippage_frac=round(slippage_frac, 6),
            sigma_at_entry=round(sigma_t, 6),
            p0=round(p0, 6),
            holding_days=holding_days,
        ))

    total = len(results)
    if total > 0:
        log.info("triple_barrier.complete",
                 n_events=total,
                 pct_tp=round(n_tp / total * 100, 1),
                 pct_sl=round(n_sl / total * 100, 1),
                 pct_ts=round(n_ts / total * 100, 1),
                 avg_slippage_pct=round(
                     np.mean([r.slippage_frac for r in results
                              if r.label == -1]) * 100, 4
                 ) if n_sl > 0 else 0.0)

        # ── Checklist v10.5: distribuição ~30-40% cada ────────────────────
        if not (0.20 <= n_tp / total <= 0.55):
            log.warning("triple_barrier.distribution_skewed",
                        pct_tp=round(n_tp / total, 3),
                        msg="Revisar k_tp/k_sl ou max_holding_days.")

    df = pd.DataFrame([
        {
            "event_date":    r.event_date,
            "t_touch":       r.t_touch,
            "label":         r.label,
            "barrier_tp":    r.barrier_tp,
            "barrier_sl":    r.barrier_sl,
            "exit_price":    r.exit_price,
            "pnl_real":      r.pnl_real,
            "slippage_frac": r.slippage_frac,
            "sigma_at_entry": r.sigma_at_entry,
            "p0":            r.p0,
            "holding_days":  r.holding_days,
        }
        for r in results
    ])

    if df.empty:
        return df

    return df.set_index("event_date")


def validate_barrier_distribution(
    barrier_df: pd.DataFrame,
    min_pct: float = 0.20,
    max_pct: float = 0.55,
) -> dict:
    """
    Checklist v10.5 (item 6b): distribuição +1/-1/0 equilibrada (~30-40% cada).
    Desequilíbrio sinaliza k_tp/k_sl ou max_holding inadequados.

    Returns:
        dict com percentuais e status PASS/FAIL.
    """
    n      = len(barrier_df)
    counts = barrier_df["label"].value_counts()

    pct_tp = counts.get( 1, 0) / n
    pct_sl = counts.get(-1, 0) / n
    pct_ts = counts.get( 0, 0) / n

    status = "PASS" if all(
        min_pct <= p <= max_pct for p in [pct_tp, pct_sl, pct_ts]
    ) else "WARN"

    result = {
        "n_total":  n,
        "pct_tp":   round(pct_tp, 3),
        "pct_sl":   round(pct_sl, 3),
        "pct_ts":   round(pct_ts, 3),
        "status":   status,
        "avg_pnl_tp": round(float(barrier_df.loc[barrier_df["label"] == 1,
                                                  "pnl_real"].mean()), 4),
        "avg_pnl_sl": round(float(barrier_df.loc[barrier_df["label"] == -1,
                                                  "pnl_real"].mean()), 4),
        "avg_slippage_sl_pct": round(
            float(barrier_df.loc[barrier_df["label"] == -1,
                                 "slippage_frac"].mean()) * 100, 4
        ) if (barrier_df["label"] == -1).any() else 0.0,
    }

    log.info("barrier_distribution.check", **result)
    return result
