#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, roc_auc_score

from meta_labeling.uniqueness import compute_label_uniqueness
from sizing.kelly_cvar import compute_kelly_fraction

MODEL_PATH = Path('/data/models')
FEATURES_PATH = MODEL_PATH / 'features'
PHASE3_PATH = MODEL_PATH / 'phase3'
PHASE3_REPORT_PATH = MODEL_PATH / 'phase3_diagnostic_report.json'
PHASE3_NESTED_REPORT_PATH = PHASE3_PATH / 'diagnostic_report.json'
PBMA_SOURCE_PATH = Path(__file__).resolve().parent / 'meta_labeling' / 'pbma_purged.py'

REQUIRED_BARRIER_COLUMNS = [
    't_touch',
    'label',
    'barrier_tp',
    'barrier_sl',
    'exit_price',
    'pnl_real',
    'slippage_frac',
    'sigma_at_entry',
    'p0',
    'holding_days',
]
REQUIRED_META_COLUMNS = ['p_bma', 'p_calibrated', 'y_target', 'uniqueness']
REQUIRED_SIZING_COLUMNS = ['kelly_frac', 'position_usdt', 'p_cal', 'sigma', 'mu_adj']
HARD_GATE_THRESHOLD = 0.50
UNIQUENESS_TOL = 1e-6
PCAL_TOL = 1e-4
KELLY_TOL = 2e-4
POSITION_TOL = 10.0
CAPITAL_TOTAL = float(__import__('os').getenv('CAPITAL_TOTAL', '200000'))
POSITION_CAP = CAPITAL_TOTAL * 0.08


def _status(ok: bool) -> str:
    return 'PASS' if ok else 'FAIL'


def _safe_read_parquet(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    if isinstance(df.index, pd.DatetimeIndex):
        idx_name = df.index.name or 'index'
        df = df.reset_index().rename(columns={idx_name: 'index'})
    return df


def _normalize_df(path: Path, date_candidates: list[str]) -> pd.DataFrame:
    df = _safe_read_parquet(path)
    for col in date_candidates:
        if col in df.columns:
            idx = pd.to_datetime(df[col], utc=True, errors='coerce').dt.tz_localize(None)
            if idx.notna().any():
                out = df.drop(columns=[col], errors='ignore').copy()
                out.index = idx
                return out.sort_index()
    return df.copy()


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors='coerce')


def _compute_ece(probs: np.ndarray, labels: np.ndarray, n_bins: int = 10) -> float:
    if len(probs) == 0:
        return np.nan
    probs = np.clip(np.asarray(probs, dtype=float), 0.0, 1.0)
    labels = np.asarray(labels, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for idx in range(n_bins):
        upper_inclusive = idx == n_bins - 1
        in_bin = (probs >= edges[idx]) & ((probs <= edges[idx + 1]) if upper_inclusive else (probs < edges[idx + 1]))
        if not in_bin.any():
            continue
        ece += in_bin.mean() * abs(labels[in_bin].mean() - probs[in_bin].mean())
    return float(ece)


def _write_report(report: dict) -> list[str]:
    saved = []
    for path in [PHASE3_REPORT_PATH, PHASE3_NESTED_REPORT_PATH]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + '.tmp')
            with open(tmp, 'w', encoding='utf-8') as fout:
                json.dump(report, fout, indent=2, default=str)
            tmp.replace(path)
            saved.append(str(path))
        except Exception as exc:
            saved.append(f'WRITE_FAIL {path}: {exc}')
    return saved


