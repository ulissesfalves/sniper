#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import f1_score
from sklearn.preprocessing import RobustScaler
from features.onchain import UNLOCK_MODEL_FEATURE_COLUMNS

from regime.pca_robust import transform_robust_pca
try:
    from .regime.hmm_filter import build_regime_target
except ImportError:
    from regime.hmm_filter import build_regime_target

MODEL_PATH = Path('/data/models')
FEATURES_PATH = MODEL_PATH / 'features'
HMM_PATH = MODEL_PATH / 'hmm'
PHASE3_PATH = MODEL_PATH / 'phase3'
PHASE2_REPORT_PATH = MODEL_PATH / 'phase2_diagnostic_report.json'
PHASE2_NESTED_REPORT_PATH = MODEL_PATH / 'phase2' / 'diagnostic_report.json'
PARQUET_BASE = Path('/data/parquet')
MAIN_SOURCE_PATH = Path(__file__).resolve().parent / 'main.py'
PCA_SOURCE_PATH = Path(__file__).resolve().parent / 'regime' / 'pca_robust.py'
UNLOCK_DIAGNOSTICS_PATH = PARQUET_BASE / 'unlock_diagnostics' / 'unlock_quality_daily.parquet'

REQUIRED_FEATURES = [
    'ret_1d',
    'ret_5d',
    'ret_20d',
    'realized_vol_30d',
    'vol_ratio',
    'funding_rate_ma7d',
    'basis_3m',
    'stablecoin_chg30',
    'btc_ma200_flag',
    'dvol_zscore',
    *UNLOCK_MODEL_FEATURE_COLUMNS,
]
HMM_REQUIRED_FEATURES = [
    'ret_1d',
    'ret_5d',
    'realized_vol_30d',
    'vol_ratio',
    'funding_rate_ma7d',
    'basis_3m',
    'stablecoin_chg30',
    'btc_ma200_flag',
    'dvol_zscore',
]
REQUIRED_COLLAPSED_ASSETS = ['LUNA', 'LUNA2', 'FTT', 'CEL']
STALE_PROXY_COLUMNS = ['drawdown_pct', 'term_spread', 'volume_momentum']
HMM_F1_MIN = 0.45
HMM_BEAR_2022_MIN = 0.60
HMM_VAR_EXP_MIN = 0.80
MAX_PCA_COMPONENTS = 5
HMM_CRASH_SIGMA_MAX = 3.0
VI_THRESHOLD_SPEC = 0.30
FEATURE_MIN_HISTORY_DAYS = 365
FEATURE_STORE_DENSE_FEATURES = [
    'ret_1d',
    'ret_5d',
    'ret_20d',
    'realized_vol_30d',
    'vol_ratio',
    'stablecoin_chg30',
    'btc_ma200_flag',
    'dvol_zscore',
]
FEATURE_STORE_SPARSE_FEATURES = [
    'funding_rate_ma7d',
    'basis_3m',
    *UNLOCK_MODEL_FEATURE_COLUMNS,
]
HMM_DEGRADABLE_FEATURES = ['funding_rate_ma7d', 'basis_3m']
UNLOCK_MODEL_FEATURE_SET = os.getenv('UNLOCK_MODEL_FEATURE_SET', 'baseline').strip().lower()
MODEL_RUN_TAG = os.getenv('MODEL_RUN_TAG', '').strip()
UNLOCK_PROXY_FEATURE_COLUMNS = [
    'unlock_overhang_proxy_rank_full',
    'unlock_fragility_proxy_rank_fallback',
]
HMM_HARD_REQUIRED_FEATURES = [
    feature for feature in HMM_REQUIRED_FEATURES
    if feature not in HMM_DEGRADABLE_FEATURES
]


def _status(ok: bool) -> str:
    return 'PASS' if ok else 'FAIL'


def get_unlock_model_feature_columns(mode: str | None = None) -> list[str]:
    normalized = (mode or UNLOCK_MODEL_FEATURE_SET or 'baseline').strip().lower()
    if normalized in {'baseline', 'none', 'off'}:
        return []
    if normalized == 'proxies':
        return UNLOCK_PROXY_FEATURE_COLUMNS.copy()
    return list(UNLOCK_MODEL_FEATURE_COLUMNS)


def _safe_read_parquet(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_parquet(path)
    except Exception:
        df = pd.read_pickle(path)
    if isinstance(df.index, pd.DatetimeIndex):
        idx_name = df.index.name or 'index'
        df = df.reset_index().rename(columns={idx_name: 'index'})
    return df


def _coerce_datetime_index(df: pd.DataFrame, candidates: list[str]) -> pd.DataFrame:
    for col in candidates:
        if col in df.columns:
            idx = pd.to_datetime(df[col], utc=True, errors='coerce').dt.tz_localize(None)
            if idx.notna().any():
                out = df.drop(columns=[col], errors='ignore').copy()
                out.index = idx
                return out.sort_index()
    return df.copy()


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors='coerce')


def _compute_psi(expected: pd.Series, actual: pd.Series, n_bins: int = 10) -> float | None:
    expected_num = _numeric(expected).dropna()
    actual_num = _numeric(actual).dropna()
    if len(expected_num) < 10 or len(actual_num) < 10:
        return None

    quantiles = expected_num.quantile(np.linspace(0.0, 1.0, n_bins + 1)).to_numpy(dtype=float)
    quantiles = np.unique(quantiles)
    if len(quantiles) < 2:
        return 0.0

    expected_bins = pd.cut(expected_num, bins=quantiles, include_lowest=True, duplicates='drop')
    actual_bins = pd.cut(actual_num, bins=quantiles, include_lowest=True, duplicates='drop')
    categories = expected_bins.cat.categories
    expected_dist = expected_bins.value_counts(normalize=True, sort=False).reindex(categories, fill_value=0.0)
    actual_dist = actual_bins.value_counts(normalize=True, sort=False).reindex(categories, fill_value=0.0)
    eps = 1e-6
    psi = ((actual_dist + eps) - (expected_dist + eps)) * np.log((actual_dist + eps) / (expected_dist + eps))
    return float(psi.sum())


