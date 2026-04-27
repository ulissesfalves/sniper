# Human Review Checklist - SNIPER Autonomous Mission Closure

## PR readiness

- [ ] Confirmar que o PR sera aberto como draft.
- [ ] Confirmar base `codex/openclaw-sniper-handoff`.
- [ ] Confirmar head `codex/autonomous-sniper-implementation`.
- [ ] Confirmar que nao ha pedido de merge automatico.
- [ ] Confirmar que nao ha promocao para official no texto do PR.

## Commits

- [ ] Revisar `649e9a9` - skill autonomous implementation manager.
- [ ] Revisar `add6ce1` - primeiro gate Phase6.
- [ ] Revisar `b1004fb` - preflight/source-doc alignment.
- [ ] Revisar `f622842` - skill autonomous stop reviewer.
- [ ] Revisar `c4bf284` - artifact rehydration e DSR stop gate.
- [ ] Revisar `e86f0db` - suporte a clean regeneration em clone isolado.
- [ ] Revisar `d7829b7` - gate final de clean regeneration.

## Gates Phase6

- [ ] Verificar `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase6_phase4_artifact_rehydration_and_dsr_stop_gate/gate_report.json`.
- [ ] Verificar `reports/gates/phase6_research_baseline_rehydration_clean_regeneration_gate/gate_report.json`.
- [ ] Confirmar que todos possuem `gate_report.json`, `gate_report.md`, `gate_manifest.json` e `gate_metrics.parquet`.

## Clean regeneration

- [ ] Confirmar que `phase6_research_baseline_rehydration_clean_regeneration_gate` tem `clean_clone_or_equivalent=true`.
- [ ] Confirmar `regeneration_returncode=0`.
- [ ] Confirmar `phase5_clean_gate_status=PASS`.
- [ ] Confirmar que o clone limpo estava no mesmo HEAD da branch fonte.
- [ ] Confirmar que o clone estava limpo antes da regeneracao.
- [ ] Confirmar que os artifacts base foram copiados para o clone, nao fabricados.

## Artifacts

- [ ] Confirmar hashes dos artifacts official Phase4 no gate pack.
- [ ] Confirmar hashes dos artifacts research baseline no gate pack.
- [ ] Confirmar que `missing_required_artifacts=[]`.
- [ ] Confirmar que `missing_regeneration_baseline_artifacts=[]`.
- [ ] Confirmar que artifacts em `data/models/**` continuam tratados como base externa/ignored, nao como promocao official.

## Governanca

- [ ] Confirmar que A3/A4 nao foram reabertos.
- [ ] Confirmar que RiskLabAI permaneceu oracle/shadow.
- [ ] Confirmar que Fast path permaneceu official.
- [ ] Confirmar que cross-sectional permanece `ALIVE_BUT_NOT_PROMOTABLE`.
- [ ] Confirmar que research nao virou official.
- [ ] Confirmar que nao houve credenciais, ordens reais, capital real, merge ou force push.

## Blockers obrigatorios

- [ ] Confirmar `dsr_honest=0.0`.
- [ ] Confirmar `dsr_passed=false`.
- [ ] Confirmar que DSR continua blocker de promocao.
- [ ] Confirmar `cvar_technical_status=PASS_ZERO_EXPOSURE`.
- [ ] Confirmar `cvar_economic_status=NOT_PROVEN_ZERO_EXPOSURE`.
- [ ] Confirmar que CVaR zero exposure nao foi tratado como robustez economica.

## Testes

- [ ] Rodar ou revisar resultado:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_phase6_global_reproducibility_source_alignment_gate.py tests/unit/test_phase5_cross_sectional_latest_headroom_reconciliation_audit.py tests/unit/test_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py tests/unit/test_phase5_cross_sectional_sovereign_hardening_recheck.py tests/unit/test_gate_reports.py tests/unit/test_hmm_regime_alignment.py -q
```

- [ ] Esperado: `26 passed`.

## Decisao humana

- [ ] Aprovar PR draft como evidencia de reprodutibilidade/governanca Phase6.
- [ ] Nao aprovar promocao official.
- [ ] Decidir se a linha cross-sectional deve ser congelada como research-only.
- [ ] Decidir se vale abrir uma nova tese research-only para atacar DSR/CVaR, sem usar esta missao como promocao.
