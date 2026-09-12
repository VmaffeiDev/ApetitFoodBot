"""Os avisos de progresso dentro do bot: tela, entrega e desistencia.

A regra de quem recebe o que esta testada em test_nudges. Aqui interessa o que
o bot faz com ela: que a pessoa consiga desligar, que nada seja marcado como
enviado se o envio falhou, e que bloquear o bot desligue o aviso em vez de
render uma tentativa por semana para sempre.
"""

import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

from telegram.error import Forbidden, TimedOut

import bot
from apetit.catalog import import_menu_csv, init_schema
from apetit.nudges import (
    LEMBRETE_ALMOCO,
    RESUMO_SEMANAL,
    already_sent,
    notification_settings,
    set_notification,
)
from tests.test_bot import FakeContext, FakeUpdate

FIXTURES = Path(__file__).parent / "fixtures"


class EntregaFalsa:
    """Bot de mentira que registra o que foi enviado — e pode falhar de proposito."""

    def __init__(self, erro=None):
        self.enviadas = []
        self.erro = erro

    async def send_message(self, chat_id=None, text=None, parse_mode=None):
        if self.erro:
            raise self.erro
        self.enviadas.append((chat_id, text))


class AvisoBase(unittest.IsolatedAsyncioTestCase):
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
        self.today_original = bot.today
        bot.today = lambda: "2025-09-05"  # sexta coberta pelo cardapio de teste

    def tearDown(self):
        bot.ADMIN_IDS = self.admins
        bot.today = self.today_original
        Path(self.tmp.name).unlink(missing_ok=True)

    async def registrar(self, context):
        await bot.start(FakeUpdate(), context)
        for texto in ("Mariana", "SM", "Industria Exemplo", "Producao"):
            await bot.handle_message(FakeUpdate(text=texto), context)
        await bot.handle_callback(FakeUpdate(callback="goal_manter"), context)
        await bot.handle_callback(FakeUpdate(callback="restr_ok"), context)
        await bot.handle_callback(FakeUpdate(callback="consent_sim"), context)

    def escolhas(self):
        conn = bot.db()
        try:
            return notification_settings(conn, self.user)
        finally:
            conn.close()

    def marcado(self, kind, period):
        conn = bot.db()
        try:
            return already_sent(conn, self.user, kind, period)
        finally:
            conn.close()


class TelaTest(AvisoBase):
    async def test_the_screen_shows_what_is_on_and_what_is_off(self):
        context = FakeContext()
        await self.registrar(context)

        update = FakeUpdate(user_id=self.user)
        await bot.show_notifications(update, context)

        rotulos = dict((rotulo, dado) for rotulo, dado in update.buttons)
        ligado = [r for r in rotulos if r.startswith("☑️")]
        desligado = [r for r in rotulos if r.startswith("⬜")]
        self.assertTrue(any("semana" in r for r in ligado))
        self.assertTrue(any("almoco" in r for r in desligado))

    async def test_a_tap_turns_it_off_and_another_turns_it_back_on(self):
        context = FakeContext()
        await self.registrar(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback=f"aviso:{RESUMO_SEMANAL}"), context)
        self.assertFalse(self.escolhas()[RESUMO_SEMANAL])

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback=f"aviso:{RESUMO_SEMANAL}"), context)
        self.assertTrue(self.escolhas()[RESUMO_SEMANAL])

    async def test_the_screen_promises_not_to_comment_on_the_food(self):
        context = FakeContext()
        await self.registrar(context)

        update = FakeUpdate(user_id=self.user)
        await bot.show_notifications(update, context)

        self.assertIn("nunca comento o que voce comeu", update.last)


