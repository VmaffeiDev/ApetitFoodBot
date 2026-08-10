"""Leitura do CSV de cardapio exportado pela operacao da Apetit.

Dois layouts sao aceitos, porque os exports observados usam formatos diferentes:

- largo: uma linha por dia, e cada categoria ocupa 5 colunas
  (NOME, KCAL, CHO, LIP, PTN). E o formato da planilha mensal.
- longo: uma linha por item, com colunas nomeadas
  (data, refeicao, categoria, item, porcao, kcal, cho, lip, ptn).

O arquivo costuma vir do Brasil: separador ";" e decimal com virgula.
"""

import csv
import io
import re
import unicodedata
from datetime import datetime

from .model import DISCARDED_COLUMNS, Issue, MenuEntry, MenuItem, Nutrition

MACRO_HEADERS = ("KCAL", "CHO", "LIP", "PTN")

# Aliases aceitos no layout longo, ja normalizados.
LONG_ALIASES = {
    "data": ("data", "dia", "data servico", "service date"),
    "meal": ("refeicao", "meal", "servico"),
    "category": ("categoria", "category", "tipo"),
    "name": ("item", "prato", "descricao", "nome"),
    "portion": ("porcao", "gramagem", "peso", "porcao g"),
    "code": ("codigo", "codigo ficha", "ficha tecnica", "code"),
    "kcal": ("kcal", "caloria", "calorias", "energia"),
    "cho": ("cho", "carboidrato", "carboidratos"),
    "lip": ("lip", "lipideo", "lipideos", "gordura", "gorduras"),
    "ptn": ("ptn", "proteina", "proteinas"),
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalize(name)).strip("_")[:64]


def parse_number(raw: str | float | int | None) -> float | None:
    """Converte "8,6", "1.234,56" e "1234.56" em float. Vazio vira None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(" ", "")
    if not text or text in {"-", "--"}:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_date(raw: str) -> str | None:
    """Aceita os formatos de data que aparecem nos exports. Devolve ISO."""
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d/%m", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%d/%m":
            parsed = parsed.replace(year=datetime.now().year)
        return parsed.date().isoformat()
    return None


def split_category(header: str) -> tuple[str, int]:
    """"PRATO PRINCIPAL 2" -> ("PRATO PRINCIPAL", 2). Sem numero, slot 1."""
    text = normalize(header).upper()
    match = re.match(r"^(.*?)[\s_-]*(\d+)$", text)
    if match:
        return match.group(1).strip(), int(match.group(2))
    return text, 1


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        # Export brasileiro costuma usar ";" justamente porque a virgula e decimal.
        return ";" if sample.count(";") >= sample.count(",") else ","


def read_rows(text: str) -> list[list[str]]:
    delimiter = sniff_delimiter(text[:4096])
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _is_wide(header: list[str]) -> bool:
    return sum(1 for cell in header if normalize(cell).upper() == "KCAL") > 1


def _find_header(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows):
        upper = [normalize(cell).upper() for cell in row]
        if "KCAL" in upper or {"ITEM", "CATEGORIA"} <= set(upper):
            return index
    return -1


def _discarded(header: list[str]) -> list[str]:
    found = []
    for cell in header:
        label = normalize(cell).upper()
        if any(label.startswith(token) for token in DISCARDED_COLUMNS):
            found.append(label)
    return found


def parse_wide(rows: list[list[str]], header_index: int, unit: str, meal: str) -> tuple[list[MenuEntry], list[Issue]]:
    header = rows[header_index]
    blocks: list[tuple[str, int, int]] = []
    index = 0
    while index < len(header):
        label = normalize(header[index]).upper()
        if label and label not in MACRO_HEADERS:
            following = [normalize(c).upper() for c in header[index + 1 : index + 5]]
            if following == list(MACRO_HEADERS):
                category, slot = split_category(header[index])
                blocks.append((category, slot, index))
                index += 5
                continue
        index += 1

    entries: list[MenuEntry] = []
    issues: list[Issue] = []
    if not blocks:
        issues.append(Issue("block", "layout_desconhecido", "Nao encontrei blocos CATEGORIA+KCAL+CHO+LIP+PTN no cabecalho."))
        return entries, issues

    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        service_date = parse_date(row[0] if row else "")
        if not service_date:
            continue
        for category, slot, col in blocks:
            if col >= len(row):
                continue
            name = (row[col] or "").strip()
            if not name:
                continue
            values = [parse_number(row[col + offset]) if col + offset < len(row) else None for offset in range(1, 5)]
            entries.append(
                MenuEntry(
                    unit=unit,
                    service_date=service_date,
                    meal=meal,
                    category=category,
                    slot=slot,
                    item=MenuItem(
                        code=slugify(name),
                        name=name,
                        nutrition=Nutrition(*values),
                    ),
                )
            )
    return entries, issues


def parse_long(rows: list[list[str]], header_index: int, unit: str, meal: str) -> tuple[list[MenuEntry], list[Issue]]:
    header = [normalize(cell) for cell in rows[header_index]]
    column: dict[str, int] = {}
    for field, aliases in LONG_ALIASES.items():
        for index, label in enumerate(header):
            if label in aliases:
                column[field] = index
                break

    issues: list[Issue] = []
    faltando = [field for field in ("data", "category", "name") if field not in column]
    if faltando:
        issues.append(Issue("block", "coluna_ausente", f"Colunas obrigatorias ausentes no CSV: {', '.join(faltando)}."))
        return [], issues

    entries: list[MenuEntry] = []
    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue

        def cell(field: str) -> str:
            index = column.get(field)
            return row[index].strip() if index is not None and index < len(row) else ""

        service_date = parse_date(cell("data"))
        name = cell("name")
        if not service_date or not name:
            continue
        category, slot = split_category(cell("category"))
        code = cell("code") or slugify(name)
        entries.append(
            MenuEntry(
                unit=unit,
                service_date=service_date,
                meal=cell("meal") or meal,
                category=category,
                slot=slot,
                item=MenuItem(
                    code=code,
                    name=name,
                    portion_g=parse_number(cell("portion")),
                    nutrition=Nutrition(
                        kcal=parse_number(cell("kcal")),
                        cho_g=parse_number(cell("cho")),
                        lip_g=parse_number(cell("lip")),
                        ptn_g=parse_number(cell("ptn")),
                    ),
                ),
            )
        )
    return entries, issues


def parse_menu_csv(text: str, unit: str = "", meal: str = "almoco") -> tuple[list[MenuEntry], list[Issue]]:
    """Le o CSV e devolve as entradas do cardapio e os problemas de formato."""
    rows = read_rows(text)
    header_index = _find_header(rows)
    if header_index < 0:
        return [], [Issue("block", "cabecalho_nao_encontrado", "Nao encontrei o cabecalho do cardapio no CSV.")]

    header = rows[header_index]
    issues: list[Issue] = []
    descartadas = _discarded(header)
    if descartadas:
        issues.append(
            Issue(
                "warn",
                "coluna_descartada",
                f"Colunas de custo/per capita ignoradas por serem dado interno: {', '.join(descartadas)}.",
            )
        )

    if _is_wide(header):
        entries, parse_issues = parse_wide(rows, header_index, unit, meal)
    else:
        entries, parse_issues = parse_long(rows, header_index, unit, meal)
    return entries, issues + parse_issues
