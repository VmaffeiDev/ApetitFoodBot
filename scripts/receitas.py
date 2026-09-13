"""Alergenico a partir da planilha de receitas da operacao.

    python scripts/receitas.py --importar Receitas-Cardapio-Nutri.xlsx
    python scripts/receitas.py --importar Receitas.xlsx --so-conferir
    python scripts/receitas.py --revisar Receitas.xlsx --prato strogonoff_de_carne

A planilha de receitas traz a lista de ingredientes de cada prato. Dela sai a
declaracao de alergenico que a ficha tecnica nunca trouxe — e sai como
**deduzida**, nao como declarada pela cozinha: `--so-conferir` mostra o que
seria gravado sem gravar, e `--revisar` abre prato por prato para a
nutricionista conferir contra a receita de verdade.

O que este script nunca faz: gravar `nao_contem`. Ingrediente prova presenca,
nao ausencia — a receita pode omitir o molho pronto, o ingrediente composto
pode esconder alergenico e existe contaminacao cruzada na cozinha. Liberar um
prato continua sendo decisao da nutricionista.
"""

import argparse
import collections
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apetit.allergens import ALLERGENS, Declaration  # noqa: E402
from apetit.catalog import connect, init_schema, set_item_allergens  # noqa: E402
from apetit.csv_import import slugify  # noqa: E402
from apetit.recipes import (  # noqa: E402
    allergens_for_ingredient,
    declarations_by_item,
    read_recipe_rows,
)
from apetit.spreadsheet import read_spreadsheet_rows  # noqa: E402

ABA_RECEITAS = "Receitas"
FONTE = "lista de ingredientes"


def carregar(caminho: str) -> dict:
    linhas = read_spreadsheet_rows(caminho, sheet=ABA_RECEITAS)
    receitas = read_recipe_rows(linhas)
    if not receitas:
        raise SystemExit(f"Nenhuma receita em {caminho} (aba {ABA_RECEITAS!r}).")
    return receitas


def resumo(pratos: dict) -> None:
    total = collections.Counter()
    for match in pratos.values():
        for alerg, decl in match.declarations.items():
            total[(alerg, decl.value)] += 1
    com = sum(1 for m in pratos.values() if m.declarations)
    conflito = [m for m in pratos.values() if m.conflicting]

    print(f"receitas agrupadas em {len(pratos)} pratos")
    print(f"  com alguma declaracao: {com}")
    print(f"  sem nenhuma:           {len(pratos) - com}")
    print("\npor alergenico:")
    for (alerg, decl), n in sorted(total.items()):
        print(f"  {ALLERGENS[alerg]:<42} {decl:<12} {n} pratos")
    if conflito:
        print(f"\n{len(conflito)} prato(s) com variantes que discordam — ficam SEM declaracao:")
        for m in conflito[:10]:
            print(f"  {m.item_code}: {', '.join(m.conflicting)} ({len(m.recipes)} receitas)")


def revisar(receitas: dict, prato: str) -> None:
    """Abre um prato: a receita, os ingredientes e o que cada um disparou."""
    achou = False
    for receita in receitas.values():
        if slugify(receita.name) != prato:
            continue
        achou = True
        print(f"\n=== {receita.code}  {receita.name}   [{receita.group}]")
        for ingrediente in receita.ingredients:
            marcas = allergens_for_ingredient(ingrediente)
            rotulo = ", ".join(f"{a}={d.value}" for a, d in sorted(marcas.items())) or "-"
            print(f"   {ingrediente:<52} {rotulo}")
    if not achou:
        print(f"Nenhuma receita com o codigo de prato {prato!r}.")


def gravar(banco: str, pratos: dict, so_publicados: bool) -> int:
    conn = connect(banco)
    init_schema(conn)
    try:
        if so_publicados:
            existentes = {
                linha["code"] for linha in conn.execute("SELECT code FROM menu_item").fetchall()
            }
        gravados = 0
        for match in pratos.values():
            if not match.declarations:
                continue
            if so_publicados and match.item_code not in existentes:
                continue
            # Nunca `nao_contem`: a planilha nao sustenta afirmar ausencia.
            declaracoes = {
                alerg: decl.value
                for alerg, decl in match.declarations.items()
                if decl is not Declaration.NAO_CONTEM
            }
            if declaracoes:
                set_item_allergens(conn, match.item_code, declaracoes,
                                   source=FONTE, deduzida=True)
                gravados += 1
        return gravados
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Alergenico deduzido da planilha de receitas.")
    parser.add_argument("--importar", metavar="ARQUIVO", help="Planilha de receitas (.xlsx).")
    parser.add_argument("--revisar", metavar="ARQUIVO", help="Abre uma receita para conferencia.")
    parser.add_argument("--prato", help="Codigo do prato a revisar (ex.: strogonoff_de_carne).")
    parser.add_argument(
        "--so-conferir",
        action="store_true",
        help="Mostra o que seria gravado, sem gravar nada.",
    )
    parser.add_argument(
        "--todos-os-pratos",
        action="store_true",
        help="Grava tambem pratos que ainda nao estao no cardapio publicado.",
    )
    parser.add_argument("--banco", default=os.getenv("APETIT_DB_PATH", "apetit.db"))
    args = parser.parse_args()

    if args.revisar:
        if not args.prato:
            parser.error("--revisar precisa de --prato")
        revisar(carregar(args.revisar), args.prato)
        return 0

    if not args.importar:
        parser.error("escolha --importar ou --revisar")

    receitas = carregar(args.importar)
    pratos = declarations_by_item(receitas, slugify)
    resumo(pratos)

    if args.so_conferir:
        print("\n--so-conferir: nada foi gravado.")
        return 0

    gravados = gravar(args.banco, pratos, so_publicados=not args.todos_os_pratos)
    alcance = "todos os pratos" if args.todos_os_pratos else "pratos ja no cardapio"
    print(f"\n{gravados} prato(s) gravados em {args.banco} ({alcance}).")
    print(f"Fonte registrada: {FONTE!r} — e deducao, nao declaracao da cozinha.")
    print("Confira com: python scripts/alergenicos.py --cobertura")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
