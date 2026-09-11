"""Leitura de planilha do Excel.

E o formato que a operacao manda de verdade — o arquivo real da semana veio
como `.xlsx`. Ate aqui todos os testes de importacao usavam CSV, entao este
caminho inteiro rodava sem cobertura nenhuma: a planilha virava linhas sem
ninguem conferir se virava certo.

A planilha e montada em memoria, nao commitada como fixture binaria: assim da
para ler no diff o que esta sendo testado, e o teste cobre a volta completa —
escrever, ler e importar.
"""

import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import openpyxl

from apetit.catalog import import_menu_rows, init_schema, menu_for_date
from apetit.csv_import import parse_menu_rows
from apetit.spreadsheet import is_spreadsheet, read_spreadsheet_rows

# O layout de planejamento, do jeito que chega: nome, porcao, ficha e custo
# grudados na mesma celula.
PLANEJAMENTO = [
    ["Dia", "PRATO PRINCIPAL", "ARROZ", "FEIJAO", "KIT - DESCARTAVEIS"],
    ["17", "BIFE ACEBOLADO (80g) - C51 - 3.11", "ARROZ PARBOILIZADO - C51 - 0.24",
     "FEIJAO CARIOCA - C51 - 0.29", "KIT - DESCARTAVEIS - A. YOSHII - 0.09"],
    ["18", "CARNE MOIDA A MEXICANA (80g) - C51 - 2.67", "ARROZ PARBOILIZADO - C51 - 0.24",
     "FEIJAO CARIOCA - C51 - 0.29", "KIT - DESCARTAVEIS - A. YOSHII - 0.09"],
]


def planilha(linhas=None, aba="Planejamento", abas_extras=()) -> io.BytesIO:
    """Monta um .xlsx em memoria, como o que chega pelo Telegram."""
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = aba
    for linha in (linhas if linhas is not None else PLANEJAMENTO):
        worksheet.append(linha)
    for nome in abas_extras:
        workbook.create_sheet(nome)
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


class ReconhecimentoTest(unittest.TestCase):
    def test_knows_which_extensions_are_spreadsheets(self):
        for nome in ("cardapio.xlsx", "Cardapio_17_a_2108.XLSX", "modelo.xltx", "macro.xlsm"):
            self.assertTrue(is_spreadsheet(nome), nome)

    def test_csv_and_others_are_not_spreadsheets(self):
        for nome in ("cardapio.csv", "cardapio.txt", "cardapio.pdf", "cardapio"):
            self.assertFalse(is_spreadsheet(nome), nome)


class LeituraTest(unittest.TestCase):
    def test_reads_an_in_memory_file_without_touching_disk(self):
        # O arquivo que chega pelo Telegram nunca vai para o disco.
        linhas = read_spreadsheet_rows(planilha())

        self.assertEqual(linhas[0][0], "Dia")
        self.assertEqual(linhas[1][1], "BIFE ACEBOLADO (80g) - C51 - 3.11")
        self.assertEqual(len(linhas), 3)

    def test_reads_a_file_on_disk_too(self):
        # O caminho do script de linha de comando.
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as arquivo:
            arquivo.write(planilha().getvalue())
            caminho = Path(arquivo.name)
        self.addCleanup(caminho.unlink, missing_ok=True)

        self.assertEqual(read_spreadsheet_rows(caminho)[0][0], "Dia")

    def test_every_cell_comes_back_as_text(self):
        # No CSV tudo chega como string. Se o Excel devolvesse numero aqui, o
        # mesmo arquivo importaria diferente so por ter vindo em outro formato.
        linhas = read_spreadsheet_rows(planilha())

        self.assertTrue(all(isinstance(c, str) for linha in linhas for c in linha))

    def test_a_whole_number_does_not_become_a_decimal(self):
        # O Excel guarda "17" como 17.0. Virando "17.0", o dia nao vira data.
        linhas = read_spreadsheet_rows(planilha([["Dia", "PRATO PRINCIPAL"], [17, "BIFE"]]))

        self.assertEqual(linhas[1][0], "17")

    def test_an_empty_cell_becomes_an_empty_string(self):
        linhas = read_spreadsheet_rows(planilha([["Dia", "PRATO PRINCIPAL"], ["17", None]]))

        self.assertEqual(linhas[1][1], "")

    def test_without_a_sheet_name_it_reads_the_first_one(self):
        linhas = read_spreadsheet_rows(planilha(abas_extras=("Custos", "Rascunho")))

        self.assertEqual(linhas[0][0], "Dia")

    def test_a_named_sheet_is_honoured(self):
        buffer = planilha(aba="Semana 34")

        self.assertEqual(read_spreadsheet_rows(buffer, sheet="Semana 34")[0][0], "Dia")

    def test_a_sheet_that_does_not_exist_names_the_ones_that_do(self):
        # Quem errou o nome precisa saber quais existem, nao so que falhou.
        with self.assertRaises(ValueError) as erro:
            read_spreadsheet_rows(planilha(aba="Planejamento"), sheet="Cardapio")

        mensagem = str(erro.exception)
        self.assertIn("Cardapio", mensagem)
        self.assertIn("Planejamento", mensagem)


class ImportacaoTest(unittest.TestCase):
    """A volta completa: planilha do Excel ate o cardapio publicado."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_a_spreadsheet_publishes_the_same_menu_a_csv_would(self):
        # Mesmo conteudo nos dois formatos tem que produzir o mesmo cardapio.
        do_excel, _ = parse_menu_rows(read_spreadsheet_rows(planilha()), unit="SM", month=8, year=2026)
        como_csv = "\n".join(";".join(c for c in linha) for linha in PLANEJAMENTO)
        do_csv, _ = parse_menu_rows(
            [linha.split(";") for linha in como_csv.splitlines()], unit="SM", month=8, year=2026
        )

        self.assertEqual(
            [(e.service_date, e.category, e.item.name) for e in do_excel],
            [(e.service_date, e.category, e.item.name) for e in do_csv],
        )

    def test_publishing_from_a_spreadsheet_reaches_the_menu(self):
        resultado = import_menu_rows(
            self.conn, read_spreadsheet_rows(planilha()), unit="SM", month=8, year=2026
        )

        self.assertTrue(resultado.published)
        self.assertEqual(resultado.blocked, [])
        nomes = {linha["name"] for linha in menu_for_date(self.conn, "2026-08-17", unit="SM")}
        self.assertIn("BIFE ACEBOLADO", nomes)
        self.assertIn("FEIJAO CARIOCA", nomes)

    def test_cost_never_survives_the_spreadsheet_path_either(self):
        # A garantia vale para os dois formatos, nao so para o CSV.
        import_menu_rows(self.conn, read_spreadsheet_rows(planilha()), unit="SM", month=8, year=2026)

        nomes = " | ".join(l["name"] for l in menu_for_date(self.conn, "2026-08-17", unit="SM"))
        for custo in ("3.11", "0.24", "0.29", "C51"):
            self.assertNotIn(custo, nomes)

    def test_supply_columns_do_not_reach_the_menu_from_a_spreadsheet(self):
        import_menu_rows(self.conn, read_spreadsheet_rows(planilha()), unit="SM", month=8, year=2026)

        nomes = " | ".join(l["name"] for l in menu_for_date(self.conn, "2026-08-17", unit="SM"))
        self.assertNotIn("DESCARTAVEIS", nomes)


if __name__ == "__main__":
    unittest.main()
