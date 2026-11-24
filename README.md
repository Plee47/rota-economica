# Rota Econômica

Informe a rota e o caminhão: o projeto calcula o custo da viagem e recomenda **onde e quando
abastecer para gastar menos**, com a economia em reais. Por trás, um modelo que prevê o preço do
diesel por região roda em produção no Databricks.

> **Status:** fase 1 em andamento, coleta automática dos dados.

## Como vai funcionar

1. **Rota:** distância, tempo e os municípios por onde ela passa.
2. **Custo:** diesel, pedágios e custo por quilômetro, comparados com o piso mínimo de frete.
3. **Economia:** onde abastecer e quanto colocar em cada parada, se vale esperar a próxima semana
   e se uma rota alternativa sai mais barata.

## Fases

| Fase | Entrega | Status |
|---|---|---|
| 1 | Coleta automática da ANP e da ANTT, job semanal no Databricks | Em andamento |
| 2 | Camadas bronze, silver e gold do diesel, com testes de qualidade | — |
| 3 | Previsão do diesel por região: campeão e desafiante, MLflow, previsão em lote | — |
| 4 | Rota, pedágios e custo da viagem | — |
| 5 | Otimização de onde e quanto abastecer | — |
| 6 | Site com mapa e monitoramento do modelo | — |
| 7 | Trânsito e horário de saída | — |

## Fontes

| Fonte | Acesso |
|---|---|
| Preços de combustíveis da ANP, desde 2004 | Página de dados abertos, sem API: coletor automático ([ADR 0002](docs/decisoes/0002-fontes-de-dados.md)) |
| Praças de pedágio da ANTT | API CKAN |
| Rotas | OSRM, servidor público |

## Rodando localmente

```bash
python -m venv .venv && .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m rota_economica --destino dados --desde 2024 --limite 3   # coleta de teste
```

## Decisões

- [0001 · Arquitetura e ML em produção](docs/decisoes/0001-arquitetura-e-ml-em-producao.md)
- [0002 · Fontes de dados](docs/decisoes/0002-fontes-de-dados.md)
- [0003 · Dados brutos fora do Git](docs/decisoes/0003-dados-fora-do-git.md)

Dados: Agência Nacional do Petróleo, Gás Natural e Biocombustíveis (ANP) e Agência Nacional de
Transportes Terrestres (ANTT), dados abertos. Rotas: © colaboradores do OpenStreetMap, via OSRM.
