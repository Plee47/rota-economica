"""Os municípios por onde a rota passa, com o quilômetro de cada um.

Um município entra se o centro dele fica a até `raio_km` do trajeto. O raio é a distância que um
caminhão aceitaria desviar para abastecer; o desvio entra depois no custo da parada.
"""

from __future__ import annotations

import pandas as pd

from rota_economica.rota.geo import acumulado_km, haversine_km, projetar
from rota_economica.rota.provedor import Rota

RAIO_KM = 15.0


def _caixa(rota: Rota, margem_graus: float) -> tuple[float, float, float, float]:
    lons, lats = zip(*rota.trajeto, strict=True)
    return (min(lons) - margem_graus, max(lons) + margem_graus,
            min(lats) - margem_graus, max(lats) + margem_graus)  # fmt: skip


def simplificar(trajeto: list[tuple[float, float]], passo_km: float = 0.5) -> list:
    """Mantém um vértice a cada `passo_km`: o OSRM devolve milhares, e a conta fica lenta à toa."""
    if len(trajeto) <= 2:
        return list(trajeto)
    saida, km = [trajeto[0]], 0.0
    for a, b in zip(trajeto, trajeto[1:], strict=False):
        km += haversine_km(a, b)
        if km >= passo_km:
            saida.append(b)
            km = 0.0
    if saida[-1] != trajeto[-1]:
        saida.append(trajeto[-1])
    return saida


def perto_do_caminho(rota: Rota, pontos: pd.DataFrame, raio_km: float) -> pd.DataFrame:
    """Linhas de `pontos` (colunas lon e lat) a até `raio_km` do trajeto, com `km` e `desvio_km`."""
    # Filtro rápido por caixa antes da conta de distância.
    x0, x1, y0, y1 = _caixa(rota, raio_km / 111 + 0.05)
    perto = pontos[pontos["lon"].between(x0, x1) & pontos["lat"].between(y0, y1)]
    linha = simplificar(rota.trajeto)
    km = acumulado_km(linha)
    linhas = []
    for _, m in perto.iterrows():
        desvio, posicao = projetar((m["lon"], m["lat"]), linha, km)
        if desvio <= raio_km:
            linhas.append({**m.to_dict(), "km": round(posicao, 1), "desvio_km": round(desvio, 1)})
    colunas = [*pontos.columns, "km", "desvio_km"]
    return pd.DataFrame(linhas, columns=colunas).sort_values("km", ignore_index=True)


def municipios_no_caminho(
    rota: Rota, municipios: pd.DataFrame, raio_km: float = RAIO_KM
) -> pd.DataFrame:
    """Municípios a até `raio_km` do trajeto, na ordem em que aparecem, com `km` e `desvio_km`."""
    return perto_do_caminho(rota, municipios, raio_km)
