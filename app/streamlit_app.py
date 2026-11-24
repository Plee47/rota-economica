"""Rota Econômica: o site.

Lê os arquivos agregados de `dados/site/` (exportados pelo job do Databricks) e calcula a rota na
hora pelo OSRM, com um segundo servidor como reserva. Não acessa o Databricks: funciona com os
últimos dados sincronizados.
"""

from __future__ import annotations

import html
import json
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))  # o Streamlit Cloud roda o arquivo sem instalar o pacote

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import pydeck as pdk  # noqa: E402
import streamlit as st  # noqa: E402

from rota_economica.custo.abastecimento import Caminhao, SemSolucao  # noqa: E402
from rota_economica.custo.viagem import (  # noqa: E402
    TARIFA_EIXO_PADRAO,
    Viagem,
    calcular,
    precos_por_municipio,
)
from rota_economica.http import ClienteHTTP, ErroHTTP  # noqa: E402
from rota_economica.rota.provedor import provedor_padrao  # noqa: E402

# Configuráveis para os testes rodarem o site com dados sintéticos e sem internet.
DADOS = Path(os.environ.get("ROTA_DADOS", RAIZ / "dados" / "site"))
CACHE_ROTAS = Path(os.environ.get("ROTA_CACHE", Path(tempfile.gettempdir()) / "rota-economica"))
TIPOS = {"S10": "Diesel S10", "comum": "Diesel comum (S500)"}
DIAS_DADOS_VELHOS = 14
EXEMPLO = {"origem": ("SP", "São Paulo"), "destino": ("BA", "Salvador")}

st.set_page_config(
    page_title="Rota Econômica", page_icon=":material/local_shipping:", layout="wide"
)
st.html(f"<style>{(Path(__file__).parent / 'estilo.css').read_text(encoding='utf-8')}</style>")


# --------------------------------------------------------------------------- dados
@st.cache_data
def carregar(pasta: str, versao: float) -> dict:
    """Os arquivos do site. `versao` (data do arquivo mais novo) renova o cache após sincronizar."""
    base = Path(pasta)
    obrigatorios = ("precos_municipio.parquet", "municipios.parquet", "pracas.parquet")
    faltam = [n for n in obrigatorios if not (base / n).exists()]
    if faltam:
        return {"faltam": faltam}

    def opcional(nome: str):
        caminho = base / nome
        if not caminho.exists():
            return None
        if nome.endswith(".json"):
            return json.loads(caminho.read_text(encoding="utf-8"))
        return pd.read_parquet(caminho)

    return {
        "municipio": pd.read_parquet(base / "precos_municipio.parquet"),
        "municipios": pd.read_parquet(base / "municipios.parquet"),
        "pracas": pd.read_parquet(base / "pracas.parquet"),
        "uf": opcional("precos_uf.parquet"),
        "previsoes": opcional("previsoes.parquet"),
        "modelo": opcional("modelo.json"),
    }


@st.cache_resource
def rotas():
    return provedor_padrao(ClienteHTTP(timeout=30.0, tentativas=3), CACHE_ROTAS)


def numero(v: float, casas: int = 2) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def reais(v: float) -> str:
    return f"R$ {numero(v)}"


def e(texto: object) -> str:
    return html.escape(str(texto))


# --------------------------------------------------------------------------- cabeçalho
versao_dados = max((a.stat().st_mtime for a in DADOS.glob("*")), default=0.0)
dados = carregar(str(DADOS), versao_dados)
if "faltam" in dados:
    st.html('<div class="marca"><span class="nome">Rota Econômica</span></div>')
    st.error(
        "Os dados do site ainda não foram sincronizados. Rode "
        "`python scripts/sincronizar_site.py` com o Databricks CLI autenticado "
        f"(faltam: {', '.join(dados['faltam'])})."
    )
    st.stop()

