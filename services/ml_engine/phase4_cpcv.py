#!/usr/bin/env python3
"""
SNIPER v10.10 — Phase 4 v3: CPCV N=6 k=2 + Fallback P_bma > 0.65
"""
from __future__ import annotations
import json, time, warnings
from datetime import datetime
from itertools import combinations
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import norm, skew, kurtosis as scipy_kurtosis
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
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
MAX_DD_THRESHOLD    = 0.45
SUBPERIOD_MIN_PASS  = 4
PBMA_FALLBACK_THR   = 0.65


def _safe_read(path):
    """Read parquet, flatten to plain columns with integer index. No date in index."""
    df = pd.read_parquet(path)
    # If index is a DatetimeIndex, move it to a column
    if isinstance(df.index, pd.DatetimeIndex):
        col_name = df.index.name or "date"
        df = df.reset_index()
        if col_name != "date" and col_name in df.columns:
            df = df.rename(columns={col_name: "date"})
    # Find date column and normalize
    for c in ["date", "index", "event_date", "timestamp"]:
        if c in df.columns:
            df["date"] = pd.to_datetime(df[c], utc=True).dt.tz_localize(None).dt.normalize()
            if c != "date":
                df = df.drop(columns=[c], errors="ignore")
            break
    # Ensure plain integer index
    df = df.reset_index(drop=True)
    df.index.name = None
    return df


def load_pooled_meta_df():
    rows = []
    symbols_ok = []
    feat_counts = {}

    for meta_path in sorted(PHASE3_PATH.glob("*_meta.parquet")):
        symbol = meta_path.stem.replace("_meta", "")
        barrier_path = PHASE3_PATH / f"{symbol}_barriers.parquet"
        feature_path = FEATURES_PATH / f"{symbol}.parquet"
        sizing_path  = PHASE3_PATH / f"{symbol}_sizing.parquet"
        if not barrier_path.exists() or not feature_path.exists():
            continue
        try:
            meta_df    = _safe_read(meta_path)
            barrier_df = _safe_read(barrier_path)
            feature_df = _safe_read(feature_path)

            # Deduplicate on date, set as index for merge
            meta_df    = meta_df.drop_duplicates(subset="date", keep="last").set_index("date")
            barrier_df = barrier_df.drop_duplicates(subset="date", keep="last").set_index("date")
            feature_df = feature_df.drop_duplicates(subset="date", keep="last").set_index("date")

            common_idx = meta_df.index.intersection(barrier_df.index)
            if len(common_idx) < 20:
                continue

            # Build row as dict of arrays — NO date index
            n = len(common_idx)
            row = {}
            row["date"]   = common_idx.values
            row["symbol"] = [symbol] * n

            m = meta_df.reindex(common_idx)
            row["p_bma_pkf"]  = m["p_bma"].values if "p_bma" in m.columns else np.full(n, np.nan)
            row["y_meta"]     = m["y_target"].values if "y_target" in m.columns else np.full(n, np.nan)
            row["uniqueness"] = m["uniqueness"].values if "uniqueness" in m.columns else np.ones(n)

            b = barrier_df.reindex(common_idx)
            for col in ["label","t_touch","pnl_real","pnl","holding_days","slippage_frac","sigma_at_entry"]:
                if col in b.columns:
                    row[col] = b[col].values
            if "pnl_real" not in row and "pnl" in row:
                row["pnl_real"] = row["pnl"]

            f = feature_df.reindex(common_idx)
            feat_added = 0
            for col in ["hmm_prob_bull","hmm_is_bull","realized_vol_30d",
                         "ret_1d","ret_5d","ret_20d","vol_ratio",
                         "drawdown_pct","dvol_zscore","btc_ma200_flag",
                         "term_spread","volume_momentum","close_fracdiff"]:
                if col in f.columns:
                    vals = f[col].values
                    try:
                        if not np.all(np.isnan(vals.astype(float))):
                            row[col] = vals
                            feat_added += 1
                    except (ValueError, TypeError):
                        row[col] = vals
                        feat_added += 1

            if "realized_vol_30d" in row:
                row["sigma_ewma"] = row["realized_vol_30d"]

            if sizing_path.exists():
                sizing_df = _safe_read(sizing_path)
                sizing_df = sizing_df.drop_duplicates(subset="date", keep="last").set_index("date")
                s = sizing_df.reindex(common_idx)
                for col in ["kelly_frac","position_usdt"]:
                    if col in s.columns:
                        row[col] = s[col].values

            df_row = pd.DataFrame(row)
            df_row = df_row.reset_index(drop=True)
            df_row.index.name = None
            rows.append(df_row)
            symbols_ok.append(symbol)
            feat_counts[symbol] = feat_added

        except Exception as e:
            print(f"  WARN: {symbol} — {e}")

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
    cols = sorted([c for c in pooled.columns if c not in ("date","symbol")])
    print(f"  Data columns: {cols}")
    return pooled


