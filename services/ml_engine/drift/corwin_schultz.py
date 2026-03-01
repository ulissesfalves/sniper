# =============================================================================
# DESTINO: services/ml_engine/drift/corwin_schultz.py
# Estimador de Spread de Corwin-Schultz (CS) como proxy de liquidez.
#
# POR QUE CS E NÃO SPREAD MÉDIO DE ORDER BOOK (Parte 11):
#   Order book exige dados de L2 tick (pagos, difíceis de obter histórico).
#   CS estima o spread bid-ask a partir de dados OHLCV públicos.
#   Fórmula derivada das razões High-Low de um bar vs dois bars consecutivos.
#   Empiricamente robusto em activos de alta volatilidade intradiária (crypto).
#   Referência: Corwin & Schultz (2012), Journal of Finance.
#
# PAPEL NO CIRCUIT BREAKER (Parte 11):
#   Gatilho de suspensão quando spread CS excede 3σ da média histórica.
#   Em flash crash: bid-ask explode (market makers saem) antes da queda de preço.
#   CS captura isso com atraso de 1 bar — suficiente para bloquear novos sinais.
#   σ_threshold calibrado com dados do treino (nunca com teste).
# =============================================================================
from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Threshold de anomalia: CS > CS_mean + CS_SIGMA_THR × CS_std
CS_SIGMA_THR_DEFAULT = 3.0    # 3σ: evento raro (~0.3% base normal)


def corwin_schultz_spread(
    high:  pd.Series,
    low:   pd.Series,
) -> pd.Series:
    """
    Estimador de Spread de Corwin-Schultz (2012).

    Fórmula:
        β_t    = [ln(H_t / L_t)]² + [ln(H_{t-1} / L_{t-1})]²
        γ_t    = [ln(max(H_t, H_{t-1}) / min(L_t, L_{t-1}))]²

        α_t = (√(2β_t) - √β_t) / (3 - 2√2) - √(γ_t / (3 - 2√2))
        S_t = 2 × (exp(α_t) - 1) / (1 + exp(α_t))

    Quando S_t < 0 (artefato numérico em barras muito estreitas):
        S_t = 0 por convenção (spread não pode ser negativo).

    High/Low devem ser de BARRAS INTRADIÁRIAS (4h recomendado).
    Barras diárias subestimam o spread real por overnight noise.
    O Binance.py do Passo 2 coleta klines 4h precisamente para este uso.

    Args:
        high: pd.Series de High prices (qualquer frequência intradiária).
        low:  pd.Series de Low prices.

    Returns:
        pd.Series de spread CS estimado ∈ [0, ∞).
        NaN na primeira observação (precisa de bar anterior).
    """
    h = np.log(high.values.astype(np.float64))
    l = np.log(low.values.astype(np.float64))

    n  = len(h)
    s  = np.full(n, np.nan)

    for t in range(1, n):
        # β: soma das variâncias de dois bars consecutivos
        beta = (h[t] - l[t]) ** 2 + (h[t - 1] - l[t - 1]) ** 2

        # γ: variância do range de dois bars juntos
        h2   = max(h[t], h[t - 1])
        l2   = min(l[t], l[t - 1])
        gamma = (h2 - l2) ** 2

        # α: componente de spread
        sqrt2   = np.sqrt(2.0)
        k1      = 3.0 - 2.0 * sqrt2         # ≈ 0.1716
        sqrt_b  = np.sqrt(max(beta, 0.0))
        sqrt_2b = np.sqrt(max(2.0 * beta, 0.0))

        alpha = (sqrt_2b - sqrt_b) / k1 - np.sqrt(max(gamma / k1, 0.0))

        # Spread: transformação para escala de preço
        exp_a = np.exp(alpha)
        s[t]  = max(2.0 * (exp_a - 1.0) / (1.0 + exp_a), 0.0)

    result = pd.Series(s, index=high.index, name="cs_spread")
    log.debug("corwin_schultz.computed",
              n=n,
              mean_spread=round(float(np.nanmean(s)), 6),
              max_spread=round(float(np.nanmax(s)), 6))
    return result