semana_dados = pd.to_datetime(dados["municipio"]["semana"]).max().date()
dias = (date.today() - semana_dados).days
st.html(
    '<div class="marca"><span class="nome">Rota Econômica</span>'
    f'<span class="placa">ANP · semana de {semana_dados:%d/%m/%Y}</span></div>'
    '<p class="lema">Quanto custa a viagem do seu caminhão e onde abastecer para gastar menos, '
    "com o preço do diesel pesquisado pela ANP em cada cidade do caminho.</p>"
)
if dias > DIAS_DADOS_VELHOS:
    st.warning(
        f"Os preços são da semana de {semana_dados:%d/%m/%Y}, há {dias} dias. A coleta semanal "
        "pode ter falhado: os valores servem de referência, não de cotação."
    )

municipios = dados["municipios"].sort_values(["uf", "nome"])
ufs = sorted(municipios["uf"].unique())


# --------------------------------------------------------------------------- entrada
def escolher_cidade(rotulo: str, padrao: tuple[str, str], chave: str) -> pd.Series:
    uf_padrao, cidade_padrao = padrao
    c1, c2 = st.columns([1, 3])
    uf = c1.selectbox(
        f"UF de {rotulo.lower()}", ufs, index=ufs.index(uf_padrao) if uf_padrao in ufs else 0,
        key=f"uf_{chave}",
    )  # fmt: skip
    nomes = municipios.loc[municipios["uf"] == uf, "nome"].tolist()
    indice = nomes.index(cidade_padrao) if cidade_padrao in nomes else 0
    nome = c2.selectbox(rotulo, nomes, index=indice, key=f"cidade_{chave}")
    return municipios[(municipios["uf"] == uf) & (municipios["nome"] == nome)].iloc[0]


with st.sidebar:
    st.subheader("Viagem")
    origem = escolher_cidade("Origem", EXEMPLO["origem"], "origem")
    destino = escolher_cidade("Destino", EXEMPLO["destino"], "destino")
    tipo = st.radio("Diesel", list(TIPOS), format_func=TIPOS.get, horizontal=True)
    st.subheader("Caminhão")
    c1, c2 = st.columns(2)
    consumo = c1.number_input("Consumo (km/L)", 1.0, 6.0, 2.5, 0.1, format="%.1f")
    tanque = c2.number_input("Tanque (L)", 100, 2000, 600, 50)
    inicial = c1.number_input("Diesel na saída (L)", 0, 2000, 300, 10)
    reserva = c2.number_input("Reserva mínima (L)", 0, 500, 60, 10)
    eixos = c1.number_input("Eixos", 2, 9, 6)
    tarifa = c2.number_input(
        "Pedágio por eixo (R$)", 0.0, 50.0, TARIFA_EIXO_PADRAO, 0.5, format="%.2f",
        help="Por praça. É uma estimativa: a ANTT não publica as tarifas como dado aberto.",
    )  # fmt: skip
    calcular_agora = st.button(
        "Calcular a viagem", type="primary", width="stretch", icon=":material/route:"
    )


def problemas_do_caminhao() -> list[str]:
    p = []
    if reserva >= tanque:
        p.append("A reserva mínima precisa ser menor que o tanque.")
    if inicial > tanque:
        p.append("O diesel na saída não cabe no tanque.")
    if inicial < reserva:
        p.append("O caminhão sai abaixo da reserva: aumente o diesel na saída ou baixe a reserva.")
    if origem["codigo"] == destino["codigo"]:
        p.append("Origem e destino são a mesma cidade.")
    return p


aba_viagem, aba_preco, aba_modelo, aba_sobre = st.tabs(
    ["Viagem", "Preço do diesel", "Modelo em produção", "Sobre os dados"]
)


