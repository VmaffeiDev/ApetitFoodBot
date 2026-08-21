"""Planilha de declaracao de alergenico para o nutricionista.

O alerta de alergia precisa de dois lados: o que a pessoa nao pode comer, que
ela mesma declara no cadastro, e o que cada prato contem, que so a cozinha
sabe. Este modulo resolve o segundo lado quando o export da operacao ainda nao
traz o campo.

O trabalho e finito e se paga: num mes real, 110 fichas cobriram 312 ocorrencias
do cardapio, e itens como arroz e feijao reaparecem dezenas de vezes. Declarar
uma vez por ficha vale para sempre.

O fluxo e ida e volta em CSV, para o nutricionista preencher no Excel em vez de
digitar prato a prato no Telegram.
"""

import csv
import io

from .allergens import ALLERGENS, Declaration
from .csv_import import normalize, read_rows

# Como a celula preenchida a mao vira estado. Vazio fica de fora de proposito:
# ausencia de resposta e "nao declarado", nunca liberacao.
CELL_VALUES = {
    "sim": Declaration.CONTEM,
    "s": Declaration.CONTEM,
    "x": Declaration.CONTEM,
    "contem": Declaration.CONTEM,
    "nao": Declaration.NAO_CONTEM,
    "n": Declaration.NAO_CONTEM,
    "nao contem": Declaration.NAO_CONTEM,
    "pode": Declaration.PODE_CONTER,
    "pode conter": Declaration.PODE_CONTER,
    "traco": Declaration.PODE_CONTER,
    "tracos": Declaration.PODE_CONTER,
}

WRITE_VALUES = {
    Declaration.CONTEM: "sim",
    Declaration.NAO_CONTEM: "nao",
    Declaration.PODE_CONTER: "pode conter",
}

HEADER = ["codigo", "prato", *ALLERGENS]


def build_template(itens: list[tuple[str, str, dict[str, Declaration]]]) -> str:
    """Monta o CSV para o nutricionista preencher.

    Cada linha e uma ficha tecnica. O que ja foi declarado vem preenchido, para
    a planilha servir tambem de revisao.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(HEADER)
    for code, name, declarado in itens:
        linha = [code, name]
        for allergen_code in ALLERGENS:
            estado = declarado.get(allergen_code)
            linha.append(WRITE_VALUES.get(estado, ""))
        writer.writerow(linha)
    return buffer.getvalue()


def parse_sheet(text: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Le a planilha preenchida.

    Devolve as declaracoes por codigo de prato e os avisos do que nao deu para
    aproveitar, para o nutricionista saber o que ficou de fora em vez de
    descobrir depois no cardapio.
    """
    rows = read_rows(text)
    if not rows:
        return {}, ["Planilha vazia."]

    header = [normalize(cell) for cell in rows[0]]
    if "codigo" not in header:
        return {}, ["Planilha sem a coluna 'codigo'. Use o arquivo gerado pela exportacao."]

    col_codigo = header.index("codigo")
    colunas = {code: header.index(code) for code in ALLERGENS if code in header}
    if not colunas:
        return {}, ["Planilha sem nenhuma coluna de alergenico reconhecida."]

    declaracoes: dict[str, dict[str, str]] = {}
    avisos: list[str] = []

    for numero, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if col_codigo >= len(row):
            continue
        code = row[col_codigo].strip()
        if not code:
            continue

        do_prato: dict[str, str] = {}
        for allergen_code, indice in colunas.items():
            bruto = normalize(row[indice]) if indice < len(row) else ""
            if not bruto:
                continue
            estado = CELL_VALUES.get(bruto)
            if estado is None:
                avisos.append(
                    f"Linha {numero}, coluna {allergen_code}: nao entendi \"{bruto}\". "
                    "Use sim, nao ou pode conter."
                )
                continue
            do_prato[allergen_code] = estado.value
        if do_prato:
            declaracoes[code] = do_prato

    return declaracoes, avisos


def coverage_summary(total: int, completos: int, parciais: int) -> str:
    if total == 0:
        return "Nenhum prato publicado ainda."
    sem = total - completos - parciais
    pct = completos / total * 100
    linhas = [
        f"{completos} de {total} fichas com todos os alergenicos declarados ({pct:.0f}%).",
        f"{parciais} parcialmente declaradas, {sem} sem nenhuma declaracao.",
    ]
    if completos < total:
        linhas.append(
            "Enquanto a ficha nao estiver completa, o app avisa o funcionario que "
            "nao consegue confirmar — nunca que o prato e seguro."
        )
    return "\n".join(linhas)
