import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

import bot
from apetit.allergens import ALLERGENS
from apetit.catalog import import_menu_csv, init_schema, menu_for_date, set_item_allergens
from apetit.profile import load_employee
from apetit.feedback import MIN_RATINGS, Rating, rating_for, save_rating
from apetit.tracking import consumption_history, favorites, history_by_day, total_points

FIXTURES = Path(__file__).parent / "fixtures"


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []
        self.markups = []

    async def reply_text(self, text=None, parse_mode=None, reply_markup=None):
        self.replies.append(text or "")
        self.markups.append(reply_markup)


class FakeQuery:
    def __init__(self, data, message):
        self.data = data
        self._message = message
        self.answered = False

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text=None, parse_mode=None, reply_markup=None):
        self._message.replies.append(text or "")
        self._message.markups.append(reply_markup)


class FakeUpdate:
    def __init__(self, text="", user_id=777, callback=None, document=None):
        self.message = FakeMessage(text)
        self.message.document = document
        self.effective_message = self.message
        self.effective_user = SimpleNamespace(id=user_id)
        self.effective_chat = SimpleNamespace(id=user_id)
        self.callback_query = FakeQuery(callback, self.message) if callback else None

    @property
    def last(self):
        return self.message.replies[-1] if self.message.replies else ""

    @property
    def buttons(self) -> list[tuple[str, str]]:
        """Os botoes da ultima tela, como (rotulo, callback_data)."""
        markup = self.message.markups[-1] if self.message.markups else None
        if not markup:
            return []
        return [(botao.text, botao.callback_data) for linha in markup.inline_keyboard for botao in linha]


class FakeDocument:
    def __init__(self, file_name, content: bytes):
        self.file_name = file_name
        self.file_id = "fid"
        self.file_size = len(content)


class FakeFile:
    def __init__(self, content: bytes):
        self._content = content

    async def download_as_bytearray(self):
        return bytearray(self._content)


class FakeContext:
    def __init__(self, arquivo: bytes = b""):
        self.user_data = {}
        self.args = []
        self.bot = self
        self._arquivo = arquivo

    async def get_file(self, file_id):
        return FakeFile(self._arquivo)


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

    async def test_written_allergy_is_recognized_and_saved(self):
        context = FakeContext()
        await bot.start(FakeUpdate(), context)
        for texto in ("Mariana", "SM", "Industria Exemplo", "Producao"):
            await bot.handle_message(FakeUpdate(text=texto), context)
        await bot.handle_callback(FakeUpdate(callback="goal_manter"), context)

        escrita = FakeUpdate(text="tenho alergia a frutos do mar e a legumes")
        await bot.handle_message(escrita, context)

        # O app devolve o que entendeu antes de salvar.
        self.assertIn("Crustaceos", escrita.last)
        self.assertIn("legumes", escrita.last)
        self.assertIn("nao consigo conferir sozinho", escrita.last)

        await bot.handle_callback(FakeUpdate(callback="restr_ok"), context)
        await bot.handle_callback(FakeUpdate(callback="consent_sim"), context)

        conn = bot.db()
        try:
            pessoa = load_employee(conn, self.user)
        finally:
            conn.close()
        self.assertEqual(set(r.allergen for r in pessoa.restrictions), {"crustaceos", "peixes"})
        self.assertEqual(pessoa.free_restrictions, ["legumes"])

    async def test_unverifiable_term_keeps_the_menu_out_of_green(self):
        context = FakeContext()
        await bot.start(FakeUpdate(), context)
        for texto in ("Mariana", "SM", "Industria Exemplo", "Producao"):
            await bot.handle_message(FakeUpdate(text=texto), context)
        await bot.handle_callback(FakeUpdate(callback="goal_manter"), context)
        await bot.handle_message(FakeUpdate(text="alergia a legumes"), context)
        await bot.handle_callback(FakeUpdate(callback="restr_ok"), context)
        await bot.handle_callback(FakeUpdate(callback="consent_sim"), context)

        conn = bot.db()
        try:
            for code in ("carne_assada_ao_molho", "sal_mix_de_alface"):
                set_item_allergens(conn, code, {c: "nao_contem" for c in ALLERGENS})
        finally:
            conn.close()
        bot.today = lambda: "2025-09-01"

        update = FakeUpdate(callback="cardapio")
        await bot.handle_callback(update, context)

        # Mesmo com toda a ficha declarada, nao pode aparecer visto verde:
        # o app nao sabe checar "legumes".
        self.assertNotIn("✅", update.last)
        self.assertIn("⚠️", update.last)

    async def test_stale_consent_button_never_wipes_an_existing_profile(self):
        # Botao de mensagem antiga continua clicavel. Tocar nele depois nao
        # pode zerar o cadastro de quem ja se cadastrou.
        context = FakeContext()
        await self.registrar(context)

        outro = FakeContext()  # sessao nova, rascunho vazio
        await bot.handle_callback(FakeUpdate(callback="consent_sim"), outro)

        conn = bot.db()
        try:
            pessoa = load_employee(conn, self.user)
        finally:
            conn.close()
        self.assertEqual(pessoa.name, "Mariana")
        self.assertTrue(pessoa.registered)

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
        self.assertIn("registrada", update.last)
        # A confirmacao repete o prato guardado: a pessoa confere na hora.
        self.assertIn("CARNE ASSADA AO MOLHO", update.last)
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


