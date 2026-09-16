"""A entrada no app: e-mail da lista, codigo de seis digitos, sessao no aparelho.

Isto e o que substitui o Telegram como forma de saber quem esta perguntando.
Num app de alergia, errar aqui nao da "erro de login": da a restricao de uma
pessoa mostrada para outra. Entao os testes cobrem menos o caminho felizado e
mais o que um atacante tentaria — chutar codigo, reusar codigo, descobrir quem
trabalha na empresa, continuar dentro depois de perder o acesso.
"""

import sqlite3
import unittest
from datetime import UTC, datetime, timedelta
from unittest import mock

from apetit import identidade
from apetit.catalog import init_schema

SEGREDO = "segredo-de-teste-que-nao-mora-no-banco"
EMAIL = "joana.ribeiro@metalurgica-exemplo.com.br"
AGORA = "2026-09-13T12:00:00+00:00"


def mais(segundos: int = 0, minutos: int = 0, dias: int = 0) -> str:
    base = datetime.fromisoformat(AGORA)
    return (base + timedelta(seconds=segundos, minutes=minutos, days=dias)).isoformat()


class Caixa:
    """Uma caixa de entrada de mentira: guarda o que seria enviado."""

    def __init__(self, quebrar: bool = False):
        self.recebidos: list[tuple[str, str]] = []
        self.quebrar = quebrar

    def __call__(self, email: str, codigo: str) -> None:
        if self.quebrar:
            raise RuntimeError("servidor de e-mail fora do ar")
        self.recebidos.append((email, codigo))

    @property
    def ultimo_codigo(self) -> str:
        return self.recebidos[-1][1]


