# 0003 · Dados brutos ficam fora do Git

**Status:** aceita · 2026-11-24

## Contexto

O histórico da ANP tem cerca de 3 GB, com um preço por posto, incluindo nome e CNPJ das revendas.
São dados públicos, mas um repositório não é lugar para gigabytes de dado bruto.

## Decisão

- Os arquivos baixados ficam no volume `bruto` do Databricks, com um manifesto de tamanho e SHA-256.
- No repositório entram código, documentos e só fixtures pequenas de teste em `tests/fixtures/`.
- O **guardião** (`python -m rota_economica.guardiao`) roda no pre-commit e no CI e barra arquivos
  de dados fora das fixtures ou fixtures maiores que 200 KB.
- O site mostra preços agregados por município, estado e região.

## Consequências

Quem clona o repositório reconstrói os dados rodando a coleta. O repositório continua leve, e
nenhum dado bruto é publicado por engano.
