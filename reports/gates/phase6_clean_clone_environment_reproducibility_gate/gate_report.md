## Resumo executivo

Gate `phase6_clean_clone_environment_reproducibility_gate` concluido com status `FAIL` e decisao `correct`.

O ambiente atual nao prova reprodutibilidade limpa: a instalacao pinned completa via `requirements.txt` falha neste Python, a coleta de testes permanece bloqueada por dependencias/imports, e o worktree limpo de HEAD nao materializa o mesmo estado sujo/untracked usado pelos gates recentes.

## Baseline congelado

- Branch base: `codex/phase6-global-reproducibility-source-alignment`
- Branch executada: `codex/phase6-clean-clone-environment-reproducibility`
- Commit: `f68832974e898e87cf9c6f6e68a2e43127d44089`
- Worktree dirty antes: `True`
- A3/A4: fechados
- RiskLabAI: shadow/oracle
- Cross-sectional: `ALIVE_BUT_NOT_PROMOTABLE`

## Mudanças implementadas

- Criado runner de gate de ambiente/reprodutibilidade.
- Gerados reports obrigatorios do gate.
- Nenhum threshold, alpha, ranking, policy ou dependencia do repo foi alterado.

## Artifacts gerados

- `reports\gates\phase6_clean_clone_environment_reproducibility_gate\environment_reproducibility_report.json`
- `reports\gates\phase6_clean_clone_environment_reproducibility_gate\dependency_audit.json`
- `reports\gates\phase6_clean_clone_environment_reproducibility_gate\clean_clone_regeneration_report.json`
- `reports\gates\phase6_clean_clone_environment_reproducibility_gate\pytest_collection_report.json`
- `reports\gates\phase6_clean_clone_environment_reproducibility_gate\artifact_diff_report.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_report.md`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_manifest.json`
- `reports/gates/phase6_clean_clone_environment_reproducibility_gate/gate_metrics.parquet`

## Resultados

- Dependency audit: `FAIL`
- pytest collect/full: `FAIL`
- Bridge/Nautilus: `PASS`
- Current workspace regeneration: `PASS`
- Isolated worktree probe: `FAIL`
- Artifact diff: `PARTIAL`
- DSR honesto: `0.0`
- CVaR previous status: `PASS_ZERO_EXPOSURE`

## Avaliação contra gates

- environment_reproducibility_status: `FAIL` (FAIL)
- dependency_audit_status: `FAIL` (FAIL)
- root_requirements_dry_run_passed: `FAIL` (False)
- pytest_collect_only_exit_zero: `FAIL` (False)
- bridge_nautilus_exit_zero: `PASS` (True)
- current_workspace_regeneration_exit_zero: `PASS` (True)
- isolated_worktree_regeneration_status: `FAIL` (FAIL)
- dsr_honest_preserved: `FAIL` (0.0)
- cross_sectional_status_preserved: `PASS` (ALIVE_BUT_NOT_PROMOTABLE)

## Riscos residuais

- Installing partial dependencies outside the pinned environment can mask reproducibility drift.
- Python 3.13 is not proven compatible with the root pinned requirements in this workspace.
- Dirty/untracked gate sources prevent a clean checkout of HEAD from matching the current working tree.
- PASS_ZERO_EXPOSURE CVaR remains technical persistence only, not economic robustness with exposure.

## Veredito final: advance / correct / abandon

`correct`. O proximo passo deve corrigir ambiente e materializacao limpa antes de qualquer nova pesquisa quantitativa. Este gate nao autoriza promocao, paper readiness, testnet readiness ou capital readiness.