class Base(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        self.addCleanup(self.conn.close)
        self.caixa = Caixa()

    def autorizar(self, email: str = EMAIL, unidade: str = "SM") -> int:
        return identidade.autorizar(self.conn, email, unidade)

    def pedir(self, email: str = EMAIL, agora: str = AGORA) -> identidade.Pedido:
        return identidade.pedir_codigo(self.conn, email, SEGREDO, self.caixa, agora=agora)

    def conferir(self, codigo: str, email: str = EMAIL, agora: str = AGORA):
        return identidade.conferir_codigo(self.conn, email, codigo, SEGREDO, agora=agora)


# --------------------------------------------------------------------------
# O caminho que a pessoa faz
# --------------------------------------------------------------------------

class EntrarTest(Base):
    def test_pedir_conferir_e_entrar(self):
        pessoa = self.autorizar()
        self.assertTrue(self.pedir().entregue)
        self.assertEqual(len(self.caixa.recebidos), 1)

        entrada = self.conferir(self.caixa.ultimo_codigo)
        self.assertTrue(entrada.ok, entrada.motivo)
        self.assertEqual(entrada.pessoa_id, pessoa)
        self.assertEqual(entrada.email, EMAIL)

        dono = identidade.de_sessao(self.conn, entrada.token, agora=AGORA)
        self.assertIsNotNone(dono)
        self.assertEqual(dono["pessoa_id"], pessoa)

    def test_o_codigo_tem_seis_digitos(self):
        self.autorizar()
        self.pedir()
        codigo = self.caixa.ultimo_codigo
        self.assertEqual(len(codigo), identidade.TAMANHO_CODIGO)
        self.assertTrue(codigo.isdigit(), codigo)

    def test_codigo_com_zero_na_frente_vale(self):
        """"012345" nao pode virar 12345 no caminho.

        Formatar o codigo como numero em qualquer ponto comeria o zero e faria
        um em dez codigos simplesmente nao funcionar — o defeito que aparece
        como "o app nao aceita meu codigo" sem ninguem conseguir reproduzir.
        """
        self.autorizar()
        with mock.patch("apetit.identidade.secrets.randbelow", return_value=12345):
            self.pedir()
        self.assertEqual(self.caixa.ultimo_codigo, "012345")
        self.assertTrue(self.conferir("012345").ok)

    def test_email_com_maiuscula_e_espaco_e_o_mesmo_email(self):
        pessoa = self.autorizar()
        self.pedir(email=f"  {EMAIL.upper()}  ")
        entrada = self.conferir(self.caixa.ultimo_codigo, email=EMAIL.upper())
        self.assertTrue(entrada.ok, entrada.motivo)
        self.assertEqual(entrada.pessoa_id, pessoa)

    def test_ponto_no_email_nao_e_ignorado(self):
        """`ana.silva@` e `anasilva@` podem ser duas pessoas na mesma empresa.

        Normalizacao "esperta" de Gmail juntaria as duas — e juntar duas pessoas
        aqui e mostrar a alergia de uma para a outra.
        """
        uma = identidade.autorizar(self.conn, "ana.silva@exemplo.com.br", "SM")
        outra = identidade.autorizar(self.conn, "anasilva@exemplo.com.br", "SM")
        self.assertNotEqual(uma, outra)


# --------------------------------------------------------------------------
# Chutar codigo
# --------------------------------------------------------------------------

class ChuteTest(Base):
    def test_codigo_errado_nao_entra(self):
        self.autorizar()
        self.pedir()
        errado = "000000" if self.caixa.ultimo_codigo != "000000" else "111111"
        self.assertFalse(self.conferir(errado).ok)

    def test_o_codigo_morre_depois_do_limite_de_tentativas(self):
        """Cinco chutes e acabou — e o que faz seis digitos ser suficiente."""
        self.autorizar()
        self.pedir()
        certo = self.caixa.ultimo_codigo
        errado = "000000" if certo != "000000" else "111111"

        for _ in range(identidade.TENTATIVAS_MAX - 1):
            self.assertEqual(self.conferir(errado).motivo, "codigo errado")
        self.assertEqual(self.conferir(errado).motivo, "tentativas esgotadas")

        # E agora nem o codigo certo vale: a pessoa pede outro.
        depois = self.conferir(certo)
        self.assertFalse(depois.ok)
        self.assertEqual(depois.motivo, "sem codigo pendente")

    def test_codigo_expira(self):
        self.autorizar()
        self.pedir()
        tarde = mais(minutos=identidade.VALIDADE_CODIGO_MIN + 1)
        resultado = self.conferir(self.caixa.ultimo_codigo, agora=tarde)
        self.assertEqual(resultado.motivo, "codigo expirado")

    def test_codigo_no_limite_do_prazo_ainda_vale(self):
        """A borda conta: expirar um minuto antes da conta e recusar quem acertou."""
        self.autorizar()
        self.pedir()
        quase = mais(minutos=identidade.VALIDADE_CODIGO_MIN, segundos=-1)
        self.assertTrue(self.conferir(self.caixa.ultimo_codigo, agora=quase).ok)

    def test_codigo_usado_nao_serve_de_novo(self):
        """Codigo e de uma vez: senao fica uma segunda chave na caixa de entrada."""
        self.autorizar()
        self.pedir()
        codigo = self.caixa.ultimo_codigo
        self.assertTrue(self.conferir(codigo).ok)
        de_novo = self.conferir(codigo)
        self.assertFalse(de_novo.ok)
        self.assertEqual(de_novo.motivo, "sem codigo pendente")

    def test_pedir_de_novo_invalida_o_codigo_anterior(self):
        """Dois codigos validos ao mesmo tempo dobrariam a chance de um chute."""
        self.autorizar()
        self.pedir()
        primeiro = self.caixa.ultimo_codigo
        self.pedir(agora=mais(segundos=identidade.ESPERA_ENTRE_PEDIDOS_S + 1))
        segundo = self.caixa.ultimo_codigo
        if primeiro != segundo:  # 1 em 1e6 de sair igual
            self.assertFalse(self.conferir(primeiro, agora=mais(segundos=61)).ok)
        self.assertTrue(self.conferir(segundo, agora=mais(segundos=61)).ok)

    def test_codigo_de_uma_pessoa_nao_serve_para_outra(self):
        """Cada codigo vale so na conta que o pediu.

        Os dois precisam ter codigo pendente. Com so um pedido no ar, o outro
        cai em "sem codigo pendente" e o teste passa sem nunca ter testado o
        que diz testar — foi exatamente o que aconteceu na primeira versao
        disto.
        """
        outro = "rafael@metalurgica-exemplo.com.br"
        self.autorizar()
        self.autorizar(email=outro)

        with mock.patch("apetit.identidade.secrets.randbelow", return_value=111111):
            self.pedir()
        with mock.patch("apetit.identidade.secrets.randbelow", return_value=222222):
            self.pedir(email=outro)

        # O codigo da Joana, na conta do Rafael, que tem codigo proprio valido.
        recusado = self.conferir("111111", email=outro)
        self.assertFalse(recusado.ok, "codigo de um nao pode valer para outro")
        self.assertEqual(recusado.motivo, "codigo errado")
        # E o dele continua funcionando.
        self.assertTrue(self.conferir("222222", email=outro).ok)


# --------------------------------------------------------------------------
# O que o banco guarda
# --------------------------------------------------------------------------

class SegredoTest(Base):
    def test_o_banco_nao_guarda_o_codigo(self):
        self.autorizar()
        self.pedir()
        codigo = self.caixa.ultimo_codigo
        despejo = " ".join(
            str(tuple(linha)) for linha in self.conn.execute("SELECT * FROM codigo_entrada")
        )
        self.assertNotIn(codigo, despejo, "o codigo em claro nao pode ficar no banco")

    def test_o_banco_nao_guarda_o_token_da_sessao(self):
        self.autorizar()
        self.pedir()
        entrada = self.conferir(self.caixa.ultimo_codigo)
        despejo = " ".join(
            str(tuple(linha)) for linha in self.conn.execute("SELECT * FROM sessao")
        )
        self.assertNotIn(entrada.token, despejo, "o token em claro nao pode ficar no banco")

    def test_sem_o_segredo_do_servidor_o_hash_do_banco_nao_abre_nada(self):
        """A razao de existir o pepper, verificada e nao afirmada.

        Um banco vazado tem o hash e o e-mail. Sem o segredo, testar o milhao de
        codigos possiveis nao encontra o hash guardado — e um milhao de
        tentativas e coisa de segundos num laptop.
        """
        self.autorizar()
        self.pedir()
        guardado = self.conn.execute(
            "SELECT hash FROM codigo_entrada WHERE email = ?", (EMAIL,)
        ).fetchone()["hash"]

        for chute in range(0, 1000):  # amostra: o argumento nao depende do total
            candidato = f"{chute:06d}"
            sem_segredo = identidade._hash_codigo(EMAIL, candidato, "segredo-errado")
            self.assertNotEqual(sem_segredo, guardado)

    def test_duas_pessoas_com_o_mesmo_codigo_tem_hash_diferente(self):
        """Isto — e nao o bloqueio de codigo cruzado — e o que o e-mail no hash faz.

        Quem impede usar o codigo de um na conta de outro e a busca, que e por
        e-mail. O hash amarrado ao endereco resolve outra coisa: sem ele, duas
        pessoas que sorteassem o mesmo numero ficariam com hashes identicos no
        banco, e um vazamento mostraria "estes dois tem o mesmo codigo agora" —
        alem de um chute acertado valer para as duas contas de uma vez.

        Descobri a diferenca reintroduzindo o defeito: sem o e-mail no hash, o
        teste de codigo cruzado continuava passando, porque nunca tinha sido ele
        que pegava isso.
        """
        outro = "rafael@metalurgica-exemplo.com.br"
        self.autorizar()
        self.autorizar(email=outro)
        with mock.patch("apetit.identidade.secrets.randbelow", return_value=424242):
            self.pedir()
            self.pedir(email=outro)

        hashes = {
            linha["email"]: linha["hash"]
            for linha in self.conn.execute("SELECT email, hash FROM codigo_entrada")
        }
        self.assertEqual(len(hashes), 2)
        self.assertNotEqual(
            hashes[EMAIL], hashes[outro],
            "o mesmo codigo em duas contas nao pode virar o mesmo hash",
        )

    def test_o_pedido_nao_devolve_o_codigo(self):
        """Quem chama nao recebe o valor, entao nao consegue registrar em log.

        O codigo passa so pela funcao de entrega. Se `Pedido` carregasse o
        numero, um `logger.info(pedido)` bem-intencionado colocaria a chave de
        entrada no arquivo de log.
        """
        self.autorizar()
        pedido = self.pedir()
        self.assertNotIn(self.caixa.ultimo_codigo, repr(pedido))


# --------------------------------------------------------------------------
# Quem esta de fora fica de fora, e nao descobre quem esta dentro
# --------------------------------------------------------------------------

class ListaTest(Base):
    def test_email_fora_da_lista_nao_recebe_codigo(self):
        pedido = self.pedir(email="qualquer@outra-empresa.com")
        self.assertFalse(pedido.entregue)
        self.assertEqual(self.caixa.recebidos, [])

    def test_email_fora_da_lista_nao_deixa_linha_no_banco(self):
        """A rota e aberta: gravar cada endereco chutado seria escrever no banco
        sem ninguem autenticado."""
        self.pedir(email="chute@dominio-qualquer.com")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM codigo_entrada").fetchone()["n"], 0
        )

    def test_email_invalido_e_recusado_sem_gravar(self):
        for ruim in ("", "   ", "sem-arroba", "a@b", "a b@c.com", "x" * 300 + "@y.com"):
            with self.assertRaises(ValueError, msg=ruim):
                identidade.normalizar(ruim)

    def test_revogar_derruba_a_sessao_aberta(self):
        """Tirar da lista sem derrubar a sessao deixaria a pessoa dentro por 30 dias."""
        self.autorizar()
        self.pedir()
        entrada = self.conferir(self.caixa.ultimo_codigo)
        self.assertIsNotNone(identidade.de_sessao(self.conn, entrada.token, agora=AGORA))

        self.assertTrue(identidade.revogar(self.conn, EMAIL))
        self.assertIsNone(identidade.de_sessao(self.conn, entrada.token, agora=AGORA))

    def test_revogar_entre_o_pedido_e_a_conferencia_nao_deixa_entrar(self):
        self.autorizar()
        self.pedir()
        codigo = self.caixa.ultimo_codigo
        identidade.revogar(self.conn, EMAIL)
        resultado = self.conferir(codigo)
        self.assertFalse(resultado.ok)

    def test_autorizar_duas_vezes_nao_cria_outra_pessoa(self):
        primeiro = self.autorizar()
        segundo = self.autorizar()
        self.assertEqual(primeiro, segundo)
        self.assertEqual(len(identidade.autorizados(self.conn)), 1)

    def test_o_id_novo_nao_cai_em_cima_de_um_id_antigo_do_telegram(self):
        """Colisao aqui emenda o historico de duas pessoas — inclusive a alergia.

        Um id sequencial a partir de 1 encostaria num `pessoa_id` baixo de
        banco antigo. O teste usa o pior caso: um id legado igual a 1.
        """
        agora = datetime.now(UTC).isoformat(timespec="seconds")
        for legado in (1, 2, 500):
            self.conn.execute(
                "INSERT INTO employee (pessoa_id, name, created_at, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (legado, f"Legado {legado}", agora, agora),
            )
        self.conn.commit()

        novo = self.autorizar()
        self.assertGreater(novo, 500)
        usados = {
            linha["pessoa_id"]
            for linha in self.conn.execute("SELECT pessoa_id FROM employee")
        }
        self.assertNotIn(novo, usados)

    def test_dois_autorizados_nao_dividem_o_mesmo_id(self):
        um = self.autorizar(email="a@exemplo.com.br")
        dois = self.autorizar(email="b@exemplo.com.br")
        tres = self.autorizar(email="c@exemplo.com.br")
        self.assertEqual(len({um, dois, tres}), 3)