def select_features(df):
    candidates = ["p_bma_pkf","hmm_prob_bull","sigma_ewma",
                   "ret_1d","ret_5d","vol_ratio","drawdown_pct",
                   "dvol_zscore","btc_ma200_flag"]
    return [c for c in candidates if c in df.columns and df[c].notna().mean() > 0.50]


def compute_sample_weights(df):
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


def compute_equity_curve(pnl_real, signal, threshold=0.5, capital=CAPITAL_INITIAL):
    active = signal > threshold
    n_active = int(active.sum())
    if n_active < 5:
        return {"sharpe":0.0,"cum_return":0.0,"max_dd":0.0,
                "n_active":n_active,"win_rate":0.0,"equity_final":capital}
    ap = np.clip(pnl_real[active], -0.50, 2.00)
    tpy = 252/5
    sharpe = float(ap.mean() / (ap.std()+1e-10) * np.sqrt(tpy))
    eq = capital
    eq_s = [capital]
    for p in ap:
        eq *= (1+p)
        eq_s.append(eq)
    ea = np.array(eq_s)
    peak = np.maximum.accumulate(ea)
    dd = (ea - peak) / (peak + 1e-10)
    return {
        "sharpe": round(sharpe,4), "cum_return": round(ea[-1]/capital-1,4),
        "max_dd": round(abs(dd.min()),4), "n_active": n_active,
        "win_rate": round(float((ap>0).mean()),4), "equity_final": round(ea[-1],2),
        "skewness": round(float(skew(ap)),4),
        "kurtosis": round(float(scipy_kurtosis(ap,fisher=False)),4),
    }


def run_cpcv(pooled_df, feature_cols):
    n = len(pooled_df)
    embargo = max(1, int(n * EMBARGO_PCT))
    splits = np.array_split(np.arange(n), N_SPLITS)
    combos = list(combinations(range(N_SPLITS), N_TEST_SPLITS))
    trajectories = []
    all_preds, all_labels = [], []

    print(f"\n  CPCV: {len(combos)} trajectories, N={n}, embargo={embargo}")
    print(f"  Features ({len(feature_cols)}): {feature_cols}")

    for combo in combos:
        test_idx  = np.concatenate([splits[i] for i in combo])
        train_idx = np.array([j for j in range(n) if j not in set(test_idx)])
        purge_mask = np.zeros(n, dtype=bool)
        for fi in combo:
            fs, fe = splits[fi][0], splits[fi][-1]
            purge_mask |= (np.arange(n) >= fs-embargo) & (np.arange(n) <= fe+embargo)
        train_idx = train_idx[~purge_mask[train_idx]]
        if len(train_idx) < 40 or len(test_idx) < 15:
            continue
        train_df = pooled_df.iloc[train_idx]
        test_df  = pooled_df.iloc[test_idx]
        uniq = train_df["uniqueness"].fillna(1.0) if "uniqueness" in train_df.columns else pd.Series(1.0, index=train_df.index)
        n_eff = float(uniq.sum())
        X_tr = train_df[feature_cols].fillna(0).values
        y_tr = train_df["y_meta"].values
        w_tr = compute_sample_weights(train_df)
        X_te = test_df[feature_cols].fillna(0).values
        y_te = test_df["y_meta"].values
        try:
            model = train_meta_model(X_tr, y_tr, w_tr, n_eff)
            p_oos = model.predict_proba(X_te)[:,1]
            auc_oos = roc_auc_score(y_te, p_oos) if len(np.unique(y_te))>=2 else 0.5
            pnl_arr = test_df["pnl_real"].fillna(0).values if "pnl_real" in test_df.columns else np.zeros(len(test_df))
            eq = compute_equity_curve(pnl_arr, p_oos, threshold=0.5)
            all_preds.extend(p_oos.tolist())
            all_labels.extend(y_te.tolist())
            trajectories.append({
                "combo":combo,"n_train":len(train_idx),"n_test":len(test_idx),
                "n_eff":round(n_eff,1),"auc_oos":round(auc_oos,4),
                "sharpe_oos":eq["sharpe"],"max_dd_oos":eq["max_dd"],
                "cum_ret_oos":eq["cum_return"],"n_active":eq["n_active"],
                "win_rate":eq["win_rate"],"beats_null":auc_oos>0.50,
            })
            flag = "✅" if auc_oos>AUC_MIN_THRESHOLD else ("⚠️" if auc_oos>0.50 else "❌")
            print(f"    {flag} {combo}: AUC={auc_oos:.4f}  Sharpe={eq['sharpe']:.2f}  DD={eq['max_dd']:.1%}  WR={eq['win_rate']:.0%}  Active={eq['n_active']}/{len(test_df)}")
        except Exception as e:
            print(f"    ❌ {combo}: {e}")

    if not trajectories:
        return {"status":"FAIL","n_trajectories":0,"pbo":1.0,"pbo_pass":False,
                "auc_mean":0.5,"auc_std":0,"auc_below_052_pct":1.0,"auc_pass":False,
                "sharpe_mean":0,"max_dd_worst":1,"avg_win_rate":0,
                "ece_global":0.5,"ece_pass":False}

    df_r = pd.DataFrame(trajectories)
    pbo = float((df_r["auc_oos"]<0.50).mean())
    auc_m = float(df_r["auc_oos"].mean())
    auc_s = float(df_r["auc_oos"].std())
    auc_b = float((df_r["auc_oos"]<AUC_MIN_THRESHOLD).mean())
    ece = _compute_ece(np.array(all_preds), np.array(all_labels))
    return {
        "trajectories":trajectories,"n_trajectories":len(trajectories),
        "auc_mean":round(auc_m,4),"auc_std":round(auc_s,4),
        "pbo":round(pbo,4),"pbo_pass":pbo<PBO_THRESHOLD,
        "auc_below_052_pct":round(auc_b,4),"auc_pass":auc_b<=0.30,
        "sharpe_mean":round(float(df_r["sharpe_oos"].mean()),4),
        "max_dd_worst":round(float(df_r["max_dd_oos"].max()),4),
        "avg_win_rate":round(float(df_r["win_rate"].mean()),4),
        "ece_global":round(ece,4),"ece_pass":ece<ECE_THRESHOLD,
        "status":"PASS" if (pbo<PBO_THRESHOLD and auc_b<=0.30) else "FAIL",
    }


