# =============================================================================
# DESTINO: services/ml_engine/vi_cfi/vi.py
# Variation of Information (VI) — métrica de distância informacional.
#
# v10.10.2 REWRITE — 4 DEFESAS LP DE PRADO:
#
#   DEFESA 1 — POOLING GLOBAL:
#     compute_vi_distance_matrix() aceita DataFrame pooled (todos os ativos
#     concatenados). A microestrutura de colinearidade em cripto (ret_1d vs
#     ret_5d) é universal. Calcular VI com N=15000 é estável; com N=500 não.
#
#   DEFESA 2 — VARIÁVEIS DISCRETAS vs CONTÍNUAS:
#     _safe_discretize() detecta binárias (n_unique ≤ 2) e categóricas
#     (n_unique ≤ 15) automaticamente. Faz contagem de frequência direta
#     sem KBinsDiscretizer. Elimina o crash com btc_ma200_flag.
#
#   DEFESA 3 — VARIÂNCIA ZERO:
#     Se std(X) < 1e-5, VI é definida como 1.0 (máxima distância).
#     Feature sem variância não contém informação mútua com nenhuma outra.
#     Isso isola hmm_prob_bull quando está travado em 0.9998.
#
#   DEFESA 4 — CLUSTERIZAÇÃO HIERÁRQUICA:
#     cluster_features() aplica scipy.cluster.hierarchy.linkage (Ward) +
#     fcluster sobre a matriz VI. Retorna mapeamento {cluster_id: [features]}
#     serializável e reutilizável por todos os ativos na Fase 4.
#
# Referência: Lopez de Prado, AFML 2018, Cap. 3.
# =============================================================================
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
from sklearn.preprocessing import KBinsDiscretizer
import structlog

log = structlog.get_logger(__name__)

# ── Constantes ──────────────────────────────────────────────────────────────
MAX_UNIQUE_CATEGORICAL = 15     # n_unique ≤ 15 → bypass KBinsDiscretizer
ZERO_VARIANCE_THRESH   = 1e-5   # std < 1e-5 → feature MATEMATICAMENTE constante
NEAR_CONSTANT_THRESH   = 1e-3   # std < 1e-3 → feature PRATICAMENTE constante (ex: hmm_prob_bull stuck)
VI_THRESHOLD_DEFAULT   = 0.30   # VI < 0.30 → mesmo cluster (70% info compartilhada)


# ═══════════════════════════════════════════════════════════════════════════
# DEFESA 2: Discretização segura — binárias, categóricas, contínuas
# ═══════════════════════════════════════════════════════════════════════════

def _entropy(z: np.ndarray) -> float:
    """Entropia de Shannon em bits. H(X) = -Σ p_i × log2(p_i)."""
    _, counts = np.unique(z, return_counts=True)
    p = counts / counts.sum()
    return float(-np.sum(p * np.log2(p + 1e-12)))


