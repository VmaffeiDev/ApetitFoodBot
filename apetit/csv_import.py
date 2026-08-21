"""Leitura do CSV de cardapio exportado pela operacao da Apetit.

Tres layouts sao aceitos, porque os exports observados usam formatos diferentes:

- largo: uma linha por dia, e cada categoria ocupa 5 colunas
  (NOME, KCAL, CHO, LIP, PTN). E o formato da planilha mensal.
- longo: uma linha por item, com colunas nomeadas
  (data, refeicao, categoria, item, porcao, kcal, cho, lip, ptn).
- planejamento: uma linha por dia, uma coluna por categoria, e a celula traz
  tudo grudado — "BIFE ACEBOLADO (80g) - C51 - 3.11". Nao tem macro nenhum.

O arquivo costuma vir do Brasil: separador ";" e decimal com virgula.
"""

import csv
import io
import re
import unicodedata
from datetime import date, datetime

from .allergens import ALLERGENS
from .model import CATEGORIES, DISCARDED_COLUMNS, Issue, MenuEntry, MenuItem, Nutrition

MACRO_HEADERS = ("KCAL", "CHO", "LIP", "PTN")

# Como a operacao costuma preencher a celula de alergenico. Celula vazia ou
# valor desconhecido fica de fora de proposito: vira "nao declarado", nunca
# liberacao.
ALLERGEN_VALUES = {
    "sim": "contem",
    "s": "contem",
    "contem": "contem",
    "x": "contem",
    "1": "contem",
    "nao": "nao_contem",
    "n": "nao_contem",
    "nao contem": "nao_contem",
    "0": "nao_contem",
    "pode conter": "pode_conter",
    "traco": "pode_conter",
    "tracos": "pode_conter",
    "pode": "pode_conter",
}

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


def _category_columns(header: list[str]) -> int:
    """Quantas colunas do cabecalho sao categoria conhecida do cardapio."""
    total = 0
    for cell in header:
        categoria, _ = split_category(cell)
        if categoria in CATEGORIES:
            total += 1
    return total


def _is_planning(header: list[str]) -> bool:
    """Planilha de planejamento: coluna "Dia" + categorias, e nenhum macro."""
    if not header or normalize(header[0]) not in ("dia", "data"):
        return False
    if any(normalize(cell).upper() == "KCAL" for cell in header):
        return False
    return _category_columns(header) >= 2


def _find_header(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows):
        upper = [normalize(cell).upper() for cell in row]
        if "KCAL" in upper or {"ITEM", "CATEGORIA"} <= set(upper):
            return index
        if _is_planning(row):
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


# Colunas de insumo operacional: descartaveis, produto de limpeza, kit de
# tempero e de galeteiro. Nao e comida que a pessoa se serve, entao nao entra
# no cardapio do funcionario.
NON_FOOD_CATEGORIES = ("KIT",)

# Codigo de ficha tecnica como aparece grudado na celula: "C51", "1",
# "06.03.01.258", "09.03.01.077-1".
FICHA_CODE = re.compile(r"^[A-Za-z]{0,2}\d[\d.\-]*$")
PERCENT = re.compile(r"^\d+\s*%$")
# Custo per capita: numero decimal solto no fim da celula.
COST = re.compile(r"^\d+[.,]\d+$")
PORTION_IN_NAME = re.compile(r"\((\d+(?:[.,]\d+)?)\s*g\)", re.IGNORECASE)


def is_non_food(category: str) -> bool:
    return any(category.startswith(prefix) for prefix in NON_FOOD_CATEGORIES)


