## Resumo executivo

- Status: `PARTIAL` / decision `correct`.
- Source-doc alignment: `DIVERGENT`.
- Clean regeneration proof: `False`; local probe rc `1`.
- CVaR persisted as `PASS_ZERO_EXPOSURE` with economic status `NOT_PROVEN_ZERO_EXPOSURE`.

## Baseline congelado

- A3/A4 remain closed.
- RiskLabAI remains oracle/shadow, not official.
- Fast path remains official; cross-sectional remains ALIVE_BUT_NOT_PROMOTABLE.
- DSR honest 0.0 remains a promotion blocker.

## Mudanças implementadas

- Added a Phase 6 gate runner for source-doc-artifact alignment.
- Added persisted CVaR evidence with an explicit zero-exposure caveat.
- Added environment and local regeneration probe artifacts.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\source_doc_alignment.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\portfolio_cvar_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\environment_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\clean_regeneration_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_global_reproducibility_source_alignment_gate\gate_metrics.parquet`

## Resultados

- Missing documented Phase 4-R4 modules: `['phase4_config.py', 'phase4_data.py', 'phase4_dsr.py', 'phase4_backtest.py', 'phase4_calibration.py']`.
- Environment packages available: `True`.
- Regeneration blocker: `local regeneration probe failed before clean-clone proof; missing official phase4 artifacts are a blocker in this clone`.
- CVaR stress report: `{'cvar_historical': 0.0, 'cvar_stress_rho1': 0.0, 'cvar_limit': 0.15, 'margin_of_safety': 0.15, 'cvar_ok': True, 'hidden_risk_factor': 0.0, 'sigma_portfolio_stress': 0, 'n_positions': 0, 'total_exposure_pct': 0.0}`.

## Avaliação contra gates

- source_doc_alignment: `FAIL` (value `DIVERGENT`)
- cvar_artifact_persisted: `PASS` (value `PASS_ZERO_EXPOSURE`)
- cvar_economic_robustness: `INCONCLUSIVE` (value `NOT_PROVEN_ZERO_EXPOSURE`)
- test_environment_probe: `PASS` (value `True`)
- local_regeneration_probe: `FAIL` (value `1`)
- clean_regeneration_proof: `INCONCLUSIVE` (value `False`)

## Riscos residuais

- A3/A4 remain closed; no promotion attempted.
- RiskLabAI remains oracle/shadow only.
- Cross-sectional family remains ALIVE_BUT_NOT_PROMOTABLE.
- DSR=0.0 remains a promotion blocker; this gate does not alter thresholds.

## Veredito final: advance / correct / abandon

- `PARTIAL` -> `correct`. Blockers: `['phase4_r4_source_doc_mismatch', 'cvar_zero_exposure_not_economic_robustness', 'clean_regeneration_not_proven_in_clean_clone_or_equivalent', 'local_regeneration_probe_failed']`.
