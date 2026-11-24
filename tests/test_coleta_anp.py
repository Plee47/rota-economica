from datetime import date

import pytest
from conftest import BASE, pagina_anp

from rota_economica.coleta import anp


def test_descobre_so_diesel_e_classifica_nomes_irregulares():
    arquivos = anp.descobrir(pagina_anp())
    por_url = {a.url: a for a in arquivos}
    assert not any("glp" in u or "gasolina" in u for u in por_url)
    assert por_url[f"{BASE}/dsas/ca/ca-2004-01.csv"] == anp.ArquivoANP(
        f"{BASE}/dsas/ca/ca-2004-01.csv", "semestral", 2004, 1
    )
    assert por_url[f"{BASE}/dsan/2026/10-diesel-gnv.csv"].periodo == 10
    assert por_url[f"{BASE}/dsan/2026/09-dados-abertos-precos-2026-09-diesel-gnv.csv"].periodo == 9
    sem_extensao = por_url[f"{BASE}/dsan/2026/04-dados-abertos-precos-diesel-gnv"]
    assert (sem_extensao.periodo, sem_extensao.nome) == (
        4,
        "04-dados-abertos-precos-diesel-gnv.csv",
    )
    assert por_url[f"{BASE}/qus/ultimas-4-semanas-diesel-gnv.csv"].tipo == "recente"
    assert por_url[f"{BASE}/dsas/ca/precos-semestrais-ca.zip"].ano is None


def test_link_repetido_entra_uma_vez():
    urls = [a.url for a in anp.descobrir(pagina_anp())]
    assert len(urls) == len(set(urls))


def test_pagina_sem_arquivos_para_a_coleta():
    with pytest.raises(anp.ErroFormato):
        anp.descobrir("<html><a href='/x.csv'>nada</a></html>")


def test_selecao_troca_semestral_por_mensal_a_partir_de_2023():
    escolhidos = anp.selecionar(anp.descobrir(pagina_anp()), desde=2004)
    urls = {a.url for a in escolhidos}
    assert f"{BASE}/dsas/ca/ca-2023-01.zip" not in urls  # repetiria os mensais
    assert f"{BASE}/dsas/ca/ca-2022-02.zip" in urls
    assert f"{BASE}/dsan/2024/precos-diesel-gnv-01.csv" in urls
    assert f"{BASE}/dsas/ca/precos-semestrais-ca.zip" in urls


def test_selecao_desde_um_ano():
    escolhidos = anp.selecionar(anp.descobrir(pagina_anp()), desde=2015)
    anos = {a.ano for a in escolhidos if a.tipo == "semestral" and a.ano}
    assert min(anos) == 2015


def test_caminhos_estaveis_no_volume():
    a = anp.classificar(f"{BASE}/dsan/2024/precos-diesel-gnv-03.csv")
    assert a.caminho == "anp/mensal/2024/precos-diesel-gnv-03.csv"
    r = anp.classificar(f"{BASE}/qus/ultimas-4-semanas-diesel-gnv.csv")
    assert r.caminho == "anp/recente/ultimas-4-semanas-diesel-gnv.csv"


@pytest.mark.parametrize(
    ("ano", "mes", "hoje", "esperado"),
    [
        (2026, 11, date(2026, 11, 24), True),
        (2026, 10, date(2026, 11, 24), True),
        (2026, 9, date(2026, 11, 24), False),
        (2025, 12, date(2026, 1, 10), True),  # virada de ano
        (2025, 11, date(2026, 1, 10), False),
    ],
)
def test_meses_recentes_ainda_mudam(ano, mes, hoje, esperado):
    a = anp.ArquivoANP(f"{BASE}/dsan/{ano}/x-{mes:02d}-diesel.csv", "mensal", ano, mes)
    assert anp.ainda_muda(a, hoje) is esperado


def test_coleta_e_idempotente(cliente, tmp_path):
    hoje = date(2026, 11, 24)
    primeira = anp.coletar(cliente, tmp_path, hoje=hoje)
    assert primeira.novos and not primeira.pulados
    baixados_antes = len(cliente.baixados)

    segunda = anp.coletar(cliente, tmp_path, hoje=hoje)
    assert not segunda.novos
    assert segunda.pulados  # histórico não é baixado de novo
    # Só o que ainda muda volta a ser baixado: últimas semanas, o mês anterior (outubro) e o
    # semestral sem ano no nome.
    novos_downloads = cliente.baixados[baixados_antes:]
    assert novos_downloads
    assert sorted(u.rsplit("/", 1)[-1] for u in novos_downloads) == [
        "10-diesel-gnv.csv",
        "precos-semestrais-ca.zip",
        "ultimas-4-semanas-diesel-gnv.csv",
    ]
    assert len(segunda.iguais) == len(novos_downloads)


def test_coleta_registra_atualizacao_da_fonte(cliente, tmp_path):
    hoje = date(2026, 11, 24)
    anp.coletar(cliente, tmp_path, hoje=hoje)
    cliente.versoes[f"{BASE}/qus/ultimas-4-semanas-diesel-gnv.csv"] = "v2"
    rel = anp.coletar(cliente, tmp_path, hoje=hoje)
    assert rel.atualizados == ["anp/recente/ultimas-4-semanas-diesel-gnv.csv"]


def test_arquivo_apagado_do_volume_e_baixado_de_novo(cliente, tmp_path):
    hoje = date(2026, 11, 24)
    anp.coletar(cliente, tmp_path, hoje=hoje)
    (tmp_path / "anp/semestral/2010/ca-2010-01.csv").unlink()
    rel = anp.coletar(cliente, tmp_path, hoje=hoje)
    assert "anp/semestral/2010/ca-2010-01.csv" in rel.iguais


def test_manifesto_sobrevive_a_reabertura(cliente, tmp_path):
    anp.coletar(cliente, tmp_path, hoje=date(2026, 11, 24), limite=3)
    m = anp.Manifesto.abrir(tmp_path / "anp" / "manifesto.jsonl")
    assert len(m.registros) == 3
    assert all(len(r.sha256) == 64 for r in m.registros.values())


def test_registro_antigo_sem_conferencia_e_baixado_de_novo(cliente, tmp_path):
    hoje = date(2026, 11, 24)
    anp.coletar(cliente, tmp_path, hoje=hoje)
    caminho = tmp_path / "anp" / "manifesto.jsonl"
    # Simula o manifesto da primeira coleta, feita antes da conferência de tamanho.
    m = anp.Manifesto.abrir(caminho)
    for r in m.registros.values():
        r.esperado = None
    m.gravar()
    rel = anp.coletar(cliente, tmp_path, hoje=hoje)
    assert not rel.pulados  # nada antigo é confiado sem conferência
    rel = anp.coletar(cliente, tmp_path, hoje=hoje)
    assert rel.pulados  # depois de conferidos, voltam a ser pulados