class EntregaTest(AvisoBase):
    async def preparar(self, context, erro=None):
        await self.registrar(context)
        conn = bot.db()
        try:
            from apetit.tracking import log_consumption
            log_consumption(conn, self.user, "2025-09-01", ["arroz_parboilizado"])
        finally:
            conn.close()
        context.bot = EntregaFalsa(erro)
        return context.bot

    async def test_the_weekly_summary_reaches_the_person(self):
        context = FakeContext()
        entrega = await self.preparar(context)

        await bot.send_weekly_summaries(context)

        self.assertEqual(len(entrega.enviadas), 1)
        chat_id, texto = entrega.enviadas[0]
        self.assertEqual(chat_id, self.user)
        self.assertIn("Sua semana", texto)
        self.assertTrue(self.marcado(RESUMO_SEMANAL, "2025-09-01"))

    async def test_it_is_not_sent_twice(self):
        context = FakeContext()
        entrega = await self.preparar(context)

        await bot.send_weekly_summaries(context)
        await bot.send_weekly_summaries(context)

        self.assertEqual(len(entrega.enviadas), 1)

    async def test_a_failed_send_is_not_marked_as_sent(self):
        # Marcar antes de entregar faria a queda de rede virar um resumo que a
        # pessoa nunca recebe e que nunca sera reenviado.
        context = FakeContext()
        await self.preparar(context, erro=TimedOut())

        await bot.send_weekly_summaries(context)

        self.assertFalse(self.marcado(RESUMO_SEMANAL, "2025-09-01"))

    async def test_blocking_the_bot_turns_the_notice_off(self):
        context = FakeContext()
        await self.preparar(context, erro=Forbidden("bot was blocked by the user"))

        await bot.send_weekly_summaries(context)

        self.assertFalse(self.escolhas()[RESUMO_SEMANAL])
        self.assertFalse(self.marcado(RESUMO_SEMANAL, "2025-09-01"))

    async def test_the_lunch_reminder_only_goes_to_who_asked(self):
        context = FakeContext()
        entrega = await self.preparar(context)
        bot.today = lambda: "2025-09-02"  # terca, e a pessoa nao registrou

        await bot.send_lunch_reminders(context)
        self.assertEqual(entrega.enviadas, [])

        conn = bot.db()
        try:
            set_notification(conn, self.user, LEMBRETE_ALMOCO, True)
        finally:
            conn.close()
        await bot.send_lunch_reminders(context)

        self.assertEqual(len(entrega.enviadas), 1)
        self.assertIn("Ja almocou?", entrega.enviadas[0][1])

    async def test_deleting_my_data_stops_the_notices(self):
        context = FakeContext()
        entrega = await self.preparar(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="del_sim"), context)
        await bot.send_weekly_summaries(context)

        self.assertEqual(entrega.enviadas, [])


class AgendaTest(AvisoBase):
    async def test_the_reminder_lands_at_lunch_time_in_brazil(self):
        # O resto do app trabalha em UTC; um lembrete de almoco as 11h UTC
        # chegaria as 8h da manha para quem recebe.
        self.assertEqual(bot.HORA_LEMBRETE.hour, 11)
        self.assertEqual(bot.HORA_LEMBRETE.utcoffset().total_seconds(), -3 * 3600)

    async def test_the_weekly_summary_goes_out_on_friday(self):
        """Sexta no calendario do JobQueue, que nao e o do `date.weekday()`.

        O `run_daily` do python-telegram-bot conta **domingo como 0** desde a
        v20; o `weekday()` do Python conta segunda como 0. A versao anterior
        deste teste repetia o literal `(4,)` e por isso nao viu que o resumo
        estava saindo na quinta. Aqui a sexta e derivada de uma sexta de
        verdade, entao trocar a constante de volta quebra o teste.
        """
        sexta = date(2025, 9, 5)
        self.assertEqual(sexta.strftime("%A"), "Friday")

        ptb = sexta.isoweekday() % 7          # domingo=0 ... sabado=6
        self.assertEqual(bot.DIA_DO_RESUMO, (ptb,))
        self.assertNotEqual(bot.DIA_DO_RESUMO, (sexta.weekday(),))

    async def test_without_a_job_queue_the_bot_still_starts(self):
        class AppSemFila:
            job_queue = None

        bot.schedule_nudges(AppSemFila())  # nao levanta


if __name__ == "__main__":
    unittest.main()
