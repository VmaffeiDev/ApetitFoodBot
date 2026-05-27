import os
import tempfile
import unittest
from pathlib import Path

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

        recommended = bot.recommend_item(user_id, "Vegetariana")

        self.assertEqual(recommended["dish_key"], "lasagna")

    def test_recent_orders_include_keys_for_recommendation_flow(self):
        user_id = 456
        bot.record_order(user_id, "soup")

        rows = bot.recent_orders(user_id)

        self.assertEqual(rows[0]["dish_key"], "soup")


if __name__ == "__main__":
    unittest.main()
