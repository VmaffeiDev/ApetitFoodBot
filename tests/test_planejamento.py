"""A planilha de planejamento: nome de prato grudado com codigo e custo.

E o formato que a operacao manda toda semana. Nao traz macro nenhum, entao o
que este arquivo garante e que o cardapio chega inteiro e limpo, e que a falta
de informacao nutricional aparece como falta — nunca como zero.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.catalog import import_menu_csv, init_schema, menu_for_date
from apetit.csv_import import parse_menu_csv, parse_planning_cell
from apetit.humanize import plate_reading
from apetit.portions import suggest_plate

FIXTURES = Path(__file__).parent / "fixtures"
PLANEJAMENTO = (FIXTURES / "cardapio_planejamento.csv").read_text(encoding="utf-8")


class CelulaTest(unittest.TestCase):
    def test_separates_name_portion_and_recipe_code(self):
        nome, porcao, ficha = parse_planning_cell("BIFE ACEBOLADO (80g) - C51 - 3.11")

        self.assertEqual(nome, "BIFE ACEBOLADO")
        self.assertEqual(porcao, 80)
        self.assertEqual(ficha, "C51")

    def test_cost_never_survives(self):
        # 3.11 e custo per capita: dado comercial da Apetit, nao pode chegar
        # ao app do funcionario por nenhum caminho.
        nome, _, _ = parse_planning_cell("BIFE ACEBOLADO (80g) - C51 - 3.11")

        self.assertNotIn("3.11", nome)

    def test_leading_percentage_and_code_are_dropped(self):
        nome, _, ficha = parse_planning_cell("30% - 06.03.01.258 - CUBOS DE MELAO - 0.55")

        self.assertEqual(nome, "CUBOS DE MELAO")
        self.assertEqual(ficha, "06.03.01.258")

    def test_name_that_contains_a_dash_is_kept_whole(self):
        # "KIT - QUIMICO - A. YOSHII" tem o separador dentro do proprio nome.
        nome, _, ficha = parse_planning_cell("100% - 09.03.01.077-1 - KIT - QUIMICO - A. YOSHII - 0.05")

        self.assertEqual(nome, "KIT - QUIMICO - A. YOSHII")
        self.assertEqual(ficha, "09.03.01.077-1")

    def test_cell_without_code_keeps_the_whole_name(self):
        nome, porcao, ficha = parse_planning_cell("REPOLHO BICOLOR COM VINAGRETE - 0.39")

        self.assertEqual(nome, "REPOLHO BICOLOR COM VINAGRETE")
        self.assertIsNone(porcao)
        self.assertEqual(ficha, "")

    def test_bare_name_survives_untouched(self):
        nome, _, _ = parse_planning_cell("SAL. TOMATE")

        self.assertEqual(nome, "SAL. TOMATE")

    def test_integer_token_is_a_recipe_code_not_a_cost(self):
        # "OVO FRITO - 1 - 0.62": o 1 e ficha tecnica, o 0.62 e custo. Trocar
        # os dois faria o item perder o codigo e ganhar um nome errado.
        nome, _, ficha = parse_planning_cell("OVO FRITO - 1 - 0.62")

        self.assertEqual(nome, "OVO FRITO")
        self.assertEqual(ficha, "1")

    def test_a_cell_left_with_only_a_number_is_not_a_dish(self):
        nome, _, _ = parse_planning_cell("0.62")

        self.assertEqual(nome, "")

    def test_empty_cell_yields_nothing(self):
        self.assertEqual(parse_planning_cell(""), ("", None, ""))
        self.assertEqual(parse_planning_cell("   "), ("", None, ""))


class LeituraTest(unittest.TestCase):
    def test_day_number_becomes_a_date_with_month_and_year(self):
        entries, _ = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        self.assertEqual({e.service_date for e in entries}, {"2025-08-17", "2025-08-18"})

    def test_without_month_and_year_it_refuses_instead_of_guessing(self):
        # Chutar o mes publicaria o cardapio da semana no dia errado.
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM")

        self.assertEqual(entries, [])
        bloqueios = [i for i in issues if i.blocking]
        self.assertEqual([i.code for i in bloqueios], ["data_indefinida"])
        self.assertIn("mes e ano", bloqueios[0].detail)

    def test_supply_columns_never_reach_the_employee(self):
        # Descartavel e produto de limpeza nao sao comida que alguem se serve.
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        nomes = " | ".join(e.item.name for e in entries)
        self.assertNotIn("DESCARTAVEIS", nomes)
        self.assertNotIn("QUIMICO", nomes)
        self.assertTrue(any(i.code == "coluna_insumo" for i in issues))

    def test_numbered_category_is_the_same_category_in_another_slot(self):
        entries, _ = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        principais = [e for e in entries if e.category == "PRATO PRINCIPAL" and e.service_date == "2025-08-17"]
        self.assertEqual(sorted(e.slot for e in principais), [1, 2])

    def test_missing_nutrition_is_reported_not_invented(self):
        entries, issues = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        self.assertTrue(all(e.item.nutrition.kcal is None for e in entries))
        self.assertTrue(any(i.code == "planilha_sem_macro" for i in issues))

    def test_the_same_dish_across_days_keeps_one_code(self):
        entries, _ = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        arroz = {e.item.code for e in entries if e.item.name == "ARROZ PARBOILIZADO"}
        self.assertEqual(len(arroz), 1)

    def test_dishes_sharing_a_recipe_code_stay_distinct(self):
        # "C51" se repete em pratos diferentes; o codigo nao pode colidir.
        entries, _ = parse_menu_csv(PLANEJAMENTO, unit="SM", month=8, year=2025)

        do_dia = [e for e in entries if e.service_date == "2025-08-17"]
        self.assertEqual(len({e.item.code for e in do_dia}), len(do_dia))


class PublicacaoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_publishes_the_week_without_nutrition(self):
        resultado = import_menu_csv(self.conn, PLANEJAMENTO, unit="SM", month=8, year=2025)

        self.assertTrue(resultado.published)
        self.assertEqual(resultado.blocked, [])
        cardapio = menu_for_date(self.conn, "2025-08-17", unit="SM")
        nomes = {linha["name"] for linha in cardapio}
        self.assertIn("BIFE ACEBOLADO", nomes)
        self.assertIn("FEIJAO CARIOCA", nomes)

    def test_planning_import_does_not_erase_existing_macros(self):
        # A ficha tecnica entra primeiro; o planejamento vem por cima sem
        # macro nenhum e nao pode zerar o que ja estava certo.
        ficha = (
            "data;categoria;item;codigo;kcal;cho;lip;ptn\n"
            "17/08/2025;PRATO PRINCIPAL;BIFE ACEBOLADO;c51_bife_acebolado;210;2,0;12,0;22,0\n"
        )
        import_menu_csv(self.conn, ficha, unit="SM")

        import_menu_csv(self.conn, PLANEJAMENTO, unit="SM", month=8, year=2025)

        linha = self.conn.execute(
            "SELECT kcal, ptn_g FROM menu_item WHERE code = ?", ("c51_bife_acebolado",)
        ).fetchone()
        self.assertAlmostEqual(linha["kcal"], 210, places=0)
        self.assertAlmostEqual(linha["ptn_g"], 22.0, places=1)

    def test_a_plate_from_this_sheet_is_never_read_as_zero(self):
        """O defeito que este cardapio expos, travado.

        Com a planilha de planejamento nenhum item tem macro. Somando com
        `or 0`, o app dizia a quem pegou carne, arroz, feijao e salada que o
        prato era "leve" e que faltavam 30 g de proteina — afirmando a partir
        de dado que nao existe.
        """
        import_menu_csv(self.conn, PLANEJAMENTO, unit="SM", month=8, year=2025)
        cardapio = menu_for_date(self.conn, "2025-08-17", unit="SM")
        prato = [l for l in cardapio if l["category"] in ("PRATO PRINCIPAL", "ARROZ", "FEIJAO", "SALADA")]
        self.assertTrue(prato)

        sem_macro = sum(1 for l in prato if l["kcal"] is None)
        com_macro = len(prato) - sem_macro
        self.assertEqual(com_macro, 0)

        linhas = plate_reading(0, 0, 700, 30, True, unknown_items=sem_macro, known_items=com_macro)

        texto = " ".join(linhas)
        self.assertIn("nao consigo ler", texto)
        for afirmacao in ("Prato leve", "Faltam 30 g", "atingida"):
            self.assertNotIn(afirmacao, texto)

    def test_this_sheet_produces_no_portion_suggestion(self):
        # Sem macro nao ha o que sugerir, e o app precisa dizer isso em vez de
        # devolver um prato inventado.
        import_menu_csv(self.conn, PLANEJAMENTO, unit="SM", month=8, year=2025)
        cardapio = menu_for_date(self.conn, "2025-08-17", unit="SM")

        sugestao = suggest_plate(
            [{"category": l["category"], "name": l["name"], "kcal": l["kcal"], "ptn_g": l["ptn_g"]} for l in cardapio],
            target_kcal=700,
            target_ptn=30,
        )

        self.assertEqual(sugestao.portions, [])
        self.assertIn("Nao consigo sugerir", sugestao.summary())

    def test_a_later_sheet_still_corrects_a_wrong_macro(self):
        # Congelar o passado nao pode virar recusa a corrigir a ficha.
        primeira = (
            "data;categoria;item;codigo;kcal;cho;lip;ptn\n"
            "17/08/2025;ARROZ;ARROZ PARBOILIZADO;arroz_pb;138;30,0;0,3;2,4\n"
        )
        corrigida = (
            "data;categoria;item;codigo;kcal;cho;lip;ptn\n"
            "17/08/2025;ARROZ;ARROZ PARBOILIZADO;arroz_pb;150;33,0;0,4;2,6\n"
        )
        import_menu_csv(self.conn, primeira, unit="SM")

        import_menu_csv(self.conn, corrigida, unit="SM")

        linha = self.conn.execute("SELECT kcal FROM menu_item WHERE code = ?", ("arroz_pb",)).fetchone()
        self.assertAlmostEqual(linha["kcal"], 150, places=0)


if __name__ == "__main__":
    unittest.main()