def parse_planning_cell(raw: str) -> tuple[str, float | None, str]:
    """Separa "BIFE ACEBOLADO (80g) - C51 - 3.11" em nome, porcao e ficha.

    A celula do planejamento junta quatro coisas com " - ": um percentual de
    adesao opcional, o codigo da ficha tecnica, o nome do prato e o custo per
    capita. O custo e dado comercial da Apetit e morre aqui — nunca chega ao
    app do funcionario.

    O nome pode conter " - " ("KIT - QUIMICO - A. YOSHII"), entao o que sobra
    depois de tirar as pontas conhecidas e remontado inteiro, em vez de a
    funcao chutar qual pedaco e o nome.
    """
    partes = [p.strip() for p in str(raw or "").split(" - ") if p.strip()]
    if not partes:
        return "", None, ""

    if len(partes) > 1 and PERCENT.match(partes[0]):
        partes.pop(0)
    # Custo per capita: sempre o ultimo pedaco, e sempre decimal ("3.11",
    # "0.62"). Exigir o decimal separa custo de codigo de ficha, que pode ser
    # um inteiro solto — em "OVO FRITO - 1 - 0.62" o "1" e ficha, nao custo.
    if len(partes) > 1 and COST.match(partes[-1]):
        partes.pop()

    ficha = ""
    if len(partes) > 1 and FICHA_CODE.match(partes[0]):
        ficha = partes.pop(0)
    elif len(partes) > 1 and FICHA_CODE.match(partes[-1]):
        ficha = partes.pop()

    nome = " - ".join(partes).strip()
    portion = None
    achado = PORTION_IN_NAME.search(nome)
    if achado:
        portion = parse_number(achado.group(1))
        nome = PORTION_IN_NAME.sub("", nome).strip()
    nome = re.sub(r"\s{2,}", " ", nome).strip(" -")
    # Celula que sobrou so com numero nao e prato: virou lixo de planilha.
    if parse_number(nome) is not None:
        return "", portion, ficha
    return nome, portion, ficha


def parse_planning(
    rows: list[list[str]],
    header_index: int,
    unit: str,
    meal: str,
    month: int | None = None,
    year: int | None = None,
) -> tuple[list[MenuEntry], list[Issue]]:
    """Le a planilha de planejamento: nomes de pratos, sem macro nenhum.

    A coluna do dia traz so o numero ("17"), sem mes nem ano. Publicar isso
    chutando o mes colocaria o cardapio de uma semana no dia errado, entao a
    falta de mes/ano barra a importacao em vez de adivinhar.
    """
    header = rows[header_index]
    issues: list[Issue] = []

    colunas: list[tuple[str, int, int]] = []
    descartadas: list[str] = []
    for index, cell in enumerate(header[1:], start=1):
        rotulo = normalize(cell).upper()
        if not rotulo:
            continue
        categoria, slot = split_category(cell)
        if is_non_food(categoria):
            descartadas.append(rotulo)
            continue
        if categoria not in CATEGORIES:
            issues.append(Issue("warn", "categoria_desconhecida", f'Coluna "{rotulo}" nao e uma categoria conhecida; itens dela ficam de fora.'))
            continue
        colunas.append((categoria, slot, index))

    if descartadas:
        issues.append(
            Issue(
                "warn",
                "coluna_insumo",
                f"Colunas de insumo operacional ignoradas (nao sao comida servida): {', '.join(sorted(set(descartadas)))}.",
            )
        )
    if not colunas:
        issues.append(Issue("block", "layout_desconhecido", "Nenhuma coluna de categoria reconhecida na planilha de planejamento."))
        return [], issues

    entries: list[MenuEntry] = []
    sem_data = 0
    for row in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in row):
            continue
        service_date = _planning_date(row[0] if row else "", month, year)
        if not service_date:
            sem_data += 1
            continue
        for categoria, slot, col in colunas:
            if col >= len(row):
                continue
            nome, portion, ficha = parse_planning_cell(row[col])
            if not nome:
                continue
            entries.append(
                MenuEntry(
                    unit=unit,
                    service_date=service_date,
                    meal=meal,
                    category=categoria,
                    slot=slot,
                    item=MenuItem(
                        code=ficha_slug(ficha, nome),
                        name=nome,
                        portion_g=portion,
                        nutrition=Nutrition(),
                    ),
                )
            )

    if sem_data:
        detalhe = (
            f"{sem_data} linha(s) com dia que nao virou data."
            if month and year
            else f"{sem_data} linha(s) trazem so o numero do dia. Informe mes e ano da semana para importar."
        )
        issues.append(Issue("block", "data_indefinida", detalhe))
    if entries:
        issues.append(
            Issue(
                "warn",
                "planilha_sem_macro",
                f"{len(entries)} item(ns) publicados sem informacao nutricional: esta planilha nao traz kcal nem macros.",
            )
        )
    return entries, issues


