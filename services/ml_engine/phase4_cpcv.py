#!/usr/bin/env python3
"""
SNIPER v10.10 -- Phase 4 v4: CPCV N=6 k=2 + Fallback P_bma > 0.65
====================================================================
Fixes v3:
  1. Backtest usa Kelly-weighted sizing (nao 100% por trade)
  2. Features constantes filtradas (hmm_prob_bull=0.9998)
  3. numpy bool fix na contagem de subperiodos
  4. Adicionado dvol_zscore, term_spread, volume_momentum, ret_20d
"""
from __future__ import annotations
import json, time, warnings
import os
from datetime import datetime
from itertools import combinations
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.stats import norm, skew, kurtosis as scipy_kurtosis
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import RobustScaler
from features.onchain import UNLOCK_MODEL_FEATURE_COLUMNS
warnings.filterwarnings("ignore")

MODEL_PATH    = Path("/data/models")
FEATURES_PATH = MODEL_PATH / "features"
PHASE3_PATH   = MODEL_PATH / "phase3"
OUTPUT_PATH   = MODEL_PATH / "phase4"

N_SPLITS       = 6
N_TEST_SPLITS  = 2
EMBARGO_PCT    = 0.01
SL_PENALTY     = 2.0
HALFLIFE_DAYS  = 180
N_TRIALS_SURFACE = 500
N_TRIALS_HONEST  = 5000
CAPITAL_INITIAL  = 200_000

PBO_THRESHOLD       = 0.10
AUC_MIN_THRESHOLD   = 0.52
ECE_THRESHOLD       = 0.05
SHARPE_OOS_MIN      = 0.70
MAX_DD_THRESHOLD    = 0.18
SUBPERIOD_MIN_PASS  = 4
PBMA_FALLBACK_THR   = 0.65
UNLOCK_MODEL_FEATURE_SET = os.getenv("UNLOCK_MODEL_FEATURE_SET", "baseline").strip().lower()
HMM_META_FEATURE_MODE = os.getenv("HMM_META_FEATURE_MODE", "include").strip().lower()
HMM_HARD_GATE_MODE = os.getenv("HMM_HARD_GATE_MODE", "off").strip().lower()
PHASE4_META_PROB_MODE = os.getenv("PHASE4_META_PROB_MODE", "calibrated").strip().lower()
MODEL_RUN_TAG = os.getenv("MODEL_RUN_TAG", "").strip()
TB_REFERENCE_POSITION_FRAC = float(os.getenv("TB_REFERENCE_POSITION_FRAC", "0.05"))
PHASE4_FIXED_ALLOC_SMALL = float(os.getenv("PHASE4_FIXED_ALLOC_SMALL", "0.01"))
PHASE4_CONSERVATIVE_KELLY_MULT = float(os.getenv("PHASE4_CONSERVATIVE_KELLY_MULT", "0.50"))
PHASE4_CONSERVATIVE_ALLOC_CAP = float(os.getenv("PHASE4_CONSERVATIVE_ALLOC_CAP", "0.02"))
PHASE4_SELECTIVE_THRESHOLD = float(os.getenv("PHASE4_SELECTIVE_THRESHOLD", "0.75"))
UNLOCK_PROXY_FEATURE_COLUMNS = [
    "unlock_overhang_proxy_rank_full",
    "unlock_fragility_proxy_rank_fallback",
]
VI_CLUSTER_ASSET_PATHS = [
    MODEL_PATH / "global_vi_clusters.json",
    MODEL_PATH / "vi_asset_clusters.json",
    MODEL_PATH / "calibration" / "global_vi_clusters.json",
]


def get_unlock_model_feature_columns(mode: str | None = None) -> list[str]:
    normalized = (mode or UNLOCK_MODEL_FEATURE_SET or "baseline").strip().lower()
    if normalized in {"baseline", "none", "off"}:
        return []
    if normalized == "proxies":
        return UNLOCK_PROXY_FEATURE_COLUMNS.copy()
    return list(UNLOCK_MODEL_FEATURE_COLUMNS)


def _is_allowed_unlock_feature(feature: str) -> bool:
    return feature not in UNLOCK_MODEL_FEATURE_COLUMNS or feature in get_unlock_model_feature_columns()


def _hmm_meta_feature_enabled(mode: str | None = None) -> bool:
    normalized = (mode or HMM_META_FEATURE_MODE or "include").strip().lower()
    return normalized not in {"exclude", "off", "gate_only", "false", "0"}


def _hmm_hard_gate_enabled(mode: str | None = None) -> bool:
    normalized = (mode or HMM_HARD_GATE_MODE or "off").strip().lower()
    return normalized in {"on", "true", "1", "hard_gate", "gate"}


def _phase4_prob_mode(mode: str | None = None) -> str:
    normalized = (mode or PHASE4_META_PROB_MODE or "calibrated").strip().lower()
    return "raw" if normalized in {"raw", "uncalibrated", "off"} else "calibrated"


def _phase4_neutral_fill(feature: str) -> float:
    if feature in {"hmm_prob_bull", "btc_ma200_flag"}:
        return 0.5
    if feature in UNLOCK_MODEL_FEATURE_COLUMNS:
        return 0.5
    return 0.0


def _prepare_feature_matrix(df: pd.DataFrame, feature_cols: list[str]) -> np.ndarray:
    frame = df.loc[:, feature_cols].replace([np.inf, -np.inf], np.nan).ffill()
    for col in feature_cols:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(_phase4_neutral_fill(col))
    return frame.to_numpy(dtype=float)


def _safe_read(path):
    df = pd.read_parquet(path)
    if isinstance(df.index, pd.DatetimeIndex):
        col_name = df.index.name or "date"
        df = df.reset_index()
        if col_name != "date" and col_name in df.columns:
            df = df.rename(columns={col_name: "date"})
    for c in ["date", "index", "event_date", "timestamp"]:
        if c in df.columns:
            df["date"] = pd.to_datetime(df[c], utc=True).dt.tz_localize(None).dt.normalize()
            if c != "date":
                df = df.drop(columns=[c], errors="ignore")
            break
    df = df.reset_index(drop=True)
    df.index.name = None
    return df


def _load_vi_cluster_map() -> dict[str, list[str]] | None:
    path = MODEL_PATH / "cluster_map.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None

    if isinstance(raw, dict) and "cluster_map" in raw and isinstance(raw["cluster_map"], dict):
        raw = raw["cluster_map"]
    elif isinstance(raw, dict) and "feature_to_cluster" in raw and isinstance(raw["feature_to_cluster"], dict):
        grouped: dict[str, list[str]] = {}
        for feat, cid in raw["feature_to_cluster"].items():
            grouped.setdefault(str(cid), []).append(str(feat))
        raw = grouped

    cleaned: dict[str, list[str]] = {}
    if isinstance(raw, dict):
        for cid, feats in raw.items():
            if isinstance(feats, list):
                cleaned[str(cid)] = [str(f) for f in feats]
    return cleaned or None


def _choose_vi_features(df: pd.DataFrame, y_col: str = "y_meta") -> list[str]:
    cluster_map = _load_vi_cluster_map()
    unlock_cols = get_unlock_model_feature_columns()
    hmm_cols = ["hmm_prob_bull"] if _hmm_meta_feature_enabled() else []
    fallback_candidates = [
        "sigma_ewma", "ret_1d", "ret_5d", "ret_20d", "vol_ratio",
        "funding_rate_ma7d", "basis_3m", "stablecoin_chg30",
        *hmm_cols, *unlock_cols, "dvol_zscore", "btc_ma200_flag", "close_fracdiff",
    ]
    if cluster_map is None:
        return [c for c in fallback_candidates if c in df.columns and df[c].notna().mean() > 0.5 and float(df[c].dropna().std()) >= 0.01]

    alias = {"realized_vol_30d": "sigma_ewma"}
    selected: list[str] = []
    y = pd.to_numeric(df[y_col], errors="coerce") if y_col in df.columns else None

    for cid, feats in sorted(cluster_map.items(), key=lambda kv: str(kv[0])):
        mapped = []
        for feat in feats:
            feat = alias.get(feat, feat)
            if feat in df.columns and feat != "p_bma_pkf" and _is_allowed_unlock_feature(feat):
                if feat == "hmm_prob_bull" and not _hmm_meta_feature_enabled():
                    continue
                ser = pd.to_numeric(df[feat], errors="coerce")
                if ser.notna().mean() > 0.5 and float(ser.dropna().std()) >= 0.01:
                    mapped.append(feat)
        mapped = list(dict.fromkeys(mapped))
        if not mapped:
            continue
        if len(mapped) == 1 or y is None:
            selected.append(mapped[0])
            continue
        best_feat = mapped[0]
        best_score = -1.0
        for feat in mapped:
            tmp = pd.concat([pd.to_numeric(df[feat], errors="coerce"), y], axis=1).dropna()
            if len(tmp) < 50:
                continue
            score = abs(float(tmp.iloc[:, 0].corr(tmp.iloc[:, 1], method="spearman")))
            if np.isnan(score):
                score = 0.0
            if score > best_score:
                best_score = score
                best_feat = feat
        selected.append(best_feat)
    return list(dict.fromkeys(selected))


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fout:
        json.dump(payload, fout, indent=2, default=str)
        fout.flush()
        import os
        os.fsync(fout.fileno())
    tmp.replace(path)


def _cluster_artifact_suffix() -> str:
    parts: list[str] = []
    if UNLOCK_MODEL_FEATURE_SET not in {"", "full"}:
        parts.append(UNLOCK_MODEL_FEATURE_SET)
    if MODEL_RUN_TAG:
        parts.append(MODEL_RUN_TAG)
    return "" if not parts else "_" + "_".join(parts)


