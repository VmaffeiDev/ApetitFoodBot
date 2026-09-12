"""Alergenico deduzido da lista de ingredientes.

Este arquivo guarda duas coisas de peso desigual. A primeira e que a deducao
funciona — e o que tira a cobertura de alergenico de 0%.

A segunda importa mais: que ela **erra para o lado seguro**. Um `nao_contem`
deduzido de uma lista de ingredientes seria o app afirmando que um prato e
seguro sem ninguem ter conferido, e e o unico erro aqui que manda alguem para
o hospital. Um ⚠️ a mais so incomoda; um ⚠️ a menos, nao.

Depois vem o falso positivo, que tambem custa: "COUVE MANTEIGA" marcado como
leite ensina a pessoa a ignorar o aviso — e ai o aviso verdadeiro tambem passa
batido.
"""

import unittest

from apetit.allergens import Declaration
from apetit.csv_import import slugify
from apetit.recipes import (
    allergens_for_ingredient,
    allergens_for_recipe,
    declarations_by_item,
    normalize,
    read_recipe_rows,
)

CABECALHO = (
    "ParentKey", "Nome da Receita", "Código do Grupo", "Nome do Grupo",
    "LineNum", "ItemCode", "Descrição do item", "Quantidade", "Warehouse",
)


def linha(codigo, nome, ingrediente, grupo="PRA. PRATOS PRINCIP."):
    return (codigo, nome, 119, grupo, 0, "01.00.00.000", ingrediente, 0.1, "UCD-0001")


class LeituraTest(unittest.TestCase):
    def test_groups_the_ingredients_of_each_recipe(self):
        receitas = read_recipe_rows([
            CABECALHO,
            linha("01.01", "CARNE ASSADA", "CARNE BOVINA - KG"),
            linha("01.01", "CARNE ASSADA", "SAL REFINADO - KG"),
            linha("01.02", "ARROZ", "ARROZ BRANCO - KG"),
        ])

        self.assertEqual(len(receitas), 2)
        self.assertEqual(receitas["01.01"].name, "CARNE ASSADA")
        self.assertEqual(len(receitas["01.01"].ingredients), 2)
        self.assertEqual(receitas["01.01"].group, "PRA. PRATOS PRINCIP.")

    def test_the_header_row_is_not_a_recipe(self):
        self.assertEqual(read_recipe_rows([CABECALHO]), {})

    def test_normalize_drops_accent_and_punctuation(self):
        self.assertEqual(normalize("Óleo de Soja - L"), "OLEO DE SOJA L")


class NuncaAfirmaAusenciaTest(unittest.TestCase):
    """A regra que nao pode quebrar."""

    def test_a_recipe_without_allergens_declares_nothing_instead_of_nao_contem(self):
        # Arroz com sal nao leva leite. Mas a receita pode ter omitido algo, e o
        # arroz cozinha na mesma cozinha do macarrao. "Nao declarado" e honesto;
        # "nao contem" seria o app liberando um prato que ninguem conferiu.
        achado = allergens_for_recipe(["ARROZ BRANCO TIPO 1 - KG", "SAL REFINADO - KG"])

        self.assertEqual(achado, {})

    def test_no_ingredient_in_the_real_vocabulary_ever_yields_nao_contem(self):
        amostra = [
            "LEITE UHT INTEGRAL - L", "OVO DE GALINHA - UN", "FARINHA DE TRIGO BRANCA - KG",
            "OLEO DE SOJA - L", "CAMARAO SECO - KG", "FILE DE MERLUZA IQF - KG",
            "AMENDOIM - KG", "CASTANHA DO PARA S/ CASCA - KG", "MOLHO SHOYU - L",
            "CEBOLA - KG", "ARROZ BRANCO TIPO 1 - KG", "TEMPERO APETIT - KG",
        ]
        for ingrediente in amostra:
            for decl in allergens_for_ingredient(ingrediente).values():
                self.assertIn(decl, (Declaration.CONTEM, Declaration.PODE_CONTER), ingrediente)


