# =============================================================================
# DESTINO: services/ml_engine/fracdiff/weights.py
# Pesos da série de Taylor para diferenciação fracionária.
# Weight cutoff τ=1e-5: janela efetiva ~500 obs para d=0.4
# vs window=10 que dá apenas 10 obs (perda de memória de longo prazo).
# Referência: Lopez de Prado, AFML 2018, Cap. 5.
# =============================================================================
from __future__ import annotations

import numpy as np
import structlog

log = structlog.get_logger(__name__)

DEFAULT_TAU = 1e-5   # weight cutoff — NUNCA aumentar acima de 1e-3


def fracdiff_weights(d: float, tau: float = DEFAULT_TAU) -> np.ndarray:
    """
    Pesos da série de Taylor para (1-L)^d com cutoff por magnitude.

    A expansão binomial de (1-L)^d gera:
        w_0 = 1
        w_k = -w_{k-1} * (d - k + 1) / k

    tau=1e-5: expande até |w_k| < tau. Para d=0.4 → ~500 coeficientes.
    window=10 (abordagem errada): trunca em 10 → perde a memória de longo prazo
    que é o PROPÓSITO da FracDiff (vs primeira diferença que usa apenas 2 obs).

    Args:
        d:   Ordem de diferenciação. d ∈ (0, 1).
             d=0 → série original, d=1 → primeira diferença.
             d ótimo: mínimo d que garante estacionariedade (ADF p < 0.05).
        tau: Weight cutoff. Interrompe expansão quando |w_k| < tau.
             τ=1e-5: ~500 obs para d=0.4 (janela efetiva longa).
             τ=1e-3: ~100 obs (compromisso memória/velocidade).
             NUNCA usar τ > 1e-3 em produção.

    Returns:
        np.ndarray: pesos ordenados do mais antigo para o mais recente.
                    Shape: (n_weights,). n_weights varia com d e tau.
    """
    if not (0.0 <= d <= 1.0):
        raise ValueError(f"d deve estar em [0, 1]. Recebido: {d}")
    if tau <= 0:
        raise ValueError(f"tau deve ser positivo. Recebido: {tau}")

    weights: list[float] = [1.0]
    k = 1
    while True:
        w = -weights[-1] * (d - k + 1) / k
        if abs(w) < tau:
            break
        weights.append(w)
        k += 1
        if k > 10_000:
            log.warning("fracdiff_weights.max_iter", d=d, tau=tau,
                        n_weights=k, msg="Interrompido em 10k iterações")
            break

    arr = np.array(weights[::-1], dtype=np.float64)  # mais antigo primeiro
    log.debug("fracdiff_weights.computed", d=round(d, 4), tau=tau,
              n_weights=len(arr), effective_window=len(arr))
    return arr


def fracdiff_weights_window(d: float, window: int = 10) -> np.ndarray:
    """
    Versão com janela fixa (ABORDAGEM INCORRETA — mantida apenas para comparação).
    NÃO usar em produção. Existe para o diagnóstico comparativo do backtest.
    Demonstra a perda de memória vs weight cutoff.
    """
    weights: list[float] = [1.0]
    for k in range(1, window):
        w = -weights[-1] * (d - k + 1) / k
        weights.append(w)
    return np.array(weights[::-1], dtype=np.float64)


def summarize_weights(d_values: list[float], tau: float = DEFAULT_TAU) -> dict:
    """
    Diagnóstico: quantos pesos são gerados para cada d?
    Útil para estimar custo computacional antes do FracDiff completo.

    Returns:
        dict: {d_value: n_weights} para cada d em d_values.
    """
    result = {}
    for d in d_values:
        w = fracdiff_weights(d, tau=tau)
        result[d] = {
            "n_weights":       len(w),
            "sum_abs_weights": float(np.abs(w).sum()),
            "max_weight":      float(np.abs(w).max()),
            "min_weight":      float(np.abs(w).min()),
        }
    return result
