# unlock_pressure_rank rev5

## Binding

This implementation is bound to:

- `SNIPER_v10_10_Especificacao_Definitiva.pdf`
- `SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf`

The model-facing unlock bundle remains split into four orthogonal continuous columns:

- `unlock_pressure_rank_observed`
- `unlock_pressure_rank_reconstructed`
- `unlock_overhang_proxy_rank_full`
- `unlock_fragility_proxy_rank_fallback`

Governance and reporting fields remain outside the predictive vector `X`:

- `unlock_feature_state`
- `reconstruction_confidence`
- `quality_flag`
- `unlock_pressure_rank_selected_for_reporting`
- all other audit/diagnostic fields in `UNLOCK_AUDIT_COLUMNS`

## What Was Audited

The hardening pass re-audited the repository against the rev5 requirements instead of trusting the previous delivery summary. The following were explicitly checked in code:

- four orthogonal columns exist in persistence and feature loading
- no consolidated unlock column re-entered training
- `percent_rank` uses `rank(method="average")`
- `NaN` stays out of the ranking and remains `NaN`
- `N_t = 1` maps to `0.5`
- `unknown_bucket_ratio > 15%` blocks observed/reconstructed promotion
- reconstructed promotion requires `confidence >= 0.85`
- Wayback captures remain `<= as_of_date`
- reconstruction scope stays restricted to cemetery plus anchors
- long-tail historical fallback keeps `insider_share_pti_lite = 0.0`
- cemetery assets remain in the point-in-time universe

## Corrections Applied In Hardening

The audit found and corrected these implementation gaps:

1. `proxy_full` was using only `observed.total_supply`.
   It now uses the best point-in-time `total_supply` available in this order:
   `observed -> reconstructed -> market_row`.

2. `fetch_and_store()` was passing the full live bundle into `_build_market_history()`.
   It now passes the normalized `market_row`, so current-day supply/circulation enrichment actually works.

3. `raw_wayback_fetches` was not being persisted for every `as_of_date` that re-used the same capture.
   It now records the reused capture per `as_of_date`, preserving point-in-time traceability.

4. Audit string columns from unlocks were being numerically coerced inside `ml_engine`.
   They are now preserved as audit metadata in Phase 2 features while still remaining outside training.

5. The unlock layer had no aggregated shadow/data-quality summary.
   It now persists `feature_unlock_quality_daily` and exports `unlock_quality_daily.parquet`.

6. The live collector was not compatible with the real Mobula payload shape.
   It now parses `unlock_date`, `tokens_to_unlock`, `allocation_details` and `distribution.name`.

7. The live runtime was still taking the heavy historical path.
   It now:
   - fetches CoinGecko coin details only for `cemetery + anchors`
   - limits Wayback URL discovery to a deterministic priority cap per asset
   - defaults runtime feature generation to the latest point-in-time row only
   - emits phase progress logs for `coin_details`, `market_history` and `reconstructed_layers`

## Final Architecture

### Observed

- Source: Mobula metadata / multi-metadata
- Market context: CoinGecko markets + market chart
- Raw persistence:
  - `raw_mobula_snapshots`
  - `raw_coingecko_snapshots`
  - `raw_defillama_unlocks`
- Promotion requirements:
  - valid schedule
  - `circ > 0`
  - `unknown_bucket_ratio <= 15%`

### Reconstructed

- Restricted to mandatory cemetery plus configurable anchors
- URL discovery from CoinGecko official links
- Live runtime queries only the highest-priority URL subset per asset to keep the Docker ingest operational
- Wayback CDX query restricted by `to=as_of_date`
- deterministic parser registry
- promotion requires:
  - `confidence >= 0.85`
  - `unknown_bucket_ratio <= 15%`
  - no lookahead
- evidence-only band:
  - `0.70 <= confidence < 0.85`

### Proxy Full

- requires point-in-time:
  - `circ`
  - `total_supply`
  - `market_cap`
  - `avg_30d_volume`
