"""A porta HTTP: a operacao publica o cardapio, o app do funcionario o recebe.

Ate aqui o app era um retrato: `dados.json` gerado por um script e publicado
junto com o site. Publicar um cardapio novo nao chegava a ninguem.

As rotas:

    POST /api/cardapio   a operacao manda o arquivo; o importador de verdade roda
    GET  /api/dia        o app pergunta o cardapio de hoje da unidade dele
    POST /api/entrar     manda o codigo de seis digitos para o e-mail
    POST /api/codigo     confere o codigo e abre a sessao
    GET  /api/eu         de quem e esta sessao
    POST /api/sair       encerra a sessao deste aparelho
    GET  /api/gestao/feedback  o painel da Apetit, so para sessao de gestao

Nao e um sistema novo: e uma porta para o que ja existe em `apetit/`, com as
mesmas validacoes que sempre valeram na importacao de cardapio.

Tornado porque era o servidor que o projeto ja carregava; com o Telegram fora,
ele ficou como dependencia propria — uma so, e pequena, num projeto que lida
com alergia alimentar e onde cada dependencia e mais codigo para auditar.

**Quem le nao se identifica.** O cardapio do dia e igual para todo mundo da
unidade, entao `GET /api/dia` e publico. Alergia, objetivo e historico continuam
no aparelho de quem usa o app, e o servidor segue sem saber o que cada pessoa
nao pode comer. Essa e a promessa que o termo faz, e ela nao muda aqui.

**Quem publica se identifica.** Publicar cardapio e a acao de maior privilegio
do sistema: quem publica define o que quinze pessoas leem sobre alergenico. A
rota exige um token; sem `APETIT_PUBLICAR_TOKEN` no ambiente, ela nao existe —
melhor uma porta ausente que uma porta destrancada.
"""

import hmac
import json
import logging
import os
import sqlite3
from datetime import UTC, datetime

from tornado.ioloop import IOLoop
from tornado.web import Application, HTTPError, RequestHandler

from . import feedback, identidade
from .catalog import connect, import_menu_rows, init_schema
from .csv_import import read_rows
from .entrega_email import montar as montar_remetente
from .payload import do_dia

logger = logging.getLogger(__name__)

# Cardapio da semana tem poucos KB. O limite existe para um upload errado nao
# virar consumo de memoria do processo que tambem atende o bot.
MAX_ARQUIVO = 2 * 1024 * 1024

REFEICOES = {"almoco", "jantar", "lanche"}


def hoje() -> str:
    return datetime.now(UTC).date().isoformat()


class Base(RequestHandler):
    def initialize(self, abrir, token: str = "") -> None:
        self.abrir = abrir
        self.token = token

    def set_default_headers(self) -> None:
        # O app e servido de outro endereco (GitHub Pages) e fala com este.
        # Sem identificacao para enviar, nao ha cookie nem credencial em jogo.
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Content-Type", "application/json; charset=utf-8")

    def options(self, *args) -> None:
        self.set_status(204)
        self.finish()

    def responder(self, corpo: dict, status: int = 200) -> None:
        self.set_status(status)
        self.finish(json.dumps(corpo, ensure_ascii=False))

    def corpo_json(self) -> dict:
        """O corpo como JSON, e `{}` quando nao e JSON.

        Dicionario vazio e nao erro 400 porque quem valida e a rota: ela sabe
        qual campo falta e da um recado que diz o que fazer, em vez de "JSON
        invalido".
        """
        try:
            dados = json.loads(self.request.body or b"{}")
        except ValueError:
            return {}
        return dados if isinstance(dados, dict) else {}

    def portador(self) -> str:
        enviado = self.request.headers.get("Authorization", "")
        return enviado[7:].strip() if enviado.lower().startswith("bearer ") else ""

    def write_error(self, status_code: int, **kwargs) -> None:
        motivo = "Nao foi possivel atender."
        erro = kwargs.get("exc_info")
        if erro and isinstance(erro[1], HTTPError) and erro[1].log_message:
            motivo = erro[1].log_message
        self.set_status(status_code)
        self.finish(json.dumps({"erro": motivo}, ensure_ascii=False))


