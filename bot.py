"""Bot da Apetit para controle nutricional do funcionario.

O produto nao vende comida: a empresa serve o refeitorio e o funcionario
acompanha o que come. Por isso nao existe preco, carrinho nem pedido — o que
existe e montar o prato do dia a partir do cardapio publicado e registrar o
consumo.

Toda a regra de dominio vive em apetit/. Este arquivo e so a camada do Telegram,
para que o mesmo dominio sirva depois a um painel do nutricionista.
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from apetit.allergens import ALLERGENS, Restriction, RestrictionKind, Verdict
from apetit.catalog import (
    check_menu_for_employee,
    connect,
    init_schema,
    item_allergens,
    menu_for_date,
    pending_issues,
    set_item_allergens,
)
from apetit.profile import Employee, aggregate_by_sector, delete_employee_data, load_employee, save_employee
from apetit.tracking import (
    RULES,
    add_favorite,
    consumption_history,
    consumption_totals,
    favorites,
    favorites_returning,
    log_consumption,
    points_breakdown,
    remove_favorite,
    score_day,
    score_week,
    total_points,
)

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("APETIT_DB_PATH", "apetit.db"))
STEP = "cadastro_step"
TRAY = "prato"

ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().isdigit()
}

GOALS = {
    "goal_perder": "Perder peso",
    "goal_manter": "Manter equilibrio",
    "goal_massa": "Ganhar massa",
    "goal_saudavel": "Comer melhor no dia a dia",
}

# Alvos ilustrativos por objetivo. Quem define faixa individual e o nutricionista
# responsavel: o bot informa e acompanha, nao prescreve.
TARGETS = {
    "Perder peso": {"kcal": 550, "ptn": 25},
    "Manter equilibrio": {"kcal": 700, "ptn": 30},
    "Ganhar massa": {"kcal": 850, "ptn": 45},
    "Comer melhor no dia a dia": {"kcal": 700, "ptn": 30},
}

VERDICT_MARK = {
    Verdict.BLOQUEIO: "⛔",
    Verdict.ATENCAO: "⚠️",
    Verdict.LIBERADO: "✅",
    Verdict.SEM_RESTRICAO: "",
}


def db():
    conn = connect(DB_PATH)
    init_schema(conn)
    return conn


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def week_start(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def tg_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


async def reply(update: Update, text: str, buttons=None, edit: bool = False) -> None:
    """Envia texto ja montado.

    Nao passa por str.format de proposito: o texto carrega nome, empresa e setor
    do proprio usuario, e uma chave solta no dado quebraria o handler.
    """
    markup = keyboard(buttons) if buttons else None
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=markup)


def main_menu() -> list[list[tuple[str, str]]]:
    return [
        [("\U0001f37d️ Cardapio de hoje", "cardapio"), ("\U0001f4dd Meu prato", "prato")],
        [("\U0001f4ca Meu dia", "meu_dia"), ("⭐ Favoritos", "favoritos")],
        [("\U0001f3c5 Meus pontos", "pontos"), ("\U0001f464 Meu cadastro", "perfil")],
    ]


# --------------------------------------------------------------------------
# Cadastro
# --------------------------------------------------------------------------

def draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("cadastro", {})


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "nome"
    await reply(
        update,
        "\U0001f37d️ <b>Bem-vindo ao acompanhamento nutricional da Apetit.</b>\n\n"
        "Vou fazer um cadastro rapido para mostrar o cardapio certo e avisar sobre o que voce nao pode comer.\n\n"
        "Qual e o seu nome?",
        edit=edit,
    )


async def ask_unit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[STEP] = "unidade"
    await reply(update, "Em qual <b>unidade da Apetit</b> voce almoca? (ex.: SM, OFL, DP)")


async def ask_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[STEP] = "empresa"
    await reply(update, "Em qual <b>empresa</b> voce trabalha?")


async def ask_sector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[STEP] = "setor"
    await reply(update, "E em qual <b>setor</b>?")


async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "objetivo"
    botoes = [[(label, code)] for code, label in GOALS.items()]
    await reply(update, "\U0001f3af Qual e o seu foco com a alimentacao agora?", botoes, edit=edit)


def restriction_buttons(selecionadas: set[str]) -> list[list[tuple[str, str]]]:
    linhas = []
    for code, label in ALLERGENS.items():
        marca = "☑️" if code in selecionadas else "⬜"
        linhas.append([(f"{marca} {label}", f"restr:{code}")])
    linhas.append([("✅ Terminei", "restr_ok")])
    return linhas


async def ask_restrictions(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "restricoes"
    selecionadas = set(draft(context).get("restricoes", []))
    await reply(
        update,
        "\U0001f6a8 <b>Alergia ou intolerancia?</b>\n\n"
        "Marque tudo o que se aplica. Vou conferir cada prato do cardapio contra essa lista.\n\n"
        "Se nao tiver nenhuma, e so tocar em <b>Terminei</b>.",
        restriction_buttons(selecionadas),
        edit=edit,
    )


async def ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "consentimento"
    await reply(
        update,
        "\U0001f512 <b>Privacidade</b>\n\n"
        "Para funcionar, preciso guardar seu nome, unidade, empresa, setor, restricoes alimentares "
        "e o que voce registrar que comeu.\n\n"
        "<b>Sua empresa nao ve nada disso individualmente.</b> O que a Apetit enxerga e so numero "
        "agregado, e recorte com menos de 5 pessoas nem aparece.\n\n"
        "Voce pode ver tudo com /meus_dados e apagar tudo com /excluir_dados, quando quiser.",
        [[("✅ Aceito", "consent_sim")], [("❌ Nao aceito", "consent_nao")]],
        edit=edit,
    )


async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dados = draft(context)
    user_id = tg_id(update)
    pessoa = Employee(
        telegram_id=user_id,
        name=dados.get("nome", ""),
        apetit_unit=dados.get("unidade", ""),
        client_company=dados.get("empresa", ""),
        sector=dados.get("setor", ""),
        goal=dados.get("objetivo", ""),
        consent_accepted=True,
        restrictions=[Restriction(code, RestrictionKind.ALERGIA) for code in dados.get("restricoes", [])],
    )
    conn = db()
    try:
        save_employee(conn, pessoa)
    finally:
        conn.close()

    context.user_data.pop(STEP, None)
    context.user_data.pop("cadastro", None)

    restricoes = ", ".join(ALLERGENS[c] for c in dados.get("restricoes", [])) or "nenhuma"
    await reply(
        update,
        "✅ <b>Cadastro concluido!</b>\n\n"
        f"<b>Nome:</b> {escape(pessoa.name)}\n"
        f"<b>Unidade Apetit:</b> {escape(pessoa.apetit_unit)}\n"
        f"<b>Empresa:</b> {escape(pessoa.client_company)}\n"
        f"<b>Setor:</b> {escape(pessoa.sector)}\n"
        f"<b>Objetivo:</b> {escape(pessoa.goal)}\n"
        f"<b>Restricoes:</b> {escape(restricoes)}\n\n"
        "Agora e so ver o cardapio \U0001f37d️",
        main_menu(),
        edit=True,
    )


def current_employee(update: Update) -> Employee | None:
    user_id = tg_id(update)
    if not user_id:
        return None
    conn = db()
    try:
        return load_employee(conn, user_id)
    finally:
        conn.close()


async def require_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> Employee | None:
    pessoa = current_employee(update)
    if pessoa and pessoa.registered:
        return pessoa
    await ask_name(update, context, edit=edit)
    return None


# --------------------------------------------------------------------------
# Cardapio e prato
# --------------------------------------------------------------------------

def menu_lines(pessoa: Employee, dia: str) -> tuple[str, list[list[tuple[str, str]]], int]:
    conn = db()
    try:
        itens = check_menu_for_employee(conn, dia, pessoa.restrictions, unit=pessoa.apetit_unit)
    finally:
        conn.close()

    if not itens:
        return (
            f"\U0001f4c5 Ainda nao tem cardapio publicado para {escape(dia)} na unidade {escape(pessoa.apetit_unit)}.",
            [[("\U0001f519 Menu", "menu")]],
            0,
        )

    linhas = [f"\U0001f37d️ <b>Cardapio de {escape(dia)}</b>"]
    botoes: list[list[tuple[str, str]]] = []
    sem_declaracao = 0
    categoria_atual = ""

    for item in itens:
        if item["category"] != categoria_atual:
            categoria_atual = item["category"]
            linhas.append(f"\n<b>{escape(categoria_atual)}</b>")
        check = item["check"]
        marca = VERDICT_MARK[check.verdict]
        kcal = f"{item['kcal']:.0f} kcal" if item["kcal"] is not None else "sem info nutricional"
        ptn = f" · {item['ptn_g']:.1f} g ptn" if item["ptn_g"] is not None else ""
        linhas.append(f"{marca} {escape(item['name'])} — {kcal}{ptn}")
        if check.verdict is Verdict.BLOQUEIO:
            linhas.append(f"   <b>{escape(check.message())}</b>")
        elif check.verdict is Verdict.ATENCAO:
            sem_declaracao += 1
            linhas.append(f"   <i>{escape(check.message())}</i>")
        if check.verdict is not Verdict.BLOQUEIO:
            botoes.append([(f"➕ {item['name'][:28]}", f"add:{item['item_code']}")])

    botoes.append([("\U0001f4dd Ver meu prato", "prato"), ("\U0001f519 Menu", "menu")])
    return "\n".join(linhas), botoes, sem_declaracao


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    texto, botoes, sem_declaracao = menu_lines(pessoa, today())
    if sem_declaracao and pessoa.restrictions:
        texto += (
            f"\n\n⚠️ <b>{sem_declaracao} prato(s) sem declaracao de alergenico.</b> "
            "Nao consigo confirmar que sao seguros para voce — confirme no balcao antes de se servir."
        )
    await reply(update, texto, botoes, edit=edit)


def tray(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.user_data.setdefault(TRAY, [])


async def show_tray(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    codigos = tray(context)
    if not codigos:
        await reply(
            update,
            "\U0001f4dd Seu prato esta vazio.\n\nAbra o cardapio e toque em <b>+</b> nos itens que voce pegou.",
            [[("\U0001f37d️ Ver cardapio", "cardapio")], [("\U0001f519 Menu", "menu")]],
            edit=edit,
        )
        return

    conn = db()
    try:
        itens = {linha["item_code"]: linha for linha in menu_for_date(conn, today(), unit=pessoa.apetit_unit)}
    finally:
        conn.close()

    kcal = cho = lip = ptn = 0.0
    linhas = ["\U0001f4dd <b>Seu prato de hoje</b>\n"]
    botoes = []
    for code in codigos:
        linha = itens.get(code)
        if not linha:
            continue
        kcal += linha["kcal"] or 0
        cho += linha["cho_g"] or 0
        lip += linha["lip_g"] or 0
        ptn += linha["ptn_g"] or 0
        linhas.append(f"• {escape(linha['name'])}")
        botoes.append([(f"➖ {linha['name'][:24]}", f"del:{code}"), (f"⭐ {linha['name'][:14]}", f"fav:{code}")])

    alvo = TARGETS.get(pessoa.goal, TARGETS["Manter equilibrio"])
    linhas.append(
        f"\n<b>Total:</b> {kcal:.0f} kcal · {cho:.0f} g carbo · {lip:.0f} g gordura · {ptn:.1f} g proteina\n"
        f"<b>Seu alvo:</b> {alvo['kcal']} kcal · {alvo['ptn']} g proteina"
    )
    botoes.append([("✅ Registrar almoco", "registrar")])
    botoes.append([("\U0001f37d️ Cardapio", "cardapio"), ("\U0001f519 Menu", "menu")])
    await reply(update, "\n".join(linhas), botoes, edit=edit)


async def register_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pessoa = await require_registration(update, context, edit=True)
    if not pessoa:
        return
    codigos = tray(context)
    if not codigos:
        await show_tray(update, context, edit=True)
        return

    dia = today()
    alvo = TARGETS.get(pessoa.goal, TARGETS["Manter equilibrio"])
    conn = db()
    try:
        log_consumption(conn, pessoa.telegram_id, dia, codigos)
        ganhas = score_day(conn, pessoa.telegram_id, dia, protein_target_g=alvo["ptn"])
        ganhas += score_week(conn, pessoa.telegram_id, week_start(dia))
        totais = consumption_totals(conn, pessoa.telegram_id, dia)
        pontos = total_points(conn, pessoa.telegram_id)
    finally:
        conn.close()

    context.user_data[TRAY] = []
    linhas = [
        "✅ <b>Almoco registrado!</b>\n",
        f"{totais['kcal']:.0f} kcal · {totais['ptn_g']:.1f} g de proteina",
    ]
    if ganhas:
        linhas.append("\n\U0001f3c5 <b>Pontos de hoje:</b>")
        linhas.extend(f"• {escape(regra.label)} +{regra.points}" for regra in ganhas)
    linhas.append(f"\n<b>Total acumulado:</b> {pontos} pontos")
    await reply(update, "\n".join(linhas), main_menu(), edit=True)


# --------------------------------------------------------------------------
# Consultas do funcionario
# --------------------------------------------------------------------------

async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    conn = db()
    try:
        totais = consumption_totals(conn, pessoa.telegram_id, today())
        historico = consumption_history(conn, pessoa.telegram_id, 15)
    finally:
        conn.close()

    alvo = TARGETS.get(pessoa.goal, TARGETS["Manter equilibrio"])
    linhas = ["\U0001f4ca <b>Seu dia</b>\n"]
    if totais.get("itens"):
        linhas.append(
            f"Hoje: {totais['kcal']:.0f} kcal de {alvo['kcal']} · "
            f"{totais['ptn_g']:.1f} g de proteina de {alvo['ptn']}"
        )
    else:
        linhas.append("Voce ainda nao registrou o almoco de hoje.")

    if historico:
        linhas.append("\n<b>Registrado antes:</b>")
        dia_atual = ""
        for linha in historico:
            if linha["service_date"] != dia_atual:
                dia_atual = linha["service_date"]
                linhas.append(f"\n<i>{escape(dia_atual)}</i>")
            linhas.append(f"• {escape(linha['name'])}")
    await reply(update, "\n".join(linhas), main_menu(), edit=edit)


async def show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    conn = db()
    try:
        lista = favorites(conn, pessoa.telegram_id)
    finally:
        conn.close()
    if not lista:
        await reply(
            update,
            "⭐ Voce ainda nao guardou nenhum prato.\n\n"
            "Ao montar o prato, toque na estrela do item — eu te aviso quando ele voltar ao cardapio.",
            main_menu(),
            edit=edit,
        )
        return
    linhas = ["⭐ <b>Seus pratos guardados</b>\n", "Aviso voce quando algum deles voltar ao cardapio.\n"]
    botoes = [[(f"❌ Remover {linha['name'][:22]}", f"unfav:{linha['item_code']}")] for linha in lista]
    linhas.extend(f"• {escape(linha['name'])}" for linha in lista)
    botoes.append([("\U0001f519 Menu", "menu")])
    await reply(update, "\n".join(linhas), botoes, edit=edit)


async def show_points(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    conn = db()
    try:
        pontos = total_points(conn, pessoa.telegram_id)
        extrato = points_breakdown(conn, pessoa.telegram_id)
    finally:
        conn.close()

    rotulos = {regra.code: regra.label for regra in RULES}
    linhas = [f"\U0001f3c5 <b>Voce tem {pontos} pontos</b>\n"]
    if extrato:
        linhas.append("<b>De onde vieram:</b>")
        linhas.extend(
            f"• {escape(rotulos.get(linha['rule_code'], linha['rule_code']))}: {linha['pontos']} ({linha['vezes']}x)"
            for linha in extrato
        )
    else:
        linhas.append("Registre um almoco para comecar a pontuar.")
    linhas.append(
        "\n<i>Os pontos premiam constancia, variedade e composicao do prato. "
        "Nada aqui pontua comer menos, e nao existe ranking entre colegas.</i>"
    )
    await reply(update, "\n".join(linhas), main_menu(), edit=edit)


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    restricoes = ", ".join(ALLERGENS[r.allergen] for r in pessoa.restrictions) or "nenhuma"
    await reply(
        update,
        "\U0001f464 <b>Seu cadastro</b>\n\n"
        f"<b>Nome:</b> {escape(pessoa.name)}\n"
        f"<b>Unidade Apetit:</b> {escape(pessoa.apetit_unit)}\n"
        f"<b>Empresa:</b> {escape(pessoa.client_company)}\n"
        f"<b>Setor:</b> {escape(pessoa.sector)}\n"
        f"<b>Objetivo:</b> {escape(pessoa.goal)}\n"
        f"<b>Restricoes:</b> {escape(restricoes)}",
        [[("✏️ Refazer cadastro", "recadastrar")], [("\U0001f519 Menu", "menu")]],
        edit=edit,
    )


async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pessoa = current_employee(update)
    if not pessoa:
        await reply(update, "\U0001f4cb Nao encontrei cadastro seu. Envie /start para comecar.")
        return
    conn = db()
    try:
        historico = consumption_history(conn, pessoa.telegram_id, 100)
        guardados = favorites(conn, pessoa.telegram_id)
        pontos = total_points(conn, pessoa.telegram_id)
    finally:
        conn.close()
    restricoes = ", ".join(ALLERGENS[r.allergen] for r in pessoa.restrictions) or "nenhuma"
    await reply(
        update,
        "\U0001f512 <b>Tudo o que guardo sobre voce</b>\n\n"
        f"<b>Nome:</b> {escape(pessoa.name)}\n"
        f"<b>Unidade:</b> {escape(pessoa.apetit_unit)}\n"
        f"<b>Empresa:</b> {escape(pessoa.client_company)}\n"
        f"<b>Setor:</b> {escape(pessoa.sector)}\n"
        f"<b>Objetivo:</b> {escape(pessoa.goal)}\n"
        f"<b>Restricoes:</b> {escape(restricoes)}\n"
        f"<b>Consentimento:</b> aceito em {escape(pessoa.consented_at or 'nao informado')}\n\n"
        f"<b>Refeicoes registradas:</b> {len(historico)}\n"
        f"<b>Pratos guardados:</b> {len(guardados)}\n"
        f"<b>Pontos:</b> {pontos}\n\n"
        "Sua empresa nao ve nada disso individualmente.\n"
        "Para apagar tudo, envie /excluir_dados.",
    )


async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not current_employee(update):
        await reply(update, "\U0001f4cb Nao encontrei dados seus para excluir.")
        return
    await reply(
        update,
        "⚠️ <b>Apagar tudo?</b>\n\n"
        "Vou remover cadastro, restricoes, historico de refeicoes, pratos guardados e pontos. "
        "Isso nao tem volta.",
        [[("✅ Sim, apagar", "del_sim")], [("❌ Cancelar", "del_nao")]],
    )


# --------------------------------------------------------------------------
# Nutricionista e administracao
# --------------------------------------------------------------------------

async def deny_admin(update: Update, acao: str) -> None:
    if not ADMIN_IDS:
        await update.effective_message.reply_text(
            "\U0001f512 Nenhum administrador configurado. "
            "Defina ADMIN_TELEGRAM_IDS no .env com os IDs autorizados."
        )
        return
    await update.effective_message.reply_text(f"\U0001f512 Apenas administradores podem {acao}.")


async def show_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await deny_admin(update, "ver a fila de revisao")
        return
    conn = db()
    try:
        fila = pending_issues(conn)
    finally:
        conn.close()
    if not fila:
        await update.effective_message.reply_text("✅ Nenhum item aguardando revisao.")
        return
    agrupado: dict[str, dict] = {}
    for linha in fila:
        chave = linha["item_name"]
        registro = agrupado.setdefault(chave, {"vezes": 0, "detail": linha["detail"]})
        registro["vezes"] += 1
    linhas = [f"\U0001f9ea {len(agrupado)} ficha(s) aguardando revisao:\n"]
    for nome, registro in agrupado.items():
        repete = f" ({registro['vezes']}x)" if registro["vezes"] > 1 else ""
        linhas.append(f"- {nome}{repete}\n  {registro['detail']}")
    await update.effective_message.reply_text("\n".join(linhas))


async def declare_allergen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/alergenico <codigo_do_prato> <alergenico> <contem|nao_contem|pode_conter>"""
    if not is_admin(tg_id(update)):
        await deny_admin(update, "declarar alergenico")
        return
    args = context.args or []
    if len(args) != 3:
        await update.effective_message.reply_text(
            "Envie assim:\n\n/alergenico <codigo_do_prato> <alergenico> <contem|nao_contem|pode_conter>\n\n"
            "Exemplo:\n/alergenico strogonoff_de_carne leite contem\n\n"
            f"Alergenicos: {', '.join(ALLERGENS)}"
        )
        return
    codigo, alergenico, estado = args
    conn = db()
    try:
        set_item_allergens(conn, codigo, {alergenico: estado}, source="telegram")
        declarado = item_allergens(conn, codigo)
    except ValueError as erro:
        await update.effective_message.reply_text(f"❌ {erro}")
        return
    finally:
        conn.close()
    await update.effective_message.reply_text(
        f"✅ {codigo}: {alergenico} = {estado}.\n"
        f"Declarados neste prato: {len(declarado)} de {len(ALLERGENS)}."
    )


