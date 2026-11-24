import pandas as pd

from rota_economica.exportacao import pracas_de_pedagio, recortar_municipios

CSV = (
    "concessionaria;praca_de_pedagio;ano_do_pnv_snv;rodovia;uf;km_m;municipal;tipo_de_pista;"
    "sentido;situacao;data_da_inativacao;latitude;longitude\n"
    "AUTOPISTA;Serrinha;2019;BR-101;RJ;123.07;Campos;Principal;Crescente;Ativo;;-22.04975;"
    "-41.685157\n"
    "AUTOPISTA;Sem Lugar;2019;BR-101;RJ;1;X;Principal;Crescente;Ativo;;;\n"
).encode("latin-1")


def test_pracas_com_coordenadas():
    df = pracas_de_pedagio(CSV)
    assert len(df) == 1
    assert df.iloc[0][["praca", "situacao"]].tolist() == ["Serrinha", "Ativo"]
    assert df.iloc[0]["lon"] == -41.685157


def test_recorte_das_ultimas_semanas():
    semanas = pd.date_range("2026-01-04", periods=12, freq="W-SUN")
    g = pd.DataFrame({"semana": semanas, "preco_mediano": 6.0})
    r = recortar_municipios(g)
    assert len(r) == 8 and r["semana"].min() == semanas[4]
