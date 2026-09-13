"""Quem esta pedindo: e-mail da empresa e um codigo de seis digitos.

Ate aqui quem identificava a pessoa era o Telegram. Saindo o Telegram, sai
tambem a unica coisa que dizia "este prato e da Joana e nao do Rafael" — e num
app de alergia alimentar trocar duas pessoas nao e um bug de cadastro, e servir
a restricao de um para o outro.

E-mail e codigo, e nao senha, por tres razoes:

* No piloto a lista de quem entra e fechada e conhecida — a empresa sabe os
  quinze e-mails. Um codigo enviado para a caixa de entrada prova o acesso a ela
  sem criar mais uma senha para a pessoa esquecer.
* Senha esquecida cai no "recuperar por e-mail" de qualquer jeito. Entao o
  e-mail ja e a autoridade real; a senha seria uma camada a mais para vazar.
* Ninguem guarda senha aqui. O que se guarda e o *hash* do codigo e o *hash* do
  token de sessao, nunca o valor.

**O que muda e o que nao muda na privacidade.** O e-mail e dado pessoal, e
passa a ficar no servidor: isso e novo, e o termo precisa dizer. O que a pessoa
nao pode comer continua no aparelho dela, e continua nao subindo — `GET
/api/dia` segue publico e impessoal, porque o cardapio da unidade e igual para
todo mundo. Identificar quem pergunta nao virou desculpa para o servidor
comecar a saber de alergia.

**O pepper (`segredo`) nao e enfeite.** Seis digitos sao um milhao de
possibilidades: um banco que vaze com o hash puro do codigo se quebra em
segundos num laptop. Com o codigo somado a um segredo que mora fora do banco,
o vazamento do banco sozinho nao da entrada em conta nenhuma. Por isso o
segredo e obrigatorio e vem de fora — sem ele a porta nao existe, em vez de
existir destrancada.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# Dez minutos: tempo de abrir o e-mail no celular, e nao tempo de deixar um
# codigo valido dormindo na caixa de entrada.
VALIDADE_CODIGO_MIN = 10

# Cinco tentativas e o codigo morre. E o que torna seis digitos suficiente: sem
# limite, um milhao de possibilidades cai em minutos de requisicao automatica;
# com limite, cada codigo aceita cinco chutes em dez minutos.
TENTATIVAS_MAX = 5

# Um minuto entre pedidos, para o botao "nao recebi" nao virar uma forma de
# encher a caixa de entrada de alguem.
ESPERA_ENTRE_PEDIDOS_S = 60

# Trinta dias no aparelho. E um refeitorio: pedir codigo toda semana faria a
# pessoa desistir do app antes de usar, e o token nao abre nada alem do proprio
# historico dela.
VALIDADE_SESSAO_DIAS = 30

TAMANHO_CODIGO = 6

# Caixa postal de tamanho sensato. O limite existe para um campo de texto aberto
# na internet nao virar linha de banco de dados de qualquer tamanho.
MAX_EMAIL = 254

# Deliberadamente permissivo no que aceita antes do @, restritivo no formato
# geral: validar e-mail por regex ao pe da letra e um poco sem fundo, e recusar
# um endereco valido de funcionario e pior que aceitar um invalido — o invalido
# simplesmente nunca recebe codigo.
FORMATO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCHEMA = """
-- Quem pode entrar. Lista fechada: no piloto e a empresa que diz quem sao os
-- quinze, e nao o formulario que decide. Sem linha aqui, nenhum codigo sai.
CREATE TABLE IF NOT EXISTS pessoa_email (
    email TEXT PRIMARY KEY,
    pessoa_id INTEGER NOT NULL UNIQUE,
    apetit_unit TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL
);

-- O codigo em transito, guardado como hash com pepper. Uma linha por e-mail:
-- pedir de novo substitui o anterior, entao nunca ha dois codigos validos para
-- a mesma pessoa.
CREATE TABLE IF NOT EXISTS codigo_entrada (
    email TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    tentativas INTEGER NOT NULL DEFAULT 0,
    pedido_em TEXT NOT NULL
);

