"""A entrada pela porta HTTP, exercitada como o app a usa.

O teste que mais importa aqui nao e "consigo entrar": e o que a resposta conta
para quem nao deveria saber. Uma rota aberta que responda diferente para e-mail
da lista e e-mail de fora transforma "entrar no app" em "descobrir quem trabalha
na empresa" — com uma lista de enderecos e um laco.
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

SEGREDO = "segredo-de-teste-bem-comprido"
EMAIL = "joana.ribeiro@metalurgica-exemplo.com.br"
FORA = "ninguem@outra-empresa.com"


class Caixa:
    def __init__(self):
        self.recebidos: list[tuple[str, str]] = []

    def __call__(self, email: str, codigo: str) -> None:
        self.recebidos.append((email, codigo))

    @property
    def ultimo_codigo(self) -> str:
        return self.recebidos[-1][1]


class Base(AsyncHTTPTestCase):
    SEGREDO = SEGREDO

    def get_app(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        conn = self.abrir()
        init_schema(conn)
        identidade.autorizar(conn, EMAIL, "SM")
        conn.close()
        self.caixa = Caixa()
        return criar_app(self.abrir, "token-publicar", segredo=self.SEGREDO, enviar=self.caixa)

    def abrir(self):
        conn = sqlite3.connect(self.banco)
        conn.row_factory = sqlite3.Row
        return conn

    def postar(self, rota: str, corpo: dict, token: str = ""):
        cabecalhos = {"Content-Type": "application/json"}
        if token:
            cabecalhos["Authorization"] = f"Bearer {token}"
        return self.fetch(rota, method="POST", body=json.dumps(corpo).encode(),
                          headers=cabecalhos)

    def entrar(self, email: str = EMAIL) -> str:
        self.postar("/api/entrar", {"email": email})
        resposta = self.postar("/api/codigo", {"email": email, "codigo": self.caixa.ultimo_codigo})
        self.assertEqual(resposta.code, 200, resposta.body)
        return json.loads(resposta.body)["token"]


class EntrarTest(Base):
    def test_o_circuito_inteiro(self):
        resposta = self.postar("/api/entrar", {"email": EMAIL})
        self.assertEqual(resposta.code, 200)
        self.assertEqual(len(self.caixa.recebidos), 1)

        conferida = self.postar(
            "/api/codigo", {"email": EMAIL, "codigo": self.caixa.ultimo_codigo}
        )
        self.assertEqual(conferida.code, 200)
        corpo = json.loads(conferida.body)
        self.assertTrue(corpo["token"])
        self.assertEqual(corpo["email"], EMAIL)
        self.assertEqual(corpo["unidade"], "SM")

        eu = self.fetch("/api/eu", headers={"Authorization": f"Bearer {corpo['token']}"})
        self.assertEqual(eu.code, 200)
        self.assertEqual(json.loads(eu.body)["email"], EMAIL)

    def test_codigo_errado_recusa_com_recado_unico(self):
        self.postar("/api/entrar", {"email": EMAIL})
        certo = self.caixa.ultimo_codigo
        errado = "000000" if certo != "000000" else "111111"
        resposta = self.postar("/api/codigo", {"email": EMAIL, "codigo": errado})
        self.assertEqual(resposta.code, 401)
        # O recado nao diferencia "errado" de "expirado": saber qual dos dois
        # ajuda quem esta chutando a decidir se continua.
        self.assertIn("invalido ou expirado", json.loads(resposta.body)["erro"])

    def test_sem_codigo_no_corpo_nao_entra(self):
        self.postar("/api/entrar", {"email": EMAIL})
        self.assertEqual(self.postar("/api/codigo", {"email": EMAIL}).code, 401)

    def test_corpo_que_nao_e_json_nao_derruba_a_rota(self):
        resposta = self.fetch("/api/entrar", method="POST", body=b"nao sou json")
        self.assertEqual(resposta.code, 400)

    def test_email_sem_formato_de_email_e_recusado(self):
        resposta = self.postar("/api/entrar", {"email": "sem-arroba"})
        self.assertEqual(resposta.code, 400)
        self.assertEqual(self.caixa.recebidos, [])


class NaoContaQuemEstaNaListaTest(Base):
    """A propriedade que sustenta deixar a rota aberta."""

    def test_a_resposta_e_identica_para_email_de_dentro_e_de_fora(self):
        dentro = self.postar("/api/entrar", {"email": EMAIL})
        fora = self.postar("/api/entrar", {"email": FORA})
        self.assertEqual(dentro.code, fora.code)
        self.assertEqual(dentro.body, fora.body, "a resposta nao pode diferenciar os dois")
        # E o de fora nao recebeu nada.
        self.assertEqual([e for e, _ in self.caixa.recebidos], [EMAIL])

    def test_o_pedido_repetido_responde_igual_ao_primeiro(self):
        """Senao "espere 60s" confirma que o endereco existe."""
        primeiro = self.postar("/api/entrar", {"email": EMAIL})
        segundo = self.postar("/api/entrar", {"email": EMAIL})
        self.assertEqual(primeiro.body, segundo.body)
        self.assertEqual(len(self.caixa.recebidos), 1, "o segundo pedido nao envia")

    def test_a_resposta_nao_carrega_o_codigo(self):
        resposta = self.postar("/api/entrar", {"email": EMAIL})
        self.assertNotIn(self.caixa.ultimo_codigo, resposta.body.decode())

    def test_email_de_fora_nao_vira_linha_no_banco(self):
        self.postar("/api/entrar", {"email": FORA})
        conn = self.abrir()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM codigo_entrada").fetchone()["n"], 0
            )
        finally:
            conn.close()


class EntregaQuebradaTest(Base):
    """SMTP fora do ar responde igual a SMTP funcionando.

    Num app proprio: a comparacao e contra o corpo que a rota promete, e nao
    contra outra resposta do mesmo servidor — trocar o remetente de um servidor
    ja de pe daria um teste que passa sem nunca ter usado o remetente quebrado.
    """

    def get_app(self):
        # Pelo efeito colateral: cria o banco descartavel e autoriza o e-mail.
        # O app que ele devolve e descartado, porque este teste precisa de um
        # com remetente quebrado.
        super().get_app()

        def quebrado(email, codigo):
            raise RuntimeError("SMTP fora do ar")

        return criar_app(self.abrir, "token-publicar", segredo=SEGREDO, enviar=quebrado)

    def test_entrega_que_falha_responde_como_entrega_que_deu_certo(self):
        resposta = self.postar("/api/entrar", {"email": EMAIL})
        self.assertEqual(resposta.code, 200)
        corpo = json.loads(resposta.body)
        self.assertTrue(corpo["pedido"])
        self.assertIn("estiver na lista", corpo["recado"])
        self.assertEqual(self.caixa.recebidos, [], "o remetente quebrado nao entregou nada")

    def test_entrega_que_falha_nao_deixa_codigo_valido_para_chutar(self):
        self.postar("/api/entrar", {"email": EMAIL})
        conn = self.abrir()
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) AS n FROM codigo_entrada").fetchone()["n"], 0
            )
        finally:
            conn.close()


class SessaoTest(Base):
    def test_sem_token_nao_ha_eu(self):
        self.assertEqual(self.fetch("/api/eu").code, 401)

    def test_token_chutado_nao_abre_sessao(self):
        self.entrar()
        resposta = self.fetch("/api/eu", headers={"Authorization": "Bearer chute"})
        self.assertEqual(resposta.code, 401)

    def test_sair_derruba_o_token(self):
        token = self.entrar()
        self.assertEqual(self.postar("/api/sair", {}, token=token).code, 200)
        resposta = self.fetch("/api/eu", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resposta.code, 401)

    def test_sair_com_token_invalido_tambem_responde_200(self):
        """Responder diferente contaria se o token era valido."""
        valido = self.postar("/api/sair", {}, token=self.entrar())
        chutado = self.postar("/api/sair", {}, token="chute")
        self.assertEqual(valido.code, chutado.code)
        self.assertEqual(valido.body, chutado.body)

    def test_revogar_derruba_a_sessao_pela_porta_http(self):
        token = self.entrar()
        conn = self.abrir()
        try:
            identidade.revogar(conn, EMAIL)
        finally:
            conn.close()
        resposta = self.fetch("/api/eu", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resposta.code, 401)


class SemSegredoTest(Base):
    """Servidor sem `APETIT_SEGREDO` nao vira porta destrancada."""

    SEGREDO = ""

    def test_sem_segredo_a_entrada_fica_indisponivel(self):
        self.assertEqual(self.postar("/api/entrar", {"email": EMAIL}).code, 503)
        self.assertEqual(
            self.postar("/api/codigo", {"email": EMAIL, "codigo": "123456"}).code, 503
        )
        self.assertEqual(self.caixa.recebidos, [])

    def test_o_resto_do_app_continua_de_pe(self):
        """Sem entrada por codigo, o cardapio do dia ainda responde."""
        self.assertEqual(json.loads(self.fetch("/api/saude").body)["ok"], True)


class SemSmtpTest(AsyncHTTPTestCase):
    """Sem remetente configurado, a rota diz que nao da — e nao finge que enviou."""

    def get_app(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        conn = sqlite3.connect(self.banco)
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        identidade.autorizar(conn, EMAIL, "SM")
        conn.close()
        return criar_app(self.abrir, "t", segredo=SEGREDO, enviar=None)

    def abrir(self):
        conn = sqlite3.connect(self.banco)
        conn.row_factory = sqlite3.Row
        return conn

    def test_sem_remetente_a_rota_de_entrar_responde_indisponivel(self):
        resposta = self.fetch(
            "/api/entrar", method="POST", body=json.dumps({"email": EMAIL}).encode()
        )
        self.assertEqual(resposta.code, 503)


class DadoPessoalTest(Base):
    """Identificar quem pergunta nao virou desculpa para subir alergia."""

    def test_o_dia_continua_publico_e_impessoal(self):
        """A promessa do termo nao muda por existir login."""
        resposta = self.fetch("/api/dia?unidade=SM&dia=2025-09-01")
        # Sem cardapio publicado neste banco, mas o que importa e nao exigir
        # token: 404 e a resposta de "nao tem cardapio", nao de "identifique-se".
        self.assertEqual(resposta.code, 404)

    def test_o_eu_nao_devolve_restricao_nem_historico(self):
        token = self.entrar()
        visto = json.loads(
            self.fetch("/api/eu", headers={"Authorization": f"Bearer {token}"}).body
        )
        for proibido in ("restricoes", "alergias", "objetivo", "historico", "progresso"):
            self.assertNotIn(proibido, visto)


if __name__ == "__main__":
    unittest.main()
