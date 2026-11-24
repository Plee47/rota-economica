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
MAX_RETOMADAS = 1000  # teto de segurança para um download que anda muito devagar


class ErroHTTP(RuntimeError):
    """O servidor recusou o pedido ou não respondeu depois de todas as tentativas."""


@dataclass(frozen=True)
class Download:
    caminho: Path
    bytes: int
    sha256: str
    esperado: int | None = None  # tamanho anunciado pelo servidor, conferido no fim


def tamanho_total(r: requests.Response) -> int | None:
    """Tamanho completo do arquivo, pelo Content-Range (pedido parcial) ou Content-Length."""
    faixa = r.headers.get("Content-Range", "")
    if "/" in faixa and faixa.rsplit("/", 1)[1].isdigit():
        return int(faixa.rsplit("/", 1)[1])
    comprimento = r.headers.get("Content-Length", "")
    return int(comprimento) if comprimento.isdigit() else None


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

    def get(
        self, url: str, *, stream: bool = False, headers: dict | None = None
    ) -> requests.Response:
        ultimo = ""
        for tentativa in range(self.tentativas):
            self._aguardar_vez()
            retry_after = None
            try:
                r = self.sessao.get(url, timeout=self.timeout, stream=stream, headers=headers)
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
        """Baixa em blocos para `destino`, de forma atômica, e confere o tamanho no fim.

        Servidores públicos às vezes fecham a conexão no meio, e a biblioteca HTTP entrega isso como
        um fim normal: um arquivo cortado passaria como completo. Por isso o tamanho recebido é
        comparado com o anunciado, e o download continua de onde parou (pedido parcial, `Range`).
        """
        destino.parent.mkdir(parents=True, exist_ok=True)
        parcial = destino.with_name(destino.name + ".parcial")
        esperado: int | None = None
        total = 0
        # identity: sem compressão no caminho, o Content-Length é o tamanho do arquivo.
        cabecalhos = {"Accept-Encoding": "identity"}
        sem_progresso, ultimo = 0, ""
        with parcial.open("wb") as f:
            # Continua enquanto cada retomada trouxer bytes: a ANP derruba a conexão a cada poucos
            # MB, e um arquivo de 120 MB precisa de dezenas de retomadas. Só tentativas seguidas
            # sem nenhum byte novo contam para desistir.
            for _ in range(MAX_RETOMADAS):
                antes = total
                pedido = {**cabecalhos, "Range": f"bytes={total}-"} if total else cabecalhos
                try:
                    with self.get(url, stream=True, headers=pedido) as r:
                        if total and r.status_code != 206:  # ignorou o Range: recomeça
                            f.seek(0)
                            f.truncate()
                            total = 0
                        esperado = esperado or tamanho_total(r)
                        for bloco in r.iter_content(chunk_size=1 << 20):
                            f.write(bloco)
                            total += len(bloco)
                except (requests.ConnectionError, requests.Timeout) as e:
                    ultimo = type(e).__name__  # conexão caiu no meio: tenta continuar
                else:
                    ultimo = "conexão fechada antes do fim"
                if esperado is None or total >= esperado:
                    break
                if total > antes:
                    sem_progresso = 0
                    self.dormir(self.intervalo)  # andou: retoma logo, respeitando o intervalo
                else:
                    sem_progresso += 1
                    if sem_progresso >= self.tentativas:
                        break
                    self.dormir(espera(sem_progresso - 1))
        if total == 0:
            parcial.unlink(missing_ok=True)
            raise ErroHTTP(f"{url}: arquivo vazio")
        if esperado is not None and total != esperado:
            parcial.unlink(missing_ok=True)
            raise ErroHTTP(f"{url}: recebi {total} de {esperado} bytes ({ultimo})")
        resumo = hashlib.sha256()
        with parcial.open("rb") as f:
            for bloco in iter(lambda: f.read(1 << 20), b""):
                resumo.update(bloco)
        parcial.replace(destino)
        return Download(destino, total, resumo.hexdigest(), esperado)
