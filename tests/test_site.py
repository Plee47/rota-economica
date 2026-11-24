"""O site com dados sintéticos e uma rota em cache: sem Databricks e sem internet."""

import json
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from rota_economica.rota.provedor import Rota, provedor_padrao  # noqa: E402

APP = str(Path(__file__).parents[1] / "app" / "streamlit_app.py")
SP, BA = ("1", "São Paulo", "SP", "SAO PAULO", 0.0, -10.0), ("9", "Salvador", "BA", "SALVADOR",
                                                              8.0, -10.0)  # fmt: skip


@pytest.fixture
def site(tmp_path, monkeypatch):
    dados, cache = tmp_path / "dados", tmp_path / "cache"
    dados.mkdir()
    linhas = [SP, *[(str(i), f"Cidade {i}", "SP", f"CIDADE {i}", float(i), -10.02)
                    for i in range(1, 8)], BA]  # fmt: skip
    mun = pd.DataFrame(linhas, columns=["codigo", "nome", "uf", "chave", "lon", "lat"])
    mun.to_parquet(dados / "municipios.parquet")
    precos = [(u, m.upper(), t, "2026-11-15", p, 5)
              for (u, m, p) in [("SP", "São Paulo", 6.5), ("SP", "Cidade 2", 5.6),
                                ("SP", "Cidade 5", 5.5), ("SP", "Cidade 7", 6.6),
                                ("BA", "Salvador", 6.3)]
              for t in ("S10", "comum")]  # fmt: skip
    colunas = ["uf", "municipio", "tipo_diesel", "semana", "preco_mediano", "postos"]
    pd.DataFrame(precos, columns=colunas).to_parquet(dados / "precos_municipio.parquet")
    pd.DataFrame({"praca": ["P1"], "situacao": ["Ativo"], "lon": [2.5], "lat": [-10.0]}).to_parquet(
        dados / "pracas.parquet"
    )
    semanas = pd.date_range("2025-01-05", periods=60, freq="W-SUN")
    pd.DataFrame({"uf": "SP", "tipo_diesel": "S10", "semana": semanas,
                  "preco_mediano": 6.0}).to_parquet(dados / "precos_uf.parquet")  # fmt: skip
    pd.DataFrame({"serie": ["SP|S10"] * 2, "semana_origem": [semanas[-1]] * 2,
                  "horizonte": [1, 2], "semana_alvo": [semanas[-1] + pd.Timedelta(weeks=h)
                                                       for h in (1, 2)],
                  "preco_atual": 6.0, "previsao": [6.05, 6.1], "p10": 5.9,
                  "p90": 6.2}).to_parquet(dados / "previsoes.parquet")  # fmt: skip
    (dados / "modelo.json").write_text(json.dumps({
        "semana_dados": "2026-11-15", "campeao": "gradiente v3",
        "decisao": {"promovido": True, "motivo": "MAE 0.0400 contra 0.0450 do campeão"},
        "backtest": {"ingenuo": {"mae": 0.05, "mape": 0.008, "cobertura": 0.8, "previsoes": 9},
                     "gradiente": {"mae": 0.04, "mape": 0.006, "cobertura": 0.82,
                                   "previsoes": 9}},
        "por_horizonte": {"1": {"mae": 0.03, "mape": 0.005, "cobertura": 0.8, "previsoes": 3}},
        "monitoramento": {"alerta": False, "mae_recente": 0.04, "mae_referencia": 0.04,
                          "mensagem": "erro recente R$ 0.040/L, dentro do esperado"},
    }))  # fmt: skip
    # Rota de SP a Salvador já em cache: o OSRM não é chamado.
    pontos = [(SP[4], SP[5]), (BA[4], BA[5])]
    arquivo = provedor_padrao(None, cache)._arquivo(pontos)
    arquivo.parent.mkdir(parents=True)
    rota = Rota(890.0, 11.0, [(float(x), -10.0) for x in range(0, 9)], "osrm")
    arquivo.write_text(json.dumps({"distancia_km": rota.distancia_km, "duracao_h": rota.duracao_h,
                                   "trajeto": rota.trajeto, "provedor": "osrm"}))  # fmt: skip
    monkeypatch.setenv("ROTA_DADOS", str(dados))
    monkeypatch.setenv("ROTA_CACHE", str(cache))
    return AppTest.from_file(APP, default_timeout=60)


def _html(at) -> str:
    return " ".join(h.proto.body for h in at.get("html"))


def test_calcula_a_viagem(site):
    at = site.run()
    assert not at.exception
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception
    pagina = _html(at)
    assert "São Paulo" in pagina and "Salvador" in pagina
    assert "890 km" in pagina and "Pedágio estimado" in pagina and "Total" in pagina
    assert 'class="economia"' in pagina
    assert 'class="paradas"' in pagina


def test_abas_de_preco_e_modelo(site):
    at = site.run()
    assert not at.exception
    textos = " ".join(m.value for m in at.markdown)
    assert "gradiente v3" in textos
    assert any("dentro do esperado" in s.value for s in at.success)


def test_exemplo_na_primeira_visita(site):
    at = site.run()
    exemplo = [b for b in at.button if "exemplo" in b.label.lower()]
    assert exemplo
    exemplo[0].click().run()
    assert not at.exception
    assert 'class="nota"' in _html(at)


def test_caminhao_impossivel_explica(site):
    at = site.run()
    campos = {n.label: n for n in at.sidebar.number_input}
    campos["Tanque (L)"].set_value(400).run()
    campos = {n.label: n for n in at.sidebar.number_input}
    campos["Reserva mínima (L)"].set_value(450).run()  # maior que o tanque
    at.sidebar.button[0].click().run()
    assert any("reserva mínima" in w.value for w in at.warning)


def test_sem_dados_explica_como_sincronizar(tmp_path, monkeypatch):
    monkeypatch.setenv("ROTA_DADOS", str(tmp_path / "vazio"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert any("sincronizar_site.py" in e.value for e in at.error)


def test_arquivo_opcional_corrompido_nao_derruba_o_site(site, tmp_path):
    import os

    (Path(os.environ["ROTA_DADOS"]) / "modelo.json").write_bytes(bytes([0xE3]) + b" nao e json")
    at = site.run()
    assert not at.exception
    assert any("modelo.json" in w.value for w in at.warning)