# --------------------------------------------------------------------------- viagem
def nota_html(v: Viagem) -> str:
    r = v.rota
    linhas = [
        ("Distância", f"{numero(r.distancia_km, 0)} km",
         f"{numero(r.duracao_h, 1)} h ao volante, sem paradas"),
        (f"{TIPOS[tipo]} consumido", reais(v.custo_diesel),
         f"{numero(v.litros_consumidos, 0)} L a {numero(consumo, 1)} km/L"),
        ("Pedágio estimado", reais(v.pedagio.custo),
         f"{len(v.pedagio.pracas)} praças federais × {eixos} eixos"),
    ]  # fmt: skip
    corpo = "".join(
        f"<div><dt>{e(a)}<small>{e(c)}</small></dt><dd>{e(b)}</dd></div>" for a, b, c in linhas
    )
    corpo += f'<div class="total"><dt>Total</dt><dd>{e(reais(v.custo_total))}</dd></div>'
    corpo += f"<div><dt>Custo por km</dt><dd>{e(reais(v.custo_km))}</dd></div>"
    economia = ""
    if v.economia is not None and v.economia > 0.5:
        economia = (
            '<p class="economia">Abastecendo onde o plano indica, você gasta '
            f"<strong>{e(reais(v.economia))} a menos</strong> em diesel do que enchendo o tanque "
            "no primeiro posto sempre que precisa.</p>"
        )
    elif v.economia is not None:
        economia = (
            '<p class="economia">Nesta viagem os preços do caminho são parecidos: abastecer onde '
            "o plano indica sai praticamente igual a abastecer quando precisar.</p>"
        )
    trecho = (
        f"{e(origem['nome'])} <span>({e(origem['uf'])})</span> até "
        f"{e(destino['nome'])} <span>({e(destino['uf'])})</span>"
    )
    return (
        f'<section class="nota"><p class="trecho">{trecho}</p><dl>{corpo}</dl>{economia}</section>'
    )


def paradas_html(v: Viagem) -> str:
    itens = []
    for p, q in v.plano.paradas:
        desvio = f", {numero(p.desvio_km, 1)} km fora da rota" if p.desvio_km >= 1 else ""
        itens.append(
            f'<li><span class="km">km<small>{numero(p.km, 0)}</small></span>'
            f'<span><span class="cidade">{e(p.nome)}</span><br>'
            f'<span class="detalhe">R$ {numero(p.preco, 3)}/L{e(desvio)}</span></span>'
            f'<span class="valor">{numero(q, 0)} L'
            f"<small>{e(reais(q * p.preco))}</small></span></li>"
        )
    return f'<ol class="paradas">{"".join(itens)}</ol>'


def mapa(v: Viagem) -> pdk.Deck:
    com_preco = v.caminho.dropna(subset=["preco"]).copy()
    com_preco["rotulo"] = com_preco.apply(
        lambda r: f"{r['nome']} ({r['uf']}): R$ {numero(r['preco'], 3)}/L", axis=1
    )
    # Divergente em torno da mediana do caminho: verde-azulado mais barato, laranja mais caro.
    mediana = com_preco["preco"].median()
    amplitude = max((com_preco["preco"] - mediana).abs().max(), 0.01)
    barato, meio, caro = (28, 124, 116), (154, 163, 159), (194, 65, 12)

    def cor(preco: float) -> list[int]:
        t = (preco - mediana) / amplitude
        alvo = caro if t > 0 else barato
        a = min(abs(t), 1.0)
        return [round(m + (c - m) * a) for m, c in zip(meio, alvo, strict=True)] + [230]

    com_preco["cor"] = com_preco["preco"].map(cor)
    nomes_paradas = {p.nome for p, _ in v.plano.paradas}
    com_preco["parada"] = (com_preco["nome"] + " (" + com_preco["uf"] + ")").isin(nomes_paradas)
    pracas = v.pedagio.pracas.assign(rotulo="Pedágio: " + v.pedagio.pracas["praca"].astype(str))
    camadas = [
        pdk.Layer("PathLayer", [{"caminho": [list(p) for p in v.rota.trajeto]}],
                  get_path="caminho", get_color=[24, 33, 29, 200], width_min_pixels=4),
        pdk.Layer("ScatterplotLayer", com_preco, get_position=["lon", "lat"], get_fill_color="cor",
                  get_line_color=[255, 255, 255], stroked=True, line_width_min_pixels=1,
                  radius_min_pixels=5, radius_max_pixels=9, get_radius=5000, pickable=True),
        pdk.Layer("ScatterplotLayer", com_preco[com_preco["parada"]],
                  get_position=["lon", "lat"], filled=False, stroked=True,
                  get_line_color=[14, 107, 69], line_width_min_pixels=3,
                  radius_min_pixels=11, get_radius=12000),
        pdk.Layer("ScatterplotLayer", pracas, get_position=["lon", "lat"],
                  get_fill_color=[227, 162, 26, 240], get_line_color=[24, 33, 29],
                  stroked=True, line_width_min_pixels=1, radius_min_pixels=4, get_radius=2500,
                  pickable=True),
    ]  # fmt: skip
    trajeto = pd.DataFrame(v.rota.trajeto, columns=["lon", "lat"])
    extensao = max(
        trajeto["lon"].max() - trajeto["lon"].min(), trajeto["lat"].max() - trajeto["lat"].min()
    )
    zoom = 7.5 if extensao < 2 else 6 if extensao < 5 else 5 if extensao < 10 else 4
    return pdk.Deck(
        layers=camadas,
        initial_view_state=pdk.ViewState(
            longitude=float(trajeto["lon"].mean()), latitude=float(trajeto["lat"].mean()), zoom=zoom
        ),
        tooltip={"text": "{rotulo}"},
        map_provider="carto",
        map_style=pdk.map_styles.CARTO_LIGHT,
    )


