import zipfile

import pytest

from rota_economica.coleta.anp import ErroFormato
from rota_economica.leitura.anp import ler_diesel

CABECALHO = (
    "Regiao - Sigla;Estado - Sigla;Municipio;Revenda;CNPJ da Revenda;Nome da Rua;Numero Rua;"
    "Complemento;Bairro;Cep;Produto;Data da Coleta;Valor de Venda;Valor de Compra;"
    "Unidade de Medida;Bandeira"
)
LINHAS = [
    "SE;SP;SÃO PAULO;POSTO A; 00.000.000/0001-00;RUA X;1;;CENTRO;01000-000;DIESEL S10;"
    "05/01/2024;6,19;;R$ / litro;IPIRANGA",
    "SE;SP;SÃO PAULO;POSTO A; 00.000.000/0001-00;RUA X;1;;CENTRO;01000-000;GASOLINA;"
    "05/01/2024;5,79;;R$ / litro;IPIRANGA",
    "N;AC;RIO BRANCO;POSTO B; 11.111.111/0001-11;RUA Y;2;;CENTRO;69900-000;Diesel;"
    "06/01/2024;7,59;;R$ / litro;VIBRA",
    "N;AC;RIO BRANCO;POSTO C; 22.222.222/0001-22;RUA Z;3;;CENTRO;69900-000;DIESEL S500;"
    "06/01/2024;75,9;;R$ / litro;VIBRA",  # vírgula no lugar errado: fora da faixa
    "N;AC;RIO BRANCO;POSTO D; 33.333.333/0001-33;RUA W;4;;CENTRO;69900-000;DIESEL;"
    "99/99/2024;7,10;;R$ / litro;VIBRA",  # data inválida
]


def _csv(tmp_path, nome, codificacao, com_bom=False, linhas=LINHAS, cabecalho=CABECALHO):
    texto = "\n".join([cabecalho, *linhas]) + "\n"
    caminho = tmp_path / nome
    dados = texto.encode(codificacao)
    caminho.write_bytes((b"\xef\xbb\xbf" if com_bom else b"") + dados)
    return caminho


@pytest.mark.parametrize(("codificacao", "bom"), [("utf-8", True), ("latin-1", False)])
def test_le_so_diesel_valido_nas_duas_codificacoes(tmp_path, codificacao, bom):
    df = ler_diesel(_csv(tmp_path, "x.csv", codificacao, bom))
    assert list(df["produto"]) == ["DIESEL S10", "DIESEL"]
    assert list(df["preco"]) == [6.19, 7.59]
    assert df["municipio"].iloc[0] == "SÃO PAULO"
    assert df["cnpj"].iloc[0] == "00.000.000/0001-00"
    assert str(df["data"].iloc[0].date()) == "2024-01-05"


def test_le_csv_dentro_do_zip(tmp_path):
    csv = _csv(tmp_path, "dentro.csv", "utf-8")
    zip_ = tmp_path / "ca-2024-01.zip"
    with zipfile.ZipFile(zip_, "w") as z:
        z.write(csv, "dentro.csv")
    df = ler_diesel(zip_)
    assert len(df) == 2
    assert set(df["arquivo"]) == {"ca-2024-01.zip"}


def test_coluna_faltando_para_a_leitura(tmp_path):
    cabecalho = CABECALHO.replace("Valor de Venda", "Preco")
    with pytest.raises(ErroFormato, match="Valor de Venda"):
        ler_diesel(_csv(tmp_path, "x.csv", "utf-8", cabecalho=cabecalho))


def test_zip_com_nome_de_csv(tmp_path):
    # A ANP publica ca-2014-01.csv como um ZIP: o formato vem do conteúdo, não da extensão.
    csv = _csv(tmp_path, "dentro.csv", "latin-1")
    disfarcado = tmp_path / "ca-2014-01.csv"
    with zipfile.ZipFile(disfarcado, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(csv, "Preços semestrais - AUTOMOTIVOS_2014.01.csv")
    df = ler_diesel(disfarcado)
    assert list(df["produto"]) == ["DIESEL S10", "DIESEL"]