def _safe_discretize(x: np.ndarray, n_bins: int) -> np.ndarray:
    """
    Discretiza array 1D com tratamento diferenciado:

      - Binária (n_unique == 2): mapeamento direto 0/1. SEM KBinsDiscretizer.
      - Categórica (n_unique ≤ 15): mapeamento ordinal direto.
      - Contínua: KBinsDiscretizer(quantile) com n_bins defensivo.
        Fallback chain: quantile → uniform → rank-based.

    Args:
        x:      Array 1D de valores numéricos (sem NaN).
        n_bins: Bins alvo para contínuas. Será reduzido se n_unique < n_bins.

    Returns:
        np.ndarray de inteiros (bins atribuídos).
    """
    n_unique = len(np.unique(x))

    # ── Caso 1: Binária ou categórica → contagem direta ──────────────────
    if n_unique <= MAX_UNIQUE_CATEGORICAL:
        uniques = np.unique(x)
        mapping = {float(v): i for i, v in enumerate(uniques)}
        return np.array([mapping[float(v)] for v in x], dtype=int)

    # ── Caso 2: Contínua → KBinsDiscretizer ──────────────────────────────
    # n_bins defensivo: max(2, min(10, sqrt(N)))
    effective_bins = max(2, min(n_bins, n_unique - 1))

    # Tenta quantile
    try:
        disc = KBinsDiscretizer(
            n_bins=effective_bins, encode="ordinal",
            strategy="quantile", subsample=None,
        )
        return disc.fit_transform(x.reshape(-1, 1)).ravel().astype(int)
    except (ValueError, Exception):
        pass

    # Fallback: uniform
    try:
        disc = KBinsDiscretizer(
            n_bins=effective_bins, encode="ordinal",
            strategy="uniform", subsample=None,
        )
        return disc.fit_transform(x.reshape(-1, 1)).ravel().astype(int)
    except (ValueError, Exception):
        pass

    # Último recurso: rank-based binning
    ranks = np.argsort(np.argsort(x))
    return (ranks * effective_bins // len(x)).astype(int)


# ═══════════════════════════════════════════════════════════════════════════
# DEFESA 3: VI com guarda de variância zero
# ═══════════════════════════════════════════════════════════════════════════

def variation_of_information(
    x:      np.ndarray,
    y:      np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Variation of Information normalizada ∈ [0, 1].

    VI(X,Y) = H(X|Y) + H(Y|X) = H(X,Y) - I(X;Y)
    VI_norm = VI / H(X,Y)

    DEFESA 3: Se std(X) < 1e-3 ou std(Y) < 1e-3 → retorna 1.0.
    Feature sem variância efetiva não contém informação mútua.
    """
    # Remove NaN E Inf alinhado (FracDiff e log-returns geram inf nas janelas iniciais)
    x_f = x.astype(float)
    y_f = y.astype(float)
    mask = ~(np.isnan(x_f) | np.isnan(y_f) | np.isinf(x_f) | np.isinf(y_f))
    x_f, y_f = x_f[mask], y_f[mask]
    n = len(x_f)

    if n < 20:
        log.warning("vi.too_few_obs", n=n, msg="< 20 obs after NaN/Inf removal")
        return 1.0

    # ── DEFESA 3: variância zero/quase-zero → distância máxima ─────────
    # std < 1e-3 captura hmm_prob_bull (std=0.0003-0.0006) e features
    # verdadeiramente constantes. Feature sem variância efetiva não
    # contém informação mútua com nenhuma outra.
    if np.std(x_f) < NEAR_CONSTANT_THRESH or np.std(y_f) < NEAR_CONSTANT_THRESH:
        return 1.0

    # n_bins adaptativo: max(2, min(10, sqrt(N)))
    bins = max(2, min(n_bins, int(np.sqrt(n))))

    try:
        xd = _safe_discretize(x_f, bins)
        yd = _safe_discretize(y_f, bins)
    except Exception as e:
        log.error("vi.discretize_error", error=str(e), exc_info=True,
                  n=n, bins=bins, x_unique=len(np.unique(x_f)),
                  y_unique=len(np.unique(y_f)))
        return 1.0

    hx  = _entropy(xd)
    hy  = _entropy(yd)

    # Entropia conjunta via codificação de par
    n_y   = int(yd.max()) + 2
    joint = xd * n_y + yd
    hxy   = _entropy(joint)

    mi     = hx + hy - hxy         # Mutual Information
    vi_raw = hx + hy - 2.0 * mi   # VI = H(X,Y) - I(X;Y)

    vi_norm = max(vi_raw / max(hxy, 1e-12), 0.0)
    return float(min(vi_norm, 1.0))


# ═══════════════════════════════════════════════════════════════════════════
# DEFESA 1: Matriz VI Global (aceita DataFrame POOLED)
# ═══════════════════════════════════════════════════════════════════════════

def compute_vi_distance_matrix(
    feature_df: pd.DataFrame,
    n_bins:     int = 10,
) -> pd.DataFrame:
    """
    Matriz de distâncias VI simétrica (n_features × n_features).

    DEFESA 1: Aceita DataFrame POOLED (todos os ativos concatenados).
    Com N=15000+ observações, o KBinsDiscretizer estabiliza.

    DEFESA 3: Features com std < 1e-5 recebem VI=1.0 automático
    (tratado dentro de variation_of_information).

    Returns:
        pd.DataFrame simétrica com VI ∈ [0,1]. Diagonal = 0.
    """
    feats = feature_df.columns.tolist()
    n = len(feats)
    D = np.zeros((n, n))

    # ── SANITIZAÇÃO BRUTAL: Inf e NaN das janelas de inicialização ───────
    # FracDiff, log-returns, e médias móveis geram inf/nan nas primeiras linhas.
    # Um único inf mata KBinsDiscretizer. Tratar ANTES de qualquer cálculo.
    n_before = len(feature_df)
    feature_df = feature_df.replace([np.inf, -np.inf], np.nan).dropna()
    n_after = len(feature_df)
    if n_after < n_before:
        log.info("vi.sanitized",
                 rows_before=n_before, rows_after=n_after,
                 rows_dropped=n_before - n_after,
                 pct_dropped=round(100 * (n_before - n_after) / max(n_before, 1), 1))

    if n_after < 50:
        log.error("vi.insufficient_after_sanitization",
                  n=n_after,
                  msg="< 50 obs after inf/nan removal. Check feature engineering.")
        # Retorna matriz de independência total em vez de crashear
        return pd.DataFrame(np.ones((n, n)) - np.eye(n), index=feats, columns=feats)

    # ── Diagnóstico de tipo por feature ──────────────────────────────────
    feat_types = {}
    zero_var_feats = []
    for f in feats:
        col = feature_df[f].dropna()
        nu = len(col.unique())
        std = float(col.std()) if len(col) > 1 else 0.0

        if std < NEAR_CONSTANT_THRESH:
            feat_types[f] = "ZERO_VAR"
            zero_var_feats.append(f)
        elif nu <= 2:
            feat_types[f] = "binary"
        elif nu <= MAX_UNIQUE_CATEGORICAL:
            feat_types[f] = f"cat({nu})"
        else:
            feat_types[f] = "cont"

    if zero_var_feats:
        log.warning("vi.zero_variance_features",
                    features=zero_var_feats,
                    msg="Distância VI fixada em 1.0 (sem informação mútua)")

    # ── Calcula pares ────────────────────────────────────────────────────
    for i in range(n):
        for j in range(i + 1, n):
            vi = variation_of_information(
                feature_df[feats[i]].values,
                feature_df[feats[j]].values,
                n_bins=n_bins,
            )
            D[i, j] = D[j, i] = vi

    triu = D[np.triu_indices(n, k=1)]
    mean_vi = round(float(triu.mean()), 4) if len(triu) > 0 else 0.0

    log.info("vi_matrix.computed",
             n_features=n,
             n_obs=len(feature_df),
             n_pairs=n * (n - 1) // 2,
             mean_vi=mean_vi,
             feature_types=feat_types,
             zero_var_count=len(zero_var_feats))

    return pd.DataFrame(D, index=feats, columns=feats)


# ═══════════════════════════════════════════════════════════════════════════
# DEFESA 4: Clusterização Hierárquica (Ward linkage + fcluster)
# ═══════════════════════════════════════════════════════════════════════════

def cluster_features(
    vi_matrix:    pd.DataFrame,
    vi_threshold: float = VI_THRESHOLD_DEFAULT,
    save_path:    str | None = None,
) -> dict:
    """
    Clusteriza features via hierarchical clustering (Ward) sobre a matriz VI.

    Ward linkage minimiza a variância intra-cluster.
    fcluster corta o dendrograma em vi_threshold:
        VI < 0.30 → features no mesmo cluster (compartilham >70% informação)
        VI > 0.30 → clusters separados

    O mapeamento é GLOBAL e aplicável a todos os ativos na Fase 4.

    Args:
        vi_matrix:     Matriz VI quadrada (output de compute_vi_distance_matrix).
        vi_threshold:  Corte do dendrograma. Default 0.30.
        save_path:     Se fornecido, salva cluster_map.json neste diretório.

    Returns:
        dict com:
            cluster_map:      {cluster_id: [feature_names]}
            feature_to_cluster: {feature_name: cluster_id}
            n_clusters:       int
            redundant_pairs:  [(f1, f2, vi)]
            status:           "PASS" | "FAIL"
    """
    feats = vi_matrix.columns.tolist()
    n = len(feats)

    if n < 2:
        return {"cluster_map": {1: feats}, "feature_to_cluster": {f: 1 for f in feats},
                "n_clusters": 1, "redundant_pairs": [], "status": "PASS"}

    # Forma condensada para scipy
    dist_condensed = squareform(vi_matrix.values, checks=False)

    # Ward linkage
    link = linkage(dist_condensed, method="ward")

    # Corta o dendrograma no threshold
    cluster_ids = fcluster(link, t=vi_threshold, criterion="distance")

    # Monta cluster_map
    cluster_map: dict[int, list[str]] = {}
    feature_to_cluster: dict[str, int] = {}
    for feat, cid in zip(feats, cluster_ids):
        cid = int(cid)
        cluster_map.setdefault(cid, []).append(feat)
        feature_to_cluster[feat] = cid

    # Pares redundantes (dentro do mesmo cluster, VI < threshold)
    redundant_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if vi_matrix.iloc[i, j] < vi_threshold:
                redundant_pairs.append({
                    "f1": feats[i], "f2": feats[j],
                    "vi": round(float(vi_matrix.iloc[i, j]), 4),
                })

    # Critério: ≥ 2 clusters com > 1 feature OU todas features independentes
    multi_feat_clusters = sum(1 for v in cluster_map.values() if len(v) > 1)
    status = "PASS" if len(cluster_map) >= 2 else "FAIL"

    log.info("vi.cluster_features",
             n_clusters=len(cluster_map),
             multi_feat_clusters=multi_feat_clusters,
             vi_threshold=vi_threshold,
             cluster_sizes={cid: len(fs) for cid, fs in cluster_map.items()},
             n_redundant_pairs=len(redundant_pairs),
             status=status)

    # Serializa se path fornecido
    if save_path:
        out_dir = Path(save_path)
        out_dir.mkdir(parents=True, exist_ok=True)

        # JSON serializable
        artifact = {
            "cluster_map":        {str(k): v for k, v in cluster_map.items()},
            "feature_to_cluster": {f: int(c) for f, c in feature_to_cluster.items()},
            "n_clusters":         len(cluster_map),
            "vi_threshold":       vi_threshold,
            "redundant_pairs":    redundant_pairs,
            "status":             status,
        }
        json_path = out_dir / "cluster_map.json"
        with open(json_path, "w") as f:
            json.dump(artifact, f, indent=2)
        log.info("vi.cluster_map_saved", path=str(json_path))

        # Também salva a matriz VI como CSV
        vi_csv_path = out_dir / "vi_matrix.csv"
        vi_matrix.to_csv(vi_csv_path)
        log.info("vi.matrix_saved", path=str(vi_csv_path))

    return {
        "cluster_map":        cluster_map,
        "feature_to_cluster": feature_to_cluster,
        "n_clusters":         len(cluster_map),
        "redundant_pairs":    redundant_pairs,
        "status":             status,
    }


# ═══════════════════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ═══════════════════════════════════════════════════════════════════════════

def plot_vi_heatmap(
    dist_matrix: pd.DataFrame,
    output_path: str = "vi_heatmap.png",
    title:       str = "VI Distance Matrix\n0=identical | 1=independent",
) -> None:
    """Gera heatmap da matriz VI."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError:
        log.error("vi_heatmap.import_error")
        return
    fig, ax = plt.subplots(figsize=(12, 10))
    sns.heatmap(dist_matrix, annot=True, fmt=".2f", cmap="YlOrRd_r",
                vmin=0.0, vmax=1.0, linewidths=0.5, ax=ax)
    ax.set_title(title, fontsize=13, pad=15)
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    log.info("vi_heatmap.saved", path=output_path)


def stability_check(
    feature_df:    pd.DataFrame,
    n_bins_range:  tuple[int, int] = (8, 12),
) -> dict:
    """Verifica estabilidade da matriz VI variando n_bins."""
    matrices = []
    for nb in range(n_bins_range[0], n_bins_range[1] + 1):
        matrices.append(compute_vi_distance_matrix(feature_df, n_bins=nb))
    stack = np.stack([m.values for m in matrices], axis=0)
    var = float(np.mean(np.var(stack, axis=0)))
    stable = var < 0.05
    log.info("vi.stability_check", mean_variance=round(var, 5), stable=stable)
    return {"mean_variance": round(var, 5), "stable": stable}
