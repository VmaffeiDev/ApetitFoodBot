"""Ida e volta da declaracao de alergenico com o nutricionista.

    python scripts/alergenicos.py --exportar alergenicos.csv
    (nutricionista preenche no Excel: sim / nao / pode conter)
    python scripts/alergenicos.py --importar alergenicos.csv
    python scripts/alergenicos.py --cobertura

O alerta de alergia precisa de dois lados. O funcionario declara no cadastro o
que ele nao pode comer; o que cada prato contem so a cozinha sabe, e e isso que
esta planilha resolve enquanto o export da operacao nao trouxer o campo.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apetit.allergen_sheet import build_template, coverage_summary, parse_sheet  # noqa: E402
from apetit.catalog import (  # noqa: E402
    allergen_coverage,
    apply_allergen_sheet,
    connect,
    init_schema,
    items_with_allergens,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Declaracao de alergenico por prato.")
    parser.add_argument("--exportar", metavar="ARQUIVO", help="Gera a planilha para preencher.")
    parser.add_argument("--importar", metavar="ARQUIVO", help="Aplica a planilha preenchida.")
    parser.add_argument("--cobertura", action="store_true", help="Mostra quanto do cardapio ja esta declarado.")
    parser.add_argument("--banco", default=os.getenv("APETIT_DB_PATH", "apetit.db"))
    args = parser.parse_args()

    if not (args.exportar or args.importar or args.cobertura):
        parser.error("escolha --exportar, --importar ou --cobertura")

    conn = connect(args.banco)
    init_schema(conn)

    if args.exportar:
        itens = items_with_allergens(conn)
        if not itens:
            print("Nenhum prato publicado. Importe um cardapio antes.", file=sys.stderr)
            return 1
        Path(args.exportar).write_text(build_template(itens), encoding="utf-8-sig")
        print(f"Planilha com {len(itens)} ficha(s) gerada em: {args.exportar}")
        print("Preencha com sim, nao ou pode conter. Celula vazia continua sem declaracao.")
        print("Os pratos mais frequentes vem primeiro — declarar esses rende mais.")
        return 0

    if args.importar:
        caminho = Path(args.importar)
        if not caminho.exists():
            print(f"Arquivo nao encontrado: {caminho}", file=sys.stderr)
            return 1
        try:
            texto = caminho.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            texto = caminho.read_text(encoding="latin-1")

        declaracoes, avisos = parse_sheet(texto)
        aplicadas, erros = apply_allergen_sheet(conn, declaracoes)
        print(f"Fichas atualizadas: {aplicadas}")
        for aviso in avisos + erros:
            print(f"  aviso: {aviso}")

    cobertura = allergen_coverage(conn)
    print()
    print(coverage_summary(cobertura["total"], cobertura["completos"], cobertura["parciais"]))
    if args.cobertura and cobertura["faltando"]:
        print("\nFaltando declarar (mais frequentes primeiro):")
        for code, nome, quantos in cobertura["faltando"][:15]:
            print(f"  - {nome} ({code}): faltam {quantos}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