async def show_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Relatorio agregado. Nunca individual: e dado de saude de funcionario."""
    if not is_admin(tg_id(update)):
        await deny_admin(update, "ver o relatorio")
        return
    conn = db()
    try:
        linhas_setor = aggregate_by_sector(conn)
    finally:
        conn.close()
    if not linhas_setor:
        await update.effective_message.reply_text("Ainda nao ha funcionarios cadastrados.")
        return
    linhas = ["\U0001f4ca <b>Adesao por setor</b>\n"]
    for linha in linhas_setor:
        onde = f"{linha['client_company']} · {linha['sector']}"
        if linha["suprimido"]:
            linhas.append(f"- {onde}: suprimido ({linha['motivo']})")
        else:
            linhas.append(f"- {onde}: {linha['total']} pessoas")
    linhas.append(
        "\n<i>So adesao agregada. Consumo, objetivo e restricao de pessoa identificada "
        "nao saem daqui — sao dado de saude do funcionario.</i>"
    )
    await update.effective_message.reply_text("\n".join(linhas), parse_mode=ParseMode.HTML)


async def notify_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Avisa quem guardou um prato que volta ao cardapio nos proximos dias."""
    if not is_admin(tg_id(update)):
        await deny_admin(update, "disparar avisos de favorito")
        return
    inicio = today()
    fim = (date.fromisoformat(inicio) + timedelta(days=7)).isoformat()
    conn = db()
    try:
        voltando = favorites_returning(conn, inicio, fim)
    finally:
        conn.close()

    por_pessoa: dict[int, list] = {}
    for linha in voltando:
        por_pessoa.setdefault(linha["telegram_id"], []).append(linha)

    avisados = 0
    for telegram_id, linhas in por_pessoa.items():
        pratos = "\n".join(f"• {escape(l['item_name'])} em {escape(l['service_date'])}" for l in linhas)
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=f"⭐ <b>Um prato que voce guardou esta voltando!</b>\n\n{pratos}",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard([[("\U0001f37d️ Ver cardapio", "cardapio")]]),
            )
            avisados += 1
        except Exception:
            logger.exception("Falha ao avisar %s", telegram_id)
    await update.effective_message.reply_text(f"✅ Avisos enviados: {avisados}.")


