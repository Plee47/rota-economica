import pandas as pd
import pytest

from rota_economica.custo.abastecimento import Caminhao
from rota_economica.custo.viagem import calcular, precos_por_municipio
from rota_economica.rota.provedor import Rota

# Linha reta de 0 a 8 graus de longitude no equador do sul: ~890 km.
ROTA = Rota(890.0, 11.0, [(lon, -10.0) for lon in range(0, 9)], "teste")
MUNICIPIOS = pd.DataFrame(
    [(str(i), f"Cidade {i}", "XX", f"CIDADE {i}", float(i), -10.02) for i in range(0, 9)],
    columns=["codigo", "nome", "uf", "chave", "lon", "lat"],
)
CAMINHAO = Caminhao(consumo_km_l=2.5, tanque_l=300, inicial_l=120, reserva_l=50)


def _gold():
    linhas = []
    for i, preco in enumerate([6.5, 6.4, 5.6, 6.8, 6.7, 5.5, 6.9, 6.6, 6.2]):
        for semana, ajuste in (("2026-11-08", 0.1), ("2026-11-15", 0.0)):
            linhas.append(("XX", f"CIDADE {i}", "S10", semana, preco + ajuste, 10))
    linhas.append(("XX", "CIDADE 2", "comum", "2026-11-15", 5.0, 3))
    return pd.DataFrame(
        linhas, columns=["uf", "municipio", "tipo_diesel", "semana", "preco_mediano", "postos"]
    )


PRACAS = pd.DataFrame(
    {"praca_de_pedagio": ["P1", "P2", "Longe"], "situacao": ["Ativo", "Ativo", "Ativo"],
     "lon": [2.5, 6.5, 4.0], "lat": [-10.0, -10.005, -11.0]}
)  # fmt: skip


def test_precos_usam_a_semana_mais_recente_do_tipo():
    p = precos_por_municipio(_gold(), "S10")
    c2 = p[p["chave"] == "CIDADE 2"].iloc[0]
    assert c2["preco"] == pytest.approx(5.6) and str(c2["semana"].date()) == "2026-11-15"


def test_viagem_completa():
    v = calcular(ROTA, MUNICIPIOS, precos_por_municipio(_gold(), "S10"), PRACAS, CAMINHAO,
                 eixos=6, tarifa_eixo=11.0)  # fmt: skip
    assert len(v.caminho) == 9
    litros = {p.nome: q for p, q in v.plano.paradas}
    # Com 120 L e reserva de 50, roda 175 km: não alcança a barata (Cidade 2, km 220) sem comprar.
    # Compra só o mínimo na Cidade 1 (R$ 6,40, a mais barata até ali) e enche nas baratas.
    assert set(litros) <= {"Cidade 1 (XX)", "Cidade 2 (XX)", "Cidade 5 (XX)"}
    assert litros.get("Cidade 1 (XX)", 0) < 60
    assert litros["Cidade 2 (XX)"] + litros.get("Cidade 5 (XX)", 0) > 200
    assert v.economia is not None and v.economia > 0
    assert len(v.pedagio.pracas) == 2 and v.pedagio.custo == pytest.approx(2 * 6 * 11.0)
    assert v.litros_consumidos == pytest.approx(890 / 2.5)
    # O diesel consumido fica entre o mais barato e o mais caro do caminho.
    assert 5.5 * v.litros_consumidos <= v.custo_diesel <= 6.9 * v.litros_consumidos
    assert v.custo_total == pytest.approx(v.custo_diesel + v.pedagio.custo)
    assert any("estaduais" in a for a in v.avisos)


def test_sem_preco_no_caminho_avisa():
    vazio = precos_por_municipio(_gold().iloc[0:0], "S10")
    with pytest.raises(ValueError, match="nenhum município"):
        calcular(ROTA, MUNICIPIOS, vazio, PRACAS, CAMINHAO)
