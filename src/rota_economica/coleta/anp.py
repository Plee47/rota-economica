"""Coletor dos preços de combustíveis da ANP.

A ANP não tem API: publica arquivos numa página de dados abertos, toda semana, com nomes que mudam
sem aviso ("10-diesel-gnv.csv", "09-dados-abertos-precos-2026-09-diesel-gnv.csv", um mês sem
extensão). Por isso o coletor lê a página, classifica cada link pelo caminho e baixa só o que
interessa ao diesel:

* **Semestrais** (`dsas/ca/`), de 2004 a 2022: todos os combustíveis, por posto.
* **Mensais de diesel** (`dsan/<ano>/`), de 2023 em diante: só diesel e GNV, bem menores.
  Os semestrais desse período ficam de fora porque repetem esses dados.
* **Últimas 4 semanas** (`qus/`): o dado mais recente, baixado de novo a cada execução.

Um manifesto guarda o que já foi baixado (tamanho e SHA-256). Arquivos históricos não são baixados
de novo; o mês corrente e as últimas semanas são, porque a ANP ainda os atualiza.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from rota_economica.http import ClienteHTTP

PAGINA = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "serie-historica-de-precos-de-combustiveis"
)
INICIO_MENSAIS = 2023  # a partir deste ano, os arquivos mensais de diesel substituem os semestrais
MINIMO_ARQUIVOS = 30  # menos que isso na página: o formato mudou e a coleta para

Tipo = Literal["semestral", "mensal", "recente"]

_RE_SEMESTRAL = re.compile(r"/dsas/ca/[^/]+$")
_RE_SEMESTRE = re.compile(r"ca-(\d{4})-0?([12])\.(?:csv|zip)$", re.I)
_RE_MENSAL = re.compile(r"/dsan/(\d{4})/([^/]+)$")
_RE_MES = re.compile(r"^(\d{2})-|-(\d{2})(?:\.csv)?$")
_RE_RECENTE = re.compile(r"/qus/[^/]*diesel[^/]*$", re.I)


class ErroFormato(RuntimeError):
    """A página ou o arquivo da ANP não tem o formato esperado: melhor parar que gravar errado."""


@dataclass(frozen=True)
class ArquivoANP:
    url: str
    tipo: Tipo
    ano: int | None
    periodo: int | None  # semestre (1–2) ou mês (1–12)

    @property
    def nome(self) -> str:
        nome = self.url.rsplit("/", 1)[-1]
        return nome if "." in nome else nome + ".csv"  # há mês publicado sem extensão

    @property
    def caminho(self) -> str:
        """Caminho relativo e estável no volume de dados brutos."""
        pasta = f"{self.tipo}/{self.ano}" if self.ano else self.tipo
        return f"anp/{pasta}/{self.nome}"


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href.strip())


def classificar(url: str) -> ArquivoANP | None:
    """Tipo, ano e período de um link da página, ou None se não for de diesel."""
    if _RE_SEMESTRAL.search(url) and re.search(r"\.(csv|zip)$", url, re.I):
        m = _RE_SEMESTRE.search(url)
        return ArquivoANP(url, "semestral", int(m[1]) if m else None, int(m[2]) if m else None)
    m = _RE_MENSAL.search(url)
    if m and "diesel" in m[2].lower():
        mes = _RE_MES.search(m[2])
        numero = int(mes[1] or mes[2]) if mes else None
        return ArquivoANP(url, "mensal", int(m[1]), numero if numero and numero <= 12 else None)
    if _RE_RECENTE.search(url):
        return ArquivoANP(url, "recente", None, None)
    return None


def descobrir(html: str) -> list[ArquivoANP]:
    """Todos os arquivos de diesel citados na página, sem repetição, na ordem da página."""
    leitor = _Links()
    leitor.feed(html)
    vistos: dict[str, ArquivoANP] = {}
    for url in leitor.links:
        arquivo = classificar(url)
        if arquivo and url not in vistos:
            vistos[url] = arquivo
    arquivos = list(vistos.values())
    if len(arquivos) < MINIMO_ARQUIVOS:
        raise ErroFormato(
            f"a página da ANP listou só {len(arquivos)} arquivos de diesel; o formato mudou?"
        )
    return arquivos


def selecionar(arquivos: list[ArquivoANP], desde: int = 2004) -> list[ArquivoANP]:
    """O conjunto mínimo que cobre o diesel de `desde` até hoje, sem baixar dado repetido."""
    escolhidos = []
    for a in arquivos:
        if a.tipo == "recente":
            escolhidos.append(a)
        elif a.tipo == "mensal":
            if a.ano >= max(desde, INICIO_MENSAIS):
                escolhidos.append(a)
        # Semestral sem ano no nome (ex.: "precos-semestrais-ca.zip"): entra, e o período sai
        # das datas do próprio arquivo na leitura.
        elif a.ano is None or desde <= a.ano < INICIO_MENSAIS:
            escolhidos.append(a)
    return escolhidos


def ainda_muda(a: ArquivoANP, hoje: date) -> bool:
    """A ANP ainda pode atualizar este arquivo? Então ele é baixado de novo a cada coleta."""
    if a.tipo == "recente":
        return True
    if a.tipo == "mensal" and a.ano is not None and a.periodo is not None:
        # O mês corrente e o anterior ainda recebem correções.
        anterior = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
        return (a.ano, a.periodo) >= anterior
    return a.tipo == "semestral" and a.ano is None


# --------------------------------------------------------------------------- manifesto
@dataclass
class Registro:
    url: str
    caminho: str
    bytes: int
    sha256: str
    baixado_em: str
    # Tamanho anunciado pelo servidor e conferido. Registros antigos não têm: foram baixados antes
    # da conferência e podem estar cortados, então são baixados de novo uma vez.
    esperado: int | None = None

    @property
    def conferido(self) -> bool:
        return self.esperado is not None and self.bytes == self.esperado


@dataclass
class Manifesto:
    caminho: Path
    registros: dict[str, Registro]

    @classmethod
    def abrir(cls, caminho: Path) -> Manifesto:
        registros = {}
        if caminho.exists():
            for linha in caminho.read_text(encoding="utf-8").splitlines():
                if linha.strip():
                    r = Registro(**json.loads(linha))
                    registros[r.url] = r
        return cls(caminho, registros)

    def gravar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        linhas = [json.dumps(asdict(r), ensure_ascii=False) for r in self.registros.values()]
        temporario = self.caminho.with_name(self.caminho.name + ".parcial")
        temporario.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        temporario.replace(self.caminho)


@dataclass
class Relatorio:
    novos: list[str]
    atualizados: list[str]
    iguais: list[str]
    pulados: list[str]

    def resumo(self) -> str:
        return (
            f"{len(self.novos)} novos, {len(self.atualizados)} atualizados, "
            f"{len(self.iguais)} sem mudança, {len(self.pulados)} já guardados"
        )


def coletar(
    cliente: ClienteHTTP,
    destino: Path,
    *,
    desde: int = 2004,
    hoje: date | None = None,
    limite: int | None = None,
) -> Relatorio:
    """Baixa para `destino` o que falta e o que ainda muda. Pode rodar quantas vezes quiser."""
    hoje = hoje or date.today()
    manifesto = Manifesto.abrir(destino / "anp" / "manifesto.jsonl")
    arquivos = selecionar(descobrir(cliente.texto(PAGINA)), desde)
    rel = Relatorio([], [], [], [])
    for a in arquivos[:limite] if limite else arquivos:
        anterior = manifesto.registros.get(a.url)
        guardado = anterior and anterior.conferido and (destino / anterior.caminho).exists()
        if guardado and not ainda_muda(a, hoje):
            rel.pulados.append(a.caminho)
            continue
        d = cliente.baixar(a.url, destino / a.caminho)
        agora = datetime.now(UTC).isoformat(timespec="seconds")
        manifesto.registros[a.url] = Registro(
            a.url, a.caminho, d.bytes, d.sha256, agora, d.esperado
        )
        if anterior is None:
            rel.novos.append(a.caminho)
        elif anterior.sha256 != d.sha256:
            rel.atualizados.append(a.caminho)
        else:
            rel.iguais.append(a.caminho)
        manifesto.gravar()  # a cada arquivo: uma queda no meio não perde o que já veio
    return rel