def compute_cs_features(
    high:      pd.Series,
    low:       pd.Series,
    roll_window: int = 30,
    roll_min:    int = 10,
) -> pd.DataFrame:
    """
    Features de liquidez derivadas do CS spread para o circuit breaker.

    Colunas geradas:
        cs_spread:      Spread CS raw (bar a bar).
        cs_roll_mean:   Média rolling 30 períodos (referência "normal").
        cs_roll_std:    Desvio rolling 30 períodos.
        cs_zscore:      (cs - mean) / std → σ acima do normal.
        cs_anomaly:     bool — True se cs_zscore > CS_SIGMA_THR (3σ por default).

    Args:
        high, low:    pd.Series de High e Low (barras 4h recomendado).
        roll_window:  Janela para cálculo da média/std de referência.
        roll_min:     Mínimo de obs para ativar o rolling.

    Returns:
        pd.DataFrame com as 5 colunas acima.
    """
    cs = corwin_schultz_spread(high, low)

    roll_mean = cs.rolling(roll_window, min_periods=roll_min).mean()
    roll_std  = cs.rolling(roll_window, min_periods=roll_min).std().clip(lower=1e-8)
    cs_z      = (cs - roll_mean) / roll_std
    anomaly   = cs_z > CS_SIGMA_THR_DEFAULT

    df = pd.DataFrame({
        "cs_spread":    cs,
        "cs_roll_mean": roll_mean,
        "cs_roll_std":  roll_std,
        "cs_zscore":    cs_z,
        "cs_anomaly":   anomaly,
    })

    n_anomaly = int(anomaly.sum())
    log.info("cs_features.computed",
             n_bars=len(high),
             n_anomaly=n_anomaly,
             anomaly_pct=round(n_anomaly / max(len(high), 1) * 100, 2))
    return df


def circuit_breaker_check(
    cs_features_df: pd.DataFrame,
    capital_at_risk: float,
    sigma_threshold: float = CS_SIGMA_THR_DEFAULT,
) -> dict:
    """
    Verificação do circuit breaker de liquidez (Parte 11 — v10.10).

    Gatilho de suspensão imediata quando:
        cs_zscore[-1] > sigma_threshold   (último bar)
        OU
        cs_zscore[-3:].mean() > 2σ        (anomalia persistente: 3 bars)

    Quando ativado:
        - Bloqueia novos sinais de entrada
        - Não força saída de posições abertas (evita pânico)
        - Registra evento para o dashboard e notificações

    Args:
        cs_features_df: DataFrame de compute_cs_features().
        capital_at_risk: Capital em posições abertas (para log).
        sigma_threshold: Limiar de ativação. Default 3.0σ.

    Returns:
        dict com status 'BLOCKED'/'CLEAR', gatilho e métricas.
    """
    if cs_features_df.empty or "cs_zscore" not in cs_features_df.columns:
        return {"status": "CLEAR", "reason": "sem dados CS"}

    latest_z   = float(cs_features_df["cs_zscore"].iloc[-1])
    recent_z   = float(cs_features_df["cs_zscore"].iloc[-3:].mean())
    latest_cs  = float(cs_features_df["cs_spread"].iloc[-1])
    baseline   = float(cs_features_df["cs_roll_mean"].iloc[-1])

    single_trigger     = latest_z > sigma_threshold
    persistent_trigger = recent_z > 2.0
    blocked            = single_trigger or persistent_trigger

    reason = ""
    if single_trigger:
        reason = f"cs_zscore={latest_z:.2f}σ > {sigma_threshold}σ (single bar)"
    elif persistent_trigger:
        reason = f"cs_zscore_3bar_mean={recent_z:.2f}σ > 2.0σ (persistente)"

    result = {
        "status":              "BLOCKED" if blocked else "CLEAR",
        "reason":              reason,
        "cs_zscore_latest":    round(latest_z, 3),
        "cs_zscore_3bar_mean": round(recent_z, 3),
        "cs_spread_latest":    round(latest_cs, 6),
        "cs_spread_baseline":  round(baseline, 6),
        "spread_ratio":        round(latest_cs / max(baseline, 1e-10), 2),
        "capital_at_risk_usdt": round(capital_at_risk, 2),
        "sigma_threshold":     sigma_threshold,
    }

    if blocked:
        log.warning("circuit_breaker.BLOCKED",
                    reason=reason,
                    cs_z=round(latest_z, 2),
                    spread_ratio=result["spread_ratio"],
                    capital_at_risk=round(capital_at_risk, 2))
    else:
        log.debug("circuit_breaker.clear",
                  cs_z=round(latest_z, 2))

    return result
