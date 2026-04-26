# Next Step Recommendation

## Gate sugerido

`phase6_source_doc_and_regeneration_preflight_gate`

## Origem da recomendacao

O gate `phase6_global_reproducibility_source_alignment_gate` foi executado em `codex/autonomous-sniper-implementation` e retornou `PARTIAL/correct`.

Blockers atuais:

- `phase4_r4_source_doc_mismatch`
- `cvar_zero_exposure_not_economic_robustness`
- `clean_regeneration_not_proven_in_clean_clone_or_equivalent`
- `local_regeneration_probe_failed`

## Objetivo

Corrigir o proximo blocker material sem promover modelo: alinhar a memoria Phase 4-R4 ao source rastreado e transformar a falha de regeneracao por artifact ausente em preflight claro, auditavel e sem stacktrace.

Este gate deve continuar sendo de governanca/reprodutibilidade, nao de performance.

## Branch sugerida

`codex/autonomous-sniper-implementation`

## Escopo

- Revisar `docs/SNIPER_memoria_especificacao_controle_fase4R_v3.md` e alinhar a narrativa Phase 4-R4 com o source realmente rastreado.
- Decidir explicitamente entre:
  - restaurar modulos Phase 4-R4 documentados; ou
  - registrar correcao documental versionada quando o source atual preserva a funcionalidade por `phase4_cpcv.py`, `phase4_gate_diagnostic.py` e `phase4_stage_a_experiment.py`.
- Endurecer `services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py` para classificar `data/models/phase4` ausente como `MISSING_OFFICIAL_PHASE4_ARTIFACTS`.
- Atualizar ou adicionar testes unitarios para a classificacao de preflight.
- Regenerar artifacts de gate.

## Fora de escopo

- Nao promover artifacts para official.
- Nao reabrir A3/A4.
- Nao tratar `ALIVE_BUT_NOT_PROMOTABLE` como promotable.
- Nao usar RiskLabAI como official.
- Nao alterar thresholds de DSR, Sharpe, PBO, ECE, N_eff ou subperiodos.
- Nao declarar readiness de paper/capital.
- Nao mascarar `PASS_ZERO_EXPOSURE` como robustez economica completa.

## Artifacts esperados

- `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/gate_report.json`
- `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/gate_report.md`
- `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/gate_manifest.json`
- `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/gate_metrics.parquet`
- `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/source_doc_alignment.json`
- `reports/gates/phase6_source_doc_and_regeneration_preflight_gate/clean_regeneration_preflight.json`

## Validacoes obrigatorias

```powershell
.\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_phase6_global_reproducibility_source_alignment_gate.py -q
.\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_gate_reports.py tests/unit/test_hmm_regime_alignment.py -q
.\\.venv\\Scripts\\python.exe -m pytest tests/unit/test_phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py tests/unit/test_phase5_cross_sectional_sovereign_hardening_recheck.py -q
.\\.venv\\Scripts\\python.exe services\\ml_engine\\phase6_global_reproducibility_source_alignment_gate.py
```

## Criterios PASS

- A divergencia Phase 4-R4 entre documentacao e source rastreado fica resolvida por decisao versionada e auditavel.
- Artifact official ausente e classificado por preflight como blocker de artifact, sem stacktrace.
- O gate nao promove official, nao reabre A3/A4 e nao altera thresholds.
- Testes obrigatorios passam.

## Criterios PARTIAL

- Source-doc fica alinhado, mas clean regeneration continua bloqueada por artifact official ausente.
- O preflight melhora a evidencia, mas ainda nao ha clone limpo ou ambiente equivalente.

## Criterios FAIL

- Qualquer research/shadow vira official.
- A3/A4 sao reabertos sem evidencia causal nova forte.
- A documentacao e alterada para fabricar PASS.
- DSR 0.0 e mascarado ou thresholds sao relaxados.

## Criterios INCONCLUSIVE

- Ambiente, artifact base ou clean workspace ausente impede validar os criterios materiais.

## Riscos

- Confundir correcao documental com mudanca de especificacao.
- Restaurar modulos grandes sem necessidade e aumentar blast radius.
- Aceitar clean regeneration sem clone limpo ou equivalente.
- Tratar CVaR de exposicao zero como robustez economica.

## Skill recomendada

`sniper-next-step-prompt-builder`
