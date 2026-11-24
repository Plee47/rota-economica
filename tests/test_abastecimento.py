import itertools

import pytest

from rota_economica.custo.abastecimento import (
    Caminhao,
    Posto,
    SemSolucao,
    otimizar,
    sem_planejar,
    simular,
)

# Caminhão de 2,5 km/L, tanque de 300 L: 750 km de tanque cheio.
CAMINHAO = Caminhao(consumo_km_l=2.5, tanque_l=300, inicial_l=150, reserva_l=50)


def test_viagem_curta_nao_compra_nada():
    plano = otimizar([Posto("A", 50, 6.0)], 100, CAMINHAO)
    assert plano.paradas == [] and plano.custo == 0
    assert plano.chegada_l == pytest.approx(150 - 40)


def test_compra_pouco_no_caro_e_enche_no_barato():
    postos = [Posto("Cara", 100, 7.0), Posto("Barata", 200, 5.0)]
    # 150 L dão 250 km sem furar a reserva (100 L úteis): chega à barata sem comprar na cara.
    # De tanque cheio, o caminhão roda 625 km acima da reserva: do km 200 alcança o km 700.
    plano = otimizar(postos, 700, CAMINHAO)
    nomes = [p.nome for p, _ in plano.paradas]
    assert nomes == ["Barata"]
    assert plano.chegada_l == pytest.approx(50, abs=10)


def test_quando_nao_alcanca_o_barato_compra_so_o_necessario_no_caro():
    postos = [Posto("Cara", 100, 7.0), Posto("Barata", 400, 5.0)]
    plano = otimizar(postos, 900, CAMINHAO)
    litros = dict((p.nome, q) for p, q in plano.paradas)
    # Precisa de 120 L até a barata (300 km) e chega com 50: compra só o que falta na cara.
    assert litros["Cara"] <= 80
    assert litros["Barata"] > litros["Cara"]


def test_o_desvio_ate_a_cidade_entra_no_custo():
    # Mesmo preço, mas uma das cidades fica 30 km fora do trajeto.
    postos = [Posto("Longe", 200, 5.0, desvio_km=30), Posto("Perto", 210, 5.0, desvio_km=0)]
    plano = otimizar(postos, 700, CAMINHAO)
    assert [p.nome for p, _ in plano.paradas] == ["Perto"]


def test_sem_postos_suficientes_nao_ha_plano():
    with pytest.raises(SemSolucao):
        otimizar([Posto("A", 600, 5.0)], 1500, CAMINHAO)  # A está além do alcance inicial


def test_simulacao_reprova_plano_que_fura_a_reserva():
    with pytest.raises(SemSolucao, match="abaixo da reserva"):
        simular([(Posto("A", 400, 5.0), 100)], 500, CAMINHAO)


def _forca_bruta(postos, destino, c, passo=10):
    """Todas as compras em múltiplos de `passo`: um limite superior do ótimo em casos pequenos."""
    opcoes = [range(0, int(c.tanque_l) + 1, passo)] * len(postos)
    melhor = None
    for compras in itertools.product(*opcoes):
        paradas = [(p, q) for p, q in zip(postos, compras, strict=True) if q]
        try:
            chegada = simular(paradas, destino, c)
        except SemSolucao:
            continue
        gasto = sum((q + c.litros(2 * p.desvio_km)) * p.preco for p, q in paradas)
        if melhor is None or gasto < melhor[0]:
            melhor = (gasto, chegada)
    return melhor


@pytest.mark.parametrize("semente", range(40))
def test_bate_com_a_forca_bruta(semente):
    import random

    rng = random.Random(semente)
    c = Caminhao(consumo_km_l=2.5, tanque_l=120, inicial_l=70, reserva_l=20)
    postos = [Posto(str(i), km, round(rng.uniform(5, 7), 2), rng.choice([0, 0, 5]))
              for i, km in enumerate(sorted(rng.sample(range(40, 380, 20), 4)))]  # fmt: skip
    destino = 420
    bruta = _forca_bruta(postos, destino, c)
    try:
        plano = otimizar(postos, destino, c)
    except SemSolucao:
        assert bruta is None  # se o otimizador não acha plano, a força bruta também não
        return
    simular(plano.paradas, destino, c)  # o plano é viável
    # Litros quebrados podem achar plano onde múltiplos de 10 L não acham; quando os dois acham,
    # o otimizador nunca é mais caro.
    if bruta is not None:
        assert plano.custo <= bruta[0] + 1e-6


def test_planejar_nunca_sai_mais_caro_que_nao_planejar():
    postos = [Posto("A", 120, 6.4), Posto("B", 300, 5.6), Posto("C", 520, 6.9),
              Posto("D", 700, 5.3), Posto("E", 900, 6.1)]  # fmt: skip
    otimo = otimizar(postos, 1200, CAMINHAO)
    ingenuo = sem_planejar(postos, 1200, CAMINHAO)
    referencia = 6.0
    assert otimo.custo_consumido(referencia) <= ingenuo.custo_consumido(referencia) + 1e-6
    for plano in (otimo, ingenuo):
        simular(plano.paradas, 1200, CAMINHAO)