def audit_source_guards() -> dict:
    if not PBMA_SOURCE_PATH.exists():
        return {
            'status': 'FAIL',
            'source_path': str(PBMA_SOURCE_PATH),
            'source_exists': False,
        }

    text = PBMA_SOURCE_PATH.read_text(encoding='utf-8', errors='ignore')
    purged_kfold_defined = 'class PurgedKFold' in text
    hmm_dropped = "drop(columns=['hmm_prob_bull']" in text or 'drop(columns=["hmm_prob_bull"]' in text
    hard_gate_function = 'apply_hmm_hard_gate' in text
    overall_ok = purged_kfold_defined and hmm_dropped and hard_gate_function
    return {
        'status': _status(overall_ok),
        'source_path': str(PBMA_SOURCE_PATH),
        'source_exists': True,
        'purged_kfold_defined': purged_kfold_defined,
        'hmm_dropped_from_train_df': hmm_dropped,
        'hard_gate_function_present': hard_gate_function,
    }


def audit_symbol(symbol: str) -> dict:
    barrier_path = PHASE3_PATH / f'{symbol}_barriers.parquet'
    meta_path = PHASE3_PATH / f'{symbol}_meta.parquet'
    sizing_path = PHASE3_PATH / f'{symbol}_sizing.parquet'
    feature_path = FEATURES_PATH / f'{symbol}.parquet'

    result = {
        'symbol': symbol,
        'barrier_exists': barrier_path.exists(),
        'meta_exists': meta_path.exists(),
        'sizing_exists': sizing_path.exists(),
        'feature_exists': feature_path.exists(),
    }
    if not barrier_path.exists() or not meta_path.exists():
        result['status'] = 'FAIL'
        result['reason'] = 'missing_barrier_or_meta'
        return result

    barrier_df = _normalize_df(barrier_path, ['event_date', 'date', 'timestamp', 'index'])
    meta_df = _normalize_df(meta_path, ['event_date', 'date', 'timestamp', 'index'])
    sizing_df = _normalize_df(sizing_path, ['date', 'event_date', 'timestamp', 'index']) if sizing_path.exists() else pd.DataFrame()
    feature_df = _normalize_df(feature_path, ['timestamp', 'date', 'event_date', 'index']) if feature_path.exists() else pd.DataFrame()

    barrier_columns_ok = all(column in barrier_df.columns for column in REQUIRED_BARRIER_COLUMNS)
    meta_columns_ok = all(column in meta_df.columns for column in REQUIRED_META_COLUMNS)
    sizing_columns_ok = True if sizing_df.empty and not sizing_path.exists() else all(column in sizing_df.columns for column in REQUIRED_SIZING_COLUMNS)

    t_touch = pd.to_datetime(barrier_df['t_touch'], utc=True, errors='coerce').dt.tz_localize(None) if 't_touch' in barrier_df.columns else pd.Series(dtype='datetime64[ns]')
    barrier_index = pd.DatetimeIndex(barrier_df.index) if isinstance(barrier_df.index, pd.DatetimeIndex) else pd.DatetimeIndex([])
    barrier_labels_ok = bool(set(pd.Series(barrier_df['label']).dropna().astype(int).unique()).issubset({-1, 0, 1})) if 'label' in barrier_df.columns else False
    touch_after_entry_ok = bool((t_touch >= barrier_index).all()) if len(t_touch) == len(barrier_df) and len(barrier_df) else False
    holding_match_ok = False
    if 'holding_days' in barrier_df.columns and len(t_touch) == len(barrier_df) and len(barrier_df):
        holding_expected = (t_touch - barrier_index).dt.days.to_numpy()
        holding_actual = _numeric(barrier_df['holding_days']).fillna(-999).astype(int).to_numpy()
        holding_match_ok = bool(np.array_equal(holding_expected, holding_actual))

    barrier_meta_rows_match = len(barrier_df) == len(meta_df)
    barrier_meta_index_match = bool(barrier_df.index.equals(meta_df.index))

    uniqueness_ok = False
    uniqueness_max_abs_diff = None
    if barrier_columns_ok and meta_columns_ok and barrier_meta_index_match and len(meta_df):
        recomputed_uniqueness = compute_label_uniqueness(barrier_df)
        uniq_diff = (recomputed_uniqueness - _numeric(meta_df['uniqueness'])).abs()
        uniqueness_max_abs_diff = float(uniq_diff.max()) if len(uniq_diff) else 0.0
        uniqueness_ok = uniqueness_max_abs_diff <= UNIQUENESS_TOL

    p_bma_gate_breaches = None
    p_cal_gate_breaches = None
    hard_gate_rows = 0
    if not feature_df.empty and 'hmm_prob_bull' in feature_df.columns and meta_columns_ok:
        hmm_gate = _numeric(feature_df['hmm_prob_bull']).reindex(meta_df.index)
        gate_mask = hmm_gate.notna() & (hmm_gate < HARD_GATE_THRESHOLD)
        hard_gate_rows = int(gate_mask.sum())
        p_bma_gate_breaches = int((_numeric(meta_df['p_bma']).fillna(0.0).abs() > 1e-8)[gate_mask].sum())
        p_cal_gate_breaches = int((_numeric(meta_df['p_calibrated']).fillna(0.0).abs() > 1e-8)[gate_mask].sum())

    expected_sizing_index = meta_df.index[_numeric(meta_df['p_calibrated']).notna()] if meta_columns_ok else pd.DatetimeIndex([])
    sizing_rows_expected = int(len(expected_sizing_index))
    sizing_index_match = False
    sizing_pcal_ok = False
    sizing_formula_ok = False
    sizing_position_ok = False
    sizing_pcal_max_abs_diff = None
    kelly_max_abs_diff = None
    position_max_abs_diff = None
    missing_sizing_dates = []
    extra_sizing_dates = []

    if sizing_path.exists():
        missing_sizing_dates = [str(ts) for ts in expected_sizing_index.difference(sizing_df.index)[:10]]
        extra_sizing_dates = [str(ts) for ts in sizing_df.index.difference(expected_sizing_index)[:10]]
        sizing_index_match = bool(sizing_df.index.equals(expected_sizing_index))
        if sizing_columns_ok and len(sizing_df):
            merged = sizing_df.join(meta_df[['p_calibrated']], how='left')
            if merged['p_calibrated'].notna().any():
                pcal_diff = (_numeric(merged['p_cal']) - _numeric(merged['p_calibrated'])).abs()
                sizing_pcal_max_abs_diff = float(pcal_diff.max())
                sizing_pcal_ok = sizing_pcal_max_abs_diff <= PCAL_TOL

                expected_kelly = np.array([
                    compute_kelly_fraction(mu=float(mu), sigma=float(sig), p_cal=float(pcal))
                    for mu, sig, pcal in zip(
                        _numeric(merged['mu_adj']).fillna(0.0),
                        _numeric(merged['sigma']).fillna(0.0),
                        _numeric(merged['p_cal']).fillna(0.0),
                    )
                ])
                stored_kelly = _numeric(merged['kelly_frac']).fillna(0.0).to_numpy(dtype=float)
                kelly_max_abs_diff = float(np.max(np.abs(expected_kelly - stored_kelly))) if len(stored_kelly) else 0.0
                sizing_formula_ok = kelly_max_abs_diff <= KELLY_TOL

                expected_position = np.minimum(expected_kelly * CAPITAL_TOTAL, POSITION_CAP)
                stored_position = _numeric(merged['position_usdt']).fillna(0.0).to_numpy(dtype=float)
                position_max_abs_diff = float(np.max(np.abs(expected_position - stored_position))) if len(stored_position) else 0.0
                sizing_position_ok = position_max_abs_diff <= POSITION_TOL
    else:
        sizing_index_match = sizing_rows_expected == 0
        sizing_pcal_ok = sizing_rows_expected == 0
        sizing_formula_ok = sizing_rows_expected == 0
        sizing_position_ok = sizing_rows_expected == 0

    probs = _numeric(meta_df['p_calibrated']) if meta_columns_ok else pd.Series(dtype=float)
    labels = _numeric(meta_df['y_target']) if meta_columns_ok else pd.Series(dtype=float)
    valid_metric_mask = probs.notna() & labels.notna()
    if valid_metric_mask.sum() >= 10 and labels.loc[valid_metric_mask].nunique() >= 2:
        auc = float(roc_auc_score(labels.loc[valid_metric_mask].astype(int), probs.loc[valid_metric_mask].astype(float)))
        brier = float(brier_score_loss(labels.loc[valid_metric_mask].astype(int), probs.loc[valid_metric_mask].astype(float)))
        ece = _compute_ece(probs.loc[valid_metric_mask].values, labels.loc[valid_metric_mask].values)
    else:
        auc = np.nan
        brier = np.nan
        ece = np.nan

    status_ok = all([
        barrier_columns_ok,
        meta_columns_ok,
        sizing_columns_ok,
        barrier_labels_ok,
        touch_after_entry_ok,
        holding_match_ok,
        barrier_meta_rows_match,
        barrier_meta_index_match,
        uniqueness_ok,
        (p_bma_gate_breaches in (None, 0)),
        (p_cal_gate_breaches in (None, 0)),
        sizing_index_match,
        sizing_pcal_ok,
        sizing_formula_ok,
        sizing_position_ok,
    ])

    result.update({
        'status': _status(status_ok),
        'barrier_rows': int(len(barrier_df)),
        'meta_rows': int(len(meta_df)),
        'sizing_rows': int(len(sizing_df)),
        'sizing_rows_expected': sizing_rows_expected,
        'barrier_columns_ok': barrier_columns_ok,
        'meta_columns_ok': meta_columns_ok,
        'sizing_columns_ok': sizing_columns_ok,
        'barrier_labels_ok': barrier_labels_ok,
        'touch_after_entry_ok': touch_after_entry_ok,
        'holding_days_match_ok': holding_match_ok,
        'barrier_meta_rows_match': barrier_meta_rows_match,
        'barrier_meta_index_match': barrier_meta_index_match,
        'uniqueness_ok': uniqueness_ok,
        'uniqueness_max_abs_diff': uniqueness_max_abs_diff,
        'hard_gate_rows': hard_gate_rows,
        'p_bma_gate_breaches': p_bma_gate_breaches,
        'p_cal_gate_breaches': p_cal_gate_breaches,
        'sizing_index_match': sizing_index_match,
        'sizing_pcal_ok': sizing_pcal_ok,
        'sizing_formula_ok': sizing_formula_ok,
        'sizing_position_ok': sizing_position_ok,
        'sizing_pcal_max_abs_diff': sizing_pcal_max_abs_diff,
        'kelly_max_abs_diff': kelly_max_abs_diff,
        'position_max_abs_diff': position_max_abs_diff,
        'missing_sizing_dates': missing_sizing_dates,
        'extra_sizing_dates': extra_sizing_dates,
        'auc': None if np.isnan(auc) else round(auc, 4),
        'brier': None if np.isnan(brier) else round(brier, 4),
        'ece': None if np.isnan(ece) else round(ece, 4),
    })
    return result


