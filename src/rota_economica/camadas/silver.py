"""Silver: um preço de diesel por posto, produto e dia, com tipos certos e a semana da pesquisa.

Funções em pandas, puras e testadas localmente. O job do Databricks só chama estas funções e grava
o resultado em Delta, arquivo por arquivo.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from rota_economica.coleta.anp import ErroFormato

# Quando o mesmo preço vem em dois arquivos, vale o de maior prioridade: os arquivos mais recentes
# trazem as correções da ANP.
PRIORIDADE = {"recente": 3, "mensal": 2, "semestral": 1}
PRIMEIRO_ANO = 2004
COLUNAS = [
    "regiao",
    "uf",
    "municipio",
    "cnpj",
    "bandeira",
    "produto",
    "tipo_diesel",
    "data",
    "semana",
    "preco",
    "arquivo",
    "tipo_arquivo",
    "prioridade",
]


def tipo_diesel(produto: pd.Series) -> pd.Series:
    """S10 (baixo enxofre, desde 2013) ou comum (S500; antes de 2012, "DIESEL" sem sufixo)."""
    s10 = produto.str.contains(r"S-?10(?!\d)", regex=True, na=False)
    return s10.map({True: "S10", False: "comum"})


def semana(data: pd.Series) -> pd.Series:
    """Domingo que abre a semana da data: a ANP pesquisa de domingo a sábado."""
    dias_desde_domingo = (data.dt.day_of_week + 1) % 7
    return (data - pd.to_timedelta(dias_desde_domingo, unit="D")).dt.normalize()


def tipo_do_caminho(caminho: str) -> str:
    """O tipo do arquivo pelo caminho no volume (anp/<tipo>/...)."""
    partes = caminho.replace("\\", "/").split("/")
    tipo = partes[1] if len(partes) > 2 and partes[0] == "anp" else ""
    if tipo not in PRIORIDADE:
        raise ErroFormato(f"{caminho}: caminho fora do padrão anp/<tipo>/...")
    return tipo


def padronizar(df: pd.DataFrame, caminho: str) -> pd.DataFrame:
    """Recebe a saída de `leitura.anp.ler_diesel` e devolve as colunas da silver."""
    tipo = tipo_do_caminho(caminho)
    saida = df.copy()
    saida["tipo_diesel"] = tipo_diesel(saida["produto"])
    saida["semana"] = semana(saida["data"])
    saida["data"] = saida["data"].dt.normalize()
    saida["arquivo"] = caminho
    saida["tipo_arquivo"] = tipo
    saida["prioridade"] = PRIORIDADE[tipo]
    # O mesmo posto pode repetir o mesmo produto no mesmo dia dentro do arquivo: fica a média.
    chave = [c for c in COLUNAS if c != "preco"]
    return saida[COLUNAS].groupby(chave, as_index=False, dropna=False)["preco"].mean()[COLUNAS]


def validar(df: pd.DataFrame, caminho: str, hoje: date | None = None) -> list[str]:
    """Problemas que impedem o arquivo de entrar na silver."""
    hoje = hoje or date.today()
    problemas = []
    if df.empty:
        problemas.append(f"{caminho}: nenhuma linha de diesel")
        return problemas
    if df["data"].min().year < PRIMEIRO_ANO:
        problemas.append(f"{caminho}: data anterior a {PRIMEIRO_ANO} ({df['data'].min().date()})")
    if df["data"].max().date() > hoje:
        problemas.append(f"{caminho}: data no futuro ({df['data'].max().date()})")
    sem_uf = df["uf"].isna() | (df["uf"].str.len() != 2)
    if sem_uf.mean() > 0.01:
        problemas.append(f"{caminho}: {sem_uf.mean():.1%} das linhas sem UF válida")
    return problemas
