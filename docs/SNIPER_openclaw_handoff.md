# SNIPER OpenClaw Handoff

## Objetivo

Consolidar o estado atual do projeto SNIPER em uma branch GitHub utilizavel para handoff e continuidade em um novo ambiente com OpenClaw + Codex.

## Branch de handoff

- Branch consolidada: `codex/openclaw-sniper-handoff`
- Baseline research-only correta: `phase5_cross_sectional_sovereign_closure_restored`
- Estado final da familia cross-sectional soberana: `ALIVE_BUT_NOT_PROMOTABLE`
- Estado final da linha A3: `encerrada como structural choke`

## Commits e branches relevantes desta trilha

- `8ebb4161d7b4fee4e45f40ac65833e17b9d71bd3` / `codex/phase5-stage-a3-choke-audit`
  - localizou o choke dominante do A3-q60
- `b498a11b87e615e3c1aa37d15519b4703ee30328` / `codex/phase5-stage1-activation-calibration-correction`
  - reconciliação `raw -> calibrated -> aggregated sovereign activation`
- `bc7fc59e41522b22c43759308777d4d356eca85e` / `codex/phase5-stage1-calibrator-family-final-shootout`
  - encerrou a linha A3 como choke estrutural
- `9e3e63c33e4f6a1ab44d908b68d062f30fac77eb` / `codex/phase5-cross-sectional-hardening-baseline`
  - primeiro hardening baseline, ainda ancorado em lineage incorreta
- `45fb61c3867e4d5eedf60b641e2b227cedeb52b6` / `codex/phase5-cross-sectional-latest-headroom-reconciliation-audit`
  - reconciliou a divergencia closure vs hardening
- `b9f8cc55084a435110a5bff41c8fcdfaefadd4e7` / `codex/phase5-cross-sectional-sovereign-closure-bundle-restore`
  - restore research-only do bundle soberano historico com `EXACT_RESTORE`
- `720abd75322e16f730ff43ade100f5c89f7c5f32` / `codex/phase5-cross-sectional-sovereign-hardening-recheck`
  - recheck do hardening ancorado na lineage soberana correta
- `1b5dbaefccbd9b1655e5551bcb5279e726c831d8` / `codex/phase5-cross-sectional-operational-fragility-bounded-correction`
  - fragilidade operacional dominante classificada como `REGIME_DEPENDENCE_DOMINANT`
- `adce4c849849761653d5bb0342d8d47608a3ea8d` / `codex/phase5-cross-sectional-recent-regime-policy-falsification`
  - ultima rodada bounded recente; familia mantida viva, mas nao promotavel

## O que foi aprovado

- Restore research-only da baseline soberana historica com equivalencia forte.
- Uso obrigatorio da baseline `phase5_cross_sectional_sovereign_closure_restored` para qualquer continuidade da familia cross-sectional.
- Conclusao de que a familia cross-sectional soberana continua viva em `latest/headroom`.

## O que foi reprovado no merito

- Linha A3/q60:
  - choke estrutural confirmado
  - sem fix honesto de calibrador que recuperasse `DSR > 0`
- Politicas bounded recentes sobre a familia cross-sectional soberana:
  - melhoraram `sharpe_operational`
  - preservaram `latest/headroom`
  - mas nao conseguiram tirar `dsr_honest` de `0.0`

## O que ficou congelado

- Fast path official continua official.
- RiskLabAI continua apenas como oracle/shadow.
- Regua soberana continua sendo decision-space causal.
- Familia cross-sectional continua sendo a familia vencedora da Fase 4.
- A linha A3 nao deve ser reaberta sem evidencia nova forte.
- A lineage soberana correta continua sendo `phase5_cross_sectional_sovereign_closure_restored`.

## O que nao deve ser reaberto sem evidencia nova forte

- A3/A4 e micro-rodadas adicionais de calibrador da linha A3.
- Uso da baseline antiga `phase4_cross_sectional_ranking_baseline` como ancora soberana quando divergir do restore research-only correto.
- Nova grade generica de policy tweaks recentes sem hipotese causal nova e falsificavel.
- Mudanca da regua soberana de `latest/headroom/recent/historical active` baseada em proxies auxiliares.

## Proxima trilha logica

Se houver continuidade da familia cross-sectional, a proxima trilha deve partir da baseline soberana correta e atacar a fragilidade operacional de forma estrutural, nao mais via pequenos tweaks bounded recentes. Se isso nao for autorizado, congelar a familia como viva, porem nao promotavel.

## Continuacao em novo ambiente

1. Clonar a branch `codex/openclaw-sniper-handoff`.
2. Instalar as dependencias do projeto.
3. Regenerar os artifacts research-only nao versionados conforme `docs/SNIPER_regeneration_guide.md`.
4. Validar primeiro o restore soberano e so depois qualquer nova trilha research-only.

## Comandos minimos para continuar

```bash
git clone https://github.com/ulissesfalves/sniper.git
cd sniper
git checkout codex/openclaw-sniper-handoff
python -m pip install -r requirements.txt
python services/ml_engine/phase5_cross_sectional_sovereign_closure_bundle_restore_and_revalidate.py
python services/ml_engine/phase5_cross_sectional_recent_regime_policy_falsification.py
```

## Artifacts research nao versionados

Os outputs em `data/models/research/**` e `reports/gates/**` desta trilha nao devem ser assumidos como presentes apos o clone novo.

Os principais artifacts research-only fora do versionamento util para continuidade sao:

- `data/models/research/phase4_cross_sectional_ranking_baseline/stage_a_predictions.parquet`
- `data/models/research/phase5_cross_sectional_sovereign_closure_restored/**`
- `data/models/research/phase5_cross_sectional_sovereign_restore/**`
- `data/models/research/phase5_cross_sectional_sovereign_hardening_recheck/**`
- `data/models/research/phase5_cross_sectional_operational_fragility/**`
- `data/models/research/phase5_cross_sectional_recent_regime/**`

Esses outputs devem ser regenerados a partir do codigo desta branch, nao copiados manualmente sem prova de lineage.
