"""O comando /piloto, que gera o relatorio que vai para a empresa.

O teste central nao e o numero: e que o relatorio saia completo **sem** dizer
quem usou o app e qual e a meta de cada um. Esse relatorio vai para o
empregador, e o funcionario aceitou o termo porque o app promete que a empresa
nao ve isso sobre ele.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

import bot
from apetit.catalog import import_menu_csv, init_schema
from apetit.prescription import Prescription, save_prescription
from apetit.profile import Employee, save_employee
from apetit.tracking import log_consumption
from tests.test_bot import FakeContext, FakeUpdate

FIXTURES = Path(__file__).parent / "fixtures"


class PilotoBotTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        bot.DB_PATH = Path(self.tmp.name)
        conn = bot.db()
        init_schema(conn)
        import_menu_csv(conn, (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"), unit="SM")
        conn.close()
        self.admin = 1
        self.admins_original = bot.ADMIN_IDS
        bot.ADMIN_IDS = {self.admin}
        self.today_original = bot.today
        bot.today = lambda: "2025-09-30"

    def tearDown(self):
        bot.ADMIN_IDS = self.admins_original
        bot.today = self.today_original
        Path(self.tmp.name).unlink(missing_ok=True)

    def piloto_de_15(self):
        # IDs altos de proposito: "100" apareceria como "100%" no relatorio e o
        # teste de vazamento passaria a acusar um numero legitimo.
        conn = bot.db()
        try:
            for i in range(15):
                save_employee(conn, Employee(
                    telegram_id=900_001 + i, name=f"Funcionario {i}", apetit_unit="SM",
                    client_company="Industria Exemplo", sector="Producao",
                    goal="Comer mais leve" if i % 2 else "Manter o equilibrio",
                    consent_accepted=True,
                ))
            for i in range(11):
                log_consumption(conn, 900_001 + i, "2025-09-01", ["arroz_parboilizado"])
                if i < 8:
                    log_consumption(conn, 900_001 + i, "2025-09-02", ["sal_mix_de_alface"])
            save_prescription(conn, 900_001, Prescription(kcal=600, escopo="almoco"))
        finally:
            conn.close()

    async def relatorio(self, args=()):
        context = FakeContext()
        context.args = list(args)
        update = FakeUpdate(user_id=self.admin)
        await bot.show_pilot_report(update, context)
        return update, "\n".join(update.message.replies)

    async def test_only_an_admin_sees_the_pilot_report(self):
        context = FakeContext()
        update = FakeUpdate(user_id=999)

        await bot.show_pilot_report(update, context)

        self.assertIn("Apenas administradores", update.last)

    async def test_it_reports_signup_use_and_return(self):
        self.piloto_de_15()

        _, texto = await self.relatorio(["15", "2025-09-01", "2025-09-30"])

        self.assertIn("Cadastrados:       15", texto)
        self.assertIn("Usaram de fato:    11", texto)
        self.assertIn("Voltaram outro dia:  8", texto)

    async def test_the_report_never_names_a_person(self):
        self.piloto_de_15()

        _, texto = await self.relatorio(["15", "2025-09-01", "2025-09-30"])

        for i in range(15):
            self.assertNotIn(f"Funcionario {i}", texto)
            self.assertNotIn(str(900_001 + i), texto)

    async def test_the_report_states_what_it_leaves_out(self):
        # Sem isso, quem le acha que o relatorio veio incompleto.
        self.piloto_de_15()

        _, texto = await self.relatorio(["15"])

        self.assertIn("O QUE NAO ENTRA NESTE RELATORIO", texto)
        self.assertIn("a meta de cada pessoa", texto)

    async def test_a_goal_group_too_small_to_hide_a_person_is_suppressed(self):
        conn = bot.db()
        try:
            for i in range(6):
                save_employee(conn, Employee(
                    telegram_id=200 + i, name=f"P{i}", apetit_unit="SM",
                    client_company="I", sector="P", goal="Manter o equilibrio",
                    consent_accepted=True,
                ))
            save_employee(conn, Employee(
                telegram_id=300, name="Unica", apetit_unit="SM", client_company="I",
                sector="P", goal="Comer mais leve", consent_accepted=True,
            ))
        finally:
            conn.close()

        _, texto = await self.relatorio(["7"])

        self.assertIn("Manter o equilibrio", texto)
        self.assertIn("suprimido", texto)

    async def test_without_the_invited_count_it_asks_instead_of_inventing_one(self):
        self.piloto_de_15()

        _, texto = await self.relatorio()

        self.assertNotIn("Convidados:", texto)
        self.assertIn("/piloto 15", texto)

    async def test_a_bad_date_is_explained_not_swallowed(self):
        _, texto = await self.relatorio(["15", "semana passada"])

        self.assertIn("Data em formato estranho", texto)

    async def test_the_daily_curve_shows_whether_it_caught_on(self):
        self.piloto_de_15()

        _, texto = await self.relatorio(["15", "2025-09-01", "2025-09-30"])

        self.assertIn("PESSOAS POR DIA", texto)
        self.assertIn("2025-09-01", texto)

    async def test_a_long_report_is_split_instead_of_cut(self):
        # Cortar no limite do Telegram perderia justamente o rodape que diz o
        # que ficou de fora do relatorio.
        pedacos = bot._split_message("\n".join(f"linha {i}" for i in range(2000)))

        self.assertGreater(len(pedacos), 1)
        self.assertTrue(all(len(p) <= 3600 for p in pedacos))
        self.assertIn("linha 1999", pedacos[-1])


if __name__ == "__main__":
    unittest.main()
