from __future__ import annotations

UNLOCK_MODEL_FEATURE_COLUMNS = [
    "unlock_pressure_rank_observed",
    "unlock_pressure_rank_reconstructed",
    "unlock_overhang_proxy_rank_full",
    "unlock_fragility_proxy_rank_fallback",
]

UNLOCK_AUDIT_COLUMNS = [
    "ups_raw_observed",
    "ups_raw_reconstructed",
    "proxy_full_raw",
    "proxy_fallback_raw",
    "unlock_pressure_rank_selected_for_reporting",
    "unlock_feature_state",
    "unknown_bucket_ratio",
    "quality_flag",
    "reconstruction_confidence",
    "circ",
    "total_supply",
    "insider_share_pti",
    "insider_share_mode",
    "avg_30d_volume",
    "market_cap",
    "source_primary",
    "snapshot_ts",
    "supply_history_mode",
]

UNLOCK_ALL_COLUMNS = UNLOCK_MODEL_FEATURE_COLUMNS + UNLOCK_AUDIT_COLUMNS
