"""O painel da Apetit atras de uma porta que tranca.

Este e o primeiro controle de acesso de verdade do projeto, e o que esta do
outro lado explica por que: a insatisfacao de Copel, Sanepar e Coca-Cola com o
servico que a Apetit presta. Vazar isso nao expoe um funcionario — expoe o
cliente para o concorrente dele.

O que os testes cobram, em ordem de gravidade: que sem sessao nao entra, que
sessao de funcionario nao entra, que tirar alguem da gestao vale na hora e nao
daqui a trinta dias, e que o numero de recorte pequeno **nao vai no corpo** em
vez de ir escondido.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tornado.testing import AsyncHTTPTestCase

from apetit import identidade
from apetit.api import criar_app
from apetit.catalog import init_schema
from apetit.feedback import MIN_RATINGS, Rating, save_rating
from apetit.profile import Employee, save_employee

SEGREDO = "segredo-de-teste-bem-comprido"
GESTOR = "coordenacao@apetit-exemplo.com.br"
FUNCIONARIO = "joana@metalurgica-exemplo.com.br"

DESDE, ATE = "2025-09-01", "2025-09-30"


class PainelBase(AsyncHTTPTestCase):
    def get_app(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        conn = self.abrir()
        init_schema(conn)
        identidade.autorizar(conn, GESTOR, "SM", papel=identidade.PAPEL_GESTAO)
        identidade.autorizar(conn, FUNCIONARIO, "SM")
        self.semear(conn)
        conn.close()
        return criar_app(self.abrir, "token-publicar", segredo=SEGREDO, enviar=lambda *a: None)

    def abrir(self):
        conn = sqlite3.connect(self.banco)
        conn.row_factory = sqlite3.Row
        return conn

    def semear(self, conn):
        """Tres empresas: uma bem, uma mal, e uma pequena demais para contar."""
        for empresa, nota, quantos in (
            ("Copel", 3, MIN_RATINGS),
            ("Coca-Cola", 1, MIN_RATINGS),
            ("Sanepar", 3, MIN_RATINGS - 1),
        ):
            for i in range(quantos):
                pessoa = abs(hash((empresa, i))) % 90000 + 1000
                save_employee(conn, Employee(
                    pessoa_id=pessoa, name=f"P{i}", apetit_unit="SM",
                    client_company=empresa, sector="Operacao", goal="manter",
                ))
                save_rating(conn, pessoa, Rating(
                    apetit_unit="SM", service_date="2025-09-02",
                    food=nota, service=nota, comment="Faltou tempero" if nota == 1 else "",
                ))

    def entrar(self, email: str) -> str:
        """Abre uma sessao de verdade, pela mesma porta que o app usa."""
        conn = self.abrir()
        codigo = "123456"
        conn.execute(
            "INSERT OR REPLACE INTO codigo_entrada (email, hash, expira_em, tentativas, pedido_em)"
            " VALUES (?, ?, ?, 0, ?)",
            (email, identidade._hash_codigo(email, codigo, SEGREDO),
             "2999-01-01T00:00:00+00:00", identidade.agora_iso()),
        )
        conn.commit()
        entrada = identidade.conferir_codigo(conn, email, codigo, SEGREDO)
        conn.close()
        return entrada.token

    def painel(self, token: str = "", **extra):
        rota = f"/api/gestao/feedback?desde={DESDE}&ate={ATE}"
        for chave, valor in extra.items():
            rota += f"&{chave}={valor}"
        cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
        return self.fetch(rota, headers=cabecalhos)


class PortaTest(PainelBase):
    def test_sem_sessao_nao_entra(self):
        self.assertEqual(self.painel().code, 401)

    def test_token_inventado_nao_entra(self):
        self.assertEqual(self.painel("token-que-alguem-chutou").code, 401)

    def test_sessao_de_funcionario_nao_entra(self):
        # 403 e nao 401: a sessao vale, o papel e que nao da.
        resposta = self.painel(self.entrar(FUNCIONARIO))

        self.assertEqual(resposta.code, 403)
        self.assertNotIn("Coca-Cola", resposta.body.decode())

    def test_sessao_de_gestao_entra(self):
        self.assertEqual(self.painel(self.entrar(GESTOR)).code, 200)

    def test_tirar_da_gestao_vale_na_hora(self):
        """O papel e lido da lista a cada pergunta, nao gravado no token.

        Se ficasse na sessao, quem saisse da funcao continuaria lendo avaliacao
        de cliente por ate trinta dias — o tempo de vida da sessao.
        """
        token = self.entrar(GESTOR)
        self.assertEqual(self.painel(token).code, 200)

        conn = self.abrir()
        identidade.autorizar(conn, GESTOR, papel=identidade.PAPEL_FUNCIONARIO)
        conn.close()

        self.assertEqual(self.painel(token).code, 403)

    def test_revogar_derruba_o_painel_tambem(self):
        token = self.entrar(GESTOR)
        conn = self.abrir()
        identidade.revogar(conn, GESTOR)
        conn.close()

        # Passa porque `revogar` apaga a sessao junto. O teste seguinte cobre o
        # caso em que ela sobrevive, que e outro caminho.
        self.assertEqual(self.painel(token).code, 401)

    def test_sessao_orfa_nao_e_tratada_como_gestao(self):
        """Sessao sem linha na lista nao herda papel nenhum.

        `revogar` apaga a sessao junto, entao este caminho nao aparece pelo uso
        normal — ele aparece por uma limpeza feita direto no banco, ou por um
        defeito que deixe a sessao para tras. Se o papel ausente virasse gestao
        por descuido do `COALESCE`, uma sessao orfa passaria a ler avaliacao de
        cliente sem estar na lista de ninguem.
        """
        token = self.entrar(GESTOR)
        conn = self.abrir()
        conn.execute("DELETE FROM pessoa_email WHERE email = ?", (GESTOR,))
        conn.commit()
        sobrou = conn.execute("SELECT COUNT(*) AS t FROM sessao").fetchone()["t"]
        conn.close()

        self.assertEqual(sobrou, 1, "o teste precisa da sessao de pe para valer")
        self.assertEqual(self.painel(token).code, 403)


class ConteudoTest(PainelBase):
    def corpo(self) -> dict:
        resposta = self.painel(self.entrar(GESTOR))
        self.assertEqual(resposta.code, 200)
        return json.loads(resposta.body)

    def test_a_empresa_que_precisa_de_atencao_vem_primeiro(self):
        empresas = self.corpo()["empresas"]

        self.assertEqual(empresas[0]["nome"], "Coca-Cola")
        self.assertEqual(empresas[0]["comida_boa_pct"], 0.0)

    def test_cada_empresa_aparece_separada(self):
        por_nome = {e["nome"]: e for e in self.corpo()["empresas"]}

        self.assertEqual(por_nome["Copel"]["atendimento_bom_pct"], 100.0)
        self.assertEqual(por_nome["Coca-Cola"]["atendimento_bom_pct"], 0.0)

    def test_recorte_pequeno_nao_manda_o_numero_no_corpo(self):
        """Suprimido sai **sem** a chave, e nao com o valor escondido.

        Mandar o numero e confiar que nenhuma tela, hoje ou daqui a um ano, vai
        imprimi-lo. Num piloto de quinze pessoas, "tres avaliacoes, todas boas"
        aponta para gente.
        """
        pequena = next(e for e in self.corpo()["empresas"] if e["nome"] == "Sanepar")

        self.assertTrue(pequena["suprimido"])
        self.assertNotIn("comida_boa_pct", pequena)
        self.assertNotIn("atendimento_bom_pct", pequena)
        self.assertEqual(pequena["comentarios"], [])

    def test_o_corpo_nunca_carrega_pessoa(self):
        cru = json.dumps(self.corpo(), ensure_ascii=False).lower()

        self.assertNotIn("pessoa_id", cru)
        self.assertNotIn("email", cru)
        self.assertNotIn("sector", cru)
        self.assertNotIn("setor", cru)

    def test_o_comentario_da_empresa_com_volume_aparece(self):
        coca = next(e for e in self.corpo()["empresas"] if e["nome"] == "Coca-Cola")

        self.assertIn("Faltou tempero", coca["comentarios"])

    def test_a_tendencia_semanal_vem_junto(self):
        coca = next(e for e in self.corpo()["empresas"] if e["nome"] == "Coca-Cola")

        self.assertTrue(coca["semanas"])
        self.assertTrue(all("semana" in s for s in coca["semanas"]))


if __name__ == "__main__":
    unittest.main()
