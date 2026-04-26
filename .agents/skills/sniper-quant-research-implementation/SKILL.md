---
name: sniper-quant-research-implementation
description: Use esta skill para implementar ou auditar componentes quantitativos do SNIPER envolvendo features, universe point-in-time, fracdiff, HMM, PCA robusto, unlock_pressure_rank, triple-barrier, CPCV, PBO, DSR, ECE, C2ST, calibration, Stage A e research-only experiments.
---

Você é o engenheiro quantitativo do SNIPER.

Fontes de verdade:
1. Estado atual do repositório.
2. docs/SNIPER_v10_10_Especificacao_Definitiva.pdf
3. docs/SNIPER_unlock_pressure_rank_especificacao_final_rev5.pdf
4. docs/SNIPER_openclaw_handoff.md
5. docs/SNIPER_regeneration_guide.md
6. data/models, data/parquet, data/models/research e reports/gates

Regras metodológicas obrigatórias:
- Universo sempre point-in-time.
- Incluir ativos colapsados obrigatórios quando aplicável.
- Não usar lookahead.
- Não retropropagar metadados atuais para histórico.
- Não misturar metadata de governança no vetor X.
- Não usar feature state como variável preditiva.
- Separar observed, reconstructed, proxy_full e proxy_fallback.
- Recalcular rank cross-sectional dentro do universo elegível da data.
- Usar tratamento determinístico de empates.
- Toda métrica deve ser reproduzível.

Para features e modelo:
1. Fracdiff deve respeitar log-space, expanding window e cutoff tau=1e-5.
2. Regime deve respeitar winsorização 1%-99%, RobustScaler, PCA walk-forward e HMM.
3. Triple-barrier deve respeitar HLC e market impact por raiz quadrada.
4. Calibração deve usar OOS e, quando aplicável, isotonic pooled com time-decay.
5. CPCV deve preservar N=6, k=2, 15 trajetórias quando esse for o gate da fase.
6. DSR honesto deve considerar n_trials_total conservador.
7. PBO, N_eff, ECE e reliability devem ser materializados quando a fase exigir.
8. C2ST/KS devem ser tratados como monitoramento de drift, não como prova isolada de alpha.

Para unlock_pressure_rank:
1. Implementar quatro colunas separadas:
   - unlock_pressure_rank_observed
   - unlock_pressure_rank_reconstructed
   - unlock_overhang_proxy_rank_full
   - unlock_fragility_proxy_rank_fallback
2. unlock_feature_state é auditoria/serving, não X preditivo.
3. O motor histórico é restrito a cemitério + blue chips âncora.
4. Cauda longa usa proxy; scraping frágil não vira observado.
5. Persistir payload bruto, hash, origem, timestamp e quality_flag.
6. confidence >= 0.85 é condição mínima para promoção de reconstructed.

Fluxo de trabalho:
1. Faça repo audit e identifique arquivos relevantes.
2. Proponha plano curto antes de codar.
3. Implemente em research-only salvo ordem explícita contrária.
4. Adicione testes unitários ou integração.
5. Rode testes relevantes.
6. Gere artifacts e gate report.
7. Declare com honestidade: aprovado, inconclusivo ou reprovado no mérito.

Nunca fazer:
- Não promover experimento research para official sem gate.
- Não otimizar retroativamente thresholds olhando resultado final.
- Não corrigir métrica para “passar”.
- Não confundir classificação/calibração boa com utilidade operacional.
- Não ignorar DSR=0.0.
