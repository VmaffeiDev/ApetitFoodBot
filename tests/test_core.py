import os
import logging
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("APETIT_DB_PATH", "test-apetit.db")

import bot


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

    def test_price_to_cents_accepts_brazilian_format(self):
        self.assertEqual(bot.price_to_cents("R$ 29,90"), 2990)
        self.assertEqual(bot.format_price(2990), "R$ 29,90")

    def test_http_client_logs_do_not_expose_bot_token_at_info_level(self):
        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertGreaterEqual(logging.getLogger("httpcore").level, logging.WARNING)

    def test_restriction_conflict_blocks_unsafe_items(self):
        fish = bot.load_menu_item("fish")
        lasagna = bot.load_menu_item("lasagna")

        self.assertEqual(bot.restriction_conflict("Vegetariana", fish), "nao esta marcado como vegetariano")
        self.assertEqual(bot.restriction_conflict("Sem lactose", lasagna), "contem lactose ou derivados de leite")
        self.assertIsNone(bot.restriction_conflict("Sem gluten", lasagna))

    def test_recommendation_respects_restriction_and_history(self):
        user_id = 123
        bot.record_order(user_id, "fish")
        bot.record_order(user_id, "fish")
        bot.record_order(user_id, "lasagna")

        recommended = bot.recommend_item(user_id, "Vegetariana", "Manter equilibrio")

        self.assertEqual(recommended["dish_key"], "lasagna")

    def test_recommendation_uses_employee_goal(self):
        recommended = bot.recommend_item(None, "Sem restricoes", "Ganhar massa")

        self.assertGreater(bot.goal_score("Ganhar massa", recommended), 0)
        self.assertIn("proteico", bot.item_search_text(recommended))

    def test_recent_orders_include_keys_for_recommendation_flow(self):
        user_id = 456
        bot.record_order(user_id, "soup")

        rows = bot.recent_orders(user_id)

        self.assertEqual(rows[0]["dish_key"], "soup")

    def test_admin_report_counts_employees_orders_and_favorites(self):
        user_id = 789
        bot.save_employee(user_id, 999, "Colaborador Teste", "", "", "Sem restricoes", "Perder peso", company="Empresa A", department="Operacoes")
        bot.record_order(user_id, "soup")
        bot.add_favorite_waitlist(user_id, "soup")

        report = bot.admin_report_data()

        self.assertEqual(report["totals"]["employees"], 1)
        self.assertEqual(report["totals"]["orders"], 1)
        self.assertEqual(report["totals"]["favorites"], 1)
        self.assertEqual(report["goals"][0]["goal"], "Perder peso")
        self.assertEqual(report["top"][0]["dish_name"], "Sopa de Lentilha")

    def test_employee_consent_is_stored_with_timestamp(self):
        user_id = 987
        consented_at = "2026-05-27T10:00:00+00:00"

        bot.save_employee(
            user_id,
            999,
            "Colaborador LGPD",
            "",
            "",
            "Sem restricoes",
            "Manter equilibrio",
            True,
            consented_at,
            company="Empresa A",
            department="Financeiro",
        )

        employee = bot.load_employee(user_id)

        self.assertEqual(employee["consent_accepted"], 1)
        self.assertEqual(employee["consented_at"], consented_at)
        self.assertEqual(employee["company"], "Empresa A")
        self.assertEqual(employee["department"], "Financeiro")

    def test_delete_employee_data_removes_profile_history_and_favorites(self):
        user_id = 654
        bot.save_employee(user_id, 999, "Colaborador Delete", "", "", "Sem restricoes", "Perder peso", company="Empresa A", department="Operacoes")
        bot.record_order(user_id, "soup")
        bot.add_favorite_waitlist(user_id, "soup")

        bot.delete_employee_data(user_id)

        self.assertIsNone(bot.load_employee(user_id))
        self.assertEqual(bot.recent_orders(user_id), [])
        self.assertEqual(bot.favorite_items(user_id), [])

    def test_init_db_migrates_legacy_client_schema(self):
        with closing(sqlite3.connect(bot.DB_PATH)) as conn:
            conn.execute("DROP TABLE clients")
            conn.execute(
                """
                CREATE TABLE clients (
                    telegram_id INTEGER PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    restriction TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO clients (
                    telegram_id, chat_id, name, phone, address, restriction, created_at, updated_at
                ) VALUES (1, 1, 'Cadastro legado', '11999999999', 'Rua antiga', 'Sem restricoes', 'agora', 'agora')
                """
            )
            conn.commit()

        bot.init_db()

        with closing(sqlite3.connect(bot.DB_PATH)) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(clients)")}
            legacy = conn.execute("SELECT phone, address FROM clients WHERE telegram_id = 1").fetchone()

        self.assertTrue({"company", "department", "goal", "consent_accepted", "consented_at"}.issubset(columns))
        self.assertEqual(legacy, ("", ""))

    def test_admin_commands_are_denied_without_configured_ids(self):
        configured_ids = bot.ADMIN_TELEGRAM_IDS
        try:
            bot.ADMIN_TELEGRAM_IDS = set()
            self.assertFalse(bot.is_admin(123))

            bot.ADMIN_TELEGRAM_IDS = {123}
            self.assertTrue(bot.is_admin(123))
            self.assertFalse(bot.is_admin(456))
        finally:
            bot.ADMIN_TELEGRAM_IDS = configured_ids

    def test_nutrition_profile_is_saved_and_synchronizes_goal(self):
        user_id = 321
        bot.save_employee(user_id, 999, "Colaborador Nutri", "", "", "Sem restricoes", company="Empresa A", department="Operacoes")

        targets = bot.save_nutrition_profile(
            user_id,
            weight_kg=70,
            height_cm=175,
            age=30,
            sex="male",
            activity="moderate",
            goal="Ganhar massa",
        )

        saved = bot.load_nutrition_profile(user_id)
        employee = bot.load_employee(user_id)
        self.assertEqual(saved["target_calories"], targets.target_calories)
        self.assertEqual(saved["protein_grams"], 126)
        self.assertTrue(saved["consented_at"])
        self.assertEqual(employee["goal"], "Ganhar massa")

    def test_gamification_prevents_duplicate_and_conflicting_daily_scores(self):
        user_id = 741

        first = bot.award_points(user_id, "partial_recommendation", 10, "soup")
        duplicate = bot.award_points(user_id, "partial_recommendation", 10, "soup")
        full_after_partial = bot.award_points(user_id, "follow_recommendation", 20, "soup")
        tip = bot.award_points(user_id, "tip", 5, "nutrition_profile")
        summary = bot.gamification_summary(user_id)

        self.assertTrue(first["awarded"])
        self.assertFalse(duplicate["awarded"])
        self.assertFalse(full_after_partial["awarded"])
        self.assertTrue(tip["awarded"])
        self.assertEqual(summary["points"], 15)
        self.assertEqual(summary["streak"], 1)
        self.assertEqual(summary["badges"][0]["badge_key"], "first_step")

    def test_delete_employee_data_includes_nutrition_and_gamification(self):
        user_id = 852
        bot.save_employee(user_id, 999, "Colaborador Delete Nutri", "", "", "Sem restricoes", company="Empresa A", department="Operacoes")
        bot.save_nutrition_profile(user_id, 65, 165, 35, "female", "light", "Perder peso")
        bot.award_points(user_id, "tip", 5, "nutrition_profile")

        bot.delete_employee_data(user_id)

        self.assertIsNone(bot.load_nutrition_profile(user_id))
        self.assertEqual(bot.gamification_summary(user_id)["points"], 0)

    def test_menu_exposes_nutrition_traffic_light(self):
        payload = bot.menu_payload(restriction="Sem restricoes", goal="Ganhar massa")

        self.assertIn("Semaforo:", payload["text"])
        self.assertIn("Boa aderencia", payload["text"])

    def test_order_gamification_requires_nutrition_consent(self):
        user_id = 963
        context = SimpleNamespace(user_data={"nutrition_recommendation": "soup"})
        item = bot.load_menu_item("soup")

        awards = bot.award_order_gamification(user_id, item, context)

        self.assertEqual(awards, [])
        self.assertEqual(bot.gamification_summary(user_id)["points"], 0)


if __name__ == "__main__":
    unittest.main()
