"""A porta HTTP: a operacao publica o cardapio, o app do funcionario o recebe.

Ate aqui o app web era um retrato: `dados.json` gerado por um script e publicado
junto com o site. Publicar um cardapio novo nao chegava a ninguem.

Estas duas rotas fecham o circuito:

    POST /api/cardapio   a operacao manda o arquivo; o importador de verdade roda
    GET  /api/dia        o app pergunta o cardapio de hoje da unidade dele

Roda no processo do bot, com o mesmo banco. Nao e um sistema novo — e uma porta
para o que ja existe, e por isso a publicacao pelo Telegram e a publicacao pela
web chegam no mesmo lugar, pelo mesmo caminho, com as mesmas validacoes.

Tornado porque ele ja vem com o `python-telegram-bot[webhooks]`: uma dependencia
a menos para auditar num projeto que lida com alergia alimentar.

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

from tornado.web import Application, HTTPError, RequestHandler

from .catalog import import_menu_rows
from .csv_import import read_rows
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


class Saude(Base):
    def get(self) -> None:
        self.responder({"ok": True, "dia": hoje(), "publicacao": bool(self.token)})


def criar_app(abrir, token: str = "") -> Application:
    """As rotas, com a funcao que abre o banco injetada.

    Injetada e nao importada para o teste poder apontar para um banco
    descartavel — e para este modulo nao decidir onde o banco mora.
    """
    if not token:
        logger.warning(
            "APETIT_PUBLICAR_TOKEN vazio: a rota de publicar fica recusando tudo. "
            "Publicar pelo Telegram continua funcionando."
        )
    contexto = {"abrir": abrir, "token": token}
    return Application([
        (r"/api/saude", Saude, contexto),
        (r"/api/dia", Dia, contexto),
        (r"/api/cardapio", Cardapio, contexto),
    ])


def abrir_padrao(caminho: str):
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    """Sobe so a API, sem o bot. Serve para desenvolver e para conferir."""
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    caminho = os.getenv("APETIT_DB_PATH", "apetit.db")
    porta = int(os.getenv("PORT", "8000"))
    app = criar_app(lambda: abrir_padrao(caminho), os.getenv("APETIT_PUBLICAR_TOKEN", ""))
    app.listen(porta)
    logger.info("API do Apetit na porta %s, banco em %s", porta, caminho)
    asyncio.get_event_loop().run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
