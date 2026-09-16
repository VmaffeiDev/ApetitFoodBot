"""Avaliacao do refeitorio.

O que este arquivo protege nao e a media: e a pessoa que deu a nota. Esta e a
unica parte do app cujo dado a Apetit le, e quem reclama esta reclamando do
servico contratado pela propria empresa onde trabalha. Se a avaliacao vazasse
identificada, o funcionario que disse "faltou comida" ficaria exposto — e o
proximo aprenderia a mentir.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from apetit.catalog import init_schema
from apetit.feedback import (
    MIN_RATINGS,
    MISSING_TAGS,
    Rating,
    all_company_reports,
    all_unit_reports,
    company_comments,
    company_report,
    company_trend,
    my_ratings,
    rating_for,
    save_rating,
    unit_comments,
    unit_report,
    unit_trend,
)
from apetit.profile import Employee, delete_employee_data, save_employee


class BancoBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def avaliar(self, pessoa_id, dia, food=3, service=3, missing=False, tags=(), comment="", unit="SM"):
        save_rating(
            self.conn,
            pessoa_id,
            Rating(
                apetit_unit=unit,
                service_date=dia,
                food=food,
                service=service,
                missing=missing,
                tags=list(tags),
                comment=comment,
            ),
        )

    def encher(self, unit="SM", dia="2025-09-01", quantos=MIN_RATINGS, food=3):
        for i in range(quantos):
            self.avaliar(1000 + i, dia, food=food, unit=unit)


class RegistroTest(BancoBase):
    def test_saves_what_the_person_answered(self):
        self.avaliar(1, "2025-09-01", food=2, service=3, missing=True, tags=["acabou"], comment="Acabou o feijao")

        guardada = rating_for(self.conn, 1, "2025-09-01")
        self.assertEqual(guardada.food, 2)
        self.assertEqual(guardada.service, 3)
        self.assertTrue(guardada.missing)
        self.assertEqual(guardada.tags, ["acabou"])
        self.assertEqual(guardada.comment, "Acabou o feijao")

    def test_rating_again_the_same_day_replaces(self):
        self.avaliar(1, "2025-09-01", food=1, tags=["acabou"])
        self.avaliar(1, "2025-09-01", food=3, tags=[])

        self.assertEqual(rating_for(self.conn, 1, "2025-09-01").food, 3)
        self.assertEqual(rating_for(self.conn, 1, "2025-09-01").tags, [])
        total = self.conn.execute("SELECT COUNT(*) AS t FROM service_rating").fetchone()["t"]
        self.assertEqual(total, 1)

    def test_replacing_does_not_leave_orphan_tags(self):
        self.avaliar(1, "2025-09-01", tags=["acabou", "comida_fria"])
        self.avaliar(1, "2025-09-01", tags=[])

        sobrando = self.conn.execute("SELECT COUNT(*) AS t FROM service_rating_tag").fetchone()["t"]
        self.assertEqual(sobrando, 0)

    def test_rating_without_a_canteen_is_refused(self):
        # Sem refeitorio nao ha o que agregar, e a linha viraria dado solto.
        with self.assertRaises(ValueError):
            save_rating(self.conn, 1, Rating(apetit_unit="", service_date="2025-09-01", food=3))

    def test_score_outside_the_scale_is_refused(self):
        with self.assertRaises(ValueError):
            save_rating(self.conn, 1, Rating(apetit_unit="SM", service_date="2025-09-01", food=7))

    def test_unknown_missing_reason_is_refused(self):
        with self.assertRaises(ValueError):
            save_rating(
                self.conn, 1,
                Rating(apetit_unit="SM", service_date="2025-09-01", tags=["motivo_inventado"]),
            )

    def test_an_empty_rating_is_recognised_as_empty(self):
        self.assertTrue(Rating(apetit_unit="SM", service_date="2025-09-01").empty)
        self.assertFalse(Rating(apetit_unit="SM", service_date="2025-09-01", food=1).empty)

    def test_the_person_can_see_their_own_ratings(self):
        self.avaliar(1, "2025-09-01")
        self.avaliar(1, "2025-09-02")

        minhas = my_ratings(self.conn, 1)
        self.assertEqual([m["service_date"] for m in minhas], ["2025-09-02", "2025-09-01"])


class NaoIdentificaQuemRespondeuTest(BancoBase):
    """A protecao central: a gestao ve o refeitorio, nunca a pessoa."""

    def test_the_rating_row_never_stores_the_sector(self):
        # E o cruzamento que reidentifica: "a unica pessoa da manutencao que
        # almocou terca". Setor e recorte pequeno por natureza — num piloto de
        # quinze pessoas, um setor tem duas. Nao existe coluna para isso, entao
        # nao ha como alguem consultar por ali depois.
        colunas = {c["name"] for c in self.conn.execute("PRAGMA table_info(service_rating)")}

        self.assertNotIn("sector", colunas)

    def test_the_rating_row_stores_the_company_and_that_is_deliberate(self):
        """A empresa fica na linha; o setor nao. A diferenca nao e de grau.

        A empresa **e** o contrato: a Apetit precisa saber que a Coca-Cola esta
        mal atendida e a Copel nao, porque e isso que ela vai corrigir na
        segunda-feira. Empresa cliente tem centenas de funcionarios, e o recorte
        ainda passa pelo n minimo antes de virar numero.

        Setor nao tem defesa equivalente: ele e pequeno por construcao, e
        nenhum n minimo devolve o anonimato de "o unico da manutencao".

        Guardar a empresa na linha, em vez de buscar em `employee` pelo
        `pessoa_id` na hora do relatorio, e o que mantem a outra regra literal:
        nenhuma leitura da gestao toca em `pessoa_id`.
        """
        colunas = {c["name"] for c in self.conn.execute("PRAGMA table_info(service_rating)")}

        self.assertIn("client_company", colunas)

    def test_the_company_comes_from_the_record_not_from_the_caller(self):
        # Se a empresa viesse no `Rating`, a tela poderia mandar qualquer nome e
        # a avaliacao de uma empresa entraria no relatorio de outra.
        save_employee(self.conn, Employee(
            pessoa_id=77, name="Alguem", apetit_unit="SM",
            client_company="Copel", sector="Operacao", goal="manter",
        ))
        save_rating(self.conn, 77, Rating(apetit_unit="SM", service_date="2025-09-01", food=3))

        linha = self.conn.execute(
            "SELECT client_company FROM service_rating WHERE pessoa_id = 77"
        ).fetchone()
        self.assertEqual(linha["client_company"], "Copel")

    def test_no_management_query_selects_the_person(self):
        """A regra que sustenta tudo, cobrada no texto das consultas.

        O teste anterior olha o objeto que volta. Este olha o SQL: uma consulta
        nova que juntasse `employee` pelo `pessoa_id` para achar a empresa
        passaria despercebida ali, porque o `pessoa_id` nao apareceria no
        resultado — mas o cruzamento teria acontecido.
        """
        import ast
        import inspect
        import textwrap

        from apetit import feedback as modulo

        for nome in ("_report", "_rated", "_all_reports", "_comments", "_trend"):
            arvore = ast.parse(textwrap.dedent(inspect.getsource(getattr(modulo, nome))))
            funcao = arvore.body[0]
            # O docstring fala sobre a regra e cita `pessoa_id` para explicar que
            # ela nao entra. Quem e cobrado aqui e o codigo, entao ele sai.
            if (funcao.body and isinstance(funcao.body[0], ast.Expr)
                    and isinstance(funcao.body[0].value, ast.Constant)
                    and isinstance(funcao.body[0].value.value, str)):
                funcao.body = funcao.body[1:]
            codigo = ast.unparse(funcao)

            self.assertNotIn("pessoa_id", codigo, f"{nome} nao pode tocar em pessoa_id")
            self.assertNotIn("employee", codigo, f"{nome} nao pode juntar com o cadastro")

    def test_the_unit_report_carries_no_person(self):
        self.encher()

        relatorio = unit_report(self.conn, "SM", "2025-09-01", "2025-09-30")

        self.assertNotIn("pessoa_id", vars(relatorio))
        self.assertFalse(any("telegram" in str(v).lower() for v in vars(relatorio).values()))

    def test_a_small_sample_is_suppressed(self):
        # Com poucas avaliacoes, "media do refeitorio" volta a ser a opiniao
        # identificavel de uma pessoa.
        self.encher(quantos=MIN_RATINGS - 1)

        relatorio = unit_report(self.conn, "SM", "2025-09-01", "2025-09-30")

        self.assertTrue(relatorio.suppressed)
        self.assertIsNone(relatorio.food_avg)
        self.assertIsNone(relatorio.food_good_pct)
        self.assertIsNone(relatorio.missing_pct)
        self.assertIn("menos de", relatorio.reason)

    def test_comments_are_withheld_until_there_is_volume(self):
        # Um comentario num dia de tres avaliacoes e um bilhete assinado.
        for i in range(MIN_RATINGS - 1):
            self.avaliar(1000 + i, "2025-09-01", comment="A comida estava fria")

        self.assertEqual(unit_comments(self.conn, "SM", "2025-09-01", "2025-09-30"), [])

    def test_comments_are_released_with_volume_and_without_authors(self):
        for i in range(MIN_RATINGS):
            self.avaliar(1000 + i, "2025-09-01", comment=f"comentario {i}")

        comentarios = unit_comments(self.conn, "SM", "2025-09-01", "2025-09-30")

        self.assertEqual(len(comentarios), MIN_RATINGS)
        self.assertTrue(all(isinstance(c, str) for c in comentarios))

    def test_comments_are_not_returned_in_arrival_order(self):
        # A ordem de chegada, cruzada com quem almocou naquele dia, tambem
        # aponta para uma pessoa.
        for i, texto in enumerate(["zebra", "abacate", "melancia", "banana", "caqui"]):
            self.avaliar(1000 + i, "2025-09-01", comment=texto)

        comentarios = unit_comments(self.conn, "SM", "2025-09-01", "2025-09-30")

        self.assertEqual(comentarios, sorted(comentarios))

    def test_deleting_my_data_removes_my_ratings(self):
        save_employee(
            self.conn,
            Employee(pessoa_id=1, name="X", apetit_unit="SM", client_company="Y", sector="Z", consent_accepted=True),
        )
        self.avaliar(1, "2025-09-01", tags=["acabou"], comment="algo")

        delete_employee_data(self.conn, 1)

        self.assertEqual(my_ratings(self.conn, 1), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS t FROM service_rating").fetchone()["t"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS t FROM service_rating_tag").fetchone()["t"], 0)


class RelatorioTest(BancoBase):
    def test_reports_what_the_operation_can_act_on(self):
        for i in range(MIN_RATINGS):
            self.avaliar(1000 + i, "2025-09-01", food=1, service=3, missing=True, tags=["acabou"])

        relatorio = unit_report(self.conn, "SM", "2025-09-01", "2025-09-30")

        self.assertEqual(relatorio.total, MIN_RATINGS)
        self.assertEqual(relatorio.food_good_pct, 0.0)
        self.assertEqual(relatorio.service_good_pct, 100.0)
        self.assertEqual(relatorio.missing_pct, 100.0)
        self.assertEqual(relatorio.tags[0][0], "acabou")

    def test_percentage_ignores_who_did_not_answer_that_question(self):
        # Quem pulou a nota de atendimento nao pode contar como atendimento ruim.
        for i in range(MIN_RATINGS):
            self.avaliar(1000 + i, "2025-09-01", food=3, service=None)

        relatorio = unit_report(self.conn, "SM", "2025-09-01", "2025-09-30")

        self.assertEqual(relatorio.food_good_pct, 100.0)
        self.assertIsNone(relatorio.service_good_pct)

    def test_worst_canteen_comes_first(self):
        # O relatorio existe para achar o refeitorio com problema.
        self.encher(unit="Bom", food=3)
        self.encher(unit="Ruim", food=1)

        relatorios = all_unit_reports(self.conn, "2025-09-01", "2025-09-30")

        self.assertEqual(relatorios[0].name, "Ruim")

    def test_suppressed_canteens_go_last(self):
        self.encher(unit="Cheio", food=1)
        self.avaliar(9999, "2025-09-01", unit="Vazio")

        relatorios = all_unit_reports(self.conn, "2025-09-01", "2025-09-30")

        self.assertEqual(relatorios[0].name, "Cheio")
        self.assertTrue(relatorios[-1].suppressed)

    def test_one_canteen_does_not_leak_into_another(self):
        self.encher(unit="SM", food=1)
        self.encher(unit="OFL", food=3)

        self.assertEqual(unit_report(self.conn, "OFL", "2025-09-01", "2025-09-30").food_good_pct, 100.0)

    def test_trend_shows_the_week_it_dropped(self):
        # Uma media do mes esconde a semana em que o refeitorio caiu.
        self.encher(dia="2025-09-01", food=3)
        self.encher(dia="2025-09-08", food=1)

        serie = unit_trend(self.conn, "SM", weeks=3, today="2025-09-12")

        visiveis = [s for s in serie if not s.suppressed]
        self.assertEqual([s.food_good_pct for s in visiveis], [100.0, 0.0])
        self.assertEqual([s.period_start for s in visiveis], ["2025-09-01", "2025-09-08"])

    def test_every_missing_reason_has_a_label_for_the_report(self):
        # Codigo sem rotulo apareceria como "acabou" cru no relatorio.
        for code, rotulo in MISSING_TAGS.items():
            self.assertTrue(rotulo.strip(), code)


class PorEmpresaClienteTest(BancoBase):
    """O painel da Apetit: qual contrato esta indo bem e qual precisa de atencao.

    A Apetit serve varias empresas no mesmo mercado de refeicao coletiva. Saber
    que a Coca-Cola esta reclamando do atendimento e a Copel nao e a diferenca
    entre mandar alguem na unidade certa e mandar um comunicado para todas.
    """

    def cadastrar(self, pessoa_id, empresa, unidade="SM", setor="Operacao"):
        save_employee(self.conn, Employee(
            pessoa_id=pessoa_id, name=f"Pessoa {pessoa_id}", apetit_unit=unidade,
            client_company=empresa, sector=setor, goal="manter",
        ))

    def encher_empresa(self, empresa, quantos=MIN_RATINGS, food=3, service=3,
                       dia="2025-09-01", unidade="SM", inicio=0, **kw):
        for i in range(quantos):
            pessoa = hash((empresa, inicio + i)) % 100000 + 1
            self.cadastrar(pessoa, empresa, unidade)
            self.avaliar(pessoa, dia, food=food, service=service, unit=unidade, **kw)

    def test_each_company_is_reported_on_its_own(self):
        self.encher_empresa("Copel", food=3, service=3)
        self.encher_empresa("Coca-Cola", food=3, service=1)

        copel = company_report(self.conn, "Copel", "2025-09-01", "2025-09-30")
        coca = company_report(self.conn, "Coca-Cola", "2025-09-01", "2025-09-30")

        self.assertEqual(copel.service_good_pct, 100.0)
        self.assertEqual(coca.service_good_pct, 0.0)
        self.assertEqual(copel.dimension, "empresa")

    def test_the_company_that_needs_attention_comes_first(self):
        # O painel existe para achar o contrato com problema, nao para elogiar
        # o que esta bem.
        self.encher_empresa("Copel", food=3)
        self.encher_empresa("Sanepar", food=2)
        self.encher_empresa("Coca-Cola", food=1)

        relatorios = all_company_reports(self.conn, "2025-09-01", "2025-09-30")

        self.assertEqual([r.name for r in relatorios], ["Coca-Cola", "Sanepar", "Copel"])

    def test_a_company_with_few_ratings_is_suppressed(self):
        # Empresa pequena no piloto volta a ser gente identificavel. O n minimo
        # e o mesmo do resto do arquivo, porque e a mesma regra.
        self.encher_empresa("Pequena", quantos=MIN_RATINGS - 1)

        relatorio = company_report(self.conn, "Pequena", "2025-09-01", "2025-09-30")

        self.assertTrue(relatorio.suppressed)
        self.assertIsNone(relatorio.food_good_pct)
        self.assertIsNone(relatorio.missing_pct)

    def test_comments_only_come_out_with_volume(self):
        self.encher_empresa("Pequena", quantos=MIN_RATINGS - 1, comment="Faltou comida")

        self.assertEqual(company_comments(self.conn, "Pequena", "2025-09-01", "2025-09-30"), [])

    def test_one_company_does_not_leak_into_another(self):
        self.encher_empresa("Copel", food=3)
        self.encher_empresa("Coca-Cola", food=1)

        self.assertEqual(
            company_report(self.conn, "Copel", "2025-09-01", "2025-09-30").food_good_pct, 100.0)

    def test_two_companies_in_the_same_canteen_are_told_apart(self):
        # Uma unidade da Apetit pode servir mais de uma empresa. Se o painel
        # olhasse so a unidade, as duas virariam uma media unica e a empresa
        # insatisfeita sumiria dentro da satisfeita.
        self.encher_empresa("Copel", food=3, unidade="Centro")
        self.encher_empresa("Coca-Cola", food=1, unidade="Centro")

        unidade = unit_report(self.conn, "Centro", "2025-09-01", "2025-09-30")
        coca = company_report(self.conn, "Coca-Cola", "2025-09-01", "2025-09-30")

        self.assertEqual(unidade.food_good_pct, 50.0)
        self.assertEqual(coca.food_good_pct, 0.0)

    def test_a_rating_without_a_record_has_no_company(self):
        # Quem avaliou antes de completar o cadastro nao tem empresa. "" nao e
        # uma empresa, e nao pode virar coluna sem dono no painel.
        self.encher(quantos=MIN_RATINGS)

        self.assertEqual(all_company_reports(self.conn, "2025-09-01", "2025-09-30"), [])

    def test_trend_shows_the_week_the_company_dropped(self):
        self.encher_empresa("Coca-Cola", dia="2025-09-01", food=3)
        self.encher_empresa("Coca-Cola", dia="2025-09-08", food=1, inicio=50)

        serie = company_trend(self.conn, "Coca-Cola", weeks=3, today="2025-09-12")

        visiveis = [s for s in serie if not s.suppressed]
        self.assertEqual([s.food_good_pct for s in visiveis], [100.0, 0.0])

    def test_the_missing_reasons_come_per_company(self):
        self.encher_empresa("Coca-Cola", missing=True, tags=["acabou"])

        relatorio = company_report(self.conn, "Coca-Cola", "2025-09-01", "2025-09-30")

        self.assertEqual(relatorio.tags, [("acabou", MIN_RATINGS)])
        self.assertEqual(relatorio.missing_pct, 100.0)


if __name__ == "__main__":
    unittest.main()
