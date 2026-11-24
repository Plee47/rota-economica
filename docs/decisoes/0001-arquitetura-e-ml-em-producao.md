# 0001 · Arquitetura: Databricks com ML em produção, site no Streamlit

**Status:** aceita · 2026-11-24

## Contexto

O projeto prevê o preço do diesel por região e usa essa previsão para recomendar onde e quando
abastecer numa rota. O objetivo de portfólio é mostrar um modelo **em produção**, não só treinado
num notebook: coleta automática, retreino, registro, inferência e monitoramento.

O plano gratuito do Databricks limita o que roda nele: Model Serving só com CPU e com número
limitado de endpoints, até 5 tarefas de job ao mesmo tempo, Apps que ficam no ar só 24 horas
depois de iniciados, e nenhum SLA. Um site público não pode depender disso.

## Decisão

| Peça | Onde | Como |
|---|---|---|
| Coleta | Job semanal no Databricks | Pacote Python deste repositório, em wheel, chamado pelo bundle |
| Dados | Unity Catalog | Volume `bruto` com os arquivos; tabelas Delta bronze, silver e gold |
| Treino | Job no Databricks | Campeão e desafiante, avaliados em semanas que o modelo não viu |
| Registro | MLflow com Unity Catalog | Versões do modelo e o apelido `campeao` |
| Inferência em lote | Job no Databricks | Previsão das próximas semanas por região, gravada numa tabela |
| Inferência em tempo real | Model Serving | Endpoint opcional, usado pelo site quando está no ar |
| Monitoramento | Job no Databricks | Compara previsão e preço real toda semana e alerta se o erro subir |
| Site | Streamlit Community Cloud | Lê as previsões exportadas; usa o endpoint quando disponível |

Todo o código é um pacote Python testado no CI. Os jobs chamam funções do pacote, e não lógica
escrita só em notebook.

## Consequências

- O site continua funcionando se o endpoint ou o workspace estiverem fora do ar, com a última
  previsão exportada.
- O deploy depende de credencial do Databricks no CI, guardada como segredo do GitHub.
- Se o Databricks apagar uma conta gratuita inativa, o histórico pode ser reconstruído do zero
  pela coleta, porque ela é idempotente e as fontes são públicas.
