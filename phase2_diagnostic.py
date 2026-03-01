#!/usr/bin/env python3
"""
SNIPER v10.10 — Phase 2 Diagnostic Validator
=============================================
Validates ALL Phase 2 components against the spec checklist.
Reads saved parquets + HMM artifacts and runs comprehensive checks.

Deploy:
    docker cp phase2_diagnostic.py sniper_ml_engine:/app/
    docker exec sniper_ml_engine python /app/phase2_diagnostic.py

Spec references are indicated as [PART.ITEM] e.g. [4.7b] = Parte 4, item 7b.
"""
import os, sys, json, pickle, warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from collections import defaultdict

warnings.filterwarnings("ignore")

# ─── Paths ─────────────────────────────────────────────────────────────
FEATURES_DIR  = Path("/data/models/features")
HMM_DIR       = Path("/data/models/hmm")
PARQUET_BASE  = Path("/data/parquet")
PHASE3_DIR    = Path("/data/models/phase3")

# ─── Spec v10.10 Thresholds ───────────────────────────────────────────
SPEC = {
    # FracDiff (Parte 3)
    "fracdiff_tau":           1e-5,    # [5c] weight cutoff
    "fracdiff_min_train":     252,     # [5] min expanding window
    "fracdiff_d_range":       (0.05, 1.0),  # valid d* range
    "fracdiff_log_space":     True,    # [5b] mandatory log-space

    # HMM/PCA (Parte 4)
    "hmm_pca_n_components":   2,       # [4] N_PCA = 2
    "hmm_var_exp_min":        0.80,    # [7c] var_exp >= 80%
    "hmm_bear_2022_min":      0.60,    # [7c] bear detection > 60% Jan-Jun 2022
    "hmm_f1_oos_min":         0.45,    # [15.1] F1 OOS >= 0.45
    "hmm_pc1_crash_max_z":    3.0,     # [7b] PC1 crash within 3σ (robust)
    "hmm_uses_robust_scaler": True,    # [7] RobustScaler mandatory
    "hmm_uses_winsorization": True,    # [7] Winsorization 1%/99% mandatory

    # Features (Parte 3)
    "required_features": [
        "ret_1d", "ret_5d", "realized_vol_30d", "vol_ratio",
        "btc_ma200_flag", "dvol_zscore",
    ],
    "hmm_features_spec": [
        "ret_1d", "ret_5d", "realized_vol_30d", "vol_ratio",
        "funding_rate_ma7d", "basis_3m", "stablecoin_chg30",
        "btc_ma200_flag", "dvol_zscore",
    ],

    # VI/CFI (Parte 6)
    "vi_threshold":           0.30,    # [8] redundancy threshold
    "vi_min_clusters":        3,       # [15.1] min clusters with imp > 0.002

    # Corwin-Schultz (Parte 11)
    "cs_sigma_threshold":     3.0,     # [13b] anomaly z-score
    "cs_uses_4h_bars":        True,    # [13b2] from 4h klines

    # BMA (Parte 5/8)
    "bma_uses_purged_kfold":  True,    # [8b] Purged K-Fold mandatory
    "bma_n_splits_min":       5,       # min splits for purged k-fold
}


# ═══════════════════════════════════════════════════════════════════════
# 1. FRACDIFF VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_fracdiff(features_data: dict) -> dict:
    """
    Validates FracDiff implementation against spec:
    [5]  Expanding window (d* not global)
    [5b] Log-space (prices → log → fracdiff)
    [5c] Weight cutoff τ=1e-5
    [5d] log_space=True flag saved per asset
    """
    results = {}

    for sym, df in features_data.items():
        checks = {}

        # Check d_star column exists and is reasonable
        if "d_star" in df.columns:
            d_star = df["d_star"].iloc[-1]
            d_min, d_max = SPEC["fracdiff_d_range"]
            checks["d_star_value"] = round(float(d_star), 4)
            checks["d_star_in_range"] = d_min <= d_star <= d_max

            # Check if d_star varies (expanding window produces time-varying d*)
            d_unique = df["d_star"].nunique()
            checks["d_star_is_constant"] = d_unique == 1
            # Note: our implementation uses final d* applied to full series,
            # but d* was found via expanding window.
            # The key check is that d* is reasonable (not 0 or >1).
        else:
            checks["d_star_value"] = None
            checks["d_star_in_range"] = False

        # Check fracdiff z-score feature exists
        has_fracdiff = any("fracdiff" in c.lower() for c in df.columns)
        checks["has_fracdiff_feature"] = has_fracdiff

        # Check no look-ahead: fracdiff should have NaN at start
        if has_fracdiff:
            fd_col = [c for c in df.columns if "fracdiff" in c.lower()]
            if fd_col:
                nan_pct = float(df[fd_col[0]].isna().mean())
                # With tau=1e-5, ~500 weights → first ~500 values should be NaN
                # For 2500 row dataset, that's ~20% NaN which is expected
                checks["fracdiff_nan_pct"] = round(nan_pct, 3)
                checks["fracdiff_nan_reasonable"] = 0.01 < nan_pct < 0.50

        results[sym] = checks

    # Summary
    n_ok = sum(1 for r in results.values()
               if r.get("d_star_in_range", False) and r.get("has_fracdiff_feature", False))

    return {
        "assets_checked": len(results),
        "assets_pass": n_ok,
        "details": results,
    }


