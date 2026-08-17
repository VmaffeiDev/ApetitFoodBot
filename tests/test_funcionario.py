import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.allergens import (
    ALLERGENS,
    Declaration,
    Restriction,
    RestrictionKind,
    Verdict,
    check_item,
    coverage,
)
from apetit.catalog import (
    check_menu_for_employee,
    import_menu_csv,
    init_schema,
    item_allergens,
    set_item_allergens,
)
from apetit.profile import Employee, aggregate_by_sector, delete_employee_data, load_employee, save_employee
from apetit.tracking import (
    BASES_PROIBIDAS,
    RULES,
    add_favorite,
    consumption_history,
    consumption_totals,
    favorites,
    favorites_returning,
    history_by_day,
    log_consumption,
    points_breakdown,
    score_day,
    score_week,
    total_points,
)

FIXTURES = Path(__file__).parent / "fixtures"


class SafetyCheckTest(unittest.TestCase):
    """A regra que mais importa: sem dado declarado, o app nao libera."""

    def test_declared_allergen_blocks(self):
        check = check_item(
            [Restriction("leite")],
            {"leite": Declaration.CONTEM},
        )

        self.assertIs(check.verdict, Verdict.BLOQUEIO)
        self.assertFalse(check.safe_to_affirm)
        self.assertIn("Leite", check.message())

    def test_undeclared_allergen_is_never_treated_as_safe(self):
        # A ficha nao diz nada sobre leite. Isso e incerteza, nao liberacao.
        check = check_item([Restriction("leite")], {})

        self.assertIs(check.verdict, Verdict.ATENCAO)
        self.assertFalse(check.safe_to_affirm)
        self.assertIn("Confirme no balcao", check.message())

    def test_partial_declaration_still_warns(self):
        # Glúten foi descartado, mas ninguem declarou ovo.
        check = check_item(
            [Restriction("gluten"), Restriction("ovos")],
            {"gluten": Declaration.NAO_CONTEM},
        )

        self.assertIs(check.verdict, Verdict.ATENCAO)
        self.assertEqual(check.nao_declarado, ["ovos"])

    def test_may_contain_warns_instead_of_releasing(self):
        check = check_item([Restriction("amendoim")], {"amendoim": Declaration.PODE_CONTER})

        self.assertIs(check.verdict, Verdict.ATENCAO)
        self.assertFalse(check.safe_to_affirm)

    def test_only_full_declaration_releases(self):
        check = check_item(
            [Restriction("gluten"), Restriction("ovos")],
            {"gluten": Declaration.NAO_CONTEM, "ovos": Declaration.NAO_CONTEM},
        )

        self.assertIs(check.verdict, Verdict.LIBERADO)
        self.assertTrue(check.safe_to_affirm)

    def test_person_without_restrictions(self):
        check = check_item([], {})

        self.assertIs(check.verdict, Verdict.SEM_RESTRICAO)
        self.assertTrue(check.safe_to_affirm)

    def test_coverage_measures_how_much_the_dish_declares(self):
        self.assertEqual(coverage({}), 0.0)
        cheio = {code: Declaration.NAO_CONTEM for code in ALLERGENS}
        self.assertEqual(coverage(cheio), 1.0)


class BancoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        import_menu_csv(
            self.conn,
            (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"),
            unit="SM",
        )
        self.user = 4242
        save_employee(
            self.conn,
            Employee(
                telegram_id=self.user,
                name="Funcionario Teste",
                apetit_unit="SM",
                client_company="Industria Exemplo",
                sector="Producao",
                goal="manter",
                consent_accepted=True,
                restrictions=[Restriction("leite", RestrictionKind.ALERGIA)],
            ),
        )

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)


