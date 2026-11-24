"""Municípios do IBGE com o centro de cada um, pela API oficial (dois pedidos, depois cache).

A chave de junção com a ANP é o nome sem acento, em maiúsculas, mais a UF: a ANP publica
"SAO PAULO", o IBGE "São Paulo".
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import pandas as pd

from rota_economica.coleta.anp import ErroFormato
from rota_economica.http import ClienteHTTP
from rota_economica.rota.geo import centroide

API = "https://servicodados.ibge.gov.br/api"
LISTA = f"{API}/v1/localidades/municipios?view=nivelado"
MALHA = (
    f"{API}/v3/malhas/paises/BR?intrarregiao=municipio&qualidade=minima"
    "&formato=application/vnd.geo+json"
)
MINIMO_MUNICIPIOS = 5000


def chave_nome(nome: str) -> str:
    """'São Paulo' e 'SAO PAULO' viram 'SAO PAULO'; apóstrofo e hífen viram espaço."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", sem_acento.upper())).strip()


def montar(lista: list[dict], malha: dict) -> pd.DataFrame:
    """Junta a lista (nome e UF) com a malha (contorno) pelo código do município."""
    centros = {}
    for f in malha["features"]:
        centros[str(f["properties"]["codarea"])] = centroide(f["geometry"])
    linhas = []
    for m in lista:
        codigo = str(m["municipio-id"])
        if codigo in centros:
            lon, lat = centros[codigo]
            nome, uf = m["municipio-nome"], m["UF-sigla"]
            linhas.append((codigo, nome, uf, chave_nome(nome), lon, lat))
    df = pd.DataFrame(linhas, columns=["codigo", "nome", "uf", "chave", "lon", "lat"])
    if len(df) < MINIMO_MUNICIPIOS:
        raise ErroFormato(f"o IBGE devolveu só {len(df)} municípios com contorno")
    return df


def carregar(cliente: ClienteHTTP, cache: Path) -> pd.DataFrame:
    """Municípios com centro; a API só é chamada se o cache não existir."""
    arquivo = cache / "municipios_ibge.json"
    if not arquivo.exists():
        df = montar(cliente.json(LISTA), cliente.json(MALHA))
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(df.to_json(orient="records", force_ascii=False), encoding="utf-8")
    return pd.DataFrame(json.loads(arquivo.read_text(encoding="utf-8")), dtype=object).astype(
        {"lon": float, "lat": float}
    )


def localizar(municipios: pd.DataFrame, nome: str, uf: str) -> pd.Series:
    """O município pelo nome e UF, tolerando acento e caixa."""
    achados = municipios[(municipios["chave"] == chave_nome(nome)) & (municipios["uf"] == uf)]
    if achados.empty:
        raise KeyError(f"município não encontrado: {nome} - {uf}")
    return achados.iloc[0]
