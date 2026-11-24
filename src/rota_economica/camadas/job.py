"""Job das camadas no Databricks: arquivos novos ou alterados viram silver, e a gold é refeita.

Ponto de entrada `rota-camadas`. Roda depois da coleta, no mesmo job. Só arquivos cujo SHA-256
mudou desde a última execução são relidos (tabela `controle_ingestao`).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from rota_economica.camadas import gold, silver
from rota_economica.coleta.anp import ErroFormato, Manifesto
from rota_economica.leitura.anp import ler_diesel

log = logging.getLogger("rota-camadas")


def _spark():
    from pyspark.sql import SparkSession  # só existe no Databricks

    return SparkSession.builder.getOrCreate()


def processados(spark, controle: str) -> dict[str, str]:
    if not spark.catalog.tableExists(controle):
        return {}
    return {r.caminho: r.sha256 for r in spark.table(controle).collect()}


def atualizar_silver(spark, bruto: Path, esquema: str) -> int:
    silver_t, controle = f"{esquema}.silver_diesel", f"{esquema}.controle_ingestao"
    feitos = processados(spark, controle)
    manifesto = Manifesto.abrir(bruto / "anp" / "manifesto.jsonl")
    pendentes = [r for r in manifesto.registros.values() if feitos.get(r.caminho) != r.sha256]
    log.info("silver: %d arquivos novos ou alterados", len(pendentes))
    for r in sorted(pendentes, key=lambda r: r.caminho):
        df = silver.padronizar(ler_diesel(bruto / r.caminho), r.caminho)
        problemas = silver.validar(df, r.caminho)
        if problemas:
            raise ErroFormato("; ".join(problemas))
        dados = spark.createDataFrame(df)
        if spark.catalog.tableExists(silver_t):
            spark.sql(f"DELETE FROM {silver_t} WHERE arquivo = '{r.caminho}'")
            dados.write.mode("append").saveAsTable(silver_t)
        else:
            dados.write.saveAsTable(silver_t)
        linha = spark.createDataFrame(
            [(r.caminho, r.sha256, len(df), datetime.now(UTC))],
            "caminho string, sha256 string, linhas long, processado_em timestamp",
        )
        if spark.catalog.tableExists(controle):
            spark.sql(f"DELETE FROM {controle} WHERE caminho = '{r.caminho}'")
            linha.write.mode("append").saveAsTable(controle)
        else:
            linha.write.saveAsTable(controle)
        log.info("  %s: %d linhas", r.caminho, len(df))
    return len(pendentes)


def refazer_gold(spark, esquema: str) -> None:
    silver_t = f"{esquema}.silver_diesel"
    for nivel in gold.NIVEIS:
        spark.sql(gold.sql_gold(silver_t, f"{esquema}.gold_diesel_semanal_{nivel}", nivel))
    falhas = []
    for nome, consulta in gold.sql_checagens(f"{esquema}.gold_diesel_semanal_brasil").items():
        n = spark.sql(consulta).collect()[0][0]
        if n:
            falhas.append(f"{nome}: {n}")
    if falhas:
        raise ErroFormato("checagens da gold falharam: " + "; ".join(falhas))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rota-camadas")
    p.add_argument("--bruto", required=True, type=Path, help="pasta do volume com os arquivos")
    p.add_argument("--esquema", required=True, help="catalogo.schema das tabelas")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    spark = _spark()
    atualizar_silver(spark, args.bruto, args.esquema)
    refazer_gold(spark, args.esquema)
    log.info("gold refeita e checada")
    return 0


if __name__ == "__main__":
    sys.exit(main())
