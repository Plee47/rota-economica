"""Onde e quanto abastecer: programação dinâmica sobre os postos da rota.

É uma versão do clássico "problema do posto de gasolina". O caminhão sai com algum diesel, passa
por cidades com preços diferentes e precisa chegar ao destino sem baixar da reserva. Abastecer numa
cidade custa o preço dela mais o diesel gasto para ir do trajeto até o centro dela e voltar.

A comparação é com o motorista que não planeja: segue até precisar e enche o tanque no posto que
estiver mais perto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Posto:
    nome: str
    km: float  # posição na rota
    preco: float  # R$/litro
    desvio_km: float = 0.0  # distância do trajeto até o centro da cidade (só ida)


@dataclass(frozen=True)
class Caminhao:
    consumo_km_l: float  # km por litro
    tanque_l: float
    inicial_l: float  # diesel ao sair
    reserva_l: float = 50.0  # nunca rodar abaixo disso

    def litros(self, km: float) -> float:
        return km / self.consumo_km_l


@dataclass
class Plano:
    custo: float  # R$ gastos em diesel comprado na viagem (inclui o desvio até o posto)
    paradas: list[tuple[Posto, float]] = field(default_factory=list)  # (posto, litros)
    chegada_l: float = 0.0  # diesel no tanque ao chegar ao destino

    @property
    def litros(self) -> float:
        return sum(q for _, q in self.paradas)

    def custo_consumido(self, preco_referencia: float) -> float:
        """Gasto menos o diesel que sobra no destino, valorado pelo preço de referência.

        É o que torna dois planos comparáveis: encher o tanque no fim da viagem não é gasto da
        viagem, é diesel para a próxima.
        """
        return self.custo - self.chegada_l * preco_referencia


def simular(paradas: list[tuple[Posto, float]], destino_km: float, c: Caminhao) -> float:
    """Refaz o plano quilômetro a quilômetro e devolve o diesel na chegada.

    Levanta `SemSolucao` se em algum trecho o tanque baixar da reserva ou passar da capacidade.
    """
    tanque, km = c.inicial_l, 0.0
    for p, q in paradas:
        tanque -= c.litros(p.km - km) + c.litros(2 * p.desvio_km)
        if tanque < c.reserva_l - 1e-6:
            raise SemSolucao(f"chega a {p.nome} abaixo da reserva")
        tanque += q
        if tanque > c.tanque_l + 1e-6:
            raise SemSolucao(f"em {p.nome}, {q:.0f} L não cabem no tanque")
        km = p.km
    tanque -= c.litros(destino_km - km)
    if tanque < c.reserva_l - 1e-6:
        raise SemSolucao("chega ao destino abaixo da reserva")
    return tanque


class SemSolucao(ValueError):
    """Nenhum plano chega ao destino respeitando tanque e reserva."""


def otimizar(
    postos: list[Posto], destino_km: float, c: Caminhao, chegada_minima_l: float | None = None
) -> Plano:
    """O plano de menor custo, exato.

    Com custo linear nos litros, um plano ótimo só faz dois tipos de compra em cada parada: enche o
    tanque, ou compra só o necessário para chegar à próxima parada (ou ao destino) na reserva. Isso
    deixa poucos estados possíveis por parada (o diesel ao chegar), e a programação dinâmica sobre
    eles é exata, sem arredondar litros.
    """
    postos = sorted((p for p in postos if p.km <= destino_km), key=lambda p: p.km)
    chegada = c.reserva_l if chegada_minima_l is None else chegada_minima_l
    direto = c.inicial_l - c.litros(destino_km)
    if direto >= chegada - 1e-9:
        return Plano(0.0, [], direto)

    n, tanque = len(postos), c.tanque_l
    desvio = [c.litros(2 * p.desvio_km) for p in postos]
    # estado (parada, diesel ao chegar) -> (custo até aqui, estado anterior, litros comprados nele)
    estados: dict[tuple[int, float], tuple[float, tuple | None, float]] = {}

    def relaxar(j: int, f: float, custo: float, anterior: tuple | None, q: float) -> None:
        chave = (j, round(f, 6))
        if chave not in estados or custo < estados[chave][0] - 1e-9:
            estados[chave] = (custo, anterior, q)

    for j, p in enumerate(postos):  # primeira parada, saindo com o diesel inicial
        f = c.inicial_l - c.litros(p.km)
        if f >= c.reserva_l + desvio[j] - 1e-9:
            relaxar(j, f, 0.0, None, 0.0)

    melhor, final = math.inf, None
    for i, p in enumerate(postos):
        for chave in [k for k in estados if k[0] == i]:
            custo, _, _ = estados[chave]
            g = chave[1] - desvio[i]  # diesel depois de ir até a cidade
            if g < c.reserva_l - 1e-9:
                continue

            def pagar(q: float, _i=i, _p=p, _custo=custo) -> float:
                return _custo + (q + desvio[_i]) * _p.preco

            ate_destino = c.litros(destino_km - p.km)
            q = chegada + ate_destino - g  # só o necessário para o destino
            if 1e-9 < q <= tanque - g + 1e-9 and pagar(q) < melhor:
                melhor, final = pagar(q), (chave, q)
            cheio = tanque - g
            if cheio > 1e-9 and tanque - ate_destino >= chegada - 1e-9 and pagar(cheio) < melhor:
                melhor, final = pagar(cheio), (chave, cheio)
            for j in range(i + 1, n):
                trecho = c.litros(postos[j].km - p.km)
                minimo_j = c.reserva_l + desvio[j]
                if tanque - trecho < minimo_j - 1e-9:
                    break  # nem de tanque cheio chega: as seguintes estão mais longe
                q = minimo_j + trecho - g  # só o necessário para a parada j
                if 1e-9 < q <= cheio + 1e-9:
                    relaxar(j, minimo_j, pagar(q), chave, q)
                if cheio > 1e-9:
                    relaxar(j, tanque - trecho, pagar(cheio), chave, cheio)

    if final is None:
        raise SemSolucao("nenhum plano chega ao destino com a reserva: faltam postos na rota")
    paradas = []
    chave, q = final
    while chave is not None:
        paradas.append((postos[chave[0]], q))
        _, anterior, q_anterior = estados[chave]
        chave, q = anterior, q_anterior
    paradas.reverse()
    return Plano(melhor, paradas, simular(paradas, destino_km, c))


def sem_planejar(postos: list[Posto], destino_km: float, c: Caminhao) -> Plano:
    """O motorista que segue até precisar e enche o tanque no último posto antes de faltar."""
    postos = sorted((p for p in postos if p.km <= destino_km), key=lambda p: p.km)
    tanque, km_atual, gasto, paradas = c.inicial_l, 0.0, 0.0, []
    while tanque - c.litros(destino_km - km_atual) < c.reserva_l - 1e-9:
        alcancaveis = [
            p for p in postos
            if p.km > km_atual and tanque - c.litros(p.km - km_atual) >= c.reserva_l - 1e-9
        ]  # fmt: skip
        if not alcancaveis:
            raise SemSolucao("o motorista sem plano não chega: faltam postos na rota")
        p = alcancaveis[-1]  # o último antes de faltar
        tanque -= c.litros(p.km - km_atual)
        desvio_l = c.litros(2 * p.desvio_km)
        tanque -= desvio_l
        q = c.tanque_l - tanque
        gasto += (q + desvio_l) * p.preco
        tanque, km_atual = c.tanque_l, p.km
        paradas.append((p, q))
    return Plano(gasto, paradas, simular(paradas, destino_km, c))
