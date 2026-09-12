"""A ficha de controle nutricional que o funcionario traz.

O que este arquivo protege e a distancia entre "li um documento" e "mandei
alguem comer isso". A ficha e documento clinico: ler errado aqui nao e um bug
de formatacao, e orientacao errada em cima de prescricao de nutricionista.
"""

import sqlite3
import unittest

from apetit.catalog import init_schema
from apetit.prescription import (
    ItemPlano,
    extract_meal_plan,
    load_plan_lunch,
    lunch_from_plan,
    save_plan_lunch,
    ALMOCO_KCAL_MAX,
    FRACAO_ALMOCO_SUGERIDA,
    Prescription,
    extract_prescription,
)

FICHA_DIARIA = """
Plano Alimentar — Clinica Exemplo
Paciente: Mariana
VET: 1800 kcal/dia
Proteinas: 90 g/dia
Carboidratos: 220 g ao dia
Lipideos: 60 g/dia
Evitar: frituras, refrigerante, embutidos
Preferir: legumes, folhas verdes
Nutricionista Responsavel CRN-3 12345
"""

FICHA_POR_REFEICAO = """
Orientacao nutricional
Almoco: 600 kcal
PTN almoco: 35 g
Restricoes: leite, queijo
"""


class LeituraTest(unittest.TestCase):
    def test_reads_the_usual_labels_of_a_brazilian_sheet(self):
        leitura = extract_prescription(FICHA_DIARIA)

        self.assertEqual(leitura.campos["kcal"].valor, 1800)
        self.assertEqual(leitura.campos["ptn_g"].valor, 90)
        self.assertEqual(leitura.campos["cho_g"].valor, 220)
        self.assertEqual(leitura.campos["lip_g"].valor, 60)

    def test_each_value_carries_the_line_it_came_from(self):
        # A pessoa confere a leitura contra o proprio documento: "1800 kcal"
        # sozinho nao diz se saiu da linha certa.
        leitura = extract_prescription(FICHA_DIARIA)

        self.assertIn("VET", leitura.campos["kcal"].trecho)

    def test_a_daily_value_is_marked_as_daily(self):
        leitura = extract_prescription(FICHA_DIARIA)

        self.assertTrue(leitura.campos["kcal"].por_dia)
        self.assertTrue(leitura.algum_valor_diario)

    def test_a_per_meal_value_is_not_marked_as_daily(self):
        leitura = extract_prescription(FICHA_POR_REFEICAO)

        self.assertEqual(leitura.campos["kcal"].valor, 600)
        self.assertFalse(leitura.campos["kcal"].por_dia)
        self.assertFalse(leitura.algum_valor_diario)

    def test_reads_what_the_sheet_forbids(self):
        leitura = extract_prescription(FICHA_DIARIA)

        self.assertEqual(leitura.proibidos, ["frituras", "refrigerante", "embutidos"])

    def test_reads_what_the_sheet_recommends(self):
        leitura = extract_prescription(FICHA_DIARIA)

        self.assertIn("legumes", leitura.recomendados)

    def test_recommended_never_overrides_forbidden(self):
        leitura = extract_prescription("Evitar: queijo\nPreferir: queijo branco, alface")

        self.assertIn("queijo", leitura.proibidos)
        self.assertNotIn("queijo", leitura.recomendados)

    def test_identifies_the_professional_when_the_crn_appears(self):
        leitura = extract_prescription(FICHA_DIARIA)

        self.assertIn("CRN", leitura.profissional)

    def test_a_field_that_is_not_in_the_sheet_stays_out(self):
        # A mesma regra do cardapio: ausencia nao vira valor padrao.
        leitura = extract_prescription("Almoco: 600 kcal")

        self.assertNotIn("ptn_g", leitura.campos)
        self.assertNotIn("cho_g", leitura.campos)

    def test_an_unreadable_document_comes_back_empty_not_guessed(self):
        for entrada in ("", "   ", "Assinatura\nCarimbo\nPagina 1"):
            self.assertTrue(extract_prescription(entrada).vazia, repr(entrada))

    def test_brazilian_decimal_is_read(self):
        leitura = extract_prescription("Proteinas: 1,6 g")

        self.assertEqual(leitura.campos["ptn_g"].valor, 1.6)


