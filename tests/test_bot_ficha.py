"""A ficha do nutricionista dentro do bot.

Aqui se testa o caminho inteiro: o funcionario manda a ficha, confere o que o
app entendeu e confirma — e so entao o app troca o alvo generico pelo dela.

Dois riscos guiam quase todo teste deste arquivo:

1. **o total do dia virar alvo do almoco**, que mandaria a pessoa comer o dia
   inteiro num prato so;
2. **o app aplicar um numero que a pessoa nao confirmou**, o que seria dar
   orientacao a partir de uma leitura automatica de documento clinico.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")

import bot
from apetit.catalog import import_menu_csv, init_schema
from apetit.prescription import load_prescription
from tests.test_bot import FakeContext, FakeDocument, FakeUpdate

FIXTURES = Path(__file__).parent / "fixtures"

FICHA_DO_DIA = """
Plano alimentar - Clinica Exemplo
Paciente: Mariana
VET: 1800 kcal/dia
PTN: 90 g/dia
Evitar: frituras, refrigerante
CRN-3 12345
"""

FICHA_DO_ALMOCO = """
Almoco: 600 kcal
PTN almoco: 35 g
"""


class FichaBase(unittest.IsolatedAsyncioTestCase):
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
        bot.today = lambda: "2025-09-01"   # dia coberto pelo cardapio de teste
        # A leitura de PDF ja e testada em test_prescription; aqui interessa o
        # que o bot faz com o texto, entao o PDF entra pronto.
        self.read_pdf_original = bot.read_pdf
        self.texto_pdf = FICHA_DO_DIA
        bot.read_pdf = lambda conteudo: self.texto_pdf

    def tearDown(self):
        bot.read_pdf = self.read_pdf_original
        bot.today = self.today_original
        bot.ADMIN_IDS = self.admins
        Path(self.tmp.name).unlink(missing_ok=True)

    async def registrar(self, context, objetivo="goal_manter"):
        await bot.start(FakeUpdate(), context)
        for texto in ("Mariana", "SM", "Industria Exemplo", "Producao"):
            await bot.handle_message(FakeUpdate(text=texto), context)
        await bot.handle_callback(FakeUpdate(callback=objetivo), context)
        await bot.handle_callback(FakeUpdate(callback="restr_ok"), context)
        await bot.handle_callback(FakeUpdate(callback="consent_sim"), context)

    async def mandar_ficha(self, context, texto=None):
        if texto is not None:
            self.texto_pdf = texto
        conteudo = b"%PDF-1.4 (o conteudo nao importa: read_pdf esta trocado)"
        update = FakeUpdate(user_id=self.user, document=FakeDocument("ficha.pdf", conteudo))
        context._arquivo = conteudo
        await bot.receive_document(update, context)
        return update

    def pessoa(self):
        return bot.current_employee(FakeUpdate(user_id=self.user))

    def ficha_salva(self):
        conn = bot.db()
        try:
            return load_prescription(conn, self.user)
        finally:
            conn.close()


class LeituraTest(FichaBase):
    async def test_shows_the_line_each_number_came_from(self):
        # Sem o trecho, confirmar seria confiar as cegas na leitura automatica.
        context = FakeContext()
        await self.registrar(context)

        update = await self.mandar_ficha(context)

        self.assertIn("1800", update.last)
        self.assertIn("VET: 1800 kcal/dia", update.last)
        self.assertIn("li de", update.last)

    async def test_nothing_is_saved_before_the_person_confirms(self):
        context = FakeContext()
        await self.registrar(context)

        await self.mandar_ficha(context)

        self.assertIsNone(self.ficha_salva())

    async def test_a_number_read_wrong_can_be_dropped(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, FICHA_DO_ALMOCO)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_campo:kcal"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        ficha = self.ficha_salva()
        self.assertIsNone(ficha.kcal)
        self.assertEqual(ficha.ptn_g, 35)


class TotalDoDiaTest(FichaBase):
    """O erro que machuca: 1.800 kcal/dia viram o alvo de um prato."""

    async def test_it_always_asks_whether_the_numbers_are_daily(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, FICHA_DO_ALMOCO)  # nem parece do dia

        update = FakeUpdate(user_id=self.user, callback="ficha_escopo")
        await bot.handle_callback(update, context)

        self.assertIn("dia inteiro ou so do almoco", update.last)
        self.assertIn("ficha_escopo:dia", [d for _, d in update.buttons])

    async def test_a_daily_sheet_is_split_instead_of_applied_whole(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:dia"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        alvo = bot.target_for(self.pessoa())
        self.assertEqual(alvo["kcal"], round(1800 * bot.FRACAO_ALMOCO_SUGERIDA))
        self.assertEqual(alvo["ptn"], round(90 * bot.FRACAO_ALMOCO_SUGERIDA))

    async def test_the_confirmation_screen_shows_the_split_number(self):
        # A pessoa confirma o que vai valer no almoco, nao o que estava no papel.
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        update = FakeUpdate(user_id=self.user, callback="ficha_escopo:dia")
        await bot.handle_callback(update, context)

        self.assertIn("630", update.last)          # 35% de 1800
        self.assertIn("Calorias no almoco", update.last)
        self.assertIn("nutricionista", update.last)

    async def test_a_daily_total_marked_as_lunch_is_refused_as_a_target(self):
        # Se a pessoa errar a pergunta, o app ainda assim nao sugere 1.800 kcal.
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        update = FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco")
        await bot.handle_callback(update, context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        self.assertIn("parece ser do dia inteiro", update.last)
        self.assertEqual(bot.target_for(self.pessoa()), bot.TARGETS["Manter o equilibrio"])


class AlvoTest(FichaBase):
    async def confirmar_almoco(self, context):
        await self.mandar_ficha(context, FICHA_DO_ALMOCO)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

    async def test_the_sheet_beats_the_generic_goal(self):
        context = FakeContext()
        await self.registrar(context, objetivo="goal_massa")  # 850 kcal / 45 g

        await self.confirmar_almoco(context)

        self.assertEqual(bot.target_for(self.pessoa()), {"kcal": 600, "ptn": 35})

    async def test_a_field_the_sheet_lacks_still_comes_from_the_goal(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, "Almoco: 600 kcal\n")
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        alvo = bot.target_for(self.pessoa())
        origem = bot.target_source(self.pessoa(), bot.prescription_for(self.pessoa()))
        self.assertEqual(alvo["kcal"], 600)
        self.assertEqual(alvo["ptn"], bot.TARGETS["Manter o equilibrio"]["ptn"])
        self.assertEqual(origem, {"kcal": "ficha", "ptn": "objetivo"})

    async def test_the_portion_screen_says_which_number_is_not_from_the_sheet(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, "Almoco: 600 kcal\n")
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        update = FakeUpdate(user_id=self.user)
        await bot.show_portions(update, context)

        self.assertIn("sua ficha", update.last)
        self.assertIn("A ficha nao trazia proteina", update.last)

    async def test_removing_the_sheet_goes_back_to_the_goal(self):
        context = FakeContext()
        await self.registrar(context)
        await self.confirmar_almoco(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_remover_sim"), context)

        self.assertIsNone(self.ficha_salva())
        self.assertEqual(bot.target_for(self.pessoa()), bot.TARGETS["Manter o equilibrio"])


class ProibidosTest(FichaBase):
    async def test_what_the_nutritionist_forbade_is_marked_on_the_menu(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, "Almoco: 600 kcal\nEvitar: feijao preto\n")
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        itens = bot.load_menu(self.pessoa(), bot.today())
        feijao = [i for i in itens if "FEIJAO" in i["name"].upper()]

        self.assertTrue(feijao, "o cardapio de teste precisa ter feijao")
        self.assertTrue(any(i["check"].verdict.name == "BLOQUEIO" for i in feijao))


class PrivacidadeTest(FichaBase):
    async def test_the_document_itself_is_never_stored(self):
        # A ficha traz peso, diagnostico e historico. O app fica so com os numeros.
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, FICHA_DO_DIA + "\nPeso: 96 kg\nDiagnostico: pre-diabetes\n")
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:dia"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        despejo = Path(self.tmp.name).read_bytes().decode("latin-1").lower()

        self.assertNotIn("diagnostico", despejo)
        self.assertNotIn("96 kg", despejo)
        self.assertNotIn("pre-diabetes", despejo)

    async def test_deleting_my_data_takes_the_sheet_with_it(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, FICHA_DO_ALMOCO)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="del_sim"), context)

        self.assertIsNone(self.ficha_salva())

    async def test_my_data_screen_says_the_document_was_not_kept(self):
        context = FakeContext()
        await self.registrar(context)
        await self.mandar_ficha(context, FICHA_DO_ALMOCO)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        update = FakeUpdate(user_id=self.user)
        await bot.show_my_data(update, context)

        self.assertIn("Ficha do nutricionista", update.last)
        self.assertIn("nao foi guardado", update.last)


class SemLeituraTest(FichaBase):
    async def test_a_scanned_sheet_is_admitted_instead_of_guessed(self):
        context = FakeContext()
        await self.registrar(context)

        update = await self.mandar_ficha(context, "")

        self.assertIn("foto ou digitalizacao", update.last)
        self.assertIn("ficha_manual", [d for _, d in update.buttons])
        self.assertIsNone(self.ficha_salva())

    async def test_typing_the_numbers_works_for_a_paper_sheet(self):
        context = FakeContext()
        await self.registrar(context)

        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_manual"), context)
        await bot.handle_message(FakeUpdate(user_id=self.user, text="600"), context)
        await bot.handle_message(FakeUpdate(user_id=self.user, text="35"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_escopo:almoco"), context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_ok"), context)

        self.assertEqual(bot.target_for(self.pessoa()), {"kcal": 600, "ptn": 35})

    async def test_typed_garbage_is_not_accepted_as_a_target(self):
        context = FakeContext()
        await self.registrar(context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_manual"), context)

        update = FakeUpdate(user_id=self.user, text="mais ou menos isso")
        await bot.handle_message(update, context)

        self.assertIn("Nao entendi esse numero", update.last)
        self.assertEqual(context.user_data.get(bot.STEP), "ficha_kcal")

    async def test_skipping_every_number_saves_nothing(self):
        context = FakeContext()
        await self.registrar(context)
        await bot.handle_callback(FakeUpdate(user_id=self.user, callback="ficha_manual"), context)

        await bot.handle_message(FakeUpdate(user_id=self.user, text="pular"), context)
        update = FakeUpdate(user_id=self.user, text="pular")
        await bot.handle_message(update, context)

        self.assertIsNone(self.ficha_salva())
        self.assertIn("Sem nenhum numero", update.last)


class RoteamentoTest(FichaBase):
    async def test_a_pdf_goes_to_the_sheet_and_a_spreadsheet_to_the_menu(self):
        # Um admin tambem almoca: o tipo do arquivo decide, sem perguntar nada.
        bot.ADMIN_IDS = {self.user}
        context = FakeContext()
        await self.registrar(context)

        update = await self.mandar_ficha(context, FICHA_DO_ALMOCO)

        self.assertIn("Confira o que eu entendi", update.last)


if __name__ == "__main__":
    unittest.main()
