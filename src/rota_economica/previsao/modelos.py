"""Os modelos: a base ingênua e um gradient boosting global com intervalo de previsão.

Os dois têm a mesma interface: `treinar(exemplos)` e `prever(origem)`, que devolve a variação
prevista e os quantis de 10% e 90%. Assim o backtest e a promoção tratam qualquer modelo igual.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from rota_economica.previsao.dados import FEATURES

QUANTIS = {"previsao": 0.5, "p10": 0.1, "p90": 0.9}


@dataclass
class Ingenuo:
    """O preço daqui a h semanas é o de hoje.

    O intervalo vem das variações já vistas em h semanas no treino.
    """

    nome: str = "ingenuo"
    _quantis: dict[int, tuple[float, float]] = field(default_factory=dict)

    def treinar(self, ex: pd.DataFrame) -> Ingenuo:
        ex = ex.dropna(subset=["alvo"])
        for h, g in ex.groupby("horizonte"):
            self._quantis[int(h)] = (g["alvo"].quantile(0.1), g["alvo"].quantile(0.9))
        return self

    def prever(self, origem: pd.DataFrame) -> pd.DataFrame:
        q = origem["horizonte"].map(lambda h: self._quantis.get(int(h), (0.0, 0.0)))
        return pd.DataFrame(
            {
                "previsao": 0.0,
                "p10": [a for a, _ in q],
                "p90": [b for _, b in q],
            },
            index=origem.index,
        )


@dataclass
class Gradiente:
    """Um modelo por quantil, treinado em todas as séries juntas (estado e tipo de diesel)."""

    nome: str = "gradiente"
    # Rasas e regularizadas: o preço muda em degraus, e árvores fundas aprendem ruído (no backtest
    # real, as fundas erravam mais que a base ingênua).
    iteracoes: int = 150
    taxa: float = 0.03
    profundidade: int = 3
    semente: int = 7
    _modelos: dict[str, HistGradientBoostingRegressor] = field(default_factory=dict)

    def treinar(self, ex: pd.DataFrame) -> Gradiente:
        ex = ex.dropna(subset=["alvo"])
        for nome, q in QUANTIS.items():
            m = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=q,
                max_iter=self.iteracoes,
                learning_rate=self.taxa,
                max_depth=self.profundidade,
                min_samples_leaf=200,
                l2_regularization=1.0,
                random_state=self.semente,
            )
            self._modelos[nome] = m.fit(ex[FEATURES], ex["alvo"])
        return self

    def prever(self, origem: pd.DataFrame) -> pd.DataFrame:
        saida = pd.DataFrame(
            {n: m.predict(origem[FEATURES]) for n, m in self._modelos.items()}, index=origem.index
        )
        # Quantis treinados separados podem se cruzar: a ordem é imposta depois.
        ordenados = np.sort(saida[["p10", "previsao", "p90"]].to_numpy(), axis=1)
        saida["p10"], saida["previsao"], saida["p90"] = ordenados.T
        return saida


def em_reais(origem: pd.DataFrame, variacoes: pd.DataFrame) -> pd.DataFrame:
    """Converte as variações previstas em preço (R$/L), com a semana do alvo."""
    p = origem["preco"]
    return pd.DataFrame(
        {
            "serie": origem["serie"],
            "semana_origem": origem["semana"],
            "horizonte": origem["horizonte"],
            "semana_alvo": origem["semana"] + pd.to_timedelta(7 * origem["horizonte"], unit="D"),
            "preco_atual": p,
            "previsao": p * (1 + variacoes["previsao"]),
            "p10": p * (1 + variacoes["p10"]),
            "p90": p * (1 + variacoes["p90"]),
        }
    )