class SalvarSugestaoTest(BotBase):
    """Um toque em "Vou pegar isso" tem que virar historico."""

    async def asyncSetUp(self):
        self.context = FakeContext()
        await self.registrar(self.context, restricoes=())
        bot.today = lambda: "2025-09-01"

    async def test_the_portion_screen_offers_to_save_what_it_suggested(self):
        update = FakeUpdate(callback="quanto")
        await bot.handle_callback(update, self.context)

        self.assertIn("registrar_sugestao", [data for _, data in update.buttons])

    async def test_saving_the_suggestion_records_the_quantities(self):
        update = FakeUpdate(callback="registrar_sugestao")
        await bot.handle_callback(update, self.context)

        conn = bot.db()
        try:
            dias = history_by_day(conn, self.user)
        finally:
            conn.close()
        self.assertEqual(len(dias), 1)
        self.assertEqual(dias[0].service_date, "2025-09-01")
        self.assertTrue(dias[0].items)
        # A sugestao pede mais de uma colher de alguma coisa; isso tem que
        # chegar no historico como quantidade, nao como item repetido.
        self.assertTrue(any(linha["quantity"] > 1 for linha in dias[0].items))
        self.assertIn("registrada", update.last)

    async def test_the_confirmation_repeats_what_was_saved(self):
        update = FakeUpdate(callback="registrar_sugestao")
        await bot.handle_callback(update, self.context)

        self.assertIn("kcal", update.last)
        self.assertIn("proteina", update.last)

    async def test_saving_the_suggestion_scores_the_day(self):
        await bot.handle_callback(FakeUpdate(callback="registrar_sugestao"), self.context)

        conn = bot.db()
        try:
            self.assertGreater(total_points(conn, self.user), 0)
        finally:
            conn.close()

    async def test_registering_again_replaces_instead_of_stacking(self):
        await bot.handle_callback(FakeUpdate(callback="registrar_sugestao"), self.context)
        await bot.handle_callback(FakeUpdate(callback="registrar_sugestao"), self.context)

        conn = bot.db()
        try:
            dias = history_by_day(conn, self.user)
        finally:
            conn.close()
        self.assertEqual(len(dias), 1)


class MeuDiaTest(BotBase):
    async def asyncSetUp(self):
        self.context = FakeContext()
        await self.registrar(self.context, restricoes=())
        bot.today = lambda: "2025-09-01"

    async def test_without_a_record_it_says_how_to_start(self):
        update = FakeUpdate(callback="meu_dia")
        await bot.handle_callback(update, self.context)

        self.assertIn("ainda nao registrou", update.last)
        self.assertIn("Vou pegar isso", update.last)

    async def test_shows_the_plate_with_household_measures_and_the_day_total(self):
        await bot.handle_callback(FakeUpdate(callback="registrar_sugestao"), self.context)

        update = FakeUpdate(callback="meu_dia")
        await bot.handle_callback(update, self.context)

        self.assertIn("kcal", update.last)
        # Quantidade em medida de fila, a mesma linguagem da sugestao.
        self.assertTrue(
            any(medida in update.last for medida in ("colher", "concha", "porcao", "pegador")),
            update.last,
        )

    async def test_previous_days_appear_with_their_own_totals(self):
        bot.today = lambda: "2025-09-01"
        await bot.handle_callback(FakeUpdate(callback="registrar_sugestao"), self.context)
        bot.today = lambda: "2025-09-02"
        await bot.handle_callback(FakeUpdate(callback="registrar_sugestao"), self.context)

        update = FakeUpdate(callback="meu_dia")
        await bot.handle_callback(update, self.context)

        self.assertIn("Seu historico", update.last)
        self.assertIn("g ptn", update.last)


