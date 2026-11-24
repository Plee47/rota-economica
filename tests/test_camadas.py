from datetime import date

import duckdb
import pandas as pd
import pytest

from rota_economica.camadas import gold, silver
from rota_economica.coleta.anp import ErroFormato


def _lidas(linhas):
    """Imita a saída de `ler_diesel`."""
    df = pd.DataFrame(
        linhas, columns=["regiao", "uf", "municipio", "cnpj", "produto", "data", "preco"]
    )
    df["data"] = pd.to_datetime(df["data"])
    df["revenda"] = "POSTO"
    df["bandeira"] = "BRANCA"
    df["unidade"] = "R$ / litro"
    return df


BASE = [
    ("SE", "SP", "CAMPINAS", "1", "DIESEL S10", "2024-01-03", 6.00),  # quarta
    ("SE", "SP", "CAMPINAS", "2", "DIESEL S10", "2024-01-07", 6.40),  # domingo seguinte
    ("SE", "SP", "CAMPINAS", "3", "DIESEL S500", "2024-01-06", 5.80),  # sábado
    ("SE", "SP", "CAMPINAS", "3", "DIESEL", "2010-05-10", 2.00),
]


def test_semana_comeca_no_domingo():
    s = silver.semana(pd.Series(pd.to_datetime(["2024-01-03", "2024-01-06", "2024-01-07"])))
    assert [str(d.date()) for d in s] == ["2023-12-31", "2023-12-31", "2024-01-07"]


def test_tipo_de_diesel():
    produtos = pd.Series(["DIESEL S10", "DIESEL S500", "DIESEL", "DIESEL S-10"])
    assert list(silver.tipo_diesel(produtos)) == ["S10", "comum", "comum", "S10"]


def test_padroniza_com_origem_e_prioridade():
    df = silver.padronizar(_lidas(BASE), "anp/mensal/2024/precos-diesel-gnv-01.csv")
    assert list(df.columns) == silver.COLUNAS
    assert set(df["tipo_arquivo"]) == {"mensal"} and set(df["prioridade"]) == {2}
    assert set(df["arquivo"]) == {"anp/mensal/2024/precos-diesel-gnv-01.csv"}


def test_repeticao_no_mesmo_arquivo_vira_media():
    linhas = [BASE[0], BASE[0][:-1] + (6.20,)]
    df = silver.padronizar(_lidas(linhas), "anp/recente/ultimas.csv")
    assert len(df) == 1 and df["preco"].iloc[0] == pytest.approx(6.10)


def test_caminho_fora_do_padrao_para():
    with pytest.raises(ErroFormato):
        silver.padronizar(_lidas(BASE), "outra/coisa.csv")


def test_validacao():
    df = silver.padronizar(_lidas(BASE), "anp/semestral/2010/ca-2010-01.csv")
    assert silver.validar(df, "x", hoje=date(2026, 11, 24)) == []
    assert silver.validar(df.iloc[0:0], "x")
    futuro = df.assign(data=pd.Timestamp("2030-01-01"))
    assert any("futuro" in p for p in silver.validar(futuro, "x", hoje=date(2026, 11, 24)))
    antigo = df.assign(data=pd.Timestamp("1999-01-01"))
    assert any("anterior" in p for p in silver.validar(antigo, "x"))
    sem_uf = df.assign(uf=None)
    assert any("UF" in p for p in silver.validar(sem_uf, "x", hoje=date(2026, 11, 24)))


@pytest.fixture
def banco():
    """Silver com o mesmo preço em dois arquivos: o recente corrige o mensal."""
    mensal = silver.padronizar(_lidas(BASE), "anp/mensal/2024/precos-diesel-gnv-01.csv")
    corrigido = [BASE[0][:-1] + (6.10,)]
    recente = silver.padronizar(_lidas(corrigido), "anp/recente/ultimas-4-semanas.csv")
    con = duckdb.connect()
    tabela = pd.concat([mensal, recente], ignore_index=True)
    con.register("silver_df", tabela)
    con.execute("CREATE TABLE silver_diesel AS SELECT * FROM silver_df")
    return con


def test_gold_vale_o_arquivo_mais_recente(banco):
    banco.execute(gold.sql_gold("silver_diesel", "gold_municipio", "municipio"))
    linhas = banco.execute(
        "SELECT tipo_diesel, CAST(semana AS DATE), preco_mediano, postos FROM gold_municipio "
        "ORDER BY 2, 1"
    ).fetchall()
    semanas = {(t, str(s)): (p, n) for t, s, p, n in linhas}
    # Semana de 31/12/2023: posto 1 (6,00 corrigido para 6,10) no S10 e posto 3 no comum.
    assert semanas[("S10", "2023-12-31")] == (pytest.approx(6.10), 1)
    assert semanas[("comum", "2023-12-31")] == (pytest.approx(5.80), 1)
    assert semanas[("S10", "2024-01-07")] == (pytest.approx(6.40), 1)


@pytest.mark.parametrize("nivel", list(gold.NIVEIS))
def test_gold_em_todos_os_niveis(banco, nivel):
    banco.execute(gold.sql_gold("silver_diesel", f"g_{nivel}", nivel))
    colunas = [c[0] for c in banco.execute(f"SELECT * FROM g_{nivel} LIMIT 0").description]
    assert colunas[: len(gold.NIVEIS[nivel])] == gold.NIVEIS[nivel]
    assert {"preco_mediano", "postos", "coletas", "semana", "tipo_diesel"} <= set(colunas)


def test_checagens_acham_semana_fraca(banco):
    banco.execute(gold.sql_gold("silver_diesel", "g_brasil", "brasil"))
    resultados = {
        nome: banco.execute(sql).fetchone()[0]
        for nome, sql in gold.sql_checagens("g_brasil").items()
    }
    # Poucos postos de propósito: as 3 linhas (semana e tipo de diesel) desde 2013 ficam abaixo.
    assert resultados["semana com poucos postos no Brasil (desde 2013)"] == 3
    assert resultados["preço mediano fora de 0,5 a 20 R$/L"] == 0
