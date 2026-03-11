# =============================================================================
# DESTINO: services/ml_engine/vi_cfi/cfi.py
# Clustered Feature Importance (CFI) com Variation of Information.
#
# v10.10.2 REFACTOR:
#   - Aceita cluster_map PRÉ-COMPUTADO de vi.cluster_features()
#   - Remove linkage/fcluster inline (agora em vi.py, DEFESA 4)
#   - Aceita vi_matrix PRÉ-COMPUTADA de vi.compute_vi_distance_matrix()
#   - Ambos calculados sobre dataset POOLED (DEFESA 1)
#
# CINCO PASSOS (Parte 6):
#   1. Treinar RF base
#   2. Receber clusters pré-computados (via vi.cluster_features())
#   3. Importância por CLUSTER (permutação conjunta) — evita double-counting
#   4. Importância within-cluster (permutação individual dentro do cluster)
#   5. Importância final = cluster_imp × within_cluster_imp
#
# CRITÉRIO DE REJEIÇÃO (Parte 15):
#   < 3 clusters com imp_mean > 0.002 → sem edge. Redesenhar features.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
import structlog

from .vi import compute_vi_distance_matrix, cluster_features, VI_THRESHOLD_DEFAULT

log = structlog.get_logger(__name__)

N_REPEATS_CLUSTER = 30
N_REPEATS_WITHIN  = 20


@dataclass
class ClusterInfo:
    cluster_id:      int
    features:        list[str]
    vi_internal_avg: float
    imp_mean:        float
    imp_std:         float


@dataclass
class CFIResult:
    """Resultado completo do CFI com VI."""
    feature_importance:  pd.Series
    clusters:            dict[int, ClusterInfo]
    vi_distance_matrix:  pd.DataFrame
    vi_threshold_used:   float
    n_clusters:          int
    auc_baseline:        float
    status:              str


