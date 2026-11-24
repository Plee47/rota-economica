import pandas as pd
import pytest

from rota_economica.coleta.anp import ErroFormato
from rota_economica.rota import geo, municipios, trajeto
from rota_economica.rota.provedor import OSRM, ComCache, Rota

SP, RJ = (-46.6333, -23.5505), (-43.1729, -22.9068)


def test_haversine_sp_rj():
    assert geo.haversine_km(SP, RJ) == pytest.approx(361, abs=3)


def test_ponto_no_segmento():
    a, b = (-47.0, -23.0), (-46.0, -23.0)
    d, t = geo.ponto_no_segmento((-46.5, -23.1), a, b)
    assert d == pytest.approx(11.1, abs=0.2) and t == pytest.approx(0.5)
    d, t = geo.ponto_no_segmento((-48.0, -23.0), a, b)  # antes do início
    assert t == 0 and d == pytest.approx(102.4, abs=1)


def test_projetar_da_o_quilometro_na_linha():
    linha = [(-47.0, -23.0), (-46.0, -23.0), (-45.0, -23.0)]
    km = geo.acumulado_km(linha)
    desvio, posicao = geo.projetar((-45.5, -22.95), linha, km)
    assert desvio == pytest.approx(5.6, abs=0.2)
    assert posicao == pytest.approx(km[1] + (km[2] - km[1]) / 2, abs=0.5)


def test_centroide_de_quadrado_e_de_multipoligono():
    quadrado = {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}
    assert geo.centroide(quadrado) == pytest.approx((1, 1))
    ilha = [[[10, 10], [10.1, 10], [10.1, 10.1], [10, 10.1], [10, 10]]]
    multi = {"type": "MultiPolygon", "coordinates": [ilha, quadrado["coordinates"]]}
    assert geo.centroide(multi) == pytest.approx((1, 1))  # o maior pedaço


@pytest.mark.parametrize(
    ("ibge", "anp"),
    [("São Paulo", "SAO PAULO"), ("Alta Floresta D'Oeste", "ALTA FLORESTA D OESTE"),
     ("Mogi-Mirim", "MOGI MIRIM"), ("Maracanaú", "MARACANAU")],
)  # fmt: skip
def test_chave_junta_ibge_e_anp(ibge, anp):
    assert municipios.chave_nome(ibge) == municipios.chave_nome(anp)


def _malha(codigos_centros):
    def quadrado(x, y):
        return [[[x - 0.01, y - 0.01], [x + 0.01, y - 0.01], [x + 0.01, y + 0.01],
                 [x - 0.01, y + 0.01], [x - 0.01, y - 0.01]]]  # fmt: skip

    return {
        "features": [
            {"properties": {"codarea": c}, "geometry": {"type": "Polygon",
                                                        "coordinates": quadrado(x, y)}}
            for c, (x, y) in codigos_centros.items()
        ]
    }  # fmt: skip


def test_montar_junta_lista_e_malha_e_exige_volume(monkeypatch):
    monkeypatch.setattr(municipios, "MINIMO_MUNICIPIOS", 2)
    lista = [
        {"municipio-id": 1, "municipio-nome": "São Paulo", "UF-sigla": "SP"},
        {"municipio-id": 2, "municipio-nome": "Rio de Janeiro", "UF-sigla": "RJ"},
        {"municipio-id": 3, "municipio-nome": "Sem Contorno", "UF-sigla": "MG"},
    ]
    df = municipios.montar(lista, _malha({"1": SP, "2": RJ}))
    assert list(df["chave"]) == ["SAO PAULO", "RIO DE JANEIRO"]
    assert df.iloc[0][["lon", "lat"]].tolist() == pytest.approx(list(SP))
    assert municipios.localizar(df, "SAO PAULO", "SP")["codigo"] == "1"
    with pytest.raises(KeyError):
        municipios.localizar(df, "São Paulo", "RJ")
    monkeypatch.setattr(municipios, "MINIMO_MUNICIPIOS", 5)
    with pytest.raises(ErroFormato):
        municipios.montar(lista, _malha({"1": SP}))


class ClienteRota:
    def __init__(self, resposta):
        self.resposta = resposta
        self.urls = []

    def json(self, url):
        self.urls.append(url)
        return self.resposta


RESPOSTA = {
    "code": "Ok",
    "routes": [{"distance": 433600, "duration": 20088,
                "geometry": {"coordinates": [list(SP), [-45.0, -23.2], list(RJ)]}}],
}  # fmt: skip


def test_osrm_monta_a_url_e_le_a_rota():
    c = ClienteRota(RESPOSTA)
    r = OSRM(c).rota([SP, RJ])
    assert "/route/v1/driving/-46.63330,-23.55050;-43.17290,-22.90680?" in c.urls[0]
    assert "overview=full" in c.urls[0]
    assert r.distancia_km == pytest.approx(433.6) and r.duracao_h == pytest.approx(5.58)
    assert r.trajeto[0] == SP


def test_osrm_sem_rota_para():
    with pytest.raises(ErroFormato):
        OSRM(ClienteRota({"code": "NoRoute", "message": "sem caminho"})).rota([SP, RJ])


def test_cache_consulta_o_provedor_uma_vez(tmp_path):
    c = ClienteRota(RESPOSTA)
    provedor = ComCache(OSRM(c), tmp_path)
    r1, r2 = provedor.rota([SP, RJ]), provedor.rota([SP, RJ])
    assert len(c.urls) == 1 and r1 == r2


def test_municipios_no_caminho_em_ordem_e_dentro_do_raio():
    rota = Rota(400, 5, [(-47.0, -23.0), (-46.0, -23.0), (-45.0, -23.0)], "teste")
    m = pd.DataFrame(
        [("3", "C", "SP", "C", -45.2, -23.05),   # perto, km ~ 185
         ("1", "A", "SP", "A", -46.9, -23.0),    # em cima, km ~ 10
         ("2", "B", "SP", "B", -46.0, -23.5),    # longe (~55 km)
         ("4", "D", "MG", "D", -40.0, -20.0)],   # fora da caixa
        columns=["codigo", "nome", "uf", "chave", "lon", "lat"],
    )  # fmt: skip
    r = trajeto.municipios_no_caminho(rota, m)
    assert list(r["nome"]) == ["A", "C"]
    assert r["km"].is_monotonic_increasing and (r["desvio_km"] <= trajeto.RAIO_KM).all()


def test_encadeado_usa_a_reserva_quando_o_primeiro_cai():
    from rota_economica.http import ErroHTTP
    from rota_economica.rota.provedor import FOSSGIS, Encadeado

    class Caido:
        def json(self, url):
            raise ErroHTTP("router.project-osrm.org: HTTP 503")

    reserva = ClienteRota(RESPOSTA)
    r = Encadeado([OSRM(Caido()), OSRM(reserva, base=FOSSGIS)]).rota([SP, RJ])
    assert r.distancia_km == pytest.approx(433.6)
    assert reserva.urls[0].startswith(FOSSGIS)


def test_encadeado_explica_quando_todos_caem():
    from rota_economica.http import ErroHTTP
    from rota_economica.rota.provedor import Encadeado

    class Caido:
        def json(self, url):
            raise ErroHTTP("fora do ar")

    with pytest.raises(ErroHTTP, match="nenhum serviço de rotas respondeu"):
        Encadeado([OSRM(Caido()), OSRM(Caido())]).rota([SP, RJ])
