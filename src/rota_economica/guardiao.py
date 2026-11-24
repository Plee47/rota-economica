"""Guardião de dados: nenhum arquivo de dados brutos entra no Git.

Os dados da ANP e da ANTT ficam no volume do Databricks. No repositório entram código, documentos
e só os arquivos pequenos de teste em `tests/fixtures/`. Roda no pre-commit e no CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path, PurePosixPath

EXTENSOES_DADOS = {".csv", ".zip", ".xlsx", ".xls", ".parquet", ".jsonl", ".kmz"}
PASTA_FIXTURES = "tests/fixtures/"
LIMITE_FIXTURE = 200_000  # bytes: fixture maior que isso é dado de verdade, não exemplo


def verificar(arquivos: list[tuple[str, int]]) -> list[str]:
    """`arquivos` são pares (caminho, tamanho em bytes). Devolve as violações."""
    violacoes = []
    for caminho, tamanho in arquivos:
        texto = caminho.replace("\\", "/")
        if PurePosixPath(texto).suffix.lower() not in EXTENSOES_DADOS:
            continue
        if not texto.startswith(PASTA_FIXTURES):
            violacoes.append(f"{texto}: arquivo de dados fora de {PASTA_FIXTURES}")
        elif tamanho > LIMITE_FIXTURE:
            violacoes.append(f"{texto}: fixture de {tamanho} bytes (limite {LIMITE_FIXTURE})")
    return violacoes


def main() -> int:
    nomes = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    arquivos = [(n, Path(n).stat().st_size) for n in nomes if Path(n).exists()]
    violacoes = verificar(arquivos)
    for v in violacoes:
        print(f"✗ {v}", file=sys.stderr)
    if not violacoes:
        print(f"Guardião: {len(arquivos)} arquivos verificados, nenhum dado bruto.")
    return 1 if violacoes else 0


if __name__ == "__main__":
    raise SystemExit(main())
