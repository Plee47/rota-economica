import pytest
import requests

from rota_economica.coleta import antt
from rota_economica.coleta.anp import ErroFormato
from rota_economica.http import USER_AGENT, ClienteHTTP, ErroHTTP, espera


class Resposta:
    def __init__(self, status, corpo=b"ok", headers=None):
        self.status_code = status
        self.corpo = corpo
        self.headers = headers or {}

    def iter_content(self, chunk_size):
        yield self.corpo

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Sessao:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.headers = {}
        self.pedidos = 0

    def get(self, url, timeout, stream):
        self.pedidos += 1
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _cliente(respostas, esperas=None):
    esperas = [] if esperas is None else esperas
    return ClienteHTTP(intervalo=0, sessao=Sessao(respostas), dormir=esperas.append)


def test_identifica_o_projeto():
    c = _cliente([])
    assert c.sessao.headers["User-Agent"] == USER_AGENT
    assert "rota-economica" in USER_AGENT


def test_tenta_de_novo_em_falha_passageira_e_respeita_retry_after():
    esperas = []
    c = _cliente(
        [Resposta(503), Resposta(429, headers={"Retry-After": "7"}), Resposta(200)], esperas
    )
    assert c.get("https://x").status_code == 200
    assert esperas == [2.0, 7.0]


def test_erro_definitivo_nao_insiste():
    c = _cliente([Resposta(404), Resposta(200)])
    with pytest.raises(ErroHTTP, match="404"):
        c.get("https://x")
    assert c.sessao.pedidos == 1


def test_desiste_depois_das_tentativas():
    c = _cliente([requests.ConnectionError()] * 5)
    with pytest.raises(ErroHTTP, match="5 tentativas"):
        c.get("https://x")


def test_espera_cresce_e_tem_teto():
    assert [espera(i) for i in range(7)] == [2, 4, 8, 16, 32, 60, 60]
    assert espera(0, "bobagem") == 2


def test_download_atomico_com_resumo(tmp_path):
    c = _cliente([Resposta(200, b"conteudo")])
    d = c.baixar("https://x", tmp_path / "a" / "b.csv")
    assert d.caminho.read_bytes() == b"conteudo"
    assert d.bytes == 8 and len(d.sha256) == 64
    assert not list(tmp_path.rglob("*.parcial"))


def test_download_vazio_nao_deixa_arquivo(tmp_path):
    c = _cliente([Resposta(200, b"")])
    with pytest.raises(ErroHTTP, match="vazio"):
        c.baixar("https://x", tmp_path / "b.csv")
    assert not list(tmp_path.iterdir())


def test_antt_baixa_o_csv_do_conjunto(cliente, tmp_path):
    cliente.jsons[f"{antt.API}/package_show?id=praca-de-pedagio"] = {
        "success": True,
        "result": {
            "resources": [
                {"name": "Dicionário", "format": "PDF", "url": "https://antt/dic.pdf"},
                {"name": "Dados", "format": "csv", "url": "https://antt/pracas.csv"},
            ]
        },
    }
    d = antt.baixar_csv(cliente, "praca-de-pedagio", tmp_path)
    assert cliente.baixados == ["https://antt/pracas.csv"]
    assert d.caminho == tmp_path / "antt" / "praca-de-pedagio.csv"


def test_antt_sem_csv_para(cliente, tmp_path):
    cliente.jsons[f"{antt.API}/package_show?id=x"] = {"success": True, "result": {"resources": []}}
    with pytest.raises(ErroFormato):
        antt.baixar_csv(cliente, "x", tmp_path)


def test_coleta_segue_se_a_antt_cair(monkeypatch, tmp_path, caplog):
    from rota_economica import __main__ as cli
    from rota_economica.coleta import anp

    monkeypatch.setattr(anp, "coletar", lambda *a, **k: anp.Relatorio(["x"], [], [], []))

    def falha(*a, **k):
        raise ErroHTTP("dados.antt.gov.br: sem resposta")

    monkeypatch.setattr(antt, "baixar_csv", falha)
    assert cli.main(["--destino", str(tmp_path)]) == 0
    assert "ANTT indisponível" in caplog.text
