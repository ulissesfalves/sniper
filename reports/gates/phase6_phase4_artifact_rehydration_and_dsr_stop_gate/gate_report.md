## Resumo executivo

- Status: `PARTIAL` / decision `correct`.
- Source-doc alignment: `ALIGNED`.
- Phase4 artifact integrity: `PASS`.
- DSR honest: `0.0`; promotion status `BLOCKED_DSR_HONEST_ZERO`.
- Clean regeneration proof: `False`; local probe rc `None`.
- CVaR persisted as `PASS_ZERO_EXPOSURE` with economic status `NOT_PROVEN_ZERO_EXPOSURE`.

## Baseline congelado

- A3/A4 remain closed.
- RiskLabAI remains oracle/shadow, not official.
- Fast path remains official; cross-sectional remains ALIVE_BUT_NOT_PROMOTABLE.
- DSR honest 0.0 remains a promotion blocker.

## Mudanças implementadas

- Added a Phase 6 gate runner for source-doc-artifact alignment.
- Added official Phase 4 artifact integrity and DSR evidence.
- Added persisted CVaR evidence with an explicit zero-exposure caveat.
- Added environment and local regeneration probe artifacts.

## Artifacts gerados

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\source_doc_alignment.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\phase4_artifact_integrity_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\portfolio_cvar_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\environment_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\clean_regeneration_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\clean_regeneration_preflight.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_phase4_artifact_rehydration_and_dsr_stop_gate\gate_metrics.parquet`

## Resultados

- Missing documented Phase 4-R4 modules: `[]`.
- Missing official Phase4 artifacts: `[]`.
- DSR honest/pass: `0.0` / `False`.
- Environment packages available: `True`.
- Regeneration blocker: `MISSING_RESEARCH_BASELINE_ARTIFACTS`.
- Missing regeneration baseline artifacts: `['C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper_codex_autonomous\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_predictions.parquet', 'C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper_codex_autonomous\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_report.json', 'C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper_codex_autonomous\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_manifest.json', 'C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper_codex_autonomous\\data\\models\\research\\phase4_cross_sectional_ranking_baseline\\stage_a_snapshot_proxy.parquet']`.
- CVaR stress report: `{'cvar_historical': 0.0, 'cvar_stress_rho1': 0.0, 'cvar_limit': 0.15, 'margin_of_safety': 0.15, 'cvar_ok': True, 'hidden_risk_factor': 0.0, 'sigma_portfolio_stress': 0, 'n_positions': 0, 'total_exposure_pct': 0.0}`.

## Avaliação contra gates

- source_doc_alignment: `PASS` (value `ALIGNED`)
- phase4_artifact_integrity: `PASS` (value `PASS`)
- phase4_dsr_honest: `FAIL` (value `0.0`)
- cvar_artifact_persisted: `PASS` (value `PASS_ZERO_EXPOSURE`)
- cvar_economic_robustness: `INCONCLUSIVE` (value `NOT_PROVEN_ZERO_EXPOSURE`)
- test_environment_probe: `PASS` (value `True`)
- local_regeneration_probe: `INCONCLUSIVE` (value `None`)
- clean_regeneration_proof: `INCONCLUSIVE` (value `False`)

## Riscos residuais

- A3/A4 remain closed; no promotion attempted.
- RiskLabAI remains oracle/shadow only.
- Cross-sectional family remains ALIVE_BUT_NOT_PROMOTABLE.
- DSR=0.0 remains a promotion blocker; this gate does not alter thresholds.

## Veredito final: advance / correct / abandon

- `PARTIAL` -> `correct`. Blockers: `['dsr_honest_zero_blocks_promotion', 'cvar_zero_exposure_not_economic_robustness', 'clean_regeneration_not_proven_in_clean_clone_or_equivalent', 'research_regeneration_baseline_artifacts_missing']`.
