# =============================================================================
# DESTINO: services/ml_engine/regime/winsorizer.py
# Winsorização feature-wise: cap explícito em percentis 1% e 99%.
# Necessário ANTES do RobustScaler para que eventos 50σ não distorçam o PCA.
#
# Por que Winsorizar antes do RobustScaler se RobustScaler já é robusto?
# RobustScaler usa mediana + IQR → imune a outliers na TRANSFORMAÇÃO.
# Mas o PCA ainda minimiza erro L2 — um outlier de 50σ winsorizado para 99th
# percentil não rotaciona PC1 para "direção do colapso FTX".
# A Winsorização é o cap explícito; RobustScaler é a normalização robusta.
# Juntos garantem HMM estável mesmo em regime de cisne negro.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import structlog

log = structlog.get_logger(__name__)

WINSOR_LOW  = 0.01   # percentil 1%
WINSOR_HIGH = 0.99   # percentil 99%


@dataclass
class WinsorizerFitted:
    """
    Parâmetros de Winsorização fittados nos dados de treino.
    NUNCA recalcular com dados de teste — sempre usar bounds do treino.
    """
    bounds:     list[tuple[float, float]]  # (lo, hi) por feature
    feature_names: list[str] = field(default_factory=list)
    low_pct:    float = WINSOR_LOW
    high_pct:   float = WINSOR_HIGH
    is_fitted:  bool  = False


def fit_winsorizer(
    X_train:       np.ndarray,
    feature_names: list[str] | None = None,
    low_pct:       float = WINSOR_LOW,
    high_pct:      float = WINSOR_HIGH,
) -> WinsorizerFitted:
    """
    Calcula bounds de Winsorização nos dados de TREINO.
    Retorna objeto WinsorizerFitted para aplicar em treino e teste.

    REGRA: bounds calculados APENAS no treino, nunca no teste.
    Aplicar os mesmos bounds no teste preserva a separação temporal.

    Args:
        X_train:       Array (N_treino × N_features).
        feature_names: Nomes das features para logging.
        low_pct:       Percentil inferior. Default 1% = 0.01.
        high_pct:      Percentil superior. Default 99% = 0.99.

    Returns:
        WinsorizerFitted com bounds por coluna.
    """
    if X_train.ndim != 2:
        raise ValueError(f"X_train deve ser 2D. Shape: {X_train.shape}")

    n_features = X_train.shape[1]
    names      = feature_names or [f"f{i}" for i in range(n_features)]

    bounds: list[tuple[float, float]] = []
    for col in range(n_features):
        col_data = X_train[:, col]
        valid    = col_data[~np.isnan(col_data)]

        if len(valid) < 10:
            log.warning("winsorizer.insufficient_data",
                        feature=names[col], n_valid=len(valid))
            bounds.append((float(-np.inf), float(np.inf)))
            continue

        lo = float(np.percentile(valid, low_pct * 100))
        hi = float(np.percentile(valid, high_pct * 100))
        bounds.append((lo, hi))

        # Log de features com distribuições extremas
        max_val = float(np.max(np.abs(valid)))
        if max_val > 10:
            log.debug("winsorizer.extreme_feature",
                      feature=names[col], max_abs=round(max_val, 2),
                      lo=round(lo, 4), hi=round(hi, 4))

    log.info("winsorizer.fitted", n_features=n_features,
             low_pct=low_pct, high_pct=high_pct)

    return WinsorizerFitted(
        bounds=bounds,
        feature_names=names,
        low_pct=low_pct,
        high_pct=high_pct,
        is_fitted=True,
    )


def apply_winsorizer(
    X:          np.ndarray,
    winsorizer: WinsorizerFitted,
) -> np.ndarray:
    """
    Aplica bounds pré-calculados em qualquer conjunto (treino ou teste).
    Usa np.clip: O(N × n_features), sem cópias desnecessárias.

    Garante que um outlier de 50σ (como FTX Nov/2022) seja tratado
    como o percentil 99 do treino — sem distorcer os autovetores do PCA.
    """
    if not winsorizer.is_fitted:
        raise RuntimeError("Winsorizer não está fittado. Chamar fit_winsorizer() primeiro.")

    X_clip = X.copy()
    for col, (lo, hi) in enumerate(winsorizer.bounds):
        if col >= X_clip.shape[1]:
            break
        X_clip[:, col] = np.clip(X_clip[:, col], lo, hi)

    return X_clip


def fit_apply_winsorizer(
    X_train: np.ndarray,
    X_test:  np.ndarray | None = None,
    feature_names: list[str] | None = None,
) -> tuple[np.ndarray, np.ndarray | None, WinsorizerFitted]:
    """
    Conveniência: fitta no treino e aplica em treino + teste.
    Retorna (X_train_winsorized, X_test_winsorized, winsorizer_fitted).
    """
    w       = fit_winsorizer(X_train, feature_names)
    Xtr_win = apply_winsorizer(X_train, w)
    Xte_win = apply_winsorizer(X_test, w) if X_test is not None else None
    return Xtr_win, Xte_win, w