def clustered_feature_importance(
    X_train:           np.ndarray,
    y_train:           np.ndarray,
    X_val:             np.ndarray,
    y_val:             np.ndarray,
    feature_names:     list[str],
    vi_threshold:      float = VI_THRESHOLD_DEFAULT,
    n_bins:            int   = 10,
    n_repeats_cluster: int   = N_REPEATS_CLUSTER,
    n_repeats_within:  int   = N_REPEATS_WITHIN,
    rf_params:         Optional[dict] = None,
    precomputed_vi_matrix:  Optional[pd.DataFrame] = None,
    precomputed_cluster_map: Optional[dict[int, list[str]]] = None,
) -> CFIResult:
    """
    CFI completo com VI como métrica de distância (Parte 6).

    v10.10.2: Aceita vi_matrix e cluster_map pré-computados sobre dataset
    POOLED (cross-asset). Se não fornecidos, calcula internamente
    (backward compatible, mas NÃO recomendado — use pooled).

    Args:
        X_train, y_train:           Dados de treino.
        X_val, y_val:               Dados de validação (permutação OOS).
        feature_names:              Nomes das features.
        vi_threshold:               Corte do dendrograma. Default 0.30.
        precomputed_vi_matrix:      Matriz VI (de vi.compute_vi_distance_matrix).
        precomputed_cluster_map:    {cluster_id: [feature_names]} (de vi.cluster_features).
    """
    if len(feature_names) != X_train.shape[1]:
        raise ValueError(
            f"feature_names={len(feature_names)}, X_train={X_train.shape[1]}")

    # ── PASSO 1: RF base ──────────────────────────────────────────────────
    default_rf = {
        "n_estimators":     500,
        "max_depth":        6,
        "min_samples_leaf": 20,
        "max_features":     "sqrt",
        "class_weight":     "balanced",
        "random_state":     42,
        "n_jobs":           -1,
    }
    params = {**default_rf, **(rf_params or {})}
    rf = RandomForestClassifier(**params)
    rf.fit(X_train, y_train)

    auc_baseline = roc_auc_score(y_val, rf.predict_proba(X_val)[:, 1])
    log.info("cfi.rf_baseline", auc=round(auc_baseline, 4))

    # ── PASSO 2: Clusters (pré-computados OU calculados aqui) ────────────
    if precomputed_vi_matrix is not None:
        dist_mat = precomputed_vi_matrix
    else:
        feat_df = pd.DataFrame(X_train, columns=feature_names)
        dist_mat = compute_vi_distance_matrix(feat_df, n_bins=n_bins)

    if precomputed_cluster_map is not None:
        clusters_raw = precomputed_cluster_map
    else:
        cl_result = cluster_features(dist_mat, vi_threshold=vi_threshold)
        clusters_raw = cl_result["cluster_map"]

    # Filtra clusters: só features que existem em feature_names
    # (cluster_map pode ter features extras de outros ativos)
    feature_set = set(feature_names)
    filtered_clusters: dict[int, list[str]] = {}
    for cid, feats in clusters_raw.items():
        present = [f for f in feats if f in feature_set]
        if present:
            filtered_clusters[int(cid)] = present

    log.info("cfi.clusters_applied",
             n_clusters=len(filtered_clusters),
             vi_threshold=vi_threshold,
             distribution={cid: len(fs) for cid, fs in filtered_clusters.items()})

    # ── PASSO 3: Importância por CLUSTER (permutação conjunta) ────────────
    rng = np.random.RandomState(42)
    clust_imp: dict[int, dict] = {}

    for cid, feats in filtered_clusters.items():
        idx = [feature_names.index(f) for f in feats]
        X_perm = X_val.copy()
        drops = []

        for _ in range(n_repeats_cluster):
            X_perm[:, idx] = rng.permutation(X_val[:, idx])
            auc_perm = roc_auc_score(y_val, rf.predict_proba(X_perm)[:, 1])
            drops.append(auc_baseline - auc_perm)
            X_perm[:, idx] = X_val[:, idx]

        # VI interno médio (diagnóstico)
        if len(feats) > 1:
            vi_internal = float(np.mean([
                dist_mat.loc[a, b]
                for a in feats for b in feats
                if a != b and a in dist_mat.index and b in dist_mat.columns
            ])) if all(f in dist_mat.index for f in feats) else 0.0
        else:
            vi_internal = 0.0

        clust_imp[cid] = {
            "features":        feats,
            "imp_mean":        float(np.mean(drops)),
            "imp_std":         float(np.std(drops)),
            "vi_internal_avg": round(vi_internal, 4),
        }

    # ── PASSO 4: Importância within-cluster ───────────────────────────────
    within: dict[str, float] = {}

    for cid, info in clust_imp.items():
        feats = info["features"]
        if len(feats) == 1:
            within[feats[0]] = 1.0
            continue

        idx = [feature_names.index(f) for f in feats]
        rf_sub = RandomForestClassifier(
            n_estimators=200, max_depth=4,
            random_state=42, n_jobs=-1,
        )
        rf_sub.fit(X_train[:, idx], y_train)

        perm = permutation_importance(
            rf_sub, X_val[:, idx], y_val,
            n_repeats=n_repeats_within, random_state=42,
        )
        total = max(float(perm.importances_mean.sum()), 1e-10)
        for feat, imp in zip(feats, perm.importances_mean):
            within[feat] = float(max(imp, 0.0)) / total

    # ── PASSO 5: Importância final = cluster_imp × within_cluster_imp ─────
    final_imp: dict[str, float] = {}
    for cid, info in clust_imp.items():
        ci = max(info["imp_mean"], 0.0)
        for feat in info["features"]:
            final_imp[feat] = ci * within.get(feat, 0.0)

    imp_series = pd.Series(final_imp).sort_values(ascending=False)

    # ── ClusterInfo objects ───────────────────────────────────────────────
    cluster_objects = {
        cid: ClusterInfo(
            cluster_id=cid,
            features=info["features"],
            vi_internal_avg=info["vi_internal_avg"],
            imp_mean=info["imp_mean"],
            imp_std=info["imp_std"],
        )
        for cid, info in clust_imp.items()
    }

    # ── Critérios de rejeição (Parte 15) ──────────────────────────────────
    n_meaningful = sum(1 for i in clust_imp.values() if i["imp_mean"] > 0.002)

    if n_meaningful < 3:
        status = "FAIL"
        log.warning("cfi.insufficient_clusters", n_meaningful=n_meaningful)
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
    """Imprime sumário legível dos clusters."""
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
        print(f"  VI interno: {info.vi_internal_avg:.3f}")

    print(f"\nTop 5 features:")
    for feat, imp in result.feature_importance.head(5).items():
        print(f"  {feat:<30} {imp:.6f}")
