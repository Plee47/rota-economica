# 0004 · O site lê agregados exportados, não o Databricks

**Status:** aceita · 2026-11-24

## Contexto

O site é público e roda no Streamlit Community Cloud. Ele precisa do preço do diesel por
município, do histórico por estado, das previsões e das métricas do modelo. Ler direto do
Databricks exigiria um token no site, um warehouse ligado a cada visita e a conta gratuita no ar.

## Decisão

1. **Exportação no job semanal:** a etapa `exportar` grava no volume arquivos pequenos e agregados:
   preço por município (últimas 8 semanas), histórico por estado, a última previsão, municípios do
   IBGE e praças de pedágio, em Parquet. O job da previsão grava `modelo.json` com as métricas.
2. **Sincronização:** `scripts/sincronizar_site.py` traz esses arquivos para `dados/site/`, que vai
   para o repositório. O guardião libera só essa pasta, só Parquet e no máximo 5 MB por arquivo.
3. **Rotas na hora:** o site chama o OSRM público, com o servidor da FOSSGIS como reserva, e guarda
   cada rota calculada.

## Consequências

- O site funciona mesmo com o Databricks fora do ar, com os últimos dados sincronizados, e avisa
  quando eles têm mais de 14 dias.
- Só entram agregados: o preço por posto, com nome e CNPJ da revenda, continua só no Databricks.
- A sincronização é um passo manual hoje. Ela pode virar uma GitHub Action semanal com um token do
  Databricks guardado como segredo do repositório.
