# DESTINO: services/ml_engine/vi_cfi/__init__.py
# v10.10.2: Adicionado cluster_features (DEFESA 4)
from .vi import (
    variation_of_information,
    compute_vi_distance_matrix,
    cluster_features,
    plot_vi_heatmap,
    stability_check,
    VI_THRESHOLD_DEFAULT,
    NEAR_CONSTANT_THRESH,
)
from .cfi import (
    clustered_feature_importance,
    describe_clusters,
    CFIResult,
    ClusterInfo,
)

__all__ = [
    "variation_of_information",
    "compute_vi_distance_matrix",
    "cluster_features",
    "plot_vi_heatmap",
    "stability_check",
    "VI_THRESHOLD_DEFAULT",
    "NEAR_CONSTANT_THRESH",
    "clustered_feature_importance",
    "describe_clusters",
    "CFIResult",
    "ClusterInfo",
]
