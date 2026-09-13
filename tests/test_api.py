"""A porta HTTP, exercitada como o cliente a usa: por HTTP.

O que importa aqui nao e "a funcao devolve o dicionario certo" — e o circuito
inteiro: a operacao manda o arquivo, o importador roda, e o app que perguntar em
seguida recebe o cardapio novo. E, do outro lado, que sem token ninguem publica.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tornado.testing import AsyncHTTPTestCase

from apetit.api import criar_app
from apetit.catalog import init_schema

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOKEN = "token-de-teste-bem-comprido"
DIA = "2025-09-01"


class ApiTest(AsyncHTTPTestCase):
    def get_app(self):
        # Banco em arquivo, e nao `:memory:`, para cada pedido abrir e fechar a
        # propria conexao — que e o que o servidor faz de verdade.
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.banco = Path(self.pasta.name) / "teste.db"
        conn = self.abrir()
        init_schema(conn)
        conn.close()
        return criar_app(self.abrir, TOKEN)

    def abrir(self):
        conn = sqlite3.connect(self.banco)
        conn.row_factory = sqlite3.Row
        return conn

    def publicar(self, arquivo="cardapio_largo.csv", token=TOKEN, unidade="SM", extra=""):
        corpo = (FIXTURES / arquivo).read_bytes()
        cabecalhos = {"Content-Type": "text/csv"}
        if token is not None:
            cabecalhos["Authorization"] = f"Bearer {token}"
        return self.fetch(
            f"/api/cardapio?unidade={unidade}&refeicao=almoco{extra}",
            method="POST", body=corpo, headers=cabecalhos,
        )

    def dia(self, unidade="SM", dia=DIA):
        return self.fetch(f"/api/dia?unidade={unidade}&dia={dia}")

    # ---------- o circuito ----------

    def test_publicar_faz_o_cardapio_aparecer_para_o_app(self):
        """O que a operacao manda e o que o funcionario ve."""
        self.assertEqual(self.dia().code, 404, "o dia comeca sem cardapio")

        resposta = self.publicar()
        self.assertEqual(resposta.code, 200)
        publicado = json.loads(resposta.body)
        self.assertEqual(publicado["publicados"], 18)
        self.assertIn(DIA, publicado["dias"])

        visto = json.loads(self.dia().body)
        nomes = [i["nome"] for g in visto["cardapio"] for i in g["itens"]]
        self.assertIn("Carne assada ao molho", nomes)
        self.assertEqual(visto["dia"], DIA)

    def test_o_app_recebe_o_cardapio_corrigido_quando_reenviam(self):
        """Reenviar o periodo e como a operacao corrige uma semana publicada."""
        self.publicar()
        antes = json.loads(self.dia().body)
        original = [i["nome"] for g in antes["cardapio"] for i in g["itens"]]

        corrigido = (FIXTURES / "cardapio_largo.csv").read_text(encoding="utf-8-sig")
        corrigido = corrigido.replace("CARNE ASSADA AO MOLHO", "CARNE ASSADA COM ALHO")
        resposta = self.fetch(
            "/api/cardapio?unidade=SM&refeicao=almoco", method="POST",
            body=corrigido.encode("utf-8"),
            headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "text/csv"},
        )
        self.assertEqual(resposta.code, 200)

        depois = [i["nome"] for g in json.loads(self.dia().body)["cardapio"] for i in g["itens"]]
        self.assertIn("Carne assada com alho", depois)
        self.assertIn("Carne assada ao molho", original)

    def test_cada_unidade_ve_o_seu_cardapio(self):
        """Publicar numa unidade nao pode aparecer na outra."""
        self.publicar(unidade="SM")
        self.assertEqual(self.dia(unidade="OUTRA").code, 404)
        self.assertEqual(self.dia(unidade="SM").code, 200)

    # ---------- quem publica se identifica ----------

    def test_sem_token_nao_publica(self):
        self.assertEqual(self.publicar(token=None).code, 401)
        self.assertEqual(self.dia().code, 404, "nada foi publicado")

    def test_token_errado_nao_publica(self):
        self.assertEqual(self.publicar(token="chute").code, 401)
        self.assertEqual(self.dia().code, 404, "nada foi publicado")

    def test_token_quase_certo_nao_publica(self):
        """Prefixo correto nao vale: a comparacao e do token inteiro."""
        self.assertEqual(self.publicar(token=TOKEN[:-1]).code, 401)
        self.assertEqual(self.dia().code, 404)

    def test_sem_token_no_ambiente_a_rota_recusa_tudo(self):
        """Servidor sem token configurado nao vira porta destrancada."""
        # A recusa acontece de fato, com token vazio dos dois lados.
        resposta = self.fetch(
            "/api/cardapio?unidade=SM", method="POST", body=b"x",
            headers={"Authorization": "Bearer "},
        )
        self.assertEqual(resposta.code, 401)

    # ---------- quem le nao se identifica, e nada pessoal sai ----------

    def test_o_payload_nao_carrega_dado_de_pessoa(self):
        """A promessa do termo: o servidor nao sabe o que voce nao pode comer."""
        self.publicar()
        corpo = self.dia().body.decode("utf-8")
        visto = json.loads(corpo)
        for proibido in ("perfil", "progresso", "meu_dia", "semana", "pessoa"):
            self.assertNotIn(proibido, visto, f"{proibido} nao pode sair do servidor")
        # Nem veredito: ele depende de quem le, e e calculado no navegador.
        for grupo in visto["cardapio"]:
            for item in grupo["itens"]:
                self.assertNotIn("veredito", item)
                self.assertIn("declaracoes", item)

    def test_ler_o_dia_nao_exige_token(self):
        self.publicar()
        self.assertEqual(self.dia().code, 200)

    # ---------- arquivo ruim nao derruba nem publica pela metade ----------

    def test_arquivo_que_nao_e_cardapio_nao_publica(self):
        resposta = self.fetch(
            "/api/cardapio?unidade=SM", method="POST",
            body=b"isto nao e um cardapio, e um bilhete\n",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        # O importador reconhece que nao ha cabecalho de cardapio e nao publica
        # nada. Responder 200 com zero itens e correto: o arquivo foi lido, e a
        # resposta diz o que aconteceu com ele.
        self.assertEqual(resposta.code, 200)
        self.assertEqual(json.loads(resposta.body)["publicados"], 0)
        self.assertEqual(self.dia().code, 404, "nada de um bilhete foi publicado")

    def test_arquivo_vazio_e_recusado(self):
        resposta = self.fetch(
            "/api/cardapio?unidade=SM", method="POST", body=b"   ",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resposta.code, 400)

    def test_sem_unidade_nao_publica(self):
        """Publicar sem unidade e publicar para ninguem, e some sem erro."""
        resposta = self.fetch(
            "/api/cardapio?refeicao=almoco", method="POST",
            body=(FIXTURES / "cardapio_largo.csv").read_bytes(),
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(resposta.code, 400)

    def test_refeicao_desconhecida_e_recusada(self):
        resposta = self.publicar(extra="&refeicao=ceia")
        self.assertEqual(resposta.code, 400)

    def test_o_bloqueio_do_importador_chega_na_resposta(self):
        """Macro inconsistente nao publica, e quem mandou fica sabendo."""
        publicado = json.loads(self.publicar().body)
        self.assertEqual(publicado["bloqueados"], 2)
        bloqueios = [a for a in publicado["achados"] if a["bloqueio"]]
        self.assertTrue(any("BIFE SUINO" in a["item"] for a in bloqueios))

    def test_ler_unidade_sem_cardapio_responde_404_e_nao_500(self):
        resposta = self.dia(unidade="NAO_EXISTE")
        self.assertEqual(resposta.code, 404)
        self.assertTrue(json.loads(resposta.body)["vazio"])

    def test_saude_responde_sem_banco_publicado(self):
        corpo = json.loads(self.fetch("/api/saude").body)
        self.assertTrue(corpo["ok"])
        self.assertTrue(corpo["publicacao"], "o token esta configurado neste teste")


if __name__ == "__main__":
    unittest.main()
