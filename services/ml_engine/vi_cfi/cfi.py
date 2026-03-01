# =============================================================================
# DESTINO: services/ml_engine/vi_cfi/cfi.py
# Clustered Feature Importance (CFI) com Variation of Information.
# Detecta redundância não-linear que Pearson não captura.
#
# CINCO PASSOS (Parte 6):
# 1. Treinar RF base
# 2. Calcular matriz VI e clusterizar (ward linkage)
# 3. Importância por CLUSTER (permutação conjunta) — evita double-counting
# 4. Importância within-cluster (permutação individual dentro do cluster)
# 5. Importância final = cluster_imp × within_cluster_imp
#
# CRITÉRIO DE REJEIÇÃO (Parte 15):
#   < 3 clusters com imp_mean > 0.002 → sem edge. Redesenhar features.
#   Todas features com VI > 0.45 → sem estrutura de cluster.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
import structlog

from .vi import compute_vi_distance_matrix

log = structlog.get_logger(__name__)

VI_THRESHOLD_DEFAULT = 0.30    # features com VI < 0.30 → mesmo cluster
N_REPEATS_CLUSTER    = 30      # permutações por cluster (importância por cluster)
N_REPEATS_WITHIN     = 20      # permutações within-cluster


@dataclass
class ClusterInfo:
    cluster_id:     int
    features:       list[str]
    vi_internal_avg: float       # VI médio dentro do cluster (diagnóstico)
    imp_mean:       float        # importância média do cluster
    imp_std:        float


@dataclass
class CFIResult:
    """Resultado completo do CFI com VI."""
    feature_importance:  pd.Series          # importância final ordenada
    clusters:            dict[int, ClusterInfo]
    vi_distance_matrix:  pd.DataFrame
    vi_threshold_used:   float
    n_clusters:          int
    auc_baseline:        float              # AUC do RF sem permutação
    status:              str               # PASS / WARN / FAIL


