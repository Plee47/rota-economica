"""Traz a exportação do Databricks para `dados/site/`, de onde o site lê.

Uso: python scripts/sincronizar_site.py [--alvo dev|prod]
Precisa do Databricks CLI autenticado (`databricks auth login`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from rota_economica.exportacao import ARQUIVOS  # noqa: E402

DESTINO = RAIZ / "dados" / "site"


def schema(alvo: str) -> str:
    resumo = subprocess.run(
        ["databricks", "bundle", "summary", "-t", alvo, "-o", "json"],
        capture_output=True, text=True, check=True, cwd=RAIZ,
    )  # fmt: skip
    return json.loads(resumo.stdout)["resources"]["schemas"]["rota"]["name"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--alvo", default="dev")
    p.add_argument("--catalogo", default="workspace")
    args = p.parse_args()
    if not shutil.which("databricks"):
        sys.exit("Databricks CLI não encontrado")
    origem = f"dbfs:/Volumes/{args.catalogo}/{schema(args.alvo)}/bruto/exportacao"
    DESTINO.mkdir(parents=True, exist_ok=True)
    for nome in ARQUIVOS:
        r = subprocess.run(
            ["databricks", "fs", "cp", f"{origem}/{nome}", str(DESTINO / nome), "--overwrite"],
            capture_output=True, text=True,
        )  # fmt: skip
        estado = f"{(DESTINO / nome).stat().st_size} bytes" if r.returncode == 0 else "falhou"
        print(f"{nome}: {estado}")
    if not (DESTINO / "municipios.parquet").exists():
        # O IBGE recusa a nuvem, mas responde daqui: monta a lista localmente.
        from rota_economica.http import ClienteHTTP
        from rota_economica.rota import municipios

        cache = RAIZ / "dados" / "cache_ibge"
        municipios.carregar(ClienteHTTP(), cache).to_parquet(
            DESTINO / "municipios.parquet", index=False
        )
        print("municipios.parquet: montado localmente pela API do IBGE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