def evaluate_fallback(pooled_df):
    if "p_bma_pkf" not in pooled_df.columns or "pnl_real" not in pooled_df.columns:
        return {"error":"Missing columns","sharpe":0,"cum_return":0,"max_dd":1,
                "n_active":0,"win_rate":0,"equity_final":CAPITAL_INITIAL,"sensitivity":{}}
    sig = pooled_df["p_bma_pkf"].fillna(0).values
    pnl = pooled_df["pnl_real"].fillna(0).values
    results = {}
    for thr in [0.55,0.60,0.65,0.70,0.75]:
        results[f"thr_{thr:.2f}"] = compute_equity_curve(pnl, sig, threshold=thr)
    main_r = results["thr_0.65"]
    return {
        "threshold":PBMA_FALLBACK_THR, "sharpe":main_r["sharpe"],
        "cum_return":main_r["cum_return"], "max_dd":main_r["max_dd"],
        "n_active":main_r["n_active"], "win_rate":main_r["win_rate"],
        "equity_final":main_r["equity_final"],
        "sensitivity":{k:{kk:vv for kk,vv in v.items()
                          if kk in ("sharpe","cum_return","max_dd","n_active","win_rate")}
                       for k,v in results.items()},
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

def analyze_subperiods(pooled_df, signal_col="p_bma_pkf", threshold=PBMA_FALLBACK_THR):
    dates = pd.to_datetime(pooled_df["date"]).values
    pnl = pooled_df["pnl_real"].fillna(0).values if "pnl_real" in pooled_df.columns else np.zeros(len(pooled_df))
    sig = pooled_df[signal_col].fillna(0).values if signal_col in pooled_df.columns else np.ones(len(pooled_df))*0.5
    results = []
    for name,start,end,regime in SUBPERIODS:
        dm = (dates>=np.datetime64(start)) & (dates<=np.datetime64(end))
        if dm.sum()<10:
            results.append({"period":name,"regime":regime,"n_obs":int(dm.sum()),"status":"SKIP","n_active":0,"sharpe":0,"cum_return":0,"max_dd":0,"win_rate":0,"positive":None})
            continue
        eq = compute_equity_curve(pnl[dm], sig[dm], threshold=threshold)
        results.append({
            "period":name,"regime":regime,"n_obs":int(dm.sum()),
            "n_active":eq["n_active"],"sharpe":eq["sharpe"],
            "cum_return":eq["cum_return"],"max_dd":eq["max_dd"],
            "win_rate":eq["win_rate"],
            "positive":eq["cum_return"]>0 if eq["n_active"]>=5 else None,
            "status":"✅" if eq["cum_return"]>0 else "❌",
        })
    return results


def main():
    print("="*72)
    print("SNIPER v10.10 — PHASE 4 v3: CPCV + Fallback P_bma > 0.65")
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    print("="*72)
    start = time.time()

    print("\n[1/7] Loading pooled cross-asset data...")
    pooled_df = load_pooled_meta_df()

    feature_cols = select_features(pooled_df)
    print(f"\n[2/7] Features selected: {len(feature_cols)}")
    for feat in feature_cols:
        nv = pooled_df[feat].notna().sum()
        print(f"  {feat:25s}  valid={nv}/{len(pooled_df)}  mean={pooled_df[feat].dropna().mean():.4f}")

    print(f"\n[3/7] Running CPCV N={N_SPLITS}, k={N_TEST_SPLITS}...")
    cpcv_result = run_cpcv(pooled_df, feature_cols)

    print("\n[4/7] Evaluating fallback (P_bma > 0.65)...")
    fallback = evaluate_fallback(pooled_df)

    print("\n[5/7] Computing DSR Honest...")
    fb_sharpe = fallback.get("sharpe",0.0)
    pnl_arr = pooled_df["pnl_real"].fillna(0).values if "pnl_real" in pooled_df.columns else np.zeros(len(pooled_df))
    sig_arr = pooled_df["p_bma_pkf"].fillna(0).values if "p_bma_pkf" in pooled_df.columns else np.zeros(len(pooled_df))
    active_pnl = np.clip(pnl_arr[sig_arr>PBMA_FALLBACK_THR], -0.50, 2.00)
    T_t = max(len(active_pnl),100)
    sk = float(skew(active_pnl)) if len(active_pnl)>10 else -0.3
    ku = float(scipy_kurtosis(active_pnl,fisher=False)) if len(active_pnl)>10 else 5.0
    dsr_result = compute_dsr_honest(fb_sharpe, T=T_t, skewness=sk, kurtosis_val=ku)

    print("\n[6/7] Analyzing subperiods (fallback P_bma > 0.65)...")
    subperiods = analyze_subperiods(pooled_df)

    elapsed = time.time() - start

    print("\n"+"="*72)
    print("SNIPER v10.10 — PHASE 4 DIAGNOSTIC REPORT v3")
    print(f"Timestamp: {datetime.utcnow().isoformat()}")
    print("="*72)

    print("\n-- CPCV N=6, k=2 (15 Trajetorias) [9] --")
    print(f"  Trajetorias: {cpcv_result['n_trajectories']}/15")
    print(f"  AUC OOS: mean={cpcv_result['auc_mean']:.4f} std={cpcv_result['auc_std']:.4f}")
    print(f"  PBO: {cpcv_result['pbo']:.1%}  {'PASS' if cpcv_result['pbo_pass'] else 'FAIL'} (thr: <{PBO_THRESHOLD:.0%})")
    print(f"  AUC<0.52: {cpcv_result['auc_below_052_pct']:.0%}  {'PASS' if cpcv_result['auc_pass'] else 'FAIL'} (thr: <=30%)")
    print(f"  Sharpe OOS medio: {cpcv_result['sharpe_mean']:.2f}")
    print(f"  Win Rate medio: {cpcv_result.get('avg_win_rate',0):.0%}")
    print(f"  ECE global: {cpcv_result['ece_global']:.4f}  {'PASS' if cpcv_result['ece_pass'] else 'FAIL'} (thr: <{ECE_THRESHOLD})")
    print(f"  Status: {cpcv_result['status']}")

    print(f"\n-- FALLBACK: P_bma > {PBMA_FALLBACK_THR} [15.1] --")
    print(f"  Trades ativos: {fallback['n_active']} / {len(pooled_df)}")
    print(f"  Win Rate:      {fallback['win_rate']:.1%}")
    print(f"  Sharpe:        {fallback['sharpe']:.4f}")
    print(f"  Cum Return:    {fallback['cum_return']:.1%}")
    print(f"  Max DD:        {fallback['max_dd']:.1%}")
    print(f"  Equity final:  ${fallback['equity_final']:,.0f}")
    print(f"  Sensibilidade:")
    for tn, td in fallback.get("sensitivity",{}).items():
        print(f"    {tn}: Sharpe={td['sharpe']:.2f}  WR={td['win_rate']:.0%}  DD={td['max_dd']:.1%}  Active={td['n_active']}")

    print(f"\n-- DSR Honesto [10] --")
    print(f"  Sharpe IS (fallback): {dsr_result['sharpe_is']:.4f}")
    print(f"  DSR surface (n={N_TRIALS_SURFACE:,}): {dsr_result['dsr_surface']:.4f}")
    print(f"  DSR honesto (n={N_TRIALS_HONEST:,}): {dsr_result['dsr_honest']:.4f}  {'PASS' if dsr_result['passed'] else 'FAIL'}")
    if dsr_result.get("sr_needed"):
        print(f"  SR necessario: >= {dsr_result['sr_needed']}")

    print(f"\n-- Subperiodos (Fallback P_bma > {PBMA_FALLBACK_THR}) [16] --")
    n_pos = sum(1 for sp in subperiods if sp.get("positive") is True)
    n_tested = sum(1 for sp in subperiods if sp.get("positive") is not None)
    print(f"  Positivos: {n_pos}/{n_tested}  {'PASS' if n_pos>=SUBPERIOD_MIN_PASS else 'FAIL'} (thr: >={SUBPERIOD_MIN_PASS}/6)")
    for sp in subperiods:
        print(f"    {sp['status']} {sp['period']:8s}  {sp['regime']:25s}  N={sp['n_obs']:5d}  Active={sp['n_active']:4d}  Sharpe={sp['sharpe']:.2f}  WR={sp['win_rate']:.0%}  DD={sp['max_dd']:.1%}  Ret={sp['cum_return']:.1%}")

    print(f"\n-- PHASE 4 OVERALL ASSESSMENT --")
    meta_pass = cpcv_result["status"]=="PASS"
    fb_sp = fallback["sharpe"]>=SHARPE_OOS_MIN
    fb_dp = fallback["max_dd"]<=MAX_DD_THRESHOLD
    fb_sub = n_pos>=SUBPERIOD_MIN_PASS
    n_eff_ok = (pooled_df["uniqueness"].fillna(1.0).sum() if "uniqueness" in pooled_df.columns else len(pooled_df)) >= 120

    checks = {
        f"CPCV PBO < {PBO_THRESHOLD:.0%} [9]":         cpcv_result["pbo_pass"],
        f"Meta-modelo AUC pass [9]":                    cpcv_result["auc_pass"],
        f"ECE < {ECE_THRESHOLD} [11]":                  cpcv_result["ece_pass"],
        f"DSR honesto > 0.95 [10]":                     dsr_result["passed"],
        f"N_eff >= 120 [17]":                           n_eff_ok,
        f"Fallback Sharpe >= {SHARPE_OOS_MIN} [15.2]":  fb_sp,
        f"Fallback Max DD <= {MAX_DD_THRESHOLD:.0%} [15.2]": fb_dp,
        f"Subperiodos >= {SUBPERIOD_MIN_PASS}/6 [16]":  fb_sub,
    }
    n_pass = sum(1 for v in checks.values() if v)
    for label, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'} {label}")
    print(f"\n  Phase 4 compliance: {n_pass}/{len(checks)}")

    if not meta_pass:
        print(f"\n  META-MODELO INVALIDADO -> FALLBACK P_bma > {PBMA_FALLBACK_THR}")
        if fb_sp and fb_dp:
            print(f"  Fallback viavel (Sharpe={fallback['sharpe']:.2f}, DD={fallback['max_dd']:.1%})")
        else:
            print(f"  Fallback precisa de mais edge.")
            print(f"  Opcoes: IAs meta-raciocinadores (Fase 3), ajustar HMM/barrier params.")

    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    report = {
        "timestamp":datetime.utcnow().isoformat(),"elapsed_s":round(elapsed,1),
        "cpcv":{k:v for k,v in cpcv_result.items() if k!="trajectories"},
        "cpcv_trajectories":cpcv_result.get("trajectories",[]),
        "fallback":fallback,"dsr":dsr_result,"subperiods":subperiods,
        "checks":{k:bool(v) for k,v in checks.items()},"n_pass":n_pass,
    }
    with open(OUTPUT_PATH/"phase4_report_v3.json","w") as fout:
        json.dump(report, fout, indent=2, default=str)
    print(f"\n  Report: {OUTPUT_PATH/'phase4_report_v3.json'}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print("="*72)


if __name__ == "__main__":
    main()
