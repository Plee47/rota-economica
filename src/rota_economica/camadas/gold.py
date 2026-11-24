"""Gold: preço semanal do diesel por município, estado, região e Brasil.

O SQL usa só o que o Spark SQL e o DuckDB entendem igual: os testes rodam as mesmas consultas no
DuckDB, localmente, e o job roda no Databricks.
"""

from __future__ import annotations

NIVEIS = {
    "municipio": ["regiao", "uf", "municipio"],
    "uf": ["regiao", "uf"],
    "regiao": ["regiao"],
    "brasil": [],
}
MINIMO_POSTOS_BRASIL = 50  # menos que isso numa semana recente: a coleta veio incompleta


def sql_unico(silver: str) -> str:
    """Um preço por posto, produto e dia: se vier em dois arquivos, vale o de maior prioridade."""
    return f"""
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY cnpj, produto, data ORDER BY prioridade DESC, arquivo DESC
            ) AS ordem
            FROM {silver}
        ) WHERE ordem = 1
    """


def sql_gold(silver: str, destino: str, nivel: str) -> str:
    grupos = NIVEIS[nivel]
    colunas = ", ".join([*grupos, "tipo_diesel", "semana"])
    return f"""
        CREATE OR REPLACE TABLE {destino} AS
        SELECT {colunas},
            median(preco) AS preco_mediano,
            avg(preco) AS preco_medio,
            min(preco) AS preco_minimo,
            max(preco) AS preco_maximo,
            count(DISTINCT cnpj) AS postos,
            count(*) AS coletas
        FROM ({sql_unico(silver)})
        GROUP BY {colunas}
    """


def sql_checagens(brasil: str) -> dict[str, str]:
    """Cada consulta devolve o número de violações; o job falha se alguma passar de zero."""
    return {
        "semana com poucos postos no Brasil (desde 2013)": f"""
            SELECT count(*) FROM {brasil}
            WHERE semana >= DATE '2013-01-06' AND postos < {MINIMO_POSTOS_BRASIL}
        """,
        "preço mediano fora de 0,5 a 20 R$/L": f"""
            SELECT count(*) FROM {brasil}
            WHERE preco_mediano < 0.5 OR preco_mediano > 20
        """,
    }
