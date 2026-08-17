import unittest

from apetit.humanize import (
    category_label,
    dish_hint,
    dish_weight,
    friendly_date,
    order_categories,
    plate_reading,
    week_summary,
)


class DataTest(unittest.TestCase):
    def test_iso_becomes_readable_portuguese(self):
        self.assertEqual(friendly_date("2025-09-01"), "segunda-feira, 1 de setembro")
        self.assertEqual(friendly_date("2025-12-25"), "quinta-feira, 25 de dezembro")

    def test_invalid_date_is_returned_untouched(self):
        self.assertEqual(friendly_date("nao e data"), "nao e data")


class CategoriaTest(unittest.TestCase):
    def test_serving_line_order_beats_alphabetical(self):
        bagunca = {"SOBREMESA", "SALADA", "PRATO PRINCIPAL", "ARROZ", "GUARNICAO"}

        ordem = order_categories(bagunca)

        self.assertEqual(ordem[0], "PRATO PRINCIPAL")
        self.assertLess(ordem.index("GUARNICAO"), ordem.index("ARROZ"))
        self.assertEqual(ordem[-1], "SOBREMESA")

    def test_unknown_category_goes_to_the_end_instead_of_disappearing(self):
        ordem = order_categories({"PRATO PRINCIPAL", "CATEGORIA NOVA"})

        self.assertEqual(ordem, ["PRATO PRINCIPAL", "CATEGORIA NOVA"])

    def test_label_is_human(self):
        self.assertEqual(category_label("OPCAO AO PP"), "Opcao ao prato principal")


class DescricaoDoPratoTest(unittest.TestCase):
    def test_weight_in_one_word(self):
        self.assertEqual(dish_weight(40), "leve")
        self.assertEqual(dish_weight(140), "moderado")
        self.assertEqual(dish_weight(300), "reforcado")

    def test_missing_data_says_so_instead_of_guessing(self):
        self.assertEqual(dish_weight(None), "sem informacao")

    def test_hint_highlights_good_protein(self):
        self.assertIn("boa fonte de proteina", dish_hint(150, 32))
        self.assertNotIn("proteina", dish_hint(150, 2))


class LeituraDoPratoTest(unittest.TestCase):
    def test_light_plate(self):
        linhas = plate_reading(kcal=300, ptn_g=12, target_kcal=700, target_ptn=30, has_fresh=True)

        self.assertIn("leve", linhas[0])

    def test_aligned_plate(self):
        linhas = plate_reading(kcal=690, ptn_g=31, target_kcal=700, target_ptn=30, has_fresh=True)

        self.assertIn("alinhado", linhas[0])
        self.assertIn("atingida", linhas[1])

    def test_plate_above_target_describes_without_scolding(self):
        linhas = plate_reading(kcal=1200, ptn_g=40, target_kcal=700, target_ptn=30, has_fresh=True)

        texto = " ".join(linhas).lower()
        self.assertIn("acima", texto)
        # Descreve onde o prato esta; nao manda comer menos.
        for palavra in ("evite", "exagerou", "cuidado", "reduza", "menos"):
            self.assertNotIn(palavra, texto)

    def test_missing_protein_says_how_much_is_left(self):
        linhas = plate_reading(kcal=400, ptn_g=18, target_kcal=700, target_ptn=30, has_fresh=True)

        self.assertIn("Faltam 12 g", linhas[1])

    def test_plate_without_salad_gets_a_suggestion_to_add(self):
        linhas = plate_reading(kcal=400, ptn_g=30, target_kcal=700, target_ptn=30, has_fresh=False)

        self.assertIn("vale somar uma", linhas[-1])


class LeituraSemMacroTest(unittest.TestCase):
    """Sem informacao nutricional o app descreve a falta; nunca a chama de zero."""

    def test_plate_with_no_nutrition_is_not_classified(self):
        # Com o cardapio de planejamento, todo item chega sem macro. Somar zero
        # e chamar de "prato leve" seria o app afirmar o que nao sabe.
        linhas = plate_reading(
            kcal=0, ptn_g=0, target_kcal=700, target_ptn=30, has_fresh=True,
            unknown_items=4, known_items=0,
        )

        texto = " ".join(linhas)
        self.assertIn("nao consigo ler", texto)
        for proibido in ("leve", "alinhado", "Faltam", "0 kcal"):
            self.assertNotIn(proibido, texto)

    def test_partial_plate_reports_a_floor_not_a_total(self):
        linhas = plate_reading(
            kcal=280, ptn_g=14, target_kcal=700, target_ptn=30, has_fresh=True,
            unknown_items=2, known_items=2,
        )

        texto = " ".join(linhas)
        self.assertIn("Leitura incompleta", texto)
        self.assertIn("no minimo 280 kcal", texto)
        # Nao classifica o prato a partir de uma soma que sabe estar incompleta.
        self.assertNotIn("Prato leve", texto)

    def test_missing_salad_is_still_flagged_without_nutrition(self):
        # A composicao do prato nao depende de macro para ser lida.
        linhas = plate_reading(
            kcal=0, ptn_g=0, target_kcal=700, target_ptn=30, has_fresh=False,
            unknown_items=3, known_items=0,
        )

        self.assertIn("vale somar uma", linhas[-1])

    def test_complete_plate_reads_exactly_as_before(self):
        linhas = plate_reading(kcal=690, ptn_g=31, target_kcal=700, target_ptn=30, has_fresh=True)

        self.assertIn("alinhado", linhas[0])
        self.assertIn("atingida", linhas[1])


class ProgressoTest(unittest.TestCase):
    def test_week_summary_is_personal_and_never_comparative(self):
        self.assertIn("ainda nao registrou", week_summary(0))
        self.assertIn("3 de 5", week_summary(3))
        self.assertIn("todos os 5", week_summary(5))
        for dias in (0, 3, 5):
            texto = week_summary(dias).lower()
            for palavra in ("media", "colega", "ranking", "melhor que"):
                self.assertNotIn(palavra, texto)


if __name__ == "__main__":
    unittest.main()
