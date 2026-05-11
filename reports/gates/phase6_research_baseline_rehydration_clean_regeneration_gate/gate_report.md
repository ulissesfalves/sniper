## Resumo executivo

- Status: `PARTIAL` / decision `correct`.
- Source-doc alignment: `ALIGNED`.
- Phase4 artifact integrity: `PASS`.
- DSR honest: `0.0`; promotion status `BLOCKED_DSR_HONEST_ZERO`.
- Clean regeneration proof: `True`; mode `isolated_clean_clone_with_copied_base_artifacts`; rc `0`.
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

- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\source_doc_alignment.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\phase4_artifact_integrity_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\portfolio_cvar_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\environment_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\clean_regeneration_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\clean_regeneration_preflight.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\gate_report.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\gate_report.md`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\gate_manifest.json`
- `C:\Users\uliss\Documentos\Meus_projetos\sniper_codex_autonomous\reports\gates\phase6_research_baseline_rehydration_clean_regeneration_gate\gate_metrics.parquet`

## Resultados

- Missing documented Phase 4-R4 modules: `[]`.
- Missing official Phase4 artifacts: `[]`.
- DSR honest/pass: `0.0` / `False`.
- Environment packages available: `True`.
- Regeneration blocker: `None`.
- Clean source status before regeneration: ``.
- Phase5 clean gate summary: `{'report_path': WindowsPath('C:/Users/uliss/Documentos/Meus_projetos/sniper_codex_autonomous/data/models/research/p6cw/9e7679baeca9/r/reports/gates/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate/gate_report.json'), 'report_exists': True, 'manifest_path': WindowsPath('C:/Users/uliss/Documentos/Meus_projetos/sniper_codex_autonomous/data/models/research/p6cw/9e7679baeca9/r/reports/gates/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate/gate_manifest.json'), 'manifest_exists': True, 'status': 'PASS', 'decision': 'advance', 'summary': ['classification_final=SOVEREIGN_BASELINE_RESTORED_AND_VALID', 'equivalence_classification=EXACT_RESTORE', 'latest_date=2026-03-20', 'latest_active_count_decision_space=2', 'headroom_decision_space=True'], 'blockers': [], 'next_recommended_step': 'Use the restored sovereign closure bundle as the phase5 research baseline.'}`.
- Missing regeneration baseline artifacts: `[]`.
- CVaR stress report: `{'cvar_historical': 0.0, 'cvar_stress_rho1': 0.0, 'cvar_limit': 0.15, 'margin_of_safety': 0.15, 'cvar_ok': True, 'hidden_risk_factor': 0.0, 'sigma_portfolio_stress': 0, 'n_positions': 0, 'total_exposure_pct': 0.0}`.

## Avaliação contra gates

- source_doc_alignment: `PASS` (value `ALIGNED`)
- phase4_artifact_integrity: `PASS` (value `PASS`)
- phase4_dsr_honest: `FAIL` (value `0.0`)
- cvar_artifact_persisted: `PASS` (value `PASS_ZERO_EXPOSURE`)
- cvar_economic_robustness: `INCONCLUSIVE` (value `NOT_PROVEN_ZERO_EXPOSURE`)
- test_environment_probe: `PASS` (value `True`)
- local_regeneration_probe: `PASS` (value `0`)
- clean_regeneration_proof: `PASS` (value `True`)

## Riscos residuais

- A3/A4 remain closed; no promotion attempted.
- RiskLabAI remains oracle/shadow only.
- Cross-sectional family remains ALIVE_BUT_NOT_PROMOTABLE.
- DSR=0.0 remains a promotion blocker; this gate does not alter thresholds.

## Veredito final: advance / correct / abandon

- `PARTIAL` -> `correct`. Blockers: `['dsr_honest_zero_blocks_promotion', 'cvar_zero_exposure_not_economic_robustness']`.
