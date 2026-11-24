# 0002 · Fontes de dados e como cada uma é coletada

**Status:** aceita · 2026-11-24

## Contexto

O projeto precisa de preços de diesel, praças de pedágio, piso mínimo de frete e rotas. Cada fonte
pública entrega os dados de um jeito, e só uma delas tem API.

## Decisão

| Fonte | Acesso | Coleta |
|---|---|---|
| Preços da ANP (por posto, produto e data) | Arquivos numa página de dados abertos; sem API | O coletor lê a página e classifica os links pelo caminho, porque os nomes mudam sem aviso |
| Praças de pedágio da ANTT | API CKAN (`/api/3/action/package_show`) | Pela API, escolhendo o recurso CSV |
| Piso mínimo de frete da ANTT | Só em resolução publicada | Tabela escrita à mão, com a resolução citada e testes |
| Rotas | OSRM, servidor público | Atrás de uma interface de provedor, com cache e no máximo 1 pedido por segundo |

**Quais arquivos da ANP:** semestrais de 2004 a 2022 (todos os combustíveis), mensais de diesel
de 2023 em diante (bem menores) e o arquivo das últimas 4 semanas. Os semestrais de 2023 em diante
ficam de fora porque repetem os mensais.

**Boa conduta com as fontes:** o cliente se identifica com o nome do projeto e o link do
repositório, espera pelo menos 1 segundo entre pedidos e só tenta de novo em falhas passageiras.
Arquivos históricos não são baixados de novo. A página da ANP recusa pedidos só de cabeçalho
(HEAD), então o coletor usa GET.

## Consequências

- A granularidade dos preços é a da pesquisa da ANP: uma amostra de postos em cerca de 400
  municípios, não todos os postos. O app diz isso ao recomendar onde abastecer.
- Se a ANP mudar a página, a coleta para com erro, em vez de gravar dado errado: a descoberta
  exige um número mínimo de arquivos e a leitura exige as colunas esperadas.
- O servidor público do OSRM é para uso razoável e não comercial, sem garantia. A interface de
  provedor permite trocar por outro sem mudar o resto do código.