def _symbol_cluster_artifact_paths() -> list[Path]:
    suffix = _cluster_artifact_suffix()
    paths: list[Path] = []
    if suffix:
        paths.extend([
            MODEL_PATH / f"vi_asset_clusters{suffix}.json",
            MODEL_PATH / f"global_vi_clusters{suffix}.json",
            MODEL_PATH / "calibration" / f"global_vi_clusters{suffix}.json",
        ])
    paths.extend([
        MODEL_PATH / "global_vi_clusters.json",
        MODEL_PATH / "vi_asset_clusters.json",
        MODEL_PATH / "calibration" / "global_vi_clusters.json",
    ])
    deduped: list[Path] = []
    seen = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _symbol_cluster_artifact_write_paths() -> list[Path]:
    suffix = _cluster_artifact_suffix()
    if suffix:
        return [
            MODEL_PATH / f"vi_asset_clusters{suffix}.json",
            MODEL_PATH / f"global_vi_clusters{suffix}.json",
            MODEL_PATH / "calibration" / f"global_vi_clusters{suffix}.json",
        ]
    return [
        MODEL_PATH / "vi_asset_clusters.json",
        MODEL_PATH / "global_vi_clusters.json",
        MODEL_PATH / "calibration" / "global_vi_clusters.json",
    ]


def _build_symbol_signature_frame(pooled_df: pd.DataFrame, feature_cols: list[str]) -> tuple[pd.DataFrame, list[str]]:
    if pooled_df.empty or "symbol" not in pooled_df.columns:
        return pd.DataFrame(), []

    candidate_features = [
        col for col in dict.fromkeys(["p_bma_pkf", *feature_cols])
        if col in pooled_df.columns
    ]
    rows = []
    summary_features: list[str] = []

    for symbol, sym_df in pooled_df.groupby("symbol", sort=True):
        row = {"symbol": str(symbol), "n_obs": int(len(sym_df))}
        for feature in candidate_features:
            ser = pd.to_numeric(sym_df[feature], errors="coerce")
            cov_col = f"{feature}__coverage"
            med_col = f"{feature}__median"
            iqr_col = f"{feature}__iqr"
            row[cov_col] = float(ser.notna().mean())
            if ser.notna().any():
                q25, q75 = ser.quantile([0.25, 0.75]).tolist()
                row[med_col] = float(ser.median())
                row[iqr_col] = float(q75 - q25)
            else:
                row[med_col] = np.nan
                row[iqr_col] = np.nan
            summary_features.extend([cov_col, med_col, iqr_col])
        rows.append(row)

    if not rows:
        return pd.DataFrame(), []

    signature_df = pd.DataFrame(rows).drop_duplicates("symbol").set_index("symbol").sort_index()
    summary_features = [col for col in dict.fromkeys(summary_features) if col in signature_df.columns]
    return signature_df, summary_features


def _normalize_cluster_payload(cluster_map: dict[str, list[str]]) -> tuple[dict[str, list[str]], dict[str, str]]:
    normalized: dict[str, list[str]] = {}
    symbol_to_cluster: dict[str, str] = {}
    for idx, (_, members) in enumerate(sorted(cluster_map.items(), key=lambda kv: tuple(sorted(kv[1])))):
        cluster_name = f"cluster_{idx + 1}"
        clean_members = sorted({str(member) for member in members if str(member)})
        if not clean_members:
            continue
        normalized[cluster_name] = clean_members
        for member in clean_members:
            symbol_to_cluster[member] = cluster_name
    return normalized, symbol_to_cluster