- formula:
  - `log(1 + vested_overhang) * (1 + insider_share_pti) * liq_penalty`

### Proxy Fallback

- requires:
  - `market_cap`
  - `avg_30d_volume`
- `insider_share_pti_lite = 0.0` when observed/reconstructed allocation is not available point-in-time

### Ranking

- cross-sectional by date
- only non-null rows enter each ranking
- ties use `rank(method="average")`
- formula:
  - `percent_rank = (rank_avg - 1) / (N_t - 1)`
- if `N_t = 1`, use `0.5`

## Persistence

SQLite tables used by unlock rev5:

- `raw_mobula_snapshots`
- `raw_coingecko_snapshots`
- `raw_wayback_fetches`
- `raw_defillama_unlocks`
- `asset_source_registry`
- `unlock_events_normalized`
- `feature_unlock_daily`
- `feature_unlock_diagnostics`
- `feature_unlock_quality_daily`

Operational outputs:

- `data/parquet/unlocks/*.parquet`
- `data/parquet/unlock_diagnostics/unlock_quality_daily.parquet`
- `data/parquet/unlock_market/*.parquet`
- `data/parquet/unlock_wayback/...`

Note for constrained local test environments:

- if neither real `polars` parquet writing nor `pyarrow/fastparquet` is available, the code falls back to a pickle-backed file at the same `.parquet` path for local validation only
- Docker images still install real parquet dependencies through service requirements

## Shadow Mode And Data Quality

The collector now emits a daily quality summary with:

- observed coverage
- reconstructed coverage
- proxy full coverage
- proxy fallback coverage
- missing rate
- `unknown_bucket_ratio > 15%` rate
- selected raw zero fraction
- massive ties fraction
- `review_required` count
- reconstructed promotion blocked count
- snapshot lag summary
- provider failure counters
- rank distribution summary JSON
- `shadow_mode_flag`

`shadow_mode_flag` remains `1` while observed snapshot history is below `UNLOCK_OBSERVED_SHADOW_MIN_SNAPSHOTS`.

Operational live-mode controls:

- `UNLOCK_RUNTIME_HISTORY_MODE=latest_only`
  - default for Docker runtime
  - builds only the latest point-in-time unlock row per asset during `run_full_ingest`
  - use `full` only for controlled backfill jobs
- `UNLOCK_RECONSTRUCTION_MAX_URLS_PER_ASSET=4`
  - caps Wayback URL fan-out in live mode
  - selection priority: `whitepaper -> docs -> tokenomics -> vesting -> unlocks -> homepage -> litepaper -> blog -> official_forum`

## Offline Diagnostic Routine

`services/ml_engine/phase2_diagnostic.py` now adds unlock-specific diagnostics for shadow mode:

- temporal coverage of the four orthogonal columns
- pairwise correlation between observed / reconstructed / proxies
- PSI-style drift between early and late windows
- baseline vs baseline+unlock completeness comparison
- latest quality summary snapshot

This is diagnostic only. It does not introduce a new research framework or trigger a retrain.

## Tests

Unit and integration coverage now includes:

- bucket normalization
- UPS 30-day causal window regression
- massive ties with `average`
- `NaN` exclusion from rank
- `N_t = 1`
- long-tail `insider_share_pti_lite = 0.0`
- `circ <= 0`
- `total_supply` missing
- `avg_30d_volume` missing/zero
- confidence gate for reconstructed
- blocking on `unknown_bucket_ratio`
- restriction of reconstruction to cemetery plus anchors
- no Wayback lookahead
- SQLite persistence and connection closure regression
- local end-to-end flow:
  `data_inserter -> SQLite -> unlock parquet -> ml_engine`
- guarantee that audit/governance fields stay out of the training vector

Official Docker test execution:

- full suite: run in `sniper_tests`, which is built from the repository root and includes both `services/data_inserter` and `services/ml_engine` dependencies
- service-local subsets: run from `data_inserter` or `ml_engine` against `/workspace/tests` when you want to isolate one layer

