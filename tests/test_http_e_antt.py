import pytest
import requests

from rota_economica.coleta import antt
from rota_economica.coleta.anp import ErroFormato
from rota_economica.http import USER_AGENT, ClienteHTTP, ErroHTTP, espera


class Resposta:
    def __init__(self, status, corpo=b"ok", headers=None, cai_depois=None):
        self.status_code = status
        self.corpo = corpo
        self.headers = headers or {}
        self.cai_depois = cai_depois  # entrega só estes bytes e derruba a conexão

    def iter_content(self, chunk_size):
        if self.cai_depois is None:
            yield self.corpo
        else:
            yield self.corpo[: self.cai_depois]
            raise requests.ConnectionError("conexão caiu")

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

    def get(self, url, timeout, stream, headers=None):
        self.pedidos += 1
        self.cabecalhos = getattr(self, "cabecalhos", []) + [headers or {}]
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


def test_download_cortado_continua_de_onde_parou(tmp_path):
    esperas = []
    c = _cliente(
        [
            Resposta(200, b"0123456789", {"Content-Length": "10"}, cai_depois=4),
            Resposta(206, b"456789", {"Content-Range": "bytes 4-9/10"}),
        ],
        esperas,
    )
    d = c.baixar("https://x", tmp_path / "a.csv")
    assert d.caminho.read_bytes() == b"0123456789"
    assert (d.bytes, d.esperado) == (10, 10)
    assert c.sessao.cabecalhos[1]["Range"] == "bytes=4-"
    assert c.sessao.cabecalhos[0]["Accept-Encoding"] == "identity"


def test_servidor_que_fecha_cedo_sem_erro_e_detectado(tmp_path):
    # Fim "normal" com menos bytes que o anunciado: era o defeito que cortava arquivos da ANP.
    curta = [Resposta(200, b"0123", {"Content-Length": "10"})] + [
        Resposta(206, b"", {"Content-Range": "bytes 4-9/10"}) for _ in range(5)
    ]
    c = _cliente(curta)
    with pytest.raises(ErroHTTP, match="recebi 4 de 10 bytes"):
        c.baixar("https://x", tmp_path / "a.csv")
    assert not list(tmp_path.iterdir())


def test_servidor_que_ignora_o_range_recomeca_do_zero(tmp_path):
    c = _cliente(
        [
            Resposta(200, b"0123456789", {"Content-Length": "10"}, cai_depois=4),
            Resposta(200, b"0123456789", {"Content-Length": "10"}),
        ]
    )
    d = c.baixar("https://x", tmp_path / "a.csv")
    assert d.caminho.read_bytes() == b"0123456789"


def test_tamanho_total():
    from rota_economica.http import tamanho_total

    assert tamanho_total(Resposta(206, headers={"Content-Range": "bytes 4-9/10"})) == 10
    assert tamanho_total(Resposta(200, headers={"Content-Length": "7"})) == 7
    assert tamanho_total(Resposta(200)) is None


def test_muitas_quedas_com_progresso_nao_desistem(tmp_path):
    # O servidor entrega 2 bytes por conexão: 50 retomadas para 100 bytes, todas com progresso.
    corpo = bytes(range(100))
    respostas = [Resposta(200, corpo, {"Content-Length": "100"}, cai_depois=2)]
    for inicio in range(2, 100, 2):
        respostas.append(
            Resposta(206, corpo[inicio:], {"Content-Range": f"bytes {inicio}-99/100"}, cai_depois=2)
        )
    esperas = []
    c = _cliente(respostas, esperas)
    d = c.baixar("https://x", tmp_path / "a.bin")
    assert d.caminho.read_bytes() == corpo
    assert set(esperas) <= {0}  # com progresso, sem espera crescente (intervalo 0 nos testes)


def test_identificacao_so_em_ascii():
    # Cabeçalho HTTP com acento ("portfólio") fazia o IBGE responder 403.
    assert USER_AGENT.isascii()
