import unittest

from apetit.portions import MEASURES, Portion, suggest_plate

CARDAPIO = [
    {"category": "PRATO PRINCIPAL", "name": "STROGONOFF DE CARNE", "kcal": 134, "ptn_g": 12.0},
    {"category": "PRATO PRINCIPAL", "name": "FILE DE FRANGO GRELHADO", "kcal": 150, "ptn_g": 32.0},
    {"category": "GUARNICAO", "name": "MACARRAO ALHO E OLEO", "kcal": 126, "ptn_g": 2.5},
    {"category": "ARROZ", "name": "ARROZ PARBOILIZADO", "kcal": 138, "ptn_g": 2.4},
    {"category": "FEIJAO", "name": "FEIJAO PRETO", "kcal": 29, "ptn_g": 1.8},
    {"category": "SALADA", "name": "SAL. MIX DE ALFACE", "kcal": 4, "ptn_g": 0.5},
]


class MedidaTest(unittest.TestCase):
    def test_measure_matches_how_the_food_is_served(self):
        # Feijao sai de concha, arroz de colher. E assim que a pessoa serve.
        self.assertEqual(MEASURES["FEIJAO"][0], "concha")
        self.assertEqual(MEASURES["ARROZ"][0], "colher")

    def test_singular_and_plural(self):
        uma = Portion("FEIJAO", "FEIJAO PRETO", 1, 29, 1.8)
        duas = Portion("FEIJAO", "FEIJAO PRETO", 2, 29, 1.8)

        self.assertEqual(uma.label(), "1 concha de Feijao preto")
        self.assertEqual(duas.label(), "2 conchas de Feijao preto")

    def test_operational_abbreviation_is_cleaned(self):
        salada = Portion("SALADA", "SAL. MIX DE ALFACE", 1, 4, 0.5)

        self.assertEqual(salada.label(), "Mix de alface a vontade")

    def test_totals_multiply_by_quantity(self):
        duas = Portion("ARROZ", "ARROZ PARBOILIZADO", 2, 138, 2.4)

        self.assertEqual(duas.total_kcal, 276)
        self.assertAlmostEqual(duas.total_ptn, 4.8)


class SugestaoTest(unittest.TestCase):
    def test_suggests_household_measures_for_the_goal(self):
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        texto = " | ".join(sugestao.lines())
        self.assertIn("colher", texto)
        self.assertIn("concha", texto)
        self.assertIn("a vontade", texto)

    def test_picks_the_most_protein_dense_main_dish(self):
        # Entre strogonoff (12 g) e file de frango (32 g), o prato principal
        # existe para entregar proteina.
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        principal = next(p for p in sugestao.portions if p.category == "PRATO PRINCIPAL")
        self.assertIn("File de frango", principal.label())

    def test_a_bigger_goal_asks_for_more_food(self):
        leve = suggest_plate(CARDAPIO, target_kcal=550, target_ptn=25)
        reforcado = suggest_plate(CARDAPIO, target_kcal=850, target_ptn=45)

        self.assertGreater(reforcado.kcal, leve.kcal)

    def test_never_goes_far_past_the_target(self):
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        self.assertLessEqual(sugestao.kcal, 700 * 1.10)

    def test_respects_the_cap_per_category(self):
        # Mesmo com alvo altissimo, ninguem deve receber "8 colheres de arroz".
        sugestao = suggest_plate(CARDAPIO, target_kcal=5000, target_ptn=300)

        for portion in sugestao.portions:
            _, _, maximo = MEASURES[portion.category]
            self.assertLessEqual(portion.quantity, maximo, portion.category)

    def test_says_plainly_when_the_menu_cannot_reach_the_goal(self):
        magro = [
            {"category": "ARROZ", "name": "ARROZ", "kcal": 138, "ptn_g": 2.4},
            {"category": "SALADA", "name": "ALFACE", "kcal": 4, "ptn_g": 0.5},
        ]

        sugestao = suggest_plate(magro, target_kcal=700, target_ptn=45)

        self.assertIn("abaixo do seu alvo", sugestao.summary())

    def test_dishes_without_macros_are_reported_not_guessed(self):
        com_furo = CARDAPIO + [{"category": "GUARNICAO", "name": "PICADO MISTO", "kcal": None, "ptn_g": None}]

        sugestao = suggest_plate(com_furo, target_kcal=700, target_ptn=30)

        self.assertTrue(any("sem informacao nutricional" in n for n in sugestao.notes))
        self.assertNotIn("Picado misto", " ".join(sugestao.lines()))

    def test_menu_without_any_macro_suggests_nothing(self):
        sugestao = suggest_plate(
            [{"category": "ARROZ", "name": "ARROZ", "kcal": None, "ptn_g": None}],
            target_kcal=700,
            target_ptn=30,
        )

        self.assertEqual(sugestao.portions, [])
        self.assertIn("Nao consigo sugerir", sugestao.summary())

    def test_dessert_is_never_used_to_close_the_calorie_gap(self):
        com_sobremesa = CARDAPIO + [
            {"category": "SOBREMESA", "name": "PUDIM", "kcal": 375, "ptn_g": 2.0},
        ]

        sugestao = suggest_plate(com_sobremesa, target_kcal=900, target_ptn=30)

        self.assertNotIn("SOBREMESA", {p.category for p in sugestao.portions})

    def test_main_dish_is_not_repeated_once_protein_is_met(self):
        # Com a meta de proteina fechada, o principal so seria repetido para
        # fechar caloria. "Pegue duas porcoes de carne" e caro no refeitorio e
        # nao e o que um nutricionista sugeriria.
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        principal = next(p for p in sugestao.portions if p.category == "PRATO PRINCIPAL")
        self.assertEqual(principal.quantity, 1)

    def test_main_dish_still_repeats_when_protein_is_short(self):
        # Alvo de proteina alto: ai repetir o principal e exatamente o certo.
        sugestao = suggest_plate(CARDAPIO, target_kcal=850, target_ptn=45)

        principal = next(p for p in sugestao.portions if p.category == "PRATO PRINCIPAL")
        self.assertEqual(principal.quantity, 2)

    def test_energy_gap_is_closed_with_sides(self):
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        acompanhamentos = {p.category: p.quantity for p in sugestao.portions}
        self.assertGreater(acompanhamentos["ARROZ"] + acompanhamentos["GUARNICAO"], 2)
        self.assertGreaterEqual(sugestao.kcal, 700 * 0.85)

    def test_summary_does_not_claim_the_goal_when_energy_is_far_below(self):
        # Bater proteina e ficar 300 kcal abaixo nao e "a meta do dia".
        magro = [
            {"category": "PRATO PRINCIPAL", "name": "FRANGO", "kcal": 150, "ptn_g": 32},
            {"category": "SALADA", "name": "ALFACE", "kcal": 4, "ptn_g": 0.5},
        ]

        sugestao = suggest_plate(magro, target_kcal=700, target_ptn=30)

        self.assertIn("abaixo do seu alvo", sugestao.summary())
        self.assertNotIn("E a sua meta do dia", sugestao.summary())

    def test_summary_claims_the_goal_only_when_both_are_met(self):
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        self.assertIn("E a sua meta do dia", sugestao.summary())

    def test_suggestion_follows_the_serving_line_order(self):
        sugestao = suggest_plate(CARDAPIO, target_kcal=700, target_ptn=30)

        categorias = [p.category for p in sugestao.portions]
        self.assertEqual(categorias[0], "PRATO PRINCIPAL")
        self.assertLess(categorias.index("ARROZ"), categorias.index("SALADA"))


if __name__ == "__main__":
    unittest.main()