class TotalDoDiaTest(unittest.TestCase):
    """O erro que machuca: total do dia virando alvo de uma refeicao."""

    def test_a_daily_sheet_is_not_used_whole_as_a_lunch_target(self):
        ficha = Prescription(kcal=1800, ptn_g=90, escopo="dia", fracao_almoco=FRACAO_ALMOCO_SUGERIDA)

        alvo = ficha.alvo_almoco()

        self.assertLess(alvo["kcal"], 1800)
        self.assertEqual(alvo["kcal"], round(1800 * FRACAO_ALMOCO_SUGERIDA))

    def test_a_per_meal_sheet_is_used_as_is(self):
        ficha = Prescription(kcal=600, ptn_g=35, escopo="almoco")

        self.assertEqual(ficha.alvo_almoco(), {"kcal": 600, "ptn": 35})

    def test_a_daily_total_left_as_a_meal_target_is_flagged_as_implausible(self):
        # Se a reparticao nao acontecer, o app precisa dizer que o numero
        # parece do dia — nao sugerir um prato de 1.800 kcal.
        ficha = Prescription(kcal=1800, escopo="almoco")

        motivo = ficha.implausivel()

        self.assertIn("dia inteiro", motivo)
        self.assertIn("1800", motivo)

    def test_a_plausible_meal_target_is_not_flagged(self):
        self.assertEqual(Prescription(kcal=600, ptn_g=35, escopo="almoco").implausivel(), "")

    def test_the_ceiling_is_a_meal_ceiling_not_a_daily_one(self):
        # Confere que o limite foi pensado para refeicao: 1.500 kcal num prato
        # de refeitorio ja e implausivel.
        self.assertLess(ALMOCO_KCAL_MAX, 1800)

    def test_an_absurd_protein_number_is_flagged_too(self):
        ficha = Prescription(ptn_g=200, escopo="almoco")

        self.assertIn("proteina", ficha.implausivel())


class AplicacaoTest(unittest.TestCase):
    def test_a_sheet_without_numbers_has_no_target(self):
        # Ficha que so lista restricao continua util para o alerta, mas nao
        # muda alvo nenhum.
        ficha = Prescription(proibidos=["frituras"])

        self.assertFalse(ficha.tem_alvo)
        self.assertEqual(ficha.alvo_almoco(), {})

    def test_a_sheet_with_only_calories_still_gives_a_target(self):
        ficha = Prescription(kcal=600, escopo="almoco")

        self.assertTrue(ficha.tem_alvo)
        self.assertEqual(ficha.alvo_almoco(), {"kcal": 600})

    def test_the_scope_is_kept_so_it_is_not_lost_later(self):
        # Guardar so o numero repartido perderia a informacao de que a ficha
        # era do dia — e a proxima leitura nao saberia mais conferir.
        ficha = Prescription(kcal=1800, escopo="dia", fracao_almoco=0.35)

        self.assertEqual(ficha.escopo, "dia")
        self.assertEqual(ficha.kcal, 1800)


class FichaRealTest(unittest.TestCase):
    """Casos tirados de uma ficha de verdade, de plano alimentar.

    A ficha que chegou da empresa nao traz kcal nem grama de macro: e um
    **plano alimentar** por refeicao, com alimento e medida caseira. Ler ela
    procurando "VET: 1800" nao acha nada — e o perigo nao e nao achar, e achar
    errado.
    """

    def test_a_product_name_is_not_a_protein_target(self):
        # "Italac Whey Protein - 1 Caixinha (250ml)", numa lista de
        # substituicao do lanche, virava "proteina = 1". Um grama de proteina
        # como meta do almoco.
        leitura = extract_prescription(
            "Natural Whey - 1 Pote (250g) - ou - Italac Whey Protein - 1 Caixinha (250ml)"
        )

        self.assertNotIn("ptn_g", leitura.campos)

    def test_a_meal_plan_yields_no_macro_target(self):
        plano = (
            "Almoco\n"
            "Arroz branco cozido4 Colher(es) de sopa cheia(s) (100g)Feijao cozido2 Colher servir cheia\n"
            "(70g)Peito de frango sem pele grelhado1.5 File(s) medio(s) (150g)"
        )

        leitura = extract_prescription(plano)

        self.assertEqual(leitura.campos, {})

    def test_a_number_too_small_is_refused_like_one_too_big(self):
        # O teto ja existia; sem o piso, ruido virava meta.
        self.assertIn("pouco demais", Prescription(ptn_g=1, escopo="almoco").implausivel())
        self.assertIn("pouco demais", Prescription(kcal=30, escopo="almoco").implausivel())
        self.assertEqual(Prescription(kcal=600, ptn_g=35, escopo="almoco").implausivel(), "")


if __name__ == "__main__":
    unittest.main()