# --------------------------------------------------------------------------
# Pedir codigo sem parar
# --------------------------------------------------------------------------

class EsperaTest(Base):
    def test_pedido_repetido_no_mesmo_minuto_e_recusado(self):
        """Senao o botao "nao recebi" vira uma forma de encher a caixa de alguem."""
        self.autorizar()
        self.assertTrue(self.pedir().entregue)
        segundo = self.pedir(agora=mais(segundos=5))
        self.assertFalse(segundo.entregue)
        self.assertGreater(segundo.espere_segundos, 0)
        self.assertEqual(len(self.caixa.recebidos), 1)

    def test_depois_da_espera_o_pedido_passa(self):
        self.autorizar()
        self.pedir()
        adiante = mais(segundos=identidade.ESPERA_ENTRE_PEDIDOS_S + 1)
        self.assertTrue(self.pedir(agora=adiante).entregue)
        self.assertEqual(len(self.caixa.recebidos), 2)

    def test_entrega_que_falha_nao_deixa_codigo_valido_no_banco(self):
        """Codigo que ninguem recebeu so serve para a pessoa errar cinco vezes."""
        self.autorizar()
        self.caixa.quebrar = True
        with self.assertRaises(RuntimeError):
            self.pedir()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM codigo_entrada").fetchone()["n"], 0
        )