def clustered_feature_importance(
    X_train:        np.ndarray,
    y_train:        np.ndarray,
    X_val:          np.ndarray,
    y_val:          np.ndarray,
    feature_names:  list[str],
    vi_threshold:   float = VI_THRESHOLD_DEFAULT,
    n_bins:         int   = 10,
    n_repeats_cluster: int = N_REPEATS_CLUSTER,
    n_repeats_within:  int = N_REPEATS_WITHIN,
    rf_params:      Optional[dict] = None,
) -> CFIResult:
    """
    CFI completo com VI como métrica de distância (Parte 6).

    O clustering agrupa features informativamente redundantes para o LightGBM —
    não apenas linearmente correlacionadas (Pearson). Isso evita que o RF
    distribua artificialmente importância entre features redundantes.

    Args:
        X_train, y_train: Dados de treino.
        X_val, y_val:     Dados de validação (para permutação OOS).
        feature_names:    Nomes das features (len = X_train.shape[1]).
        vi_threshold:     VI máxima para agrupar no mesmo cluster.
                          0.30 = features compartilham > 70% da informação.
                          Calibrar com plot_vi_heatmap() ANTES de executar.
        n_bins:           Bins para discretização da VI.
        n_repeats_cluster: Repetições de permutação por cluster.
        n_repeats_within:  Repetições de permutação within-cluster.
        rf_params:         Parâmetros do RandomForestClassifier. Default SNIPER.

    Returns:
        CFIResult com importância por feature, clusters e diagnósticos.
    """
    if len(feature_names) != X_train.shape[1]:
        raise ValueError(
            f"feature_names tem {len(feature_names)} itens, "
            f"X_train tem {X_train.shape[1]} colunas."
        )

    # ── PASSO 1: RF base ──────────────────────────────────────────────────
    default_rf = {
        "n_estimators":    500,
        "max_depth":       6,
        "min_samples_leaf": 20,
        "max_features":    "sqrt",
        "class_weight":    "balanced",
        "random_state":    42,
        "n_jobs":          -1,
    }
    params = {**default_rf, **(rf_params or {})}
    rf     = RandomForestClassifier(**params)
    rf.fit(X_train, y_train)

    auc_baseline = roc_auc_score(y_val, rf.predict_proba(X_val)[:, 1])
    log.info("cfi.rf_baseline", auc=round(auc_baseline, 4))

    # ── PASSO 2: Matriz VI e clustering hierárquico ───────────────────────
    feat_df    = pd.DataFrame(X_train, columns=feature_names)
    dist_mat   = compute_vi_distance_matrix(feat_df, n_bins=n_bins)

    # Converte para forma condensada (scipy exige)
    dist_condensed = squareform(dist_mat.values, checks=False)
    link           = linkage(dist_condensed, method="ward")
    cluster_ids    = fcluster(link, t=vi_threshold, criterion="distance")

    # Mapeia feature → cluster
    clusters_raw: dict[int, list[str]] = {}
    for feat, cid in zip(feature_names, cluster_ids):
        clusters_raw.setdefault(int(cid), []).append(feat)

    log.info("cfi.clusters_formed",
             n_clusters=len(clusters_raw),
             vi_threshold=vi_threshold,
             distribution={cid: len(feats)
                           for cid, feats in clusters_raw.items()})

    # ── PASSO 3: Importância por CLUSTER (permutação conjunta) ────────────
    rng       = np.random.RandomState(42)
    clust_imp: dict[int, dict] = {}

    for cid, feats in clusters_raw.items():
        idx      = [feature_names.index(f) for f in feats]
        X_perm   = X_val.copy()
        drops    = []

        for _ in range(n_repeats_cluster):
            # Permuta todas as features do cluster juntas
            X_perm[:, idx] = rng.permutation(X_val[:, idx])
            auc_perm        = roc_auc_score(
                y_val, rf.predict_proba(X_perm)[:, 1]
            )
            drops.append(auc_baseline - auc_perm)
            X_perm[:, idx] = X_val[:, idx]   # restaura

        # VI interno médio (diagnóstico: clusters coesos têm VI baixo)
        if len(feats) > 1:
            vi_internal = float(np.mean([
                dist_mat.loc[a, b]
                for a in feats for b in feats if a != b
            ]))
        else:
            vi_internal = 0.0

        clust_imp[cid] = {
            "features":        feats,
            "imp_mean":        float(np.mean(drops)),
            "imp_std":         float(np.std(drops)),
            "vi_internal_avg": round(vi_internal, 4),
        }
        log.debug("cfi.cluster_importance",
                  cluster_id=cid, features=feats,
                  imp_mean=round(float(np.mean(drops)), 5),
                  vi_internal=round(vi_internal, 4))

    # ── PASSO 4: Importância within-cluster ───────────────────────────────
    within: dict[str, float] = {}

    for cid, info in clust_imp.items():
        feats = info["features"]

        if len(feats) == 1:
            within[feats[0]] = 1.0
            continue

        idx    = [feature_names.index(f) for f in feats]
        rf_sub = RandomForestClassifier(
            n_estimators=200,
            max_depth=4,
            random_state=42,
            n_jobs=-1,
        )
        rf_sub.fit(X_train[:, idx], y_train)

        perm   = permutation_importance(
            rf_sub, X_val[:, idx], y_val,
            n_repeats=n_repeats_within,
            random_state=42,
        )
        total  = max(float(perm.importances_mean.sum()), 1e-10)
        for feat, imp in zip(feats, perm.importances_mean):
            within[feat] = float(max(imp, 0.0)) / total

    # ── PASSO 5: Importância final = cluster_imp × within_cluster_imp ─────
    final_imp: dict[str, float] = {}
    for cid, info in clust_imp.items():
        ci = max(info["imp_mean"], 0.0)
        for feat in info["features"]:
            final_imp[feat] = ci * within.get(feat, 0.0)

    imp_series = pd.Series(final_imp).sort_values(ascending=False)

    # ── Monta ClusterInfo objects ─────────────────────────────────────────
    cluster_objects: dict[int, ClusterInfo] = {
        cid: ClusterInfo(
            cluster_id=cid,
            features=info["features"],
            vi_internal_avg=info["vi_internal_avg"],
            imp_mean=info["imp_mean"],
            imp_std=info["imp_std"],
        )
        for cid, info in clust_imp.items()
    }

    # ── Checklist Parte 15: critérios de rejeição ─────────────────────────
    n_meaningful = sum(
        1 for info in clust_imp.values() if info["imp_mean"] > 0.002
    )
    all_vi_high  = all(
        dist_mat.values[i, j] > 0.45
        for i in range(len(feature_names))
        for j in range(i + 1, len(feature_names))
    )

    if n_meaningful < 3:
        status = "FAIL"
        log.warning("cfi.insufficient_clusters",
                    n_meaningful=n_meaningful,
                    msg="< 3 clusters com imp_mean > 0.002. Redesenhar features.")
    elif all_vi_high:
        status = "WARN"
        log.warning("cfi.no_cluster_structure",
                    msg="Todas features com VI > 0.45. Sem estrutura de cluster.")
    else:
        status = "PASS"

    log.info("cfi.complete",
             n_clusters=len(cluster_objects),
             n_meaningful=n_meaningful,
             status=status,
             top_features=imp_series.head(3).index.tolist())

    return CFIResult(
        feature_importance=imp_series,
        clusters=cluster_objects,
        vi_distance_matrix=dist_mat,
        vi_threshold_used=vi_threshold,
        n_clusters=len(cluster_objects),
        auc_baseline=auc_baseline,
        status=status,
    )


def describe_clusters(result: CFIResult) -> None:
    """Imprime sumário legível dos clusters para diagnóstico."""
    print(f"\n{'='*60}")
    print(f"CFI com VI — {result.n_clusters} clusters "
          f"(threshold={result.vi_threshold_used})")
    print(f"AUC baseline RF: {result.auc_baseline:.4f}")
    print(f"Status: {result.status}")
    print(f"{'='*60}")

    for cid, info in sorted(result.clusters.items(),
                             key=lambda x: -x[1].imp_mean):
        bar = "█" * max(0, int(info.imp_mean * 500))
        print(f"\nCluster {cid} | imp={info.imp_mean:.5f} {bar}")
        print(f"  Features: {info.features}")
        print(f"  VI interno médio: {info.vi_internal_avg:.3f}")

    print(f"\nTop 5 features:")
    for feat, imp in result.feature_importance.head(5).items():
        print(f"  {feat:<30} {imp:.6f}")