def _select_latest_operational_quality_row(quality_summary: pd.DataFrame) -> tuple[dict, dict | None]:
    if quality_summary.empty:
        return {}, None

    ordered = quality_summary.sort_values('date').reset_index(drop=True)
    raw_latest = ordered.iloc[-1].to_dict()

    if 'n_assets' not in ordered.columns:
        return raw_latest, None

    n_assets = pd.to_numeric(ordered['n_assets'], errors='coerce')
    valid_counts = n_assets.dropna()
    if valid_counts.empty:
        return raw_latest, None

    recent_window = valid_counts.tail(30)
    reference_count = float(recent_window.median())
    minimum_operational_assets = max(10.0, reference_count * 0.8)
    eligible = ordered.loc[n_assets >= minimum_operational_assets]
    if eligible.empty:
        return raw_latest, None

    operational_latest = eligible.iloc[-1].to_dict()
    metadata = {
        'raw_latest_date': raw_latest.get('date'),
        'raw_latest_n_assets': None if pd.isna(raw_latest.get('n_assets')) else int(raw_latest.get('n_assets')),
        'operational_latest_date': operational_latest.get('date'),
        'operational_latest_n_assets': None if pd.isna(operational_latest.get('n_assets')) else int(operational_latest.get('n_assets')),
        'min_operational_assets': round(float(minimum_operational_assets), 2),
        'operational_reference_assets': round(reference_count, 2),
        'partial_latest_filtered': bool(raw_latest.get('date') != operational_latest.get('date')),
    }
    return operational_latest, metadata


def _project_quality_row(row: dict | None) -> dict:
    if not row:
        return {}

    return {
        'date': row.get('date'),
        'n_assets': row.get('n_assets'),
        'observed_coverage': row.get('observed_coverage'),
        'reconstructed_coverage': row.get('reconstructed_coverage'),
        'proxy_full_coverage': row.get('proxy_full_coverage'),
        'proxy_fallback_coverage': row.get('proxy_fallback_coverage'),
        'missing_rate': row.get('missing_rate'),
        'unknown_bucket_block_rate': row.get('unknown_bucket_block_rate'),
        'massive_ties_fraction': row.get('massive_ties_fraction'),
        'shadow_mode_flag': row.get('shadow_mode_flag'),
    }


def _parse_hmm_step(path: Path) -> int:
    stem = path.stem
    if 'hmm_t' not in stem:
        return -1
    try:
        return int(stem.split('hmm_t', 1)[1])
    except ValueError:
        return -1


def _write_report(report: dict) -> list[str]:
    saved = []
    paths = [PHASE2_REPORT_PATH, PHASE2_NESTED_REPORT_PATH]
    suffix_parts: list[str] = []
    if UNLOCK_MODEL_FEATURE_SET not in {'', 'full'}:
        suffix_parts.append(UNLOCK_MODEL_FEATURE_SET)
    if MODEL_RUN_TAG:
        suffix_parts.append(MODEL_RUN_TAG)
    if suffix_parts:
        suffix = '_' + '_'.join(suffix_parts)
        paths.extend([
            MODEL_PATH / f'phase2_diagnostic_report{suffix}.json',
            MODEL_PATH / 'phase2' / f'diagnostic_report{suffix}.json',
        ])
    for path in paths:
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


def _load_ohlcv_history_lengths() -> dict[str, int]:
    ohlcv_dir = PARQUET_BASE / 'ohlcv_daily'
    lengths: dict[str, int] = {}
    if not ohlcv_dir.exists():
        return lengths

    for path in sorted(ohlcv_dir.glob('*.parquet')):
        df = _coerce_datetime_index(_safe_read_parquet(path), ['timestamp', 'date', 'event_date', 'index'])
        lengths[path.stem] = int(len(df))
    return lengths


def audit_feature_store() -> dict:
    files = sorted(FEATURES_PATH.glob('*.parquet'))
    missing_required_counts = {feature: 0 for feature in REQUIRED_FEATURES}
    nonnull_required_counts = {feature: 0 for feature in REQUIRED_FEATURES}
    stale_proxy_counts = {feature: 0 for feature in STALE_PROXY_COLUMNS}
    asset_details = []
    symbols = []
    global_start = None
    global_end = None

    for path in files:
        symbol = path.stem
        symbols.append(symbol)
        df = _coerce_datetime_index(
            _safe_read_parquet(path),
            ['timestamp', 'date', 'event_date', 'index'],
        )
        columns = set(df.columns)
        missing = [feature for feature in REQUIRED_FEATURES if feature not in columns]
        stale = [feature for feature in STALE_PROXY_COLUMNS if feature in columns]
        nonnull = {
            feature: bool(feature in df.columns and _numeric(df[feature]).notna().any())
            for feature in REQUIRED_FEATURES
        }

        for feature in missing:
            missing_required_counts[feature] += 1
        for feature, present in nonnull.items():
            if present:
                nonnull_required_counts[feature] += 1
        for feature in stale:
            stale_proxy_counts[feature] += 1

        date_start = str(df.index.min())[:10] if isinstance(df.index, pd.DatetimeIndex) and len(df) else None
        date_end = str(df.index.max())[:10] if isinstance(df.index, pd.DatetimeIndex) and len(df) else None
        if date_start is not None:
            global_start = date_start if global_start is None else min(global_start, date_start)
        if date_end is not None:
            global_end = date_end if global_end is None else max(global_end, date_end)

        asset_details.append({
            'symbol': symbol,
            'rows': int(len(df)),
            'date_start': date_start,
            'date_end': date_end,
            'missing_required': missing,
            'stale_proxy_columns': stale,
            'nonnull_required': nonnull,
        })

    symbols = sorted(symbols)
    n_assets = len(symbols)
    history_lengths = _load_ohlcv_history_lengths()
    eligible_symbols = sorted([
        symbol for symbol, n_rows in history_lengths.items()
        if n_rows >= FEATURE_MIN_HISTORY_DAYS
    ])
    eligible_set = set(eligible_symbols)
    feature_set = set(symbols)
    missing_eligible_assets = sorted(eligible_set - feature_set)
    unexpected_assets = sorted(feature_set - eligible_set)
    missing_collapsed_assets = [symbol for symbol in REQUIRED_COLLAPSED_ASSETS if symbol not in history_lengths]
    collapsed_below_history_threshold = [
        symbol for symbol in REQUIRED_COLLAPSED_ASSETS
        if symbol in history_lengths and history_lengths[symbol] < FEATURE_MIN_HISTORY_DAYS
    ]
    schema_ok = (
        n_assets == len(eligible_symbols)
        and not missing_eligible_assets
        and not unexpected_assets
        and not missing_collapsed_assets
        and all(count == 0 for count in missing_required_counts.values())
        and all(count == 0 for count in stale_proxy_counts.values())
    )
    dense_population_counts = {
        feature: nonnull_required_counts[feature]
        for feature in FEATURE_STORE_DENSE_FEATURES
    }
    dense_population_ok = n_assets > 0 and all(count == n_assets for count in dense_population_counts.values())
    sparse_feature_coverage = {
        feature: round(float(nonnull_required_counts[feature] / max(n_assets, 1)), 4)
        for feature in FEATURE_STORE_SPARSE_FEATURES
    }

    return {
        'status': _status(schema_ok and dense_population_ok),
        'n_assets': n_assets,
        'expected_assets': len(eligible_symbols),
        'asset_count_ok': n_assets == len(eligible_symbols),
        'symbols': symbols,
        'eligible_symbols': eligible_symbols,
        'missing_eligible_assets': missing_eligible_assets,
        'unexpected_assets': unexpected_assets,
        'missing_collapsed_assets': missing_collapsed_assets,
        'collapsed_below_history_threshold': collapsed_below_history_threshold,
        'schema_ok': schema_ok,
        'population_ok': dense_population_ok,
        'missing_required_counts': missing_required_counts,
        'nonnull_required_counts': nonnull_required_counts,
        'dense_population_counts': dense_population_counts,
        'sparse_feature_coverage': sparse_feature_coverage,
        'stale_proxy_counts': stale_proxy_counts,
        'global_date_start': global_start,
        'global_date_end': global_end,
        'details': asset_details,
    }


