"""Aviso de progresso: o app falando primeiro, sem virar cobranca.

Ate aqui o app so respondia. Uma mensagem que o app manda sozinho e outra
coisa: ela chega no celular da pessoa, num app da empresa, sobre o que ela come.
Isso pode ser util ou pode ser pressao, e a diferenca esta em poucas regras.

**1. Fala do proprio progresso, nunca do prato.** "Voce registrou 4 de 5 dias"
e sobre constancia. "Voce passou das calorias" seria o empregador comentando o
almoco de alguem — e nao vai existir aqui.

**2. Nunca compara com colega.** Vale a mesma regra do resto do app: nao ha
ranking, nao ha media do setor, nao ha "voce registrou menos que a equipe".

**3. Quem nao esta usando para de receber.** Duas semanas sem registro e o
resumo semanal cala. Sem isso, o aviso vira cobranca semanal de um app
corporativo para quem ja decidiu nao usar — o jeito mais rapido de a pessoa
passar a ignorar tudo, inclusive o alerta de alergenico.

**4. Nada e enviado duas vezes.** O envio fica registrado por periodo, entao
reiniciar o bot no meio do dia nao reenvia o que ja saiu.

**5. O lembrete de almoco e opt-in e some sozinho.** So chega para quem pediu,
so quando ha cardapio publicado e so se a pessoa ainda nao registrou — e se
registrou, nao chega.

Este modulo decide **quem recebe o que e com que texto**. Quem entrega e o
bot; aqui nao ha Telegram nenhum, o que deixa a regra testavel sem rede.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from .humanize import week_summary
from .tracking import RULES_BY_CODE

RESUMO_SEMANAL = "resumo_semanal"
LEMBRETE_ALMOCO = "lembrete_almoco"

# O resumo semanal ja vem ligado: e o proprio progresso da pessoa, uma vez por
# semana, e desligavel em dois toques. O lembrete de almoco nao: ele chega todo
# dia util e ninguem pediu para um app da empresa lembrar da hora do almoco.
PADRAO = {RESUMO_SEMANAL: True, LEMBRETE_ALMOCO: False}

# Semanas seguidas sem nenhum registro depois das quais o resumo cala.
SEMANAS_ATE_CALAR = 2

def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Nudge:
    """Uma mensagem pronta para uma pessoa. O bot so entrega."""

    telegram_id: int
    kind: str
    period: str
    text: str


def notification_settings(conn: sqlite3.Connection, telegram_id: int) -> dict[str, bool]:
    escolhas = dict(PADRAO)
    for linha in conn.execute(
        "SELECT kind, enabled FROM employee_notification WHERE telegram_id = ?", (telegram_id,)
    ).fetchall():
        escolhas[linha["kind"]] = bool(linha["enabled"])
    return escolhas


def set_notification(conn: sqlite3.Connection, telegram_id: int, kind: str, enabled: bool) -> None:
    if kind not in PADRAO:
        raise ValueError(f"Aviso desconhecido: {kind}")
    conn.execute(
        """
        INSERT INTO employee_notification (telegram_id, kind, enabled, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id, kind) DO UPDATE SET
            enabled = excluded.enabled, updated_at = excluded.updated_at
        """,
        (telegram_id, kind, int(enabled), now_iso()),
    )
    conn.commit()


def already_sent(conn: sqlite3.Connection, telegram_id: int, kind: str, period: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM notification_sent WHERE telegram_id = ? AND kind = ? AND period = ?",
        (telegram_id, kind, period),
    ).fetchone() is not None


def mark_sent(conn: sqlite3.Connection, telegram_id: int, kind: str, period: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO notification_sent (telegram_id, kind, period, sent_at) "
        "VALUES (?, ?, ?, ?)",
        (telegram_id, kind, period, now_iso()),
    )
    conn.commit()


def week_start(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def _quem_recebe(conn: sqlite3.Connection, kind: str) -> list[int]:
    """Cadastrados com consentimento que nao desligaram este aviso."""
    pessoas = [
        linha["telegram_id"]
        for linha in conn.execute(
            "SELECT telegram_id FROM employee WHERE consent_accepted = 1 ORDER BY telegram_id"
        ).fetchall()
    ]
    return [p for p in pessoas if notification_settings(conn, p).get(kind, False)]


def _dias_registrados(conn: sqlite3.Connection, telegram_id: int, inicio: str, fim: str) -> int:
    return conn.execute(
        "SELECT COUNT(DISTINCT service_date) AS t FROM consumption "
        "WHERE telegram_id = ? AND service_date BETWEEN ? AND ?",
        (telegram_id, inicio, fim),
    ).fetchone()["t"]


def _conquistas_da_semana(conn: sqlite3.Connection, telegram_id: int, inicio: str, fim: str) -> list[str]:
    linhas = conn.execute(
        "SELECT DISTINCT rule_code FROM points_event "
        "WHERE telegram_id = ? AND reference_date BETWEEN ? AND ? ORDER BY rule_code",
        (telegram_id, inicio, fim),
    ).fetchall()
    return [RULES_BY_CODE[l["rule_code"]].label for l in linhas if l["rule_code"] in RULES_BY_CODE]


def weekly_nudges(conn: sqlite3.Connection, day: str) -> list[Nudge]:
    """O resumo da semana da propria pessoa, para quem ainda esta usando o app.

    `day` e o dia em que o resumo sai (sexta, na pratica). A semana resumida e
    a que contem esse dia.
    """
    inicio = week_start(day)
    fim = day
    avisos = []
    for telegram_id in _quem_recebe(conn, RESUMO_SEMANAL):
        if already_sent(conn, telegram_id, RESUMO_SEMANAL, inicio):
            continue
        dias = _dias_registrados(conn, telegram_id, inicio, fim)
        if dias == 0 and _abandonou(conn, telegram_id, inicio):
            continue  # ver regra 3 no topo do modulo
        avisos.append(Nudge(telegram_id, RESUMO_SEMANAL, inicio, _texto_semanal(conn, telegram_id, inicio, fim, dias)))
    return avisos


def _abandonou(conn: sqlite3.Connection, telegram_id: int, inicio_semana: str) -> bool:
    """Semanas seguidas sem registro nenhum, incluindo a que esta acabando."""
    comeco = date.fromisoformat(inicio_semana)
    limite = (comeco - timedelta(weeks=SEMANAS_ATE_CALAR - 1)).isoformat()
    fim = (comeco + timedelta(days=6)).isoformat()
    return _dias_registrados(conn, telegram_id, limite, fim) == 0


def _texto_semanal(conn: sqlite3.Connection, telegram_id: int, inicio: str, fim: str, dias: int) -> str:
    linhas = ["\U0001f4c8 <b>Sua semana</b>", "", week_summary(dias)]
    conquistas = _conquistas_da_semana(conn, telegram_id, inicio, fim)
    if conquistas:
        linhas.append("")
        linhas.append("<b>Suas conquistas desta semana:</b>")
        linhas.extend(f"• {c}" for c in conquistas)
    if dias == 0:
        # Primeira semana sem registro: convite, nunca cobranca. A segunda nao
        # chega a existir — o resumo cala.
        linhas.append("")
        linhas.append("Sem pressa. Quando quiser, e so tocar em <b>Quanto pegar hoje</b>.")
    linhas.append("")
    linhas.append("<i>Isso e so seu: nao existe ranking e sua empresa nao ve nada disso.</i>")
    linhas.append("<i>Para nao receber mais: /avisos</i>")
    return "\n".join(linhas)


def lunch_nudges(conn: sqlite3.Connection, day: str) -> list[Nudge]:
    """Lembrete de registrar o almoco, so para quem pediu e ainda nao registrou.

    Exige cardapio publicado no dia: sem cardapio nao ha o que registrar, e o
    lembrete viraria uma mensagem que nao leva a lugar nenhum.
    """
    if date.fromisoformat(day).weekday() >= 5:
        return []  # fim de semana nao tem refeitorio
    avisos = []
    for telegram_id in _quem_recebe(conn, LEMBRETE_ALMOCO):
        if already_sent(conn, telegram_id, LEMBRETE_ALMOCO, day):
            continue
        if _dias_registrados(conn, telegram_id, day, day):
            continue
        if not _tem_cardapio(conn, telegram_id, day):
            continue
        avisos.append(Nudge(
            telegram_id, LEMBRETE_ALMOCO, day,
            "\U0001f37d️ <b>Ja almocou?</b>\n\n"
            "Registrar leva dois toques e mantem o seu historico em dia.\n\n"
            "<i>Para nao receber mais: /avisos</i>",
        ))
    return avisos


def _tem_cardapio(conn: sqlite3.Connection, telegram_id: int, day: str) -> bool:
    unidade = conn.execute(
        "SELECT apetit_unit FROM employee WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    if not unidade:
        return False
    return conn.execute(
        "SELECT 1 FROM menu_entry WHERE service_date = ? AND unit = ? LIMIT 1",
        (day, unidade["apetit_unit"]),
    ).fetchone() is not None
