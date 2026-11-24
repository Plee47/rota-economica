"""Geometria em coordenadas geográficas (longitude, latitude), sem dependências externas.

As distâncias são em quilômetros. Para ponto contra segmento, usa uma projeção plana local: o erro
é desprezível nas distâncias de interesse aqui (dezenas de quilômetros).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

RAIO_TERRA_KM = 6371.0088
Ponto = tuple[float, float]  # (longitude, latitude)


def haversine_km(a: Ponto, b: Ponto) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * RAIO_TERRA_KM * math.asin(math.sqrt(h))


def _plano(p: Ponto, lat0: float) -> tuple[float, float]:
    """Projeção equiretangular em km, centrada na latitude `lat0`."""
    k = math.pi / 180 * RAIO_TERRA_KM
    return p[0] * k * math.cos(math.radians(lat0)), p[1] * k


def ponto_no_segmento(p: Ponto, a: Ponto, b: Ponto) -> tuple[float, float]:
    """Distância (km) de `p` ao segmento a–b e a fração (0 a 1) do ponto mais próximo nele."""
    lat0 = (a[1] + b[1] + p[1]) / 3
    (px, py), (ax, ay), (bx, by) = _plano(p, lat0), _plano(a, lat0), _plano(b, lat0)
    dx, dy = bx - ax, by - ay
    comprimento2 = dx * dx + dy * dy
    t = (
        0.0
        if comprimento2 == 0
        else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / comprimento2))
    )
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), t


def acumulado_km(linha: Sequence[Ponto]) -> list[float]:
    """Quilômetro de cada vértice da linha, a partir do primeiro."""
    km = [0.0]
    for a, b in zip(linha, linha[1:], strict=False):
        km.append(km[-1] + haversine_km(a, b))
    return km


def projetar(p: Ponto, linha: Sequence[Ponto], km: Sequence[float]) -> tuple[float, float]:
    """(distância do ponto até a linha, quilômetro da linha onde ele fica mais perto)."""
    melhor = (math.inf, 0.0)
    for i, (a, b) in enumerate(zip(linha, linha[1:], strict=False)):
        d, t = ponto_no_segmento(p, a, b)
        if d < melhor[0]:
            melhor = (d, km[i] + t * (km[i + 1] - km[i]))
    return melhor


def _centroide_anel(anel: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    """(área com sinal, x, y) do anel, pela fórmula do polígono (em graus: basta para comparar)."""
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(anel, anel[1:], strict=False):
        cruz = x0 * y1 - x1 * y0
        area += cruz
        cx += (x0 + x1) * cruz
        cy += (y0 + y1) * cruz
    area /= 2
    if area == 0:
        xs, ys = zip(*anel, strict=True)
        return 0.0, sum(xs) / len(xs), sum(ys) / len(ys)
    return area, cx / (6 * area), cy / (6 * area)


def centroide(geometria: dict) -> Ponto:
    """Centro de um Polygon ou MultiPolygon GeoJSON (o do maior pedaço, para ilhas)."""
    if geometria["type"] == "Polygon":
        poligonos = [geometria["coordinates"]]
    elif geometria["type"] == "MultiPolygon":
        poligonos = geometria["coordinates"]
    else:
        raise ValueError(f"geometria não suportada: {geometria['type']}")
    candidatos = [_centroide_anel(p[0]) for p in poligonos]
    _, x, y = max(candidatos, key=lambda c: abs(c[0]))
    return x, y
