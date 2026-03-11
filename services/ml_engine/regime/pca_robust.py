# =============================================================================
# DESTINO: services/ml_engine/regime/pca_robust.py
# Pipeline robusto: WinsorizaÃ§Ã£o â†’ RobustScaler â†’ PCA walk-forward.
#
# v10.10.1 FIX: PCA agora seleciona componentes por VARIÃ‚NCIA MÃNIMA,
# em linha com a especificaÃ§Ã£o: 2 PCs por padrÃ£o, 3 PCs apenas se necessÃ¡rio
# â†’ HMM cego (hmm_prob_bull=0.9998 constante). SoluÃ§Ã£o: PCA retÃ©m PCs
# suficientes para â‰¥85% variÃ¢ncia. Se precisar de 4-5 PCs, passa 4-5.
# HMM params: 5 PCs, 2 estados, full cov = ~37 params.
# Com N_train â‰¥ 500, ratio N/params â‰ˆ 13-40 â†’ estÃ¡vel.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import RobustScaler
import structlog

from .winsorizer import WinsorizerFitted, fit_winsorizer, apply_winsorizer

log = structlog.get_logger(__name__)

# v10.10.1: variÃ¢ncia mÃ­nima em vez de contagem fixa
MIN_VARIANCE_TARGET  = 0.80
MAX_PCA_COMPONENTS   = 5
MIN_PCA_COMPONENTS   = 2


@dataclass
class RobustPCAFitted:
    """Artefato completo WinsorizaÃ§Ã£o â†’ RobustScaler â†’ PCA."""
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

    v10.10.1: SeleÃ§Ã£o de componentes por variÃ¢ncia.
    Se n_components Ã© None ou float:
        PCA retÃ©m o mÃ­nimo de PCs para atingir min_variance (default 80%).
        Clampado entre MIN_PCA_COMPONENTS e MAX_PCA_COMPONENTS.
    Se n_components Ã© int:
        Comportamento legado (forÃ§ar N PCs fixos).

    Pipeline:
        1. WinsorizaÃ§Ã£o 1%-99%
        2. RobustScaler (mediana + IQR)
        3. PCA (variÃ¢ncia-alvo)
    """
    names = feature_names or [f"f{i}" for i in range(X_train.shape[1])]
    n_features = X_train.shape[1]

    # â”€â”€ 1. WinsorizaÃ§Ã£o â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    winsorizer = fit_winsorizer(X_train, feature_names=names)
    X_win      = apply_winsorizer(X_train, winsorizer)

    # â”€â”€ 2. RobustScaler â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    scaler   = RobustScaler()
    X_scaled = scaler.fit_transform(X_win)

    # â”€â”€ 3. PCA com seleÃ§Ã£o dinÃ¢mica de componentes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    if n_components is not None and isinstance(n_components, int):
        # Legado: forÃ§ar N PCs fixos
        n_pcs = min(n_components, n_features)
        pca = PCA(n_components=n_pcs, random_state=42)
        pca.fit(X_scaled)
        var_exp = float(pca.explained_variance_ratio_.cumsum()[-1])
        log.info("pca.fitted_fixed",
                 n_components=n_pcs, var_explained=round(var_exp, 3))
    else:
        # v10.10.1: SeleÃ§Ã£o por variÃ¢ncia
        # Passo 1: Fittar PCA completo para ver a curva de variÃ¢ncia
        pca_full = PCA(random_state=42)
        pca_full.fit(X_scaled)
        cumvar = pca_full.explained_variance_ratio_.cumsum()

        # Passo 2: Encontrar o mÃ­nimo de PCs para atingir min_variance
        n_needed = 1
        for i, cv in enumerate(cumvar):
            if cv >= min_variance:
                n_needed = i + 1
                break
        else:
            n_needed = len(cumvar)  # Usar tudo se nÃ£o atingir target

        # Clampar entre MIN e MAX
        n_pcs = max(MIN_PCA_COMPONENTS, min(n_needed, MAX_PCA_COMPONENTS))

        # Passo 3: Refittar PCA com o nÃºmero correto de componentes
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
    """DiagnÃ³stico: PC1 z-scores em datas de crash (Standard vs Robust)."""
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