def _derive_symbol_vi_clusters(pooled_df: pd.DataFrame, feature_cols: list[str]) -> dict:
    signature_df, summary_features = _build_symbol_signature_frame(pooled_df, feature_cols)
    symbols = signature_df.index.astype(str).tolist()
    min_symbols_per_cluster = 4

    if len(symbols) < 2 or len(summary_features) < 2:
        cluster_map = {"cluster_global": symbols}
        normalized, symbol_to_cluster = _normalize_cluster_payload(cluster_map)
        return {
            "vi_clusters": normalized,
            "symbol_to_cluster": symbol_to_cluster,
            "summary_features": summary_features,
            "n_symbols": len(symbols),
            "n_clusters": len(normalized),
            "method": "global_single_cluster_insufficient_signature",
            "min_symbols_per_cluster": min_symbols_per_cluster,
        }

    matrix = signature_df[summary_features].replace([np.inf, -np.inf], np.nan)
    for column in matrix.columns:
        median = pd.to_numeric(matrix[column], errors="coerce").median()
        matrix[column] = pd.to_numeric(matrix[column], errors="coerce").fillna(
            0.0 if pd.isna(median) else float(median)
        )

    if len(symbols) < min_symbols_per_cluster * 2:
        cluster_map = {"cluster_global": symbols}
        normalized, symbol_to_cluster = _normalize_cluster_payload(cluster_map)
        return {
            "vi_clusters": normalized,
            "symbol_to_cluster": symbol_to_cluster,
            "summary_features": summary_features,
            "n_symbols": len(symbols),
            "n_clusters": len(normalized),
            "method": "global_single_cluster_insufficient_symbol_count",
            "min_symbols_per_cluster": min_symbols_per_cluster,
        }

    scaler = RobustScaler()
    X = scaler.fit_transform(matrix.to_numpy(dtype=float))
    target_clusters = min(4, max(2, len(symbols) // min_symbols_per_cluster))
    linkage_matrix = linkage(X, method="ward")
    raw_labels = fcluster(linkage_matrix, t=target_clusters, criterion="maxclust")
    raw_cluster_map: dict[str, list[str]] = {}
    for symbol, label in zip(symbols, raw_labels):
        raw_cluster_map.setdefault(f"cluster_{int(label)}", []).append(symbol)

    centroids = {
        cluster: matrix.loc[members].to_numpy(dtype=float).mean(axis=0)
        for cluster, members in raw_cluster_map.items()
    }
    large_clusters = {
        cluster for cluster, members in raw_cluster_map.items()
        if len(members) >= min_symbols_per_cluster
    }
    if not large_clusters:
        raw_cluster_map = {"cluster_global": symbols}
    else:
        for cluster_name, members in list(raw_cluster_map.items()):
            if cluster_name in large_clusters:
                continue
            for symbol in members:
                vec = matrix.loc[symbol].to_numpy(dtype=float)
                nearest = min(
                    large_clusters,
                    key=lambda cid: float(np.linalg.norm(vec - centroids[cid])),
                )
                raw_cluster_map.setdefault(nearest, []).append(symbol)
            raw_cluster_map.pop(cluster_name, None)

    normalized, symbol_to_cluster = _normalize_cluster_payload(raw_cluster_map)
    return {
        "vi_clusters": normalized,
        "symbol_to_cluster": symbol_to_cluster,
        "summary_features": summary_features,
        "n_symbols": len(symbols),
        "n_clusters": len(normalized),
        "method": "ward_symbol_signature_v1",
        "min_symbols_per_cluster": min_symbols_per_cluster,
    }


def _ensure_symbol_vi_cluster_artifact(pooled_df: pd.DataFrame, feature_cols: list[str]) -> str | None:
    artifact = _derive_symbol_vi_clusters(pooled_df, feature_cols)
    artifact_payload = {
        "generated_at_utc": datetime.utcnow().isoformat(),
        "unlock_model_feature_set": UNLOCK_MODEL_FEATURE_SET,
        **artifact,
    }
    write_paths = _symbol_cluster_artifact_write_paths()
    for path in write_paths:
        _atomic_json_write(path, artifact_payload)
    return str(write_paths[0]) if write_paths else None


def load_pooled_meta_df():
    unlock_cols = get_unlock_model_feature_columns()
    rows, symbols_ok, feat_counts = [], [], {}
    for meta_path in sorted(PHASE3_PATH.glob("*_meta.parquet")):
        symbol = meta_path.stem.replace("_meta", "")
        barrier_path = PHASE3_PATH / f"{symbol}_barriers.parquet"
        feature_path = FEATURES_PATH / f"{symbol}.parquet"
        sizing_path  = PHASE3_PATH / f"{symbol}_sizing.parquet"
        if not barrier_path.exists() or not feature_path.exists():
            continue
        try:
            meta_df    = _safe_read(meta_path).drop_duplicates("date", keep="last").set_index("date")
            barrier_df = _safe_read(barrier_path).drop_duplicates("date", keep="last").set_index("date")
            feature_df = _safe_read(feature_path).drop_duplicates("date", keep="last").set_index("date")
            common_idx = meta_df.index.intersection(barrier_df.index)

            # Preserva o histÃ³rico de cada ativo.
            # O warm-up do FracDiff nÃ£o deve cortar anos inteiros do dataset pooled.
            if len(common_idx) < 20:
                continue
            n = len(common_idx)
            row = {"date": common_idx.values, "symbol": [symbol]*n}
            m = meta_df.reindex(common_idx)
            row["p_bma_pkf"]  = m["p_bma"].values if "p_bma" in m.columns else np.full(n, np.nan)
            row["y_meta"]     = m["y_target"].values if "y_target" in m.columns else np.full(n, np.nan)
            row["uniqueness"] = m["uniqueness"].values if "uniqueness" in m.columns else np.ones(n)
            b = barrier_df.reindex(common_idx)
            for col in ["label","t_touch","pnl_real","pnl","holding_days","slippage_frac","sigma_at_entry","barrier_sl","p0"]:
                if col in b.columns:
                    row[col] = b[col].values
            if "pnl_real" not in row and "pnl" in row:
                row["pnl_real"] = row["pnl"]
            f = feature_df.reindex(common_idx)
            feat_added = 0
            for col in [
                "hmm_prob_bull",
                "hmm_is_bull",
                "realized_vol_30d",
                "ret_1d",
                "ret_5d",
                "ret_20d",
                "vol_ratio",
                "funding_rate_ma7d",
                "basis_3m",
                "stablecoin_chg30",
                *unlock_cols,
                "dvol_zscore",
                "btc_ma200_flag",
                "close_fracdiff",
            ]:
                if col in f.columns:
                    vals = f[col].values
                    try:
                        vals_num = vals.astype(float)
                        if not np.all(np.isnan(vals_num)):
                            row[col] = vals_num; feat_added += 1
                    except (ValueError, TypeError):
                        row[col] = vals; feat_added += 1
            if "realized_vol_30d" in row:
                row["sigma_ewma"] = row["realized_vol_30d"]

            # close_fracdiff deve sobreviver sem cortar o histÃ³rico todo do ativo.
            if "close_fracdiff" in row:
                frac_ser = pd.to_numeric(pd.Series(row["close_fracdiff"]), errors="coerce")
                row["close_fracdiff"] = frac_ser.ffill().fillna(0.0).values

            # Hard gate incondicional: regime bear invalida p_bma e zera sizing.
            bear_mask = None
            if _hmm_hard_gate_enabled() and "hmm_prob_bull" in row and "p_bma_pkf" in row:
                hmm_vals = np.asarray(row["hmm_prob_bull"], dtype=float)
                p_vals = np.asarray(row["p_bma_pkf"], dtype=float)
                bear_mask = np.isnan(hmm_vals) | (hmm_vals < 0.50)
                p_vals[bear_mask] = 0.0
                row["p_bma_pkf"] = p_vals
            if sizing_path.exists():
                sizing_df = _safe_read(sizing_path).drop_duplicates("date", keep="last").set_index("date")
                s = sizing_df.reindex(common_idx)
                for col in ["kelly_frac","position_usdt"]:
                    if col in s.columns:
                        vals = s[col].values
                        if bear_mask is not None:
                            vals = np.asarray(vals, dtype=float)
                            vals[bear_mask] = 0.0
                        row[col] = vals
            df_row = pd.DataFrame(row).reset_index(drop=True)
            df_row.index.name = None
            rows.append(df_row)
            symbols_ok.append(symbol)
            feat_counts[symbol] = feat_added
        except Exception as e:
            print(f"  WARN: {symbol} -- {e}")
    if not rows:
        raise RuntimeError("Nenhum ativo com dados completos")
    pooled = pd.concat(rows, ignore_index=True)
    pooled.index.name = None
    pooled = pooled.sort_values("date", kind="mergesort").reset_index(drop=True)
    pooled.index.name = None
    avg_f = np.mean(list(feat_counts.values())) if feat_counts else 0
    print(f"  Pooled: {len(pooled)} obs, {len(symbols_ok)} symbols")
    print(f"  Date range: {pooled['date'].min()} -> {pooled['date'].max()}")
    print(f"  y_meta: {pooled['y_meta'].value_counts().to_dict()}")
    print(f"  Avg features/symbol: {avg_f:.0f}")
    if "kelly_frac" in pooled.columns:
        kf = pooled["kelly_frac"].dropna()
        kf_active = kf[kf > 0]
        print(f"  Kelly: {len(kf_active)}/{len(kf)} active, mean_when_active={kf_active.mean():.3f}" if len(kf_active)>0 else "  Kelly: all zero")
    return pooled




def select_features(df):
    selected = ["p_bma_pkf"] if "p_bma_pkf" in df.columns else []
    vi_feats = _choose_vi_features(df, y_col="y_meta")
    selected.extend([c for c in vi_feats if c not in selected])
    if _hmm_meta_feature_enabled() and "hmm_prob_bull" in df.columns:
        hmm_prob = pd.to_numeric(df["hmm_prob_bull"], errors="coerce")
        if hmm_prob.notna().mean() > 0.50 and float(hmm_prob.dropna().std()) >= 0.01:
            if "hmm_prob_bull" not in selected:
                selected.append("hmm_prob_bull")

    # close_fracdiff Ã© mandatÃ³ria na Fase 4 quando houver dados minimamente vÃ¡lidos.
    if "close_fracdiff" in df.columns:
        frac = pd.to_numeric(df["close_fracdiff"], errors="coerce")
        if frac.notna().mean() > 0.30 and float(frac.dropna().std()) >= 1e-8:
            if "close_fracdiff" not in selected:
                selected.append("close_fracdiff")
    return selected


def compute_sample_weights(
df):
    n = len(df)
    dates = pd.to_datetime(df["date"])
    w_uniq = df["uniqueness"].fillna(1.0).values if "uniqueness" in df.columns else np.ones(n)
    days_ago = (dates.max() - dates).dt.days.astype(float).values
    w_time = np.exp(-days_ago / HALFLIFE_DAYS)
    w_sl = df["label"].map({1:1.0,0:1.0,-1:SL_PENALTY}).fillna(1.0).values if "label" in df.columns else np.ones(n)
    c = w_uniq * w_time * w_sl
    return c / c.sum() * n


def train_meta_model(X_tr, y_tr, w_tr, n_eff):
    try:
        from lightgbm import LGBMClassifier
        has_lgbm = True
    except ImportError:
        has_lgbm = False
    if n_eff < 60 or not has_lgbm:
        m = LogisticRegression(C=0.1, max_iter=500, solver="lbfgs",
                               class_weight="balanced", random_state=42)
        m.fit(X_tr, y_tr)
    elif n_eff < 120:
        m = LGBMClassifier(n_estimators=100, max_depth=2, learning_rate=0.05,
                           min_child_samples=50, subsample=0.8, reg_lambda=1.0,
                           class_weight="balanced", random_state=42, verbose=-1)
        m.fit(X_tr, y_tr, sample_weight=w_tr)
    else:
        m = LGBMClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                           min_child_samples=30, subsample=0.8, reg_lambda=0.5,
                           class_weight="balanced", random_state=42, verbose=-1)
        m.fit(X_tr, y_tr, sample_weight=w_tr)
    return m


def _build_portfolio_returns(
    pnl_real,
    signal,
    threshold=0.5,
    capital=CAPITAL_INITIAL,
    kelly_frac=None,
    position_usdt=None,
):
    active = signal > threshold
    n_active = int(active.sum())
    if n_active < 5:
        return {
            "portfolio_returns": np.array([], dtype=float),
            "active_pnl": np.array([], dtype=float),
            "allocations": np.array([], dtype=float),
            "n_active": n_active,
        }

    ap = np.clip(pnl_real[active], -0.50, 2.00)
    if position_usdt is not None:
        alloc = np.clip(position_usdt[active] / max(capital, 1), 0.0, 0.25)
    elif kelly_frac is not None:
        alloc = np.clip(kelly_frac[active], 0.0, 0.25)
    else:
        alloc = np.full(n_active, 0.10)

    nonzero = alloc > 0.001
    if nonzero.sum() < 5:
        return {
            "portfolio_returns": np.array([], dtype=float),
            "active_pnl": np.array([], dtype=float),
            "allocations": np.array([], dtype=float),
            "n_active": int(nonzero.sum()),
        }
    return {
        "portfolio_returns": ap[nonzero] * alloc[nonzero],
        "active_pnl": ap[nonzero],
        "allocations": alloc[nonzero],
        "n_active": int(nonzero.sum()),
    }


def compute_equity_curve(pnl_real, signal, threshold=0.5, capital=CAPITAL_INITIAL,
                         kelly_frac=None, position_usdt=None):
    """
    Equity curve com Kelly position sizing.
    portfolio_return_i = pnl_real_i * allocation_i
    allocation = kelly_frac (capped 25%) ou position_usdt/capital ou 10% default.
    """
    series = _build_portfolio_returns(
        pnl_real,
        signal,
        threshold=threshold,
        capital=capital,
        kelly_frac=kelly_frac,
        position_usdt=position_usdt,
    )
    if series["n_active"] < 5 or len(series["portfolio_returns"]) < 5:
        return {"sharpe":0.0,"cum_return":0.0,"max_dd":0.0,"n_active":series["n_active"],
                "win_rate":0.0,"equity_final":float(capital),"avg_alloc":0.0}
    pr = series["portfolio_returns"]
    ap_nz = series["active_pnl"]
    al_nz = series["allocations"]
    n_real = len(pr)

    tpy = 252 / 5
    sharpe = float(pr.mean() / (pr.std() + 1e-10) * np.sqrt(tpy))

    eq = float(capital)
    eq_s = [eq]
    for r in pr:
        eq *= (1 + r)
        eq_s.append(eq)
    ea = np.array(eq_s)
    peak = np.maximum.accumulate(ea)
    dd = (ea - peak) / (peak + 1e-10)

    return {
        "sharpe": round(sharpe, 4),
        "cum_return": round(float(ea[-1] / capital - 1), 4),
        "max_dd": round(float(abs(dd.min())), 4),
        "n_active": n_real,
        "win_rate": round(float((ap_nz > 0).mean()), 4),
        "equity_final": round(float(ea[-1]), 2),
        "avg_alloc": round(float(al_nz.mean()), 4),
    }


def _reference_order_usdt(capital: float = CAPITAL_INITIAL) -> float:
    return max(float(capital) * TB_REFERENCE_POSITION_FRAC, 1.0)


def _rescale_slippage_to_position(slippage_frac_ref: float, position_usdt: float, reference_order_usdt: float) -> float:
    slip_ref = max(float(slippage_frac_ref), 0.0)
    if slip_ref <= 0:
        return 0.0
    q_exec = max(float(position_usdt), 0.0)
    if q_exec <= 0:
        return slip_ref
    scale = np.sqrt(q_exec / max(float(reference_order_usdt), 1e-9))
    return float(min(slip_ref * scale, 0.50))


def _attach_execution_pnl(df: pd.DataFrame, position_col: str, output_col: str) -> pd.DataFrame:
    out = df.copy()
    pnl_real = pd.to_numeric(out["pnl_real"], errors="coerce").fillna(0.0) if "pnl_real" in out.columns else pd.Series(0.0, index=out.index)
    labels = pd.to_numeric(out["label"], errors="coerce") if "label" in out.columns else pd.Series(np.nan, index=out.index)
    barrier_sl = pd.to_numeric(out["barrier_sl"], errors="coerce") if "barrier_sl" in out.columns else pd.Series(np.nan, index=out.index)
    p0 = pd.to_numeric(out["p0"], errors="coerce") if "p0" in out.columns else pd.Series(np.nan, index=out.index)
    slippage_ref = pd.to_numeric(out["slippage_frac"], errors="coerce").fillna(0.0) if "slippage_frac" in out.columns else pd.Series(0.0, index=out.index)
    positions = pd.to_numeric(out[position_col], errors="coerce").fillna(0.0) if position_col in out.columns else pd.Series(0.0, index=out.index)
    reference_order_usdt = _reference_order_usdt()

    pnl_exec = pnl_real.to_numpy(dtype=float, copy=True)
    slip_exec = slippage_ref.to_numpy(dtype=float, copy=True)
    labels_arr = labels.to_numpy(dtype=float, copy=False)
    barrier_sl_arr = barrier_sl.to_numpy(dtype=float, copy=False)
    p0_arr = p0.to_numpy(dtype=float, copy=False)
    slippage_ref_arr = slippage_ref.to_numpy(dtype=float, copy=False)
    position_arr = positions.to_numpy(dtype=float, copy=False)

    stop_mask = (
        np.isfinite(labels_arr)
        & (labels_arr == -1)
        & np.isfinite(barrier_sl_arr)
        & np.isfinite(p0_arr)
        & (p0_arr > 0)
        & np.isfinite(slippage_ref_arr)
        & (slippage_ref_arr > 0)
        & np.isfinite(position_arr)
        & (position_arr > 0)
    )
    if np.any(stop_mask):
        scaled = np.array(
            [
                _rescale_slippage_to_position(slippage_ref_arr[idx], position_arr[idx], reference_order_usdt)
                for idx in np.where(stop_mask)[0]
            ],
            dtype=float,
        )
        pnl_exec[stop_mask] = (barrier_sl_arr[stop_mask] * (1.0 - scaled) / p0_arr[stop_mask]) - 1.0
        slip_exec[stop_mask] = scaled

    suffix = output_col[4:] if output_col.startswith("pnl_") else output_col
    out[output_col] = pnl_exec
    out[f"slippage_{suffix}"] = slip_exec
    out[f"slippage_ref_capped_{suffix}"] = (slippage_ref_arr >= 0.499).astype(int)
    out[f"reference_order_usdt_{suffix}"] = reference_order_usdt
    return out


def _evaluate_decision_policy(
    df: pd.DataFrame,
    *,
    label: str,
    threshold: float,
    signal_col: str,
    position_col: str,
    pnl_col: str,
) -> dict:
    signal = pd.to_numeric(df.get(signal_col), errors="coerce").fillna(0.0).values
    pnl = pd.to_numeric(df.get(pnl_col), errors="coerce").fillna(0.0).values
    positions = pd.to_numeric(df.get(position_col), errors="coerce").fillna(0.0).values
    eq = compute_equity_curve(pnl, signal, threshold=threshold, position_usdt=positions)
    series = _build_portfolio_returns(pnl, signal, threshold=threshold, position_usdt=positions)
    pr = series["portfolio_returns"]
    if len(pr) >= 5:
        sk = float(skew(pr)) if len(pr) > 10 else -0.3
        ku = float(scipy_kurtosis(pr, fisher=False)) if len(pr) > 10 else 5.0
        dsr = compute_dsr_honest(eq["sharpe"], T=max(len(pr), 100), skewness=sk, kurtosis_val=ku)
    else:
        dsr = compute_dsr_honest(0.0, T=100, skewness=-0.3, kurtosis_val=5.0)
    subperiods = analyze_subperiods(
        df,
        signal_col=signal_col,
        threshold=threshold,
        position_col=position_col,
        pnl_col=pnl_col,
    )
    active_mask = (signal > threshold) & (positions > 0)
    capped_col = f"slippage_ref_capped_{pnl_col[4:] if pnl_col.startswith('pnl_') else pnl_col}"
    capped_active = 0
    if capped_col in df.columns:
        capped_active = int(pd.to_numeric(df[capped_col], errors="coerce").fillna(0).values[active_mask].sum())
    return {
        "policy": label,
        "threshold": round(float(threshold), 4),
        "sharpe": eq["sharpe"],
        "cum_return": eq["cum_return"],
        "max_dd": eq["max_dd"],
        "n_active": eq["n_active"],
        "win_rate": eq["win_rate"],
        "equity_final": eq["equity_final"],
        "avg_alloc": eq.get("avg_alloc", 0.0),
        "dsr_honest": dsr["dsr_honest"],
        "subperiods_positive": int(sum(1 for row in subperiods if row.get("positive") is True)),
        "subperiods_total": int(sum(1 for row in subperiods if row.get("positive") is not None)),
        "capped_slippage_ref_active": capped_active,
        "subperiods": subperiods,
        "active_port_returns": pr,
    }


def _load_symbol_vi_clusters(symbols):
    symbols = sorted({str(s) for s in symbols if str(s)})
    artifact_used = False
    artifact_path = None
    clusters = {}

    for path in _symbol_cluster_artifact_paths():
        if not path.exists():
            continue
        try:
            with open(path, "r", encoding="utf-8") as fin:
                raw = json.load(fin)
            candidate = raw.get("vi_clusters") if isinstance(raw, dict) and isinstance(raw.get("vi_clusters"), dict) else raw
            if not isinstance(candidate, dict):
                continue
            parsed = {}
            for cluster_name, members in candidate.items():
                if not isinstance(members, list):
                    continue
                valid_members = sorted({str(m) for m in members if str(m) in symbols})
                if valid_members:
                    parsed[str(cluster_name)] = valid_members
            if parsed:
                clusters = parsed
                artifact_used = True
                artifact_path = str(path)
                break
        except Exception:
            continue

    symbol_to_cluster = {}
    for cluster_name, members in clusters.items():
        for symbol in members:
            symbol_to_cluster[symbol] = cluster_name

    missing = [symbol for symbol in symbols if symbol not in symbol_to_cluster]
    if missing:
        fallback_cluster = "cluster_global"
        clusters[fallback_cluster] = sorted({*clusters.get(fallback_cluster, []), *missing})
        for symbol in missing:
            symbol_to_cluster[symbol] = fallback_cluster

    if not clusters:
        clusters = {"cluster_global": symbols}
        symbol_to_cluster = {symbol: "cluster_global" for symbol in symbols}

    if artifact_used and missing:
        mode = "artifact_with_global_fallback"
    elif artifact_used:
        mode = "artifact"
    else:
        mode = "global_fallback_missing_artifact"

    return clusters, symbol_to_cluster, mode, artifact_path


def _fit_cluster_calibrators(oos_df, min_pool_size=150):
    from meta_labeling.isotonic_calibration import fit_isotonic_calibrator

    symbols = oos_df["symbol"].astype(str).unique().tolist()
    clusters, symbol_to_cluster, cluster_mode, artifact_path = _load_symbol_vi_clusters(symbols)

    global_dates = pd.DatetimeIndex(pd.to_datetime(oos_df["date"]).values)
    global_calibrator = fit_isotonic_calibrator(
        oos_df["p_meta_raw"].values,
        oos_df["y_meta"].values,
        global_dates,
        halflife_days=HALFLIFE_DAYS,
    )

    calibrators = {}
    cluster_summary = []

    for cluster_name, members in sorted(clusters.items()):
        cluster_df = oos_df[oos_df["symbol"].astype(str).isin(members)].copy()
        if cluster_df.empty:
            continue

        use_global = len(cluster_df) < max(60, min_pool_size)
        if use_global:
            calibrator = global_calibrator
            fit_mode = "global_fallback_small_cluster"
        else:
            cluster_dates = pd.DatetimeIndex(pd.to_datetime(cluster_df["date"]).values)
            calibrator = fit_isotonic_calibrator(
                cluster_df["p_meta_raw"].values,
                cluster_df["y_meta"].values,
                cluster_dates,
                halflife_days=HALFLIFE_DAYS,
            )
            fit_mode = "cluster_specific"

        raw_probs = np.clip(cluster_df["p_meta_raw"].values.astype(float), 0.001, 0.999)
        cal_probs = np.asarray(calibrator.predict(raw_probs), dtype=float)
        cal_probs[cluster_df["p_meta_raw"].values.astype(float) <= 0.0] = 0.0

        calibrators[cluster_name] = calibrator
        cluster_summary.append({
            "cluster": cluster_name,
            "n_obs": int(len(cluster_df)),
            "n_symbols": int(cluster_df["symbol"].nunique()),
            "symbols": sorted(cluster_df["symbol"].astype(str).unique().tolist()),
            "fit_mode": fit_mode,
            "ece_before": round(_compute_ece(cluster_df["p_meta_raw"].values, cluster_df["y_meta"].values), 4),
            "ece_after": round(_compute_ece(cal_probs, cluster_df["y_meta"].values), 4),
        })

    return calibrators, cluster_summary, symbol_to_cluster, cluster_mode, artifact_path


def _apply_cluster_calibration(oos_df, calibrators, symbol_to_cluster):
    cluster_names = oos_df["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
    calibrated = pd.Series(np.nan, index=oos_df.index, dtype=float)

    for cluster_name, idx in cluster_names.groupby(cluster_names).groups.items():
        calibrator = calibrators.get(cluster_name) or calibrators.get("cluster_global")
        raw_vals = pd.to_numeric(oos_df.loc[idx, "p_meta_raw"], errors="coerce").fillna(0.0).values
        if calibrator is None:
            calibrated.loc[idx] = raw_vals
            continue
        clipped = np.clip(raw_vals, 0.001, 0.999)
        cal_vals = np.asarray(calibrator.predict(clipped), dtype=float)
        cal_vals[raw_vals <= 0.0] = 0.0
        calibrated.loc[idx] = cal_vals

    return calibrated.astype(float)


def _compute_symbol_trade_stats(reference_df):
    if reference_df.empty or "pnl_real" not in reference_df.columns:
        return {}, 0.02, 0.02

    pnl_real = pd.to_numeric(reference_df["pnl_real"], errors="coerce").dropna()
    pnl_tp = pnl_real[pnl_real > 0]
    pnl_sl = pnl_real[pnl_real < 0]

    global_tp = abs(float(pnl_tp.mean())) if not pnl_tp.empty else 0.02
    global_sl = abs(float(pnl_sl.mean())) if not pnl_sl.empty else 0.02

    stats = {}
    for symbol, sym_df in reference_df.groupby("symbol"):
        sym_pnl = pd.to_numeric(sym_df["pnl_real"], errors="coerce").dropna()
        sym_tp = sym_pnl[sym_pnl > 0]
        sym_sl = sym_pnl[sym_pnl < 0]
        stats[str(symbol)] = {
            "avg_tp": abs(float(sym_tp.mean())) if not sym_tp.empty else global_tp,
            "avg_sl": abs(float(sym_sl.mean())) if not sym_sl.empty else global_sl,
        }

    return stats, global_tp, global_sl


def _attach_trade_stats(df, symbol_stats, global_tp, global_sl, tp_col, sl_col):
    out = df.copy()
    mapped = out["symbol"].astype(str).map(lambda symbol: symbol_stats.get(symbol, {}))
    out[tp_col] = mapped.map(lambda item: float(item.get("avg_tp", global_tp)))
    out[sl_col] = mapped.map(lambda item: float(item.get("avg_sl", global_sl)))
    out[tp_col] = pd.to_numeric(out[tp_col], errors="coerce").fillna(global_tp).clip(lower=1e-6)
    out[sl_col] = pd.to_numeric(out[sl_col], errors="coerce").fillna(global_sl).clip(lower=1e-6)
    return out


def _compute_phase4_sizing(df, prob_col, prefix, avg_tp_col, avg_sl_col):
    from sizing.kelly_cvar import compute_kelly_fraction

    out = df.copy()
    n = len(out)
    if n == 0:
        return out

    probs = pd.to_numeric(out[prob_col], errors="coerce").fillna(0.0).clip(0.0, 1.0).values
    if "sigma_ewma" in out.columns:
        sigmas = pd.to_numeric(out["sigma_ewma"], errors="coerce").fillna(0.01).clip(lower=1e-6).values
    else:
        sigmas = np.full(n, 0.01, dtype=float)

    avg_tp = pd.to_numeric(out[avg_tp_col], errors="coerce").fillna(0.02).clip(lower=1e-6).values
    avg_sl = pd.to_numeric(out[avg_sl_col], errors="coerce").fillna(0.02).clip(lower=1e-6).values

    mu_vals, kelly_vals, position_vals = [], [], []
    for prob, sigma, tp_val, sl_val in zip(probs, sigmas, avg_tp, avg_sl):
        if prob < 0.50 or sigma < 1e-6:
            mu_vals.append(0.0)
            kelly_vals.append(0.0)
            position_vals.append(0.0)
            continue

        mu_adj = prob * tp_val - (1.0 - prob) * sl_val
        if mu_adj <= 0:
            mu_vals.append(float(mu_adj))
            kelly_vals.append(0.0)
            position_vals.append(0.0)
            continue

        kelly_frac = float(compute_kelly_fraction(mu=mu_adj, sigma=sigma, p_cal=prob))
        position_usdt = min(kelly_frac * CAPITAL_INITIAL, CAPITAL_INITIAL * 0.08)

        mu_vals.append(float(mu_adj))
        kelly_vals.append(kelly_frac)
        position_vals.append(float(position_usdt))

    out[f"mu_adj_{prefix}"] = mu_vals
    out[f"kelly_frac_{prefix}"] = kelly_vals
    out[f"position_usdt_{prefix}"] = position_vals
    return out


def _build_execution_snapshot(predictions_df):
    if predictions_df.empty:
        return pd.DataFrame()
    prob_col = "p_meta_raw" if _phase4_prob_mode() == "raw" else "p_meta_calibrated"

    latest = (
        predictions_df.sort_values(["date", "symbol"], kind="mergesort")
        .groupby("symbol", as_index=False)
        .tail(1)
        .sort_values(["date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )
    latest["p_calibrated"] = pd.to_numeric(latest.get(prob_col, 0.0), errors="coerce").fillna(0.0)
    latest["kelly_frac"] = pd.to_numeric(latest.get("kelly_frac_meta", 0.0), errors="coerce").fillna(0.0)
    latest["position_usdt"] = pd.to_numeric(latest.get("position_usdt_meta", 0.0), errors="coerce").fillna(0.0)
    latest["side"] = np.where(latest["position_usdt"] > 0, "BUY", "FLAT")
    latest["is_active"] = latest["position_usdt"] > 0
    return latest


def _aggregate_oos_predictions(oos_df):
    if oos_df.empty:
        return pd.DataFrame()

    agg_spec = {
        "y_meta": "first",
        "p_meta_raw": "mean",
        "p_meta_calibrated": "mean",
        "cluster_name": "first",
    }
    for col in [
        "p_bma_pkf",
        "pnl_real",
        "pnl_exec_meta",
        "hmm_prob_bull",
        "kelly_frac",
        "position_usdt",
        "sigma_ewma",
        "avg_tp_train",
        "avg_sl_train",
        "mu_adj_meta",
        "kelly_frac_meta",
        "position_usdt_meta",
        "slippage_frac",
        "barrier_sl",
        "p0",
    ]:
        if col in oos_df.columns:
            agg_spec[col] = "mean" if col.startswith(("avg_", "mu_adj_", "kelly_frac_", "position_usdt_")) else "first"

    grouped = (
        oos_df.groupby(["date", "symbol"], as_index=False)
        .agg(agg_spec)
        .sort_values(["date", "symbol"], kind="mergesort")
        .reset_index(drop=True)
    )
    counts = (
        oos_df.groupby(["date", "symbol"])
        .size()
        .reset_index(name="n_cpcv_predictions")
    )
    return grouped.merge(counts, on=["date", "symbol"], how="left")

def run_cpcv(pooled_df, feature_cols):
    cluster_artifact_path = _ensure_symbol_vi_cluster_artifact(pooled_df, feature_cols)
    prob_col = "p_meta_raw" if _phase4_prob_mode() == "raw" else "p_meta_calibrated"
    n = len(pooled_df)
    embargo = max(1, int(n * EMBARGO_PCT))
    splits = np.array_split(np.arange(n), N_SPLITS)
    combos = list(combinations(range(N_SPLITS), N_TEST_SPLITS))
    trajectories, prediction_rows = [], []

    print(f"\n  CPCV: {len(combos)} trajectories, N={n}, embargo={embargo}")
    print(f"  Features ({len(feature_cols)}): {feature_cols}")
    if cluster_artifact_path:
        print(f"  Symbol cluster artifact: {cluster_artifact_path}")

    for combo in combos:
        test_idx = np.concatenate([splits[i] for i in combo])
        train_idx = np.array([j for j in range(n) if j not in set(test_idx)])
        purge_mask = np.zeros(n, dtype=bool)
        for fi in combo:
            fs, fe = splits[fi][0], splits[fi][-1]
            purge_mask |= (np.arange(n) >= fs - embargo) & (np.arange(n) <= fe + embargo)
        train_idx = train_idx[~purge_mask[train_idx]]
        if len(train_idx) < 40 or len(test_idx) < 15:
            continue

        train_df = pooled_df.iloc[train_idx]
        test_df = pooled_df.iloc[test_idx]
        symbol_stats, global_tp, global_sl = _compute_symbol_trade_stats(train_df)
        uniq = train_df["uniqueness"].fillna(1.0) if "uniqueness" in train_df.columns else pd.Series(1.0, index=train_df.index)
        n_eff = float(uniq.sum())

        X_tr = _prepare_feature_matrix(train_df, feature_cols)
        y_tr = train_df["y_meta"].values
        w_tr = compute_sample_weights(train_df)
        X_te = _prepare_feature_matrix(test_df, feature_cols)
        y_te = test_df["y_meta"].values

        try:
            model = train_meta_model(X_tr, y_tr, w_tr, n_eff)
            p_oos_raw = model.predict_proba(X_te)[:, 1]
            if _hmm_hard_gate_enabled() and "hmm_prob_bull" in test_df.columns:
                hmm_te = pd.to_numeric(test_df["hmm_prob_bull"], errors="coerce").fillna(0.0).values
                p_oos_raw = np.where(hmm_te < 0.50, 0.0, p_oos_raw)
            auc_oos = roc_auc_score(y_te, p_oos_raw) if len(np.unique(y_te)) >= 2 else 0.5

            trajectories.append({
                "combo": str(combo),
                "n_train": len(train_idx),
                "n_test": len(test_idx),
                "n_eff": round(n_eff, 1),
                "auc_oos": round(auc_oos, 4),
                "beats_null": bool(auc_oos > 0.50),
            })
            tag = "PASS" if auc_oos > AUC_MIN_THRESHOLD else ("~" if auc_oos > 0.50 else "FAIL")
            print(f"    [{tag:4s}] {combo}: AUC_raw={auc_oos:.4f}  N_train={len(train_idx)}  N_test={len(test_df)}")

            test_rows = test_df.reset_index(drop=True)
            for row_idx, (_, row) in enumerate(test_rows.iterrows()):
                stats = symbol_stats.get(str(row["symbol"]), {"avg_tp": global_tp, "avg_sl": global_sl})
                prediction_rows.append({
                    "combo": str(combo),
                    "date": row["date"],
                    "symbol": row["symbol"],
                    "y_meta": int(y_te[row_idx]),
                    "p_meta_raw": float(p_oos_raw[row_idx]),
                    "p_bma_pkf": float(row["p_bma_pkf"]) if "p_bma_pkf" in row else np.nan,
                    "pnl_real": float(row["pnl_real"]) if "pnl_real" in row else np.nan,
                    "label": int(row["label"]) if "label" in row and pd.notna(row["label"]) else np.nan,
                    "hmm_prob_bull": float(row["hmm_prob_bull"]) if "hmm_prob_bull" in row else np.nan,
                    "sigma_ewma": float(row["sigma_ewma"]) if "sigma_ewma" in row else np.nan,
                    "avg_tp_train": float(stats["avg_tp"]),
                    "avg_sl_train": float(stats["avg_sl"]),
                })
        except Exception as e:
            print(f"    [ERR ] {combo}: {e}")

    if not trajectories or not prediction_rows:
        return {
            "status": "FAIL",
            "n_trajectories": 0,
            "pbo": 1.0,
            "pbo_pass": False,
            "auc_mean": 0.5,
            "auc_std": 0.0,
            "auc_below_052_pct": 1.0,
            "auc_pass": False,
            "sharpe_mean": 0.0,
            "max_dd_worst": 1.0,
            "avg_win_rate": 0.0,
            "ece_global": 0.5,
            "ece_pass": False,
        }

    oos_df = pd.DataFrame(prediction_rows)
    calibrators, cluster_summary, symbol_to_cluster, cluster_mode, artifact_path = _fit_cluster_calibrators(oos_df)
    oos_df["cluster_name"] = oos_df["symbol"].astype(str).map(symbol_to_cluster).fillna("cluster_global")
    oos_df["p_meta_calibrated"] = _apply_cluster_calibration(oos_df, calibrators, symbol_to_cluster)
    oos_df = _compute_phase4_sizing(
        oos_df,
        prob_col=prob_col,
        prefix="meta",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    oos_df = _attach_execution_pnl(oos_df, position_col="position_usdt_meta", output_col="pnl_exec_meta")
    aggregated_predictions = _aggregate_oos_predictions(oos_df)
    aggregated_predictions = _compute_phase4_sizing(
        aggregated_predictions,
        prob_col=prob_col,
        prefix="meta",
        avg_tp_col="avg_tp_train",
        avg_sl_col="avg_sl_train",
    )
    aggregated_predictions = _attach_execution_pnl(
        aggregated_predictions,
        position_col="position_usdt_meta",
        output_col="pnl_exec_meta",
    )

    df_r = pd.DataFrame(trajectories)
    combo_metrics = []
    for combo_name, combo_df in oos_df.groupby("combo", sort=False):
        pnl_col = "pnl_exec_meta" if "pnl_exec_meta" in combo_df.columns else "pnl_real"
        pnl_arr = pd.to_numeric(combo_df[pnl_col], errors="coerce").fillna(0.0).values
        eq = compute_equity_curve(
            pnl_arr,
            combo_df[prob_col].values,
            threshold=0.5,
            position_usdt=combo_df["position_usdt_meta"].values if "position_usdt_meta" in combo_df.columns else None,
            kelly_frac=combo_df["kelly_frac_meta"].values if "kelly_frac_meta" in combo_df.columns else None,
        )
        auc_cal = roc_auc_score(combo_df["y_meta"], combo_df[prob_col]) if combo_df["y_meta"].nunique() >= 2 else 0.5
        combo_metrics.append({
            "combo": combo_name,
            "auc_calibrated": round(float(auc_cal), 4),
            "sharpe_oos": eq["sharpe"],
            "max_dd_oos": eq["max_dd"],
            "cum_ret_oos": eq["cum_return"],
            "n_active": eq["n_active"],
            "win_rate": eq["win_rate"],
            "avg_alloc": eq["avg_alloc"],
        })
    if combo_metrics:
        df_r = df_r.merge(pd.DataFrame(combo_metrics), on="combo", how="left")

    pbo = float((df_r["auc_oos"] < 0.50).mean())
    auc_m = float(df_r["auc_oos"].mean())
    auc_s = float(df_r["auc_oos"].std())
    auc_b = float((df_r["auc_oos"] < AUC_MIN_THRESHOLD).mean())
    ece = _compute_ece(oos_df[prob_col].values, oos_df["y_meta"].values)
    ece_raw = _compute_ece(oos_df["p_meta_raw"].values, oos_df["y_meta"].values)
    ece_unique = _compute_ece(aggregated_predictions[prob_col].values, aggregated_predictions["y_meta"].values) if not aggregated_predictions.empty else ece

    return {
        "trajectories": df_r.to_dict(orient="records"),
        "n_trajectories": len(df_r),
        "auc_mean": round(auc_m, 4),
        "auc_std": round(auc_s, 4),
        "pbo": round(pbo, 4),
        "pbo_pass": pbo < PBO_THRESHOLD,
        "auc_below_052_pct": round(auc_b, 4),
        "auc_pass": auc_b <= 0.30,
        "sharpe_mean": round(float(df_r["sharpe_oos"].mean()), 4),
        "max_dd_worst": round(float(df_r["max_dd_oos"].max()), 4),
        "avg_win_rate": round(float(df_r["win_rate"].mean()), 4),
        "ece_global": round(ece, 4),
        "ece_raw": round(ece_raw, 4),
        "ece_unique": round(ece_unique, 4),
        "ece_pass": ece < ECE_THRESHOLD,
        "cluster_calibration_mode": cluster_mode,
        "cluster_calibration_artifact": artifact_path,
        "cluster_calibration": cluster_summary,
        "phase4_probability_mode": prob_col,
        "auc_selected_prob_mean": round(float(df_r["auc_calibrated"].mean()), 4) if "auc_calibrated" in df_r.columns else None,
        "auc_calibrated_mean": round(float(df_r["auc_calibrated"].mean()), 4) if "auc_calibrated" in df_r.columns else None,
        "mean_predictions_per_obs": round(float(aggregated_predictions["n_cpcv_predictions"].mean()), 4) if not aggregated_predictions.empty else 0.0,
        "oos_predictions_df": oos_df,
        "aggregated_predictions_df": aggregated_predictions,
        "status": "PASS" if (pbo < PBO_THRESHOLD and auc_b <= 0.30) else "FAIL",
    }

def evaluate_fallback(pooled_df):
    if "p_bma_pkf" not in pooled_df.columns or "pnl_real" not in pooled_df.columns:
        return {"error":"Missing","sharpe":0,"cum_return":0,"max_dd":1,
                "n_active":0,"win_rate":0,"equity_final":CAPITAL_INITIAL,
                "avg_alloc":0,"sensitivity":{}}
    symbol_stats, global_tp, global_sl = _compute_symbol_trade_stats(pooled_df)
    fallback_df = _attach_trade_stats(
        pooled_df,
        symbol_stats,
        global_tp,
        global_sl,
        tp_col="avg_tp_fallback",
        sl_col="avg_sl_fallback",
    )
    fallback_df = _compute_phase4_sizing(
        fallback_df,
        prob_col="p_bma_pkf",
        prefix="fallback",
        avg_tp_col="avg_tp_fallback",
        avg_sl_col="avg_sl_fallback",
    )
    sig = fallback_df["p_bma_pkf"].fillna(0).values
    if _hmm_hard_gate_enabled() and "hmm_prob_bull" in fallback_df.columns:
        hmm = pd.to_numeric(fallback_df["hmm_prob_bull"], errors="coerce").fillna(0.0).values
        sig = np.where(hmm < 0.50, 0.0, sig)
    fallback_df["p_bma_pkf_exec"] = sig

    current_position = pd.to_numeric(fallback_df["position_usdt_fallback"], errors="coerce").fillna(0.0)
    fallback_df["position_usdt_policy_current"] = current_position
    fallback_df = _attach_execution_pnl(
        fallback_df,
        position_col="position_usdt_policy_current",
        output_col="pnl_exec_current",
    )

    threshold_results = {}
    for thr in [0.55, 0.60, 0.65, 0.70, 0.75]:
        threshold_results[f"thr_{thr:.2f}"] = _evaluate_decision_policy(
            fallback_df,
            label=f"current_kelly_{thr:.2f}",
            threshold=thr,
            signal_col="p_bma_pkf_exec",
            position_col="position_usdt_policy_current",
            pnl_col="pnl_exec_current",
        )
    main_r = threshold_results[f"thr_{PBMA_FALLBACK_THR:.2f}"]

    fixed_position = np.full(len(fallback_df), CAPITAL_INITIAL * PHASE4_FIXED_ALLOC_SMALL, dtype=float)
    conservative_position = np.minimum(
        current_position.to_numpy(dtype=float) * PHASE4_CONSERVATIVE_KELLY_MULT,
        CAPITAL_INITIAL * PHASE4_CONSERVATIVE_ALLOC_CAP,
    )
    policy_specs = [
        ("current_kelly_065", PBMA_FALLBACK_THR, "position_usdt_policy_current", "pnl_exec_current"),
        ("fixed_small_065", PBMA_FALLBACK_THR, "position_usdt_policy_fixed", "pnl_exec_fixed"),
        ("kelly_conservative_065", PBMA_FALLBACK_THR, "position_usdt_policy_conservative", "pnl_exec_conservative"),
        ("fixed_small_075", PHASE4_SELECTIVE_THRESHOLD, "position_usdt_policy_selective", "pnl_exec_selective"),
    ]
    fallback_df["position_usdt_policy_fixed"] = fixed_position
    fallback_df["position_usdt_policy_conservative"] = conservative_position
    fallback_df["position_usdt_policy_selective"] = fixed_position
    fallback_df = _attach_execution_pnl(fallback_df, "position_usdt_policy_fixed", "pnl_exec_fixed")
    fallback_df = _attach_execution_pnl(fallback_df, "position_usdt_policy_conservative", "pnl_exec_conservative")
    fallback_df = _attach_execution_pnl(fallback_df, "position_usdt_policy_selective", "pnl_exec_selective")
    policy_ablation = {}
    for label, thr, position_col, pnl_col in policy_specs:
        policy_ablation[label] = {
            k: v for k, v in _evaluate_decision_policy(
                fallback_df,
                label=label,
                threshold=thr,
                signal_col="p_bma_pkf_exec",
                position_col=position_col,
                pnl_col=pnl_col,
            ).items()
            if k not in {"subperiods", "active_port_returns"}
        }
    main_signals_df = fallback_df.copy()
    main_signals_df["position_usdt_active_policy"] = main_signals_df["position_usdt_policy_current"]
    main_signals_df["pnl_exec_active_policy"] = main_signals_df["pnl_exec_current"]
    return {
        "threshold":PBMA_FALLBACK_THR,
        "sharpe":main_r["sharpe"],"cum_return":main_r["cum_return"],
        "max_dd":main_r["max_dd"],"n_active":main_r["n_active"],
        "win_rate":main_r["win_rate"],"equity_final":main_r["equity_final"],
        "avg_alloc":main_r.get("avg_alloc",0),
        "signals_df": main_signals_df,
        "active_port_returns": main_r.get("active_port_returns", np.array([], dtype=float)),
        "execution_repricing": {
            "mode": "scaled_from_reference_slippage",
            "reference_order_usdt": round(_reference_order_usdt(), 2),
            "current_policy_capped_ref_active": int(main_r.get("capped_slippage_ref_active", 0)),
        },
        "policy_ablation": policy_ablation,
        "sensitivity":{k:{kk:vv for kk,vv in v.items()
                          if kk in ("sharpe","cum_return","max_dd","n_active","win_rate","avg_alloc","dsr_honest","subperiods_positive","subperiods_total","capped_slippage_ref_active")}
                       for k,v in threshold_results.items()},
    }

def compute_dsr_honest(sharpe_is, T=1500, skewness=-0.3, kurtosis_val=5.0):
    def dsr(sr, nt):
        sr_s = ((1-np.euler_gamma)*norm.ppf(1-1.0/nt)
                + np.euler_gamma*norm.ppf(1-1.0/(nt*np.e)))
        v = (1 - skewness*sr + ((kurtosis_val-1)/4)*sr**2) / (T-1)
        return float(norm.cdf((sr-sr_s)/np.sqrt(max(v,1e-12))))
    ds = dsr(sharpe_is, N_TRIALS_SURFACE)
    dh = dsr(sharpe_is, N_TRIALS_HONEST)
    sr_need = None
    if dh <= 0.95:
        for s in np.linspace(0.5,5.0,1000):
            if dsr(s, N_TRIALS_HONEST) > 0.95:
                sr_need = round(s,2); break
    return {"sharpe_is":round(sharpe_is,4),"dsr_surface":round(ds,4),
            "dsr_honest":round(dh,4),"passed":dh>0.95,"sr_needed":sr_need,
            "n_trials_honest":N_TRIALS_HONEST}


def _compute_ece(probs, labels, n_bins=10):
    edges = np.linspace(0,1,n_bins+1)
    ece = 0.0
    for i in range(n_bins):
        m = (probs>=edges[i]) & (probs<edges[i+1])
        if m.sum()==0: continue
        ece += m.sum()/len(probs) * abs(labels[m].mean()-probs[m].mean())
    return float(ece)


SUBPERIODS = [
    ("2020-H1","2020-01-01","2020-06-30","COVID crash + recovery"),
    ("2020-H2","2020-07-01","2020-12-31","Bull inicio"),
    ("2021",   "2021-01-01","2021-12-31","Bull pleno"),
    ("2022",   "2022-01-01","2022-12-31","Bear (LUNA+FTX)"),
    ("2023",   "2023-01-01","2023-12-31","Recuperacao lateral"),
    ("2024+",  "2024-01-01","2026-12-31","Bull/lateral"),
]

def analyze_subperiods(
    pooled_df,
    signal_col="p_bma_pkf",
    threshold=PBMA_FALLBACK_THR,
    kelly_col="kelly_frac",
    position_col: str | None = None,
    pnl_col: str = "pnl_real",
):
    dates = pd.to_datetime(pooled_df["date"]).values
    pnl = pooled_df[pnl_col].fillna(0).values if pnl_col in pooled_df.columns else np.zeros(len(pooled_df))
    sig = pooled_df[signal_col].fillna(0).values if signal_col in pooled_df.columns else np.ones(len(pooled_df))*0.5
    positions = pooled_df[position_col].fillna(0).values if position_col and position_col in pooled_df.columns else None
    kelly = None if positions is not None else (pooled_df[kelly_col].fillna(0).values if kelly_col in pooled_df.columns else None)
    results = []
    for name, start, end, regime in SUBPERIODS:
        dm = (dates >= np.datetime64(start)) & (dates <= np.datetime64(end))
        if dm.sum() < 10:
            results.append({"period":name,"regime":regime,"n_obs":int(dm.sum()),
                            "status":"SKIP","n_active":0,"sharpe":0.0,
                            "cum_return":0.0,"max_dd":0.0,"win_rate":0.0,
                            "avg_alloc":0.0,"positive":None})
            continue
        k_sub = kelly[dm] if kelly is not None else None
        pos_sub = positions[dm] if positions is not None else None
        eq = compute_equity_curve(pnl[dm], sig[dm], threshold=threshold, kelly_frac=k_sub, position_usdt=pos_sub)
        # FIX: explicit Python bool (not numpy bool)
        is_positive = bool(eq["cum_return"] > 0) if eq["n_active"] >= 5 else None
        results.append({
            "period":name,"regime":regime,"n_obs":int(dm.sum()),
            "n_active":eq["n_active"],"sharpe":eq["sharpe"],
            "cum_return":eq["cum_return"],"max_dd":eq["max_dd"],
            "win_rate":eq["win_rate"],"avg_alloc":eq.get("avg_alloc",0),
            "positive":is_positive,
            "status":"PASS" if is_positive else ("SKIP" if is_positive is None else "FAIL"),
        })
    return results


def main():
    print("="*72)
    print("SNIPER v10.10 -- PHASE 4 v4: CPCV + Fallback (Kelly-weighted)")
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    print(f"Unlock feature set: {UNLOCK_MODEL_FEATURE_SET} -> {get_unlock_model_feature_columns()}")
    print(f"HMM meta feature mode: {HMM_META_FEATURE_MODE}")
    print(f"HMM hard gate mode: {HMM_HARD_GATE_MODE}")
    print(f"Phase 4 probability mode: {_phase4_prob_mode()}")
    if MODEL_RUN_TAG:
        print(f"Model run tag: {MODEL_RUN_TAG}")
    print("="*72)
    start = time.time()

    print("\n[1/7] Loading pooled cross-asset data...")
    pooled_df = load_pooled_meta_df()

    feature_cols = select_features(pooled_df)
    selected_feature_stats = {
        feat: {
            "coverage": round(float(pooled_df[feat].notna().mean()), 4),
            "std": round(float(pooled_df[feat].dropna().std()), 6) if pooled_df[feat].notna().any() else 0.0,
        }
        for feat in feature_cols
    }
    unlock_feature_coverage = {
        feat: round(float(pooled_df[feat].notna().mean()), 4)
        for feat in get_unlock_model_feature_columns()
        if feat in pooled_df.columns
    }
    print(f"\n[2/7] Features selected: {len(feature_cols)}")
    for feat in feature_cols:
        nv = pooled_df[feat].notna().sum()
        std = float(pooled_df[feat].dropna().std())
        print(f"  {feat:25s}  valid={nv}/{len(pooled_df)}  "
              f"mean={pooled_df[feat].dropna().mean():.4f}  std={std:.4f}")

    print(f"\n[3/7] Running CPCV N={N_SPLITS}, k={N_TEST_SPLITS}...")
    cpcv_result = run_cpcv(pooled_df, feature_cols)

    print("\n[4/7] Evaluating fallback (P_bma > 0.65 + Kelly sizing)...")
    fallback = evaluate_fallback(pooled_df)

    print("\n[5/7] Computing DSR Honest...")
    fb_sharpe = fallback.get("sharpe", 0.0)
    active_port_ret = np.asarray(fallback.get("active_port_returns", np.array([], dtype=float)), dtype=float)
    T_t = max(len(active_port_ret), 100)
    sk = float(skew(active_port_ret)) if len(active_port_ret) > 10 else -0.3
    ku = float(scipy_kurtosis(active_port_ret, fisher=False)) if len(active_port_ret) > 10 else 5.0
    dsr_result = compute_dsr_honest(fb_sharpe, T=T_t, skewness=sk, kurtosis_val=ku)

    print("\n[6/7] Analyzing subperiods (fallback + Kelly)...")
    fallback_signals_df = fallback.get("signals_df", pooled_df)
    subperiods = analyze_subperiods(
        fallback_signals_df,
        signal_col="p_bma_pkf_exec",
        threshold=PBMA_FALLBACK_THR,
        position_col="position_usdt_active_policy",
        pnl_col="pnl_exec_active_policy",
    )

    elapsed = time.time() - start

    # ====================== REPORT ======================
    print("\n" + "=" * 72)
    print("SNIPER v10.10 -- PHASE 4 DIAGNOSTIC REPORT v4")
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    print("=" * 72)

    print("\n-- CPCV N=6, k=2 (15 Trajetorias) [9] --")
    print(f"  Trajetorias: {cpcv_result['n_trajectories']}/15")
    print(f"  AUC OOS: mean={cpcv_result['auc_mean']:.4f} std={cpcv_result['auc_std']:.4f}")
    if cpcv_result.get("auc_selected_prob_mean") is not None:
        print(f"  AUC prob usada: {cpcv_result['auc_selected_prob_mean']:.4f} ({cpcv_result.get('phase4_probability_mode','p_meta_calibrated')})")
    print(f"  PBO: {cpcv_result['pbo']:.1%}  {'PASS' if cpcv_result['pbo_pass'] else 'FAIL'} (thr <{PBO_THRESHOLD:.0%})")
    print(f"  AUC<0.52: {cpcv_result['auc_below_052_pct']:.0%}  {'PASS' if cpcv_result['auc_pass'] else 'FAIL'} (thr <=30%)")
    print(f"  Sharpe OOS medio: {cpcv_result['sharpe_mean']:.2f}")
    print(f"  Win Rate medio: {cpcv_result.get('avg_win_rate',0):.0%}")
    print(f"  ECE global: {cpcv_result['ece_global']:.4f}  {'PASS' if cpcv_result['ece_pass'] else 'FAIL'}")
    if "ece_raw" in cpcv_result:
        print(f"  ECE raw:    {cpcv_result['ece_raw']:.4f}")
    if "ece_unique" in cpcv_result:
        print(f"  ECE unique: {cpcv_result['ece_unique']:.4f}")
    if "cluster_calibration_mode" in cpcv_result:
        print(f"  Calibration mode: {cpcv_result['cluster_calibration_mode']}")
    if "mean_predictions_per_obs" in cpcv_result:
        print(f"  Mean preds/obs:   {cpcv_result['mean_predictions_per_obs']:.2f}")
    print(f"  Status: {cpcv_result['status']}")

    print(f"\n-- FALLBACK: P_bma > {PBMA_FALLBACK_THR} + Kelly [15.1] --")
    print(f"  Trades ativos: {fallback['n_active']} / {len(pooled_df)}")
    print(f"  Avg allocation: {fallback.get('avg_alloc',0):.1%}")
    print(f"  Win Rate:       {fallback['win_rate']:.1%}")
    print(f"  Sharpe:         {fallback['sharpe']:.4f}")
    print(f"  Cum Return:     {fallback['cum_return']:.1%}")
    print(f"  Max DD:         {fallback['max_dd']:.1%}")
    print(f"  Equity:         ${fallback['equity_final']:,.0f} (de ${CAPITAL_INITIAL:,.0f})")
    exec_diag = fallback.get("execution_repricing", {})
    if exec_diag:
        print(f"  Exec repricing: {exec_diag.get('mode')}  ref_Q=${exec_diag.get('reference_order_usdt', 0):,.0f}  capped_ref_active={exec_diag.get('current_policy_capped_ref_active', 0)}")
    print(f"  Sensibilidade:")
    for tn, td in fallback.get("sensitivity",{}).items():
        print(f"    {tn}: Sharpe={td['sharpe']:.2f}  WR={td['win_rate']:.0%}  "
              f"DD={td['max_dd']:.1%}  Active={td['n_active']}  Alloc={td.get('avg_alloc',0):.1%}")
    print(f"  Policy ablation:")
    for name, stats in fallback.get("policy_ablation", {}).items():
        print(
            f"    {name}: Sharpe={stats['sharpe']:.2f}  CumRet={stats['cum_return']:.1%}  "
            f"DD={stats['max_dd']:.1%}  Active={stats['n_active']}  Alloc={stats.get('avg_alloc',0):.1%}  "
            f"DSR={stats.get('dsr_honest',0):.3f}  Sub={stats.get('subperiods_positive',0)}/{stats.get('subperiods_total',0)}"
        )

    print(f"\n-- DSR Honesto [10] --")
    print(f"  Sharpe IS (fallback): {dsr_result['sharpe_is']:.4f}")
    print(f"  DSR surface (n={N_TRIALS_SURFACE:,}): {dsr_result['dsr_surface']:.4f}")
    print(f"  DSR honesto (n={N_TRIALS_HONEST:,}): {dsr_result['dsr_honest']:.4f}  "
          f"{'PASS' if dsr_result['passed'] else 'FAIL'}")
    if dsr_result.get("sr_needed"):
        print(f"  SR necessario para pass: >= {dsr_result['sr_needed']}")

    print(f"\n-- Subperiodos (Fallback + Kelly) [16] --")
    # FIX: use bool() explicitly for counting
    n_pos = sum(1 for sp in subperiods if sp.get("positive") == True)
    n_tested = sum(1 for sp in subperiods if sp.get("positive") is not None)
    print(f"  Positivos: {n_pos}/{n_tested}  "
          f"{'PASS' if n_pos>=SUBPERIOD_MIN_PASS else 'FAIL'} (thr >={SUBPERIOD_MIN_PASS}/6)")
    for sp in subperiods:
        print(f"    [{sp['status']:4s}] {sp['period']:8s}  {sp['regime']:25s}  "
              f"N={sp['n_obs']:5d}  Active={sp['n_active']:4d}  "
              f"Sharpe={sp['sharpe']:.2f}  WR={sp['win_rate']:.0%}  "
              f"DD={sp['max_dd']:.1%}  Ret={sp['cum_return']:.1%}  "
              f"Alloc={sp.get('avg_alloc',0):.1%}")

    print(f"\n-- PHASE 4 OVERALL ASSESSMENT --")
    meta_pass = cpcv_result["status"] == "PASS"
    fb_sp = fallback["sharpe"] >= SHARPE_OOS_MIN
    fb_dp = fallback["max_dd"] <= MAX_DD_THRESHOLD
    fb_sub = n_pos >= SUBPERIOD_MIN_PASS
    n_eff_ok = (pooled_df["uniqueness"].fillna(1.0).sum()
                if "uniqueness" in pooled_df.columns else len(pooled_df)) >= 120
    checks = {
        f"CPCV PBO < {PBO_THRESHOLD:.0%} [9]":          cpcv_result["pbo_pass"],
        f"Meta-modelo AUC pass [9]":                     cpcv_result["auc_pass"],
        f"ECE < {ECE_THRESHOLD} [11]":                   cpcv_result["ece_pass"],
        f"DSR honesto > 0.95 [10]":                      dsr_result["passed"],
        f"N_eff >= 120 [17]":                            n_eff_ok,
        f"Fallback Sharpe >= {SHARPE_OOS_MIN} [15.2]":   fb_sp,
        f"Fallback Max DD <= {MAX_DD_THRESHOLD:.0%} [15.2]": fb_dp,
        f"Subperiodos >= {SUBPERIOD_MIN_PASS}/6 [16]":  fb_sub,
    }
    n_pass = sum(1 for v in checks.values() if v)
    for label, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'} {label}")
    print(f"\n  Phase 4 compliance: {n_pass}/{len(checks)}")

    if not meta_pass:
        print(f"\n  META-MODELO INVALIDADO -> FALLBACK P_bma > {PBMA_FALLBACK_THR}")

    # Diagnostics
    print(f"\n-- DIAGNOSTICO --")
    if "hmm_prob_bull" in pooled_df.columns:
        hmm_std = float(pooled_df["hmm_prob_bull"].std())
        if hmm_std < 0.01:
            print(f"  [BUG] hmm_prob_bull constante (std={hmm_std:.6f}) - HMM nao diferencia regimes")
    p_bma = pooled_df["p_bma_pkf"].dropna()
    print(f"  p_bma: mean={p_bma.mean():.3f}  std={p_bma.std():.3f}  "
          f">0.65={int((p_bma>0.65).sum())}  >0.70={int((p_bma>0.70).sum())}")
    if "kelly_frac" in pooled_df.columns:
        kf = pooled_df["kelly_frac"].dropna()
        kfa = kf[kf > 0.001]
        if len(kfa) > 0:
            print(f"  kelly: {len(kfa)}/{len(kf)} active ({len(kfa)/len(kf)*100:.1f}%), "
                  f"mean={kfa.mean():.3f}, max={kfa.max():.3f}")
        else:
            print(f"  kelly: ALL ZERO - sizing pipeline nao gera posicoes")
    if "pnl_real" in pooled_df.columns:
        pnl = pooled_df["pnl_real"].dropna()
        print(f"  pnl_real: mean={pnl.mean():.4f}  std={pnl.std():.4f}  "
              f"win_rate={float((pnl>0).mean()):.1%}")

    # Save
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    oos_predictions_df = cpcv_result.get("oos_predictions_df", pd.DataFrame())
    aggregated_predictions_df = cpcv_result.get("aggregated_predictions_df", pd.DataFrame())
    execution_snapshot_df = _build_execution_snapshot(aggregated_predictions_df)
    if not oos_predictions_df.empty:
        oos_predictions_df.to_parquet(OUTPUT_PATH / "phase4_oos_predictions.parquet", index=False)
    if not aggregated_predictions_df.empty:
        aggregated_predictions_df.to_parquet(OUTPUT_PATH / "phase4_aggregated_predictions.parquet", index=False)
    if not execution_snapshot_df.empty:
        execution_snapshot_df.to_parquet(OUTPUT_PATH / "phase4_execution_snapshot.parquet", index=False)
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "elapsed_s": round(elapsed, 1),
        "unlock_model_feature_set": UNLOCK_MODEL_FEATURE_SET,
        "unlock_model_columns": get_unlock_model_feature_columns(),
        "hmm_meta_feature_mode": HMM_META_FEATURE_MODE,
        "hmm_hard_gate_mode": HMM_HARD_GATE_MODE,
        "phase4_meta_prob_mode": _phase4_prob_mode(),
        "model_run_tag": MODEL_RUN_TAG,
        "selected_features": feature_cols,
        "selected_feature_stats": selected_feature_stats,
        "unlock_feature_coverage": unlock_feature_coverage,
        "cpcv": {k: v for k, v in cpcv_result.items() if k not in ("trajectories", "oos_predictions_df", "aggregated_predictions_df")},
        "cpcv_trajectories": cpcv_result.get("trajectories", []),
        "fallback": {k: v for k, v in fallback.items() if k not in {"signals_df", "active_port_returns"}},
        "dsr": dsr_result,
        "subperiods": subperiods,
        "checks": {k: bool(v) for k, v in checks.items()},
        "n_pass": n_pass,
    }
    suffix_parts: list[str] = []
    if UNLOCK_MODEL_FEATURE_SET not in {"", "full"}:
        suffix_parts.append(UNLOCK_MODEL_FEATURE_SET)
    if MODEL_RUN_TAG:
        suffix_parts.append(MODEL_RUN_TAG)
    suffix = "" if not suffix_parts else "_" + "_".join(suffix_parts)
    nested_path = OUTPUT_PATH / "phase4_report_v4.json"
    root_path = MODEL_PATH / "phase4_report_v4.json"
    _atomic_json_write(nested_path, report)
    _atomic_json_write(root_path, report)
    extra_paths = []
    if suffix:
        if not oos_predictions_df.empty:
            oos_predictions_df.to_parquet(OUTPUT_PATH / f"phase4_oos_predictions{suffix}.parquet", index=False)
        if not aggregated_predictions_df.empty:
            aggregated_predictions_df.to_parquet(OUTPUT_PATH / f"phase4_aggregated_predictions{suffix}.parquet", index=False)
        if not execution_snapshot_df.empty:
            execution_snapshot_df.to_parquet(OUTPUT_PATH / f"phase4_execution_snapshot{suffix}.parquet", index=False)
        nested_scenario_path = OUTPUT_PATH / f"phase4_report_v4{suffix}.json"
        root_scenario_path = MODEL_PATH / f"phase4_report_v4{suffix}.json"
        _atomic_json_write(nested_scenario_path, report)
        _atomic_json_write(root_scenario_path, report)
        extra_paths.extend([nested_scenario_path, root_scenario_path])
    try:
        size_nested = nested_path.stat().st_size
        size_root = root_path.stat().st_size
        print("\n  Reports saved:")
        print(f"    - {nested_path} ({size_nested} bytes)")
        print(f"    - {root_path} ({size_root} bytes)")
        for extra_path in extra_paths:
            print(f"    - {extra_path} ({extra_path.stat().st_size} bytes)")
    except Exception:
        print(f"\n  Reports saved: {nested_path}, {root_path}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("=" * 72)


if __name__ == "__main__":
    main()