class PlanoAlimentarTest(unittest.TestCase):
    """O formato que apareceu no mundo real.

    A ficha da empresa nao traz caloria nenhuma: e um plano alimentar por
    refeicao, com alimento e medida caseira. Este bloco usa o texto exato que
    saiu do PDF de verdade.
    """

    ALMOCO = (
        "Almoço\n"
        "Arroz branco cozido4 Colher(es) de sopa cheia(s) (100g)Feijão cozido2 Colher servir cheia\n"
        "(70g)Peito de frango sem pele grelhado1.5 Filé(s) médio(s) (150g)Salada crua ou\n"
        "cozida/refogadaÀ vontadeLaranja1 Unidade(s) pequena(s) (90g)\n"
        "• Opções de substituição para Arroz branco cozido:\n"
        "Purê de batata inglesa - 5 Colher(es) sopa cheia(s) (175g)\n"
    )

    def test_it_reads_the_foods_of_the_lunch(self):
        almoco = lunch_from_plan(extract_meal_plan(self.ALMOCO))

        self.assertEqual(len(almoco), 5)
        self.assertEqual(almoco[0].nome, "Arroz branco cozido")
        self.assertEqual(almoco[0].quantidade, 4)
        self.assertEqual(almoco[0].peso, "100 g")

    def test_a_food_without_a_measure_is_kept_as_a_vontade(self):
        almoco = lunch_from_plan(extract_meal_plan(self.ALMOCO))
        salada = [i for i in almoco if "Salada" in i.nome][0]

        self.assertTrue(salada.a_vontade)
        self.assertIsNone(salada.quantidade)
        self.assertIn("a vontade", salada.descreve())

    def test_the_substitution_list_does_not_become_lunch(self):
        # "No lugar do arroz: pure, batata, mandioca" multiplicaria o almoco por
        # cinco, e o app nao saberia qual foi para o prato.
        almoco = lunch_from_plan(extract_meal_plan(self.ALMOCO))

        self.assertFalse([i for i in almoco if "Purê" in i.nome])

    def test_decimal_quantities_survive(self):
        almoco = lunch_from_plan(extract_meal_plan(self.ALMOCO))
        frango = [i for i in almoco if "frango" in i.nome][0]

        self.assertEqual(frango.quantidade, 1.5)
        self.assertIn("1.5", frango.descreve())

    def test_meals_are_kept_apart(self):
        texto = (
            "Café da manhã\nPão de forma integral2 Fatia(s) (50g)\n"
            "Almoço\nArroz branco cozido4 Colher(es) de sopa cheia(s) (100g)\n"
            "Jantar\nOvo de galinha1 Unidade(s) (50g)\n"
        )

        plano = extract_meal_plan(texto)

        self.assertEqual(set(plano), {"Café da manhã", "Almoço", "Jantar"})
        self.assertEqual(lunch_from_plan(plano)[0].nome, "Arroz branco cozido")

    def test_a_volume_without_parentheses_does_not_swallow_the_next_food(self):
        # "Café150mlBanana1 Unidade (45g)" — sem separador, o café engolia a
        # banana e virava um item so.
        plano = extract_meal_plan("Café da manhã\nCafé150mlBanana1 Unidade(s) (45g)\n")
        itens = plano["Café da manhã"]

        self.assertEqual([i.nome for i in itens], ["Café", "Banana"])

    def test_the_signature_footer_is_not_a_food(self):
        texto = (
            "Almoço\nArroz branco cozido4 Colher(es) de sopa cheia(s) (100g)\n"
            "Digitally signed by ANA ENDRICA LIMA BARRETO:609.892.653-07-\n"
            "12/03/2026\nNUTRIÇÃO\n"
        )

        almoco = lunch_from_plan(extract_meal_plan(texto))

        self.assertEqual([i.nome for i in almoco], ["Arroz branco cozido"])

    def test_a_sheet_with_no_meals_yields_nothing(self):
        self.assertEqual(extract_meal_plan("VET: 1800 kcal/dia"), {})
        self.assertEqual(extract_meal_plan(""), {})


class PlanoGuardadoTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_the_lunch_survives_a_round_trip(self):
        itens = [
            ItemPlano("Arroz branco cozido", 4, "Colher(es) de sopa cheia(s)", "100 g"),
            ItemPlano("Salada", None, "a vontade", ""),
        ]

        save_plan_lunch(self.conn, 1, itens)
        voltou = load_plan_lunch(self.conn, 1)

        self.assertEqual([i.nome for i in voltou], [i.nome for i in itens])
        self.assertTrue(voltou[1].a_vontade)

    def test_saving_again_replaces_instead_of_stacking(self):
        save_plan_lunch(self.conn, 1, [ItemPlano("Arroz", 4, "colheres")])
        save_plan_lunch(self.conn, 1, [ItemPlano("Macarrao", 2, "colheres")])

        self.assertEqual([i.nome for i in load_plan_lunch(self.conn, 1)], ["Macarrao"])

    def test_deleting_my_data_takes_the_plan_with_it(self):
        from apetit.profile import Employee, delete_employee_data, save_employee

        save_employee(self.conn, Employee(telegram_id=1, name="M", apetit_unit="SM",
                                          client_company="I", sector="P", consent_accepted=True))
        save_plan_lunch(self.conn, 1, [ItemPlano("Arroz", 4, "colheres")])

        delete_employee_data(self.conn, 1)

        self.assertEqual(load_plan_lunch(self.conn, 1), [])