class Dia(Base):
    """O cardapio do dia de uma unidade, como o app o consome.

    Publico de proposito: nao ha nada aqui sobre pessoa nenhuma.
    """

    def get(self) -> None:
        unidade = self.get_argument("unidade", "").strip()
        if not unidade:
            raise HTTPError(400, log_message="Informe a unidade do refeitorio.")
        refeicao = self.get_argument("refeicao", "almoco").strip()
        if refeicao not in REFEICOES:
            raise HTTPError(400, log_message=f"Refeicao desconhecida: {refeicao}.")
        dia = self.get_argument("dia", "").strip() or hoje()

        conn = self.abrir()
        try:
            corpo = do_dia(conn, unidade, dia, refeicao)
        except ValueError as erro:
            # Cardapio grande demais para a montagem offline. E uma limitacao
            # conhecida, nao um defeito: o app precisa saber para nao mostrar
            # o montador prometendo o que nao tem.
            logger.warning("payload do dia %s/%s: %s", unidade, dia, erro)
            corpo = do_dia_sem_montagem(conn, unidade, dia, refeicao, str(erro))
        finally:
            conn.close()

        if not corpo["cardapio"]:
            self.set_header("Cache-Control", "no-store")
            self.responder({**corpo, "vazio": True}, status=404)
            return
        # Cardapio muda uma vez por semana, mas uma republicacao precisa chegar
        # no mesmo dia: revalidar e barato e mantem o app honesto.
        self.set_header("Cache-Control", "no-cache")
        self.responder(corpo)


def do_dia_sem_montagem(conn, unidade: str, dia: str, refeicao: str, motivo: str) -> dict:
    from .payload import avaliacao, cardapio, combinacoes, opcoes_montagem
    from .allergens import ALLERGENS
    from .humanize import friendly_date
    from .profile import TARGETS

    return {
        "dia": dia, "dia_amigavel": friendly_date(dia),
        "unidade": unidade, "refeicao": refeicao,
        "cardapio": cardapio(conn, unidade, dia, refeicao),
        "alergenicos": [{"codigo": c, "nome": n} for c, n in ALLERGENS.items()],
        "objetivos": [{"nome": n, "alvo": a} for n, a in TARGETS.items()],
        "combinacoes": combinacoes(conn, unidade, dia, refeicao),
        "montagens": [],
        "opcoes_montagem": opcoes_montagem(conn, unidade, dia, refeicao),
        "avaliacao": avaliacao(),
        "montagem_indisponivel": motivo,
    }


class Cardapio(Base):
    """Recebe o arquivo da operacao e publica, com as mesmas regras do bot."""

    def prepare(self) -> None:
        if self.request.method == "OPTIONS":
            return
        enviado = self.request.headers.get("Authorization", "")
        if enviado.lower().startswith("bearer "):
            enviado = enviado[7:]
        # `compare_digest` para a resposta nao contar, pelo tempo, quantos
        # caracteres do token estavam certos.
        if not self.token or not hmac.compare_digest(enviado, self.token):
            raise HTTPError(401, log_message="Token de publicacao ausente ou invalido.")

    def post(self) -> None:
        corpo = self.request.body or b""
        if len(corpo) > MAX_ARQUIVO:
            raise HTTPError(413, log_message="Arquivo grande demais para um cardapio.")
        if not corpo.strip():
            raise HTTPError(400, log_message="Nao veio arquivo nenhum.")

        unidade = self.get_argument("unidade", "").strip()
        if not unidade:
            # Sem unidade o cardapio publica para ninguem, e some sem erro
            # nenhum — a pior forma de falhar.
            raise HTTPError(400, log_message="Informe a unidade do refeitorio.")
        refeicao = self.get_argument("refeicao", "almoco").strip()
        if refeicao not in REFEICOES:
            raise HTTPError(400, log_message=f"Refeicao desconhecida: {refeicao}.")
        mes = self.inteiro("mes", 1, 12)
        ano = self.inteiro("ano", 2020, 2100)

        try:
            texto = corpo.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Export do Windows costuma vir em latin-1.
            texto = corpo.decode("latin-1", errors="replace")

        conn = self.abrir()
        try:
            resultado = import_menu_rows(
                conn, read_rows(texto), unit=unidade, meal=refeicao, month=mes, year=ano,
            )
        except Exception:
            conn.rollback()
            logger.exception("falha ao importar cardapio de %s", unidade)
            raise HTTPError(400, log_message="Nao consegui ler esse arquivo como cardapio.")
        finally:
            conn.close()

        dias = sorted({e.service_date for e in resultado.published})
        logger.info("cardapio de %s: %s itens em %s dia(s)",
                    unidade, len(resultado.published), len(dias))
        self.responder({
            "publicados": len(resultado.published),
            "bloqueados": len(resultado.blocked),
            "dias": dias,
            "resumo": resultado.summary(),
            "achados": [
                {"bloqueio": i.blocking, "codigo": i.code,
                 "item": i.item_name or i.category or "", "detalhe": i.detail,
                 "vezes": vezes, "primeira": datas[0] if datas else ""}
                for i, vezes, datas in resultado.grouped_issues()
            ],
        })

    def inteiro(self, nome: str, minimo: int, maximo: int) -> int | None:
        bruto = self.get_argument(nome, "").strip()
        if not bruto:
            return None
        try:
            valor = int(bruto)
        except ValueError:
            raise HTTPError(400, log_message=f"{nome} precisa ser um numero.") from None
        if not minimo <= valor <= maximo:
            raise HTTPError(400, log_message=f"{nome} fora da faixa ({minimo}-{maximo}).")
        return valor


