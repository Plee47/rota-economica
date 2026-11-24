"""Das tabelas gold para séries semanais e para exemplos de treino sem vazamento.

Cada série é um estado e um tipo de diesel. O alvo é a variação percentual do preço `h` semanas à
frente; prever variação, e não o preço, deixa um modelo único servir a estados com níveis de preço
diferentes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONTES = (1, 2, 3, 4)
DEFASAGENS = (1, 2, 4, 8, 13, 26)  # semanas: variação desde cada uma delas
LACUNA_MAXIMA = 2  # semanas sem coleta preenchidas com o último preço; mais que isso fica vazio
FEATURES = [
    *(f"var_{k}" for k in DEFASAGENS),
    "volatilidade_8",
    "preco",
    "mes",
    "horizonte",
    "s10",
]


def series(gold_uf: pd.DataFrame) -> pd.DataFrame:
    """Colunas serie, uf, tipo_diesel, semana e preco, com todas as semanas de cada série."""
    df = gold_uf[["uf", "tipo_diesel", "semana", "preco_mediano"]].rename(
        columns={"preco_mediano": "preco"}
    )
    df["semana"] = pd.to_datetime(df["semana"])
    partes = []
    for (uf, tipo), g in df.groupby(["uf", "tipo_diesel"]):
        g = g.set_index("semana").sort_index()
        semanas = pd.date_range(g.index.min(), g.index.max(), freq="W-SUN")
        g = g.reindex(semanas)
        g["preco"] = g["preco"].ffill(limit=LACUNA_MAXIMA)
        g["uf"], g["tipo_diesel"] = uf, tipo
        partes.append(g.rename_axis("semana").reset_index())
    saida = pd.concat(partes, ignore_index=True)
    saida["serie"] = saida["uf"] + "|" + saida["tipo_diesel"]
    return saida[["serie", "uf", "tipo_diesel", "semana", "preco"]]


def _features_da_serie(g: pd.DataFrame) -> pd.DataFrame:
    p = g["preco"]
    f = pd.DataFrame(index=g.index)
    for k in DEFASAGENS:
        f[f"var_{k}"] = p / p.shift(k) - 1
    f["volatilidade_8"] = (p / p.shift(1) - 1).rolling(8).std()
    f["preco"] = p
    f["mes"] = g["semana"].dt.month
    f["s10"] = (g["tipo_diesel"] == "S10").astype(int)
    return f


def exemplos(s: pd.DataFrame, horizontes=HORIZONTES) -> pd.DataFrame:
    """Uma linha por série, semana de origem e horizonte, com o alvo e a semana do alvo.

    A coluna `semana_alvo` é o que impede vazamento: no backtest, um exemplo só entra no treino se
    o alvo dele já aconteceu até a origem da previsão.
    """
    partes = []
    for _, g in s.sort_values(["serie", "semana"]).groupby("serie", sort=False):
        g = g.reset_index(drop=True)
        base = _features_da_serie(g)
        for h in horizontes:
            e = base.copy()
            e["horizonte"] = h
            e["alvo"] = g["preco"].shift(-h) / g["preco"] - 1
            e["preco_alvo"] = g["preco"].shift(-h)
            e["serie"], e["semana"] = g["serie"], g["semana"]
            e["semana_alvo"] = g["semana"] + pd.to_timedelta(7 * h, unit="D")
            partes.append(e)
    todos = pd.concat(partes, ignore_index=True)
    return todos.replace([np.inf, -np.inf], np.nan)


def origem(s: pd.DataFrame, semana: pd.Timestamp, horizontes=HORIZONTES) -> pd.DataFrame:
    """As features de cada série na semana `semana`, prontas para prever (sem o alvo)."""
    ate = s[s["semana"] <= semana]
    e = exemplos(ate, horizontes)
    return e[e["semana"] == semana].drop(columns=["alvo", "preco_alvo"])
