"""Cliente da API de dados abertos da ANTT (CKAN), usado para as praças de pedágio."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rota_economica.coleta.anp import ErroFormato
from rota_economica.http import ClienteHTTP, Download

API = "https://dados.antt.gov.br/api/3/action"
PRACAS_DE_PEDAGIO = "praca-de-pedagio"


@dataclass(frozen=True)
class Recurso:
    nome: str
    formato: str
    url: str
    atualizado_em: str


def recursos(cliente: ClienteHTTP, conjunto: str) -> list[Recurso]:
    dados = cliente.json(f"{API}/package_show?id={conjunto}")
    if not dados.get("success"):
        raise ErroFormato(f"a ANTT não devolveu o conjunto {conjunto!r}")
    return [
        Recurso(
            nome=r.get("name") or "",
            formato=(r.get("format") or "").upper(),
            url=r.get("url") or "",
            atualizado_em=r.get("last_modified") or r.get("created") or "",
        )
        for r in dados["result"].get("resources", [])
    ]


def baixar_csv(cliente: ClienteHTTP, conjunto: str, destino: Path) -> Download:
    """Baixa o recurso CSV de dados do conjunto (o dicionário em PDF e o mapa ficam de fora)."""
    csvs = [r for r in recursos(cliente, conjunto) if r.formato == "CSV" and r.url]
    if not csvs:
        raise ErroFormato(f"o conjunto {conjunto!r} da ANTT não tem CSV")
    return cliente.baixar(csvs[0].url, destino / "antt" / f"{conjunto}.csv")