def vale_esperar(v: Viagem) -> str | None:
    if dados["previsoes"] is None:
        return None
    prev = dados["previsoes"]
    ufs_rota = list(dict.fromkeys(v.caminho["uf"]))
    prox = prev[(prev["horizonte"] == 1) & prev["serie"].isin([f"{u}|{tipo}" for u in ufs_rota])]
    if prox.empty:
        return None
    pedacos = []
    for u in ufs_rota:
        linha = prox[prox["serie"] == f"{u}|{tipo}"]
        if linha.empty:
            continue
        r = linha.iloc[0]
        var = r["previsao"] / r["preco_atual"] - 1
        classe = "sobe" if var > 0.001 else "cai" if var < -0.001 else ""
        verbo = "sobe" if var > 0.001 else "cai" if var < -0.001 else "estável"
        valor = f" {numero(abs(var) * 100, 1)}%" if classe else ""
        pedacos.append(f'<span><b>{e(u)}</b><span class="{classe}">{verbo}{valor}</span></span>')
    return f'<div class="esperar">{"".join(pedacos)}</div>'


with aba_viagem:
    pedido = (origem["codigo"], destino["codigo"], tipo, consumo, tanque, inicial, reserva,
              eixos, tarifa)  # fmt: skip
    if calcular_agora:
        st.session_state["pedido"] = pedido
    problemas = problemas_do_caminhao()
    if st.session_state.get("pedido") != pedido:
        st.html(
            '<div class="vazio"><p><b>Escolha a viagem e o caminhão ao lado.</b></p>'
            "<p>O cálculo traça a rota, encontra as cidades do caminho onde a ANP pesquisa o "
            "preço do diesel e monta o plano de abastecimento mais barato que respeita o tanque e "
            "a reserva.</p></div>"
        )
        if st.button("Ver um exemplo: São Paulo até Salvador", icon=":material/play_arrow:"):
            st.session_state["pedido"] = pedido
            st.rerun()
    elif problemas:
        for p in problemas:
            st.warning(p)
    else:
        caminhao = Caminhao(float(consumo), float(tanque), float(inicial), float(reserva))
        precos = precos_por_municipio(dados["municipio"], tipo)
        try:
            with st.spinner("Traçando a rota e montando o plano de abastecimento…"):
                rota = rotas().rota(
                    [(origem["lon"], origem["lat"]), (destino["lon"], destino["lat"])]
                )
                v = calcular(
                    rota, municipios, precos, dados["pracas"], caminhao, int(eixos), tarifa
                )
        except ErroHTTP:
            st.error(
                "Os serviços de rota (OSRM) não responderam agora. Tente de novo em alguns "
                "segundos: as rotas já calculadas ficam guardadas e abrem na hora."
            )
            st.stop()
        except SemSolucao:
            st.error(
                "Com este tanque e esta reserva, nenhum plano chega ao destino: entre duas "
                "cidades pesquisadas pela ANP a distância passa da autonomia do caminhão. "
                "Aumente o tanque ou o diesel na saída, ou baixe a reserva."
            )
            st.stop()
        except ValueError as erro:
            st.error(f"Não foi possível calcular esta viagem: {erro}.")
            st.stop()

        esquerda, direita = st.columns([2, 3], gap="large")
        with esquerda:
            st.html(nota_html(v))
            st.subheader("Onde abastecer")
            if v.plano.paradas:
                st.html(paradas_html(v))
            else:
                st.write("O diesel da saída basta para a viagem: nenhuma parada necessária.")
            esperar = vale_esperar(v)
            if esperar:
                st.subheader("O diesel na próxima semana")
                st.html(esperar)
                st.caption(
                    "Previsão do modelo em produção para a mediana de cada estado da rota. "
                    "Variações menores que a faixa de erro da aba Modelo não justificam adiar."
                )
        with direita:
            st.pydeck_chart(mapa(v), height=560)
            st.html(
                '<div class="legenda"><span><i class="faixa"></i>preço do diesel: mais barato '
                'a mais caro</span><span><i style="border:3px solid #0e6b45"></i>parada '
                'recomendada</span><span><i style="background:#e3a21a"></i>praça de pedágio'
                "</span></div>"
            )
            st.caption(
                f"Preço: mediana dos postos pesquisados na semana de {semana_dados:%d/%m/%Y}. "
                + " ".join(v.avisos)
            )

