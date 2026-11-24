"""Exportação para o site: arquivos pequenos e agregados, gravados no volume pelo job semanal.

O site não acessa o Databricks: ele lê estes arquivos, trazidos para `dados/site/` pelo script
`scripts/sincronizar_site.py` (ADR 0004). Só entram agregados de dados públicos, nunca o preço por
posto.
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from pathlib import Path

import pandas as pd

from rota_economica.http import ClienteHTTP
from rota_economica.rota import municipios

SEMANAS_MUNICIPIO = 8  # o site usa o preço mais recente de cada município; 8 semanas dão folga
ARQUIVOS = (
    "precos_municipio.parquet",
    "precos_uf.parquet",
    "previsoes.parquet",
    "municipios.parquet",
    "pracas.parquet",
    "modelo.json",
)
log = logging.getLogger("rota-exportar")


def pracas_de_pedagio(csv_bruto: bytes) -> pd.DataFrame:
    """Praças da ANTT com nome, rodovia, UF, situação e coordenadas (lon, lat)."""
    try:
        texto = csv_bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = csv_bruto.decode("latin-1")
    df = pd.read_csv(io.StringIO(texto), sep=";", dtype=str)
    saida = pd.DataFrame(
        {
            "praca": df["praca_de_pedagio"].str.strip(),
            "rodovia": df["rodovia"].str.strip(),
            "uf": df["uf"].str.strip(),
            "concessionaria": df["concessionaria"].str.strip(),
            "situacao": df["situacao"].str.strip(),
            "lat": pd.to_numeric(df["latitude"].str.replace(",", "."), errors="coerce"),
            "lon": pd.to_numeric(df["longitude"].str.replace(",", "."), errors="coerce"),
        }
    )
    return saida.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def recortar_municipios(gold_municipio: pd.DataFrame) -> pd.DataFrame:
    """As últimas semanas de cada município: é o que o site usa, e mantém o arquivo pequeno."""
    g = gold_municipio.copy()
    g["semana"] = pd.to_datetime(g["semana"])
    limite = g["semana"].max() - pd.Timedelta(weeks=SEMANAS_MUNICIPIO - 1)
    return g[g["semana"] >= limite].reset_index(drop=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rota-exportar")
    p.add_argument("--esquema", required=True)
    p.add_argument("--bruto", required=True, type=Path)
    p.add_argument("--destino", required=True, type=Path)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()
    args.destino.mkdir(parents=True, exist_ok=True)

    def tabela(nome: str) -> pd.DataFrame | None:
        completo = f"{args.esquema}.{nome}"
        if not spark.catalog.tableExists(completo):
            log.warning("%s não existe: o arquivo dela fica de fora desta exportação", completo)
            return None
        return spark.table(completo).toPandas()

    # Cada arquivo é independente: o que faltar não impede os outros (o site funciona sem eles).
    gold_municipio = tabela("gold_diesel_semanal_municipio")
    if gold_municipio is not None:
        recortar_municipios(gold_municipio).to_parquet(
            args.destino / "precos_municipio.parquet", index=False
        )
    gold_uf = tabela("gold_diesel_semanal_uf")
    if gold_uf is not None:
        gold_uf.to_parquet(args.destino / "precos_uf.parquet", index=False)
    previsoes = tabela("previsoes_diesel")
    if previsoes is not None and not previsoes.empty:
        ultima = previsoes["semana_origem"].max()
        previsoes[previsoes["semana_origem"] == ultima].to_parquet(
            args.destino / "previsoes.parquet", index=False
        )
    municipios.carregar(ClienteHTTP(), args.bruto / "ibge").to_parquet(
        args.destino / "municipios.parquet", index=False
    )
    csv = args.bruto / "antt" / "praca-de-pedagio.csv"
    if csv.exists():
        pracas_de_pedagio(csv.read_bytes()).to_parquet(args.destino / "pracas.parquet", index=False)
    # modelo.json é gravado pelo job da previsão, que tem as métricas.
    for nome in ARQUIVOS:
        caminho = args.destino / nome
        log.info("%s: %s", nome, f"{caminho.stat().st_size} bytes" if caminho.exists() else "falta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