def _inspect_source_file(path: Path, preferred_columns: list[str]) -> dict:
    if not path.exists():
        return {
            'exists': False,
            'path': str(path),
            'rows': 0,
            'columns': [],
        }

    df = _safe_read_parquet(path)
    cols = list(df.columns)
    matched = [col for col in preferred_columns if col in cols]
    return {
        'exists': True,
        'path': str(path),
        'rows': int(len(df)),
        'columns': cols,
        'matched_columns': matched,
    }


def audit_upstream_inputs() -> dict:
    funding_dir = PARQUET_BASE / 'funding'
    basis_dir = PARQUET_BASE / 'basis'
    unlock_candidates = [PARQUET_BASE / 'unlocks', PARQUET_BASE / 'ups', PARQUET_BASE / 'onchain']
    stablecoin_candidates = [
        PARQUET_BASE / 'stablecoin' / 'stablecoin_chg30.parquet',
        PARQUET_BASE / 'stablecoin_chg30.parquet',
        PARQUET_BASE / 'macro' / 'stablecoin_chg30.parquet',
    ]

    funding_files = sorted(funding_dir.glob('*.parquet')) if funding_dir.exists() else []
    basis_files = sorted(basis_dir.glob('*.parquet')) if basis_dir.exists() else []
    stablecoin_path = next((path for path in stablecoin_candidates if path.exists()), None)

    unlock_files = []
    for directory in unlock_candidates:
        if directory.exists():
            unlock_files = sorted(directory.glob('*.parquet'))
            if unlock_files:
                break

    funding_sample = _inspect_source_file(
        funding_files[0],
        ['timestamp', 'funding_rate_ma7d', 'funding_rate'],
    ) if funding_files else {'exists': False, 'rows': 0, 'columns': []}
    basis_sample = _inspect_source_file(
        basis_files[0],
        ['timestamp', 'basis_3m', 'basis'],
    ) if basis_files else {'exists': False, 'rows': 0, 'columns': []}
    stablecoin_sample = _inspect_source_file(
        stablecoin_path,
        ['timestamp', 'stablecoin_chg30'],
    ) if stablecoin_path else {'exists': False, 'rows': 0, 'columns': []}
    unlock_sample = _inspect_source_file(
        unlock_files[0],
        ['timestamp', *UNLOCK_MODEL_FEATURE_COLUMNS, 'unlock_pressure_rank_selected_for_reporting'],
    ) if unlock_files else {'exists': False, 'rows': 0, 'columns': []}

    return {
        'funding_files': len(funding_files),
        'basis_files': len(basis_files),
        'stablecoin_present': stablecoin_path is not None,
        'unlock_files': len(unlock_files),
        'funding_sample': funding_sample,
        'basis_sample': basis_sample,
        'stablecoin_sample': stablecoin_sample,
        'unlock_sample': unlock_sample,
    }


