import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

import bot
from apetit.catalog import import_menu_csv, init_schema, set_item_allergens
from apetit.profile import load_employee
from apetit.tracking import consumption_history, favorites, total_points

FIXTURES = Path(__file__).parent / "fixtures"


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text=None, parse_mode=None, reply_markup=None):
        self.replies.append(text or "")


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self._message = message
        self.answered = False

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text=None, parse_mode=None, reply_markup=None):
        self._message.replies.append(text or "")


class FakeUpdate:
    def __init__(self, text="", user_id=777, callback=None):
        self.message = FakeMessage(text)
        self.effective_message = self.message
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=user_id)
        self.callback_query = FakeQuery(callback, self.message) if callback else None

    @property
    def last(self):
        return self.message.replies[-1] if self.message.replies else ""


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.args = []
        self.bot = self


class BotBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        bot.DB_PATH = Path(self.tmp.name)
        conn = bot.db()
        init_schema(conn)
        import_menu_csv(conn, (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"), unit="SM")
        conn.close()
        self.user = 777
        self.admins = bot.ADMIN_IDS
        bot.ADMIN_IDS = set()
        # bot.today e substituido em varios testes; guardar o original evita
        # que a data fixada de um teste vaze para os seguintes.
        self.today_original = bot.today

    def tearDown(self):
        bot.ADMIN_IDS = self.admins
        bot.today = self.today_original
        Path(self.tmp.name).unlink(missing_ok=True)

    async def registrar(self, context, restricoes=("leite",)):
        """Percorre o cadastro inteiro como o funcionario faria."""
        await bot.start(FakeUpdate(), context)
        for texto in ("Mariana", "SM", "Industria Exemplo", "Producao"):
            await bot.handle_message(FakeUpdate(text=texto), context)
        await bot.handle_callback(FakeUpdate(callback="goal_manter"), context)
        for code in restricoes:
            await bot.handle_callback(FakeUpdate(callback=f"restr:{code}"), context)
        await bot.handle_callback(FakeUpdate(callback="restr_ok"), context)
        update = FakeUpdate(callback="consent_sim")
        await bot.handle_callback(update, context)
        return update


class CadastroTest(BotBase):
    async def test_full_registration_saves_unit_company_and_sector(self):
        context = FakeContext()

        await self.registrar(context)

        conn = bot.db()
        try:
            pessoa = load_employee(conn, self.user)
        finally:
            conn.close()
        self.assertEqual(pessoa.name, "Mariana")
        self.assertEqual(pessoa.apetit_unit, "SM")
        self.assertEqual(pessoa.client_company, "Industria Exemplo")
        self.assertEqual(pessoa.sector, "Producao")
        self.assertEqual([r.allergen for r in pessoa.restrictions], ["leite"])
        self.assertTrue(pessoa.registered)

    async def test_refusing_consent_saves_nothing(self):
        context = FakeContext()
        await bot.start(FakeUpdate(), context)
        for texto in ("Mariana", "SM", "Industria Exemplo", "Producao"):
            await bot.handle_message(FakeUpdate(text=texto), context)
        await bot.handle_callback(FakeUpdate(callback="goal_manter"), context)
        await bot.handle_callback(FakeUpdate(callback="restr_ok"), context)

        update = FakeUpdate(callback="consent_nao")
        await bot.handle_callback(update, context)

        conn = bot.db()
        try:
            self.assertIsNone(load_employee(conn, self.user))
        finally:
            conn.close()
        self.assertIn("Sem o aceite", update.last)

    async def test_restriction_toggles_off_when_tapped_twice(self):
        context = FakeContext()
        await bot.handle_callback(FakeUpdate(callback="restr:leite"), context)
        await bot.handle_callback(FakeUpdate(callback="restr:leite"), context)

        self.assertEqual(context.user_data["cadastro"]["restricoes"], [])

    async def test_unregistered_user_is_sent_to_registration(self):
        context = FakeContext()
        update = FakeUpdate(callback="cardapio")

        await bot.handle_callback(update, context)

        self.assertIn("Como voce quer ser chamado", update.last)


class CardapioTest(BotBase):
    async def test_menu_marks_dish_with_the_declared_allergen(self):
        context = FakeContext()
        await self.registrar(context)
        conn = bot.db()
        try:
            set_item_allergens(conn, "carne_assada_ao_molho", {"leite": "contem"})
        finally:
            conn.close()
        bot.today = lambda: "2025-09-01"

        update = FakeUpdate(callback="cardapio")
        await bot.handle_callback(update, context)

        self.assertIn("⛔", update.last)
        self.assertIn("CARNE ASSADA AO MOLHO", update.last)

    async def test_menu_warns_when_nothing_is_declared(self):
        context = FakeContext()
        await self.registrar(context)
        bot.today = lambda: "2025-09-01"

        update = FakeUpdate(callback="cardapio")
        await bot.handle_callback(update, context)

        self.assertIn("sem informacao de alergenico", update.last)
        self.assertIn("pergunte no balcao", update.last.lower())

    async def test_person_without_restrictions_sees_no_warning(self):
        context = FakeContext()
        await self.registrar(context, restricoes=())
        bot.today = lambda: "2025-09-01"

        update = FakeUpdate(callback="cardapio")
        await bot.handle_callback(update, context)

        self.assertNotIn("⚠️", update.last)


class PratoTest(BotBase):
    async def asyncSetUp(self):
        self.context = FakeContext()
        await self.registrar(self.context, restricoes=())
        bot.today = lambda: "2025-09-01"
        await bot.handle_callback(FakeUpdate(callback="montar"), self.context)

    async def test_guided_flow_walks_the_serving_line_in_order(self):
        ordem = self.context.user_data["monta_ordem"]

        # Prato principal antes de guarnicao, salada antes de sobremesa:
        # a ordem da bandeja, nao a ordem alfabetica.
        self.assertEqual(ordem[0], "PRATO PRINCIPAL")
        self.assertLess(ordem.index("GUARNICAO"), ordem.index("SALADA"))

    async def test_step_shows_progress_and_advances(self):
        update = FakeUpdate(callback="flow_next")
        await bot.handle_callback(update, self.context)

        self.assertIn("Passo 2 de", update.last)

    async def test_building_and_registering_the_plate_logs_consumption(self):
        await bot.handle_callback(FakeUpdate(callback="pick:carne_assada_ao_molho"), self.context)
        await bot.handle_callback(FakeUpdate(callback="pick:sal_mix_de_alface"), self.context)

        update = FakeUpdate(callback="registrar")
        await bot.handle_callback(update, self.context)

        conn = bot.db()
        try:
            historico = consumption_history(conn, self.user)
            pontos = total_points(conn, self.user)
        finally:
            conn.close()
        self.assertEqual({linha["name"] for linha in historico}, {"CARNE ASSADA AO MOLHO", "SAL. MIX DE ALFACE"})
        self.assertEqual(pontos, 15)  # registro + composicao
        self.assertIn("Registrado", update.last)
        self.assertEqual(self.context.user_data[bot.TRAY], [])

    async def test_tapping_the_same_item_twice_removes_it(self):
        await bot.handle_callback(FakeUpdate(callback="pick:carne_assada_ao_molho"), self.context)
        await bot.handle_callback(FakeUpdate(callback="pick:carne_assada_ao_molho"), self.context)

        self.assertEqual(self.context.user_data[bot.TRAY], [])

    async def test_summary_reads_the_plate_in_words(self):
        await bot.handle_callback(FakeUpdate(callback="pick:carne_assada_ao_molho"), self.context)

        update = FakeUpdate(callback="flow_fim")
        await bot.handle_callback(update, self.context)

        self.assertIn("Prato leve para o seu objetivo", update.last)
        self.assertIn("Sem salada nem fruta", update.last)

    async def test_favoriting_a_dish(self):
        await bot.handle_callback(FakeUpdate(callback="fav:arroz_parboilizado"), self.context)

        conn = bot.db()
        try:
            guardados = favorites(conn, self.user)
        finally:
            conn.close()
        self.assertEqual([linha["item_code"] for linha in guardados], ["arroz_parboilizado"])

    async def test_empty_plate_does_not_register(self):
        update = FakeUpdate(callback="registrar")
        await bot.handle_callback(update, self.context)

        conn = bot.db()
        try:
            self.assertEqual(consumption_history(conn, self.user), [])
        finally:
            conn.close()
        self.assertIn("prato esta vazio", update.last)


class LgpdTest(BotBase):
    async def test_my_data_shows_what_is_stored_and_who_cannot_see_it(self):
        context = FakeContext()
        await self.registrar(context)

        update = FakeUpdate()
        await bot.show_my_data(update, context)

        self.assertIn("Mariana", update.last)
        self.assertIn("Industria Exemplo", update.last)
        self.assertIn("Sua empresa nao ve nada disso sobre voce", update.last)

    async def test_delete_wipes_everything(self):
        context = FakeContext()
        await self.registrar(context, restricoes=())
        bot.today = lambda: "2025-09-01"
        await bot.handle_callback(FakeUpdate(callback="montar"), context)
        await bot.handle_callback(FakeUpdate(callback="pick:carne_assada_ao_molho"), context)
        await bot.handle_callback(FakeUpdate(callback="registrar"), context)

        await bot.handle_callback(FakeUpdate(callback="del_sim"), context)

        conn = bot.db()
        try:
            self.assertIsNone(load_employee(conn, self.user))
            self.assertEqual(consumption_history(conn, self.user), [])
            self.assertEqual(total_points(conn, self.user), 0)
        finally:
            conn.close()


class AdminTest(BotBase):
    async def test_admin_commands_are_closed_when_nobody_is_configured(self):
        bot.ADMIN_IDS = set()
        for handler in (bot.show_pending, bot.show_report, bot.declare_allergen, bot.notify_favorites):
            update = FakeUpdate()
            await handler(update, FakeContext())
            self.assertIn("Nenhum administrador configurado", update.last)

    async def test_non_admin_is_refused(self):
        bot.ADMIN_IDS = {999}
        update = FakeUpdate(user_id=self.user)

        await bot.show_report(update, FakeContext())

        self.assertIn("Apenas administradores", update.last)

    async def test_report_is_aggregate_and_suppresses_small_sectors(self):
        bot.ADMIN_IDS = {self.user}
        context = FakeContext()
        await self.registrar(context)

        update = FakeUpdate(user_id=self.user)
        await bot.show_report(update, context)

        self.assertIn("suprimido", update.last)
        self.assertNotIn("Mariana", update.last)

    async def test_admin_declares_allergen(self):
        bot.ADMIN_IDS = {self.user}
        context = FakeContext()
        context.args = ["carne_assada_ao_molho", "leite", "contem"]
        update = FakeUpdate(user_id=self.user)

        await bot.declare_allergen(update, context)

        self.assertIn("leite = contem", update.last)


if __name__ == "__main__":
    unittest.main()
