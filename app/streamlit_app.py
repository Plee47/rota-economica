"""Rota Econômica: o site.

Lê os arquivos agregados de `dados/site/` (exportados pelo job do Databricks) e calcula a rota na
hora pelo OSRM. Não acessa o Databricks: funciona com os últimos dados sincronizados.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
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
    calcular,
    precos_por_municipio,
)
from rota_economica.http import ClienteHTTP, ErroHTTP  # noqa: E402
from rota_economica.rota.provedor import OSRM, ComCache  # noqa: E402

# Configuráveis para os testes rodarem o site com dados sintéticos e sem internet.
DADOS = Path(os.environ.get("ROTA_DADOS", RAIZ / "dados" / "site"))
CACHE_ROTAS = Path(
    os.environ.get("ROTA_CACHE", Path(tempfile.gettempdir()) / "rota-economica-rotas")
)
TIPOS = {"S10": "Diesel S10", "comum": "Diesel comum (S500)"}

st.set_page_config(
    page_title="Rota Econômica", page_icon=":material/local_shipping:", layout="wide"
)


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
def provedor() -> ComCache:
    return ComCache(OSRM(ClienteHTTP()), CACHE_ROTAS)


def reais(v: float) -> str:
    texto = f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def litros(v: float) -> str:
    return f"{v:,.0f} L".replace(",", ".")


# --------------------------------------------------------------------------- entrada
versao_dados = max((a.stat().st_mtime for a in DADOS.glob("*")), default=0.0)
dados = carregar(str(DADOS), versao_dados)
st.title("Rota Econômica")
st.caption(
    "Informe a rota e o caminhão: o custo da viagem e onde abastecer para gastar menos, com o "
    "preço do diesel pesquisado pela ANP e uma previsão para as próximas semanas."
)
if "faltam" in dados:
    st.error(
        "Os dados do site ainda não foram sincronizados. Rode `python scripts/sincronizar_site.py` "
        f"(faltam: {', '.join(dados['faltam'])})."
    )
    st.stop()

municipios = dados["municipios"].sort_values(["uf", "nome"])
ufs = sorted(municipios["uf"].unique())
semana_dados = pd.to_datetime(dados["municipio"]["semana"]).max().date()


def escolher_cidade(rotulo: str, uf_padrao: str, cidade_padrao: str, chave: str) -> pd.Series:
    c1, c2 = st.columns([1, 3])
    uf = c1.selectbox(f"UF ({rotulo})", ufs, index=ufs.index(uf_padrao), key=f"uf_{chave}")
    nomes = municipios.loc[municipios["uf"] == uf, "nome"].tolist()
    indice = nomes.index(cidade_padrao) if cidade_padrao in nomes else 0
    nome = c2.selectbox(rotulo, nomes, index=indice, key=f"cidade_{chave}")
    return municipios[(municipios["uf"] == uf) & (municipios["nome"] == nome)].iloc[0]


with st.sidebar:
    st.header("Viagem")
    origem = escolher_cidade("Origem", "SP", "São Paulo", "origem")
    destino = escolher_cidade("Destino", "BA", "Salvador", "destino")
    tipo = st.radio("Diesel", list(TIPOS), format_func=TIPOS.get, horizontal=True)
    st.header("Caminhão")
    consumo = st.number_input("Consumo (km/L)", 1.0, 6.0, 2.5, 0.1)
    tanque = st.number_input("Tanque (L)", 100, 2000, 600, 50)
    inicial = st.number_input("Diesel na saída (L)", 0, 2000, 300, 10)
    reserva = st.number_input("Reserva mínima (L)", 0, 500, 60, 10)
    eixos = st.number_input("Eixos", 2, 9, 6)
    tarifa = st.number_input(
        "Pedágio por eixo, por praça (R$)", 0.0, 50.0, TARIFA_EIXO_PADRAO, 0.5,
        help="Estimativa: a ANTT não publica as tarifas como dado aberto.",
    )  # fmt: skip
    calcular_agora = st.button("Calcular rota", type="primary", width="stretch")

if inicial > tanque:
    st.sidebar.error("O diesel na saída não cabe no tanque.")
    st.stop()

aba_viagem, aba_preco, aba_modelo, aba_sobre = st.tabs(
    ["Viagem", "Preço do diesel", "Modelo em produção", "Sobre os dados"]
)

# --------------------------------------------------------------------------- viagem
with aba_viagem:
    pedido = (origem["codigo"], destino["codigo"], tipo, consumo, tanque, inicial, reserva,
              eixos, tarifa)  # fmt: skip
    if calcular_agora:
        st.session_state["pedido"] = pedido
    if st.session_state.get("pedido") != pedido:
        st.info("Escolha a viagem ao lado e clique em **Calcular rota**.")
    elif origem["codigo"] == destino["codigo"]:
        st.warning("Origem e destino são a mesma cidade.")
    else:
        caminhao = Caminhao(consumo, float(tanque), float(inicial), float(reserva))
        precos = precos_por_municipio(dados["municipio"], tipo)
        try:
            with st.spinner("Traçando a rota e calculando onde abastecer…"):
                rota = provedor().rota(
                    [(origem["lon"], origem["lat"]), (destino["lon"], destino["lat"])]
                )
                v = calcular(
                    rota, municipios, precos, dados["pracas"], caminhao, int(eixos), tarifa
                )
        except ErroHTTP:
            st.error("O serviço de rotas (OSRM) não respondeu. Tente de novo em alguns segundos.")
            st.stop()
        except (SemSolucao, ValueError) as e:
            st.error(f"Não há plano possível para esta viagem: {e}.")
            st.stop()

        m = st.columns(5)
        m[0].metric("Distância", f"{rota.distancia_km:,.0f} km".replace(",", "."))
        m[1].metric("Tempo ao volante", f"{rota.duracao_h:.1f} h".replace(".", ","))
        m[2].metric("Diesel consumido", reais(v.custo_diesel), litros(v.litros_consumidos))
        m[3].metric("Pedágio estimado", reais(v.pedagio.custo), f"{len(v.pedagio.pracas)} praças")
        m[4].metric("Custo por km", reais(v.custo_km))
        if v.economia is not None:
            st.success(
                f"**Abastecendo como o plano indica, a viagem economiza {reais(v.economia)}** "
                "em diesel, contra encher o tanque no posto mais próximo sempre que precisar."
            )

        paradas = pd.DataFrame(
            [
                {"Parada": p.nome, "Km": round(p.km), "Preço (R$/L)": p.preco,
                 "Litros": round(q), "Valor": reais(q * p.preco),
                 "Desvio até a cidade": f"{p.desvio_km:.1f} km".replace(".", ",")}
                for p, q in v.plano.paradas
            ]
        )  # fmt: skip
        c_mapa, c_tabela = st.columns([3, 2])
        with c_tabela:
            st.subheader("Onde abastecer")
            if paradas.empty:
                st.write("O diesel da saída basta: nenhuma parada necessária.")
            else:
                st.dataframe(paradas, hide_index=True, width="stretch")
            st.caption(
                f"Preços: mediana dos postos pesquisados pela ANP na semana de "
                f"{semana_dados:%d/%m/%Y}, só nas cidades que a ANP pesquisa."
            )
            for aviso in v.avisos:
                st.caption(aviso)

        with c_mapa:
            com_preco = v.caminho.dropna(subset=["preco"]).copy()
            com_preco["rotulo"] = com_preco.apply(
                lambda r: f"{r['nome']} ({r['uf']}): R$ {r['preco']:.3f}/L".replace(".", ","),
                axis=1,
            )
            pmin, pmax = com_preco["preco"].min(), com_preco["preco"].max()
            escala = (com_preco["preco"] - pmin) / ((pmax - pmin) or 1)
            com_preco["cor"] = [[int(40 + 200 * x), int(160 - 110 * x), 90, 200] for x in escala]
            nomes_paradas = {p.nome for p, _ in v.plano.paradas}
            com_preco["parada"] = (com_preco["nome"] + " (" + com_preco["uf"] + ")").isin(
                nomes_paradas
            )
            camadas = [
                pdk.Layer("PathLayer", [{"caminho": [list(p) for p in rota.trajeto]}],
                          get_path="caminho", get_color=[40, 90, 160], width_min_pixels=3),
                pdk.Layer("ScatterplotLayer", com_preco, get_position=["lon", "lat"],
                          get_fill_color="cor", get_radius=6000, pickable=True),
                pdk.Layer("ScatterplotLayer", com_preco[com_preco["parada"]],
                          get_position=["lon", "lat"], get_fill_color=[20, 20, 20, 0],
                          get_line_color=[20, 20, 20], stroked=True, line_width_min_pixels=3,
                          get_radius=14000),
                pdk.Layer("ScatterplotLayer", v.pedagio.pracas.assign(rotulo="Pedágio: " +
                          v.pedagio.pracas["praca"].astype(str)), get_position=["lon", "lat"],
                          get_fill_color=[230, 160, 0, 220], get_radius=3500, pickable=True),
            ]  # fmt: skip
            centro = pd.DataFrame(rota.trajeto, columns=["lon", "lat"]).mean()
            st.pydeck_chart(
                pdk.Deck(
                    layers=camadas,
                    initial_view_state=pdk.ViewState(
                        longitude=centro["lon"], latitude=centro["lat"], zoom=5
                    ),
                    tooltip={"text": "{rotulo}"},
                    map_style=None,
                ),
                height=520,
            )
            st.caption(
                "Rota em azul. Cidades com preço pesquisado: verde mais barato, vermelho mais "
                "caro. "
                "Paradas recomendadas com contorno preto. Praças de pedágio em amarelo."
            )

        if dados["previsoes"] is not None:
            st.subheader("Vale esperar?")
            ufs_rota = v.caminho["uf"].unique().tolist()
            prev = dados["previsoes"].copy()
            prev = prev[prev["serie"].isin([f"{u}|{tipo}" for u in ufs_rota])]
            prox = prev[prev["horizonte"] == 1].copy()
            if not prox.empty:
                prox["variacao"] = prox["previsao"] / prox["preco_atual"] - 1
                linhas = [
                    f"**{r.serie.split('|')[0]}**: {r.variacao:+.1%} na próxima semana "
                    f"(entre {r.p10 / r.preco_atual - 1:+.1%} e {r.p90 / r.preco_atual - 1:+.1%})"
                    for r in prox.itertuples()
                ]
                st.write(" · ".join(linhas).replace(".", ","))
                st.caption(
                    "Previsão do modelo em produção para o preço mediano do estado. Uma variação "
                    "pequena, dentro do intervalo, não justifica adiar a viagem."
                )

# --------------------------------------------------------------------------- preço
with aba_preco:
    if dados["uf"] is None:
        st.info("O histórico por estado ainda não foi sincronizado.")
    else:
        c1, c2 = st.columns(2)
        uf_sel = c1.selectbox("Estado", ufs, index=ufs.index("SP"), key="uf_grafico")
        anos = c2.slider("Anos de histórico", 1, 20, 3)
        h = dados["uf"].copy()
        h["semana"] = pd.to_datetime(h["semana"])
        h = h[(h["uf"] == uf_sel) & (h["tipo_diesel"] == tipo)]
        h = h[h["semana"] >= h["semana"].max() - pd.DateOffset(years=anos)]
        linha = alt.Chart(h).mark_line(strokeWidth=2).encode(
            x=alt.X("semana:T", title=None),
            y=alt.Y(
                "preco_mediano:Q", title="R$/L (mediana do estado)", scale=alt.Scale(zero=False)
            ),
            tooltip=[alt.Tooltip("semana:T", title="Semana"),
                     alt.Tooltip("preco_mediano:Q", title="R$/L", format=".3f")],
        )  # fmt: skip
        grafico = linha
        if dados["previsoes"] is not None:
            p = dados["previsoes"]
            p = p[p["serie"] == f"{uf_sel}|{tipo}"].copy()
            if not p.empty:
                p["semana_alvo"] = pd.to_datetime(p["semana_alvo"])
                faixa = (
                    alt.Chart(p)
                    .mark_area(opacity=0.25)
                    .encode(x="semana_alvo:T", y="p10:Q", y2="p90:Q")
                )
                pontos = alt.Chart(p).mark_line(strokeDash=[4, 3], point=True).encode(
                    x="semana_alvo:T", y="previsao:Q",
                    tooltip=[alt.Tooltip("semana_alvo:T", title="Semana"),
                             alt.Tooltip("previsao:Q", title="Previsão", format=".3f"),
                             alt.Tooltip("p10:Q", title="Mínimo provável", format=".3f"),
                             alt.Tooltip("p90:Q", title="Máximo provável", format=".3f")],
                )  # fmt: skip
                grafico = linha + faixa + pontos
        st.altair_chart(grafico, width="stretch")
        st.caption(
            f"{TIPOS[tipo]} em {uf_sel}: preço mediano semanal (linha) e previsão das próximas "
            "4 semanas (tracejado), com a faixa onde o preço deve ficar em 80% dos casos."
        )

# --------------------------------------------------------------------------- modelo
with aba_modelo:
    mod = dados["modelo"]
    if mod is None:
        st.info("As métricas do modelo ainda não foram sincronizadas.")
    else:
        b = mod["backtest"]
        st.write(
            f"Modelo em produção: **{mod['campeao']}**. Dados até a semana de "
            f"{pd.Timestamp(mod['semana_dados']):%d/%m/%Y}."
        )
        c = st.columns(3)
        c[0].metric("Erro médio do modelo", f"R$ {b['gradiente']['mae']:.3f}/L".replace(".", ","))
        c[1].metric("Erro da base ingênua", f"R$ {b['ingenuo']['mae']:.3f}/L".replace(".", ","))
        c[2].metric("Cobertura do intervalo de 80%", f"{b['gradiente']['cobertura']:.0%}")
        st.caption(
            "Backtest com origem móvel nas últimas 52 semanas: em cada semana, o modelo só viu o "
            "que já tinha acontecido. A base ingênua repete o preço atual."
        )
        horiz = pd.DataFrame(mod["por_horizonte"]).T
        horiz.index = [f"{h} semana(s)" for h in horiz.index]
        st.dataframe(
            horiz[["mae", "mape", "cobertura"]]
            .rename(
                columns={"mae": "Erro médio (R$/L)", "mape": "Erro %", "cobertura": "Cobertura"}
            )
            .style.format(
                {"Erro médio (R$/L)": "{:.3f}", "Erro %": "{:.2%}", "Cobertura": "{:.0%}"}
            ),  # fmt: skip
            width="stretch",
        )
        st.write(f"**Última decisão de promoção:** {mod['decisao']['motivo']}.")
        mon = mod["monitoramento"]
        (st.error if mon["alerta"] else st.success)(f"Monitoramento: {mon['mensagem']}.")

# --------------------------------------------------------------------------- sobre
with aba_sobre:
    st.markdown(
        """
**Preços:** Levantamento de Preços de Combustíveis da ANP, de 2004 até hoje, coletado toda
semana por um job no Databricks. O preço de cada cidade é a mediana dos postos pesquisados.
A ANP pesquisa cerca de 400 municípios, então só eles aparecem como opção de parada.

**Rotas:** OSRM, servidor público, sobre dados do OpenStreetMap (© colaboradores do
OpenStreetMap). A rota é a de carro: restrições de caminhão, como altura e peso, não entram.

**Pedágios:** praças das rodovias federais concedidas (ANTT). A tarifa é uma estimativa por
eixo, editável, porque a ANTT não publica as tarifas como dado aberto. Rodovias estaduais com
pedágio não entram.

**Municípios:** IBGE, API de localidades e de malhas.

**Limitações:** o consumo é fixo, sem efeito de carga, relevo ou trânsito. O piso mínimo de
frete da ANTT ainda não entra no cálculo.

Código e decisões: [github.com/Plee47/rota-economica](https://github.com/Plee47/rota-economica).
"""
    )
