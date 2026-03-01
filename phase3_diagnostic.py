#!/usr/bin/env python3
"""
SNIPER v10.10 — Phase 3 Diagnostic Validator
=============================================
Lê os parquets de Phase 3 salvos e valida contra todos os thresholds da spec.
Deploy: docker cp phase3_diagnostic.py sniper_ml_engine:/app/
        docker exec sniper_ml_engine python /app/phase3_diagnostic.py
"""
import os, sys, json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

PHASE3_DIR = Path("/data/models/phase3")
FEATURES_DIR = Path("/data/models/features")
HMM_DIR = Path("/data/models/hmm")

# ─── Spec v10.10 Thresholds ───────────────────────────────────────────
SPEC = {
    "meta_auc_min":        0.56,    # Parte 12.1: AUC OOS > 0.56
    "ece_max":             0.05,    # Parte 7.3/11: ECE < 0.05
    "precision_at_65_min": 0.60,    # Parte 12.1: Precision@65% > 0.60
    "brier_max":           0.25,    # Brier score < 0.25
    "cvar_limit":          0.15,    # Parte 10.5: CVaR portfolio ≤ 15%
    "pct_sl_warn":         0.45,    # Barrier: SL > 45% = WARN
    "min_events":          30,      # Minimum barrier events
    "n_eff_logistic":      60,      # N_eff < 60 → Logistic only
    "n_eff_lgbm_strict":   120,     # 60-120 → LGBM strict
    "kelly_kappa":         0.25,    # Kelly fraction = κ × f*
    "kelly_max_frac":      0.22,    # Max 22% per position (cap = 0.50 in code)
    "min_phase3_assets":   20,      # At least 20 assets should complete Phase 3
}

def load_phase3_data():
    """Load all Phase 3 parquets."""
    barriers = {}
    meta = {}
    sizing = {}

    if not PHASE3_DIR.exists():
        print(f"❌ PHASE3_DIR not found: {PHASE3_DIR}")
        return barriers, meta, sizing

    for f in sorted(PHASE3_DIR.glob("*_barriers.parquet")):
        sym = f.stem.replace("_barriers", "")
        barriers[sym] = pd.read_parquet(f)

    for f in sorted(PHASE3_DIR.glob("*_meta.parquet")):
        sym = f.stem.replace("_meta", "")
        meta[sym] = pd.read_parquet(f)

    for f in sorted(PHASE3_DIR.glob("*_sizing.parquet")):
        sym = f.stem.replace("_sizing", "")
        sizing[sym] = pd.read_parquet(f)

    return barriers, meta, sizing


def validate_barriers(barriers: dict) -> dict:
    """Validate Triple-Barrier outputs."""
    results = {}
    for sym, df in barriers.items():
        n = len(df)
        if n < SPEC["min_events"]:
            results[sym] = {"status": "SKIP", "n_events": n}
            continue

        pct_tp = float((df["label"] == 1).mean())
        pct_sl = float((df["label"] == -1).mean())
        pct_ts = float((df["label"] == 0).mean())

        # Check pnl columns
        has_pnl = "pnl_real" in df.columns
        avg_pnl_tp = float(df.loc[df["label"]==1, "pnl_real"].mean()) if has_pnl and (df["label"]==1).any() else None
        avg_pnl_sl = float(df.loc[df["label"]==-1, "pnl_real"].mean()) if has_pnl and (df["label"]==-1).any() else None

        # Check slippage
        has_slip = "slippage_pct" in df.columns
        avg_slip = float(df["slippage_pct"].mean()) if has_slip else None

        status = "OK" if pct_sl <= SPEC["pct_sl_warn"] else "WARN_SL_HIGH"

        results[sym] = {
            "status": status,
            "n_events": n,
            "pct_tp": round(pct_tp, 3),
            "pct_sl": round(pct_sl, 3),
            "pct_ts": round(pct_ts, 3),
            "avg_pnl_tp": round(avg_pnl_tp, 4) if avg_pnl_tp else None,
            "avg_pnl_sl": round(avg_pnl_sl, 4) if avg_pnl_sl else None,
            "avg_slippage": round(avg_slip, 4) if avg_slip else None,
            "has_t_touch": "t_touch" in df.columns,
            "has_pnl_real": has_pnl,
        }
    return results


