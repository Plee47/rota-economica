"""Linha de comando da coleta: `python -m rota_economica --destino PASTA`.

No Databricks, o job semanal chama o mesmo ponto de entrada (`rota-coleta`) com a pasta do volume.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rota_economica.coleta import anp, antt
from rota_economica.http import ClienteHTTP, ErroHTTP


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rota-coleta", description=__doc__)
    p.add_argument("--destino", required=True, type=Path, help="pasta dos dados brutos")
    p.add_argument("--desde", type=int, default=2004, help="primeiro ano do histórico")
    p.add_argument("--limite", type=int, default=None, help="no máximo N arquivos (testes)")
    p.add_argument("--sem-pedagio", action="store_true", help="não baixar as praças da ANTT")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("rota-coleta")

    cliente = ClienteHTTP()
    rel = anp.coletar(cliente, args.destino, desde=args.desde, limite=args.limite)
    log.info("ANP: %s", rel.resumo())
    for caminho in rel.novos + rel.atualizados:
        log.info("  %s", caminho)
    if not args.sem_pedagio:
        # Pedágio muda pouco: se a ANTT estiver fora do ar, vale o arquivo da coleta anterior.
        try:
            d = antt.baixar_csv(cliente, antt.PRACAS_DE_PEDAGIO, args.destino)
            log.info("ANTT: praças de pedágio, %d bytes", d.bytes)
        except (ErroHTTP, anp.ErroFormato) as e:
            log.warning("ANTT indisponível, mantida a coleta anterior das praças: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