# --------------------------------------------------------------------------- preço
with aba_preco:
    if dados["uf"] is None:
        st.info("O histórico por estado ainda não foi sincronizado.")
    else:
        c1, c2 = st.columns([1, 2])
        uf_sel = c1.selectbox("Estado", ufs, index=ufs.index("SP"), key="uf_grafico")
        anos = c2.select_slider(
            "Período", options=[1, 2, 3, 5, 10, 20], value=2,
            format_func=lambda a: f"{a} {'ano' if a == 1 else 'anos'}",
        )  # fmt: skip
        h = dados["uf"].copy()
        h["semana"] = pd.to_datetime(h["semana"])
        h = h[(h["uf"] == uf_sel) & (h["tipo_diesel"] == tipo)]
        h = h[h["semana"] >= h["semana"].max() - pd.DateOffset(years=anos)]
        if h.empty:
            st.info(f"A ANP não tem série de {TIPOS[tipo]} em {uf_sel} neste período.")
        else:
            eixo_y = alt.Y("preco_mediano:Q", title="R$/L", scale=alt.Scale(zero=False))
            linha = alt.Chart(h).mark_line(strokeWidth=2, color="#18211d").encode(
                x=alt.X("semana:T", title=None), y=eixo_y,
                tooltip=[alt.Tooltip("semana:T", title="Semana", format="%d/%m/%Y"),
                         alt.Tooltip("preco_mediano:Q", title="R$/L", format=".3f")],
            )  # fmt: skip
            grafico = linha
            p = dados["previsoes"]
            if p is not None:
                p = p[p["serie"] == f"{uf_sel}|{tipo}"].copy()
            if p is not None and not p.empty:
                p["semana_alvo"] = pd.to_datetime(p["semana_alvo"])
                faixa = (
                    alt.Chart(p)
                    .mark_area(opacity=0.22, color="#0e6b45")
                    .encode(x="semana_alvo:T", y="p10:Q", y2="p90:Q")
                )
                prev = alt.Chart(p).mark_line(
                    strokeDash=[5, 3], point=True, color="#0e6b45"
                ).encode(
                    x="semana_alvo:T", y="previsao:Q",
                    tooltip=[alt.Tooltip("semana_alvo:T", title="Semana", format="%d/%m/%Y"),
                             alt.Tooltip("previsao:Q", title="Previsão", format=".3f"),
                             alt.Tooltip("p10:Q", title="Mínimo provável", format=".3f"),
                             alt.Tooltip("p90:Q", title="Máximo provável", format=".3f")],
                )  # fmt: skip
                grafico = linha + faixa + prev
            st.altair_chart(grafico.properties(height=380), width="stretch")
            st.caption(
                f"{TIPOS[tipo]} em {uf_sel}: mediana semanal dos postos pesquisados (linha escura) "
                "e previsão das próximas 4 semanas (verde tracejado), com a faixa onde o preço "
                "deve ficar em 80% dos casos."
            )

