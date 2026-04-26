# Next Step Recommendation

## Origem da recomendação

Revisão do gate:

`reports/gates/phase6_global_reproducibility_source_alignment_gate/`

Resultado revisado:

- Status: `PARTIAL`
- Decision: `correct`
- Classificação do revisor: `CORRECTION_REQUIRED`
- Subclasse de evidência: `INCONCLUSIVE_EVIDENCE` para ambiente, clean clone e PDF técnico.

O gate Phase 6 resolveu parte da lacuna global, mas não mudou o veredito global do SNIPER. O estado continua `GLOBAL_PARTIAL`.

## Gate sugerido

`phase6_clean_clone_environment_reproducibility_gate`

## Branch sugerida

`codex/phase6-clean-clone-environment-reproducibility`

Base recomendada:

`codex/phase6-global-reproducibility-source-alignment`, após preservar os artifacts do gate Phase 6. Se essa branch ainda não estiver commitada, primeiro consolidar ou trabalhar em worktree limpa para não misturar artifacts locais não versionados.

## Objetivo

Resolver os blockers remanescentes de reprodutibilidade e ambiente antes de qualquer novo trabalho quantitativo.

O gate deve provar, em ambiente isolado ou clone limpo, que o SNIPER consegue:

1. Instalar dependências mínimas de forma reproduzível.
2. Coletar e executar a suíte unitária relevante sem falhas de import/dependência.
3. Reexecutar os runners Phase 5 e Phase 6 com artifacts esperados.
4. Explicar qualquer diferença de hash por causa objetiva.
5. Manter `DSR=0.0`, `ALIVE_BUT_NOT_PROMOTABLE`, RiskLabAI shadow e ausência de promoção official como restrições explícitas.

Este gate não deve tentar melhorar alpha, DSR, Sharpe, thresholds, ranking ou política operacional.

## Escopo

1. Criar ou atualizar:
   - `reports/gates/phase6_clean_clone_environment_reproducibility_gate/`
2. Rodar validação em clone limpo, worktree limpa ou diretório isolado com checkout explícito da branch alvo.
3. Auditar dependências Python que bloquearam a coleta:
   - `hmmlearn` ausente.
   - `polars.Date` ausente/incompatível na versão instalada.
   - warning de `asyncio_mode` desconhecido no pytest, classificado como menor se não bloquear execução.
4. Decidir, com evidência, se o problema é:
   - dependency pin ausente/incorreto;
   - incompatibilidade de código com versão instalada;
   - ambiente local incompleto;
   - artifact ausente;
   - falha real de código.
5. Rodar:
   - coleta completa de testes unitários;
   - testes unitários completos, se a coleta passar;
   - testes bridge/Nautilus existentes;
   - runners de regeneração Phase 5;
   - runner `phase6_global_reproducibility_source_alignment_gate.py`.
6. Validar JSON/parquet dos artifacts produzidos.
7. Registrar hashes before/after e divergências objetivas.
8. Opcional, se couber no mesmo gate sem desviar do foco: gerar um artifact de OCR/render/text-extraction para `docs/SNIPER_v10.10_Technical_Architecture_presentation.pdf`, classificando o resultado como PASS/PARTIAL/INCONCLUSIVE.

## Fora de escopo

1. Não alterar thresholds de DSR, Sharpe, PBO, ECE, N_eff ou subperíodos.
2. Não implementar nova hipótese alpha.
3. Não reabrir A3/A4.
4. Não promover a família cross-sectional.
5. Não tratar `PASS_ZERO_EXPOSURE` de CVaR como validação econômica com exposição real.
6. Não alimentar paper official.
7. Não transformar RiskLabAI em official.
8. Não apagar artifacts locais sem registrar motivo, hash e impacto.
9. Não mascarar falhas de import/dependência com skips silenciosos.

## Blockers que o próximo gate precisa resolver

### Blocker de metodologia

- `clean_regeneration=PARTIAL`: o gate Phase 6 rodou no workspace atual, não em clone limpo isolado.

### Blocker de dependência/ambiente

- `python -m pytest tests/unit -q` falhou na coleta:
  - `AttributeError: module 'polars' has no attribute 'Date'`.
  - `ModuleNotFoundError: No module named 'hmmlearn'`.

### Blocker de artifact

- `docs/SNIPER_v10.10_Technical_Architecture_presentation.pdf` segue inconclusivo sem OCR/render dedicado.
- `data/models/research/**` e `reports/gates/**` ainda dependem de prova de regeneração em clone limpo.

### Blocker quantitativo preservado

- `dsr_honest=0.0` com `n_trials_honest=5000`.
- Família cross-sectional continua `ALIVE_BUT_NOT_PROMOTABLE`.

### Blocker de governança

- Nenhuma violação foi detectada no gate Phase 6. O próximo gate deve preservar esse estado.

## Artifacts esperados

Obrigatórios:

- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.md`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_manifest.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_metrics.parquet`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/environment_reproducibility_report.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/dependency_audit.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/clean_clone_regeneration_report.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/pytest_collection_report.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/artifact_diff_report.json`

Opcional, se executado:

- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/technical_architecture_pdf_extraction_report.json`

## Arquivos provavelmente alterados

Somente se a evidência apontar necessidade real:

- `requirements.txt`
- `pyproject.toml`
- `pytest.ini` ou configuração equivalente de pytest
- `services/data_inserter/collectors/binance.py`, apenas se o blocker `polars.Date` for incompatibilidade de código e não de versão.
- `docs/SNIPER_regeneration_guide.md`, se comandos de ambiente precisarem ser documentados.
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/**`

Não alterar modelo, policy, thresholds ou artifacts official para forçar PASS.

## Testes e validações obrigatórios

Pré-checagem:

```powershell
$env:PYTHONUTF8='1'
git status --short
git branch --show-current
git rev-parse HEAD
python --version
python -m pip --version
python -m pip check
python - <<'PY'
import importlib
for name in ["polars", "hmmlearn", "pandas", "pyarrow", "sklearn"]:
    try:
        module = importlib.import_module(name)
        print(name, "OK", getattr(module, "__version__", "unknown"))
    except Exception as exc:
        print(name, "FAIL", repr(exc))
PY
```

Coleta e testes:

```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/unit --collect-only -q
python -m pytest tests/unit/test_gate_reports.py -q
python -m pytest tests/unit/test_nautilus_bridge_contract.py tests/unit/test_nautilus_bridge_acceptance.py tests/unit/test_nautilus_bridge_phase4_publisher.py tests/unit/test_nautilus_bridge_consumer.py tests/unit/test_nautilus_bridge_reconciler.py tests/unit/test_nautilus_bridge_phase4_paper_daemon.py tests/unit/test_nautilus_bridge_phase4_paper_once.py -q
python -m pytest tests/unit -q
```

Regeneração:

```powershell
$env:PYTHONUTF8='1'
python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py
python services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py
python services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py
python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py
python services/ml_engine/phase4_gate_diagnostic.py
python services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py
```

Validação de artifacts:

```powershell
$env:PYTHONUTF8='1'
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_manifest.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/environment_reproducibility_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/dependency_audit.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/clean_clone_regeneration_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/pytest_collection_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/artifact_diff_report.json > $null
python - <<'PY'
import pandas as pd
pd.read_parquet("reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_metrics.parquet")
print("gate_metrics.parquet OK")
PY
```

## Critérios de PASS

PASS exige todos os itens:

1. Clone/worktree isolado criado e registrado com branch, commit e diff limpo inicial.
2. Dependências necessárias resolvidas ou pinadas de forma reproduzível.
3. `python -m pytest tests/unit --collect-only -q` passa.
4. Suíte unitária completa passa ou falhas restantes são reais, não de ambiente, e classificadas fora do escopo com evidência forte.
5. Runners Phase 5 e Phase 6 executam com exit code 0 no ambiente isolado.
6. Artifacts esperados são gerados e validados.
7. Diferenças de hash são inexistentes ou explicadas por causa objetiva e reproduzível.
8. `clean_regeneration` deixa de ser apenas local e passa a ser evidência de clone limpo.
9. Nenhum research/RiskLabAI é promovido para official.
10. A3/A4 permanecem fechados.
11. `ALIVE_BUT_NOT_PROMOTABLE` permanece não-promotable.
12. `DSR=0.0` continua bloqueando promoção.

## Critérios de PARTIAL

PARTIAL se:

1. Ambiente isolado é criado, mas algum artifact diverge com causa objetiva ainda pendente.
2. Coleta de testes passa, mas suíte completa tem falhas reais não relacionadas a dependência.
3. Dependências são identificadas e documentadas, mas ainda não há fix/pin reproduzível.
4. Regeneração roda parcialmente e classifica gaps sem promoção.
5. PDF técnico permanece inconclusivo, mas isolado como artifact blocker não operacional.

## Critérios de FAIL

FAIL se:

1. Ambiente isolado não pode ser criado.
2. Dependências continuam sem diagnóstico objetivo.
3. Coleta de testes continua quebrada por import/dependência sem resolução.
4. Regeneração Phase 5/Phase 6 falha sem causa objetiva.
5. Algum artifact research/shadow é tratado como official.
6. A3/A4 são reabertos.
7. O gate altera métrica/threshold para forçar PASS.

## Critérios de INCONCLUSIVE

INCONCLUSIVE se:

1. Falta acesso a dependência, Python runtime, clone, parquet ou artifacts necessários.
2. O ambiente externo impede distinguir falha de código de falha de instalação.
3. `gate_metrics.parquet` não pode ser lido por falta de engine parquet.
4. PDF técnico não pode ser renderizado/OCR por falta de ferramenta e isso for parte do escopo executado.

## Riscos

- Confundir ambiente local funcionando parcialmente com reprodutibilidade.
- Resolver dependência com instalação manual não versionada.
- Usar skip de teste para mascarar import quebrado.
- Tratar `PASS_ZERO_EXPOSURE` como validação econômica.
- Avançar para paper sem DSR/Sharpe/subperíodos/clean clone.
- Misturar artifacts local-only com official.

## O que não deve ser feito

- Não promover nada para official.
- Não declarar paper readiness.
- Não declarar capital readiness.
- Não reabrir A3/A4.
- Não alterar thresholds.
- Não atacar alpha/DSR neste gate.
- Não usar RiskLabAI como official.
- Não apagar artifacts sem registro.

## Skill recomendada para gerar o prompt seguinte

Usar:

`$sniper-next-step-prompt-builder`

Prompt curto sugerido:

`[$sniper-next-step-prompt-builder](C:\\Users\\uliss\\Documentos\\Meus_projetos\\sniper\\.agents\\skills\\sniper-next-step-prompt-builder\\SKILL.md) Leia reports/audits/global_spec_adherence/next_step_recommendation.md e gere o prompt completo para executar o gate phase6_clean_clone_environment_reproducibility_gate.`