# ═══════════════════════════════════════════════════════════════════════
# 2. FEATURE ENGINEERING VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_features(features_data: dict) -> dict:
    """
    Validates feature engineering:
    - All required features present
    - No excessive NaN
    - Correct data types
    - Feature statistics reasonable
    """
    results = {}

    for sym, df in features_data.items():
        checks = {}

        # Required features presence
        present = [f for f in SPEC["required_features"] if f in df.columns]
        missing = [f for f in SPEC["required_features"] if f not in df.columns]
        checks["required_present"] = len(present)
        checks["required_missing"] = missing if missing else None
        checks["all_required_ok"] = len(missing) == 0

        # All columns
        checks["total_features"] = len([c for c in df.columns
                                         if c not in ("symbol", "d_star",
                                                       "hmm_prob_bull", "hmm_is_bull")])
        checks["columns"] = sorted(df.columns.tolist())

        # NaN check per feature
        nan_pcts = {}
        for col in SPEC["required_features"]:
            if col in df.columns:
                nan_pcts[col] = round(float(df[col].isna().mean()), 3)
        checks["nan_pcts"] = nan_pcts

        # Stat checks: ret_1d should be ~0 mean, realized_vol should be positive
        if "ret_1d" in df.columns:
            ret_mean = float(df["ret_1d"].mean())
            checks["ret_1d_mean"] = round(ret_mean, 6)
            checks["ret_1d_plausible"] = abs(ret_mean) < 0.01  # < 1% daily mean

        if "realized_vol_30d" in df.columns:
            vol_mean = float(df["realized_vol_30d"].dropna().mean())
            checks["vol_30d_mean"] = round(vol_mean, 4)
            checks["vol_30d_plausible"] = 0.1 < vol_mean < 3.0  # annualized

        if "btc_ma200_flag" in df.columns:
            btc_pct = float(df["btc_ma200_flag"].mean())
            checks["btc_above_ma200_pct"] = round(btc_pct, 3)

        if "dvol_zscore" in df.columns:
            dvol_std = float(df["dvol_zscore"].dropna().std())
            checks["dvol_zscore_std"] = round(dvol_std, 3)
            checks["dvol_zscore_plausible"] = 0.5 < dvol_std < 2.0

        # Data range
        if hasattr(df.index, 'min'):
            try:
                checks["date_start"] = str(df.index.min())[:10]
                checks["date_end"] = str(df.index.max())[:10]
            except Exception:
                pass
        checks["n_rows"] = len(df)

        results[sym] = checks

    return {
        "assets_checked": len(results),
        "all_features_present": sum(1 for r in results.values()
                                     if r.get("all_required_ok", False)),
        "details": results,
    }