# --------------------------------------------------------------------------
# A sessao no aparelho
# --------------------------------------------------------------------------

class SessaoTest(Base):
    def entrar(self) -> str:
        self.autorizar()
        self.pedir()
        return self.conferir(self.caixa.ultimo_codigo).token

    def test_token_inventado_nao_abre_sessao(self):
        self.entrar()
        self.assertIsNone(identidade.de_sessao(self.conn, "token-chutado", agora=AGORA))
        self.assertIsNone(identidade.de_sessao(self.conn, "", agora=AGORA))

    def test_sessao_expira(self):
        token = self.entrar()
        tarde = mais(dias=identidade.VALIDADE_SESSAO_DIAS + 1)
        self.assertIsNone(identidade.de_sessao(self.conn, token, agora=tarde))

    def test_sessao_vencida_sai_do_banco_quando_aparece(self):
        token = self.entrar()
        tarde = mais(dias=identidade.VALIDADE_SESSAO_DIAS + 1)
        identidade.de_sessao(self.conn, token, agora=tarde)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM sessao").fetchone()["n"], 0
        )

    def test_encerrar_derruba_so_aquele_aparelho(self):
        """Sair no celular nao pode desconectar o mesmo cadastro no computador."""
        celular = self.entrar()
        self.pedir(agora=mais(segundos=61))
        computador = self.conferir(self.caixa.ultimo_codigo, agora=mais(segundos=61)).token

        self.assertTrue(identidade.encerrar(self.conn, celular))
        self.assertIsNone(identidade.de_sessao(self.conn, celular, agora=AGORA))
        self.assertIsNotNone(identidade.de_sessao(self.conn, computador, agora=mais(segundos=61)))

    def test_limpar_apaga_o_que_venceu_e_poupa_o_que_vale(self):
        vencido = self.entrar()
        self.pedir(agora=mais(segundos=61))
        valido_em = mais(dias=identidade.VALIDADE_SESSAO_DIAS)
        # Uma sessao nova, aberta bem depois, ainda dentro do prazo dela.
        self.pedir(agora=valido_em)
        novo = self.conferir(self.caixa.ultimo_codigo, agora=valido_em).token

        resumo = identidade.limpar(self.conn, agora=mais(dias=identidade.VALIDADE_SESSAO_DIAS + 1))
        self.assertEqual(resumo["sessoes"], 1)
        self.assertIsNone(identidade.de_sessao(self.conn, vencido, agora=valido_em))
        self.assertIsNotNone(identidade.de_sessao(self.conn, novo, agora=valido_em))


if __name__ == "__main__":
    unittest.main()