Run the full suite in Docker:

```powershell
docker compose run --rm sniper_tests
```

Run unit tests in Docker:

```powershell
docker compose run --rm sniper_tests python -m unittest discover -s tests/unit -t . -p "test_*.py" -v
```

Run integration/E2E tests in Docker:

```powershell
docker compose run --rm sniper_tests python -m unittest discover -s tests/integration -t . -p "test_*.py" -v
```

Run the unlock collector unit subset directly in `data_inserter`:

```powershell
docker compose run --rm data_inserter -m unittest discover -s /workspace/tests/unit -t /workspace -p "test_token_unlocks_collector.py" -v
```

Run the unlock ML integration subset directly in `ml_engine`:

```powershell
docker compose run --rm ml_engine python -m unittest discover -s /workspace/tests/unit -t /workspace -p "test_unlock_ml_integration.py" -v
```

Run the unlock Phase 2 diagnostic subset directly in `ml_engine`:

```powershell
docker compose run --rm ml_engine python -m unittest discover -s /workspace/tests/unit -t /workspace -p "test_unlock_phase2_diagnostic.py" -v
```

## How To Run

Build the affected services:

```powershell
docker compose build data_inserter ml_engine sniper_tests
```

Run one-shot ingestion:

```powershell
docker compose run --rm data_inserter -c "import asyncio; from main import run_full_ingest; asyncio.run(run_full_ingest())"
```

Recommended `.env` additions for operational runtime:

```dotenv
UNLOCK_RUNTIME_HISTORY_MODE=latest_only
UNLOCK_RECONSTRUCTION_MAX_URLS_PER_ASSET=4
UNLOCK_OBSERVED_SHADOW_MIN_SNAPSHOTS=30
```

Run the Phase 2 bootstrap only:

```powershell
docker compose run --rm data_inserter bootstrap_phase2_inputs.py
```

Run the unlock E2E validation locally:

```powershell
docker compose run --rm sniper_tests python -m unittest discover -s tests/integration -t . -p "test_*.py" -v
```

Run the full unit suite:

```powershell
docker compose run --rm sniper_tests python -m unittest discover -s tests/unit -t . -p "test_*.py" -v
```

Run the full test suite:

```powershell
docker compose run --rm sniper_tests
```

Run the offline Phase 2 diagnostic:

```powershell
docker compose run --rm ml_engine python phase2_diagnostic.py
```

Run the ML pipeline once:

```powershell
docker compose run --rm ml_engine python -c "import asyncio; from main import run_ml_pipeline_full; asyncio.run(run_ml_pipeline_full())"
```

Start the minimum stack:

```powershell
docker compose up -d redis data_inserter ml_engine
```

## Inspecting Outputs

- unlock feature rows:
  `data/parquet/unlocks/*.parquet`
- unlock quality summary:
  `data/parquet/unlock_diagnostics/unlock_quality_daily.parquet`
- SQLite diagnostics:
  `feature_unlock_daily`
  `feature_unlock_diagnostics`
  `feature_unlock_quality_daily`
- Phase 2 diagnostic report:
  `data/models/phase2_diagnostic_report.json`

## Credentials

Optional:

- `MOBULA_API_KEY`
- `COINGECKO_API_KEY`
- `DEFILLAMA_UNLOCKS_ENDPOINT`

Required for real live collection:

- at least one working CoinGecko path for universe/market data
- Mobula access if observed layer collection is expected to populate

Wayback does not require an API key, but does require network reachability and stable archive responses.

## Remaining Real Limitations

- observed historical coverage still depends on snapshots accumulated by SNIPER itself, so the observed layer remains naturally sparse at first
- reconstructed `total_supply` is available only when the historical document itself exposes a valid supply hint
- local non-Docker environments without parquet dependencies use the pickle-backed fallback only for validation convenience; production should rely on the Docker environment with real parquet support