def main() -> None:
    print('Loading Phase 3 artifacts...')
    barrier_files = sorted(PHASE3_PATH.glob('*_barriers.parquet'))
    meta_files = sorted(PHASE3_PATH.glob('*_meta.parquet'))
    sizing_files = sorted(PHASE3_PATH.glob('*_sizing.parquet'))

    barrier_symbols = {path.stem.replace('_barriers', '') for path in barrier_files}
    meta_symbols = {path.stem.replace('_meta', '') for path in meta_files}
    sizing_symbols = {path.stem.replace('_sizing', '') for path in sizing_files}
    symbols = sorted(barrier_symbols | meta_symbols | sizing_symbols)

    details = [audit_symbol(symbol) for symbol in symbols]
    detail_df = pd.DataFrame(details)
    source_guards = audit_source_guards()

    n_row_mismatches = int((~detail_df['barrier_meta_rows_match'].fillna(False)).sum()) if not detail_df.empty else 0
    n_index_mismatches = int((~detail_df['barrier_meta_index_match'].fillna(False)).sum()) if not detail_df.empty else 0
    n_uniqueness_fail = int((~detail_df['uniqueness_ok'].fillna(False)).sum()) if not detail_df.empty else 0
    n_hard_gate_fail = int(((detail_df['p_bma_gate_breaches'].fillna(0) > 0) | (detail_df['p_cal_gate_breaches'].fillna(0) > 0)).sum()) if not detail_df.empty else 0
    n_sizing_alignment_fail = int((~detail_df['sizing_index_match'].fillna(False)).sum()) if not detail_df.empty else 0
    n_sizing_formula_fail = int((~detail_df['sizing_formula_ok'].fillna(False)).sum()) if not detail_df.empty else 0
    n_symbols_pass = int((detail_df['status'] == 'PASS').sum()) if not detail_df.empty else 0

    report = {
        'timestamp_utc': datetime.utcnow().isoformat(),
        'overall_status': _status(bool(len(details)) and n_symbols_pass == len(details) and source_guards['status'] == 'PASS'),
        'inventory': {
            'barrier_files': len(barrier_files),
            'meta_files': len(meta_files),
            'sizing_files': len(sizing_files),
            'symbols': symbols,
        },
        'source_guards': source_guards,
        'summary': {
            'symbols_checked': len(details),
            'symbols_pass': n_symbols_pass,
            'n_row_mismatches': n_row_mismatches,
            'n_index_mismatches': n_index_mismatches,
            'n_uniqueness_failures': n_uniqueness_fail,
            'n_hard_gate_failures': n_hard_gate_fail,
            'n_sizing_alignment_failures': n_sizing_alignment_fail,
            'n_sizing_formula_failures': n_sizing_formula_fail,
            'auc_avg': float(detail_df['auc'].dropna().mean()) if not detail_df.empty and detail_df['auc'].notna().any() else None,
            'ece_avg': float(detail_df['ece'].dropna().mean()) if not detail_df.empty and detail_df['ece'].notna().any() else None,
        },
        'details': details,
    }
    saved_paths = _write_report(report)

    print('\n' + '=' * 88)
    print('SNIPER v10.10 - PHASE 3 AUDIT')
    print('=' * 88)
    print(f"overall_status: {report['overall_status']}")
    print(f"source_guards:   {source_guards['status']}")
    print(f"symbols_pass:    {n_symbols_pass}/{len(details)}")
    print(f"row_mismatches:  {n_row_mismatches}")
    print(f"index_mismatch:  {n_index_mismatches}")
    print(f"uniqueness_fail: {n_uniqueness_fail}")
    print(f"hard_gate_fail:  {n_hard_gate_fail}")
    print(f"sizing_fail:     {n_sizing_alignment_fail}")
    print(f"sizing_formula:  {n_sizing_formula_fail}")

    if not detail_df.empty:
        bad_rows = detail_df[detail_df['status'] == 'FAIL'][['symbol', 'barrier_rows', 'meta_rows', 'sizing_rows', 'sizing_rows_expected']]
        if not bad_rows.empty:
            print('\nFailing symbols:')
            for _, row in bad_rows.head(15).iterrows():
                print(
                    f"  - {row['symbol']}: barriers={int(row['barrier_rows'])}, "
                    f"meta={int(row['meta_rows'])}, sizing={int(row['sizing_rows'])}, "
                    f"expected_sizing={int(row['sizing_rows_expected'])}"
                )

    print('\nReports saved:')
    for path in saved_paths:
        print(f'  - {path}')
    print('=' * 88)


if __name__ == '__main__':
    main()


