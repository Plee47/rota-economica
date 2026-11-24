import numpy as np
import pandas as pd
import pytest

from rota_economica.previsao import avaliacao, dados
from rota_economica.previsao.modelos import Gradiente, Ingenuo, em_reais


def _gold(semanas=160, ufs=("SP", "RJ", "MG"), semente=1, tendencia=0.004):
    """Gold sintética: preço com tendência e ruído, uma série por estado e tipo de diesel."""
    rng = np.random.default_rng(semente)
    datas = pd.date_range("2022-01-02", periods=semanas, freq="W-SUN")
    linhas = []
    for i, uf in enumerate(ufs):
        for tipo, base in (("S10", 6.0), ("comum", 5.7)):
            p = base + 0.1 * i
            for d in datas:
                p *= 1 + tendencia + rng.normal(0, 0.003)
                linhas.append((uf, tipo, d, p))
    return pd.DataFrame(linhas, columns=["uf", "tipo_diesel", "semana", "preco_mediano"])


def test_series_completam_lacunas_curtas_e_nao_as_longas():
    g = _gold(semanas=20, ufs=("SP",))
    g = g[~g["semana"].isin(g["semana"].unique()[[5, 10, 11, 12]])]  # 1 e 3 semanas faltando
    s = dados.series(g)
    sp = s[s["serie"] == "SP|S10"].set_index("semana")["preco"]
    assert len(sp) == 20
    assert sp.iloc[5] == sp.iloc[4]  # lacuna de 1 semana: preenchida
    assert sp.iloc[[10, 11]].notna().all() and np.isnan(sp.iloc[12])  # de 3: só 2 preenchidas


def test_exemplos_tem_alvo_e_semana_do_alvo():
    s = dados.series(_gold(semanas=40, ufs=("SP",)))
    e = dados.exemplos(s)
    linha = e[(e["serie"] == "SP|S10") & (e["horizonte"] == 2)].iloc[30]
    sp = s[s["serie"] == "SP|S10"].reset_index(drop=True)
    assert linha["semana_alvo"] == sp["semana"].iloc[32]
    assert linha["alvo"] == pytest.approx(sp["preco"].iloc[32] / sp["preco"].iloc[30] - 1)
    assert set(dados.FEATURES) <= set(e.columns)


def test_origem_nao_enxerga_o_futuro():
    s = dados.series(_gold(semanas=60, ufs=("SP",)))
    semana = s["semana"].unique()[40]
    o = dados.origem(s, semana)
    assert set(o["semana"]) == {semana}
    assert "alvo" not in o.columns
    # Mudar o futuro não muda as features da origem.
    futuro_mexido = s.copy()
    futuro_mexido.loc[futuro_mexido["semana"] > semana, "preco"] = 99.0
    pd.testing.assert_frame_equal(o, dados.origem(futuro_mexido, semana))
    # E é igual ao que o backtest filtra dos exemplos já calculados com a série inteira.
    todos = dados.exemplos(s)
    filtrado = todos[todos["semana"] == semana].drop(columns=["alvo", "preco_alvo"])
    pd.testing.assert_frame_equal(o.reset_index(drop=True), filtrado.reset_index(drop=True))


def test_quantis_do_gradiente_nao_se_cruzam():
    s = dados.series(_gold())
    e = dados.exemplos(s).dropna()
    m = Gradiente(iteracoes=50).treinar(e)
    p = m.prever(e.head(200))
    assert (p["p10"] <= p["previsao"]).all() and (p["previsao"] <= p["p90"]).all()


def test_em_reais_converte_a_variacao():
    o = pd.DataFrame({"serie": ["SP|S10"], "semana": [pd.Timestamp("2024-01-07")],
                      "horizonte": [2], "preco": [6.0]})  # fmt: skip
    v = pd.DataFrame({"previsao": [0.05], "p10": [0.0], "p90": [0.1]})
    r = em_reais(o, v).iloc[0]
    assert r["previsao"] == pytest.approx(6.3)
    assert r["semana_alvo"] == pd.Timestamp("2024-01-21")


def test_backtest_sem_vazamento_e_gradiente_aprende_a_tendencia():
    s = dados.series(_gold(semanas=160))
    origens = list(s["semana"].unique()[-20:-4])
    r_ing = avaliacao.backtest(s, Ingenuo, origens)
    r_gra = avaliacao.backtest(s, lambda: Gradiente(iteracoes=100), origens)
    m_ing, m_gra = avaliacao.metricas(r_ing), avaliacao.metricas(r_gra)
    assert m_ing.previsoes == m_gra.previsoes == len(origens) * 6 * 4
    # Com tendência clara, o modelo bate a base ingênua.
    assert m_gra.mae < m_ing.mae
    assert 0.5 < m_gra.cobertura <= 1
    assert list(avaliacao.por_horizonte(r_gra).index) == [1, 2, 3, 4]


def test_regras_de_promocao():
    M = avaliacao.Metricas
    ingenuo = M(0.10, 0.02, 0.8, 100)
    assert not avaliacao.decidir(M(0.11, 0, 0, 1), None, ingenuo).promover
    assert avaliacao.decidir(M(0.08, 0, 0, 1), None, ingenuo).promover
    assert not avaliacao.decidir(M(0.079, 0, 0, 1), M(0.08, 0, 0, 1), ingenuo).promover  # < 2%
    d = avaliacao.decidir(M(0.07, 0, 0, 1), M(0.08, 0, 0, 1), ingenuo)
    assert d.promover and "0.0700" in d.motivo
