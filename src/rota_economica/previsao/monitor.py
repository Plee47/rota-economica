"""Monitoramento: as previsões já feitas contra o preço que de fato aconteceu.

Funções puras em pandas; o job lê as tabelas, chama estas funções e falha se o erro passar do
limite, para o Databricks avisar.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

JANELA_SEMANAS = 8
TOLERANCIA = 1.5  # erro recente até 1,5 vez o erro medido no backtest é aceitável


def conferir(previsoes: pd.DataFrame, series: pd.DataFrame) -> pd.DataFrame:
    """Junta cada previsão ao preço real da semana do alvo (só as que já aconteceram)."""
    reais = series[["serie", "semana", "preco"]].rename(
        columns={"semana": "semana_alvo", "preco": "real"}
    )
    junto = previsoes.merge(reais, on=["serie", "semana_alvo"], how="inner")
    junto = junto.dropna(subset=["real"])
    junto["erro_abs"] = (junto["previsao"] - junto["real"]).abs()
    junto["dentro"] = (junto["real"] >= junto["p10"]) & (junto["real"] <= junto["p90"])
    return junto


@dataclass(frozen=True)
class Alerta:
    disparou: bool
    mae_recente: float | None
    mae_referencia: float
    mensagem: str


def avaliar(conferidas: pd.DataFrame, mae_referencia: float) -> Alerta:
    """Compara o erro das últimas semanas com o do backtest do modelo em produção."""
    if conferidas.empty:
        return Alerta(False, None, mae_referencia, "ainda não há previsões com o preço real")
    ultima = conferidas["semana_alvo"].max()
    inicio = ultima - pd.Timedelta(weeks=JANELA_SEMANAS - 1)
    recentes = conferidas[conferidas["semana_alvo"] >= inicio]
    mae = float(recentes["erro_abs"].mean())
    limite = mae_referencia * TOLERANCIA
    if mae > limite:
        return Alerta(
            True,
            mae,
            mae_referencia,
            f"erro das últimas {JANELA_SEMANAS} semanas (R$ {mae:.3f}/L) passou de "
            f"{TOLERANCIA:g} vez o do backtest (R$ {mae_referencia:.3f}/L)",
        )
    return Alerta(False, mae, mae_referencia, f"erro recente R$ {mae:.3f}/L, dentro do esperado")