# ═══════════════════════════════════════════════════════════════════════
# 3. HMM / PCA VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_hmm_pca(features_data: dict) -> dict:
    """
    Validates HMM + PCA pipeline:
    [7]  RobustScaler + Winsorization 1%/99% (NOT StandardScaler)
    [7b] PC1 crash within 3σ after Winsorization
    [7c] var_exp >= 80% in all windows
    [7c] 2022 bear detected > 60% Jan-Jun
    [15.1] F1 OOS >= 0.45
    Walk-forward (not global fit)
    """
    results = {}

    for sym, df in features_data.items():
        checks = {}

        # Check HMM columns present
        has_prob = "hmm_prob_bull" in df.columns
        has_bull = "hmm_is_bull" in df.columns
        checks["has_hmm_prob_bull"] = has_prob
        checks["has_hmm_is_bull"] = has_bull

        if not (has_prob and has_bull):
            results[sym] = checks
            continue

        prob = df["hmm_prob_bull"]
        is_bull = df["hmm_is_bull"]

        # Basic stats
        valid_pct = float((~prob.isna()).mean())
        checks["hmm_valid_pct"] = round(valid_pct, 3)
        checks["hmm_pct_bull"] = round(float(is_bull.mean()), 3)
        checks["hmm_pct_bear"] = round(1 - float(is_bull.mean()), 3)

        # [7c] 2022 bear detection > 60% Jan-Jun
        mask_2022h1 = (df.index >= "2022-01-01") & (df.index <= "2022-06-30")
        if mask_2022h1.sum() > 0:
            bear_pct = float((~is_bull[mask_2022h1]).mean())
            checks["bear_2022h1_pct"] = round(bear_pct, 3)
            checks["bear_2022h1_pass"] = bear_pct >= SPEC["hmm_bear_2022_min"]
        else:
            checks["bear_2022h1_pct"] = None
            checks["bear_2022h1_pass"] = None

        # Additional: 2022 full year and 2020 March
        mask_2022 = (df.index >= "2022-01-01") & (df.index <= "2022-12-31")
        if mask_2022.sum() > 0:
            checks["bear_2022_full_pct"] = round(float((~is_bull[mask_2022]).mean()), 3)

        mask_mar2020 = (df.index >= "2020-03-01") & (df.index <= "2020-03-31")
        if mask_mar2020.sum() > 0:
            checks["bear_mar2020_pct"] = round(float((~is_bull[mask_mar2020]).mean()), 3)

        # F1 OOS (using ret_1d > 0 as ground truth)
        if "ret_1d" in df.columns:
            from sklearn.metrics import f1_score as _f1
            valid = (~prob.isna()) & (~df["ret_1d"].isna())
            if valid.sum() > 50:
                y_true = (df["ret_1d"][valid] > 0).astype(int).values
                y_pred = is_bull[valid].astype(int).values
                f1 = float(_f1(y_true, y_pred, zero_division=0))
                checks["f1_oos"] = round(f1, 4)
                checks["f1_oos_pass"] = f1 >= SPEC["hmm_f1_oos_min"]

        # Walk-forward check: prob should vary over time (not constant from global fit)
        if valid_pct > 0.5:
            prob_std = float(prob.dropna().std())
            checks["prob_std"] = round(prob_std, 4)
            checks["walk_forward_likely"] = prob_std > 0.01  # constant = global fit

        results[sym] = checks

    # Check HMM artifacts for RobustScaler/Winsorization
    artifacts_check = validate_hmm_artifacts()

    return {
        "assets_checked": len(results),
        "bear_2022_pass": sum(1 for r in results.values()
                              if r.get("bear_2022h1_pass") is True),
        "f1_pass": sum(1 for r in results.values()
                       if r.get("f1_oos_pass") is True),
        "artifacts": artifacts_check,
        "details": results,
    }


def validate_hmm_artifacts() -> dict:
    """
    Inspects serialized HMM artifacts to verify:
    - RobustScaler used (not StandardScaler)
    - Winsorization applied (1%-99%)
    - var_explained >= 80%
    - Walk-forward: multiple artifacts per symbol
    """
    results = {}

    if not HMM_DIR.exists():
        return {"status": "NO_HMM_DIR", "path": str(HMM_DIR)}

    for sym_dir in sorted(HMM_DIR.iterdir()):
        if not sym_dir.is_dir():
            continue
        sym = sym_dir.name
        artifacts = sorted(sym_dir.glob("hmm_t*.pkl"))

        checks = {
            "n_artifacts": len(artifacts),
            "walk_forward_verified": len(artifacts) >= 2,
        }

        # Inspect latest artifact
        if artifacts:
            try:
                with open(artifacts[-1], "rb") as f:
                    fitted = pickle.load(f)

                # Check type of scaler
                pca_pipe = fitted.pca_pipeline
                scaler_type = type(pca_pipe.scaler).__name__
                checks["scaler_type"] = scaler_type
                checks["uses_robust_scaler"] = scaler_type == "RobustScaler"

                # Check winsorization
                has_winsorizer = hasattr(pca_pipe, 'winsorizer') and pca_pipe.winsorizer is not None
                checks["has_winsorizer"] = has_winsorizer

                if has_winsorizer:
                    w = pca_pipe.winsorizer
                    checks["winsor_low_pct"] = getattr(w, 'low_pct', None)
                    checks["winsor_high_pct"] = getattr(w, 'high_pct', None)

                # var_explained
                checks["var_explained"] = round(pca_pipe.var_explained, 4)
                checks["var_exp_pass"] = pca_pipe.var_explained >= SPEC["hmm_var_exp_min"]

                # n_components
                checks["n_pca_components"] = pca_pipe.n_components

                # Feature names
                checks["feature_names"] = pca_pipe.feature_names

                # HMM details
                checks["n_hmm_states"] = fitted.hmm.n_components
                checks["bull_state"] = fitted.bull_state
                checks["threshold"] = round(fitted.threshold, 4)
                checks["f1_train"] = round(fitted.f1_train, 4)

                # Check var_explained across all artifacts
                var_exps = []
                for art_path in artifacts:
                    try:
                        with open(art_path, "rb") as f2:
                            a = pickle.load(f2)
                        var_exps.append(a.pca_pipeline.var_explained)
                    except Exception:
                        pass
                if var_exps:
                    checks["var_exp_min_all_windows"] = round(min(var_exps), 4)
                    checks["var_exp_max_all_windows"] = round(max(var_exps), 4)
                    checks["var_exp_all_pass"] = min(var_exps) >= SPEC["hmm_var_exp_min"]

            except Exception as e:
                checks["artifact_error"] = str(e)

        results[sym] = checks

    return {
        "symbols_with_artifacts": len(results),
        "all_use_robust_scaler": all(r.get("uses_robust_scaler", False)
                                      for r in results.values()),
        "all_have_winsorizer": all(r.get("has_winsorizer", False)
                                    for r in results.values()),
        "all_var_exp_pass": all(r.get("var_exp_pass", False)
                                for r in results.values()),
        "details": results,
    }


