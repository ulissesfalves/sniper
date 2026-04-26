[$sniper-gate-governance](C:\Users\uliss\Documentos\Meus_projetos\sniper\.agents\skills\sniper-gate-governance\SKILL.md)
[$sniper-quant-research-implementation](C:\Users\uliss\Documentos\Meus_projetos\sniper\.agents\skills\sniper-quant-research-implementation\SKILL.md)
[$sniper-paper-execution-hardening](C:\Users\uliss\Documentos\Meus_projetos\sniper\.agents\skills\sniper-paper-execution-hardening\SKILL.md)

Execute o próximo gate recomendado do SNIPER.

Repo local:
C:\Users\uliss\Documentos\Meus_projetos\sniper

Branch base:
codex/phase6-global-reproducibility-source-alignment

Gate:
phase6_clean_clone_environment_reproducibility_gate

Branch sugerida:
codex/phase6-clean-clone-environment-reproducibility

Objetivo:
Resolver os blockers remanescentes de reprodutibilidade e ambiente antes de qualquer novo trabalho quantitativo. O gate deve provar, em clone limpo, worktree isolada ou ambiente isolado equivalente, que dependências, coleta de testes, regeneração Phase 5/Phase 6 e validação de artifacts são reproduzíveis.

Escopo:
1. Criar `reports/gates/phase6_clean_clone_environment_reproducibility_gate/`.
2. Trabalhar em ambiente isolado ou worktree limpa, com branch/commit registrados.
3. Auditar dependências que quebraram a coleta no gate anterior: `hmmlearn` ausente e `polars.Date` ausente/incompatível.
4. Classificar cada falha como blocker de dependência, código, artifact, metodologia ou governança.
5. Rodar coleta de testes, testes unitários, testes bridge/Nautilus existentes, runners Phase 5, `phase4_gate_diagnostic.py` e `phase6_global_reproducibility_source_alignment_gate.py`.
6. Gerar artifacts com comandos, exit codes, hashes, divergências e classificação objetiva.
7. Opcionalmente, se não desviar do foco, criar artifact de OCR/render/text extraction para `docs/SNIPER_v10.10_Technical_Architecture_presentation.pdf`.

Fora de escopo:
1. Não alterar thresholds de DSR, Sharpe, PBO, ECE, N_eff ou subperíodos.
2. Não implementar alpha, ranking, policy tweak ou tuning.
3. Não reabrir A3/A4.
4. Não promover research/RiskLabAI/cross-sectional para official.
5. Não declarar paper readiness, testnet readiness ou capital readiness.
6. Não usar skip de teste para mascarar import quebrado.
7. Não tratar `PASS_ZERO_EXPOSURE` de CVaR como robustez econômica com exposição real.

Fontes de verdade:
1. Estado atual do repositório.
2. `reports/audits/global_spec_adherence/next_step_recommendation.md`
3. `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_report.json`
4. `reports/gates/phase6_global_reproducibility_source_alignment_gate/gate_result_review.md`
5. `docs/SNIPER_openclaw_handoff.md`
6. `docs/SNIPER_regeneration_guide.md`
7. `reports/gates/**`
8. `data/models/**`, `data/models/research/**`, `data/parquet/**`, quando aplicável.

Resumo do estado atual:
1. Veredito global: `GLOBAL_PARTIAL`.
2. Gate anterior: `phase6_global_reproducibility_source_alignment_gate`, resultado `PARTIAL / correct`.
3. Resolvido: source-doc alignment Fase 4-R4, CVaR técnico persistido para snapshot com exposição zero, validações direcionadas e bridge/Nautilus passaram.
4. Ainda bloqueado: clean clone não provado, suíte unitária completa inconclusiva por `polars.Date` e `hmmlearn`, PDF técnico inconclusivo, `dsr_honest=0.0`, cross-sectional `ALIVE_BUT_NOT_PROMOTABLE`.
5. O gate anterior não alterou o veredito global e não autorizou promoção.

Instruções:
1. Rode `git status --short`, `git branch --show-current`, `git rev-parse HEAD`.
2. Preserve mudanças existentes; não sobrescreva artifacts do gate anterior.
3. Faça plano curto antes de editar.
4. Crie ou use a branch `codex/phase6-clean-clone-environment-reproducibility`.
5. Use ambiente isolado/worktree limpa sempre que possível; se não for possível, classifique como `INCONCLUSIVE`.
6. Implemente apenas o necessário para tornar dependências/testes/regeneração reproduzíveis.
7. Se modificar dependências, justifique com evidência objetiva e rode validações.
8. Gere o gate pack completo e artifacts auxiliares.
9. Não promova nada para official.

Entregáveis obrigatórios:
1. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.json`
2. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.md`
3. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_manifest.json`
4. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_metrics.parquet`
5. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/environment_reproducibility_report.json`
6. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/dependency_audit.json`
7. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/clean_clone_regeneration_report.json`
8. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/pytest_collection_report.json`
9. `reports/gates/phase6_clean_clone_environment_reproducibility_gate/artifact_diff_report.json`