-- A sessao do aparelho. Guarda o hash do token, e nao o token: um banco que
-- vaze nao entrega a sessao de ninguem.
CREATE TABLE IF NOT EXISTS sessao (
    token_hash TEXT PRIMARY KEY,
    pessoa_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    criada_em TEXT NOT NULL,
    expira_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessao_pessoa ON sessao (pessoa_id);
"""


def criar_tabelas(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def agora_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _quando(agora: str | None) -> datetime:
    return datetime.fromisoformat(agora) if agora else datetime.now(UTC)


def normalizar(email: str) -> str:
    """Minusculas e sem espaco em volta — e nada alem disso.

    Nao mexe em ponto nem em `+`: isso e regra de um provedor especifico, e
    aplicada a e-mail corporativo juntaria `ana.silva@` e `anasilva@`, que numa
    empresa podem ser duas pessoas. Juntar duas pessoas aqui significa mostrar a
    restricao de uma para a outra.
    """
    limpo = (email or "").strip().lower()
    if not limpo or len(limpo) > MAX_EMAIL or not FORMATO.match(limpo):
        raise ValueError("e-mail invalido")
    return limpo


def _hash_codigo(email: str, codigo: str, segredo: str) -> str:
    """HMAC do codigo com o segredo do servidor, preso ao e-mail.

    Preso ao e-mail de proposito: sem isso, o hash de "123456" seria igual para
    todo mundo, e um vazamento mostraria quem tem o mesmo codigo que quem.
    """
    if not segredo:
        raise ValueError("segredo do servidor ausente")
    return hmac.new(segredo.encode(), f"{email}\x00{codigo}".encode(), hashlib.sha256).hexdigest()


def _hash_token(token: str) -> str:
    """Token de sessao e aleatorio de 32 bytes, entao sha256 basta.

    Nao leva pepper porque nao ha o que adivinhar: nao existe dicionario de
    tokens de 256 bits para um vazamento testar.
    """
    return hashlib.sha256(token.encode()).hexdigest()


# --------------------------------------------------------------------------
# Quem pode entrar
# --------------------------------------------------------------------------

def autorizar(conn: sqlite3.Connection, email: str, unidade: str = "") -> int:
    """Poe um e-mail na lista e devolve o id da pessoa. Chamar de novo nao duplica.

    O id nao e sequencial a partir de 1: ele nasce acima de tudo que o banco ja
    usa, inclusive dos `telegram_id` antigos. Sequencial simples poderia cair em
    cima de um id de Telegram baixo e emendar o historico de duas pessoas — o
    tipo de defeito que aparece como "meu app diz que eu nao posso comer ovo"
    seis meses depois. Aqui a colisao e impossivel por construcao, e nao
    improvavel.
    """
    limpo = normalizar(email)
    ja = conn.execute("SELECT pessoa_id FROM pessoa_email WHERE email = ?", (limpo,)).fetchone()
    if ja:
        if unidade:
            conn.execute("UPDATE pessoa_email SET apetit_unit = ? WHERE email = ?", (unidade, limpo))
            conn.commit()
        return int(ja["pessoa_id"])

    usados = [0]
    for tabela, coluna in (("pessoa_email", "pessoa_id"), ("employee", "telegram_id")):
        existe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabela,)
        ).fetchone()
        if existe:
            maior = conn.execute(f"SELECT MAX({coluna}) AS m FROM {tabela}").fetchone()
            if maior and maior["m"] is not None:
                usados.append(int(maior["m"]))
    novo = max(usados) + 1
    conn.execute(
        "INSERT INTO pessoa_email (email, pessoa_id, apetit_unit, criado_em) VALUES (?, ?, ?, ?)",
        (limpo, novo, unidade, agora_iso()),
    )
    conn.commit()
    return novo


def autorizados(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT email, pessoa_id, apetit_unit, criado_em FROM pessoa_email ORDER BY email"
    ).fetchall()


def revogar(conn: sqlite3.Connection, email: str) -> bool:
    """Tira da lista e derruba as sessoes abertas.

    Derrubar as sessoes e o ponto: tirar da lista sem isso deixaria a pessoa
    dentro do app por trinta dias depois de perder o acesso.
    """
    limpo = normalizar(email)
    cursor = conn.execute("DELETE FROM pessoa_email WHERE email = ?", (limpo,))
    conn.execute("DELETE FROM sessao WHERE email = ?", (limpo,))
    conn.execute("DELETE FROM codigo_entrada WHERE email = ?", (limpo,))
    conn.commit()
    return cursor.rowcount > 0


def pessoa_id_de(conn: sqlite3.Connection, email: str) -> int | None:
    linha = conn.execute(
        "SELECT pessoa_id FROM pessoa_email WHERE email = ?", (normalizar(email),)
    ).fetchone()
    return int(linha["pessoa_id"]) if linha else None


# --------------------------------------------------------------------------
# Pedir o codigo
# --------------------------------------------------------------------------

@dataclass
class Pedido:
    """O que aconteceu com o pedido — para o servidor, nao para a resposta.

    `entregue` e `espere_segundos` contam se o e-mail esta na lista e se houve
    pedido recente. Isso responde "essa pessoa trabalha aqui?" a quem so
    precisava chutar enderecos, entao a rota HTTP tem de responder igual nos
    dois casos. O detalhe fica aqui dentro, para decisao e para log.
    """

    entregue: bool
    espere_segundos: int = 0
    motivo: str = ""


def pedir_codigo(conn: sqlite3.Connection, email: str, segredo: str, enviar,
                 agora: str | None = None) -> Pedido:
    """Gera o codigo, guarda o hash e entrega o codigo a quem envia.

    `enviar` e injetado: este modulo nao sabe mandar e-mail, e nao deve. O valor
    em claro passa por aqui uma vez, vai para a funcao de entrega e nao e
    guardado nem devolvido — quem chama nao tem como registrar em log por
    descuido o que nao recebe.

    Se a entrega falhar, o codigo e apagado. Deixar um hash valido de um codigo
    que ninguem recebeu so serviria para a pessoa tentar cinco vezes um numero
    que nao existe.
    """
    limpo = normalizar(email)
    quando = _quando(agora)

    alvo = conn.execute(
        "SELECT pessoa_id, apetit_unit FROM pessoa_email WHERE email = ?", (limpo,)
    ).fetchone()
    if not alvo:
        # Sem linha na lista, nada e enviado e nada e gravado. Nao gravar e
        # deliberado: a rota e aberta, e guardar cada endereco chutado deixaria
        # qualquer um escrever no banco.
        return Pedido(entregue=False, motivo="fora da lista")

    anterior = conn.execute(
        "SELECT pedido_em FROM codigo_entrada WHERE email = ?", (limpo,)
    ).fetchone()
    if anterior:
        passou = (quando - datetime.fromisoformat(anterior["pedido_em"])).total_seconds()
        if passou < ESPERA_ENTRE_PEDIDOS_S:
            return Pedido(
                entregue=False,
                espere_segundos=int(ESPERA_ENTRE_PEDIDOS_S - passou),
                motivo="pedido recente",
            )

    codigo = f"{secrets.randbelow(10 ** TAMANHO_CODIGO):0{TAMANHO_CODIGO}d}"
    expira = (quando + timedelta(minutes=VALIDADE_CODIGO_MIN)).isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO codigo_entrada (email, hash, expira_em, tentativas, pedido_em)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(email) DO UPDATE SET
            hash = excluded.hash, expira_em = excluded.expira_em,
            tentativas = 0, pedido_em = excluded.pedido_em
        """,
        (limpo, _hash_codigo(limpo, codigo, segredo), expira,
         quando.isoformat(timespec="seconds")),
    )
    conn.commit()

    try:
        enviar(limpo, codigo)
    except Exception:
        conn.execute("DELETE FROM codigo_entrada WHERE email = ?", (limpo,))
        conn.commit()
        raise
    return Pedido(entregue=True)


