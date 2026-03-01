# =============================================================================
# DESTINO: services/ml_engine/regime/pca_robust.py
# Pipeline robusto: Winsorização → RobustScaler → PCA walk-forward.
# Corrige o problema do StandardScaler v10.7: outliers 10-20σ (FTX, Mar/2020)
# rotacionam PC1 para a "direção do colapso", contaminando o HMM.
# RobustScaler usa mediana + IQR: imune a fat tails.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import RobustScaler
import structlog

from .winsorizer import WinsorizerFitted, fit_winsorizer, apply_winsorizer

log = structlog.get_logger(__name__)

N_PCA_COMPONENTS    = 2
MIN_VARIANCE_EXPLAINED = 0.80


@dataclass
class RobustPCAFitted:
    """
    Artefato completo do pipeline Winsorização → RobustScaler → PCA.
    Serializado em disco para cada janela de treino (expanding window).
    """
    winsorizer:        WinsorizerFitted
    scaler:            RobustScaler
    pca:               PCA
    var_explained:     float
    n_components:      int
    feature_names:     list[str]
    train_end_idx:     int             # índice final do treino (auditoria)


def fit_robust_pca(
    X_train:       np.ndarray,
    feature_names: list[str] | None = None,
    n_components:  int   = N_PCA_COMPONENTS,
) -> RobustPCAFitted:
    """
    Fitta pipeline robusto EXCLUSIVAMENTE nos dados de treino.

    Pipeline:
        1. Winsorização 1%-99% (cap de outliers extremos)
        2. RobustScaler (mediana + IQR): imune a fat tails
        3. PCA: reduz dimensionalidade preservando estrutura real

    Por que esse pipeline funciona em crypto:
        FTX Nov/2022: funding_rate_ma7d atingiu +15σ.
        StandardScaler: std global inclui o outlier → todos os outros
            pontos comprimidos em intervalo minúsculo → PCA contaminado.
        RobustScaler: mediana e IQR imunes ao outlier → transformação estável.
        Winsorização: garante que 50σ seja tratado como 99th percentil.

    Conta de parâmetros HMM (Parte 4, Tabela 4.2):
        9 features, 2 estados, cov full: ~112 params, ratio N/params ≈ 13 → INSTÁVEL
        2 PCs, 2 estados, cov full:      ~12 params, ratio N/params ≈ 125 → ESTÁVEL

    Args:
        X_train:      Array (N_treino × N_features). Sem NaN.
        feature_names: Nomes das features para diagnóstico.
        n_components:  Número de PCs. Default 2.

    Returns:
        RobustPCAFitted com todos os objetos fittados.
    """
    names = feature_names or [f"f{i}" for i in range(X_train.shape[1])]

    # ── 1. Winsorização ───────────────────────────────────────────────────
    winsorizer = fit_winsorizer(X_train, feature_names=names)
    X_win      = apply_winsorizer(X_train, winsorizer)

    # ── 2. RobustScaler ───────────────────────────────────────────────────
    scaler   = RobustScaler()
    X_scaled = scaler.fit_transform(X_win)

    # ── 3. PCA ────────────────────────────────────────────────────────────
    pca     = PCA(n_components=n_components, random_state=42)
    pca.fit(X_scaled)
    var_exp = float(pca.explained_variance_ratio_.cumsum()[-1])

    if var_exp < MIN_VARIANCE_EXPLAINED:
        log.warning("pca.low_variance",
                    n_components=n_components,
                    var_explained=round(var_exp, 3),
                    suggestion=f"Considerar n_components={n_components + 1}")

    log.info("pca.fitted",
             n_components=n_components,
             var_explained=round(var_exp, 3),
             features=names)

    return RobustPCAFitted(
        winsorizer=winsorizer,
        scaler=scaler,
        pca=pca,
        var_explained=var_exp,
        n_components=n_components,
        feature_names=names,
        train_end_idx=len(X_train),
    )


def transform_robust_pca(
    X:       np.ndarray,
    fitted:  RobustPCAFitted,
) -> np.ndarray:
    """
    Transforma features novas com pipeline fittado no treino.

    REGRA CRÍTICA: nunca re-calcular bounds de Winsorização com dados de teste.
    Usar SEMPRE os bounds do treino (armazenados em fitted.winsorizer).

    Returns:
        np.ndarray: (N × n_components) — coordenadas no espaço PCA.
    """
    X_win    = apply_winsorizer(X, fitted.winsorizer)
    X_scaled = fitted.scaler.transform(X_win)
    return fitted.pca.transform(X_scaled)


def diagnose_pca_robustness(
    X_full:        np.ndarray,
    crash_indices: list[int],
    feature_names: list[str] | None = None,
) -> dict:
    """
    Diagnóstico obrigatório v10.8:
    Compara PC1 com StandardScaler vs RobustScaler em datas de crash.

    Esperado:
    - Com StandardScaler: PC1[crash] >> 5σ (autovetor contaminado)
    - Com RobustScaler + Winsor: PC1[crash] dentro de 3σ

    Args:
        X_full:        Array completo (N × features).
        crash_indices: Índices das datas de crash (Mar/2020, Nov/2022).
        feature_names: Nomes das features.

    Returns:
        dict com z-scores de PC1 nas datas de crash para ambos os métodos.
    """
    from sklearn.preprocessing import StandardScaler

    names = feature_names or [f"f{i}" for i in range(X_full.shape[1])]

    # Pipeline padrão (errado — v10.7)
    std_scaler = StandardScaler()
    X_std      = std_scaler.fit_transform(X_full)
    pca_std    = PCA(n_components=2, random_state=42).fit(X_std)
    pc1_std    = pca_std.transform(X_std)[:, 0]

    # Pipeline robusto (correto — v10.8)
    fitted     = fit_robust_pca(X_full, names)
    X_robust   = transform_robust_pca(X_full, fitted)
    pc1_robust = X_robust[:, 0]

    # Z-scores
    std_z    = (pc1_std    - pc1_std.mean())    / (pc1_std.std()    + 1e-10)
    robust_z = (pc1_robust - pc1_robust.mean()) / (pc1_robust.std() + 1e-10)

    results = {}
    for idx in crash_indices:
        if 0 <= idx < len(X_full):
            results[idx] = {
                "pc1_zscore_standard": round(float(std_z[idx]),    3),
                "pc1_zscore_robust":   round(float(robust_z[idx]), 3),
                "contaminated":        abs(std_z[idx]) > 5.0,
                "robust_ok":           abs(robust_z[idx]) <= 3.0,
            }

    log.info("pca.diagnosis",
             n_crashes=len(crash_indices),
             contaminated=sum(1 for r in results.values() if r["contaminated"]),
             robust_ok=sum(1 for r in results.values() if r["robust_ok"]))
    return results
