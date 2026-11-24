"""Provedores de rota atrás de uma interface única, com cache em disco.

O primeiro é o servidor público do OSRM: gratuito, para uso razoável e não comercial, no máximo um
pedido por segundo e sem garantia de estar no ar (ADR 0002). Trocar de provedor é escrever outra
classe com o mesmo método `rota`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from rota_economica.coleta.anp import ErroFormato
from rota_economica.http import ClienteHTTP, ErroHTTP
from rota_economica.rota.geo import Ponto


@dataclass(frozen=True)
class Rota:
    distancia_km: float
    duracao_h: float
    trajeto: list[Ponto]  # (longitude, latitude) do início ao fim
    provedor: str


class ProvedorRota(Protocol):
    def rota(self, pontos: list[Ponto]) -> Rota: ...


@dataclass
class OSRM:
    cliente: ClienteHTTP
    base: str = "https://router.project-osrm.org"
    perfil: str = "driving"

    def url(self, pontos: list[Ponto]) -> str:
        coords = ";".join(f"{lon:.5f},{lat:.5f}" for lon, lat in pontos)
        return (
            f"{self.base}/route/v1/{self.perfil}/{coords}"
            "?overview=full&geometries=geojson&steps=false"
        )

    def rota(self, pontos: list[Ponto]) -> Rota:
        if len(pontos) < 2:
            raise ValueError("uma rota precisa de origem e destino")
        dados = self.cliente.json(self.url(pontos))
        if dados.get("code") != "Ok" or not dados.get("routes"):
            raise ErroFormato(f"o OSRM não achou rota: {dados.get('code')} {dados.get('message')}")
        r = dados["routes"][0]
        trajeto = [(float(x), float(y)) for x, y in r["geometry"]["coordinates"]]
        return Rota(r["distance"] / 1000, r["duration"] / 3600, trajeto, "osrm")


FOSSGIS = "https://routing.openstreetmap.de/routed-car"  # segundo servidor público, mesma API


@dataclass
class Encadeado:
    """Tenta cada provedor na ordem: se um servidor público cair, o seguinte responde."""

    provedores: list[ProvedorRota]

    def rota(self, pontos: list[Ponto]) -> Rota:
        erros = []
        for p in self.provedores:
            try:
                return p.rota(pontos)
            except (ErroHTTP, ErroFormato) as e:
                erros.append(f"{type(p).__name__}: {e}")
        raise ErroHTTP("nenhum serviço de rotas respondeu (" + "; ".join(erros) + ")")


def provedor_padrao(cliente: ClienteHTTP, cache: Path) -> ComCache:
    """OSRM público, com o servidor da FOSSGIS como reserva, e cache em disco."""
    return ComCache(Encadeado([OSRM(cliente), OSRM(cliente, base=FOSSGIS)]), cache)


@dataclass
class ComCache:
    """Guarda cada rota calculada: a mesma viagem não consulta o provedor de novo."""

    provedor: ProvedorRota
    pasta: Path

    def _arquivo(self, pontos: list[Ponto]) -> Path:
        chave = ";".join(f"{lon:.5f},{lat:.5f}" for lon, lat in pontos)
        nome = type(self.provedor).__name__.lower()
        return self.pasta / f"{nome}-{hashlib.sha256(chave.encode()).hexdigest()[:20]}.json"

    def rota(self, pontos: list[Ponto]) -> Rota:
        arquivo = self._arquivo(pontos)
        if arquivo.exists():
            d = json.loads(arquivo.read_text(encoding="utf-8"))
            return Rota(d["distancia_km"], d["duracao_h"], [tuple(p) for p in d["trajeto"]],
                        d["provedor"])  # fmt: skip
        r = self.provedor.rota(pontos)
        arquivo.parent.mkdir(parents=True, exist_ok=True)
        arquivo.write_text(json.dumps(asdict(r)), encoding="utf-8")
        return r