# --------------------------------------------------------------------------
# Entrar: e-mail, codigo, sessao
# --------------------------------------------------------------------------

# A mesma resposta para e-mail da lista, e-mail de fora e pedido repetido. O
# texto e no condicional ("se esse e-mail estiver na lista") porque a resposta
# nao pode confirmar que esta — isso responderia "quem trabalha ai?" a quem so
# tinha uma lista de enderecos para chutar.
RESPOSTA_ENTRAR = {
    "pedido": True,
    "recado": "Se esse e-mail estiver na lista do refeitorio, o codigo chegou na caixa de entrada.",
}


class Entrar(Base):
    """Recebe o e-mail e manda o codigo, sem dizer se o e-mail existe.

    **O que esta rota nao resolve:** o tempo. Endereco de fora da lista volta em
    microssegundos; endereco de dentro espera o SMTP. Quem medir o tempo das
    duas respostas consegue distinguir. Para um piloto de quinze pessoas numa
    empresa conhecida, o endereco ja e adivinhavel pelo padrao da casa
    (nome.sobrenome@empresa), e esconder isso custaria fila de entrega e um
    caminho a mais para errar. Fica registrado como limitacao conhecida, e nao
    como problema resolvido.
    """

    def initialize(self, abrir, token: str = "", segredo: str = "", enviar=None) -> None:
        super().initialize(abrir, token)
        self.segredo = segredo
        self.enviar = enviar

    async def post(self) -> None:
        if not self.segredo or not self.enviar:
            raise HTTPError(503, log_message="Entrada por codigo nao esta configurada.")
        email = self.corpo_json().get("email", "")
        try:
            limpo = identidade.normalizar(email)
        except ValueError:
            # E-mail sem formato de e-mail nao e informacao sobre a lista: pode
            # dizer que esta errado, porque a resposta nao revela quem entra.
            raise HTTPError(400, log_message="Esse e-mail nao parece um e-mail.") from None

        # Numa thread: `smtplib` e bloqueante, e travar o IOLoop faria o
        # cardapio de todo mundo esperar o servidor de e-mail de um.
        #
        # O banco abre *dentro* da thread: uma conexao de SQLite pertence a
        # thread que a criou, e passar a de fora estoura com "created in a
        # thread can only be used in that same thread" — que aqui apareceria
        # como "pedi o codigo e nao chegou", sem erro visivel na resposta.
        def trabalho() -> identidade.Pedido:
            conn = self.abrir()
            try:
                return identidade.pedir_codigo(conn, limpo, self.segredo, self.enviar)
            finally:
                conn.close()

        try:
            pedido = await IOLoop.current().run_in_executor(None, trabalho)
        except Exception:
            logger.exception("falha entregando codigo para %s", limpo)
            # Mesma resposta: "o e-mail falhou" tambem contaria que o endereco
            # esta na lista. O erro fica no log do servidor, onde e util.
            self.responder(RESPOSTA_ENTRAR)
            return

        logger.info("pedido de codigo para %s: entregue=%s %s",
                    limpo, pedido.entregue, pedido.motivo)
        self.responder(RESPOSTA_ENTRAR)