# --------------------------------------------------------------------------
# Roteamento
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pessoa = current_employee(update)
    if pessoa and pessoa.registered:
        await reply(
            update,
            f"\U0001f37d️ <b>Ola, {escape(pessoa.name)}!</b>\n\nO que voce quer ver?",
            main_menu(),
        )
        return
    await ask_name(update, context)


async def restart_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await ask_name(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    step = context.user_data.get(STEP)
    texto = (update.message.text or "").strip()
    if not step:
        pessoa = current_employee(update)
        if pessoa and pessoa.registered:
            await reply(update, "Escolha uma opcao \U0001f447", main_menu())
        else:
            await ask_name(update, context)
        return

    if len(texto) < 2:
        await reply(update, "Me manda uma resposta um pouco mais completa \U0001f60a")
        return

    dados = draft(context)
    if step == "nome":
        dados["nome"] = texto
        await ask_unit(update, context)
    elif step == "unidade":
        dados["unidade"] = texto
        await ask_company(update, context)
    elif step == "empresa":
        dados["empresa"] = texto
        await ask_sector(update, context)
    elif step == "setor":
        dados["setor"] = texto
        await ask_goal(update, context)
    else:
        await reply(update, "Use os botoes acima para continuar \U0001f60a")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data in GOALS:
        draft(context)["objetivo"] = GOALS[data]
        await ask_restrictions(update, context, edit=True)
        return
    if data.startswith("restr:"):
        code = data.removeprefix("restr:")
        selecionadas = set(draft(context).get("restricoes", []))
        selecionadas.symmetric_difference_update({code})
        draft(context)["restricoes"] = sorted(selecionadas)
        await ask_restrictions(update, context, edit=True)
        return
    if data == "restr_ok":
        await ask_consent(update, context, edit=True)
        return
    if data == "consent_sim":
        await finish_registration(update, context)
        return
    if data == "consent_nao":
        context.user_data.clear()
        await reply(
            update,
            "\U0001f512 Sem o aceite eu nao consigo guardar seus dados nem acompanhar suas refeicoes.\n\n"
            "Se mudar de ideia, envie /start.",
            edit=True,
        )
        return
    if data == "recadastrar":
        context.user_data.clear()
        await ask_name(update, context, edit=True)
        return
    if data == "menu":
        await reply(update, "O que voce quer ver?", main_menu(), edit=True)
        return
    if data == "cardapio":
        await show_menu(update, context, edit=True)
        return
    if data == "prato":
        await show_tray(update, context, edit=True)
        return
    if data.startswith("add:"):
        code = data.removeprefix("add:")
        if code not in tray(context):
            tray(context).append(code)
        await show_tray(update, context, edit=True)
        return
    if data.startswith("del:"):
        code = data.removeprefix("del:")
        if code in tray(context):
            tray(context).remove(code)
        await show_tray(update, context, edit=True)
        return
    if data.startswith("fav:"):
        pessoa = await require_registration(update, context, edit=True)
        if pessoa:
            conn = db()
            try:
                add_favorite(conn, pessoa.telegram_id, data.removeprefix("fav:"))
            finally:
                conn.close()
            await reply(
                update,
                "⭐ Guardado! Te aviso quando esse prato voltar ao cardapio.",
                [[("\U0001f4dd Meu prato", "prato"), ("\U0001f519 Menu", "menu")]],
                edit=True,
            )
        return
    if data.startswith("unfav:"):
        pessoa = await require_registration(update, context, edit=True)
        if pessoa:
            conn = db()
            try:
                remove_favorite(conn, pessoa.telegram_id, data.removeprefix("unfav:"))
            finally:
                conn.close()
            await show_favorites(update, context, edit=True)
        return
    if data == "registrar":
        await register_meal(update, context)
        return
    if data == "meu_dia":
        await show_day(update, context, edit=True)
        return
    if data == "favoritos":
        await show_favorites(update, context, edit=True)
        return
    if data == "pontos":
        await show_points(update, context, edit=True)
        return
    if data == "perfil":
        await show_profile(update, context, edit=True)
        return
    if data == "del_sim":
        user_id = tg_id(update)
        if user_id:
            conn = db()
            try:
                delete_employee_data(conn, user_id)
            finally:
                conn.close()
        context.user_data.clear()
        await reply(update, "✅ Tudo apagado. Se quiser voltar, e so enviar /start.", edit=True)
        return
    if data == "del_nao":
        await reply(update, "Tudo bem, mantive seus dados como estavam \U0001f60a", main_menu(), edit=True)
        return

    await reply(update, "O que voce quer ver?", main_menu(), edit=True)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Defina TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.")
    conn = db()
    conn.close()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recadastrar", restart_registration))
    app.add_handler(CommandHandler("cardapio", show_menu))
    app.add_handler(CommandHandler("prato", show_tray))
    app.add_handler(CommandHandler("meu_dia", show_day))
    app.add_handler(CommandHandler("favoritos", show_favorites))
    app.add_handler(CommandHandler("pontos", show_points))
    app.add_handler(CommandHandler("meus_dados", show_my_data))
    app.add_handler(CommandHandler("excluir_dados", confirm_delete))
    app.add_handler(CommandHandler("pendencias", show_pending))
    app.add_handler(CommandHandler("alergenico", declare_allergen))
    app.add_handler(CommandHandler("relatorio", show_report))
    app.add_handler(CommandHandler("avisar_favoritos", notify_favorites))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    if webhook_url:
        webhook_path = os.getenv("TELEGRAM_WEBHOOK_PATH", "telegram-webhook").strip("/")
        port = int(os.getenv("PORT", "8000"))
        logger.info("Apetit Bot iniciado em webhook na porta %s.", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=f"{webhook_url}/{webhook_path}",
            secret_token=os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or None,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Apetit Bot iniciado em polling.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
