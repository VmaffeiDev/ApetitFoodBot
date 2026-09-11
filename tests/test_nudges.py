"""Aviso de progresso.

O app passa a falar primeiro. Quase todo teste aqui e sobre **nao** mandar
mensagem: para quem desligou, para quem ja recebeu, para quem parou de usar, e
sobre um prato. Uma mensagem a mais num app da empresa sobre o que a pessoa come
custa mais caro que uma a menos.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.catalog import import_menu_csv, init_schema
from apetit.nudges import (
    LEMBRETE_ALMOCO,
    RESUMO_SEMANAL,
    already_sent,
    lunch_nudges,
    mark_sent,
    notification_settings,
    set_notification,
    weekly_nudges,
)
from apetit.profile import Employee, save_employee
from apetit.tracking import log_consumption, score_day

FIXTURES = Path(__file__).parent / "fixtures"

# 2025-09-01 e uma segunda; 2025-09-05, a sexta em que o resumo sai.
SEGUNDA = "2025-09-01"
SEXTA = "2025-09-05"


class NudgeBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        import_menu_csv(self.conn, (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8"), unit="SM")

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def pessoa(self, uid=1, consentiu=True):
        save_employee(self.conn, Employee(
            telegram_id=uid, name=f"P{uid}", apetit_unit="SM", client_company="Industria",
            sector="Producao", goal="Manter o equilibrio", consent_accepted=consentiu,
        ))
        return uid

    def comeu(self, uid, dia, codigos=("arroz_parboilizado",)):
        log_consumption(self.conn, uid, dia, list(codigos))


class ResumoSemanalTest(NudgeBase):
    def test_it_reports_the_persons_own_week(self):
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)
        self.comeu(uid, "2025-09-02")

        avisos = weekly_nudges(self.conn, SEXTA)

        self.assertEqual(len(avisos), 1)
        self.assertIn("2 de 5 dias", avisos[0].text)

    def test_it_never_mentions_a_colleague_or_an_average(self):
        # Mesma regra do resto do app: nao existe ranking.
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)
        self.pessoa(2)
        self.comeu(2, SEGUNDA)
        self.comeu(2, "2025-09-02")

        texto = weekly_nudges(self.conn, SEXTA)[0].text.lower()

        for proibido in ("colega", "media", "equipe", "ranking", "setor", "que voce"):
            self.assertNotIn(proibido, texto.replace("nao existe ranking", ""))

    def test_it_never_judges_what_was_eaten(self):
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)

        texto = weekly_nudges(self.conn, SEXTA)[0].text.lower()

        for proibido in ("caloria", "kcal", "peso", "emagrec", "exagero", "passou d"):
            self.assertNotIn(proibido, texto)

    def test_achievements_of_the_week_are_listed(self):
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)
        score_day(self.conn, uid, SEGUNDA, protein_target_g=1)

        texto = weekly_nudges(self.conn, SEXTA)[0].text

        self.assertIn("conquistas", texto.lower())
        self.assertIn("Registrou a refeicao", texto)

    def test_it_says_how_to_stop_receiving(self):
        # Aviso de app da empresa sem saida visivel e pressao.
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)

        self.assertIn("/avisos", weekly_nudges(self.conn, SEXTA)[0].text)


class NaoMandaTest(NudgeBase):
    def test_nobody_gets_it_twice_in_the_same_week(self):
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)
        aviso = weekly_nudges(self.conn, SEXTA)[0]

        mark_sent(self.conn, uid, aviso.kind, aviso.period)

        self.assertEqual(weekly_nudges(self.conn, SEXTA), [])
        self.assertTrue(already_sent(self.conn, uid, RESUMO_SEMANAL, aviso.period))

    def test_turning_it_off_stops_it(self):
        uid = self.pessoa()
        self.comeu(uid, SEGUNDA)

        set_notification(self.conn, uid, RESUMO_SEMANAL, False)

        self.assertEqual(weekly_nudges(self.conn, SEXTA), [])
        self.assertFalse(notification_settings(self.conn, uid)[RESUMO_SEMANAL])

    def test_someone_who_never_consented_gets_nothing(self):
        self.pessoa(uid=9, consentiu=False)

        self.assertEqual(weekly_nudges(self.conn, SEXTA), [])

    def test_after_two_silent_weeks_the_summary_goes_quiet(self):
        # Quem decidiu nao usar nao precisa de cobranca semanal da empresa.
        uid = self.pessoa()
        self.comeu(uid, "2025-08-18")  # duas semanas antes

        primeira = weekly_nudges(self.conn, "2025-08-29")  # semana seguinte, ainda convida
        segunda = weekly_nudges(self.conn, SEXTA)          # mais uma semana em branco

        self.assertEqual(len(primeira), 1)
        self.assertIn("Sem pressa", primeira[0].text)
        self.assertEqual(segunda, [])

    def test_a_first_empty_week_invites_instead_of_charging(self):
        uid = self.pessoa()
        self.comeu(uid, "2025-08-29")  # sexta da semana anterior

        texto = weekly_nudges(self.conn, SEXTA)[0].text

        self.assertIn("Sem pressa", texto)
        for proibido in ("voce nao registrou nenhum dia", "falta", "deveria"):
            self.assertNotIn(proibido, texto.lower())


class LembreteAlmocoTest(NudgeBase):
    def test_it_is_opt_in(self):
        uid = self.pessoa()

        self.assertEqual(lunch_nudges(self.conn, SEGUNDA), [])
        self.assertFalse(notification_settings(self.conn, uid)[LEMBRETE_ALMOCO])

    def test_someone_who_asked_for_it_gets_it(self):
        uid = self.pessoa()
        set_notification(self.conn, uid, LEMBRETE_ALMOCO, True)

        avisos = lunch_nudges(self.conn, SEGUNDA)

        self.assertEqual(len(avisos), 1)
        self.assertIn("Ja almocou?", avisos[0].text)

    def test_whoever_already_registered_is_left_alone(self):
        uid = self.pessoa()
        set_notification(self.conn, uid, LEMBRETE_ALMOCO, True)
        self.comeu(uid, SEGUNDA)

        self.assertEqual(lunch_nudges(self.conn, SEGUNDA), [])

    def test_no_menu_no_reminder(self):
        # Sem cardapio o lembrete nao leva a lugar nenhum.
        uid = self.pessoa()
        set_notification(self.conn, uid, LEMBRETE_ALMOCO, True)

        self.assertEqual(lunch_nudges(self.conn, "2025-10-06"), [])

    def test_weekends_are_quiet(self):
        uid = self.pessoa()
        set_notification(self.conn, uid, LEMBRETE_ALMOCO, True)

        self.assertEqual(lunch_nudges(self.conn, "2025-09-06"), [])  # sabado

    def test_it_does_not_repeat_on_the_same_day(self):
        uid = self.pessoa()
        set_notification(self.conn, uid, LEMBRETE_ALMOCO, True)
        aviso = lunch_nudges(self.conn, SEGUNDA)[0]

        mark_sent(self.conn, uid, aviso.kind, aviso.period)

        self.assertEqual(lunch_nudges(self.conn, SEGUNDA), [])


class ExclusaoTest(NudgeBase):
    def test_deleting_my_data_takes_the_notification_choices_with_it(self):
        from apetit.profile import delete_employee_data

        uid = self.pessoa()
        set_notification(self.conn, uid, LEMBRETE_ALMOCO, True)
        mark_sent(self.conn, uid, RESUMO_SEMANAL, SEGUNDA)

        delete_employee_data(self.conn, uid)

        sobrou = self.conn.execute(
            "SELECT COUNT(*) AS t FROM employee_notification WHERE telegram_id = ?", (uid,)
        ).fetchone()["t"]
        enviados = self.conn.execute(
            "SELECT COUNT(*) AS t FROM notification_sent WHERE telegram_id = ?", (uid,)
        ).fetchone()["t"]
        self.assertEqual((sobrou, enviados), (0, 0))


if __name__ == "__main__":
    unittest.main()