class DeduzTest(unittest.TestCase):
    """Os dois pratos que o docstring de allergens.py cita como o erro a evitar."""

    def test_strogonoff_finally_declares_the_cream(self):
        # "STROGONOFF DE CARNE nao avisa que leva creme de leite" — a receita avisa.
        achado = allergens_for_recipe([
            "MISTURA P/ PREPARADO MOLHO STROGONOFF - KG",
            "COMPOSTO LACTEO - KG",
            "CARNE CONG BOV S/OSSO-TIRAS - CX 16KG - ACEM",
        ])

        self.assertEqual(achado["leite"], Declaration.CONTEM)

    def test_milanesa_finally_declares_egg_and_wheat(self):
        achado = allergens_for_recipe([
            "PEITO DE FRANGO S/ OSSO E S/ PELE - KG",
            "OVO DE GALINHA - UN",
            "FARINHA DE TRIGO BRANCA - KG",
        ])

        self.assertEqual(achado["ovos"], Declaration.CONTEM)
        self.assertEqual(achado["gluten"], Declaration.CONTEM)

    def test_contem_wins_over_pode_conter_in_the_same_recipe(self):
        # A margarina sozinha daria "pode conter leite"; o queijo resolve a duvida.
        achado = allergens_for_recipe(["MARGARINA C/ SAL - KG", "QUEIJO MUSSARELA FATIADO 20G - KG"])

        self.assertEqual(achado["leite"], Declaration.CONTEM)

    def test_an_industrialized_mix_says_it_cannot_tell(self):
        achado = allergens_for_recipe(["CALDO DE GALINHA - KG"])

        self.assertEqual(achado["gluten"], Declaration.PODE_CONTER)

    def test_shoyu_carries_wheat_not_only_soy(self):
        achado = allergens_for_ingredient("MOLHO SHOYU - L")

        self.assertEqual(achado["soja"], Declaration.CONTEM)
        self.assertEqual(achado["gluten"], Declaration.CONTEM)


class NaoConfundeTest(unittest.TestCase):
    """Cada caso aqui e um ⚠️ falso que o app deixou de mostrar.

    Todos vieram da planilha real, nao de imaginacao: sao os nomes que um
    casamento por pedaco de palavra marcaria errado.
    """

    def test_kale_is_not_butter(self):
        self.assertNotIn("leite", allergens_for_ingredient("COUVE MANTEIGA - KG"))

    def test_cheese_bread_is_made_of_polvilho_and_has_no_gluten(self):
        achado = allergens_for_ingredient("PAO DE QUEIJO CONGELADO 80G - KG")

        self.assertNotIn("gluten", achado)
        self.assertEqual(achado["leite"], Declaration.CONTEM)   # queijo, esse tem

    def test_coconut_and_plant_milks_are_not_dairy(self):
        for item in ("LEITE DE COCO - L", "LEITE DE SOJA - L", "LEITE DE AMENDOA - L"):
            self.assertNotIn("leite", allergens_for_ingredient(item), item)

    def test_a_plant_milk_still_declares_its_own_allergen(self):
        self.assertEqual(allergens_for_ingredient("LEITE DE SOJA - L")["soja"], Declaration.CONTEM)

    def test_nutmeg_is_a_spice_not_a_nut(self):
        self.assertNotIn("castanhas", allergens_for_ingredient("NOZ MOSCADA PO - KG"))

    def test_pita_bread_has_no_crab_in_it(self):
        achado = allergens_for_ingredient("PAO SIRIO 100G - UN")

        self.assertNotIn("crustaceos", achado)
        self.assertEqual(achado["gluten"], Declaration.CONTEM)   # pao, esse tem

    def test_syrian_pepper_is_not_crab_either(self):
        self.assertEqual(allergens_for_ingredient("PIMENTA SIRIA - KG"), {})

    def test_ovomaltine_is_a_brand_not_an_egg(self):
        self.assertNotIn("ovos", allergens_for_ingredient("ACHOCOLATADO OVOMALTINE - KG"))

    def test_baking_powder_is_cornstarch(self):
        self.assertEqual(allergens_for_ingredient("FERMENTO EM PO - KG"), {})

    def test_cassava_and_corn_flour_are_not_wheat(self):
        for item in ("FARINHA DE MANDIOCA - KG", "AMIDO DE MILHO - KG", "POLVILHO DOCE - KG"):
            self.assertNotIn("gluten", allergens_for_ingredient(item), item)

    def test_plain_tomato_sauce_does_not_drag_the_industrialized_warning(self):
        self.assertEqual(allergens_for_ingredient("MOLHO DE TOMATE - KG"), {})

    def test_but_powdered_tomato_sauce_does(self):
        # Preparado desidratado tem formula fechada, como qualquer molho pronto.
        achado = allergens_for_ingredient("MOLHO DE TOMATE EM PO - KG")

        self.assertEqual(achado["gluten"], Declaration.PODE_CONTER)