class AvaliacaoTest(BotBase):
    async def asyncSetUp(self):
        self.context = FakeContext()
        await self.registrar(self.context, restricoes=())
        bot.today = lambda: "2025-09-01"

    async def avaliar(self, comida="aval_comida:3", atendimento="aval_atend:2", faltou="aval_faltou:nao"):
        await bot.handle_callback(FakeUpdate(callback="avaliar"), self.context)
        await bot.handle_callback(FakeUpdate(callback=comida), self.context)
        await bot.handle_callback(FakeUpdate(callback=atendimento), self.context)
        update = FakeUpdate(callback=faltou)
        await bot.handle_callback(update, self.context)
        return update

    async def test_the_first_screen_says_the_answer_is_not_identified(self):
        # Quem nao sabe que esta protegido responde como se nao estivesse.
        update = FakeUpdate(callback="avaliar")
        await bot.handle_callback(update, self.context)

        self.assertIn("sem o seu nome", update.last)
        self.assertIn("nunca quem disse o que", update.last)

    async def test_three_taps_record_the_rating(self):
        await self.avaliar()
        update = FakeUpdate(callback="aval_enviar")
        await bot.handle_callback(update, self.context)

        conn = bot.db()
        try:
            guardada = rating_for(conn, self.user, "2025-09-01")
        finally:
            conn.close()
        self.assertEqual(guardada.food, 3)
        self.assertEqual(guardada.service, 2)
        self.assertFalse(guardada.missing)
        self.assertIn("Obrigado", update.last)

    async def test_saying_something_was_missing_asks_what(self):
        update = await self.avaliar(faltou="aval_faltou:sim")

        self.assertIn("O que faltou", update.last)

    async def test_missing_reasons_are_recorded(self):
        await self.avaliar(faltou="aval_faltou:sim")
        await bot.handle_callback(FakeUpdate(callback="aval_tag:acabou"), self.context)
        await bot.handle_callback(FakeUpdate(callback="aval_tag:comida_fria"), self.context)
        await bot.handle_callback(FakeUpdate(callback="aval_enviar"), self.context)

        conn = bot.db()
        try:
            guardada = rating_for(conn, self.user, "2025-09-01")
        finally:
            conn.close()
        self.assertEqual(sorted(guardada.tags), ["acabou", "comida_fria"])
        self.assertTrue(guardada.missing)

    async def test_tapping_a_reason_twice_unmarks_it(self):
        await self.avaliar(faltou="aval_faltou:sim")
        await bot.handle_callback(FakeUpdate(callback="aval_tag:acabou"), self.context)
        await bot.handle_callback(FakeUpdate(callback="aval_tag:acabou"), self.context)
        await bot.handle_callback(FakeUpdate(callback="aval_enviar"), self.context)

        conn = bot.db()
        try:
            self.assertEqual(rating_for(conn, self.user, "2025-09-01").tags, [])
        finally:
            conn.close()

    async def test_a_written_comment_is_saved(self):
        await self.avaliar()
        await bot.handle_callback(FakeUpdate(callback="aval_comentario"), self.context)
        await bot.handle_message(FakeUpdate(text="O arroz estava salgado demais"), self.context)

        conn = bot.db()
        try:
            self.assertEqual(
                rating_for(conn, self.user, "2025-09-01").comment, "O arroz estava salgado demais"
            )
        finally:
            conn.close()

    async def test_after_registering_a_meal_it_offers_to_rate(self):
        update = FakeUpdate(callback="registrar_sugestao")
        await bot.handle_callback(update, self.context)

        self.assertIn("avaliar", [data for _, data in update.buttons])

    async def test_it_does_not_ask_again_once_already_rated(self):
        # Pedir de novo o que a pessoa ja respondeu e o caminho mais rapido
        # para ela parar de responder.
        await self.avaliar()
        await bot.handle_callback(FakeUpdate(callback="aval_enviar"), self.context)

        update = FakeUpdate(callback="registrar_sugestao")
        await bot.handle_callback(update, self.context)

        self.assertNotIn("avaliar", [data for _, data in update.buttons])

    async def test_my_data_shows_the_ratings_and_where_they_go(self):
        await self.avaliar()
        await bot.handle_callback(FakeUpdate(callback="aval_enviar"), self.context)

        update = FakeUpdate()
        await bot.show_my_data(update, self.context)

        self.assertIn("Avaliacoes do refeitorio", update.last)
        self.assertIn("sem o seu nome", update.last)

    async def test_deleting_my_data_removes_the_ratings(self):
        await self.avaliar()
        await bot.handle_callback(FakeUpdate(callback="aval_enviar"), self.context)

        await bot.handle_callback(FakeUpdate(callback="del_sim"), self.context)

        conn = bot.db()
        try:
            self.assertIsNone(rating_for(conn, self.user, "2025-09-01"))
        finally:
            conn.close()