class Codigo(Base):
    """Confere o codigo e devolve o token da sessao."""

    def initialize(self, abrir, token: str = "", segredo: str = "", enviar=None) -> None:
        super().initialize(abrir, token)
        self.segredo = segredo

    def post(self) -> None:
        if not self.segredo:
            raise HTTPError(503, log_message="Entrada por codigo nao esta configurada.")
        dados = self.corpo_json()
        try:
            limpo = identidade.normalizar(dados.get("email", ""))
        except ValueError:
            raise HTTPError(400, log_message="Esse e-mail nao parece um e-mail.") from None

        conn = self.abrir()
        try:
            entrada = identidade.conferir_codigo(
                conn, limpo, str(dados.get("codigo", "")), self.segredo,
            )
            unidade = ""
            if entrada.ok:
                linha = conn.execute(
                    "SELECT apetit_unit FROM pessoa_email WHERE email = ?", (limpo,)
                ).fetchone()
                unidade = linha["apetit_unit"] if linha else ""
        finally:
            conn.close()

        if not entrada.ok:
            # Um recado so para todos os motivos: dizer "expirou" e nao "errado"
            # ajudaria quem esta chutando a saber se ainda vale a pena.
            logger.info("codigo recusado para %s: %s", limpo, entrada.motivo)
            raise HTTPError(401, log_message="Codigo invalido ou expirado. Peca outro.")

        self.responder({
            "token": entrada.token,
            "pessoa_id": entrada.pessoa_id,
            "email": entrada.email,
            "unidade": unidade,
            "dias": identidade.VALIDADE_SESSAO_DIAS,
        })


class Eu(Base):
    """Quem e o dono deste token. Serve para o app saber se a sessao vale ainda."""

    def get(self) -> None:
        conn = self.abrir()
        try:
            dono = identidade.de_sessao(conn, self.portador())
            if not dono:
                raise HTTPError(401, log_message="Sessao expirada. Entre de novo.")
            linha = conn.execute(
                "SELECT apetit_unit FROM pessoa_email WHERE email = ?", (dono["email"],)
            ).fetchone()
            self.responder({
                "pessoa_id": dono["pessoa_id"],
                "email": dono["email"],
                "unidade": linha["apetit_unit"] if linha else "",
                "expira_em": dono["expira_em"],
            })
        finally:
            conn.close()


class Sair(Base):
    """Encerra a sessao deste aparelho, e so dele."""

    def post(self) -> None:
        conn = self.abrir()
        try:
            identidade.encerrar(conn, self.portador())
        finally:
            conn.close()
        # Sempre 200: "esse token nao existia" nao muda nada para quem saiu, e
        # responder diferente contaria se o token era valido.
        self.responder({"saiu": True})


class GestaoFeedback(Base):
    """O painel da Apetit: como cada empresa cliente avaliou o servico.

    Esta e a primeira rota do projeto com controle de acesso de verdade, e
    precisa ser: ate aqui a gestao morava atras de uma chave no proprio
    aparelho, lendo numeros de demonstracao de um arquivo publico. Com avaliacao
    real de Copel, Sanepar e Coca-Cola do outro lado, a mesma porta seria
    entregar a insatisfacao de um cliente para qualquer um que soubesse o
    endereco — inclusive para o concorrente dele.

    O que sai daqui ja vem suprimido pelo `apetit/feedback.py`: recorte pequeno
    nao tem numero, e comentario so aparece com volume. A rota nao repete essa
    regra, e nao pode repetir — quem decide isso e um lugar so.
    """

    def get(self) -> None:
        desde = self.get_argument("desde", "").strip()
        ate = self.get_argument("ate", "").strip()
        semanas = min(max(int(self.get_argument("semanas", "8") or 8), 1), 26)

        conn = self.abrir()
        try:
            dono = identidade.de_sessao(conn, self.portador())
            if not dono:
                raise HTTPError(401, log_message="Sessao expirada. Entre de novo.")
            if dono["papel"] != identidade.PAPEL_GESTAO:
                # 403 e nao 404: quem esta autenticado e nao pode ver merece
                # saber que a porta existe e nao e dele. Esconder aqui so faria
                # alguem da Apetit achar que o painel esta quebrado.
                raise HTTPError(403, log_message="Este painel e da gestao da Apetit.")

            empresas = []
            for relatorio in feedback.all_company_reports(conn, desde, ate):
                empresas.append({
                    **_recorte_json(relatorio),
                    "motivos": [
                        {"codigo": tag, "rotulo": feedback.MISSING_TAGS.get(tag, tag), "vezes": n}
                        for tag, n in relatorio.tags
                    ],
                    "comentarios": feedback.company_comments(conn, relatorio.name, desde, ate),
                    "semanas": [
                        _recorte_json(semana)
                        for semana in feedback.company_trend(conn, relatorio.name, weeks=semanas)
                    ],
                })
            corpo = {
                "periodo": {"desde": desde, "ate": ate},
                "n_minimo": feedback.MIN_RATINGS,
                "escala": feedback.SCALE,
                "empresas": empresas,
                "refeitorios": [
                    _recorte_json(r) for r in feedback.all_unit_reports(conn, desde, ate)
                ],
            }
        finally:
            conn.close()
        self.responder(corpo)