class OleoRefinadoTest(unittest.TestCase):
    def test_refined_soy_oil_does_not_stamp_contains_soy_on_every_dish(self):
        # Oleo de soja esta em 1.811 das 27.298 linhas da planilha. Marcar
        # "contem soja" em todo prato apagaria o aviso para quem tem alergia de
        # verdade — que e justamente quem precisa dele.
        self.assertEqual(allergens_for_ingredient("OLEO DE SOJA - L")["soja"], Declaration.PODE_CONTER)

    def test_soy_that_is_not_refined_oil_still_says_contains(self):
        for item in ("PROTEINA TEXTURIZADA DE SOJA - KG", "SOJA - KG", "MISSO - PASTA DE SOJA - KG"):
            self.assertEqual(allergens_for_ingredient(item)["soja"], Declaration.CONTEM, item)


class VarianteTest(unittest.TestCase):
    """Um prato do cardapio pode ter varias receitas. Escolher uma seria chutar."""

    def test_variants_that_agree_are_declared(self):
        receitas = read_recipe_rows([
            CABECALHO,
            linha("02.07.03.001", "OVO FRITO - 1", "OVO DE GALINHA - UN"),
            linha("02.07.03.002", "OVO FRITO - 2", "OVO DE GALINHA - UN"),
        ])

        pratos = declarations_by_item(receitas, slugify)
        # "OVO FRITO - 1" e "- 2" caem no mesmo prato? Nao: o sufixo muda o slug.
        # O que importa e que cada um declare o ovo.
        for match in pratos.values():
            self.assertEqual(match.declarations["ovos"], Declaration.CONTEM)

    def test_variants_that_disagree_leave_the_dish_undeclared(self):
        # "CARNE ASSADA AO MOLHO" existe com champignon e ao molho madeira, com
        # ingredientes diferentes. Declarar o de uma seria atribuir ao prato o
        # ingrediente da outra.
        receitas = read_recipe_rows([
            CABECALHO,
            linha("01.01.01.020", "CARNE ASSADA AO MOLHO", "CHAMPIGNON EM CONSERVA - KG"),
            linha("01.01.01.030", "CARNE ASSADA AO MOLHO", "MOLHO MADEIRA - KG"),
        ])

        match = declarations_by_item(receitas, slugify)["carne_assada_ao_molho"]

        self.assertIn("gluten", match.conflicting)
        self.assertNotIn("gluten", match.declarations)
        self.assertTrue(match.ambiguous)

    def test_what_the_variants_agree_on_survives_the_disagreement(self):
        receitas = read_recipe_rows([
            CABECALHO,
            linha("01.01", "TORTA", "QUEIJO MUSSARELA FATIADO 20G - KG"),
            linha("01.01", "TORTA", "FARINHA DE TRIGO BRANCA - KG"),
            linha("01.02", "TORTA", "QUEIJO MUSSARELA FATIADO 20G - KG"),
        ])

        match = declarations_by_item(receitas, slugify)["torta"]

        self.assertEqual(match.declarations["leite"], Declaration.CONTEM)  # as duas tem
        self.assertIn("gluten", match.conflicting)                        # so uma tem


class FichaDeExemploTest(unittest.TestCase):
    """A demonstracao mostra os tres estados. Isso tem que continuar verdade.

    As telas do simulador nascem desta ficha quando a planilha da empresa nao
    esta a mao. Se um dia ela deixar de provar um `contem`, a demonstracao
    passa a mostrar so ⚠️ — e quem assiste conclui que o app nunca sabe de
    nada, que e o oposto do que ele faz.
    """

    def declaracoes(self):
        import csv
        from pathlib import Path

        caminho = Path(__file__).resolve().parent / "fixtures" / "receitas.csv"
        linhas = list(csv.reader(caminho.read_text(encoding="utf-8").splitlines(), delimiter=";"))
        return declarations_by_item(read_recipe_rows(linhas), slugify)

    def test_proves_presence_where_the_ingredient_says_so(self):
        pratos = self.declaracoes()

        self.assertEqual(pratos["ovo_cozido"].declarations["ovos"], Declaration.CONTEM)
        self.assertEqual(pratos["pure_de_batata"].declarations["leite"], Declaration.CONTEM)

    def test_industrialized_ingredient_only_reaches_pode_conter(self):
        molho = self.declaracoes()["bife_suino_ao_molho_barbecue"].declarations

        self.assertEqual(molho["gluten"], Declaration.PODE_CONTER)
        self.assertEqual(molho["soja"], Declaration.PODE_CONTER)

    def test_never_says_a_dish_is_free_of_anything(self):
        # O ✅ do simulador vem de uma declaracao escrita a mao no
        # `demo_telas.py`, nunca daqui: ingrediente prova presenca, nao ausencia.
        for prato in self.declaracoes().values():
            for alergenico, declaracao in prato.declarations.items():
                self.assertNotEqual(
                    declaracao, Declaration.NAO_CONTEM,
                    f"{prato.item_code} afirmou ausencia de {alergenico}",
                )


if __name__ == "__main__":
    unittest.main()
