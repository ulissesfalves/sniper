## Resumo executivo

Gate `phase6_global_reproducibility_source_alignment_gate` concluído com status `PARTIAL` e decisão `correct`.

O gate produziu evidência local de alinhamento source-doc-artifact, persistiu CVaR empírico do snapshot official atual e reexecutou os runners Phase 5/Phase 4 com hashes before/after. A classificação permanece limitada porque não houve clone limpo isolado e os blockers quantitativos continuam preservados.

## Baseline congelado

- Branch base recomendada: `codex/openclaw-sniper-handoff`
- Branch executada: `codex/phase6-clean-clone-environment-reproducibility`
- Commit: `f68832974e898e87cf9c6f6e68a2e43127d44089`
- A3/A4: não reabertos
- Cross-sectional: `ALIVE_BUT_NOT_PROMOTABLE`
- RiskLabAI: oracle/shadow, não official

## Mudanças implementadas

- Criado runner `services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py`.
- Adicionada nota documental de alinhamento source-doc na memória Fase 4-R.
- Persistidos `source_doc_alignment.json` e `portfolio_cvar_report.json`.
- Nenhuma promoção official foi feita.

## Artifacts gerados

- `reports\gates\phase6_global_reproducibility_source_alignment_gate\source_doc_alignment.json`
- `reports\gates\phase6_global_reproducibility_source_alignment_gate\portfolio_cvar_report.json`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.md`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_manifest.json`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_metrics.parquet`

## Resultados

- Source-doc alignment: `PASS`
- Clean/local regeneration: `PARTIAL`
- Portfolio CVaR: `PASS_ZERO_EXPOSURE`
- Targeted validation: `PASS`
- Full unit suite: `INCONCLUSIVE`
- DSR honesto: `0.0`
- Sharpe OOS: `0.8808`
- Hard blockers: `['DSR honesto', 'DSR invalidação global', 'Sharpe OOS', 'Subperiodos']`

## Avaliação contra gates

- source_doc_alignment_status: `PASS` (PASS)
- portfolio_cvar_stress_rho1: `PASS` (0.0)
- regeneration_commands_exit_zero: `PASS` (True)
- targeted_validation_exit_zero: `PASS` (True)
- full_unit_suite_exit_zero: `INCONCLUSIVE` (False)
- clean_clone_proven: `PARTIAL` (False)
- technical_architecture_pdf_text_auditable: `INCONCLUSIVE` (False)
- dsr_honest_preserved: `FAIL` (0.0)
- cross_sectional_status: `PASS` (ALIVE_BUT_NOT_PROMOTABLE)
- upstream_gate_phase5_cross_sectional_hardening_baseline: `PASS` (PASS)
- upstream_gate_phase5_cross_sectional_latest_headroom_reconciliation_audit: `PASS` (PASS)
- upstream_gate_phase5_cross_sectional_operational_fragility_audit_and_bounded_correction: `PARTIAL` (PARTIAL)
- upstream_gate_phase5_cross_sectional_recent_regime_policy_falsification: `PARTIAL` (PARTIAL)
- upstream_gate_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate: `PASS` (PASS)
- upstream_gate_phase5_cross_sectional_sovereign_hardening_recheck: `PARTIAL` (PARTIAL)
- upstream_gate_phase5_stage_a3_activation_calibration_correction: `PARTIAL` (PARTIAL)
- upstream_gate_phase5_stage_a3_calibrator_family_final_shootout: `PASS` (PASS)
- upstream_gate_phase5_stage_a3_choke_audit: `PASS` (PASS)
- upstream_gate_phase5_stage_a3_spec_hardening: `FAIL` (FAIL)

## Riscos residuais

- Treating this gate as model promotion would violate the audit scope.
- Existing research artifacts are local and may not exist in a clean clone.
- CVaR was computed for the current official snapshot only; zero exposure is not paper readiness.
- RiskLabAI must remain oracle/shadow.

## Veredito final: advance / correct / abandon

`correct`. O próximo passo é uma regeneração em clone limpo isolado ou aceitar este gate como PARTIAL local-only. Não há paper readiness, model promotion ou capital readiness nesta rodada.