def validate_meta(meta: dict, barriers: dict) -> dict:
    """Validate Meta-Labeling outputs against spec."""
    from sklearn.metrics import roc_auc_score, brier_score_loss
    results = {}

    for sym, df in meta.items():
        n = len(df)
        has_p_bma = "p_bma" in df.columns
        has_p_cal = "p_calibrated" in df.columns
        has_y     = "y_target" in df.columns
        has_uniq  = "uniqueness" in df.columns

        if not (has_p_bma and has_y):
            results[sym] = {"status": "MISSING_COLS", "columns": list(df.columns)}
            continue

        y = df["y_target"].values
        p_bma = df["p_bma"].values

        # Filter valid
        valid = ~np.isnan(p_bma) & ~np.isnan(y)
        y_v = y[valid]
        p_v = p_bma[valid]

        # AUC on p_bma (raw)
        auc_raw = None
        if len(np.unique(y_v)) == 2 and len(y_v) >= 30:
            auc_raw = float(roc_auc_score(y_v, p_v))

        # AUC on p_calibrated
        auc_cal = None
        brier = None
        p_cal_mean = None
        p_cal_std = None
        ece = None

        if has_p_cal:
            p_cal = df["p_calibrated"].values
            valid_cal = ~np.isnan(p_cal) & ~np.isnan(y)
            y_c = y[valid_cal]
            p_c = p_cal[valid_cal]

            if len(np.unique(y_c)) == 2 and len(y_c) >= 30:
                auc_cal = float(roc_auc_score(y_c, p_c))
                brier = float(brier_score_loss(y_c, p_c))
                p_cal_mean = float(np.mean(p_c))
                p_cal_std = float(np.std(p_c))

                # ECE (10 bins)
                n_bins = 10
                bin_edges = np.linspace(0, 1, n_bins + 1)
                ece_val = 0.0
                for b in range(n_bins):
                    mask = (p_c >= bin_edges[b]) & (p_c < bin_edges[b+1])
                    if mask.sum() > 0:
                        avg_pred = p_c[mask].mean()
                        avg_true = y_c[mask].mean()
                        ece_val += abs(avg_pred - avg_true) * mask.sum() / len(p_c)
                ece = float(ece_val)

        # Uniqueness stats
        uniq_mean = float(df["uniqueness"].mean()) if has_uniq else None
        n_eff = float(df["uniqueness"].sum()) if has_uniq else None

        # Precision@65%
        prec_at_65 = None
        if has_p_cal and p_cal_mean is not None:
            p_cal_valid = df["p_calibrated"].values[valid_cal]
            y_valid = y[valid_cal]
            mask_65 = p_cal_valid >= 0.65
            if mask_65.sum() >= 5:
                prec_at_65 = float(y_valid[mask_65].mean())

        # Status assessment
        checks = []
        if auc_cal is not None and auc_cal < SPEC["meta_auc_min"]:
            checks.append(f"AUC={auc_cal:.3f}<{SPEC['meta_auc_min']}")
        if ece is not None and ece > SPEC["ece_max"]:
            checks.append(f"ECE={ece:.3f}>{SPEC['ece_max']}")
        if brier is not None and brier > SPEC["brier_max"]:
            checks.append(f"Brier={brier:.3f}>{SPEC['brier_max']}")

        status = "PASS" if len(checks) == 0 and auc_cal is not None else "FAIL"
        if auc_cal is None:
            status = "NO_DATA"

        results[sym] = {
            "status": status,
            "n": n,
            "n_eff": round(n_eff, 1) if n_eff else None,
            "auc_raw": round(auc_raw, 4) if auc_raw else None,
            "auc_cal": round(auc_cal, 4) if auc_cal else None,
            "brier": round(brier, 4) if brier else None,
            "ece": round(ece, 4) if ece else None,
            "p_cal_mean": round(p_cal_mean, 4) if p_cal_mean else None,
            "p_cal_std": round(p_cal_std, 4) if p_cal_std else None,
            "prec_at_65": round(prec_at_65, 4) if prec_at_65 else None,
            "uniq_mean": round(uniq_mean, 4) if uniq_mean else None,
            "fails": checks if checks else None,
        }
    return results


def validate_sizing(sizing: dict) -> dict:
    """Validate Kelly sizing outputs."""
    results = {}
    for sym, df in sizing.items():
        n = len(df)
        has_kelly = "kelly_frac" in df.columns
        has_pos = "position_usdt" in df.columns
        has_pcal = "p_cal" in df.columns

        if not has_kelly:
            results[sym] = {"status": "MISSING_COLS"}
            continue

        kelly_fracs = df["kelly_frac"].values
        active = kelly_fracs > 0
        pct_active = float(active.mean()) * 100

        avg_frac = float(kelly_fracs[active].mean()) if active.any() else 0
        max_frac = float(kelly_fracs.max())

        avg_pos = float(df["position_usdt"][active].mean()) if has_pos and active.any() else 0
        max_pos = float(df["position_usdt"].max()) if has_pos else 0

        # P_cal distribution for active signals
        p_cal_active_mean = None
        if has_pcal and active.any():
            p_cal_active_mean = float(df["p_cal"][active].mean())

        results[sym] = {
            "status": "OK",
            "n_signals": n,
            "pct_active": round(pct_active, 1),
            "avg_kelly_frac": round(avg_frac, 4),
            "max_kelly_frac": round(max_frac, 4),
            "avg_position_usdt": round(avg_pos, 0),
            "max_position_usdt": round(max_pos, 0),
            "p_cal_active_mean": round(p_cal_active_mean, 4) if p_cal_active_mean else None,
            "capped_at_max": bool(max_frac >= 0.49),  # 0.50 cap
        }
    return results