Pré-checagem:
```powershell
$env:PYTHONUTF8='1'
git status --short
git branch --show-current
git rev-parse HEAD
python --version
python -m pip --version
python -m pip check
python -c "import importlib; mods=['polars','hmmlearn','pandas','pyarrow','sklearn']; [print(m, 'OK', getattr(importlib.import_module(m), '__version__', 'unknown')) if importlib.util.find_spec(m) else print(m, 'MISSING') for m in mods]"
```

Testes obrigatórios:
```powershell
$env:PYTHONUTF8='1'
python -m pytest tests/unit --collect-only -q
python -m pytest tests/unit/test_gate_reports.py -q
python -m pytest tests/unit/test_nautilus_bridge_contract.py tests/unit/test_nautilus_bridge_acceptance.py tests/unit/test_nautilus_bridge_phase4_publisher.py tests/unit/test_nautilus_bridge_consumer.py tests/unit/test_nautilus_bridge_reconciler.py tests/unit/test_nautilus_bridge_phase4_paper_daemon.py tests/unit/test_nautilus_bridge_phase4_paper_once.py -q
python -m pytest tests/unit -q
```

Regeneração obrigatória:
```powershell
$env:PYTHONUTF8='1'
python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py
python services/ml_engine/phase5_cross_sectional_sovereign_hardening_recheck.py
python services/ml_engine/phase5_cross_sectional_operational_fragility_audit_and_bounded_correction.py
python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py
python services/ml_engine/phase4_gate_diagnostic.py
python services/ml_engine/phase6_global_reproducibility_source_alignment_gate.py
```

Validação dos artifacts:
```powershell
$env:PYTHONUTF8='1'
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_manifest.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/environment_reproducibility_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/dependency_audit.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/clean_clone_regeneration_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/pytest_collection_report.json > $null
python -m json.tool reports/gates/phase6_clean_clone_environment_reproducibility_gate/artifact_diff_report.json > $null
python -c "import pandas as pd; pd.read_parquet('reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_metrics.parquet'); print('gate_metrics.parquet OK')"
git status --short
```

Critérios de PASS:
1. Ambiente isolado/clean clone/worktree limpa registrado com branch, commit e diff limpo inicial.
2. Dependências resolvidas ou pinadas de forma reproduzível.
3. `pytest --collect-only` passa.
4. Suíte unitária completa passa, ou falhas restantes são reais, não de ambiente, e classificadas com evidência.
5. Runners Phase 5 e Phase 6 executam com exit code 0 no ambiente isolado.
6. Artifacts esperados são gerados e validados.
7. Hash divergences são inexistentes ou explicadas objetivamente.
8. Nenhum research/RiskLabAI é promovido.
9. A3/A4 seguem fechados.
10. `ALIVE_BUT_NOT_PROMOTABLE` segue não-promotable.
11. `DSR=0.0` segue bloqueando promoção.

Critérios de PARTIAL:
1. Ambiente isolado existe, mas artifact diverge com causa objetiva pendente.
2. Coleta passa, mas suíte completa tem falhas reais classificadas.
3. Dependências são diagnosticadas, mas ainda sem pin/fix reproduzível.
4. Regeneração roda parcialmente e classifica gaps sem promoção.
5. PDF técnico permanece inconclusivo, mas isolado como blocker documental.

Critérios de FAIL:
1. Ambiente isolado não pode ser criado.
2. Dependências seguem sem diagnóstico objetivo.
3. Coleta continua quebrada por import/dependência.
4. Regeneração falha sem causa objetiva.
5. Algum research/shadow vira official.
6. A3/A4 são reabertos.
7. O gate altera métrica/threshold para forçar PASS.

Critérios de INCONCLUSIVE:
1. Falta runtime, dependência, parquet, clone ou artifact essencial.
2. Ambiente externo impede distinguir falha de código de falha de instalação.
3. `gate_metrics.parquet` não pode ser lido por falta de engine.
4. PDF técnico não pode ser renderizado/OCR se esse subescopo for executado.

Resposta final esperada:
1. Veredito do gate.
2. Dependências auditadas e resolução/classificação.
3. Resultado de `pytest --collect-only` e suíte unitária.
4. Resultado dos testes bridge/Nautilus.
5. Resultado dos runners Phase 5/Phase 6.
6. Artifacts criados.
7. Arquivos alterados.
8. Comandos executados.
9. Blockers remanescentes.
10. Confirmação explícita: nada foi promovido para official, A3/A4 não foram reabertos, RiskLabAI permaneceu shadow, cross-sectional permaneceu `ALIVE_BUT_NOT_PROMOTABLE`, `DSR=0.0` não foi mascarado.
