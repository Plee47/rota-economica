"""O custo de uma viagem: diesel com o plano de abastecimento, pedágio estimado e a economia.

Junta as peças: a rota e os municípios do caminho (`rota`), o preço do diesel por município (gold
da ANP), as praças de pedágio federais (ANTT) e a otimização (`abastecimento`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from rota_economica.custo.abastecimento import (
    Caminhao,
    Plano,
    Posto,
    SemSolucao,
    otimizar,
    sem_planejar,
)
from rota_economica.rota.municipios import chave_nome
from rota_economica.rota.provedor import Rota
from rota_economica.rota.trajeto import RAIO_KM, municipios_no_caminho, perto_do_caminho

RAIO_PEDAGIO_KM = 1.5  # praça a até 1,5 km do trajeto: a rota passa por ela
TARIFA_EIXO_PADRAO = 10.0  # R$ por eixo por praça: estimativa editável, a ANTT não publica tarifas


@dataclass(frozen=True)
class Pedagio:
    pracas: pd.DataFrame
    eixos: int
    tarifa_eixo: float

    @property
    def custo(self) -> float:
        return len(self.pracas) * self.eixos * self.tarifa_eixo


@dataclass
class Viagem:
    rota: Rota
    caminho: pd.DataFrame  # municípios do caminho, com preço quando a ANP pesquisou
    caminhao: Caminhao
    plano: Plano
    sem_plano: Plano | None  # o motorista que não planeja; None se nem ele chegaria
    pedagio: Pedagio
    preco_referencia: float  # R$/L para valorar o diesel no tanque (início e chegada)
    avisos: list[str] = field(default_factory=list)

    @property
    def litros_consumidos(self) -> float:
        return self.rota.distancia_km / self.caminhao.consumo_km_l

    def _consumido(self, plano: Plano) -> float:
        """R$ do diesel gasto: o comprado, mais o que já estava no tanque, menos o que sobra."""
        inicial = self.caminhao.inicial_l * self.preco_referencia
        return plano.custo + inicial - plano.chegada_l * self.preco_referencia

    @property
    def custo_diesel(self) -> float:
        return self._consumido(self.plano)

    @property
    def economia(self) -> float | None:
        """Quanto o plano economiza contra o motorista que não planeja (diesel consumido)."""
        if self.sem_plano is None:
            return None
        return self._consumido(self.sem_plano) - self._consumido(self.plano)

    @property
    def custo_total(self) -> float:
        return self.custo_diesel + self.pedagio.custo

    @property
    def custo_km(self) -> float:
        return self.custo_total / self.rota.distancia_km


def precos_por_municipio(gold_municipio: pd.DataFrame, tipo_diesel: str) -> pd.DataFrame:
    """Último preço mediano de cada município para o tipo de diesel, com a semana dele."""
    g = gold_municipio[gold_municipio["tipo_diesel"] == tipo_diesel].copy()
    g["semana"] = pd.to_datetime(g["semana"])
    ultimo = g.sort_values("semana").groupby(["uf", "municipio"], as_index=False).last()
    ultimo["chave"] = ultimo["municipio"].map(chave_nome)
    return ultimo[["uf", "chave", "preco_mediano", "semana", "postos"]].rename(
        columns={"preco_mediano": "preco"}
    )


def calcular(
    rota: Rota,
    municipios: pd.DataFrame,
    precos: pd.DataFrame,
    pracas: pd.DataFrame,
    caminhao: Caminhao,
    eixos: int = 5,
    tarifa_eixo: float = TARIFA_EIXO_PADRAO,
    raio_km: float = RAIO_KM,
) -> Viagem:
    avisos = []
    caminho = municipios_no_caminho(rota, municipios, raio_km).merge(
        precos, on=["uf", "chave"], how="left"
    )
    com_preco = caminho.dropna(subset=["preco"])
    if com_preco.empty:
        raise ValueError("nenhum município do caminho tem preço pesquisado pela ANP")
    postos = [
        Posto(f"{m.nome} ({m.uf})", float(m.km), float(m.preco), float(m.desvio_km))
        for m in com_preco.itertuples()
    ]
    # Preço de referência: o do destino, se a ANP pesquisa lá; senão a mediana do caminho.
    fim = caminho.iloc[-1]
    referencia = (
        float(fim["preco"]) if pd.notna(fim["preco"]) else float(com_preco["preco"].median())
    )
    plano = otimizar(postos, rota.distancia_km, caminhao)
    try:
        ingenuo = sem_planejar(postos, rota.distancia_km, caminhao)
    except SemSolucao:  # o motorista sem plano pode não chegar onde o plano chega
        ingenuo = None
        avisos.append("sem um plano, o caminhão não chegaria: os postos pesquisados são esparsos")
    ativas = pracas[pracas["situacao"].str.lower().eq("ativo")] if "situacao" in pracas else pracas
    no_caminho = perto_do_caminho(rota, ativas, RAIO_PEDAGIO_KM)
    avisos.append(
        "Pedágio estimado: só praças federais concedidas (ANTT), com tarifa média por eixo; "
        "rodovias estaduais com pedágio não entram."
    )
    pedagio = Pedagio(no_caminho, eixos, tarifa_eixo)
    return Viagem(rota, caminho, caminhao, plano, ingenuo, pedagio, referencia, avisos)