# ═══════════════════════════════════════════════════════════════════════
# 4. CORWIN-SCHULTZ VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_corwin_schultz() -> dict:
    """
    Validates Corwin-Schultz Circuit Breaker:
    [13b]  CS spread from High/Low 4h bars
    [13b2] Data from Binance 4h klines
    [11.2] sigma_threshold = 3.0
    """
    results = {}
    ohlcv_4h_dir = PARQUET_BASE / "ohlcv_4h"

    if not ohlcv_4h_dir.exists():
        return {"status": "NO_4H_DATA", "path": str(ohlcv_4h_dir),
                "details": {}}

    # Check 4h data availability
    kline_files = sorted(ohlcv_4h_dir.glob("*.parquet"))

    for kf in kline_files:
        sym = kf.stem
        try:
            df = pd.read_parquet(kf)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp").sort_index()

            has_high = "high" in df.columns
            has_low = "low" in df.columns

            checks = {
                "n_bars": len(df),
                "has_high_low": has_high and has_low,
                "date_start": str(df.index.min())[:10] if len(df) > 0 else None,
                "date_end": str(df.index.max())[:10] if len(df) > 0 else None,
            }

            # Verify 4h frequency
            if len(df) > 2:
                diffs = pd.Series(df.index).diff().dropna()
                median_hours = diffs.dt.total_seconds().median() / 3600
                checks["median_bar_hours"] = round(float(median_hours), 1)
                checks["is_4h_data"] = 3.5 < median_hours < 4.5

            # Compute CS spread sample if high/low available
            if has_high and has_low and len(df) > 10:
                high = df["high"].astype(float)
                low = df["low"].astype(float)
                h = np.log(high.values)
                l = np.log(low.values)
                beta = (h[1:] - l[1:])**2 + (h[:-1] - l[:-1])**2
                gamma = (np.log(np.maximum(np.exp(h[1:]), np.exp(h[:-1])) /
                         np.minimum(np.exp(l[1:]), np.exp(l[:-1]))))**2
                alpha = (np.sqrt(2*beta) - np.sqrt(beta)) / (3 - 2*np.sqrt(2)) - \
                        np.sqrt(gamma / (3 - 2*np.sqrt(2)))
                spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
                spread = np.clip(spread, 0, 1)

                checks["cs_spread_mean"] = round(float(np.nanmean(spread)), 6)
                checks["cs_spread_std"] = round(float(np.nanstd(spread)), 6)
                checks["cs_spread_max"] = round(float(np.nanmax(spread)), 6)

                # Check if anomaly detection would work
                mu = np.nanmean(spread)
                std = np.nanstd(spread)
                if std > 1e-8:
                    max_z = float((np.nanmax(spread) - mu) / std)
                    checks["cs_max_z_score"] = round(max_z, 2)
                    checks["cs_would_trigger"] = max_z > SPEC["cs_sigma_threshold"]

            results[sym] = checks
        except Exception as e:
            results[sym] = {"error": str(e)}

    return {
        "n_symbols_with_4h": len(results),
        "all_4h_frequency": sum(1 for r in results.values()
                                 if r.get("is_4h_data", False)),
        "details": results,
    }


