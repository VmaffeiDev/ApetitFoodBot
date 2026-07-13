import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("APETIT_DB_PATH", "test-apetit.db")

import bot


def monday_of_this_week() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


class CoreLogicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        bot.DB_PATH = Path(self.tmp.name)
        bot.init_db()

    def tearDown(self):
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except PermissionError:
            pass

    def seed_dish(self, key, name, base_category, ingredients="", allergens="", tags=""):
        with bot.db() as conn:
            conn.execute(
                """
                INSERT INTO dishes (dish_key, dish_name, base_category, ingredients, allergens, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key, name, base_category, ingredients, allergens, tags, bot.now_iso(), bot.now_iso()),
            )

    def test_parse_menu_cell_extracts_code_and_name(self):
        code, name = bot.parse_menu_cell("100% - 08.03.01.110 - X-FRANGO - 2.28")
        self.assertEqual(code, "08.03.01.110")
        self.assertEqual(name, "X-FRANGO")

    def test_parse_menu_cell_returns_none_for_empty(self):
        self.assertIsNone(bot.parse_menu_cell(None))
        self.assertIsNone(bot.parse_menu_cell("  "))

    def test_base_category_strips_trailing_number(self):
        self.assertEqual(bot.base_category_from_slot("ACOMPANHAMENTO 2"), "ACOMPANHAMENTO")
        self.assertEqual(bot.base_category_from_slot("BEBIDA"), "BEBIDA")

    def test_restriction_conflict_blocks_unsafe_items(self):
        self.seed_dish("frango", "Frango Grelhado", "LANCHE", ingredients="frango, arroz", tags="proteico")
        self.seed_dish("lasanha", "Lasanha", "LANCHE", ingredients="massa, molho de tomate, queijo", allergens="leite")
        frango = bot.load_dish("frango")
        lasanha = bot.load_dish("lasanha")

        self.assertEqual(bot.restriction_conflict("Vegetariana", frango), "nao esta marcado como vegetariano")
        self.assertEqual(bot.restriction_conflict("Sem lactose", lasanha), "contem lactose ou derivados de leite")
        self.assertIsNone(bot.restriction_conflict("Sem gluten", frango))

    def test_best_dish_for_goal_excludes_restriction_conflicts(self):
        self.seed_dish("peixe", "Peixe Assado", "LANCHE", ingredients="peixe", tags="leve, proteico")
        self.seed_dish("salada", "Salada de Grao de Bico", "LANCHE", ingredients="grao de bico, legumes", tags="leve, vegano, perda de peso")
        dishes = [bot.load_dish("peixe"), bot.load_dish("salada")]

        best = bot.best_dish_for_goal(dishes, "Sem frutos do mar", "Perder peso")

        self.assertEqual(best["dish_key"], "salada")

    def test_restriction_conflicts_for_dates_reports_menu_date_and_dish(self):
        self.seed_dish("peixe", "Peixe Assado", "LANCHE", ingredients="peixe")
        self.seed_dish("salada", "Salada", "LANCHE", ingredients="grao de bico")
        with bot.db() as conn:
            for slot, key, name in [("LANCHE", "peixe", "Peixe Assado"), ("LANCHE 2", "salada", "Salada")]:
                conn.execute(
                    "INSERT INTO daily_menu (menu_date, slot, base_category, dish_key, dish_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-07-14", slot, "LANCHE", key, name, bot.now_iso()),
                )

        conflicts = bot.restriction_conflicts_for_dates("Sem frutos do mar", ["2026-07-14"])

        self.assertEqual(conflicts, [("2026-07-14", "Peixe Assado", "contem peixe ou frutos do mar")])

    def test_restriction_conflicts_for_dates_empty_without_restriction(self):
        self.assertEqual(bot.restriction_conflicts_for_dates("", ["2026-07-14"]), [])

    def test_pending_dish_names_for_dates_lists_dishes_without_metadata(self):
        self.seed_dish("novo", "Prato Novo", "LANCHE")
        self.seed_dish("completo", "Prato Completo", "LANCHE", ingredients="arroz")
        with bot.db() as conn:
            for key, name in [("novo", "Prato Novo"), ("completo", "Prato Completo")]:
                conn.execute(
                    "INSERT INTO daily_menu (menu_date, slot, base_category, dish_key, dish_name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    ("2026-07-14", key.upper(), "LANCHE", key, name, bot.now_iso()),
                )

        pending = bot.pending_dish_names_for_dates(["2026-07-14"])

        self.assertEqual(pending, ["Prato Novo"])

    def test_import_menu_workbook_creates_dishes_and_daily_menu(self):
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Oficina do lanche"
        sheet.append(["Dia", "LANCHE", "ACOMPANHAMENTO", "ACOMPANHAMENTO 2"])
        sheet.append(["14", "100% - 08.03.01.110 - X-FRANGO - 2.28", "100% - 03.01.02.190 - BATATA CHIPS - 0.55", "100% - 09.03.01.050 - KIT - LANCHE - 0.08"])
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp_file:
            workbook.save(tmp_file.name)
            tmp_path = tmp_file.name
        try:
            result = bot.import_menu_workbook(tmp_path, 2026, 7)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        self.assertEqual(result["days"], 1)
        self.assertEqual(result["dates"], ["2026-07-14"])
        self.assertEqual({code for code, _ in result["new_dishes"]}, {"08.03.01.110", "03.01.02.190", "09.03.01.050"})

        grouped = bot.day_menu_by_category("2026-07-14")
        self.assertEqual(set(grouped.keys()), {"LANCHE", "ACOMPANHAMENTO"})
        self.assertEqual(len(grouped["ACOMPANHAMENTO"]), 2)

    def test_import_menu_workbook_keeps_existing_dish_metadata(self):
        self.seed_dish("08.03.01.110", "X-FRANGO", "LANCHE", ingredients="pao, frango, queijo", allergens="gluten, leite")
        import openpyxl

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(["Dia", "LANCHE"])
        sheet.append(["14", "100% - 08.03.01.110 - X-FRANGO - 2.28"])
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp_file:
            workbook.save(tmp_file.name)
            tmp_path = tmp_file.name
        try:
            result = bot.import_menu_workbook(tmp_path, 2026, 7)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        self.assertEqual(result["new_dishes"], [])
        dish = bot.load_dish("08.03.01.110")
        self.assertEqual(dish["allergens"], "gluten, leite")

    def test_save_employee_stores_consent(self):
        user_id = 987
        consented_at = "2026-07-13T10:00:00+00:00"

        bot.save_employee(user_id, 999, "Colaborador LGPD", "Manter equilibrio", "Sem restricoes", True, consented_at)

        employee = bot.load_employee(user_id)
        self.assertEqual(employee["consent_accepted"], 1)
        self.assertEqual(employee["consented_at"], consented_at)

    def test_delete_employee_data_removes_profile_and_selections(self):
        user_id = 654
        bot.save_employee(user_id, 999, "Colaborador Delete", "Perder peso", "Sem restricoes", True, bot.now_iso())
        self.seed_dish("soup", "Sopa de Lentilha", "LANCHE")
        menu_date = monday_of_this_week().isoformat()
        bot.save_selection(user_id, menu_date, "LANCHE", "soup", "Sopa de Lentilha")

        bot.delete_employee_data(user_id)

        self.assertIsNone(bot.load_employee(user_id))
        self.assertEqual(bot.week_selections(user_id, bot.current_week_dates()), [])

    def test_admin_report_counts_employees_selections_and_pending_dishes(self):
        user_id = 789
        bot.save_employee(user_id, 999, "Colaborador Teste", "Perder peso", "Sem restricoes", True, bot.now_iso())
        self.seed_dish("soup", "Sopa de Lentilha", "LANCHE", ingredients="lentilha")
        self.seed_dish("pendente", "Prato Novo", "LANCHE")
        menu_date = monday_of_this_week().isoformat()
        bot.save_selection(user_id, menu_date, "LANCHE", "soup", "Sopa de Lentilha")

        report = bot.admin_report_data()

        self.assertEqual(report["totals"]["employees"], 1)
        self.assertEqual(report["totals"]["selections"], 1)
        self.assertEqual(report["totals"]["dishes"], 2)
        self.assertEqual(report["totals"]["pending"], 1)
        self.assertEqual(report["goals"][0]["goal"], "Perder peso")
        self.assertEqual(report["top"][0]["dish_name"], "Sopa de Lentilha")


if __name__ == "__main__":
    unittest.main()
