"""Backtest com origem móvel e a regra de promoção do campeão.

Em cada semana de origem, o modelo é treinado só com exemplos cujo alvo já tinha acontecido até
ali, e prevê as semanas seguintes. É a forma honesta de medir um modelo de série temporal: dividir
ao acaso deixaria o futuro vazar para o treino.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from rota_economica.previsao.dados import exemplos
from rota_economica.previsao.modelos import em_reais

MARGEM_PROMOCAO = 0.02  # o desafiante precisa errar pelo menos 2% menos que o campeão


def backtest(s: pd.DataFrame, fabrica: Callable, origens: list[pd.Timestamp]) -> pd.DataFrame:
    """Previsões em reais e o preço que de fato aconteceu, para cada origem e horizonte."""
    todos = exemplos(s)
    reais = s.set_index(["serie", "semana"])["preco"]
    resultados = []
    for o in origens:
        treino = todos[todos["semana_alvo"] <= o]
        modelo = fabrica().treinar(treino)
        # As features só olham para trás, então filtrar a semana dá o mesmo que recalcular com a
        # série cortada em `o` (o teste de dados confere isso), e é muito mais rápido.
        base = todos[todos["semana"] == o].drop(columns=["alvo", "preco_alvo"])
        base = base.dropna(subset=["preco"])
        if base.empty:
            continue
        prev = em_reais(base, modelo.prever(base))  # o gradiente trata valores vazios sozinho
        chaves = list(zip(prev["serie"], prev["semana_alvo"], strict=True))
        prev["real"] = [reais.get(k, np.nan) for k in chaves]
        resultados.append(prev)
    return pd.concat(resultados, ignore_index=True).dropna(subset=["real"])


@dataclass(frozen=True)
class Metricas:
    mae: float  # erro médio absoluto em R$/L
    mape: float  # erro percentual médio
    cobertura: float  # fração dos preços reais dentro do intervalo de 10% a 90%
    previsoes: int

    def como_dict(self) -> dict[str, float]:
        return {"mae": self.mae, "mape": self.mape, "cobertura": self.cobertura,
                "previsoes": self.previsoes}  # fmt: skip


def metricas(r: pd.DataFrame) -> Metricas:
    erro = (r["previsao"] - r["real"]).abs()
    dentro = (r["real"] >= r["p10"]) & (r["real"] <= r["p90"])
    return Metricas(
        mae=float(erro.mean()),
        mape=float((erro / r["real"]).mean()),
        cobertura=float(dentro.mean()),
        previsoes=len(r),
    )


def por_horizonte(r: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {h: metricas(g).como_dict() for h, g in r.groupby("horizonte")}
    ).T.rename_axis("horizonte")


@dataclass(frozen=True)
class Decisao:
    promover: bool
    motivo: str


def decidir(desafiante: Metricas, campeao: Metricas | None, ingenuo: Metricas) -> Decisao:
    """Promove o desafiante só se ele bater a base ingênua e o campeão atual com margem."""
    if desafiante.mae >= ingenuo.mae:
        return Decisao(False, f"não bate a base ingênua (MAE {desafiante.mae:.4f} ≥ "
                              f"{ingenuo.mae:.4f})")  # fmt: skip
    if campeao is None:
        return Decisao(True, "primeiro modelo, e bate a base ingênua")
    limite = campeao.mae * (1 - MARGEM_PROMOCAO)
    if desafiante.mae > limite:
        return Decisao(False, f"não melhora o campeão em {MARGEM_PROMOCAO:.0%} "
                              f"(MAE {desafiante.mae:.4f} > {limite:.4f})")  # fmt: skip
    return Decisao(True, f"MAE {desafiante.mae:.4f} contra {campeao.mae:.4f} do campeão")
