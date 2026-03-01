# SNIPER v10.10 — Complete Project Structure

```
sniper-v10.10/
│
├── .env.example                    # Template de credenciais (NUNCA commitar .env)
├── .gitignore
├── docker-compose.yml              # Orquestração completa: 7 serviços
├── pyproject.toml                  # Dependências Python com pinagem semântica
├── requirements.txt                # Flat list para Docker pip install
│
├── services/
│   │
│   ├── data_inserter/              # ── SERVIÇO 1: Ingestão de Dados ──────────
│   │   ├── Dockerfile
│   │   ├── requirements.txt        # (herda do root, pode especializar)
│   │   ├── main.py                 # Entry point: APScheduler cron jobs
│   │   ├── collectors/
│   │   │   ├── __init__.py
│   │   │   ├── coingecko.py        # OHLCV point-in-time, top-50 histórico
│   │   │   ├── binance.py          # Funding rate, basis 3m, volume intraday
│   │   │   ├── deribit.py          # Dvol (implied volatility index)
│   │   │   └── token_unlocks.py    # Unlock schedule → UPS score
│   │   ├── parsers/
│   │   │   ├── __init__.py
│   │   │   └── ohlcv.py            # Polars → parquet particionado ativo/ano
│   │   └── validators/
│   │       ├── __init__.py
│   │       └── anti_survivorship.py  # Garante Luna, FTT, CEL no universo
│   │
│   ├── ml_engine/                  # ── SERVIÇO 2: Motor ML (CPU-intensivo) ──
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py                 # Orchestrator: pipeline completo diário
│   │   │
│   │   ├── fracdiff/               # PARTE 3 — FracDiff log-space
│   │   │   ├── __init__.py
│   │   │   ├── weights.py          # fracdiff_weights() τ=1e-5
│   │   │   ├── transform.py        # fracdiff_log() expanding window
│   │   │   └── optimal_d.py        # find_optimal_d_expanding() ADF-based
│   │   │
│   │   ├── regime/                 # PARTE 4 — HMM Regime Filter
│   │   │   ├── __init__.py
│   │   │   ├── winsorizer.py       # Winsorização 1%-99% feature-wise
│   │   │   ├── pca_robust.py       # RobustScaler → PCA walk-forward
│   │   │   └── hmm_filter.py       # GaussianHMM 2-states, bull_state detect
│   │   │
│   │   ├── features/               # PARTE 3 (tabela) — Feature store
│   │   │   ├── __init__.py
│   │   │   ├── momentum.py         # ret_1d, ret_5d, ret_20d
│   │   │   ├── derivatives.py      # funding_rate_ma7d, basis_3m
│   │   │   ├── macro.py            # stablecoin_chg30, dvol_zscore
│   │   │   ├── onchain.py          # unlock_pressure_rank
│   │   │   └── volatility.py       # sigma_ewma (EWMA vol base)
│   │   │
│   │   ├── triple_barrier/         # PARTE 5 — Triple-Barrier v10.10
│   │   │   ├── __init__.py
│   │   │   ├── labeler.py          # apply_triple_barrier_v1010() HLC check
│   │   │   ├── market_impact.py    # compute_sqrt_market_impact() √(Q/V)
│   │   │   └── intraday_vol.py     # compute_intraday_vol() Parkinson
│   │   │
│   │   ├── vi_cfi/                 # PARTE 6 — CFI com Variation of Info
│   │   │   ├── __init__.py
│   │   │   ├── vi.py               # variation_of_information() normalized
│   │   │   ├── distance_matrix.py  # compute_vi_distance_matrix()
│   │   │   └── cfi.py              # clustered_feature_importance_vi()
│   │   │
│   │   ├── meta_labeling/          # PARTE 8 — Meta-Model + CPCV
│   │   │   ├── __init__.py
│   │   │   ├── pbma_purged.py      # generate_pbma_purged_kfold()
│   │   │   ├── uniqueness.py       # compute_label_uniqueness_dynamic()
│   │   │   ├── weights.py          # compute_meta_sample_weights()
│   │   │   ├── cpcv.py             # cpcv_meta_labeling_v107()
│   │   │   └── isotonic.py         # calibrate_isotonic_pooled() + time-decay
│   │   │
│   │   ├── sizing/                 # PARTE 10 — Position Sizing
│   │   │   ├── __init__.py
│   │   │   ├── cvar.py             # compute_cvar() + portfolio CVaR ρ=1.0
│   │   │   ├── kelly.py            # kelly_cvar_limited()
│   │   │   └── hrp.py              # hrp_weights() Ledoit-Wolf + dendogram
│   │   │
│   │   ├── drift/                  # PARTE 9 — Concept Drift Detection
│   │   │   ├── __init__.py
│   │   │   ├── c2st.py             # C2STDriftDetector block bootstrap
│   │   │   └── ks_diagnostic.py    # KSClusterDiagnostic per VI cluster
│   │   │
│   │   └── backtest/               # PARTE 12 — Anti-fraud backtest
│   │       ├── __init__.py
│   │       ├── engine.py           # Full backtest pipeline
│   │       ├── dsr.py              # compute_dsr_honest() n_trials ≥ 5000
│   │       └── checklist.py        # Automated checklist validator
│   │
│   ├── execution_engine/           # ── SERVIÇO 3: Execução + Risco ──────────
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── main.py                 # Signal consumer (Redis queue)
│   │   ├── binance/
│   │   │   ├── __init__.py
│   │   │   └── executor.py         # BinanceExecutor async + halt_all()
│   │   ├── risk/
│   │   │   ├── __init__.py
│   │   │   └── gate.py             # Pre-trade CVaR gate, HRP check
│   │   ├── circuit_breaker/
│   │   │   ├── __init__.py
│   │   │   └── corwin_schultz.py   # CircuitBreakerCorwinSchultz v10.10
│   │   └── notifications/
│   │       ├── __init__.py
│   │       ├── telegram.py         # Async Telegram alerts
│   │       └── email.py            # aiosmtplib async email
│   │
│   └── api/                        # ── SERVIÇO 4: FastAPI Backend ────────────
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── main.py                 # FastAPI app factory + lifespan
│       ├── config.py               # Settings (pydantic-settings)
│       ├── database.py             # SQLAlchemy async engine
│       ├── routers/
│       │   ├── __init__.py
│       │   ├── portfolio.py        # GET /api/portfolio, /api/positions
│       │   ├── signals.py          # GET /api/signals, /api/meta-model
│       │   ├── risk.py             # GET /api/risk (CVaR, drawdown, HMM)
│       │   ├── drift.py            # GET /api/drift (C2ST, KS, CB)
│       │   ├── backtest.py         # POST /api/backtest/run
│       │   ├── commands.py         # POST /api/halt, /api/resume
│       │   └── ws.py               # WebSocket /ws — real-time push
│       ├── schemas/
│       │   ├── __init__.py
│       │   ├── portfolio.py        # Pydantic response models
│       │   ├── risk.py
│       │   └── signals.py
│       ├── deps/
│       │   ├── __init__.py
│       │   └── auth.py             # API key header auth
│       └── middleware/
│           ├── __init__.py
│           └── logging.py          # structlog request logger
│
├── frontend/                       # ── SERVIÇO 5: React SPA ─────────────────
│   ├── Dockerfile                  # Node builder → Nginx static server
│   ├── nginx.conf                  # SPA routing fallback
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx                # React root
│       ├── App.tsx                 # Router + theme provider
│       │
│       ├── components/
│       │   ├── layout/
│       │   │   ├── Sidebar.tsx     # Navigation: Portfolio / Risk / Drift / Backtest
│       │   │   ├── TopBar.tsx      # System status, halt button, clock
│       │   │   └── StatusBadge.tsx # LIVE / HALT / PAPER indicator
│       │   │
│       │   ├── charts/
│       │   │   ├── CandlestickChart.tsx   # lightweight-charts OHLCV
│       │   │   ├── EquityCurve.tsx        # Recharts: P&L curve + drawdown
│       │   │   ├── CalibrationCurve.tsx   # Reliability diagram (ECE)
│       │   │   ├── FeatureImportance.tsx  # CFI/VI bar chart
│       │   │   ├── CovMatrix.tsx          # HRP correlation heatmap
│       │   │   └── DriftTimeline.tsx      # C2ST AUC over time
│       │   │
│       │   ├── risk/
│       │   │   ├── CVarGauge.tsx          # Portfolio CVaR gauge (historical vs stress)
│       │   │   ├── DrawdownMonitor.tsx    # Real-time DD% with halt threshold
│       │   │   ├── CircuitBreakerPanel.tsx # CS spread z-score + funding anomaly
│       │   │   └── PositionSizingTable.tsx # Kelly × HRP × P_meta per asset
│       │   │
│       │   └── signals/
│       │       ├── SignalFeed.tsx          # Live signal stream (WebSocket)
│       │       ├── MetaModelCard.tsx       # P_meta, p_bma, IA scores per asset
│       │       └── HmmRegimeIndicator.tsx  # Bull/Bear probability bar
│       │
│       ├── pages/
│       │   ├── Dashboard.tsx       # Main overview: equity + positions + alerts
│       │   ├── RiskMonitor.tsx     # CVaR, drawdown, sizing table
│       │   ├── DriftAnalysis.tsx   # C2ST + KS + CB panels
│       │   ├── Backtest.tsx        # Backtest runner + checklist results
│       │   └── Settings.tsx        # Hyperparameters (read-only display)
│       │
│       ├── hooks/
│       │   ├── useWebSocket.ts     # WS connection with auto-reconnect
│       │   ├── usePortfolio.ts     # SWR polling for positions
│       │   └── useRiskMetrics.ts   # SWR polling for CVaR, drift
│       │
│       ├── store/
│       │   ├── systemStore.ts      # Zustand: LIVE/HALT state, alerts
│       │   └── portfolioStore.ts   # Zustand: positions, P&L
│       │
│       ├── types/
│       │   ├── api.ts              # API response type definitions
│       │   └── trading.ts          # Signal, Position, RiskMetrics types
│       │
│       └── utils/
│           ├── formatters.ts       # Currency, %, date formatting
│           └── colors.ts           # Bull/Bear/Warn color helpers
│
├── infra/
│   └── nginx/
│       └── nginx.conf              # Reverse proxy: /api → FastAPI, / → SPA
│
├── data/                           # GITIGNORED — local volume mounts only
│   ├── raw/
│   ├── processed/parquet/          # Partitioned: ativo=SOLUSDT/year=2024/
│   ├── models/                     # HMM, LGBM, Isotonic calibrators
│   └── calibration/                # VI cluster assignments
│
├── tests/
│   ├── unit/
│   │   ├── test_fracdiff.py        # Property: d=1 → first diff; d=0 → no-op
│   │   ├── test_triple_barrier.py  # Low[t]≤SL checked before High[t]≥TP
│   │   ├── test_market_impact.py   # sqrt(Q/V) monotonicity, cap=50%
│   │   ├── test_cvar.py            # CVaR ≥ VaR, stress ρ=1.0 ≥ historical
│   │   └── test_isotonic.py        # ECE decreases after calibration
│   ├── integration/
│   │   ├── test_pipeline_e2e.py    # Full pipeline: data → signal → size
│   │   └── test_api_endpoints.py   # FastAPI routes with TestClient
│   └── backtest/
│       └── test_anti_fraud.py      # Checklist automation
│
├── scripts/
│   ├── bootstrap.sh                # One-command: cp .env.example, mkdir data/*
│   ├── backtest_run.py             # CLI: python scripts/backtest_run.py --start 2020
│   └── export_checklist.py         # Exports backtest checklist to PDF/markdown
│
└── logs/                           # GITIGNORED — structured JSON logs
```