# --------------------------------------------------------------------------- modelo
with aba_modelo:
    mod = dados["modelo"]
    if mod is None:
        st.info("As métricas do modelo ainda não foram sincronizadas.")
    else:
        b = mod["backtest"]
        mon = mod["monitoramento"]
        (st.error if mon["alerta"] else st.success)(f"Monitoramento: {mon['mensagem']}.")
        st.write(
            f"Em produção: **{mod['campeao']}**, com dados até a semana de "
            f"{pd.Timestamp(mod['semana_dados']):%d/%m/%Y}. Última decisão de promoção: "
            f"{mod['decisao']['motivo']}."
        )
        c = st.columns(3)
        c[0].metric("Erro médio do modelo", f"R$ {numero(b['gradiente']['mae'], 3)}/L")
        c[1].metric(
            "Erro da base ingênua", f"R$ {numero(b['ingenuo']['mae'], 3)}/L",
            help="A base ingênua repete o preço atual. Bater ela é o mínimo.",
        )  # fmt: skip
        c[2].metric(
            "Preço real dentro do intervalo", f"{b['gradiente']['cobertura']:.0%}",
            help="O intervalo de previsão foi desenhado para conter 80% dos casos.",
        )  # fmt: skip
        horiz = pd.DataFrame(mod["por_horizonte"]).T
        horiz.index = [f"{h} semana{'s' if h != '1' else ''} à frente" for h in horiz.index]
        st.dataframe(
            horiz[["mae", "mape", "cobertura"]]
            .rename(
                columns={"mae": "Erro médio (R$/L)", "mape": "Erro %", "cobertura": "No intervalo"}
            )
            .style.format(
                {"Erro médio (R$/L)": "{:.3f}", "Erro %": "{:.2%}", "No intervalo": "{:.0%}"}
            ),  # fmt: skip
            width="stretch",
        )
        st.caption(
            "Backtest com origem móvel nas últimas 52 semanas: em cada semana, o modelo foi "
            "treinado só com o que já tinha acontecido. Toda semana o job retreina, compara com o "
            "modelo em produção e só promove o novo se ele errar pelo menos 2% menos."
        )

# --------------------------------------------------------------------------- sobre
with aba_sobre:
    st.markdown(
        """
**Preços:** Levantamento de Preços de Combustíveis da ANP, de 2004 até hoje, coletado toda
semana por um job no Databricks. O preço de cada cidade é a mediana dos postos pesquisados.
A ANP pesquisa cerca de 400 municípios, então só eles aparecem como opção de parada.

**Rotas:** OSRM, com o servidor público do projeto OSRM e o da FOSSGIS como reserva, sobre dados
do OpenStreetMap (© colaboradores do OpenStreetMap). A rota é a de carro: restrições de
caminhão, como altura e peso, não entram.

**Pedágios:** praças das rodovias federais concedidas (ANTT). A tarifa é uma estimativa por eixo,
editável, porque a ANTT não publica as tarifas como dado aberto. Rodovias estaduais com pedágio
não entram.

**Municípios:** IBGE, APIs de localidades e de malhas.

**Limitações:** o consumo é fixo, sem efeito de carga, relevo ou trânsito. O piso mínimo de frete
da ANTT ainda não entra no cálculo.

Código, testes e decisões de arquitetura:
[github.com/Plee47/rota-economica](https://github.com/Plee47/rota-economica).
"""
    )