class CadastroTest(BancoBase):
    def test_saves_and_loads_the_full_link_to_the_company(self):
        pessoa = load_employee(self.conn, self.user)

        self.assertEqual(pessoa.apetit_unit, "SM")
        self.assertEqual(pessoa.client_company, "Industria Exemplo")
        self.assertEqual(pessoa.sector, "Producao")
        self.assertEqual([r.allergen for r in pessoa.restrictions], ["leite"])
        self.assertTrue(pessoa.registered)

    def test_registration_is_incomplete_without_sector_or_consent(self):
        sem_setor = Employee(telegram_id=1, name="X", apetit_unit="SM", client_company="Y", consent_accepted=True)
        self.assertFalse(sem_setor.registered)
        self.assertIn("setor", sem_setor.missing_fields())

        sem_consent = Employee(telegram_id=2, name="X", apetit_unit="SM", client_company="Y", sector="Z")
        self.assertFalse(sem_consent.registered)
        self.assertIn("aceite do termo de privacidade", sem_consent.missing_fields())

    def test_unknown_allergen_is_rejected(self):
        with self.assertRaises(ValueError):
            save_employee(
                self.conn,
                Employee(telegram_id=9, name="X", restrictions=[Restriction("gluten_falso")]),
            )

    def test_small_sector_is_suppressed_in_aggregate(self):
        linhas = aggregate_by_sector(self.conn, "Industria Exemplo")

        self.assertEqual(len(linhas), 1)
        self.assertTrue(linhas[0]["suprimido"])
        self.assertIsNone(linhas[0]["total"])

    def test_delete_removes_everything_of_the_person(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho"])
        add_favorite(self.conn, self.user, "macarrao_alho_e_oleo")
        score_day(self.conn, self.user, "2025-09-01")

        delete_employee_data(self.conn, self.user)

        self.assertIsNone(load_employee(self.conn, self.user))
        self.assertEqual(consumption_history(self.conn, self.user), [])
        self.assertEqual(favorites(self.conn, self.user), [])
        self.assertEqual(total_points(self.conn, self.user), 0)


class AvisoNoCardapioTest(BancoBase):
    def test_menu_flags_the_dish_that_contains_the_allergen(self):
        set_item_allergens(self.conn, "carne_assada_ao_molho", {"leite": "contem"})

        cardapio = check_menu_for_employee(self.conn, "2025-09-01", [Restriction("leite")], unit="SM")
        strogonoff = next(i for i in cardapio if i["item_code"] == "carne_assada_ao_molho")

        self.assertIs(strogonoff["check"].verdict, Verdict.BLOQUEIO)

    def test_dish_without_declaration_is_attention_not_release(self):
        cardapio = check_menu_for_employee(self.conn, "2025-09-01", [Restriction("leite")], unit="SM")

        vereditos = {i["check"].verdict for i in cardapio}
        self.assertEqual(vereditos, {Verdict.ATENCAO})
        self.assertTrue(all(not i["check"].safe_to_affirm for i in cardapio))

    def test_declared_free_dish_is_released(self):
        set_item_allergens(self.conn, "sal_mix_de_alface", {"leite": "nao_contem"})

        cardapio = check_menu_for_employee(self.conn, "2025-09-01", [Restriction("leite")], unit="SM")
        alface = next(i for i in cardapio if i["item_code"] == "sal_mix_de_alface")

        self.assertIs(alface["check"].verdict, Verdict.LIBERADO)

    def test_allergen_declaration_round_trips(self):
        set_item_allergens(self.conn, "macarrao_alho_e_oleo", {"gluten": "pode_conter", "leite": "nao_contem"})

        gravado = item_allergens(self.conn, "macarrao_alho_e_oleo")
        self.assertEqual(gravado["gluten"], Declaration.PODE_CONTER)
        self.assertEqual(gravado["leite"], Declaration.NAO_CONTEM)


class HistoricoEFavoritoTest(BancoBase):
    def test_logs_what_was_eaten_and_sums_the_macros(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "arroz_parboilizado"])

        totais = consumption_totals(self.conn, self.user, "2025-09-01")
        self.assertEqual(totais["itens"], 2)
        self.assertAlmostEqual(totais["kcal"], 134 + 138, places=0)

    def test_relogging_the_same_day_replaces_instead_of_duplicating(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "arroz_parboilizado"])
        log_consumption(self.conn, self.user, "2025-09-01", ["sal_mix_de_alface"])

        totais = consumption_totals(self.conn, self.user, "2025-09-01")
        self.assertEqual(totais["itens"], 1)

    def test_history_keeps_everything_already_eaten(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho"])
        log_consumption(self.conn, self.user, "2025-09-02", ["file_de_frango_grelhado"])

        historico = consumption_history(self.conn, self.user)
        self.assertEqual([linha["service_date"] for linha in historico], ["2025-09-02", "2025-09-01"])

    def test_favorite_returns_when_the_dish_comes_back_to_the_menu(self):
        add_favorite(self.conn, self.user, "arroz_parboilizado")

        voltando = favorites_returning(self.conn, "2025-09-01", "2025-09-07", apetit_unit="SM")

        nomes = {linha["item_name"] for linha in voltando}
        self.assertIn("ARROZ PARBOILIZADO", nomes)
        self.assertEqual(voltando[0]["telegram_id"], self.user)

    def test_favorite_not_on_the_menu_does_not_notify(self):
        add_favorite(self.conn, self.user, "sal_vinagrete")

        voltando = favorites_returning(self.conn, "2025-09-01", "2025-09-02", apetit_unit="SM")

        self.assertEqual([linha["item_name"] for linha in voltando], [])

    def test_repeating_the_code_becomes_quantity(self):
        # Duas conchas de feijao chegam como o mesmo codigo duas vezes.
        log_consumption(self.conn, self.user, "2025-09-01", ["arroz_parboilizado", "arroz_parboilizado"])

        historico = consumption_history(self.conn, self.user)
        self.assertEqual(len(historico), 1)
        self.assertEqual(historico[0]["quantity"], 2)

    def test_quantity_multiplies_the_macros_of_the_day(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["arroz_parboilizado", "arroz_parboilizado"])

        totais = consumption_totals(self.conn, self.user, "2025-09-01")
        self.assertEqual(totais["itens"], 2)
        self.assertAlmostEqual(totais["kcal"], 138 * 2, places=0)

    def test_history_records_the_category_it_was_served_in(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["sal_mix_de_alface"])

        historico = consumption_history(self.conn, self.user)
        self.assertEqual(historico[0]["category"], "SALADA")


class HistoricoEFotografiaTest(BancoBase):
    """Historico e fotografia: corrigir a ficha tecnica nao reescreve o passado."""

    def test_correcting_the_recipe_does_not_change_what_was_already_eaten(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["arroz_parboilizado"])
        antes = consumption_totals(self.conn, self.user, "2025-09-01")["kcal"]

        # A operacao corrige a ficha tecnica meses depois.
        self.conn.execute("UPDATE menu_item SET kcal = 999, name = 'ARROZ CORRIGIDO' WHERE code = ?",
                          ("arroz_parboilizado",))
        self.conn.commit()

        depois = consumption_totals(self.conn, self.user, "2025-09-01")
        historico = consumption_history(self.conn, self.user)

        self.assertAlmostEqual(depois["kcal"], antes, places=0)
        self.assertEqual(historico[0]["name"], "ARROZ PARBOILIZADO")

    def test_a_new_meal_uses_the_corrected_recipe(self):
        # A fotografia congela o passado, nao o futuro.
        self.conn.execute("UPDATE menu_item SET kcal = 200 WHERE code = ?", ("arroz_parboilizado",))
        self.conn.commit()

        log_consumption(self.conn, self.user, "2025-09-02", ["arroz_parboilizado"])

        self.assertAlmostEqual(
            consumption_totals(self.conn, self.user, "2025-09-02")["kcal"], 200, places=0
        )

    def test_item_missing_from_the_base_keeps_the_record_without_inventing_macros(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["prato_que_sumiu"])

        historico = consumption_history(self.conn, self.user)
        self.assertEqual(historico[0]["name"], "prato_que_sumiu")
        self.assertIsNone(historico[0]["kcal"])
        self.assertEqual(consumption_totals(self.conn, self.user, "2025-09-01")["sem_macro"], 1)

    def test_history_by_day_groups_the_plate_with_its_totals(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "arroz_parboilizado"])
        log_consumption(self.conn, self.user, "2025-09-02", ["file_de_frango_grelhado"])

        dias = history_by_day(self.conn, self.user)

        self.assertEqual([d.service_date for d in dias], ["2025-09-02", "2025-09-01"])
        self.assertEqual(len(dias[1].items), 2)
        self.assertAlmostEqual(dias[1].kcal, 134 + 138, places=0)
        self.assertFalse(dias[1].incomplete)

    def test_an_item_with_calories_but_no_protein_still_counts_as_unknown(self):
        # kcal sem proteina nao da para ler: somar so a metade produz um numero
        # que parece total e nao e.
        self.conn.execute("UPDATE menu_item SET ptn_g = NULL WHERE code = ?", ("arroz_parboilizado",))
        self.conn.commit()

        log_consumption(self.conn, self.user, "2025-09-01", ["arroz_parboilizado"])

        totais = consumption_totals(self.conn, self.user, "2025-09-01")
        self.assertEqual(totais["sem_macro"], 1)
        self.assertEqual(totais["com_macro"], 0)
        self.assertTrue(history_by_day(self.conn, self.user)[0].incomplete)

    def test_a_day_with_an_unknown_item_is_flagged_as_incomplete(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "prato_que_sumiu"])

        dias = history_by_day(self.conn, self.user)

        self.assertTrue(dias[0].incomplete)

    def test_old_database_gets_the_snapshot_backfilled(self):
        # Banco gravado antes da fotografia existir: a migracao nao pode perder
        # o historico de ninguem.
        antigo = tempfile.NamedTemporaryFile(delete=False)
        antigo.close()
        self.addCleanup(Path(antigo.name).unlink, missing_ok=True)
        conn = sqlite3.connect(antigo.name)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE menu_item (code TEXT PRIMARY KEY, name TEXT NOT NULL, portion_g REAL,
                kcal REAL, cho_g REAL, lip_g REAL, ptn_g REAL, updated_at TEXT NOT NULL);
            CREATE TABLE menu_entry (id INTEGER PRIMARY KEY AUTOINCREMENT, unit TEXT NOT NULL,
                service_date TEXT NOT NULL, meal TEXT NOT NULL, category TEXT NOT NULL,
                slot INTEGER NOT NULL DEFAULT 1, item_code TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE consumption (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
                service_date TEXT NOT NULL, meal TEXT NOT NULL DEFAULT 'almoco',
                item_code TEXT NOT NULL, logged_at TEXT NOT NULL);
            INSERT INTO menu_item VALUES ('feijao_preto', 'FEIJAO PRETO', 80, 29, 4, 0.2, 1.8, '2025-09-01');
            INSERT INTO menu_entry (unit, service_date, meal, category, item_code, created_at)
                VALUES ('SM', '2025-09-01', 'almoco', 'FEIJAO', 'feijao_preto', '2025-09-01');
            INSERT INTO consumption (telegram_id, service_date, item_code, logged_at)
                VALUES (7, '2025-09-01', 'feijao_preto', '2025-09-01T12:00:00+00:00');
            """
        )
        conn.commit()

        init_schema(conn)

        historico = consumption_history(conn, 7)
        self.assertEqual(historico[0]["name"], "FEIJAO PRETO")
        self.assertEqual(historico[0]["category"], "FEIJAO")
        self.assertEqual(historico[0]["quantity"], 1)
        self.assertAlmostEqual(historico[0]["kcal"], 29, places=0)
        conn.close()


class PontuacaoTest(BancoBase):
    def test_no_rule_rewards_eating_less(self):
        # A intencao do produto fica testavel: nada aqui pontua deficit ou peso.
        for regra in RULES:
            self.assertNotIn(regra.basis, BASES_PROIBIDAS, f"regra {regra.code} premia {regra.basis}")

    def test_logging_the_meal_scores(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho"])

        concedidas = score_day(self.conn, self.user, "2025-09-01")

        self.assertIn("registro", {r.code for r in concedidas})
        self.assertEqual(total_points(self.conn, self.user), 10)

    def test_salad_or_fruit_adds_composition_points(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "sal_mix_de_alface"])

        concedidas = score_day(self.conn, self.user, "2025-09-01")

        self.assertEqual({r.code for r in concedidas}, {"registro", "composicao"})
        self.assertEqual(total_points(self.conn, self.user), 15)

    def test_protein_target_scores_only_when_reached(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho"])

        score_day(self.conn, self.user, "2025-09-01", protein_target_g=30)
        self.assertEqual(total_points(self.conn, self.user), 10)

        score_day(self.conn, self.user, "2025-09-01", protein_target_g=10)
        self.assertEqual(total_points(self.conn, self.user), 20)

    def test_rescoring_the_same_day_does_not_double_points(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "sal_mix_de_alface"])

        score_day(self.conn, self.user, "2025-09-01")
        score_day(self.conn, self.user, "2025-09-01")
        score_day(self.conn, self.user, "2025-09-01")

        self.assertEqual(total_points(self.conn, self.user), 15)

    def test_weekly_variety_scores(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "arroz_parboilizado", "sal_mix_de_alface"])
        log_consumption(self.conn, self.user, "2025-09-02", ["file_de_frango_grelhado", "arroz_integral", "sal_beterraba_ralada"])

        concedidas = score_week(self.conn, self.user, "2025-09-01")

        self.assertIn("variedade", {r.code for r in concedidas})

    def test_breakdown_explains_where_the_points_came_from(self):
        log_consumption(self.conn, self.user, "2025-09-01", ["carne_assada_ao_molho", "sal_mix_de_alface"])
        score_day(self.conn, self.user, "2025-09-01")

        extrato = {linha["rule_code"]: linha["pontos"] for linha in points_breakdown(self.conn, self.user)}

        self.assertEqual(extrato, {"registro": 10, "composicao": 5})


if __name__ == "__main__":
    unittest.main()
