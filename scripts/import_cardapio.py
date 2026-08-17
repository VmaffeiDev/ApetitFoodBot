"""Importa um cardapio (CSV ou planilha do Excel), valida e publica o que passou.

Uso:
    python scripts/import_cardapio.py cardapio.csv --unidade SM --refeicao almoco
    python scripts/import_cardapio.py Cardapio_17_a_2108.xlsx --unidade SM --mes 8 --ano 2025

A planilha de planejamento traz so o numero do dia ("17"), sem mes nem ano.
Nesse caso --mes e --ano sao obrigatorios: chutar o mes publicaria o cardapio
de uma semana no dia errado.

O que nao passa na validacao nao e publicado: fica na fila de revisao, que sai
no relatorio final e pode ser consultada depois com --pendencias.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apetit.catalog import connect, import_menu_rows, init_schema, pending_issues  # noqa: E402
from apetit.csv_import import read_rows  # noqa: E402
from apetit.spreadsheet import is_spreadsheet, read_spreadsheet_rows  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Importa cardapio (CSV ou .xlsx) para o banco da Apetit.")
    parser.add_argument("arquivo", nargs="?", help="Arquivo CSV ou .xlsx exportado da operacao.")
    parser.add_argument("--unidade", default="", help="Unidade ou turno do cardapio (ex.: SM, OFL, DP).")
    parser.add_argument("--refeicao", default="almoco", help="Refeicao padrao quando o arquivo nao traz a coluna.")
    parser.add_argument("--mes", type=int, default=None, help="Mes da semana, quando a planilha so traz o dia.")
    parser.add_argument("--ano", type=int, default=None, help="Ano da semana, quando a planilha so traz o dia.")
    parser.add_argument("--aba", default="", help="Aba da planilha; sem isso, le a primeira.")
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

    if not args.arquivo:
        parser.error("informe o arquivo CSV ou .xlsx, ou use --pendencias")

    caminho = Path(args.arquivo)
    if not caminho.exists():
        print(f"Arquivo nao encontrado: {caminho}", file=sys.stderr)
        return 1

    if is_spreadsheet(caminho):
        try:
            linhas = read_spreadsheet_rows(caminho, sheet=args.aba)
        except (RuntimeError, ValueError) as erro:
            print(str(erro), file=sys.stderr)
            return 1
    else:
        # Exports do Windows costumam vir em latin-1.
        try:
            texto = caminho.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            texto = caminho.read_text(encoding="latin-1")
        linhas = read_rows(texto)

    resultado = import_menu_rows(
        conn,
        linhas,
        unit=args.unidade,
        meal=args.refeicao,
        batch=args.lote,
        month=args.mes,
        year=args.ano,
    )
    print(resultado.summary())
    return 1 if resultado.blocked or resultado.blocking_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