def ficha_slug(ficha: str, name: str) -> str:
    """Codigo do item: prefere a ficha tecnica, cai no nome quando nao tem.

    A ficha e o que liga o mesmo prato entre semanas e entre planilhas — e o
    gancho para casar esta planilha com a que tem os macros. Mas "C51" se
    repete em pratos diferentes na mesma planilha, entao o nome entra junto
    para o codigo continuar identificando um prato so.
    """
    base = slugify(name)
    if not ficha:
        return base
    return f"{slugify(ficha)}_{base}"[:64]


def _planning_date(raw: str, month: int | None, year: int | None) -> str | None:
    """Aceita a data completa se vier; senao monta a partir do dia + mes/ano."""
    completa = parse_date(raw)
    if completa:
        return completa
    texto = str(raw or "").strip()
    if not (texto.isdigit() and month and year):
        return None
    try:
        return date(year, month, int(texto)).isoformat()
    except ValueError:
        return None


def _allergen_columns(header: list[str]) -> dict[str, int]:
    """Colunas de alergenico no layout longo.

    Aceita "alerg_leite", "alergenico gluten", "contem_ovos" — qualquer coluna
    cujo nome termine num codigo conhecido. Quando a ficha tecnica passar a
    exportar esses campos, o alerta liga sem mudar mais nada.
    """
    encontradas: dict[str, int] = {}
    for index, cell in enumerate(header):
        label = normalize(cell).replace("-", " ").replace("_", " ")
        if not any(label.startswith(p) for p in ("alerg", "contem")):
            continue
        for code in ALLERGENS:
            if label.endswith(code):
                encontradas[code] = index
                break
    return encontradas


def parse_long(rows: list[list[str]], header_index: int, unit: str, meal: str) -> tuple[list[MenuEntry], list[Issue]]:
    header = [normalize(cell) for cell in rows[header_index]]
    column: dict[str, int] = {}
    for field, aliases in LONG_ALIASES.items():
        for index, label in enumerate(header):
            if label in aliases:
                column[field] = index
                break
    colunas_alergenico = _allergen_columns(rows[header_index])

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
        declarados: dict[str, str] = {}
        for allergen_code, index in colunas_alergenico.items():
            bruto = normalize(row[index]) if index < len(row) else ""
            estado = ALLERGEN_VALUES.get(bruto)
            if estado:
                declarados[allergen_code] = estado
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
                    allergens=declarados,
                ),
            )
        )
    return entries, issues


def parse_menu_rows(
    rows: list[list[str]],
    unit: str = "",
    meal: str = "almoco",
    month: int | None = None,
    year: int | None = None,
) -> tuple[list[MenuEntry], list[Issue]]:
    """Reconhece o layout e devolve as entradas do cardapio e os problemas.

    Trabalha sobre linhas ja lidas, para que CSV e planilha do Excel entrem
    pelo mesmo caminho e passem exatamente pelas mesmas regras.
    """
    header_index = _find_header(rows)
    if header_index < 0:
        return [], [Issue("block", "cabecalho_nao_encontrado", "Nao encontrei o cabecalho do cardapio.")]

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
    elif _is_planning(header):
        entries, parse_issues = parse_planning(rows, header_index, unit, meal, month, year)
    else:
        entries, parse_issues = parse_long(rows, header_index, unit, meal)
    return entries, issues + parse_issues


def parse_menu_csv(
    text: str,
    unit: str = "",
    meal: str = "almoco",
    month: int | None = None,
    year: int | None = None,
) -> tuple[list[MenuEntry], list[Issue]]:
    """Le o CSV e devolve as entradas do cardapio e os problemas de formato."""
    return parse_menu_rows(read_rows(text), unit=unit, meal=meal, month=month, year=year)
