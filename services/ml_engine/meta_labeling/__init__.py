# DESTINO: services/ml_engine/meta_labeling/__init__.py
# Versão atualizada — Passo 4 adiciona isotonic_calibration
from .uniqueness import (
    compute_label_uniqueness,
    compute_effective_n,
    compute_meta_sample_weights,
    N_EFF_LOGISTIC_ONLY,
    N_EFF_LGBM_STRICT,
)
from .pbma_purged import (
    generate_pbma_purged_kfold,
    audit_orthogonality,
    MODEL_PARAMS,
)
from .cpcv import run_cpcv, META_FEATURES_V107
from .isotonic_calibration import (
    fit_isotonic_calibrator,
    run_isotonic_walk_forward,
    calibration_diagnostics,
    DEFAULT_HALFLIFE,
    MIN_CALIB_OBS,
    RETRAIN_FREQ_DAYS,
)

__all__ = [
    "compute_label_uniqueness",
    "compute_effective_n",
    "compute_meta_sample_weights",
    "N_EFF_LOGISTIC_ONLY",
    "N_EFF_LGBM_STRICT",
    "generate_pbma_purged_kfold",
    "audit_orthogonality",
    "run_cpcv",
    "META_FEATURES_V107",
    "MODEL_PARAMS",
    "fit_isotonic_calibrator",
    "run_isotonic_walk_forward",
    "calibration_diagnostics",
    "DEFAULT_HALFLIFE",
    "MIN_CALIB_OBS",
    "RETRAIN_FREQ_DAYS",
]