def audit_unlock_shadow_mode() -> dict:
    unlock_candidates = [PARQUET_BASE / 'unlocks', PARQUET_BASE / 'ups', PARQUET_BASE / 'onchain']
    unlock_files = []
    for directory in unlock_candidates:
        if directory.exists():
            unlock_files = sorted(directory.glob('*.parquet'))
            if unlock_files:
                break

    if not unlock_files:
        return {
            'status': 'FAIL',
            'unlock_files': 0,
            'quality_summary_present': False,
            'coverage_by_feature': {},
            'pairwise_correlation': {},
            'drift_psi': {},
            'baseline_vs_augmented': {},
        }

    stacked_frames = []
    for path in unlock_files:
        df = _coerce_datetime_index(_safe_read_parquet(path), ['timestamp', 'date', 'event_date', 'index'])
        if df.empty:
            continue
        cols = [
            col for col in (
                UNLOCK_MODEL_FEATURE_COLUMNS
                + ['unlock_pressure_rank_selected_for_reporting', 'unlock_feature_state']
            )
            if col in df.columns
        ]
        if not cols:
            continue
        frame = df[cols].copy()
        frame['symbol'] = path.stem
        frame['date'] = frame.index
        stacked_frames.append(frame.reset_index(drop=True))

    if not stacked_frames:
        return {
            'status': 'FAIL',
            'unlock_files': len(unlock_files),
            'quality_summary_present': UNLOCK_DIAGNOSTICS_PATH.exists(),
            'coverage_by_feature': {},
            'pairwise_correlation': {},
            'drift_psi': {},
            'baseline_vs_augmented': {},
        }

    stacked = pd.concat(stacked_frames, ignore_index=True).sort_values('date')
    effective_unlock_features = [
        col for col in get_unlock_model_feature_columns()
        if col in stacked.columns
    ]
    coverage_by_feature = {
        col: round(float(_numeric(stacked[col]).notna().mean()), 4)
        for col in UNLOCK_MODEL_FEATURE_COLUMNS
        if col in stacked.columns
    }
    effective_x_feature_coverage = {
        col: coverage_by_feature[col]
        for col in effective_unlock_features
        if col in coverage_by_feature
    }
    audit_only_coverage = {}
    if 'unlock_pressure_rank_selected_for_reporting' in stacked.columns:
        audit_only_coverage['unlock_pressure_rank_selected_for_reporting'] = round(
            float(_numeric(stacked['unlock_pressure_rank_selected_for_reporting']).notna().mean()),
            4,
        )
    stacked_recent = stacked.tail(90)
    stacked_unlock_present = [col for col in UNLOCK_MODEL_FEATURE_COLUMNS if col in stacked.columns]
    stacked_unlock_any_valid_avg = None
    stacked_unlock_any_valid_recent = None
    stacked_legacy_all_avg = None
    stacked_legacy_all_recent = None
    stacked_selected_reporting_avg = None
    stacked_selected_reporting_recent = None
    if stacked_unlock_present:
        stacked_unlock_any_valid_avg = round(float(stacked[stacked_unlock_present].notna().any(axis=1).mean()), 4)
        stacked_unlock_any_valid_recent = round(float(stacked_recent[stacked_unlock_present].notna().any(axis=1).mean()), 4)
        stacked_legacy_all_avg = round(float(stacked[stacked_unlock_present].notna().all(axis=1).mean()), 4)
        stacked_legacy_all_recent = round(float(stacked_recent[stacked_unlock_present].notna().all(axis=1).mean()), 4)
    if 'unlock_pressure_rank_selected_for_reporting' in stacked.columns:
        stacked_selected_reporting_avg = round(
            float(_numeric(stacked['unlock_pressure_rank_selected_for_reporting']).notna().mean()),
            4,
        )
        stacked_selected_reporting_recent = round(
            float(_numeric(stacked_recent['unlock_pressure_rank_selected_for_reporting']).notna().mean()),
            4,
        )

    corr_matrix = (
        stacked.reindex(columns=UNLOCK_MODEL_FEATURE_COLUMNS)
        .apply(pd.to_numeric, errors='coerce')
        .corr(min_periods=10)
    )
    pairwise_correlation = {
        f'{left}__{right}': None if pd.isna(corr_matrix.loc[left, right]) else round(float(corr_matrix.loc[left, right]), 4)
        for left in UNLOCK_MODEL_FEATURE_COLUMNS
        for right in UNLOCK_MODEL_FEATURE_COLUMNS
        if left < right and left in corr_matrix.columns and right in corr_matrix.columns
    }

    split_idx = max(1, len(stacked) // 2)
    early = stacked.iloc[:split_idx]
    late = stacked.iloc[split_idx:]
    drift_psi = {
        col: _compute_psi(early[col], late[col])
        for col in UNLOCK_MODEL_FEATURE_COLUMNS
        if col in early.columns and col in late.columns
    }

    feature_files = sorted(FEATURES_PATH.glob('*.parquet'))
    baseline_core_features = FEATURE_STORE_DENSE_FEATURES.copy()
    baseline_complete = []
    augmented_complete = []
    baseline_complete_recent = []
    augmented_complete_recent = []
    hmm_eligible = []
    hmm_eligible_recent = []
    hmm_full = []
    hmm_full_recent = []
    unlock_any_valid = []
    unlock_any_valid_recent = []
    legacy_unlock_all = []
    legacy_unlock_all_recent = []
    selected_for_reporting = []
    selected_for_reporting_recent = []
    for path in feature_files:
        df = _coerce_datetime_index(_safe_read_parquet(path), ['timestamp', 'date', 'event_date', 'index'])
        if df.empty:
            continue
        recent_df = df.tail(90)
        baseline_present = [feature for feature in baseline_core_features if feature in df.columns]
        hmm_hard_present = [feature for feature in HMM_HARD_REQUIRED_FEATURES if feature in df.columns]
        hmm_full_present = [feature for feature in HMM_REQUIRED_FEATURES if feature in df.columns]
        effective_unlock_present = [feature for feature in effective_unlock_features if feature in df.columns]
        legacy_unlock_present = [feature for feature in UNLOCK_MODEL_FEATURE_COLUMNS if feature in df.columns]

        if baseline_present:
            baseline_mask = df[baseline_present].notna().all(axis=1)
            recent_baseline_mask = recent_df[baseline_present].notna().all(axis=1)
            baseline_complete.append(float(baseline_mask.mean()))
            baseline_complete_recent.append(float(recent_baseline_mask.mean()))
        else:
            baseline_mask = pd.Series(False, index=df.index)
            recent_baseline_mask = pd.Series(False, index=recent_df.index)

        if hmm_hard_present:
            hmm_eligible.append(float(df[hmm_hard_present].notna().all(axis=1).mean()))
            hmm_eligible_recent.append(float(recent_df[hmm_hard_present].notna().all(axis=1).mean()))
        if hmm_full_present:
            hmm_full.append(float(df[hmm_full_present].notna().all(axis=1).mean()))
            hmm_full_recent.append(float(recent_df[hmm_full_present].notna().all(axis=1).mean()))

        if legacy_unlock_present:
            unlock_any_mask = df[legacy_unlock_present].notna().any(axis=1)
            recent_unlock_any_mask = recent_df[legacy_unlock_present].notna().any(axis=1)
            unlock_any_valid.append(float(unlock_any_mask.mean()))
            unlock_any_valid_recent.append(float(recent_unlock_any_mask.mean()))
        if effective_unlock_present:
            augmented_complete.append(float((baseline_mask & unlock_any_mask).mean()))
            augmented_complete_recent.append(float((recent_baseline_mask & recent_unlock_any_mask).mean()))
        elif baseline_present:
            augmented_complete.append(float(baseline_mask.mean()))
            augmented_complete_recent.append(float(recent_baseline_mask.mean()))

        if legacy_unlock_present:
            legacy_unlock_all.append(float(df[legacy_unlock_present].notna().all(axis=1).mean()))
            legacy_unlock_all_recent.append(float(recent_df[legacy_unlock_present].notna().all(axis=1).mean()))

        if 'unlock_pressure_rank_selected_for_reporting' in df.columns:
            selected_for_reporting.append(
                float(_numeric(df['unlock_pressure_rank_selected_for_reporting']).notna().mean())
            )
            selected_for_reporting_recent.append(
                float(_numeric(recent_df['unlock_pressure_rank_selected_for_reporting']).notna().mean())
            )

    quality_summary = None
    quality_summary_present = UNLOCK_DIAGNOSTICS_PATH.exists()
    if quality_summary_present:
        quality_summary = _safe_read_parquet(UNLOCK_DIAGNOSTICS_PATH)

    latest_quality = {}
    raw_latest_quality = {}
    latest_quality_metadata = {}
    if quality_summary is not None and not quality_summary.empty:
        latest, metadata = _select_latest_operational_quality_row(quality_summary)
        latest_quality = _project_quality_row(latest)
        raw_latest_quality = _project_quality_row(quality_summary.sort_values('date').iloc[-1].to_dict())
        latest_quality_metadata = metadata or {}

    status_ok = bool(coverage_by_feature) and quality_summary_present
    return {
        'status': _status(status_ok),
        'unlock_files': len(unlock_files),
        'quality_summary_present': quality_summary_present,
        'coverage_by_feature': coverage_by_feature,
        'coverage_scope': 'historical_unlock_rows_non_null_ratio',
        'effective_x_feature_coverage': effective_x_feature_coverage,
        'audit_only_coverage': audit_only_coverage,
        'pairwise_correlation': pairwise_correlation,
        'drift_psi': drift_psi,
        'baseline_vs_augmented': {
            'unlock_model_feature_set': UNLOCK_MODEL_FEATURE_SET,
            'baseline_feature_count': len(baseline_core_features),
            'effective_unlock_feature_count': len(effective_unlock_features),
            'effective_unlock_features': effective_unlock_features,
            'augmented_feature_count': len(baseline_core_features) + len(effective_unlock_features),
            'baseline_complete_row_ratio_avg': round(float(np.mean(baseline_complete)), 4) if baseline_complete else None,
            'augmented_complete_row_ratio_avg': round(float(np.mean(augmented_complete)), 4) if augmented_complete else None,
            'baseline_complete_row_ratio_last90d_avg': round(float(np.mean(baseline_complete_recent)), 4) if baseline_complete_recent else None,
            'augmented_complete_row_ratio_last90d_avg': round(float(np.mean(augmented_complete_recent)), 4) if augmented_complete_recent else None,
            'hmm_eligible_row_ratio_avg': round(float(np.mean(hmm_eligible)), 4) if hmm_eligible else None,
            'hmm_eligible_row_ratio_last90d_avg': round(float(np.mean(hmm_eligible_recent)), 4) if hmm_eligible_recent else None,
            'hmm_full_input_row_ratio_avg': round(float(np.mean(hmm_full)), 4) if hmm_full else None,
            'hmm_full_input_row_ratio_last90d_avg': round(float(np.mean(hmm_full_recent)), 4) if hmm_full_recent else None,
            'unlock_any_valid_row_ratio_avg': (
                stacked_unlock_any_valid_avg
                if stacked_unlock_any_valid_avg is not None
                else round(float(np.mean(unlock_any_valid)), 4) if unlock_any_valid else None
            ),
            'unlock_any_valid_row_ratio_last90d_avg': (
                stacked_unlock_any_valid_recent
                if stacked_unlock_any_valid_recent is not None
                else round(float(np.mean(unlock_any_valid_recent)), 4) if unlock_any_valid_recent else None
            ),
            'selected_for_reporting_coverage_avg': (
                stacked_selected_reporting_avg
                if stacked_selected_reporting_avg is not None
                else round(float(np.mean(selected_for_reporting)), 4) if selected_for_reporting else None
            ),
            'selected_for_reporting_coverage_last90d_avg': (
                stacked_selected_reporting_recent
                if stacked_selected_reporting_recent is not None
                else round(float(np.mean(selected_for_reporting_recent)), 4) if selected_for_reporting_recent else None
            ),
            'legacy_all_unlock_columns_nonnull_row_ratio_avg': (
                stacked_legacy_all_avg
                if stacked_legacy_all_avg is not None
                else round(float(np.mean(legacy_unlock_all)), 4) if legacy_unlock_all else None
            ),
            'legacy_all_unlock_columns_nonnull_row_ratio_last90d_avg': (
                stacked_legacy_all_recent
                if stacked_legacy_all_recent is not None
                else round(float(np.mean(legacy_unlock_all_recent)), 4) if legacy_unlock_all_recent else None
            ),
        },
        'latest_quality': latest_quality,
        'raw_latest_quality': raw_latest_quality,
        'latest_quality_metadata': latest_quality_metadata,
    }


def audit_source_guards() -> dict:
    if not MAIN_SOURCE_PATH.exists() or not PCA_SOURCE_PATH.exists():
        return {
            'status': 'FAIL',
            'main_source_exists': MAIN_SOURCE_PATH.exists(),
            'pca_source_exists': PCA_SOURCE_PATH.exists(),
        }

    main_text = MAIN_SOURCE_PATH.read_text(encoding='utf-8', errors='ignore')
    pca_text = PCA_SOURCE_PATH.read_text(encoding='utf-8', errors='ignore')

    strict_required_inputs_enforced = 'hmm.required_inputs_missing' in main_text
    zero_fill_fallback_absent = 'feat_hmm.ffill().fillna(0)' not in main_text
    hmm_min_train_guard = 'len(valid_rows) < max(126, HMM_MIN_TRAIN_OBS)' in main_text
    configurable_min_variance = 'min_variance=HMM_MIN_VARIANCE' in main_text
    adaptive_pca_cap_ok = ('MAX_PCA_COMPONENTS   = 5' in pca_text) or ('MAX_PCA_COMPONENTS = 5' in pca_text)
    winsorized_robust_pca = 'RobustScaler' in pca_text and 'fit_winsorizer' in pca_text

    overall_ok = all([
        strict_required_inputs_enforced,
        zero_fill_fallback_absent,
        hmm_min_train_guard,
        configurable_min_variance,
        adaptive_pca_cap_ok,
        winsorized_robust_pca,
    ])
    return {
        'status': _status(overall_ok),
        'main_source_path': str(MAIN_SOURCE_PATH),
        'pca_source_path': str(PCA_SOURCE_PATH),
        'strict_required_inputs_enforced': strict_required_inputs_enforced,
        'zero_fill_fallback_absent': zero_fill_fallback_absent,
        'hmm_min_train_guard': hmm_min_train_guard,
        'configurable_min_variance': configurable_min_variance,
        'adaptive_pca_cap_ok': adaptive_pca_cap_ok,
        'winsorized_robust_pca': winsorized_robust_pca,
    }


def _empty_crash_check() -> dict:
    return {
        'mar_2020_pc1_abs_zmax': None,
        'mar_2020_pc1_ok': None,
        'nov_2022_pc1_abs_zmax': None,
        'nov_2022_pc1_ok': None,
    }


def _compute_pc1_crash_checks(df: pd.DataFrame, fitted: object) -> dict:
    pca_pipeline = getattr(fitted, 'pca_pipeline', None)
    if pca_pipeline is None:
        return _empty_crash_check()

    feature_names = list(getattr(pca_pipeline, 'feature_names', []) or [])
    if not feature_names:
        return _empty_crash_check()
    if any(feature not in df.columns for feature in feature_names):
        return _empty_crash_check()

    feat_df = df[feature_names].apply(pd.to_numeric, errors='coerce').ffill().dropna()
    if len(feat_df) < 30:
        return _empty_crash_check()

    try:
        X_pca = transform_robust_pca(feat_df.to_numpy(dtype=float), pca_pipeline)
    except Exception:
        return _empty_crash_check()

    pc1 = pd.Series(X_pca[:, 0], index=feat_df.index)
    std = float(pc1.std(ddof=0))
    if not np.isfinite(std) or std < 1e-10:
        return _empty_crash_check()

    zscore = (pc1 - float(pc1.mean())) / std
    result = {}
    for label, start, end in [
        ('mar_2020', '2020-03-01', '2020-03-31'),
        ('nov_2022', '2022-11-01', '2022-11-30'),
    ]:
        mask = (zscore.index >= start) & (zscore.index <= end)
        if mask.any():
            max_abs = float(zscore.loc[mask].abs().max())
            result[f'{label}_pc1_abs_zmax'] = round(max_abs, 4)
            result[f'{label}_pc1_ok'] = max_abs <= HMM_CRASH_SIGMA_MAX
        else:
            result[f'{label}_pc1_abs_zmax'] = None
            result[f'{label}_pc1_ok'] = None
    return result


def audit_hmm_outputs() -> dict:
    files = sorted(FEATURES_PATH.glob('*.parquet'))
    details = []
    assets_with_artifacts = 0
    total_artifacts = 0
    n_pass = 0
    n_missing_hmm_inputs = 0
    n_var_window_failures = 0
    n_latest_var_failures = 0
    n_degraded_inputs = 0
    n_crash_pc1_failures = 0
    n_low_f1 = 0
    n_low_bear_2022 = 0

    for path in files:
        symbol = path.stem
        df = _coerce_datetime_index(
            _safe_read_parquet(path),
            ['timestamp', 'date', 'event_date', 'index'],
        )
        prob = _numeric(df['hmm_prob_bull']) if 'hmm_prob_bull' in df.columns else pd.Series(np.nan, index=df.index)
        bull = df['hmm_is_bull'].fillna(False).astype(bool) if 'hmm_is_bull' in df.columns else pd.Series(False, index=df.index)
        returns = _numeric(df['ret_1d']) if 'ret_1d' in df.columns else pd.Series(np.nan, index=df.index)
        coverage_by_input = {
            feature: int(_numeric(df[feature]).notna().sum()) if feature in df.columns else 0
            for feature in HMM_REQUIRED_FEATURES
        }

        missing_hmm_inputs = [
            feature for feature in HMM_REQUIRED_FEATURES
            if feature not in df.columns or not _numeric(df[feature]).notna().any()
        ]
        degraded_missing_inputs = [
            feature for feature in missing_hmm_inputs
            if feature in HMM_DEGRADABLE_FEATURES and prob.notna().any()
        ]
        hard_missing_inputs = [
            feature for feature in missing_hmm_inputs
            if feature not in degraded_missing_inputs
        ]
        if degraded_missing_inputs:
            n_degraded_inputs += 1
        if hard_missing_inputs:
            n_missing_hmm_inputs += 1

        y_true = build_regime_target(returns)
        valid_mask = prob.notna() & y_true.notna()
        if valid_mask.sum() >= 30 and bull.loc[valid_mask].nunique() >= 1:
            y_pred = bull.loc[valid_mask].astype(int)
            f1_oos = float(f1_score(y_true.loc[valid_mask].astype(int), y_pred, zero_division=0))
        else:
            f1_oos = np.nan

        mask_2022 = (
            isinstance(df.index, pd.DatetimeIndex)
            and ((df.index >= '2022-01-01') & (df.index <= '2022-06-30')).sum() > 0
        )
        if mask_2022:
            idx_2022 = (df.index >= '2022-01-01') & (df.index <= '2022-06-30')
            bear_2022 = float((~bull.loc[idx_2022]).mean())
        else:
            bear_2022 = np.nan

        artifact_dir = HMM_PATH / symbol
        artifact_files = sorted(artifact_dir.glob('hmm_t*.pkl'), key=_parse_hmm_step) if artifact_dir.exists() else []
        latest_path = artifact_files[-1] if artifact_files else None
        if artifact_files:
            assets_with_artifacts += 1
            total_artifacts += len(artifact_files)

        latest_var_explained = np.nan
        latest_threshold = np.nan
        latest_train_end_date = None
        max_n_pca_components = 0
        artifact_load_failures = 0
        window_var_failures = 0
        window_pipeline_failures = 0
        latest_fitted = None

        for artifact_path in artifact_files:
            try:
                with open(artifact_path, 'rb') as fin:
                    fitted = pickle.load(fin)
            except Exception:
                artifact_load_failures += 1
                continue

            pca_pipeline = getattr(fitted, 'pca_pipeline', None)
            var_explained = float(getattr(fitted, 'var_explained', np.nan))
            n_pca_components = getattr(fitted, 'n_pca_components', None)
            robust_scaler_ok = isinstance(getattr(pca_pipeline, 'scaler', None), RobustScaler)
            winsorizer_ok = getattr(pca_pipeline, 'winsorizer', None) is not None

            if np.isfinite(var_explained) and var_explained < HMM_VAR_EXP_MIN:
                window_var_failures += 1
            if n_pca_components is not None:
                max_n_pca_components = max(max_n_pca_components, int(n_pca_components))
            if not (robust_scaler_ok and winsorizer_ok):
                window_pipeline_failures += 1

            if artifact_path == latest_path:
                latest_fitted = fitted
                latest_var_explained = var_explained
                latest_threshold = float(getattr(fitted, 'threshold', np.nan))
                latest_train_end_date = getattr(fitted, 'train_end_date', None)

        crash_checks = _compute_pc1_crash_checks(df, latest_fitted) if latest_fitted is not None else _empty_crash_check()
        crash_pc1_fail = any(value is False for key, value in crash_checks.items() if key.endswith('_ok'))
        if crash_pc1_fail:
            n_crash_pc1_failures += 1
        low_f1 = not np.isnan(f1_oos) and f1_oos < HMM_F1_MIN
        low_bear_2022 = not np.isnan(bear_2022) and bear_2022 < HMM_BEAR_2022_MIN
        if low_f1:
            n_low_f1 += 1
        if low_bear_2022:
            n_low_bear_2022 += 1
        latest_var_fail = np.isfinite(latest_var_explained) and latest_var_explained < HMM_VAR_EXP_MIN
        if latest_var_fail:
            n_latest_var_failures += 1
        if window_var_failures > 0:
            n_var_window_failures += 1

        asset_ok = (
            not hard_missing_inputs
            and prob.notna().any()
            and prob.dropna().between(0.0, 1.0).all()
            and len(artifact_files) > 0
            and artifact_load_failures == 0
            and window_pipeline_failures == 0
            and max_n_pca_components <= MAX_PCA_COMPONENTS
            and not crash_pc1_fail
            and not latest_var_fail
            and (np.isnan(f1_oos) or f1_oos >= HMM_F1_MIN)
            and (np.isnan(bear_2022) or bear_2022 >= HMM_BEAR_2022_MIN)
        )
        if asset_ok:
            n_pass += 1

        details.append({
            'symbol': symbol,
            'missing_hmm_inputs': hard_missing_inputs,
            'degraded_missing_inputs': degraded_missing_inputs,
            'input_coverage': coverage_by_input,
            'n_prob_valid': int(prob.notna().sum()),
            'f1_oos': None if np.isnan(f1_oos) else round(f1_oos, 4),
            'bear_2022_h1_pct': None if np.isnan(bear_2022) else round(bear_2022, 4),
            'artifact_count': len(artifact_files),
            'artifact_load_failures': artifact_load_failures,
            'window_var_failures': window_var_failures,
            'window_pipeline_failures': window_pipeline_failures,
            'latest_artifact': str(latest_path) if latest_path is not None else None,
            'latest_train_end_date': latest_train_end_date,
            'latest_var_explained': None if np.isnan(latest_var_explained) else round(latest_var_explained, 4),
            'latest_var_fail': bool(latest_var_fail),
            'low_f1_oos': bool(low_f1),
            'low_bear_2022_h1': bool(low_bear_2022),
            'max_n_pca_components': None if max_n_pca_components == 0 else max_n_pca_components,
            'latest_threshold': None if np.isnan(latest_threshold) else round(latest_threshold, 4),
            **crash_checks,
            'status': _status(asset_ok),
        })

    overall_ok = len(files) > 0 and n_pass == len(files)
    return {
        'status': _status(overall_ok),
        'assets_checked': len(files),
        'assets_with_artifacts': assets_with_artifacts,
        'total_artifacts': total_artifacts,
        'assets_pass': n_pass,
        'assets_with_missing_hmm_inputs': n_missing_hmm_inputs,
        'assets_with_degraded_inputs': n_degraded_inputs,
        'assets_with_var_window_failures': n_var_window_failures,
        'assets_with_latest_var_failures': n_latest_var_failures,
        'assets_with_crash_pc1_failures': n_crash_pc1_failures,
        'assets_with_low_f1_oos': n_low_f1,
        'assets_with_low_bear_2022': n_low_bear_2022,
        'details': details,
    }


def audit_vi_outputs() -> dict:
    cluster_path = MODEL_PATH / 'cluster_map.json'
    vi_matrix_path = MODEL_PATH / 'vi_matrix.csv'

    cluster_payload = {}
    if cluster_path.exists():
        with open(cluster_path, 'r', encoding='utf-8') as fin:
            cluster_payload = json.load(fin)

    cluster_map = cluster_payload.get('cluster_map', {}) if isinstance(cluster_payload, dict) else {}
    cluster_features = sorted({feature for features in cluster_map.values() for feature in features})
    required_missing = [feature for feature in REQUIRED_FEATURES if feature not in cluster_features]
    stale_proxy_features = [feature for feature in STALE_PROXY_COLUMNS if feature in cluster_features]
    vi_threshold = cluster_payload.get('vi_threshold') if isinstance(cluster_payload, dict) else None
    n_clusters = int(cluster_payload.get('n_clusters', len(cluster_map))) if isinstance(cluster_payload, dict) else 0

    if vi_matrix_path.exists():
        vi_df = pd.read_csv(vi_matrix_path, index_col=0)
        matrix_square = vi_df.shape[0] == vi_df.shape[1]
        matrix_symmetric = bool(np.allclose(vi_df.values, vi_df.values.T, atol=1e-10))
        matrix_features_match = sorted(vi_df.index.tolist()) == sorted(cluster_features)
    else:
        matrix_square = False
        matrix_symmetric = False
        matrix_features_match = False

    overall_ok = (
        cluster_path.exists()
        and vi_matrix_path.exists()
        and vi_threshold == VI_THRESHOLD_SPEC
        and n_clusters >= 3
        and matrix_square
        and matrix_symmetric
        and matrix_features_match
        and not required_missing
        and not stale_proxy_features
    )

    return {
        'status': _status(overall_ok),
        'cluster_map_exists': cluster_path.exists(),
        'vi_matrix_exists': vi_matrix_path.exists(),
        'n_clusters': n_clusters,
        'vi_threshold': vi_threshold,
        'required_features_missing': required_missing,
        'stale_proxy_features': stale_proxy_features,
        'matrix_square': matrix_square,
        'matrix_symmetric': matrix_symmetric,
        'matrix_features_match': matrix_features_match,
        'cluster_features': cluster_features,
    }


def audit_pbma_presence() -> dict:
    meta_files = sorted(PHASE3_PATH.glob('*_meta.parquet'))
    n_with_pbma = 0
    missing = []
    for path in meta_files:
        df = _safe_read_parquet(path)
        if 'p_bma' in df.columns:
            n_with_pbma += 1
        else:
            missing.append(path.name)

    ok = len(meta_files) > 0 and n_with_pbma == len(meta_files)
    return {
        'status': _status(ok),
        'logical_location': '/data/models/phase3/*_meta.parquet',
        'meta_files': len(meta_files),
        'meta_files_with_p_bma': n_with_pbma,
        'missing_p_bma_files': missing,
    }


def derive_root_causes(
    feature_store: dict,
    upstream_inputs: dict,
    source_guards: dict,
    hmm_outputs: dict,
    vi_outputs: dict,
    unlock_shadow_mode: dict,
) -> list[str]:
    causes = []
    if any(feature_store['stale_proxy_counts'].values()):
        causes.append('feature_store_still_uses_proxy_schema_from_old_pipeline')
    if any(feature_store['missing_required_counts'].values()):
        causes.append('feature_store_not_regenerated_with_binding_phase2_schema')
    if feature_store.get('missing_eligible_assets'):
        causes.append('feature_store_missing_assets_that_are_eligible_from_ohlcv_history')
    if feature_store.get('missing_collapsed_assets'):
        causes.append('collapsed_assets_missing_upstream_from_ohlcv_history')
    if upstream_inputs['funding_files'] > 0 and feature_store['missing_required_counts'].get('funding_rate_ma7d', 0) > 0:
        causes.append('funding_feed_exists_but_feature_store_did_not_ingest_it')
    if upstream_inputs['basis_files'] == 0:
        causes.append('basis_feed_missing_upstream')
    if not upstream_inputs['stablecoin_present']:
        causes.append('stablecoin_regime_feed_missing_upstream')
    if upstream_inputs['unlock_files'] == 0:
        causes.append('unlock_pressure_feed_missing_upstream')
    if vi_outputs['stale_proxy_features'] or vi_outputs['required_features_missing']:
        causes.append('vi_cluster_map_is_stale_and_not_based_on_current_required_features')
    if hmm_outputs['assets_with_missing_hmm_inputs'] > 0:
        causes.append('hmm_required_inputs_not_populated')
    if hmm_outputs.get('assets_with_latest_var_failures', 0) > 0:
        causes.append('hmm_latest_pca_variance_below_spec')
    if hmm_outputs['assets_with_crash_pc1_failures'] > 0:
        causes.append('hmm_pc1_crash_contamination_exceeds_3sigma')
    if hmm_outputs.get('assets_with_low_f1_oos', 0) > 0:
        causes.append('hmm_oos_f1_below_spec')
    if hmm_outputs.get('assets_with_low_bear_2022', 0) > 0:
        causes.append('hmm_bear_2022_detection_below_spec')
    if not source_guards.get('strict_required_inputs_enforced', False):
        causes.append('hmm_source_allows_missing_required_inputs')
    if not source_guards.get('zero_fill_fallback_absent', False):
        causes.append('hmm_source_uses_zero_fill_fallback')
    if not source_guards.get('configurable_min_variance', False):
        causes.append('hmm_source_hardcodes_min_variance')
    if not source_guards.get('adaptive_pca_cap_ok', False):
        causes.append('pca_source_caps_components_below_needed_range')
    if not unlock_shadow_mode.get('quality_summary_present', False):
        causes.append('unlock_quality_summary_missing')
    if any(value is None for value in unlock_shadow_mode.get('coverage_by_feature', {}).values()):
        causes.append('unlock_feature_coverage_missing')
    return causes


def main() -> None:
    print('Loading Phase 2 artifacts...')
    feature_store = audit_feature_store()
    upstream_inputs = audit_upstream_inputs()
    unlock_shadow_mode = audit_unlock_shadow_mode()
    source_guards = audit_source_guards()
    hmm_outputs = audit_hmm_outputs()
    vi_outputs = audit_vi_outputs()
    pbma_presence = audit_pbma_presence()
    root_causes = derive_root_causes(feature_store, upstream_inputs, source_guards, hmm_outputs, vi_outputs, unlock_shadow_mode)

    overall_ok = all(section['status'] == 'PASS' for section in [
        feature_store,
        unlock_shadow_mode,
        source_guards,
        hmm_outputs,
        vi_outputs,
        pbma_presence,
    ])

    report = {
        'timestamp_utc': datetime.utcnow().isoformat(),
        'spec_binding': 'SNIPER v10.10 Phase 2 deliverables',
        'unlock_model_feature_set': UNLOCK_MODEL_FEATURE_SET,
        'model_run_tag': MODEL_RUN_TAG,
        'overall_status': _status(overall_ok),
        'feature_store': feature_store,
        'upstream_inputs': upstream_inputs,
        'unlock_shadow_mode': unlock_shadow_mode,
        'source_guards': source_guards,
        'hmm_outputs': hmm_outputs,
        'vi_outputs': vi_outputs,
        'pbma_presence': pbma_presence,
        'root_causes': root_causes,
    }
    saved_paths = _write_report(report)

    print('\n' + '=' * 88)
    print('SNIPER v10.10 - PHASE 2 AUDIT')
    print('=' * 88)
    print(f"overall_status: {report['overall_status']}")
    print(f"feature_store:  {feature_store['status']} ({feature_store['n_assets']}/{feature_store['expected_assets']} assets)")
    print(f"unlock_shadow:  {unlock_shadow_mode['status']} ({unlock_shadow_mode['unlock_files']} unlock files)")
    print(f"source_guards:  {source_guards['status']}")
    print(f"hmm_outputs:    {hmm_outputs['status']} ({hmm_outputs['assets_pass']}/{hmm_outputs['assets_checked']} assets)")
    print(f"vi_outputs:     {vi_outputs['status']} ({vi_outputs['n_clusters']} clusters, threshold={vi_outputs['vi_threshold']})")
    print(f"p_bma_presence: {pbma_presence['status']} ({pbma_presence['meta_files_with_p_bma']}/{pbma_presence['meta_files']} meta files)")

    print('\nFeature store blockers:')
    if feature_store.get('missing_eligible_assets'):
        print(f"  - missing eligible assets: {feature_store['missing_eligible_assets']}")
    if feature_store.get('unexpected_assets'):
        print(f"  - unexpected assets: {feature_store['unexpected_assets']}")
    if feature_store.get('missing_collapsed_assets'):
        print(f"  - collapsed assets missing upstream: {feature_store['missing_collapsed_assets']}")
    if feature_store.get('collapsed_below_history_threshold'):
        print(f"  - collapsed assets below {FEATURE_MIN_HISTORY_DAYS}d history threshold: {feature_store['collapsed_below_history_threshold']}")
    for feature, count in feature_store['missing_required_counts'].items():
        if count:
            print(f'  - missing column {feature}: {count} assets')
    for feature, count in feature_store.get('dense_population_counts', {}).items():
        if count != feature_store['n_assets']:
            print(f'  - dense feature population shortfall {feature}: {count}/{feature_store["n_assets"]} assets')
    for feature, count in feature_store['stale_proxy_counts'].items():
        if count:
            print(f'  - stale proxy column {feature}: {count} assets')
    for feature, coverage in feature_store.get('sparse_feature_coverage', {}).items():
        print(f'  - sparse feature coverage {feature}: {coverage}')

    print('\nUpstream availability:')
    print(f"  - funding_files: {upstream_inputs['funding_files']}")
    print(f"  - basis_files: {upstream_inputs['basis_files']}")
    print(f"  - stablecoin_present: {upstream_inputs['stablecoin_present']}")
    print(f"  - unlock_files: {upstream_inputs['unlock_files']}")

    print('\nUnlock shadow mode:')
    print(f"  - quality_summary_present: {unlock_shadow_mode['quality_summary_present']}")
    print(f"  - coverage_scope: {unlock_shadow_mode.get('coverage_scope')}")
    print(f"  - coverage_by_feature: {unlock_shadow_mode['coverage_by_feature']}")
    print(f"  - effective_x_feature_coverage: {unlock_shadow_mode.get('effective_x_feature_coverage', {})}")
    print(f"  - audit_only_coverage: {unlock_shadow_mode.get('audit_only_coverage', {})}")
    print(f"  - baseline_vs_augmented: {unlock_shadow_mode['baseline_vs_augmented']}")
    print(f"  - latest_quality: {unlock_shadow_mode['latest_quality']}")
    if unlock_shadow_mode.get('latest_quality_metadata'):
        print(f"  - latest_quality_metadata: {unlock_shadow_mode['latest_quality_metadata']}")
    if unlock_shadow_mode.get('raw_latest_quality'):
        print(f"  - raw_latest_quality: {unlock_shadow_mode['raw_latest_quality']}")

    print('\nHMM blockers:')
    print(f"  - assets_with_missing_hmm_inputs: {hmm_outputs['assets_with_missing_hmm_inputs']}")
    print(f"  - assets_with_degraded_inputs: {hmm_outputs.get('assets_with_degraded_inputs', 0)}")
    print(f"  - assets_with_var_window_failures_historical: {hmm_outputs['assets_with_var_window_failures']}")
    print(f"  - assets_with_latest_var_failures: {hmm_outputs.get('assets_with_latest_var_failures', 0)}")
    print(f"  - assets_with_crash_pc1_failures: {hmm_outputs['assets_with_crash_pc1_failures']}")
    print(f"  - assets_with_low_f1_oos: {hmm_outputs.get('assets_with_low_f1_oos', 0)}")
    print(f"  - assets_with_low_bear_2022: {hmm_outputs.get('assets_with_low_bear_2022', 0)}")

    if root_causes:
        print('\nRoot causes:')
        for cause in root_causes:
            print(f'  - {cause}')

    print('\nReports saved:')
    for path in saved_paths:
        print(f'  - {path}')
    print('=' * 88)


if __name__ == '__main__':
    main()