class RelatorioAtendimentoTest(BotBase):
    async def test_non_admin_cannot_see_the_service_report(self):
        bot.ADMIN_IDS = {999}
        update = FakeUpdate(user_id=self.user)

        await bot.show_service_report(update, FakeContext())

        self.assertIn("Apenas administradores", update.last)

    async def test_report_suppresses_a_canteen_with_few_ratings(self):
        bot.ADMIN_IDS = {self.user}
        bot.today = lambda: "2025-09-10"
        conn = bot.db()
        try:
            for i in range(2):
                save_rating(conn, 500 + i, Rating(apetit_unit="SM", service_date="2025-09-01", food=1))
        finally:
            conn.close()

        update = FakeUpdate(user_id=self.user)
        await bot.show_service_report(update, FakeContext())

        self.assertIn("suprimido", update.last)

    async def test_report_shows_the_canteen_when_there_is_volume(self):
        bot.ADMIN_IDS = {self.user}
        bot.today = lambda: "2025-09-10"
        conn = bot.db()
        try:
            for i in range(MIN_RATINGS):
                save_rating(
                    conn, 500 + i,
                    Rating(apetit_unit="SM", service_date="2025-09-01", food=1, service=3, missing=True,
                           tags=["acabou"]),
                )
        finally:
            conn.close()

        update = FakeUpdate(user_id=self.user)
        await bot.show_service_report(update, FakeContext())

        self.assertIn("SM", update.last)
        self.assertIn("comida boa 0%", update.last)
        self.assertIn("atendimento bom 100%", update.last)


CARDAPIO_SEMANA = (
    "Dia;PRATO PRINCIPAL;ARROZ;FEIJAO\n"
    "17;BIFE ACEBOLADO (80g) - C51 - 3.11;ARROZ PARBOILIZADO - C51 - 0.24;FEIJAO CARIOCA - C51 - 0.29\n"
    "18;CARNE MOIDA A MEXICANA (80g) - C51 - 2.67;ARROZ PARBOILIZADO - C51 - 0.24;FEIJAO CARIOCA - C51 - 0.29\n"
).encode("utf-8")


class EnvioDoCardapioTest(BotBase):
    """Publicar a semana mandando o arquivo, sem terminal."""

    def setUp(self):
        super().setUp()
        bot.ADMIN_IDS = {self.user}
        bot.today = lambda: "2025-08-15"

    async def mandar(self, nome="Cardapio_17_a_2108.csv", conteudo=CARDAPIO_SEMANA, context=None):
        context = context or FakeContext(arquivo=conteudo)
        update = FakeUpdate(user_id=self.user, document=FakeDocument(nome, conteudo))
        await bot.receive_menu_file(update, context)
        return update, context

    async def test_non_admin_cannot_publish_a_menu(self):
        bot.ADMIN_IDS = {999}
        update, _ = await self.mandar()

        self.assertIn("Apenas administradores", update.last)

    async def test_the_preview_shows_the_period_before_publishing(self):
        update, _ = await self.mandar()

        # Data montada, nao "mes 8": e o que a pessoa consegue conferir.
        self.assertIn("17 de agosto", update.last)
        self.assertIn("18 de agosto", update.last)
        self.assertIn("6 itens", update.last)

    async def test_the_preview_says_where_the_month_came_from(self):
        update, _ = await self.mandar()

        self.assertIn("nome do arquivo", update.last)

    async def test_nothing_is_published_before_confirming(self):
        await self.mandar()

        conn = bot.db()
        try:
            self.assertEqual(menu_for_date(conn, "2025-08-17", unit="SM"), [])
        finally:
            conn.close()

    async def test_confirming_publishes_the_week(self):
        _, context = await self.mandar()

        update = FakeUpdate(user_id=self.user, callback="imp_publicar")
        await bot.handle_callback(update, context)

        conn = bot.db()
        try:
            cardapio = menu_for_date(conn, "2025-08-17", unit="SM")
        finally:
            conn.close()
        self.assertIn("BIFE ACEBOLADO", {l["name"] for l in cardapio})
        self.assertIn("publicado", update.last)

    async def test_the_month_can_be_corrected_before_publishing(self):
        _, context = await self.mandar()

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="imp_mes:2025-09"), context)
        update = FakeUpdate(user_id=self.user, callback="imp_publicar")
        await bot.handle_callback(update, context)

        conn = bot.db()
        try:
            self.assertTrue(menu_for_date(conn, "2025-09-17", unit="SM"))
            self.assertEqual(menu_for_date(conn, "2025-08-17", unit="SM"), [])
        finally:
            conn.close()

    async def test_cancelling_publishes_nothing(self):
        _, context = await self.mandar()

        update = FakeUpdate(user_id=self.user, callback="imp_cancelar")
        await bot.handle_callback(update, context)

        conn = bot.db()
        try:
            self.assertEqual(menu_for_date(conn, "2025-08-17", unit="SM"), [])
        finally:
            conn.close()
        self.assertIn("Nada foi publicado", update.last)

    async def test_sending_the_same_week_again_replaces_it(self):
        # E como a operacao corrige uma semana ja publicada.
        _, context = await self.mandar()
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="imp_publicar"), context)

        corrigido = CARDAPIO_SEMANA.replace(b"BIFE ACEBOLADO", b"BIFE GRELHADO")
        _, context2 = await self.mandar(conteudo=corrigido)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="imp_publicar"), context2)

        conn = bot.db()
        try:
            nomes = {l["name"] for l in menu_for_date(conn, "2025-08-17", unit="SM")}
        finally:
            conn.close()
        self.assertIn("BIFE GRELHADO", nomes)
        self.assertNotIn("BIFE ACEBOLADO", nomes)

    async def test_a_file_type_it_cannot_read_is_refused_clearly(self):
        update, _ = await self.mandar(nome="cardapio.pdf", conteudo=b"%PDF-1.4")

        self.assertIn("Nao sei ler", update.last)
        self.assertIn(".csv", update.last)

    async def test_a_corrupt_spreadsheet_does_not_crash(self):
        update, _ = await self.mandar(nome="cardapio.xlsx", conteudo=b"nao sou uma planilha")

        self.assertIn("Nao consegui abrir", update.last)

    async def test_a_file_with_no_menu_says_so(self):
        update, _ = await self.mandar(nome="lista.csv", conteudo=b"a;b;c\n1;2;3\n")

        self.assertIn("Nao encontrei nenhum item", update.last)

    async def test_the_help_tells_the_person_to_just_send_the_file(self):
        update = FakeUpdate(user_id=self.user)
        await bot.show_import_help(update, FakeContext())

        self.assertIn("me mandar o arquivo", update.last)


