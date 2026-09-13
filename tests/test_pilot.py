"""Relatorio do piloto.

Duas coisas sao testadas aqui, e a segunda e a que importa mais: que os numeros
de adesao estao certos, e que **nenhum deles identifica uma pessoa**. Num piloto
de 15, um recorte a mais e um funcionario exposto.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.catalog import import_menu_csv, init_schema
from apetit.feedback import Rating, save_rating
from apetit.pilot import MIN_AGGREGATE, daily_activity, format_report, pilot_report, weeks_in
from apetit.prescription import Prescription, save_prescription
from apetit.profile import Employee, save_employee
from apetit.tracking import add_favorite, log_consumption

FIXTURES = Path(__file__).parent / "fixtures"


class PilotoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        import_menu_csv(self.conn, (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"), unit="SM")

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def pessoa(self, uid, objetivo="Manter o equilibrio", setor="Producao"):
        save_employee(self.conn, Employee(
            pessoa_id=uid, name=f"P{uid}", apetit_unit="SM", client_company="Industria",
            sector=setor, goal=objetivo, consent_accepted=True,
        ))

    def grupo(self, quantos=15, usaram=10, voltaram=6):
        """Monta um piloto parecido com o real: nem todo cadastrado usa."""
        for i in range(quantos):
            self.pessoa(100 + i, objetivo="Comer mais leve" if i % 3 else "Manter o equilibrio")
        for i in range(usaram):
            log_consumption(self.conn, 100 + i, "2026-09-01", ["arroz_parboilizado"])
            if i < voltaram:
                log_consumption(self.conn, 100 + i, "2026-09-02", ["sal_mix_de_alface"])


class AdesaoTest(PilotoBase):
    def test_counts_who_signed_up_who_used_and_who_came_back(self):
        self.grupo(quantos=15, usaram=10, voltaram=6)

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07", convidados=15)

        self.assertEqual(rel.cadastrados, 15)
        self.assertEqual(rel.ativos, 10)
        self.assertEqual(rel.voltaram, 6)

    def test_the_three_rates_answer_different_questions(self):
        self.grupo(quantos=15, usaram=10, voltaram=6)

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07", convidados=20)

        self.assertEqual(rel.adesao_pct, 75.0)      # 15 de 20 convidados
        self.assertAlmostEqual(rel.ativacao_pct, 66.7, places=0)  # 10 dos 15 cadastrados
        self.assertEqual(rel.retencao_pct, 60.0)    # 6 dos 10 que usaram

    def test_rates_are_none_instead_of_zero_when_there_is_no_base(self):
        # Dividir por zero viraria "0%", que mentiria dizendo que ninguem aderiu.
        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        self.assertIsNone(rel.adesao_pct)
        self.assertIsNone(rel.ativacao_pct)
        self.assertIsNone(rel.retencao_pct)

    def test_days_per_person_measures_habit(self):
        self.grupo(quantos=4, usaram=4, voltaram=4)

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        self.assertEqual(rel.dias_por_pessoa, 2.0)

    def test_the_daily_curve_shows_whether_it_caught_on(self):
        self.grupo(quantos=10, usaram=10, voltaram=4)

        curva = daily_activity(self.conn, "2026-09-01", "2026-09-07")

        self.assertEqual(curva, [("2026-09-01", 10), ("2026-09-02", 4)])

    def test_only_the_period_is_counted(self):
        self.grupo(quantos=5, usaram=5, voltaram=0)
        log_consumption(self.conn, 100, "2026-08-01", ["arroz_parboilizado"])

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        self.assertEqual(rel.registros, 5)


class UsoPorFuncaoTest(PilotoBase):
    def datar(self, tabela, coluna, quando="2026-09-02T12:00:00+00:00"):
        """Poe a linha dentro do periodo pedido.

        `add_favorite` e `save_prescription` carimbam o instante de agora, que
        nunca cai na janela de um relatorio de periodo fechado.
        """
        self.conn.execute(f"UPDATE {tabela} SET {coluna} = ?", (quando,))
        self.conn.commit()

    def test_measures_which_features_people_actually_use(self):
        self.grupo(quantos=5, usaram=5, voltaram=0)
        add_favorite(self.conn, 100, "arroz_parboilizado")
        save_rating(self.conn, 101, Rating(apetit_unit="SM", service_date="2026-09-01", food=3))
        save_prescription(self.conn, 102, Prescription(kcal=600, escopo="almoco"))
        self.datar("favorite", "created_at")
        self.datar("employee_prescription", "updated_at")

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")
        por_nome = {f.nome: f for f in rel.funcoes}

        self.assertEqual(por_nome["Registrar a refeição"].pessoas, 5)
        self.assertEqual(por_nome["Guardar favorito"].pessoas, 1)
        self.assertEqual(por_nome["Avaliar o refeitório"].pessoas, 1)
        self.assertEqual(por_nome["Ficha nutricional"].pessoas, 1)

    def test_activity_outside_the_period_does_not_inflate_adoption(self):
        # O relatorio e de um periodo. Contar favorito e ficha de sempre faria
        # um piloto anterior aparecer como adesao deste.
        self.grupo(quantos=5, usaram=5, voltaram=0)
        add_favorite(self.conn, 100, "arroz_parboilizado")
        save_prescription(self.conn, 102, Prescription(kcal=600, escopo="almoco"))
        self.datar("favorite", "created_at", "2026-08-01T12:00:00+00:00")
        self.datar("employee_prescription", "updated_at", "2026-08-01T12:00:00+00:00")

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")
        por_nome = {f.nome: f for f in rel.funcoes}

        self.assertEqual(por_nome["Guardar favorito"].pessoas, 0)
        self.assertEqual(por_nome["Ficha nutricional"].pessoas, 0)

    def test_a_lone_prescription_is_not_reported_to_the_employer(self):
        """"Ficha nutricional: 1 pessoa" conta a empresa quem foi ao nutricionista.

        Ter acompanhamento nutricional e dado de saude. Num piloto de 15, o
        numero 1 aponta para alguem tanto quanto o nome — e o relatorio ja
        suprime objetivo em grupo pequeno pelo mesmo motivo.
        """
        self.grupo(quantos=5, usaram=5, voltaram=0)
        save_prescription(self.conn, 102, Prescription(kcal=600, escopo="almoco"))
        self.datar("employee_prescription", "updated_at")

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")
        ficha = next(f for f in rel.funcoes if f.nome == "Ficha nutricional")

        self.assertTrue(ficha.suprimida)
        texto = "\n".join(format_report(rel))
        self.assertIn("Ficha nutricional", texto)
        self.assertNotIn("Ficha nutricional             1 pessoa(s)", texto)
        self.assertRegex(texto, r"Ficha nutricional\s+suprimido")

    def test_following_the_suggestion_is_counted_apart_from_building_by_hand(self):
        # Sao habitos diferentes: seguir a sugestao e um toque, montar e escolha.
        self.pessoa(1)
        log_consumption(self.conn, 1, "2026-09-01", ["arroz_parboilizado"], source="sugestao")
        self.pessoa(2)
        log_consumption(self.conn, 2, "2026-09-01", ["arroz_parboilizado"], source="montado")

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")
        por_nome = {f.nome: f for f in rel.funcoes}

        self.assertEqual(por_nome["Registrar a refeição"].pessoas, 2)
        self.assertEqual(por_nome["Seguir a sugestão de porção"].pessoas, 1)


class NaoExpoePessoaTest(PilotoBase):
    """A protecao central: o relatorio responde pelo grupo, nunca pela pessoa."""

    def test_no_field_in_the_report_carries_a_person(self):
        self.grupo(quantos=15, usaram=10, voltaram=6)

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        texto = " ".join(str(v) for v in vars(rel).values())
        for proibido in ("telegram", "100", "101", "P100"):
            if proibido.isdigit():
                continue  # numero solto pode ser contagem legitima
            self.assertNotIn(proibido, texto)
        self.assertFalse(any("nome" in c or "name" in c for c in vars(rel)))

    def test_a_small_goal_group_is_suppressed(self):
        # Num piloto de 15, "1 pessoa quer emagrecer" identifica alguem.
        for i in range(MIN_AGGREGATE + 2):
            self.pessoa(200 + i, objetivo="Manter o equilibrio")
        self.pessoa(999, objetivo="Reforcar a proteina")

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")
        por_objetivo = dict(rel.objetivos)

        self.assertEqual(por_objetivo["Manter o equilibrio"], MIN_AGGREGATE + 2)
        self.assertIsNone(por_objetivo["Reforcar a proteina"])

    def test_ratings_are_suppressed_below_the_minimum(self):
        self.grupo(quantos=5, usaram=1, voltaram=0)
        for i in range(MIN_AGGREGATE - 1):
            save_rating(self.conn, 300 + i, Rating(apetit_unit="SM", service_date="2026-09-01", food=1))

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        self.assertEqual(rel.avaliacoes, MIN_AGGREGATE - 1)
        self.assertIsNone(rel.comida_boa_pct)

    def test_the_printed_report_says_what_it_leaves_out(self):
        # Quem le precisa saber que a ausencia e deliberada, nao esquecimento.
        self.grupo(quantos=5, usaram=3, voltaram=1)

        texto = "\n".join(format_report(pilot_report(self.conn, "2026-09-01", "2026-09-07")))

        self.assertIn("O QUE NAO ENTRA NESTE RELATORIO", texto)
        self.assertIn("dado de saude", texto)

    def test_the_printed_report_never_prints_a_name(self):
        self.grupo(quantos=6, usaram=4, voltaram=2)

        texto = "\n".join(format_report(pilot_report(self.conn, "2026-09-01", "2026-09-07")))

        for uid in range(100, 106):
            self.assertNotIn(f"P{uid}", texto)


class QualidadeDoDadoTest(PilotoBase):
    def test_reports_how_complete_the_menu_data_is(self):
        # Limita o que o app consegue entregar, entao entra no relatorio.
        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        self.assertGreater(rel.itens_cardapio, 0)
        self.assertEqual(rel.cobertura_alergenico_pct, 0.0)
        self.assertIsNotNone(rel.cobertura_macro_pct)

    def test_coverage_is_none_when_there_is_no_menu(self):
        vazio = sqlite3.connect(":memory:")
        vazio.row_factory = sqlite3.Row
        init_schema(vazio)

        rel = pilot_report(vazio, "2026-09-01", "2026-09-07")

        self.assertIsNone(rel.cobertura_macro_pct)
        vazio.close()

    def test_counts_how_many_declared_a_restriction(self):
        from apetit.allergens import Restriction

        save_employee(self.conn, Employee(
            pessoa_id=1, name="A", apetit_unit="SM", client_company="I", sector="P",
            consent_accepted=True, restrictions=[Restriction("leite")],
        ))

        rel = pilot_report(self.conn, "2026-09-01", "2026-09-07")

        self.assertEqual(rel.pessoas_com_restricao, 1)


class PeriodoTest(unittest.TestCase):
    def test_counts_the_weeks_of_the_pilot(self):
        self.assertEqual(weeks_in("2026-09-01", "2026-09-07"), 1)
        self.assertEqual(weeks_in("2026-09-01", "2026-09-28"), 4)
        self.assertEqual(weeks_in("lixo", "2026-09-28"), 0)


if __name__ == "__main__":
    unittest.main()
