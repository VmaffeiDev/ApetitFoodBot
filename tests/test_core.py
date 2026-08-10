import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("APETIT_DB_PATH", "test-apetit.db")

import bot


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text=None, parse_mode=None, reply_markup=None):
        self.replies.append(text or "")


class FakeUpdate:
    def __init__(self, text, user_id, chat_id=999):
        self.message = FakeMessage(text)
        self.effective_message = self.message
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.callback_query = None


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.args = []


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

    def test_recommendation_uses_customer_goal(self):
        recommended = bot.recommend_item(None, "Sem restricoes", "Ganhar massa")

        self.assertGreater(bot.goal_score("Ganhar massa", recommended), 0)
        self.assertIn("proteico", bot.item_search_text(recommended))

    def test_recent_orders_include_keys_for_recommendation_flow(self):
        user_id = 456
        bot.record_order(user_id, "soup")

        rows = bot.recent_orders(user_id)

        self.assertEqual(rows[0]["dish_key"], "soup")

    def test_admin_report_counts_clients_orders_and_favorites(self):
        user_id = 789
        bot.save_client(user_id, 999, "Cliente Teste", "11999999999", "Centro", "Sem restricoes", "Perder peso")
        bot.record_order(user_id, "soup")
        bot.add_favorite_waitlist(user_id, "soup")

        report = bot.admin_report_data()

        self.assertEqual(report["totals"]["clients"], 1)
        self.assertEqual(report["totals"]["orders"], 1)
        self.assertEqual(report["totals"]["favorites"], 1)
        self.assertEqual(report["goals"][0]["goal"], "Perder peso")
        self.assertEqual(report["top"][0]["dish_name"], "Sopa de Lentilha")

    def test_client_consent_is_stored_with_timestamp(self):
        user_id = 987
        consented_at = "2026-05-27T10:00:00+00:00"

        bot.save_client(
            user_id,
            999,
            "Cliente LGPD",
            "11988887777",
            "Centro",
            "Sem restricoes",
            "Manter equilibrio",
            True,
            consented_at,
        )

        client = bot.load_client(user_id)

        self.assertEqual(client["consent_accepted"], 1)
        self.assertEqual(client["consented_at"], consented_at)

    def test_delete_client_data_removes_profile_history_and_favorites(self):
        user_id = 654
        bot.save_client(user_id, 999, "Cliente Delete", "11977776666", "Bairro", "Sem restricoes", "Perder peso")
        bot.record_order(user_id, "soup")
        bot.add_favorite_waitlist(user_id, "soup")

        bot.delete_client_data(user_id)

        self.assertIsNone(bot.load_client(user_id))
        self.assertEqual(bot.recent_orders(user_id), [])
        self.assertEqual(bot.favorite_items(user_id), [])


class AdminAccessTest(unittest.TestCase):
    def setUp(self):
        self.original_admins = bot.ADMIN_TELEGRAM_IDS

    def tearDown(self):
        bot.ADMIN_TELEGRAM_IDS = self.original_admins

    def test_no_admin_configured_blocks_everyone(self):
        bot.ADMIN_TELEGRAM_IDS = set()

        self.assertFalse(bot.is_admin(123))
        self.assertFalse(bot.is_admin(None))

    def test_only_configured_ids_are_admin(self):
        bot.ADMIN_TELEGRAM_IDS = {123}

        self.assertTrue(bot.is_admin(123))
        self.assertFalse(bot.is_admin(456))
        self.assertFalse(bot.is_admin(None))


class FreeTextOrderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        bot.DB_PATH = Path(self.tmp.name)
        bot.init_db()
        self.user_id = 321
        bot.save_client(self.user_id, 999, "Cliente Veg", "11999998888", "Centro", "Vegetariana", "Manter equilibrio")

    def tearDown(self):
        try:
            Path(self.tmp.name).unlink(missing_ok=True)
        except PermissionError:
            pass

    async def test_incompatible_dish_typed_as_text_is_not_recorded(self):
        update = FakeUpdate("Quero pedir o Peixe Assado com Legumes", self.user_id)

        await bot.handle_message(update, FakeContext())

        self.assertEqual(bot.recent_orders(self.user_id), [])
        self.assertIn("nao registrei esse pedido", update.message.replies[-1])

    async def test_compatible_dish_typed_as_text_is_recorded(self):
        update = FakeUpdate("Quero pedir a Sopa de Lentilha", self.user_id)

        await bot.handle_message(update, FakeContext())

        orders = bot.recent_orders(self.user_id)
        self.assertEqual([row["dish_key"] for row in orders], ["soup"])


if __name__ == "__main__":
    unittest.main()