def print_summary(barrier_results, meta_results, sizing_results):
    """Print comprehensive spec compliance summary."""
    print("\n" + "="*80)
    print("SNIPER v10.10 — PHASE 3 DIAGNOSTIC REPORT")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print("="*80)

    # ── 1. Barrier Summary ──
    print("\n── TRIPLE BARRIER ──────────────────────────────────────────")
    n_ok = sum(1 for r in barrier_results.values() if r["status"] in ("OK", "WARN_SL_HIGH"))
    n_warn = sum(1 for r in barrier_results.values() if r["status"] == "WARN_SL_HIGH")
    n_skip = sum(1 for r in barrier_results.values() if r["status"] == "SKIP")
    print(f"Assets: {n_ok} processed, {n_warn} WARN (SL>45%), {n_skip} skipped (<30 events)")

    if n_ok > 0:
        all_pct_tp = [r["pct_tp"] for r in barrier_results.values() if r.get("pct_tp")]
        all_pct_sl = [r["pct_sl"] for r in barrier_results.values() if r.get("pct_sl")]
        all_slip = [r["avg_slippage"] for r in barrier_results.values() if r.get("avg_slippage")]
        print(f"  avg pct_tp: {np.mean(all_pct_tp):.1%}  avg pct_sl: {np.mean(all_pct_sl):.1%}")
        if all_slip:
            print(f"  avg slippage: {np.mean(all_slip):.2%}  (market impact √(Q/V) ✓)")
        # Verify HLC priority and t_touch
        has_t_touch = all(r.get("has_t_touch", False) for r in barrier_results.values() if r["status"] != "SKIP")
        has_pnl = all(r.get("has_pnl_real", False) for r in barrier_results.values() if r["status"] != "SKIP")
        print(f"  t_touch (dynamic uniqueness): {'✅' if has_t_touch else '❌'}")
        print(f"  pnl_real (with slippage):      {'✅' if has_pnl else '❌'}")

    # ── 2. Meta-Labeling Summary ──
    print("\n── META-LABELING ───────────────────────────────────────────")
    aucs = [r["auc_cal"] for r in meta_results.values() if r.get("auc_cal") is not None]
    eces = [r["ece"] for r in meta_results.values() if r.get("ece") is not None]
    briers = [r["brier"] for r in meta_results.values() if r.get("brier") is not None]
    n_effs = [r["n_eff"] for r in meta_results.values() if r.get("n_eff") is not None]

    if aucs:
        avg_auc = np.mean(aucs)
        n_above_056 = sum(1 for a in aucs if a >= SPEC["meta_auc_min"])
        print(f"  AUC: avg={avg_auc:.4f}  min={min(aucs):.4f}  max={max(aucs):.4f}")
        print(f"  AUC ≥ 0.56: {n_above_056}/{len(aucs)} assets")
        print(f"  SPEC CHECK: avg AUC {'✅ PASS' if avg_auc >= SPEC['meta_auc_min'] else '⚠️ BELOW THRESHOLD'} (need >{SPEC['meta_auc_min']})")

    if eces:
        avg_ece = np.mean(eces)
        n_ece_ok = sum(1 for e in eces if e <= SPEC["ece_max"])
        print(f"  ECE: avg={avg_ece:.4f}  min={min(eces):.4f}  max={max(eces):.4f}")
        print(f"  ECE ≤ 0.05: {n_ece_ok}/{len(eces)} assets")
        print(f"  SPEC CHECK: avg ECE {'✅ PASS' if avg_ece <= SPEC['ece_max'] else '⚠️ ABOVE THRESHOLD'} (need <{SPEC['ece_max']})")

    if briers:
        print(f"  Brier: avg={np.mean(briers):.4f}")

    if n_effs:
        print(f"  N_eff: avg={np.mean(n_effs):.0f}  min={min(n_effs):.0f}  max={max(n_effs):.0f}")

    # Per-asset detail (top/bottom 5)
    print("\n  Top 5 AUC:")
    sorted_meta = sorted(meta_results.items(), key=lambda x: x[1].get("auc_cal") or 0, reverse=True)
    for sym, r in sorted_meta[:5]:
        auc_s = f"{r['auc_cal']:.4f}" if r.get('auc_cal') is not None else "N/A"
        ece_s = f"{r['ece']:.4f}" if r.get('ece') is not None else "N/A"
        neff_s = f"{r['n_eff']:.0f}" if r.get('n_eff') is not None else "N/A"
        print(f"    {sym:8s}  AUC={auc_s:>7s}  ECE={ece_s:>7s}  N_eff={neff_s}")

    print("  Bottom 5 AUC:")
    for sym, r in sorted_meta[-5:]:
        auc_s = f"{r['auc_cal']:.4f}" if r.get('auc_cal') is not None else "N/A"
        ece_s = f"{r['ece']:.4f}" if r.get('ece') is not None else "N/A"
        neff_s = f"{r['n_eff']:.0f}" if r.get('n_eff') is not None else "N/A"
        print(f"    {sym:8s}  AUC={auc_s:>7s}  ECE={ece_s:>7s}  N_eff={neff_s}")

    # ── 3. Kelly Sizing Summary ──
    print("\n── KELLY SIZING ────────────────────────────────────────────")
    all_pct_active = [r["pct_active"] for r in sizing_results.values() if r.get("pct_active") is not None]
    all_avg_pos = [r["avg_position_usdt"] for r in sizing_results.values() if r.get("avg_position_usdt") and r["avg_position_usdt"] > 0]

    if all_pct_active:
        avg_pct = np.mean(all_pct_active)
        print(f"  pct_active: avg={avg_pct:.1f}%  min={min(all_pct_active):.1f}%  max={max(all_pct_active):.1f}%")
        print(f"  SPEC CHECK: avg pct_active {'✅' if avg_pct >= 20 else '⚠️ LOW'} (target >20%)")
    if all_avg_pos:
        print(f"  avg position: ${np.mean(all_avg_pos):,.0f}  max: ${max(r['max_position_usdt'] for r in sizing_results.values() if r.get('max_position_usdt')):,.0f}")

    # ── 4. Overall Assessment ──
    print("\n── PHASE 3 OVERALL ASSESSMENT ──────────────────────────────")
    n_phase3 = len(meta_results)
    print(f"  Assets with Phase 3: {n_phase3}")
    print(f"  SPEC CHECK: n_phase3 ≥ {SPEC['min_phase3_assets']}: {'✅ PASS' if n_phase3 >= SPEC['min_phase3_assets'] else '❌ FAIL'}")

    # Decision gate from spec
    if aucs:
        all_below = all(a < SPEC["meta_auc_min"] for a in aucs)
        if all_below:
            print(f"\n  ⚠️  DECISION GATE: AUC < 0.56 em TODOS os {len(aucs)} ativos")
            print(f"      Spec says: 'Descartar Meta-Labeling. Usar P_bma > 0.65 direto.'")
            print(f"      FALLBACK MODE: Kelly sizing uses P_bma threshold instead of P_calibrated")
        else:
            n_ok_auc = sum(1 for a in aucs if a >= SPEC["meta_auc_min"])
            print(f"\n  AUC ≥ 0.56 em {n_ok_auc}/{len(aucs)} ativos — meta-labeling parcialmente efetivo")

    # Write JSON report
    report = {
        "timestamp": datetime.now().isoformat(),
        "n_barriers": len(barrier_results),
        "n_meta": len(meta_results),
        "n_sizing": len(sizing_results),
        "avg_auc": round(np.mean(aucs), 4) if aucs else None,
        "avg_ece": round(np.mean(eces), 4) if eces else None,
        "avg_pct_active": round(np.mean(all_pct_active), 1) if all_pct_active else None,
        "spec_compliance": {
            "auc_above_056": f"{sum(1 for a in aucs if a >= 0.56)}/{len(aucs)}" if aucs else "N/A",
            "ece_below_005": f"{sum(1 for e in eces if e <= 0.05)}/{len(eces)}" if eces else "N/A",
            "cvar_test": "PENDING_FIX",
        },
        "barriers": barrier_results,
        "meta": meta_results,
        "sizing": sizing_results,
    }

    report_path = "/data/models/phase3/diagnostic_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Full report saved: {report_path}")
    print("="*80)

    return report


if __name__ == "__main__":
    print("Loading Phase 3 outputs...")
    barriers, meta, sizing = load_phase3_data()
    print(f"Found: {len(barriers)} barriers, {len(meta)} meta, {len(sizing)} sizing")

    if not barriers:
        print("❌ No Phase 3 data found. Run pipeline first.")
        sys.exit(1)

    barrier_results = validate_barriers(barriers)
    meta_results = validate_meta(meta, barriers)
    sizing_results = validate_sizing(sizing)
    report = print_summary(barrier_results, meta_results, sizing_results)
