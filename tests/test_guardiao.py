from rota_economica.guardiao import LIMITE_FIXTURE, verificar


def test_codigo_e_documentos_passam():
    assert verificar([("src/rota_economica/http.py", 10), ("README.md", 10)]) == []


def test_dado_bruto_fora_das_fixtures_e_barrado():
    assert verificar([("dados/ca-2004-01.csv", 10)])
    assert verificar([("anp/mensal/2024/precos.ZIP", 10)])


def test_fixture_pequena_passa_e_grande_nao():
    assert verificar([("tests/fixtures/exemplo.csv", 100)]) == []
    assert verificar([("tests/fixtures/exemplo.csv", LIMITE_FIXTURE + 1)])


def test_agregados_do_site_passam_com_limite():
    from rota_economica.guardiao import LIMITE_SITE

    assert verificar([("dados/site/precos_uf.parquet", 1_000_000)]) == []
    assert verificar([("dados/site/precos_uf.parquet", LIMITE_SITE + 1)])
    assert verificar([("dados/site/bruto.csv", 10)])  # só parquet no site
    assert verificar([("dados/outra/precos.parquet", 10)])
