# Next Step Recommendation

## Gate sugerido

`phase6_global_reproducibility_source_alignment_gate`

## Objetivo

Executar uma rodada de evidência reproduzível que alinhe documentação, source rastreado e artifacts de gate. O gate deve fechar lacunas de auditoria sem promover modelo, sem reabrir A3/A4 e sem transformar research em official.

## Branch sugerida

`codex/phase6-global-reproducibility-source-alignment`

## Arquivos provavelmente alterados

- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.md`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_manifest.json`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/source_doc_alignment.json`
- `reports/gates/phase6_global_reproducibility_source_alignment_gate/portfolio_cvar_report.json`
- `docs/SNIPER_openclaw_handoff.md`
- `docs/SNIPER_regeneration_guide.md`
- Opcionalmente, source Fase 4 se a decisão for restaurar os módulos documentados em vez de corrigir a documentação.

## Artifacts esperados

- Manifest de source-doc-artifact com hashes.
- Resultado de clean regeneration dos gates Phase 5 relevantes.
- Relatório de alinhamento Fase 4-R4: módulos presentes, módulos ausentes, decisão de restauração ou atualização documental.
- Relatório empírico de CVaR/stress `rho=1`, drawdown e exposure para snapshot/portfolio current.
- Sumário explicitando `official`, `research`, `sandbox` e `shadow`.
- Registro de blockers quantitativos preservados: DSR, Sharpe, subperíodos, cross-sectional status.

## Critérios objetivos

PASS:

- Clean regeneration reproduz os artifacts esperados ou explica diferenças com hashes e causa objetiva.
- Source e documentação Fase 4-R4 ficam alinhados por restauração de módulos ou correção documental versionada.
- CVaR empirical artifact é persistido e validável.
- Nenhum artifact research/RiskLabAI é tratado como official.
- A3/A4 permanecem fechados.

PARTIAL:

- Regeneration roda, mas algum artifact local não reproduz exatamente e a divergência fica explicada.
- CVaR é persistido, mas ainda não há PASS quantitativo global.
- Technical Architecture PDF permanece inconclusivo, mas a lacuna fica documentada e isolada.

FAIL:

- Regeneration não roda.
- Artifacts official divergem sem explicação objetiva.
- Source-doc Fase 4-R4 permanece contraditório.
- Qualquer research/shadow é promovido como official.
- A3/A4 são reabertos sem nova evidência causal forte.

## Comandos sugeridos

```powershell
$env:PYTHONUTF8='1'
git switch -c codex/phase6-global-reproducibility-source-alignment
python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py
python services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py
python services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py
python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py
python services/ml_engine/phase4_gate_diagnostic.py
python -m pytest tests/unit/test_nautilus_bridge_contract.py tests/unit/test_nautilus_bridge_acceptance.py tests/unit/test_nautilus_bridge_publisher.py tests/unit/test_nautilus_bridge_consumer.py tests/unit/test_nautilus_bridge_reconciler.py tests/unit/test_nautilus_bridge_daemon.py
git status --short
```

Adicionar ao gate, se ainda não existir script dedicado:

```powershell
$env:PYTHONUTF8='1'
python services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py
```

## Riscos a controlar

- Confundir implementação existente com aprovação quantitativa.
- Reabrir A3/A4 por conveniência narrativa.
- Promover `ALIVE_BUT_NOT_PROMOTABLE`.
- Usar RiskLabAI como official.
- Aceitar artifacts locais sem hash/regeneration.
- Tratar CVaR estrutural em código como CVaR empírico aprovado.
- Ignorar a divergência Fase 4-R4 entre documentação e source rastreado.

## O que não deve ser feito na próxima rodada

- Não implementar ajustes de performance antes do gate de alinhamento.
- Não alterar thresholds de DSR, Sharpe, PBO, ECE, N_eff ou subperíodos para forçar PASS.
- Não promover snapshots para paper official.
- Não declarar capital readiness.
- Não substituir evidência por narrativa.

