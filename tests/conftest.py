"""Cliente HTTP falso: os testes nunca acessam a internet."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from rota_economica.http import Download

BASE = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/shpc"


def pagina_anp(extras: list[str] | None = None) -> str:
    """Uma página parecida com a real: semestrais, mensais com nomes irregulares, GLP e gasolina."""
    links = [f"{BASE}/dsas/ca/ca-{a}-0{s}.csv" for a in range(2004, 2022) for s in (1, 2)]
    links += [f"{BASE}/dsas/ca/ca-2022-02.zip", f"{BASE}/dsas/ca/ca-2023-01.zip"]
    links += [f"{BASE}/dsas/ca/precos-semestrais-ca.zip"]
    links += [f"{BASE}/dsas/glp/glp-2010-01.csv", f"{BASE}/dsan/2024/precos-glp-01.csv"]
    links += [f"{BASE}/dsan/2024/precos-gasolina-etanol-01.csv"]
    links += [f"{BASE}/dsan/2024/precos-diesel-gnv-{m:02d}.csv" for m in range(1, 13)]
    links += [
        f"{BASE}/dsan/2026/10-diesel-gnv.csv",
        f"{BASE}/dsan/2026/09-dados-abertos-precos-2026-09-diesel-gnv.csv",
        f"{BASE}/dsan/2026/04-dados-abertos-precos-diesel-gnv",
        f"{BASE}/qus/ultimas-4-semanas-diesel-gnv.csv",
        f"{BASE}/qus/ultimas-4-semanas-glp.csv",
    ]
    links += extras or []
    corpo = "\n".join(f'<li><a href="{u}">arquivo</a></li>' for u in links)
    # Um link repetido, como na página real.
    return f"<html><body><ul>{corpo}<a href='{links[0]}'>de novo</a></ul></body></html>"


class ClienteFalso:
    """Responde à página e "baixa" escrevendo um conteúdo que muda quando a URL muda de versão."""

    def __init__(self, html: str, versoes: dict[str, str] | None = None) -> None:
        self.html = html
        self.versoes = versoes or {}
        self.baixados: list[str] = []
        self.jsons: dict[str, dict] = {}

    def texto(self, url: str) -> str:
        return self.html

    def json(self, url: str) -> dict:
        return self.jsons[url]

    def baixar(self, url: str, destino: Path) -> Download:
        self.baixados.append(url)
        conteudo = f"{url}|{self.versoes.get(url, 'v1')}".encode()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)
        return Download(destino, len(conteudo), hashlib.sha256(conteudo).hexdigest())


@pytest.fixture
def cliente() -> ClienteFalso:
    return ClienteFalso(pagina_anp())
