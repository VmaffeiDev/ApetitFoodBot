import unittest

from apetit.allergens import Declaration, Restriction, Verdict, check_item
from apetit.allergy_text import describe, recognize


class ReconhecimentoTest(unittest.TestCase):
    def test_simple_food_names(self):
        self.assertEqual(recognize("camarao")[0], ["crustaceos"])
        self.assertEqual(recognize("leite")[0], ["leite"])
        self.assertEqual(recognize("amendoim")[0], ["amendoim"])

    def test_how_people_actually_write_it(self):
        for frase in ("alergia a camarao", "sou alergico a camarao", "nao posso comer camarao"):
            self.assertEqual(recognize(frase)[0], ["crustaceos"], frase)

    def test_lactose_and_dairy_map_to_milk(self):
        for frase in ("intolerante a lactose", "nao posso laticinios", "alergia a queijo"):
            self.assertEqual(recognize(frase)[0], ["leite"], frase)

    def test_seafood_expands_to_more_than_one_allergen(self):
        codigos, livres = recognize("alergia a frutos do mar")

        self.assertEqual(set(codigos), {"crustaceos", "peixes"})
        self.assertEqual(livres, [])

    def test_several_allergies_in_one_sentence(self):
        codigos, livres = recognize("nao posso leite nem ovo, e tenho alergia a amendoim")

        self.assertEqual(set(codigos), {"leite", "ovos", "amendoim"})
        self.assertEqual(livres, [])

    def test_celiac_maps_to_gluten(self):
        self.assertEqual(recognize("sou celiaco")[0], ["gluten"])

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(recognize("ALERGIA A CAMARÃO")[0], ["crustaceos"])
        self.assertEqual(recognize("Intolerância à Lactose")[0], ["leite"])

    def test_nothing_to_declare(self):
        for frase in ("nenhuma", "nao tenho", "nada", ""):
            codigos, livres = recognize(frase)
            self.assertEqual(codigos, [], frase)
            self.assertEqual(livres, [], frase)

    def test_unknown_term_is_kept_not_discarded(self):
        # "legumes" nao existe como campo em ficha tecnica. Nao pode sumir:
        # a pessoa precisa saber que o app nao confere isso.
        codigos, livres = recognize("alergia a legumes")

        self.assertEqual(codigos, [])
        self.assertEqual(livres, ["legumes"])

    def test_mix_of_known_and_unknown(self):
        codigos, livres = recognize("alergia a camarao e a pimenta")

        self.assertEqual(codigos, ["crustaceos"])
        self.assertEqual(livres, ["pimenta"])

    def test_nothing_is_guessed_by_similarity(self):
        # "morango" nao parece nada da lista e nao pode virar palpite.
        codigos, livres = recognize("morango")

        self.assertEqual(codigos, [])
        self.assertEqual(livres, ["morango"])


class DescricaoTest(unittest.TestCase):
    def test_says_what_it_will_check(self):
        texto = describe(["crustaceos"], [])

        self.assertIn("Vou conferir", texto)
        self.assertIn("Crustaceos", texto)

    def test_says_plainly_what_it_cannot_check(self):
        texto = describe(["crustaceos"], ["legumes"])

        self.assertIn("legumes", texto)
        self.assertIn("nao consigo conferir sozinho", texto)
        self.assertIn("balcao", texto)

    def test_says_when_it_understood_nothing(self):
        self.assertIn("Nao identifiquei", describe([], []))


class SegurancaComTermoLivreTest(unittest.TestCase):
    """Quem tem restricao que o app nao confere nunca pode ver visto verde."""

    def test_unverifiable_term_prevents_release(self):
        # Todos os alergenicos conhecidos da pessoa estao descartados, mas ela
        # tambem escreveu "legumes", que o app nao sabe checar.
        check = check_item(
            [Restriction("leite")],
            {"leite": Declaration.NAO_CONTEM},
            unverifiable=["legumes"],
        )

        self.assertIs(check.verdict, Verdict.ATENCAO)
        self.assertFalse(check.safe_to_affirm)
        self.assertIn("legumes", check.message())

    def test_unverifiable_term_alone_still_warns(self):
        check = check_item([], {}, unverifiable=["legumes"])

        self.assertIs(check.verdict, Verdict.ATENCAO)
        self.assertFalse(check.safe_to_affirm)

    def test_declared_allergen_still_wins_over_warning(self):
        check = check_item(
            [Restriction("leite")],
            {"leite": Declaration.CONTEM},
            unverifiable=["legumes"],
        )

        self.assertIs(check.verdict, Verdict.BLOQUEIO)

    def test_without_unverifiable_terms_release_still_works(self):
        check = check_item([Restriction("leite")], {"leite": Declaration.NAO_CONTEM})

        self.assertIs(check.verdict, Verdict.LIBERADO)


if __name__ == "__main__":
    unittest.main()
