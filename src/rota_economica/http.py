"""Cliente HTTP educado para fontes públicas.

Identifica o projeto, espera um intervalo mínimo entre pedidos ao mesmo servidor e tenta de novo
só em falhas passageiras (limite de vazão, erro do servidor, conexão). Downloads vão para um
arquivo temporário e só trocam de nome no fim: uma queda no meio nunca deixa arquivo pela metade.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from rota_economica import __version__

REPOSITORIO = "https://github.com/Plee47/rota-economica"
USER_AGENT = f"rota-economica/{__version__} (projeto de portfólio; +{REPOSITORIO})"
PASSAGEIROS = {429, 500, 502, 503, 504}


class ErroHTTP(RuntimeError):
    """O servidor recusou o pedido ou não respondeu depois de todas as tentativas."""


@dataclass(frozen=True)
class Download:
    caminho: Path
    bytes: int
    sha256: str


def espera(tentativa: int, retry_after: str | None = None) -> float:
    """Segundos até a próxima tentativa: o que o servidor pedir, ou 2, 4, 8… até 60."""
    if retry_after:
        try:
            return min(120.0, float(retry_after))
        except ValueError:
            pass
    return min(60.0, 2.0 ** (tentativa + 1))


@dataclass
class ClienteHTTP:
    intervalo: float = 1.0  # segundos mínimos entre pedidos
    tentativas: int = 5
    timeout: float = 60.0  # por leitura: um download grande pode levar mais, desde que flua
    sessao: requests.Session = field(default_factory=requests.Session)
    dormir: object = time.sleep  # trocado nos testes
    _ultimo: float = 0.0

    def __post_init__(self) -> None:
        self.sessao.headers["User-Agent"] = USER_AGENT

    def _aguardar_vez(self) -> None:
        falta = self.intervalo - (time.monotonic() - self._ultimo)
        if falta > 0:
            self.dormir(falta)
        self._ultimo = time.monotonic()

    def get(self, url: str, *, stream: bool = False) -> requests.Response:
        ultimo = ""
        for tentativa in range(self.tentativas):
            self._aguardar_vez()
            retry_after = None
            try:
                r = self.sessao.get(url, timeout=self.timeout, stream=stream)
            except (requests.ConnectionError, requests.Timeout) as e:
                ultimo = type(e).__name__
            else:
                if r.status_code < 400:
                    return r
                ultimo = f"HTTP {r.status_code}"
                if r.status_code not in PASSAGEIROS:
                    raise ErroHTTP(f"{url}: {ultimo}")
                retry_after = r.headers.get("Retry-After")
                r.close()
            if tentativa < self.tentativas - 1:
                self.dormir(espera(tentativa, retry_after))
        raise ErroHTTP(f"{url}: sem resposta depois de {self.tentativas} tentativas ({ultimo})")

    def texto(self, url: str) -> str:
        r = self.get(url)
        r.encoding = r.encoding or "utf-8"
        return r.text

    def json(self, url: str) -> dict:
        return self.get(url).json()

    def baixar(self, url: str, destino: Path) -> Download:
        """Baixa em blocos para `destino`, de forma atômica, e devolve tamanho e SHA-256."""
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_name(destino.name + ".parcial")
        resumo = hashlib.sha256()
        total = 0
        with self.get(url, stream=True) as r, parcial.open("wb") as f:
            for bloco in r.iter_content(chunk_size=1 << 20):
                f.write(bloco)
                resumo.update(bloco)
                total += len(bloco)
        if total == 0:
            parcial.unlink(missing_ok=True)
            raise ErroHTTP(f"{url}: arquivo vazio")
        parcial.replace(destino)
        return Download(destino, total, resumo.hexdigest())
