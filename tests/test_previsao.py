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


def test_monitor_confere_e_alerta():
    from rota_economica.previsao import monitor

    semanas = pd.date_range("2024-01-07", periods=12, freq="W-SUN")
    s = pd.DataFrame({"serie": "SP|S10", "semana": semanas, "preco": 6.0})
    prev = pd.DataFrame(
        {"serie": "SP|S10", "semana_alvo": semanas, "previsao": 6.1, "p10": 5.9, "p90": 6.3}
    )
    futura = prev.iloc[[-1]].assign(semana_alvo=pd.Timestamp("2030-01-06"))
    conferidas = monitor.conferir(pd.concat([prev, futura]), s)
    assert len(conferidas) == 12  # a previsão do futuro ainda não tem preço real
    assert conferidas["erro_abs"].round(3).eq(0.1).all() and conferidas["dentro"].all()
    assert not monitor.avaliar(conferidas, mae_referencia=0.08).disparou  # 0,10 < 1,5 × 0,08
    alerta = monitor.avaliar(conferidas, mae_referencia=0.05)
    assert alerta.disparou and "passou de 1.5 vez" in alerta.mensagem
    assert not monitor.avaliar(conferidas.iloc[0:0], 0.05).disparou


def test_embrulho_do_modelo_devolve_reais():
    from rota_economica.previsao.job import ModeloPyfunc

    s = dados.series(_gold(semanas=60, ufs=("SP",)))
    m = Ingenuo().treinar(dados.exemplos(s))
    origem = dados.origem(s, s["semana"].max())
    r = ModeloPyfunc(m).prever_em_reais(origem)
    assert (r["previsao"] == r["preco_atual"]).all()
    assert set(r["horizonte"]) == {1, 2, 3, 4}


def test_retreino_mensal_no_backtest_nao_ve_o_futuro():
    s = dados.series(_gold(semanas=100, ufs=("SP",)))
    origens = list(s["semana"].unique()[-12:-4])
    vistos = []

    class Espiao(Ingenuo):
        def treinar(self, ex):
            vistos.append(ex["semana_alvo"].max())
            return super().treinar(ex)

    r = avaliacao.backtest(s, Espiao, origens, retreinar_a_cada=4)
    assert len(vistos) == 2  # 8 origens, retreino a cada 4
    assert vistos[0] <= origens[0] and vistos[1] <= origens[4]
    assert r["semana_origem"].nunique() == 8


def test_variaveis_nacionais():
    semanas = pd.date_range("2024-01-07", periods=6, freq="W-SUN")
    linhas = []
    for uf, precos in (("SP", [6.0, 6.0, 6.6, 6.6, 6.6, 6.6]), ("RJ", [6.2] * 6),
                       ("MG", [5.8, 5.8, 6.38, 6.38, 6.38, 6.38])):  # fmt: skip
        linhas += [(uf, "S10", d, p) for d, p in zip(semanas, precos, strict=True)]
    s = dados.series(pd.DataFrame(linhas, columns=["uf", "tipo_diesel", "semana", "preco_mediano"]))
    e = dados.exemplos(s, horizontes=(1,))
    semana3 = e[(e["semana"] == semanas[2]) & (e["serie"] == "RJ|S10")].iloc[0]
    # Dois de três estados subiram 10%: a mediana nacional subiu 10%; o RJ ainda não reajustou.
    assert semana3["brasil_var_1"] == pytest.approx(0.10)
    assert semana3["desvio_brasil"] == pytest.approx(6.2 / 6.38 - 1)
