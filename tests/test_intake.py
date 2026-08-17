"""Publicar o cardapio da semana sem terminal.

O risco desta tela nao e falhar: e acertar sozinha. Um palpite silencioso de
mes publica o cardapio da semana no dia errado para o refeitorio inteiro. Por
isso o palpite existe para ser **confirmado**, e o que este arquivo protege e
que ele nunca vire decisao.
"""

import unittest
from datetime import date

from apetit.csv_import import parse_menu_csv
from apetit.intake import build_preview, infer_period

PLANEJAMENTO = (
    "Dia;PRATO PRINCIPAL;ARROZ;FEIJAO\n"
    "17;BIFE ACEBOLADO (80g) - C51 - 3.11;ARROZ PARBOILIZADO - C51 - 0.24;FEIJAO CARIOCA - C51 - 0.29\n"
    "18;CARNE MOIDA A MEXICANA (80g) - C51 - 2.67;ARROZ PARBOILIZADO - C51 - 0.24;FEIJAO CARIOCA - C51 - 0.29\n"
)


class PeriodoTest(unittest.TestCase):
    def test_reads_the_month_from_the_real_file_name(self):
        # E o nome que a operacao usa: "Cardapio_17_a_2108.xlsx".
        periodo = infer_period("Cardapio_17_a_2108.xlsx", date(2025, 8, 15))

        self.assertEqual((periodo.month, periodo.year), (8, 2025))
        self.assertIn("nome do arquivo", periodo.source)

    def test_accepts_other_ways_of_writing_the_range(self):
        for nome in ("cardapio 17 a 21-08.csv", "Cardapio_17a2108.xlsx", "17_a_21_08_2025.csv"):
            periodo = infer_period(nome, date(2025, 8, 15))
            self.assertEqual((periodo.month, periodo.year), (8, 2025), nome)

    def test_reads_an_explicit_month_and_year(self):
        self.assertEqual(infer_period("cardapio_09_2025.csv").month, 9)
        self.assertEqual(infer_period("2025-09-cardapio.xlsx").month, 9)

    def test_reads_the_month_written_out(self):
        periodo = infer_period("Cardapio setembro 2025.xlsx")

        self.assertEqual((periodo.month, periodo.year), (9, 2025))

    def test_two_digit_year_becomes_four(self):
        periodo = infer_period("cardapio_17_a_2108_25.xlsx", date(2025, 8, 1))

        self.assertEqual(periodo.year, 2025)

    def test_without_a_date_it_proposes_next_week_and_says_so(self):
        # A proposta e explicita para a pessoa saber que precisa conferir.
        periodo = infer_period("cardapio.xlsx", date(2025, 8, 27))

        self.assertEqual((periodo.month, periodo.year), (9, 2025))
        self.assertIn("nao achei data", periodo.source)

    def test_an_impossible_month_is_not_accepted(self):
        # "13" nao e mes: cai na proposta, em vez de gerar data invalida.
        periodo = infer_period("cardapio_17_a_2113.xlsx", date(2025, 8, 1))

        self.assertIn("nao achei data", periodo.source)


class PreviaTest(unittest.TestCase):
    def test_preview_shows_period_and_volume_before_publishing(self):
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        previa = build_preview(entries, issues, "SM")

        self.assertTrue(previa.ok)
        self.assertEqual(previa.entries, 6)
        self.assertEqual(previa.dates, ["2025-08-17", "2025-08-18"])
        self.assertEqual(previa.period_label, "2025-08-17 a 2025-08-18")
        self.assertEqual(previa.dishes, 4)

    def test_preview_counts_what_has_no_nutrition(self):
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        previa = build_preview(entries, issues, "SM")

        self.assertEqual(previa.without_macros, 6)

    def test_a_period_falling_on_the_weekend_is_flagged(self):
        # 17 a 21 de agosto de 2025 cai de domingo a quinta: sinal de que o ano
        # do palpite esta errado. Os dias vem da planilha, entao o que nao bate
        # e o mes/ano — e o calendario denuncia sem precisar de outro palpite.
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        previa = build_preview(entries, issues, "SM")

        self.assertTrue(any("fim de semana" in a for a in previa.warnings))
        # Aviso, nao bloqueio: existe refeitorio que serve no fim de semana.
        self.assertTrue(previa.ok)

    def test_a_normal_work_week_is_not_flagged(self):
        # As mesmas datas em 2026 caem de segunda a terca.
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2026)

        previa = build_preview(entries, issues, "SM")

        self.assertFalse(any("fim de semana" in a for a in previa.warnings))

    def test_publishing_without_a_canteen_is_not_allowed(self):
        # O funcionario ve o cardapio filtrado pela unidade dele: publicar sem
        # unidade e publicar para ninguem, e some sem erro nenhum.
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="", month=8, year=2025)

        previa = build_preview(entries, issues, "")

        self.assertTrue(previa.entries)
        self.assertFalse(previa.ok)

    def test_a_file_that_cannot_be_published_is_not_ok(self):
        # Sem mes, a leitura barra e a previa nao oferece publicar.
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM")

        previa = build_preview(entries, issues, "SM")

        self.assertFalse(previa.ok)
        self.assertTrue(previa.blocking)


if __name__ == "__main__":
    unittest.main()
