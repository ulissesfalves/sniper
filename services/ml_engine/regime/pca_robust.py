# =============================================================================
# DESTINO: services/ml_engine/regime/pca_robust.py
# Pipeline robusto: Winsorização → RobustScaler → PCA walk-forward.
#
# v10.10.1 FIX: PCA agora seleciona componentes por VARIÂNCIA MÍNIMA,
# não por contagem fixa. Com 9 features e n_components=2, var_exp=49%
# → HMM cego (hmm_prob_bull=0.9998 constante). Solução: PCA retém PCs
# suficientes para ≥85% variância. Se precisar de 4-5 PCs, passa 4-5.
# HMM params: 5 PCs, 2 estados, full cov = ~37 params.
# Com N_train ≥ 500, ratio N/params ≈ 13-40 → estável.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import RobustScaler
import structlog

from .winsorizer import WinsorizerFitted, fit_winsorizer, apply_winsorizer

log = structlog.get_logger(__name__)

# v10.10.1: variância mínima em vez de contagem fixa
MIN_VARIANCE_TARGET  = 0.85   # PCA retém PCs até atingir 85% variância
MAX_PCA_COMPONENTS   = 7      # Cap: nunca mais que 7 PCs (evita curse of dimensionality)
MIN_PCA_COMPONENTS   = 2      # Floor: nunca menos que 2 PCs


@dataclass
class RobustPCAFitted:
    """Artefato completo Winsorização → RobustScaler → PCA."""
    winsorizer:        WinsorizerFitted
    scaler:            RobustScaler
    pca:               PCA
    var_explained:     float
    n_components:      int
    feature_names:     list[str]
    train_end_idx:     int


def fit_robust_pca(
    X_train:          np.ndarray,
    feature_names:    list[str] | None = None,
    n_components:     int | float | None = None,
    min_variance:     float = MIN_VARIANCE_TARGET,
) -> RobustPCAFitted:
    """
    Fitta pipeline robusto nos dados de treino.

    v10.10.1: Seleção de componentes por variância.
    Se n_components é None ou float:
        PCA retém o mínimo de PCs para atingir min_variance (default 85%).
        Clampado entre MIN_PCA_COMPONENTS e MAX_PCA_COMPONENTS.
    Se n_components é int:
        Comportamento legado (forçar N PCs fixos).

    Pipeline:
        1. Winsorização 1%-99%
        2. RobustScaler (mediana + IQR)
        3. PCA (variância-alvo)
    """
    names = feature_names or [f"f{i}" for i in range(X_train.shape[1])]
    n_features = X_train.shape[1]

    # ── 1. Winsorização ──────────────────────────────────────────────
    winsorizer = fit_winsorizer(X_train, feature_names=names)
    X_win      = apply_winsorizer(X_train, winsorizer)

    # ── 2. RobustScaler ──────────────────────────────────────────────
    scaler   = RobustScaler()
    X_scaled = scaler.fit_transform(X_win)

    # ── 3. PCA com seleção dinâmica de componentes ───────────────────
    if n_components is not None and isinstance(n_components, int):
        # Legado: forçar N PCs fixos
        n_pcs = min(n_components, n_features)
        pca = PCA(n_components=n_pcs, random_state=42)
        pca.fit(X_scaled)
        var_exp = float(pca.explained_variance_ratio_.cumsum()[-1])
        log.info("pca.fitted_fixed",
                 n_components=n_pcs, var_explained=round(var_exp, 3))
    else:
        # v10.10.1: Seleção por variância
        # Passo 1: Fittar PCA completo para ver a curva de variância
        pca_full = PCA(random_state=42)
        pca_full.fit(X_scaled)
        cumvar = pca_full.explained_variance_ratio_.cumsum()

        # Passo 2: Encontrar o mínimo de PCs para atingir min_variance
        n_needed = 1
        for i, cv in enumerate(cumvar):
            if cv >= min_variance:
                n_needed = i + 1
                break
        else:
            n_needed = len(cumvar)  # Usar tudo se não atingir target

        # Clampar entre MIN e MAX
        n_pcs = max(MIN_PCA_COMPONENTS, min(n_needed, MAX_PCA_COMPONENTS))

        # Passo 3: Refittar PCA com o número correto de componentes
        pca = PCA(n_components=n_pcs, random_state=42)
        pca.fit(X_scaled)
        var_exp = float(pca.explained_variance_ratio_.cumsum()[-1])

        log.info("pca.fitted_variance",
                 target_variance=min_variance,
                 n_features=n_features,
                 n_components_needed=n_needed,
                 n_components_used=n_pcs,
                 var_explained=round(var_exp, 3),
                 cumvar_curve=[round(float(cv), 3) for cv in cumvar[:min(8, len(cumvar))]])

    return RobustPCAFitted(
        winsorizer=winsorizer,
        scaler=scaler,
        pca=pca,
        var_explained=var_exp,
        n_components=n_pcs,
        feature_names=names,
        train_end_idx=len(X_train),
    )


def transform_robust_pca(
    X:       np.ndarray,
    fitted:  RobustPCAFitted,
) -> np.ndarray:
    """Transforma features novas com pipeline fittado no treino."""
    X_win    = apply_winsorizer(X, fitted.winsorizer)
    X_scaled = fitted.scaler.transform(X_win)
    return fitted.pca.transform(X_scaled)


def diagnose_pca_robustness(
    X_full:        np.ndarray,
    crash_indices: list[int],
    feature_names: list[str] | None = None,
) -> dict:
    """Diagnóstico: PC1 z-scores em datas de crash (Standard vs Robust)."""
    from sklearn.preprocessing import StandardScaler

    names = feature_names or [f"f{i}" for i in range(X_full.shape[1])]

    std_scaler = StandardScaler()
    X_std      = std_scaler.fit_transform(X_full)
    pca_std    = PCA(n_components=2, random_state=42).fit(X_std)
    pc1_std    = pca_std.transform(X_std)[:, 0]

    fitted     = fit_robust_pca(X_full, names)
    X_robust   = transform_robust_pca(X_full, fitted)
    pc1_robust = X_robust[:, 0]

    std_z    = (pc1_std    - pc1_std.mean())    / (pc1_std.std()    + 1e-10)
    robust_z = (pc1_robust - pc1_robust.mean()) / (pc1_robust.std() + 1e-10)

    results = {}
    for idx in crash_indices:
        if 0 <= idx < len(X_full):
            results[idx] = {
                "pc1_zscore_standard": round(float(std_z[idx]), 3),
                "pc1_zscore_robust":   round(float(robust_z[idx]), 3),
                "contaminated":        abs(std_z[idx]) > 5.0,
                "robust_ok":           abs(robust_z[idx]) <= 3.0,
            }

    log.info("pca.diagnosis",
             n_pcs_robust=fitted.n_components,
             var_exp_robust=round(fitted.var_explained, 3),
             n_crashes=len(crash_indices),
             contaminated=sum(1 for r in results.values() if r["contaminated"]),
             robust_ok=sum(1 for r in results.values() if r["robust_ok"]))
    return results
