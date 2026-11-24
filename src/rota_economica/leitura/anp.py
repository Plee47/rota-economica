"""Lê um arquivo de preços da ANP (CSV ou ZIP) e devolve só o diesel, com tipos certos.

O formato é o mesmo de 2004 até hoje: um preço por posto, produto e data, separado por ";", com
vírgula decimal. A codificação muda (UTF-8 com BOM ou Latin-1), então as duas são tentadas.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

from rota_economica.coleta.anp import ErroFormato

COLUNAS = {
    "Regiao - Sigla": "regiao",
    "Estado - Sigla": "uf",
    "Municipio": "municipio",
    "Revenda": "revenda",
    "CNPJ da Revenda": "cnpj",
    "Produto": "produto",
    "Data da Coleta": "data",
    "Valor de Venda": "preco",
    "Unidade de Medida": "unidade",
    "Bandeira": "bandeira",
}
PRECO_MINIMO, PRECO_MAXIMO = 0.5, 20.0  # R$/litro: fora disso é erro de digitação na fonte


def _bytes_do_csv(caminho: Path) -> bytes:
    if caminho.suffix.lower() != ".zip":
        return caminho.read_bytes()
    with zipfile.ZipFile(caminho) as z:
        csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise ErroFormato(f"{caminho.name}: esperava 1 CSV dentro do ZIP, achei {len(csvs)}")
        return z.read(csvs[0])


def _texto(dados: bytes) -> str:
    try:
        return dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dados.decode("latin-1")


def ler_diesel(caminho: Path) -> pd.DataFrame:
    """Linhas de diesel do arquivo, com `data` em datetime e `preco` em float (R$/litro)."""
    bruto = pd.read_csv(io.StringIO(_texto(_bytes_do_csv(caminho))), sep=";", dtype=str)
    bruto.columns = [c.strip() for c in bruto.columns]
    faltam = set(COLUNAS) - set(bruto.columns)
    if faltam:
        raise ErroFormato(f"{caminho.name}: faltam as colunas {sorted(faltam)}")
    df = bruto[list(COLUNAS)].rename(columns=COLUNAS)
    for col in ("regiao", "uf", "municipio", "revenda", "cnpj", "produto", "unidade", "bandeira"):
        df[col] = df[col].str.strip()
    df["produto"] = df["produto"].str.upper()
    df = df[df["produto"].str.contains("DIESEL", na=False)].copy()
    df["data"] = pd.to_datetime(df["data"].str.strip(), format="%d/%m/%Y", errors="coerce")
    df["preco"] = pd.to_numeric(df["preco"].str.replace(",", ".", regex=False), errors="coerce")
    df = df.dropna(subset=["data", "preco"])
    df = df[(df["preco"] >= PRECO_MINIMO) & (df["preco"] <= PRECO_MAXIMO)]
    df["arquivo"] = caminho.name
    return df.reset_index(drop=True)