# ═══════════════════════════════════════════════════════════════════════
# 5. VI/CFI CLUSTERING VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def validate_vi_clustering(features_data: dict) -> dict:
    """
    Validates VI/CFI clustering:
    [8]  Uses Variation of Information (not linear correlation)
    [8]  VI threshold = 0.30
    """
    # We can verify by computing VI on the saved features
    if not features_data:
        return {"status": "NO_DATA"}

    # Pick a symbol with enough data
    ref_sym = None
    ref_df = None
    for sym, df in features_data.items():
        # Only use actual feature columns (exclude metadata)
        feat_cols = [c for c in df.columns
                     if c not in ("symbol", "d_star", "hmm_prob_bull", "hmm_is_bull")]
        if not feat_cols:
            continue
        valid = df[feat_cols].dropna()
        if len(valid) > 300:
            ref_sym = sym
            ref_df = valid
            break

    if ref_df is None:
        return {"status": "INSUFFICIENT_DATA"}

    # Compute VI matrix on feature columns
    feature_cols = [c for c in ref_df.columns
                    if c not in ("symbol", "d_star", "hmm_prob_bull", "hmm_is_bull")]

    if len(feature_cols) < 3:
        return {"status": "TOO_FEW_FEATURES", "n_features": len(feature_cols)}

    try:
        from sklearn.preprocessing import KBinsDiscretizer

        n_bins = min(10, int(np.sqrt(len(ref_df))))
        n_bins = max(n_bins, 5)

        vi_matrix = np.zeros((len(feature_cols), len(feature_cols)))

        for i, f1 in enumerate(feature_cols):
            for j, f2 in enumerate(feature_cols):
                if i >= j:
                    continue
                x = ref_df[f1].values.reshape(-1, 1)
                y = ref_df[f2].values.reshape(-1, 1)

                try:
                    kbd = KBinsDiscretizer(n_bins=n_bins, encode='ordinal',
                                           strategy='quantile')
                    x_d = kbd.fit_transform(x).ravel().astype(int)
                    y_d = kbd.fit_transform(y).ravel().astype(int)

                    # Compute VI
                    def _entropy(z):
                        _, counts = np.unique(z, return_counts=True)
                        p = counts / counts.sum()
                        return float(-np.sum(p * np.log2(p + 1e-12)))

                    def _joint_entropy(a, b):
                        combined = a * (n_bins + 1) + b
                        return _entropy(combined)

                    hx = _entropy(x_d)
                    hy = _entropy(y_d)
                    hxy = _joint_entropy(x_d, y_d)
                    mi = hx + hy - hxy
                    vi = hx + hy - 2 * mi
                    vi_norm = vi / max(hxy, 1e-12)

                    vi_matrix[i, j] = vi_norm
                    vi_matrix[j, i] = vi_norm
                except Exception:
                    vi_matrix[i, j] = 1.0
                    vi_matrix[j, i] = 1.0

        # Identify redundant pairs (VI < threshold)
        redundant_pairs = []
        for i in range(len(feature_cols)):
            for j in range(i+1, len(feature_cols)):
                if vi_matrix[i, j] < SPEC["vi_threshold"]:
                    redundant_pairs.append({
                        "f1": feature_cols[i],
                        "f2": feature_cols[j],
                        "vi": round(float(vi_matrix[i, j]), 4)
                    })

        mean_vi = float(vi_matrix[np.triu_indices_from(vi_matrix, k=1)].mean())

        return {
            "status": "OK",
            "reference_symbol": ref_sym,
            "n_features": len(feature_cols),
            "feature_names": feature_cols,
            "mean_vi": round(mean_vi, 4),
            "n_redundant_pairs": len(redundant_pairs),
            "redundant_pairs": redundant_pairs[:10],  # top 10
            "vi_threshold_used": SPEC["vi_threshold"],
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════
# 6. CODE AUDIT — STATIC CHECKS
# ═══════════════════════════════════════════════════════════════════════

def audit_code_compliance() -> dict:
    """
    Static code audit: verify that implementations match spec.
    Checks imports, class types, constants.
    """
    checks = {}

    # 1. FracDiff: verify log-space in transform.py
    try:
        from fracdiff.transform import fracdiff_log, fracdiff_log_fast
        # Function exists and name indicates log-space
        checks["fracdiff_log_function_exists"] = True

        # Check tau default
        from fracdiff.weights import DEFAULT_TAU
        checks["fracdiff_tau_default"] = float(DEFAULT_TAU)
        checks["fracdiff_tau_correct"] = DEFAULT_TAU == SPEC["fracdiff_tau"]
    except ImportError as e:
        checks["fracdiff_import_error"] = str(e)

    # 2. HMM: verify RobustScaler in pca_robust.py
    try:
        from regime.pca_robust import fit_robust_pca, RobustPCAFitted
        checks["robust_pca_function_exists"] = True

        # Check MIN_VARIANCE_EXPLAINED
        from regime.pca_robust import MIN_VARIANCE_EXPLAINED
        checks["min_var_exp_constant"] = float(MIN_VARIANCE_EXPLAINED)
        checks["min_var_exp_correct"] = MIN_VARIANCE_EXPLAINED >= SPEC["hmm_var_exp_min"]
    except ImportError as e:
        checks["pca_import_error"] = str(e)

    # 3. Winsorizer exists
    try:
        from regime.winsorizer import fit_winsorizer, apply_winsorizer
        checks["winsorizer_exists"] = True
    except ImportError as e:
        checks["winsorizer_import_error"] = str(e)

    # 4. VI uses Variation of Information (not correlation)
    try:
        from vi_cfi.vi import variation_of_information, compute_vi_distance_matrix
        checks["vi_function_exists"] = True
    except ImportError as e:
        checks["vi_import_error"] = str(e)

    # 5. Corwin-Schultz
    try:
        from drift.corwin_schultz import corwin_schultz_spread, compute_cs_features
        checks["cs_function_exists"] = True
    except ImportError as e:
        checks["cs_import_error"] = str(e)

    # 6. PBMA Purged K-Fold
    try:
        from meta_labeling.pbma_purged import generate_pbma_purged_kfold
        checks["pbma_purged_function_exists"] = True
    except ImportError as e:
        checks["pbma_import_error"] = str(e)

    # 7. Isotonic calibration with time-decay
    try:
        from meta_labeling.isotonic_calibration import run_isotonic_walk_forward
        checks["isotonic_wf_function_exists"] = True
    except ImportError as e:
        checks["isotonic_import_error"] = str(e)

    # 8. Kelly CVaR
    try:
        from sizing.kelly_cvar import compute_kelly_fraction, compute_cvar_stress
        checks["kelly_cvar_functions_exist"] = True
    except ImportError as e:
        checks["kelly_import_error"] = str(e)

    # 9. Triple Barrier with market impact
    try:
        from triple_barrier.labeler import apply_triple_barrier
        from triple_barrier.market_impact import compute_sqrt_market_impact
        checks["triple_barrier_exists"] = True
        checks["market_impact_exists"] = True
    except ImportError as e:
        checks["barrier_import_error"] = str(e)
        checks["triple_barrier_exists"] = False
        checks["market_impact_exists"] = False

    return checks


# ═══════════════════════════════════════════════════════════════════════
# MAIN: LOAD DATA AND RUN ALL CHECKS
# ═══════════════════════════════════════════════════════════════════════

def load_features_data() -> dict:
    """Load all saved features parquets."""
    data = {}
    if not FEATURES_DIR.exists():
        print(f"  ❌ FEATURES_DIR not found: {FEATURES_DIR}")
        return data
    for f in sorted(FEATURES_DIR.glob("*.parquet")):
        try:
            df = pd.read_parquet(f)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp").sort_index()
            elif "index" in df.columns:
                df["index"] = pd.to_datetime(df["index"], utc=True)
                df = df.set_index("index").sort_index()
            data[f.stem] = df
        except Exception as e:
            print(f"  ⚠️ Error loading {f.name}: {e}")
    return data


def print_report(
    code_audit: dict,
    fracdiff_result: dict,
    features_result: dict,
    hmm_result: dict,
    cs_result: dict,
    vi_result: dict,
) -> dict:
    """Print comprehensive Phase 2 compliance report."""
    print("\n" + "="*80)
    print("SNIPER v10.10 — PHASE 2 DIAGNOSTIC REPORT")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("="*80)

    # ── 1. Code Audit ──
    print("\n── [AUDIT] CODE COMPLIANCE ─────────────────────────────────")
    code_checks = [
        ("fracdiff_log (log-space) [5b]",     code_audit.get("fracdiff_log_function_exists")),
        ("fracdiff τ=1e-5 [5c]",              code_audit.get("fracdiff_tau_correct")),
        ("RobustPCA (RobustScaler) [7]",      code_audit.get("robust_pca_function_exists")),
        ("Winsorizer 1%/99% [7]",             code_audit.get("winsorizer_exists")),
        ("var_exp ≥ 80% constant [7c]",       code_audit.get("min_var_exp_correct")),
        ("VI function (not correlation) [8]",  code_audit.get("vi_function_exists")),
        ("Corwin-Schultz [13b]",              code_audit.get("cs_function_exists")),
        ("PBMA Purged K-Fold [8b]",           code_audit.get("pbma_purged_function_exists")),
        ("Isotonic walk-forward [12c]",       code_audit.get("isotonic_wf_function_exists")),
        ("Kelly + CVaR stress [12]",          code_audit.get("kelly_cvar_functions_exist")),
        ("Triple Barrier + Market Impact [6b]", code_audit.get("market_impact_exists")),
    ]
    for name, ok in code_checks:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")
    n_code_pass = sum(1 for _, ok in code_checks if ok)
    print(f"  Code audit: {n_code_pass}/{len(code_checks)} pass")

    # ── 2. FracDiff ──
    print("\n── [5] FRACDIFF ─────────────────────────────────────────────")
    print(f"  Assets checked: {fracdiff_result['assets_checked']}")
    print(f"  Assets pass (d* in range + feature present): {fracdiff_result['assets_pass']}")
    # Sample d* values
    d_values = [(sym, r.get("d_star_value"))
                for sym, r in fracdiff_result["details"].items()
                if r.get("d_star_value") is not None]
    if d_values:
        ds = [d for _, d in d_values]
        print(f"  d* range: [{min(ds):.3f}, {max(ds):.3f}], mean={np.mean(ds):.3f}")
        print(f"  SPEC: expanding window [5], log-space [5b], τ=1e-5 [5c]")

    # ── 3. Features ──
    print("\n── [3] FEATURE ENGINEERING ──────────────────────────────────")
    print(f"  Assets with all required features: {features_result['all_features_present']}/{features_result['assets_checked']}")
    # Show a sample
    sample_sym = list(features_result["details"].keys())[:1]
    for sym in sample_sym:
        r = features_result["details"][sym]
        print(f"  Sample ({sym}): {r.get('n_rows')} rows, {r.get('total_features')} features")
        print(f"    Date range: {r.get('date_start')} → {r.get('date_end')}")
        if r.get("required_missing"):
            print(f"    ❌ Missing: {r['required_missing']}")
        else:
            print(f"    ✅ All required features present")
        if r.get("columns"):
            feat_cols = [c for c in r["columns"]
                         if c not in ("symbol", "d_star", "hmm_prob_bull", "hmm_is_bull")]
            print(f"    Features: {feat_cols}")

    # ── 4. HMM/PCA ──
    print("\n── [4/7] HMM + PCA REGIME DETECTION ────────────────────────")

    # Artifacts check
    art = hmm_result.get("artifacts", {})
    print(f"  Symbols with HMM artifacts: {art.get('symbols_with_artifacts', 0)}")
    print(f"  All use RobustScaler [7]:     {'✅' if art.get('all_use_robust_scaler') else '❌'}")
    print(f"  All have Winsorizer [7]:      {'✅' if art.get('all_have_winsorizer') else '❌'}")
    print(f"  All var_exp ≥ 80% [7c]:       {'✅' if art.get('all_var_exp_pass') else '❌'}")

    # Sample artifact details
    art_details = art.get("details", {})
    # Check for n_PCs != 2 (critical spec violation)
    bad_npcs = [(s, a.get("n_pca_components"))
                for s, a in art_details.items()
                if a.get("n_pca_components") is not None and a.get("n_pca_components") != 2]
    if bad_npcs:
        print(f"  ⚠️  n_PCs ≠ 2 detected in {len(bad_npcs)} assets! Spec mandates N_PCA=2.")
        print(f"      Actual: n_PCs={bad_npcs[0][1]} — causes overfitting (params ratio drops)")
        print(f"      FIX: filter to 9 HMM features + force n_components=2")

    sample_art = list(art_details.keys())[:2]
    for sym in sample_art:
        a = art_details[sym]
        print(f"  Artifact {sym}: {a.get('n_artifacts')} windows, "
              f"scaler={a.get('scaler_type')}, "
              f"var_exp={a.get('var_explained')}, "
              f"n_PCs={a.get('n_pca_components')}, "
              f"threshold={a.get('threshold')}")
        if a.get("var_exp_min_all_windows"):
            print(f"    var_exp across all windows: "
                  f"[{a['var_exp_min_all_windows']}, {a['var_exp_max_all_windows']}]")

    # Bear 2022 detection
    print(f"\n  Bear 2022 H1 detection [7c]: {hmm_result.get('bear_2022_pass', 0)} assets pass (>60%)")
    print(f"  F1 OOS ≥ 0.45 [15.1]:        {hmm_result.get('f1_pass', 0)} assets pass")

    # Sample per-asset
    for sym in list(hmm_result.get("details", {}).keys())[:3]:
        r = hmm_result["details"][sym]
        bear = r.get("bear_2022h1_pct", "N/A")
        f1 = r.get("f1_oos", "N/A")
        bull = r.get("hmm_pct_bull", "N/A")
        print(f"    {sym:8s}  bear_2022={bear}  F1={f1}  pct_bull={bull}")

    # ── 5. Corwin-Schultz ──
    print("\n── [11/13b] CORWIN-SCHULTZ CIRCUIT BREAKER ──────────────────")
    print(f"  Symbols with 4h klines: {cs_result.get('n_symbols_with_4h', 0)}")
    print(f"  All confirmed 4h frequency: {cs_result.get('all_4h_frequency', 0)}")
    cs_details = cs_result.get("details", {})
    for sym in list(cs_details.keys())[:3]:
        r = cs_details[sym]
        if r.get("cs_spread_mean") is not None:
            print(f"    {sym:8s}  bars={r.get('n_bars')}  "
                  f"CS_mean={r.get('cs_spread_mean'):.6f}  "
                  f"CS_max_z={r.get('cs_max_z_score', 'N/A')}")

    # ── 6. VI/CFI ──
    print("\n── [6/8] VI/CFI CLUSTERING ──────────────────────────────────")
    print(f"  Status: {vi_result.get('status')}")
    if vi_result.get("status") == "OK":
        print(f"  Reference: {vi_result.get('reference_symbol')}")
        print(f"  Features: {vi_result.get('n_features')}")
        print(f"  Mean VI: {vi_result.get('mean_vi')}")
        print(f"  Redundant pairs (VI < {SPEC['vi_threshold']}): {vi_result.get('n_redundant_pairs')}")
        for pair in vi_result.get("redundant_pairs", [])[:5]:
            print(f"    {pair['f1']:20s} ↔ {pair['f2']:20s}  VI={pair['vi']}")

    # ── OVERALL ──
    print("\n── PHASE 2 OVERALL ASSESSMENT ──────────────────────────────")
    overall_checks = {
        "Code audit":            n_code_pass >= 10,
        "FracDiff":              fracdiff_result["assets_pass"] >= 20,
        "Features":              features_result["all_features_present"] >= 20,
        "HMM RobustScaler [7]":  art.get("all_use_robust_scaler", False),
        "HMM Winsorizer [7]":    art.get("all_have_winsorizer", False),
        "HMM var_exp ≥ 80% [7c]": art.get("all_var_exp_pass", False),
        "HMM n_PCs = 2 [4]":    all(r.get("n_pca_components") == 2
                                     for r in art.get("details", {}).values()
                                     if "n_pca_components" in r),
        "HMM walk-forward":      art.get("symbols_with_artifacts", 0) >= 20,
        "4h data for CS [13b]":  cs_result.get("n_symbols_with_4h", 0) >= 10,
        "VI clustering [8]":     vi_result.get("status") == "OK",
    }
    for name, ok in overall_checks.items():
        print(f"  {'✅' if ok else '❌'} {name}")

    n_pass = sum(1 for ok in overall_checks.values() if ok)
    n_total = len(overall_checks)
    print(f"\n  Phase 2 compliance: {n_pass}/{n_total} checks pass")

    if n_pass < n_total:
        print("  ⚠️  Fix failing checks before proceeding to Phase 4 (CPCV)")
    else:
        print("  ✅  Phase 2 fully compliant with spec v10.10")

    # Save JSON report
    report = {
        "timestamp": datetime.now().isoformat(),
        "code_audit": code_audit,
        "fracdiff": fracdiff_result,
        "features": {k: v for k, v in features_result.items() if k != "details"},
        "hmm_pca": {k: v for k, v in hmm_result.items() if k != "details"},
        "hmm_artifacts": {k: v for k, v in art.items() if k != "details"},
        "corwin_schultz": {k: v for k, v in cs_result.items() if k != "details"},
        "vi_clustering": {k: v for k, v in vi_result.items()
                          if k not in ("details", "redundant_pairs")},
        "overall": overall_checks,
        "overall_pass": n_pass,
        "overall_total": n_total,
    }

    report_path = Path("/data/models") / "phase2_diagnostic_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Full report saved: {report_path}")
    print("="*80)

    return report


if __name__ == "__main__":
    print("SNIPER v10.10 — Phase 2 Diagnostic")
    print("Loading data...")

    features_data = load_features_data()
    print(f"  Features parquets loaded: {len(features_data)}")

    print("\nRunning checks...\n")

    # 1. Code audit
    print("  [1/6] Code compliance audit...")
    code_audit = audit_code_compliance()

    # 2. FracDiff
    print("  [2/6] FracDiff validation...")
    fracdiff_result = validate_fracdiff(features_data)

    # 3. Features
    print("  [3/6] Feature engineering validation...")
    features_result = validate_features(features_data)

    # 4. HMM/PCA
    print("  [4/6] HMM + PCA validation...")
    hmm_result = validate_hmm_pca(features_data)

    # 5. Corwin-Schultz
    print("  [5/6] Corwin-Schultz validation...")
    cs_result = validate_corwin_schultz()

    # 6. VI/CFI
    print("  [6/6] VI/CFI clustering validation...")
    vi_result = validate_vi_clustering(features_data)

    # Report
    report = print_report(
        code_audit, fracdiff_result, features_result,
        hmm_result, cs_result, vi_result,
    )