class ConfiguracaoTest(unittest.TestCase):
    """Erro de configuracao precisa dizer o que fazer, nao so que falhou."""

    def setUp(self):
        self.anterior = os.environ.get("TELEGRAM_BOT_TOKEN")

    def tearDown(self):
        if self.anterior is None:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        else:
            os.environ["TELEGRAM_BOT_TOKEN"] = self.anterior

    def test_missing_token_says_where_to_put_it(self):
        os.environ["TELEGRAM_BOT_TOKEN"] = ""

        with self.assertRaises(RuntimeError) as erro:
            bot.main()

        self.assertIn(".env", str(erro.exception))

    def test_the_example_placeholder_is_caught_before_telegram(self):
        # Quem copia o .env.example e esquece de trocar receberia "InvalidToken"
        # do Telegram — erro que nao diz qual e o problema.
        os.environ["TELEGRAM_BOT_TOKEN"] = bot.TOKEN_EXEMPLO

        with self.assertRaises(RuntimeError) as erro:
            bot.main()

        mensagem = str(erro.exception)
        self.assertIn("texto de exemplo", mensagem)
        self.assertIn("BotFather", mensagem)

    def test_the_leaked_token_refuses_to_start(self):
        # O token que ficou no historico publico deste repositorio. Subir com
        # ele e subir com uma credencial que qualquer pessoa consegue ler.
        os.environ["TELEGRAM_BOT_TOKEN"] = f"{bot.BOT_ID_COMPROMETIDO}:qualquer-coisa"

        with self.assertRaises(RuntimeError) as erro:
            bot.main()

        mensagem = str(erro.exception)
        self.assertIn("vazou", mensagem)
        self.assertIn("/revoke", mensagem)

    def test_the_secret_half_of_the_leaked_token_is_not_in_the_code(self):
        # A guarda usa so o id do bot, que nao e segredo. Guardar a outra
        # metade aqui seria versionar a credencial de novo.
        fonte = Path("bot.py").read_text(encoding="utf-8")

        self.assertNotIn("AAFf9GaV", fonte)
        self.assertEqual(bot.BOT_ID_COMPROMETIDO, bot.BOT_ID_COMPROMETIDO.split(":")[0])

    def test_a_normal_token_passes_the_checks(self):
        # A guarda nao pode barrar quem fez tudo certo.
        bot._validar_token("1234567890:AAHrealisticlookingtokenvalue123456789")


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
