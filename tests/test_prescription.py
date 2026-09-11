"""A ficha de controle nutricional que o funcionario traz.

O que este arquivo protege e a distancia entre "li um documento" e "mandei
alguem comer isso". A ficha e documento clinico: ler errado aqui nao e um bug
de formatacao, e orientacao errada em cima de prescricao de nutricionista.
"""

import unittest

from apetit.prescription import (
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


if __name__ == "__main__":
    unittest.main()
