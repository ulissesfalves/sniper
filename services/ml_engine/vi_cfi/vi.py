# =============================================================================
# DESTINO: services/ml_engine/vi_cfi/vi.py
# Variation of Information (VI) — métrica de distância informacional.
#
# POR QUE VI E NÃO CORRELAÇÃO DE PEARSON (Parte 6):
# LightGBM explora relações não-lineares. Correlação zero NÃO implica
# independência informacional. VI = 0 implica. Exemplo:
#   ret_1d vs |ret_1d|: Pearson/Spearman ≈ 0 (parece independente).
#   VI: baixo (são informativamente dependentes — correto).
# VI é a única métrica com garantia matemática de consistência:
# satisfaz a desigualdade triangular (é uma métrica própria).
# Referência: Lopez de Prado, AFML 2018, Cap. 3.
# =============================================================================
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import KBinsDiscretizer
import structlog

log = structlog.get_logger(__name__)


def _entropy(z: np.ndarray) -> float:
    """Entropia de Shannon em bits. H(X) = -Σ p_i × log2(p_i)."""
    _, counts = np.unique(z, return_counts=True)
    p = counts / counts.sum()
    return float(-np.sum(p * np.log2(p + 1e-12)))


def variation_of_information(
    x:      np.ndarray,
    y:      np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Variation of Information normalizada ∈ [0, 1].

    VI(X, Y) = H(X|Y) + H(Y|X) = H(X,Y) - I(X;Y)
    VI_norm   = VI(X,Y) / H(X,Y)

    Interpretação:
        0.0 → X e Y são funcionalmente idênticas (redundância total)
        1.0 → X e Y são informativamente independentes
        0.30 → features compartilham ~70% da informação mútua

    n_bins adaptativo: min(10, √N) — reduz sensibilidade com N pequeno.
    n_bins mínimo: 5 (abaixo disso a discretização perde resolução).

    Args:
        x, y:   Arrays 1D de valores reais. NaN removidos antes do cálculo.
        n_bins: Número de bins para discretização. Adaptativo se N < 100.

    Returns:
        float: VI normalizada ∈ [0, 1].
    """
    # Remove NaN (alinhado — remove linha se qualquer um é NaN)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    n    = len(x)

    if n < 20:
        log.warning("vi.insufficient_data", n=n)
        return 1.0   # sem dados suficientes → assume independência

    # n_bins adaptativo
    bins = max(5, min(n_bins, int(np.sqrt(n))))

    disc = KBinsDiscretizer(
        n_bins=bins,
        encode="ordinal",
        strategy="quantile",
        subsample=None,
    )

    xd = disc.fit_transform(x.reshape(-1, 1)).ravel().astype(int)
    yd = disc.fit_transform(y.reshape(-1, 1)).ravel().astype(int)

    hx  = _entropy(xd)
    hy  = _entropy(yd)

    # Entropia conjunta via codificação de pares
    joint    = xd * (bins + 1) + yd
    hxy      = _entropy(joint)
    mi       = hx + hy - hxy             # Mutual Information
    vi_raw   = hx + hy - 2 * mi          # = H(X,Y) - I(X;Y)

    # Normaliza por H(X,Y) para escala [0,1]
    vi_norm = max(vi_raw / max(hxy, 1e-12), 0.0)
    return float(min(vi_norm, 1.0))


def compute_vi_distance_matrix(
    feature_df: pd.DataFrame,
    n_bins:     int = 10,
) -> pd.DataFrame:
    """
    Matriz de distâncias VI para todas as features.
    Complexidade: O(n_features²). Para 15 features: 105 pares.

    Returns:
        pd.DataFrame simétrica (n_features × n_features) com VI ∈ [0,1].
        diagonal = 0 (feature idêntica a si mesma).
    """
    feats = feature_df.columns.tolist()
    n     = len(feats)
    D     = np.zeros((n, n))

    for i in range(n):
        for j in range(i + 1, n):
            vi = variation_of_information(
                feature_df[feats[i]].values,
                feature_df[feats[j]].values,
                n_bins=n_bins,
            )
            D[i, j] = D[j, i] = vi

    log.info("vi_matrix.computed",
             n_features=n,
             n_pairs=n * (n - 1) // 2,
             mean_vi=round(float(D[D > 0].mean()), 4) if D.any() else 0.0)

    return pd.DataFrame(D, index=feats, columns=feats)


def plot_vi_heatmap(
    dist_matrix: pd.DataFrame,
    output_path: str = "vi_heatmap.png",
    title:       str = "Variation of Information Distance Matrix\n0=idênticas | 1=independentes",
) -> None:
    """
    Gera heatmap da matriz VI. OBRIGATÓRIO antes de definir vi_threshold.

    Salvar como artefato permanente do backtest (checklist item 8).
    Inspecionar visualmente para determinar threshold adequado:
        - Clusters com VI < 0.30: features informativamente redundantes
        - VI > 0.45: features genuinamente independentes

    Args:
        dist_matrix: pd.DataFrame retornado por compute_vi_distance_matrix.
        output_path: Caminho para salvar o PNG.
        title:       Título do gráfico.
    """
    try:
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError:
        log.error("vi_heatmap.import_error",
                  msg="Instalar matplotlib e seaborn para gerar heatmap.")
        return

    fig, ax = plt.subplots(figsize=(12, 10))
    sns.heatmap(
        dist_matrix,
        annot=True,
        fmt=".2f",
        cmap="YlOrRd_r",    # invertido: vermelho = mais parecido (VI baixo)
        vmin=0.0,
        vmax=1.0,
        linewidths=0.5,
        ax=ax,
    )
    ax.set_title(title, fontsize=13, pad=15)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("vi_heatmap.saved", path=output_path,
             msg="Definir vi_threshold ANTES de executar CFI.")


def stability_check(
    feature_df: pd.DataFrame,
    n_bins_range: tuple[int, int] = (8, 12),
    n_bins_default: int = 10,
) -> dict:
    """
    Verifica estabilidade da matriz VI com n_bins ± 2.
    Checklist item da Parte 17 (Riscos não resolvidos): VI sensível a n_bins com N pequeno.

    Returns:
        dict com variância média das entradas da matriz para diferentes n_bins.
        Se variância > 0.05: resultado instável — aumentar N ou fundir clusters.
    """
    matrices: list[pd.DataFrame] = []
    for nb in range(n_bins_range[0], n_bins_range[1] + 1):
        matrices.append(compute_vi_distance_matrix(feature_df, n_bins=nb))

    stack  = np.stack([m.values for m in matrices], axis=0)
    var    = float(np.mean(np.var(stack, axis=0)))
    stable = var < 0.05

    log.info("vi.stability_check",
             n_bins_range=n_bins_range,
             mean_variance=round(var, 5),
             stable=stable)

    return {
        "mean_variance": round(var, 5),
        "stable":        stable,
        "n_bins_tested": list(range(n_bins_range[0], n_bins_range[1] + 1)),
        "recommendation": "OK" if stable else
                          "INSTÁVEL: considerar N maior ou fundir clusters",
    }