def _recorte_json(relatorio) -> dict:
    """Um `ServiceReport` como JSON.

    Recorte suprimido sai **sem os numeros**, e nao com os numeros escondidos:
    mandar o valor e confiar que nenhuma tela, hoje ou daqui a um ano, vai
    imprimi-lo.
    """
    if relatorio.suppressed:
        return {
            "nome": relatorio.name,
            "avaliacoes": relatorio.total,
            "suprimido": True,
            "motivo": relatorio.reason,
            "semana": relatorio.period_start,
        }
    return {
        "nome": relatorio.name,
        "avaliacoes": relatorio.total,
        "suprimido": False,
        "motivo": "",
        "semana": relatorio.period_start,
        "comida_boa_pct": relatorio.food_good_pct,
        "atendimento_bom_pct": relatorio.service_good_pct,
        "comida_media": relatorio.food_avg,
        "atendimento_media": relatorio.service_avg,
        "faltou_algo": relatorio.missing_count,
        "faltou_pct": relatorio.missing_pct,
    }


class Saude(Base):
    def get(self) -> None:
        self.responder({"ok": True, "dia": hoje(), "publicacao": bool(self.token)})


def criar_app(abrir, token: str = "", segredo: str = "", enviar=None) -> Application:
    """As rotas, com a funcao que abre o banco injetada.

    Injetada e nao importada para o teste poder apontar para um banco
    descartavel — e para este modulo nao decidir onde o banco mora.

    `enviar` tambem e injetado: assim o teste exercita a entrada por codigo
    inteira sem mandar um e-mail de verdade, e este modulo nao precisa saber o
    que e SMTP.
    """
    if not token:
        logger.warning(
            "APETIT_PUBLICAR_TOKEN vazio: a rota de publicar fica recusando tudo. "
            "Publicar continua possivel por scripts/import_cardapio.py, no servidor."
        )
    if not segredo:
        logger.warning(
            "APETIT_SEGREDO vazio: a entrada por codigo fica indisponivel. "
            "Sem ele, um banco vazado entregaria as contas — melhor a porta "
            "ausente que destrancada."
        )
    if segredo and not enviar:
        logger.warning(
            "Sem SMTP configurado (APETIT_SMTP_HOST/APETIT_SMTP_FROM): o codigo "
            "nao tem como sair, e a rota de entrar responde indisponivel."
        )
    contexto = {"abrir": abrir, "token": token}
    entrada = {**contexto, "segredo": segredo, "enviar": enviar}
    return Application([
        (r"/api/saude", Saude, contexto),
        (r"/api/dia", Dia, contexto),
        (r"/api/cardapio", Cardapio, contexto),
        (r"/api/entrar", Entrar, entrada),
        (r"/api/codigo", Codigo, entrada),
        (r"/api/eu", Eu, contexto),
        (r"/api/sair", Sair, contexto),
        (r"/api/gestao/feedback", GestaoFeedback, contexto),
    ])


def abrir_padrao(caminho: str):
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    return conn


def preparar(caminho: str) -> None:
    """Cria as tabelas se o banco ainda nao as tem.

    Quem fazia isso ao subir era o `bot.py`, e a API pegava carona. Apagado o
    bot, um servidor novo atendia o primeiro `GET /api/dia` com 500 e
    `no such table: menu_entry` — nao com "nao ha cardapio publicado".

    O defeito nao aparecia em teste porque todo teste cria o esquema no setUp.
    Apareceu ao rodar o comando que o README manda rodar.
    """
    conn = connect(caminho)
    try:
        init_schema(conn)
    finally:
        conn.close()


def main() -> int:
    """Sobe so a API, sem o bot. Serve para desenvolver e para conferir."""
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    caminho = os.getenv("APETIT_DB_PATH", "apetit.db")
    porta = int(os.getenv("PORT", "8000"))
    preparar(caminho)
    app = criar_app(
        lambda: abrir_padrao(caminho),
        os.getenv("APETIT_PUBLICAR_TOKEN", ""),
        segredo=os.getenv("APETIT_SEGREDO", ""),
        enviar=montar_remetente(identidade.VALIDADE_CODIGO_MIN),
    )
    app.listen(porta)
    logger.info("API do Apetit na porta %s, banco em %s", porta, caminho)
    asyncio.get_event_loop().run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
