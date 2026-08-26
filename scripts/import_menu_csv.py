"""Atualiza o cardapio a partir de um arquivo CSV.

Uso:
    python scripts/import_menu_csv.py caminho/para/cardapio_semana.csv

O CSV deve ter uma linha de cabecalho com colunas como:
    nome,preco,dia,ingredientes,alergenicos,tags,disponivel

Aceita separador "," ou ";" (detectado automaticamente pelo cabecalho) e
tolera nomes de coluna com/sem acento, em portugues ou ingles (ex: "preco"
ou "price"). Este script SUBSTITUI o cardapio atual: qualquer prato que
estiver no banco mas nao aparecer na planilha enviada e removido - ou
seja, e so trocar o CSV pelo da proxima semana e rodar o script de novo.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import init_db, parse_menu_csv, replace_menu_from_rows  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python scripts/import_menu_csv.py caminho/para/cardapio.csv")
        raise SystemExit(1)

    csv_path = Path(sys.argv[1])
    if not csv_path.exists():
        print(f"Arquivo nao encontrado: {csv_path}")
        raise SystemExit(1)

    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = csv_path.read_text(encoding="latin-1")

    rows = parse_menu_csv(text)
    if not rows:
        print(
            "Nenhum prato valido encontrado no CSV. Confira se a primeira linha "
            "tem os titulos (nome, preco, dia, ingredientes, alergenicos, tags, disponivel)."
        )
        raise SystemExit(1)

    init_db()
    result = replace_menu_from_rows(rows)
    print(
        f"Cardapio atualizado: {result['saved']} prato(s) cadastrado(s)/atualizado(s), "
        f"{result['removed']} removido(s) por nao estarem mais na planilha."
    )


if __name__ == "__main__":
    main()
