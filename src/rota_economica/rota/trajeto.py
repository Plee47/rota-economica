"""Os municípios por onde a rota passa, com o quilômetro de cada um.

Um município entra se o centro dele fica a até `raio_km` do trajeto. O raio é a distância que um
caminhão aceitaria desviar para abastecer; o desvio entra depois no custo da parada.
"""

from __future__ import annotations

import pandas as pd

from rota_economica.rota.geo import acumulado_km, projetar
from rota_economica.rota.provedor import Rota

RAIO_KM = 15.0


def _caixa(rota: Rota, margem_graus: float) -> tuple[float, float, float, float]:
    lons, lats = zip(*rota.trajeto, strict=True)
    return (min(lons) - margem_graus, max(lons) + margem_graus,
            min(lats) - margem_graus, max(lats) + margem_graus)  # fmt: skip


def municipios_no_caminho(
    rota: Rota, municipios: pd.DataFrame, raio_km: float = RAIO_KM
) -> pd.DataFrame:
    """Municípios a até `raio_km` do trajeto, na ordem em que aparecem, com `km` e `desvio_km`."""
    # Filtro rápido por caixa antes da conta de distância (0,2 grau ≈ 22 km).
    x0, x1, y0, y1 = _caixa(rota, raio_km / 111 + 0.05)
    perto = municipios[municipios["lon"].between(x0, x1) & municipios["lat"].between(y0, y1)]
    km = acumulado_km(rota.trajeto)
    linhas = []
    for _, m in perto.iterrows():
        desvio, posicao = projetar((m["lon"], m["lat"]), rota.trajeto, km)
        if desvio <= raio_km:
            linhas.append({**m.to_dict(), "km": round(posicao, 1), "desvio_km": round(desvio, 1)})
    colunas = [*municipios.columns, "km", "desvio_km"]
    return pd.DataFrame(linhas, columns=colunas).sort_values("km", ignore_index=True)