# --------------------------------------------------------------------------
# Conferir o codigo e abrir a sessao
# --------------------------------------------------------------------------

@dataclass
class Entrada:
    """O resultado de conferir um codigo. `token` so vem quando deu certo."""

    ok: bool
    token: str = ""
    pessoa_id: int = 0
    email: str = ""
    motivo: str = ""


def conferir_codigo(conn: sqlite3.Connection, email: str, codigo: str, segredo: str,
                    agora: str | None = None) -> Entrada:
    """Confere o codigo e, acertando, abre a sessao.

    O codigo e consumido no acerto: um codigo que continuasse valendo depois de
    usado viraria uma segunda chave dormindo na caixa de entrada.

    Cada erro conta, e no limite o codigo morre — e a pessoa pede outro. Sem
    isso, seis digitos seriam so um milhao de requisicoes.
    """
    limpo = normalizar(email)
    quando = _quando(agora)
    digitos = (codigo or "").strip()

    linha = conn.execute(
        "SELECT hash, expira_em, tentativas FROM codigo_entrada WHERE email = ?", (limpo,)
    ).fetchone()
    if not linha:
        return Entrada(ok=False, motivo="sem codigo pendente")

    if quando > datetime.fromisoformat(linha["expira_em"]):
        conn.execute("DELETE FROM codigo_entrada WHERE email = ?", (limpo,))
        conn.commit()
        return Entrada(ok=False, motivo="codigo expirado")

    esperado = _hash_codigo(limpo, digitos, segredo)
    # `compare_digest` para a resposta nao contar, pelo tempo, quantos digitos
    # estavam certos.
    if not hmac.compare_digest(esperado, linha["hash"]):
        tentativas = int(linha["tentativas"]) + 1
        if tentativas >= TENTATIVAS_MAX:
            conn.execute("DELETE FROM codigo_entrada WHERE email = ?", (limpo,))
            conn.commit()
            return Entrada(ok=False, motivo="tentativas esgotadas")
        conn.execute(
            "UPDATE codigo_entrada SET tentativas = ? WHERE email = ?", (tentativas, limpo)
        )
        conn.commit()
        return Entrada(ok=False, motivo="codigo errado")

    alvo = conn.execute(
        "SELECT pessoa_id FROM pessoa_email WHERE email = ?", (limpo,)
    ).fetchone()
    if not alvo:
        # Revogado entre o pedido e a conferencia. O codigo nao vale mais.
        conn.execute("DELETE FROM codigo_entrada WHERE email = ?", (limpo,))
        conn.commit()
        return Entrada(ok=False, motivo="fora da lista")

    conn.execute("DELETE FROM codigo_entrada WHERE email = ?", (limpo,))
    token = secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO sessao (token_hash, pessoa_id, email, criada_em, expira_em)
        VALUES (?, ?, ?, ?, ?)
        """,
        (_hash_token(token), int(alvo["pessoa_id"]), limpo,
         quando.isoformat(timespec="seconds"),
         (quando + timedelta(days=VALIDADE_SESSAO_DIAS)).isoformat(timespec="seconds")),
    )
    conn.commit()
    return Entrada(ok=True, token=token, pessoa_id=int(alvo["pessoa_id"]), email=limpo)


def de_sessao(conn: sqlite3.Connection, token: str, agora: str | None = None) -> sqlite3.Row | None:
    """Quem e o dono deste token, ou None se ele nao vale (mais).

    Sessao vencida e apagada na hora em que aparece: e o unico momento em que se
    sabe com certeza que ela nao serve mais.
    """
    if not token:
        return None
    linha = conn.execute(
        "SELECT token_hash, pessoa_id, email, expira_em FROM sessao WHERE token_hash = ?",
        (_hash_token(token),),
    ).fetchone()
    if not linha:
        return None
    if _quando(agora) > datetime.fromisoformat(linha["expira_em"]):
        conn.execute("DELETE FROM sessao WHERE token_hash = ?", (linha["token_hash"],))
        conn.commit()
        return None
    return linha


def encerrar(conn: sqlite3.Connection, token: str) -> bool:
    if not token:
        return False
    cursor = conn.execute("DELETE FROM sessao WHERE token_hash = ?", (_hash_token(token),))
    conn.commit()
    return cursor.rowcount > 0


def limpar(conn: sqlite3.Connection, agora: str | None = None) -> dict[str, int]:
    """Apaga codigo e sessao vencidos.

    Nao e so arrumacao: dado que nao serve para nada e dado que ainda pode
    vazar.
    """
    marca = _quando(agora).isoformat(timespec="seconds")
    codigos = conn.execute("DELETE FROM codigo_entrada WHERE expira_em < ?", (marca,)).rowcount
    sessoes = conn.execute("DELETE FROM sessao WHERE expira_em < ?", (marca,)).rowcount
    conn.commit()
    return {"codigos": codigos, "sessoes": sessoes}
