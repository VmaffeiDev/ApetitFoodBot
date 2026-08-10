"""Importa um CSV de cardapio, valida e publica o que passou.

Uso:
    python scripts/import_cardapio.py cardapio.csv --unidade SM --refeicao almoco

O que nao passa na validacao nao e publicado: fica na fila de revisao, que sai
no relatorio final e pode ser consultada depois com --pendencias.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apetit.catalog import connect, import_menu_csv, init_schema, pending_issues  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa cardapio em CSV para o banco da Apetit.")
    parser.add_argument("csv", nargs="?", help="Arquivo CSV exportado da operacao.")
    parser.add_argument("--unidade", default="", help="Unidade ou turno do cardapio (ex.: SM, OFL, DP).")
    parser.add_argument("--refeicao", default="almoco", help="Refeicao padrao quando o CSV nao traz a coluna.")
    parser.add_argument("--banco", default=os.getenv("APETIT_DB_PATH", "apetit.db"), help="Caminho do banco SQLite.")
    parser.add_argument("--lote", default="", help="Identificador do lote de importacao.")
    parser.add_argument("--pendencias", action="store_true", help="Apenas lista a fila de revisao e sai.")
    args = parser.parse_args()

    conn = connect(args.banco)
    init_schema(conn)

    if args.pendencias:
        fila = pending_issues(conn)
        if not fila:
            print("Nenhuma pendencia de revisao.")
            return 0
        print(f"{len(fila)} item(ns) aguardando revisao do nutricionista:\n")
        for row in fila:
            local = " | ".join(p for p in (row["service_date"], row["unit"], row["category"]) if p)
            print(f"- {row['item_name']} ({local})\n    {row['detail']}")
        return 0

    if not args.csv:
        parser.error("informe o arquivo CSV ou use --pendencias")

    caminho = Path(args.csv)
    if not caminho.exists():
        print(f"Arquivo nao encontrado: {caminho}", file=sys.stderr)
        return 1

    # Exports do Windows costumam vir em latin-1.
    try:
        texto = caminho.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        texto = caminho.read_text(encoding="latin-1")

    resultado = import_menu_csv(conn, texto, unit=args.unidade, meal=args.refeicao, batch=args.lote)
    print(resultado.summary())
    return 1 if resultado.blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
