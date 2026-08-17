"""Bot da Apetit para controle nutricional do funcionario.

E uma ferramenta de acompanhamento pessoal: quem usa quer comer melhor e
enxergar o proprio dia. Nao ha venda, nao ha pedido e nao ha comparacao com
colega — nem ranking, nem media de setor exibida para a pessoa.

Duas escolhas de interface guiam o arquivo inteiro:

- montar o prato segue a ordem da fila do refeitorio, uma categoria por vez,
  em vez de uma lista unica com tudo;
- o numero vem acompanhado de leitura em palavras, porque "prato leve, boa
  fonte de proteina" ajuda mais que "134 kcal, 12 g PTN".

Toda a regra de dominio vive em apetit/, para servir depois a um painel do
nutricionista sem reescrita.
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta
from html import escape
from pathlib import Path

from dotenv import load_dotenv
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from apetit.allergen_sheet import coverage_summary
from apetit.allergy_text import describe, recognize
from apetit.portions import measure_label, suggest_plate
from apetit.catalog import (
    allergen_coverage,
    check_menu_for_employee,
    connect,
    init_schema,
    item_allergens,
    known_units,
    menu_for_date,
    pending_issues,
    set_item_allergens,
)
from apetit.feedback import (
    MISSING_TAGS,
    SCALE,
    Rating,
    all_unit_reports,
    my_ratings,
    rating_for,
    save_rating,
    unit_comments,
    unit_report,
    unit_trend,
)
from apetit.humanize import (
    FRESH_CATEGORIES,
    category_label,
    clean_dish_name,
    dish_hint,
    friendly_date,
    order_categories,
    plate_reading,
    week_summary,
)
from apetit.profile import (
    Employee,
    aggregate_by_sector,
    delete_employee_data,
    known_companies,
    known_sectors,
    load_employee,
    save_employee,
)
from apetit.tracking import (
    RULES,
    add_favorite,
    consumption_history,
    consumption_totals,
    favorites,
    favorites_returning,
    history_by_day,
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
FLOW = "monta_categoria"

ADMIN_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().isdigit()
}

GOALS = {
    "goal_saudavel": "Comer melhor no dia a dia",
    "goal_manter": "Manter o equilibrio",
    "goal_perder": "Comer mais leve",
    "goal_massa": "Reforcar a proteina",
}

# Alvos ilustrativos. Quem define faixa individual e o nutricionista
# responsavel: o app informa e acompanha, nao prescreve.
TARGETS = {
    "Comer melhor no dia a dia": {"kcal": 700, "ptn": 30},
    "Manter o equilibrio": {"kcal": 700, "ptn": 30},
    "Comer mais leve": {"kcal": 550, "ptn": 25},
    "Reforcar a proteina": {"kcal": 850, "ptn": 45},
}
TARGET_PADRAO = {"kcal": 700, "ptn": 30}

VERDICT_MARK = {
    Verdict.BLOQUEIO: "⛔",
    Verdict.ATENCAO: "⚠️",
    Verdict.LIBERADO: "✅",
    Verdict.SEM_RESTRICAO: "",
}

COMMANDS = [
    BotCommand("cardapio", "Ver o cardapio de hoje"),
    BotCommand("quanto_pegar", "Quanto pegar de cada coisa hoje"),
    BotCommand("montar", "Montar meu prato passo a passo"),
    BotCommand("avaliar", "Avaliar o refeitorio de hoje"),
    BotCommand("meu_dia", "O que eu comi hoje e nos ultimos dias"),
    BotCommand("favoritos", "Pratos que eu guardei"),
    BotCommand("progresso", "Minha sequencia e conquistas"),
    BotCommand("meus_dados", "Ver tudo o que o app guarda de mim"),
    BotCommand("excluir_dados", "Apagar meus dados"),
    BotCommand("ajuda", "Como usar o app"),
]


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
        [("\U0001f957 Quanto pegar hoje", "quanto")],
        [("\U0001f37d️ Montar meu prato", "montar"), ("\U0001f4c5 Cardapio", "cardapio")],
        [("⭐ Avaliar o refeitorio", "avaliar")],
        [("\U0001f4c8 Meu progresso", "progresso")],
        [("\U0001f4ca Meu dia", "meu_dia"), ("⭐ Favoritos", "favoritos")],
        [("\U0001f464 Meu cadastro", "perfil"), ("❓ Ajuda", "ajuda")],
    ]


def target_for(pessoa: Employee) -> dict:
    return TARGETS.get(pessoa.goal, TARGET_PADRAO)


# --------------------------------------------------------------------------
# Cadastro
# --------------------------------------------------------------------------

def draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("cadastro", {})


def options_keyboard(valores: list[str], prefixo: str, rotulo_outro: str) -> list[list[tuple[str, str]]]:
    """Botoes com o que ja existe, mais a saida para digitar algo novo."""
    linhas = [[(valor[:40], f"{prefixo}:{indice}")] for indice, valor in enumerate(valores[:8])]
    linhas.append([(rotulo_outro, f"{prefixo}:outro")])
    return linhas


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "nome"
    await reply(
        update,
        "\U0001f37d️ <b>Ola! Sou o acompanhamento nutricional da Apetit.</b>\n\n"
        "Eu te mostro o cardapio do refeitorio, aviso o que voce nao pode comer "
        "e te ajudo a acompanhar o que voce anda comendo.\n\n"
        "E so seu: ninguem compara voce com colega nenhum.\n\n"
        "<b>Como voce quer ser chamado?</b>",
        edit=edit,
    )


async def ask_unit(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "unidade"
    conn = db()
    try:
        unidades = known_units(conn)
    finally:
        conn.close()
    draft(context)["_unidades"] = unidades
    if unidades:
        await reply(
            update,
            "\U0001f4cd <b>Onde voce almoca?</b>\n\nEscolha o refeitorio:",
            options_keyboard(unidades, "unit", "Nao vejo o meu aqui"),
            edit=edit,
        )
        return
    await reply(update, "\U0001f4cd Qual e o <b>refeitorio</b> onde voce almoca?", edit=edit)


async def ask_company(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "empresa"
    conn = db()
    try:
        empresas = known_companies(conn, draft(context).get("unidade", ""))
    finally:
        conn.close()
    draft(context)["_empresas"] = empresas
    if empresas:
        await reply(
            update,
            "\U0001f3e2 <b>Em qual empresa voce trabalha?</b>",
            options_keyboard(empresas, "comp", "Outra empresa"),
            edit=edit,
        )
        return
    await reply(update, "\U0001f3e2 Em qual <b>empresa</b> voce trabalha?", edit=edit)


async def ask_sector(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "setor"
    conn = db()
    try:
        setores = known_sectors(conn, draft(context).get("empresa", ""))
    finally:
        conn.close()
    draft(context)["_setores"] = setores
    if setores:
        await reply(
            update,
            "\U0001f477 <b>E em qual setor?</b>",
            options_keyboard(setores, "setor", "Outro setor"),
            edit=edit,
        )
        return
    await reply(update, "\U0001f477 E em qual <b>setor</b>?", edit=edit)


async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "objetivo"
    botoes = [[(label, code)] for code, label in GOALS.items()]
    await reply(
        update,
        "\U0001f3af <b>O que voce quer do app?</b>\n\n"
        "Isso so ajusta as dicas que eu te dou. Da para mudar quando quiser.",
        botoes,
        edit=edit,
    )


def restriction_buttons(selecionadas: set[str]) -> list[list[tuple[str, str]]]:
    linhas = []
    for code, label in ALLERGENS.items():
        marca = "☑️" if code in selecionadas else "⬜"
        linhas.append([(f"{marca} {label}", f"restr:{code}")])
    rotulo = "✅ Confirmar" if selecionadas else "Nao tenho nenhuma"
    linhas.append([(rotulo, "restr_ok")])
    return linhas


async def ask_restrictions(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    """Pergunta em texto livre. A pessoa escreve do jeito dela."""
    context.user_data[STEP] = "restricoes_texto"
    await reply(
        update,
        "\U0001f6a8 <b>Voce tem alguma alergia ou intolerancia alimentar?</b>\n\n"
        "Pode escrever do seu jeito. Por exemplo:\n"
        "<i>alergia a frutos do mar</i>\n"
        "<i>nao posso leite nem ovo</i>\n"
        "<i>intolerante a lactose</i>\n\n"
        "Vou conferir <b>cada prato</b> do cardapio contra o que voce escrever.",
        [[("Nao tenho nenhuma", "restr_nenhuma")], [("Prefiro escolher numa lista", "restr_lista")]],
        edit=edit,
    )


async def confirm_restrictions(update: Update, context: ContextTypes.DEFAULT_TYPE, texto: str) -> None:
    """Mostra o que o app entendeu e pede confirmacao antes de salvar."""
    reconhecidos, livres = recognize(texto)
    dados = draft(context)
    dados["restricoes"] = reconhecidos
    dados["restricoes_livres"] = livres
    context.user_data[STEP] = "restricoes_confirma"

    linhas = [f"Voce escreveu: <i>{escape(texto)}</i>\n", escape(describe(reconhecidos, livres))]

    if livres:
        # Alergia e preferencia pedem rigor diferente. Tratar as duas igual
        # enche o cardapio de aviso e a pessoa para de ler o que importa.
        termos = ", ".join(livres)
        linhas.append(
            f"\n<b>Sobre {escape(termos)}:</b> preciso saber o quanto ser rigoroso."
        )
        await reply(
            update,
            "\n".join(linhas),
            [
                [("\U0001f6a8 E alergia — me avise sempre que houver duvida", "livre_alergia")],
                [("\U0001f44c So prefiro evitar — avise se aparecer no prato", "livre_evitar")],
                [("✏️ Escrever de novo", "restr_refazer")],
            ],
            edit=False,
        )
        return

    if not reconhecidos:
        linhas.append("\nSe voce tem alguma alergia, tente escrever so o alimento, como <i>camarao</i>.")
    await reply(
        update,
        "\n".join(linhas),
        [
            [("✅ Esta certo", "restr_ok")],
            [("✏️ Escrever de novo", "restr_refazer")],
            [("\U0001f4cb Escolher numa lista", "restr_lista")],
        ],
        edit=False,
    )


async def ask_restrictions_list(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True) -> None:
    """Saida por lista, para quem prefere marcar do que escrever."""
    context.user_data[STEP] = "restricoes"
    selecionadas = set(draft(context).get("restricoes", []))
    await reply(
        update,
        "\U0001f6a8 <b>Marque o que se aplica a voce:</b>",
        restriction_buttons(selecionadas),
        edit=edit,
    )


async def ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[STEP] = "consentimento"
    await reply(
        update,
        "\U0001f512 <b>Antes de terminar</b>\n\n"
        "Preciso guardar seu nome, onde voce almoca, empresa, setor, suas restricoes "
        "e o que voce registrar que comeu.\n\n"
        "<b>Sua empresa nao ve nada disso sobre voce.</b> Nem o que voce come, nem seu objetivo, "
        "nem suas restricoes. A Apetit so enxerga quantas pessoas usam, por setor, e mesmo assim "
        "setor com menos de 5 pessoas nem aparece.\n\n"
        "Voce ve tudo com /meus_dados e apaga tudo com /excluir_dados, quando quiser.",
        [[("✅ Pode guardar", "consent_sim")], [("❌ Prefiro nao", "consent_nao")]],
        edit=edit,
    )


async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    dados = draft(context)

    # Botao de mensagem antiga continua clicavel no Telegram para sempre. Sem
    # esta guarda, tocar num "Pode guardar" de dias atras salvaria um cadastro
    # vazio por cima do que a pessoa ja tinha.
    if not all(dados.get(campo) for campo in ("nome", "unidade", "empresa", "setor")):
        ja_cadastrado = current_employee(update)
        if ja_cadastrado and ja_cadastrado.registered:
            await reply(update, "Seu cadastro ja esta completo \U0001f60a", main_menu(), edit=True)
        else:
            await ask_name(update, context, edit=True)
        return

    pessoa = Employee(
        telegram_id=tg_id(update),
        name=dados.get("nome", ""),
        apetit_unit=dados.get("unidade", ""),
        client_company=dados.get("empresa", ""),
        sector=dados.get("setor", ""),
        goal=dados.get("objetivo", ""),
        consent_accepted=True,
        restrictions=[Restriction(code, RestrictionKind.ALERGIA) for code in dados.get("restricoes", [])],
        free_restrictions=dados.get("restricoes_livres", []),
        avoid_foods=dados.get("evitar", []),
    )
    conn = db()
    try:
        save_employee(conn, pessoa)
    finally:
        conn.close()

    context.user_data.pop(STEP, None)
    context.user_data.pop("cadastro", None)

    reconhecidos = dados.get("restricoes", [])
    livres = dados.get("restricoes_livres", [])
    evitar = dados.get("evitar", [])
    if reconhecidos or livres or evitar:
        aviso = "\U0001f6a8 " + describe(reconhecidos, livres)
        if evitar:
            aviso += f" Tambem aviso quando {', '.join(evitar)} aparecer no prato."
    else:
        aviso = "Voce nao informou restricoes. Da para incluir depois em Meu cadastro."

    await reply(
        update,
        f"✅ <b>Pronto, {escape(pessoa.name)}!</b>\n\n"
        f"{aviso}\n\n"
        "Comece por <b>Montar meu prato</b>: eu te levo pela fila, categoria por categoria.",
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
# Cardapio
# --------------------------------------------------------------------------

def describe_restrictions(pessoa: Employee) -> str:
    partes = [ALLERGENS[r.allergen] for r in pessoa.restrictions]
    partes += [f"{termo} (alergia, sem conferencia automatica)" for termo in pessoa.free_restrictions]
    partes += [f"{termo} (prefiro evitar)" for termo in pessoa.avoid_foods]
    return ", ".join(partes) or "nenhuma"


def load_menu(pessoa: Employee, dia: str) -> list[dict]:
    conn = db()
    try:
        return check_menu_for_employee(
            conn,
            dia,
            pessoa.restrictions,
            unit=pessoa.apetit_unit,
            unverifiable=pessoa.free_restrictions,
            avoid_terms=pessoa.avoid_foods,
        )
    finally:
        conn.close()


def group_by_category(itens: list[dict]) -> dict[str, list[dict]]:
    grupos: dict[str, list[dict]] = {}
    for item in itens:
        grupos.setdefault(item["category"], []).append(item)
    return grupos


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    dia = today()
    itens = load_menu(pessoa, dia)
    if not itens:
        await reply(
            update,
            f"\U0001f4c5 Ainda nao tem cardapio publicado para {escape(friendly_date(dia))}.\n\n"
            "Assim que a cozinha publicar, ele aparece aqui.",
            [[("\U0001f519 Voltar", "menu")]],
            edit=edit,
        )
        return

    grupos = group_by_category(itens)
    linhas = [f"\U0001f4c5 <b>{escape(friendly_date(dia))}</b>"]
    sem_declaracao = 0
    for categoria in order_categories(grupos):
        linhas.append(f"\n<b>{escape(category_label(categoria))}</b>")
        for item in grupos[categoria]:
            check = item["check"]
            marca = VERDICT_MARK[check.verdict]
            dica = dish_hint(item["kcal"], item["ptn_g"])
            linhas.append(f"{marca} {escape(item['name'])} — <i>{escape(dica)}</i>")
            if check.verdict is Verdict.BLOQUEIO:
                linhas.append(f"    <b>Nao pode: {escape(', '.join(ALLERGENS[c] for c in check.contem))}</b>")
            elif check.verdict is Verdict.ATENCAO:
                sem_declaracao += 1

    if sem_declaracao and pessoa.restrictions:
        linhas.append(
            f"\n⚠️ <b>{sem_declaracao} prato(s) sem informacao de alergenico.</b> "
            "Nao consigo garantir que sao seguros para voce — pergunte no balcao antes de se servir."
        )

    await reply(
        update,
        "\n".join(linhas),
        [[("\U0001f37d️ Montar meu prato", "montar")], [("\U0001f519 Voltar", "menu")]],
        edit=edit,
    )


def build_suggestion(pessoa: Employee, dia: str):
    """A sugestao de porcoes do dia, ou None quando nao ha cardapio para ela.

    Fica fora do handler porque a tela mostra a sugestao e o botao de salvar
    precisa recalcular a mesma coisa. Recalcular e de proposito: guardar a
    sugestao na sessao deixaria um botao de ontem gravando o prato de ontem.
    """
    alvo = target_for(pessoa)
    # So entra na sugestao o que a pessoa pode comer: sugerir quantidade de um
    # prato bloqueado seria pior que nao sugerir nada.
    liberados = [
        {
            "code": i["item_code"],
            "category": i["category"],
            "name": i["name"],
            "kcal": i["kcal"],
            "ptn_g": i["ptn_g"],
        }
        for i in load_menu(pessoa, dia)
        if i["check"].verdict is not Verdict.BLOQUEIO
    ]
    if not liberados:
        return None
    return suggest_plate(liberados, alvo["kcal"], alvo["ptn"])


async def show_portions(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    """Quanto pegar de cada coisa, em concha e colher."""
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    dia = today()

    sugestao = build_suggestion(pessoa, dia)
    if sugestao is None:
        await show_menu(update, context, edit=edit)
        return
    if not sugestao.portions:
        await reply(
            update,
            "Ainda nao consigo sugerir quantidades: o cardapio de hoje esta sem informacao nutricional.",
            [[("\U0001f4c5 Ver cardapio", "cardapio")], [("\U0001f519 Voltar", "menu")]],
            edit=edit,
        )
        return

    linhas = [
        f"\U0001f37d️ <b>Quanto pegar hoje</b>",
        f"<i>{escape(friendly_date(dia))} · objetivo: {escape(pessoa.goal)}</i>\n",
    ]
    linhas.extend(f"• {escape(linha)}" for linha in sugestao.lines())
    linhas.append(f"\n{escape(sugestao.summary())}")
    for nota in sugestao.notes:
        linhas.append(f"\n⚠️ {escape(nota)}")
    linhas.append(
        "\n<i>E uma sugestao com base no que tem hoje e no objetivo que voce escolheu. "
        "Quem define quantidade individual e o nutricionista.</i>"
    )

    await reply(
        update,
        "\n".join(linhas),
        [
            [("✅ Vou pegar isso — registrar", "registrar_sugestao")],
            [("\U0001f37d️ Montar do meu jeito", "montar")],
            [("\U0001f4c5 Ver cardapio", "cardapio"), ("\U0001f519 Voltar", "menu")],
        ],
        edit=edit,
    )


# --------------------------------------------------------------------------
# Montar o prato, categoria por categoria
# --------------------------------------------------------------------------

def tray(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.user_data.setdefault(TRAY, [])


def flow_categories(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.user_data.get("monta_ordem", [])


async def start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    itens = load_menu(pessoa, today())
    if not itens:
        await show_menu(update, context, edit=edit)
        return
    grupos = group_by_category(itens)
    context.user_data["monta_ordem"] = order_categories(grupos)
    context.user_data[FLOW] = 0
    context.user_data[TRAY] = []
    await show_flow_step(update, context, edit=edit)


async def show_flow_step(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    ordem = flow_categories(context)
    indice = context.user_data.get(FLOW, 0)
    if indice >= len(ordem):
        await show_plate_summary(update, context, edit=edit)
        return

    categoria = ordem[indice]
    itens = [i for i in load_menu(pessoa, today()) if i["category"] == categoria]
    escolhidos = set(tray(context))

    linhas = [
        f"<b>Passo {indice + 1} de {len(ordem)}</b> · {escape(category_label(categoria))}",
        "",
        "Toque no que voce pegou:",
    ]
    botoes: list[list[tuple[str, str]]] = []
    for item in itens:
        check = item["check"]
        dica = dish_hint(item["kcal"], item["ptn_g"])
        if check.verdict is Verdict.BLOQUEIO:
            alergenicos = ", ".join(ALLERGENS[c] for c in check.contem)
            linhas.append(f"⛔ <s>{escape(item['name'])}</s> — <b>contem {escape(alergenicos)}</b>")
            continue
        marca = "☑️" if item["item_code"] in escolhidos else "⬜"
        atencao = " ⚠️" if check.verdict is Verdict.ATENCAO else ""
        linhas.append(f"{marca} {escape(item['name'])} — <i>{escape(dica)}</i>{atencao}")
        botoes.append([(f"{marca} {item['name'][:30]}", f"pick:{item['item_code']}")])

    if any(i["check"].verdict is Verdict.ATENCAO for i in itens) and pessoa.restrictions:
        linhas.append("\n⚠️ = sem informacao de alergenico. Pergunte no balcao.")

    navegacao = []
    if indice > 0:
        navegacao.append(("\U0001f519 Voltar", "flow_prev"))
    navegacao.append(("Pular ➡️" if not escolhidos else "Proximo ➡️", "flow_next"))
    botoes.append(navegacao)
    botoes.append([("✋ Terminei de montar", "flow_fim")])

    await reply(update, "\n".join(linhas), botoes, edit=edit)


def plate_totals(pessoa: Employee, codigos: list[str]) -> tuple[dict, list[str], set[str]]:
    """Soma o prato e conta o que nao da para somar.

    `sem_macro` e `com_macro` viajam junto do total de proposito: sem eles,
    quem le o total nao tem como saber que ele e piso e nao total.
    """
    conn = db()
    try:
        linhas = {l["item_code"]: l for l in menu_for_date(conn, today(), unit=pessoa.apetit_unit)}
    finally:
        conn.close()
    totais = {"kcal": 0.0, "cho": 0.0, "lip": 0.0, "ptn": 0.0, "sem_macro": 0, "com_macro": 0}
    nomes: list[str] = []
    categorias: set[str] = set()
    for code in codigos:
        linha = linhas.get(code)
        if not linha:
            continue
        # kcal e proteina sao o par que o app usa para dizer qualquer coisa.
        # Faltando um dos dois, o item nao entra na conta — somar so a metade
        # daria um numero que parece total e nao e.
        if linha["kcal"] is None or linha["ptn_g"] is None:
            totais["sem_macro"] += 1
        else:
            totais["com_macro"] += 1
            totais["kcal"] += linha["kcal"]
            totais["cho"] += linha["cho_g"] or 0
            totais["lip"] += linha["lip_g"] or 0
            totais["ptn"] += linha["ptn_g"]
        nomes.append(linha["name"])
        categorias.add(linha["category"])
    return totais, nomes, categorias


async def show_plate_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = True) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    codigos = tray(context)
    if not codigos:
        await reply(
            update,
            "Seu prato esta vazio.\n\nQuer comecar de novo?",
            [[("\U0001f37d️ Montar meu prato", "montar")], [("\U0001f519 Voltar", "menu")]],
            edit=edit,
        )
        return

    alvo = target_for(pessoa)
    totais, nomes, categorias = plate_totals(pessoa, codigos)
    leitura = plate_reading(
        totais["kcal"],
        totais["ptn"],
        alvo["kcal"],
        alvo["ptn"],
        bool(categorias & FRESH_CATEGORIES),
        unknown_items=totais["sem_macro"],
        known_items=totais["com_macro"],
    )

    linhas = ["\U0001f37d️ <b>Seu prato</b>\n"]
    linhas.extend(f"• {escape(nome)}" for nome in nomes)
    linhas.append("")
    linhas.extend(escape(frase) for frase in leitura)
    if totais["com_macro"]:
        prefixo = "no minimo " if totais["sem_macro"] else ""
        linhas.append(
            f"\n<i>{prefixo}{totais['kcal']:.0f} kcal · {totais['cho']:.0f} g carboidrato · "
            f"{totais['lip']:.0f} g gordura · {totais['ptn']:.0f} g proteina</i>"
        )

    botoes = [
        [("✅ Registrar este prato", "registrar")],
        [("✏️ Mudar o prato", "montar")],
        [("⭐ Guardar um prato", "fav_lista"), ("\U0001f519 Voltar", "menu")],
    ]
    await reply(update, "\n".join(linhas), botoes, edit=edit)


async def save_meal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    pessoa: Employee,
    codigos: list[str],
    resumo: list[str],
    source: str,
) -> None:
    """Grava a refeicao do dia e confirma o que ficou registrado.

    `resumo` e o prato em texto: a confirmacao repete o que foi guardado, para
    a pessoa conferir na hora em vez de descobrir depois que salvou errado.
    """
    dia = today()
    alvo = target_for(pessoa)
    conn = db()
    try:
        log_consumption(conn, pessoa.telegram_id, dia, codigos, source=source)
        ganhas = score_day(conn, pessoa.telegram_id, dia, protein_target_g=alvo["ptn"])
        ganhas += score_week(conn, pessoa.telegram_id, week_start(dia))
        totais = consumption_totals(conn, pessoa.telegram_id, dia)
        dias = {linha["service_date"] for linha in consumption_history(conn, pessoa.telegram_id, 60)}
    finally:
        conn.close()

    context.user_data[TRAY] = []
    context.user_data.pop(FLOW, None)

    inicio = week_start(dia)
    dias_semana = sum(1 for d in dias if inicio <= d <= dia)

    linhas = [f"✅ <b>Refeicao de {escape(friendly_date(dia))} registrada</b>\n"]
    linhas.extend(f"• {escape(item)}" for item in resumo)
    sem_macro = totais.get("sem_macro") or 0
    if totais.get("com_macro"):
        prefixo = "no minimo " if sem_macro else ""
        linhas.append(
            f"\n<i>{prefixo}{totais['kcal']:.0f} kcal · {totais['ptn_g']:.0f} g de proteina</i>"
        )
    if sem_macro:
        linhas.append(
            f"⚠️ {sem_macro} item(ns) sem informacao nutricional — "
            "ainda nao da para fechar o total do dia."
        )
    linhas.append(f"\n{escape(week_summary(dias_semana))}")
    if ganhas:
        linhas.append("\n\U0001f331 <b>Voce conquistou hoje:</b>")
        linhas.extend(f"• {escape(regra.label)}" for regra in ganhas)
    linhas.append("\nIsso fica no seu <b>Meu dia</b>. Registrar de novo hoje substitui este.")

    # Convite para avaliar so aparece se ela ainda nao avaliou hoje: pedir de
    # novo o que a pessoa ja respondeu e o caminho mais rapido para ela parar
    # de responder.
    conn = db()
    try:
        ja_avaliou = rating_for(conn, pessoa.telegram_id, dia) is not None
    finally:
        conn.close()

    botoes = []
    if not ja_avaliou:
        linhas.append("\nComo foi o refeitorio hoje? Leva tres toques.")
        botoes.append([("⭐ Avaliar o refeitorio", "avaliar")])
    botoes.append([("\U0001f4ca Ver meu historico", "meu_dia")])
    botoes.append([("\U0001f519 Voltar", "menu")])

    await reply(update, "\n".join(linhas), botoes, edit=True)


async def register_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pessoa = await require_registration(update, context, edit=True)
    if not pessoa:
        return
    codigos = tray(context)
    if not codigos:
        await show_plate_summary(update, context, edit=True)
        return
    _, nomes, _ = plate_totals(pessoa, codigos)
    await save_meal(update, context, pessoa, codigos, nomes, source="montado")


async def register_suggestion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Salva a sugestao de porcoes como a refeicao do dia, num toque so.

    A sugestao e recalculada aqui em vez de vir da sessao: o botao continua
    clicavel amanha, e amanha o cardapio e outro.
    """
    pessoa = await require_registration(update, context, edit=True)
    if not pessoa:
        return
    sugestao = build_suggestion(pessoa, today())
    if sugestao is None or not sugestao.portions:
        await show_portions(update, context, edit=True)
        return
    await save_meal(
        update, context, pessoa, sugestao.item_codes(), sugestao.lines(), source="sugestao"
    )


# --------------------------------------------------------------------------
# Avaliacao do refeitorio
# --------------------------------------------------------------------------
#
# Esta e a unica tela do app cujo dado a Apetit le. Por isso ela avisa, antes
# de qualquer pergunta, que a resposta vai sem nome — quem nao sabe que esta
# protegido responde como se nao estivesse.

AVALIACAO = "avaliacao"

NOTAS = ((3, "\U0001f60b Boa"), (2, "\U0001f610 Regular"), (1, "\U0001f61e Ruim"))


def rascunho_avaliacao(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault(AVALIACAO, {})


async def ask_food(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    context.user_data[AVALIACAO] = {}
    await reply(
        update,
        "⭐ <b>Como foi o almoco de hoje?</b>\n"
        f"<i>{escape(pessoa.apetit_unit)} · {escape(friendly_date(today()))}</i>\n\n"
        "<b>A comida estava boa?</b>\n\n"
        "<i>Sua resposta vai sem o seu nome. A Apetit ve como o refeitorio esta indo, "
        "nunca quem disse o que — nem a sua empresa, nem o seu setor.</i>",
        [[(rotulo, f"aval_comida:{nota}")] for nota, rotulo in NOTAS] + [[("\U0001f519 Voltar", "menu")]],
        edit=edit,
    )


async def ask_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(
        update,
        "⭐ <b>E o atendimento?</b>\n\n"
        "<i>Como voce foi tratado no balcao e na fila.</i>",
        [[(rotulo, f"aval_atend:{nota}")] for nota, rotulo in NOTAS] + [[("← Voltar", "avaliar")]],
        edit=True,
    )


async def ask_missing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply(
        update,
        "⭐ <b>Faltou alguma coisa?</b>",
        [
            [("\U0001f44d Nao, estava tudo la", "aval_faltou:nao")],
            [("\U0001f44e Sim, faltou", "aval_faltou:sim")],
        ],
        edit=True,
    )


async def ask_missing_what(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    marcados = set(rascunho_avaliacao(context).get("tags", []))
    botoes = [
        [(("☑️ " if code in marcados else "⬜ ") + rotulo, f"aval_tag:{code}")]
        for code, rotulo in MISSING_TAGS.items()
    ]
    botoes.append([("Continuar ➡️", "aval_comentario")])
    await reply(update, "⭐ <b>O que faltou?</b>\n\nPode marcar mais de um.", botoes, edit=True)


async def ask_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[STEP] = "avaliacao_comentario"
    await reply(
        update,
        "⭐ <b>Quer contar mais alguma coisa?</b>\n\n"
        "Escreva o que quiser sobre o refeitorio de hoje, ou toque em pular.\n\n"
        "<i>O texto chega a Apetit sem o seu nome. Ainda assim, evite escrever algo "
        "que so voce diria — e evite citar colega pelo nome.</i>",
        [[("Pular e enviar", "aval_enviar")]],
        edit=True,
    )


async def submit_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pessoa = await require_registration(update, context, edit=True)
    if not pessoa:
        return
    dados = rascunho_avaliacao(context)
    avaliacao = Rating(
        apetit_unit=pessoa.apetit_unit,
        service_date=today(),
        food=dados.get("comida"),
        service=dados.get("atendimento"),
        missing=bool(dados.get("faltou")),
        tags=dados.get("tags", []),
        comment=dados.get("comentario", ""),
    )
    context.user_data.pop(AVALIACAO, None)
    context.user_data.pop(STEP, None)

    if avaliacao.empty:
        await reply(update, "Nao registrei nada — voce nao respondeu nenhuma pergunta.", main_menu(), edit=True)
        return

    conn = db()
    try:
        save_rating(conn, pessoa.telegram_id, avaliacao)
    finally:
        conn.close()

    linhas = ["✅ <b>Obrigado!</b>\n", "Sua avaliacao de hoje foi registrada."]
    if avaliacao.food is not None:
        linhas.append(f"\n<b>Comida:</b> {escape(SCALE[avaliacao.food])}")
    if avaliacao.service is not None:
        linhas.append(f"<b>Atendimento:</b> {escape(SCALE[avaliacao.service])}")
    if avaliacao.tags:
        faltou = ", ".join(MISSING_TAGS[t] for t in avaliacao.tags)
        linhas.append(f"<b>Faltou:</b> {escape(faltou)}")
    linhas.append(
        "\n<i>Isso entra na media do refeitorio, sem o seu nome. "
        "Se quiser mudar, e so avaliar de novo hoje — a nova substitui esta.</i>"
    )
    await reply(update, "\n".join(linhas), main_menu(), edit=True)


# --------------------------------------------------------------------------
# Consultas do funcionario
# --------------------------------------------------------------------------

def history_line(linha) -> str:
    """Uma linha do historico: o prato e quanto foi pego, em medida de fila.

    "Feijao preto — 2 conchas" diz mais para quem comeu do que "Feijao preto",
    e e a mesma linguagem da sugestao de porcao.
    """
    nome = clean_dish_name(linha["name"])
    categoria = (linha["category"] or "").upper()
    if not categoria:
        return nome
    return f"{nome} — {measure_label(categoria, linha['quantity'])}"


async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    dia = today()
    conn = db()
    try:
        dias = history_by_day(conn, pessoa.telegram_id, days=14)
    finally:
        conn.close()

    alvo = target_for(pessoa)
    hoje = next((d for d in dias if d.service_date == dia), None)

    linhas = ["\U0001f4ca <b>Meu dia</b>\n"]
    if hoje:
        categorias = {(linha["category"] or "").upper() for linha in hoje.items}
        leitura = plate_reading(
            hoje.kcal,
            hoje.ptn_g,
            alvo["kcal"],
            alvo["ptn"],
            bool(categorias & FRESH_CATEGORIES),
            unknown_items=hoje.unknown_items,
            known_items=hoje.known_items,
        )
        linhas.extend(escape(frase) for frase in leitura)
        linhas.append("")
        linhas.extend(f"• {escape(history_line(linha))}" for linha in hoje.items)
        if hoje.known_items:
            prefixo = "no minimo " if hoje.incomplete else ""
            linhas.append(f"\n<i>{prefixo}{hoje.kcal:.0f} kcal · {hoje.ptn_g:.0f} g de proteina</i>")
    else:
        linhas.append(
            "Voce ainda nao registrou o almoco de hoje.\n\n"
            "Toque em <b>Quanto pegar hoje</b> e depois em <b>Vou pegar isso</b> — "
            "assim fica guardado no seu historico."
        )

    anteriores = [d for d in dias if d.service_date != dia]
    if anteriores:
        linhas.append("\n\U0001f4c6 <b>Seu historico</b>")
        for registro in anteriores[:7]:
            if registro.known_items:
                prefixo = "min. " if registro.incomplete else ""
                resumo = f" — {prefixo}{registro.kcal:.0f} kcal · {registro.ptn_g:.0f} g ptn"
            else:
                resumo = " — sem informacao nutricional"
            linhas.append(f"\n<i>{escape(friendly_date(registro.service_date))}</i>{resumo}")
            linhas.extend(f"• {escape(history_line(linha))}" for linha in registro.items)
        if len(anteriores) > 7:
            linhas.append(f"\n<i>… e mais {len(anteriores) - 7} dia(s) registrados.</i>")

    botoes = [
        [("\U0001f37d️ Quanto pegar hoje", "quanto")] if not hoje else [("✏️ Corrigir o de hoje", "montar")],
        [("\U0001f519 Voltar", "menu")],
    ]
    await reply(update, "\n".join(linhas), botoes, edit=edit)


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
            "⭐ <b>Voce ainda nao guardou nenhum prato.</b>\n\n"
            "Depois de montar o prato, toque em <b>Guardar um prato</b>. "
            "Quando ele voltar ao cardapio, eu te aviso.",
            [[("\U0001f37d️ Montar meu prato", "montar")], [("\U0001f519 Voltar", "menu")]],
            edit=edit,
        )
        return
    linhas = ["⭐ <b>Pratos que voce guardou</b>\n", "Eu te aviso quando algum voltar ao cardapio.\n"]
    linhas.extend(f"• {escape(linha['name'])}" for linha in lista)
    botoes = [[(f"Remover {linha['name'][:24]}", f"unfav:{linha['item_code']}")] for linha in lista]
    botoes.append([("\U0001f519 Voltar", "menu")])
    await reply(update, "\n".join(linhas), botoes, edit=edit)


async def show_favorite_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pessoa = await require_registration(update, context, edit=True)
    if not pessoa:
        return
    codigos = tray(context)
    if not codigos:
        await show_favorites(update, context, edit=True)
        return
    _, nomes, _ = plate_totals(pessoa, codigos)
    botoes = [[(nome[:32], f"fav:{code}")] for code, nome in zip(codigos, nomes)]
    botoes.append([("\U0001f519 Voltar", "flow_fim")])
    await reply(update, "⭐ <b>Qual prato voce quer guardar?</b>", botoes, edit=True)


async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    dia = today()
    inicio = week_start(dia)
    conn = db()
    try:
        pontos = total_points(conn, pessoa.telegram_id)
        extrato = points_breakdown(conn, pessoa.telegram_id)
        dias = {linha["service_date"] for linha in consumption_history(conn, pessoa.telegram_id, 60)}
    finally:
        conn.close()

    dias_semana = sum(1 for d in dias if inicio <= d <= dia)
    rotulos = {regra.code: regra.label for regra in RULES}

    linhas = ["\U0001f4c8 <b>Meu progresso</b>\n", escape(week_summary(dias_semana))]
    if extrato:
        linhas.append("\n<b>Minhas conquistas:</b>")
        linhas.extend(
            f"• {escape(rotulos.get(linha['rule_code'], linha['rule_code']))} — {linha['vezes']}x"
            for linha in extrato
        )
        linhas.append(f"\n<b>{pontos} pontos</b> acumulados.")
    else:
        linhas.append("\nRegistre um almoco para comecar a acompanhar.")
    linhas.append(
        "\n<i>Isso e so seu. Nao existe ranking, e ninguem compara voce com colega nenhum.</i>"
    )
    await reply(update, "\n".join(linhas), main_menu(), edit=edit)


async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = await require_registration(update, context, edit=edit)
    if not pessoa:
        return
    restricoes = describe_restrictions(pessoa)
    await reply(
        update,
        "\U0001f464 <b>Meu cadastro</b>\n\n"
        f"<b>Nome:</b> {escape(pessoa.name)}\n"
        f"<b>Refeitorio:</b> {escape(pessoa.apetit_unit)}\n"
        f"<b>Empresa:</b> {escape(pessoa.client_company)}\n"
        f"<b>Setor:</b> {escape(pessoa.sector)}\n"
        f"<b>Objetivo:</b> {escape(pessoa.goal)}\n"
        f"<b>Restricoes:</b> {escape(restricoes)}",
        [[("✏️ Refazer cadastro", "recadastrar")], [("\U0001f512 Meus dados", "meus_dados")], [("\U0001f519 Voltar", "menu")]],
        edit=edit,
    )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    await reply(
        update,
        "❓ <b>Como usar</b>\n\n"
        "<b>Quanto pegar hoje</b> — eu digo quantas conchas e colheres pegar de cada coisa "
        "para chegar no seu objetivo, com o cardapio de hoje. Se voce pegar o que eu sugeri, "
        "toque em <b>Vou pegar isso</b> e ja fica guardado.\n\n"
        "<b>Montar meu prato</b> — eu te levo pela fila, uma categoria por vez. "
        "Voce toca no que pegou e eu mostro como ficou o prato.\n\n"
        "<b>Cardapio de hoje</b> — o que tem hoje, com aviso do que voce nao pode comer.\n\n"
        "<b>Meu dia</b> — o que voce registrou hoje e nos dias anteriores, com quanto pegou "
        "de cada coisa. Registrar de novo no mesmo dia substitui o registro anterior.\n\n"
        "<b>Favoritos</b> — pratos que voce gostou. Aviso quando voltarem.\n\n"
        "<b>Meu progresso</b> — sua sequencia e suas conquistas. So suas.\n\n"
        "<b>Avaliar o refeitorio</b> — tres toques dizendo como foi a comida, o "
        "atendimento e se faltou algo. <b>Vai sem o seu nome:</b> a Apetit ve como o "
        "refeitorio esta indo, nunca quem disse o que — nem a sua empresa, nem o seu "
        "setor. E a unica coisa do app que sai de voce.\n\n"
        "<b>Sobre os simbolos</b>\n"
        "⛔ contem algo que voce marcou como alergia\n"
        "⚠️ sem informacao de alergenico — pergunte no balcao\n"
        "✅ conferido e liberado para voce\n\n"
        "Seus dados sao seus: /meus_dados mostra tudo, /excluir_dados apaga tudo. "
        "Sua empresa nao ve nada disso sobre voce.",
        main_menu(),
        edit=edit,
    )


async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    pessoa = current_employee(update)
    if not pessoa:
        await reply(update, "Nao encontrei cadastro seu. Envie /start para comecar.", edit=edit)
        return
    conn = db()
    try:
        historico = consumption_history(conn, pessoa.telegram_id, 200)
        guardados = favorites(conn, pessoa.telegram_id)
        pontos = total_points(conn, pessoa.telegram_id)
        avaliacoes = my_ratings(conn, pessoa.telegram_id, 200)
    finally:
        conn.close()
    restricoes = describe_restrictions(pessoa)
    dias = len({linha["service_date"] for linha in historico})
    await reply(
        update,
        "\U0001f512 <b>Tudo o que eu guardo sobre voce</b>\n\n"
        f"<b>Nome:</b> {escape(pessoa.name)}\n"
        f"<b>Refeitorio:</b> {escape(pessoa.apetit_unit)}\n"
        f"<b>Empresa:</b> {escape(pessoa.client_company)}\n"
        f"<b>Setor:</b> {escape(pessoa.sector)}\n"
        f"<b>Objetivo:</b> {escape(pessoa.goal)}\n"
        f"<b>Restricoes:</b> {escape(restricoes)}\n"
        f"<b>Aceite:</b> {escape(pessoa.consented_at or 'nao informado')}\n\n"
        f"<b>Dias com refeicao registrada:</b> {dias}\n"
        f"<b>Pratos guardados:</b> {len(guardados)}\n"
        f"<b>Pontos:</b> {pontos}\n"
        f"<b>Avaliacoes do refeitorio:</b> {len(avaliacoes)}\n\n"
        "<b>Sua empresa nao ve nada disso sobre voce.</b>\n"
        "A unica coisa que sai daqui e a avaliacao do refeitorio — e ela vai "
        "sem o seu nome, sem a sua empresa e sem o seu setor, junto com a de "
        "todo mundo. A Apetit ve como o refeitorio esta indo, nunca quem disse o que.\n\n"
        "Para apagar tudo: /excluir_dados",
        [[("\U0001f519 Voltar", "menu")]],
        edit=edit,
    )


async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not current_employee(update):
        await reply(update, "Nao encontrei dados seus para excluir.")
        return
    await reply(
        update,
        "⚠️ <b>Apagar tudo?</b>\n\n"
        "Vou remover seu cadastro, restricoes, historico, pratos guardados e progresso. "
        "Isso nao tem volta.",
        [[("Sim, apagar tudo", "del_sim")], [("Nao, cancelar", "del_nao")]],
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
        registro = agrupado.setdefault(linha["item_name"], {"vezes": 0, "detail": linha["detail"]})
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


async def show_coverage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Quanto do cardapio ja sustenta o alerta de alergia."""
    if not is_admin(tg_id(update)):
        await deny_admin(update, "ver a cobertura de alergenicos")
        return
    conn = db()
    try:
        cobertura = allergen_coverage(conn)
    finally:
        conn.close()

    linhas = ["\U0001f9ea <b>Cobertura de alergenicos</b>\n"]
    linhas.append(escape(coverage_summary(cobertura["total"], cobertura["completos"], cobertura["parciais"])))
    if cobertura["faltando"]:
        linhas.append("\n<b>Declarar primeiro (aparecem mais no cardapio):</b>")
        linhas.extend(f"• {escape(nome)}" for _, nome, _ in cobertura["faltando"][:10])
        linhas.append(
            "\n<i>Para declarar em lote: scripts/alergenicos.py --exportar, "
            "preencher no Excel e --importar.</i>"
        )
    await update.effective_message.reply_text("\n".join(linhas), parse_mode=ParseMode.HTML)


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


def _linha_relatorio(relatorio) -> str:
    if relatorio.suppressed:
        return f"- <b>{escape(relatorio.apetit_unit)}</b>: suprimido ({escape(relatorio.reason)})"
    partes = [f"{relatorio.total} avaliacoes"]
    if relatorio.food_good_pct is not None:
        partes.append(f"comida boa {relatorio.food_good_pct:.0f}%")
    if relatorio.service_good_pct is not None:
        partes.append(f"atendimento bom {relatorio.service_good_pct:.0f}%")
    if relatorio.missing_pct:
        partes.append(f"faltou algo em {relatorio.missing_pct:.0f}%")
    return f"- <b>{escape(relatorio.apetit_unit)}</b>: {escape(' · '.join(partes))}"


async def show_service_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Como cada refeitorio esta sendo avaliado. Sempre agregado, nunca por pessoa.

    Com `/atendimento <unidade>` abre o detalhe daquele refeitorio: o que mais
    faltou, a serie semanal e os comentarios.
    """
    if not is_admin(tg_id(update)):
        await deny_admin(update, "ver o relatorio de atendimento")
        return

    fim = today()
    inicio = (date.fromisoformat(fim) - timedelta(days=29)).isoformat()
    unidade = " ".join(context.args).strip() if context.args else ""

    conn = db()
    try:
        if unidade:
            relatorio = unit_report(conn, unidade, inicio, fim)
            serie = unit_trend(conn, unidade, weeks=6, today=fim)
            comentarios = unit_comments(conn, unidade, inicio, fim)
        else:
            relatorios = all_unit_reports(conn, inicio, fim)
    finally:
        conn.close()

    periodo = f"{friendly_date(inicio)} a {friendly_date(fim)}"

    if not unidade:
        if not relatorios:
            await update.effective_message.reply_text("Ainda nao ha avaliacoes de refeitorio no periodo.")
            return
        linhas = [f"\U0001f4ca <b>Atendimento por refeitorio</b>\n<i>{escape(periodo)}</i>\n"]
        linhas.extend(_linha_relatorio(r) for r in relatorios)
        linhas.append(
            "\n<i>Ordenado por quem precisa de atencao primeiro. "
            "Use /atendimento &lt;refeitorio&gt; para o detalhe.</i>"
        )
        linhas.append(
            "<i>Avaliacao nao carrega nome, empresa nem setor de quem respondeu.</i>"
        )
        await update.effective_message.reply_text("\n".join(linhas), parse_mode=ParseMode.HTML)
        return

    linhas = [f"\U0001f4ca <b>{escape(unidade)}</b>\n<i>{escape(periodo)}</i>\n"]
    if relatorio.suppressed:
        linhas.append(f"Sem dado suficiente: {escape(relatorio.reason)}.")
        linhas.append(
            "\n<i>Abaixo do minimo a media nao sai: com poucas avaliacoes ela vira "
            "a opiniao identificavel de uma pessoa.</i>"
        )
        await update.effective_message.reply_text("\n".join(linhas), parse_mode=ParseMode.HTML)
        return

    linhas.append(_linha_relatorio(relatorio).split(": ", 1)[1])
    if relatorio.tags:
        linhas.append("\n<b>O que mais faltou:</b>")
        linhas.extend(f"• {escape(MISSING_TAGS.get(tag, tag))} — {total}x" for tag, total in relatorio.tags)

    visiveis = [s for s in serie if not s.suppressed]
    if visiveis:
        linhas.append("\n<b>Semana a semana (comida boa):</b>")
        for semana in serie:
            if semana.suppressed:
                linhas.append(f"• {escape(friendly_date(semana.period_start))}: sem dado suficiente")
            else:
                linhas.append(
                    f"• {escape(friendly_date(semana.period_start))}: "
                    f"{semana.food_good_pct:.0f}% ({semana.total} avaliacoes)"
                )

    if comentarios:
        linhas.append("\n<b>O que escreveram:</b>")
        linhas.extend(f"• <i>{escape(c)}</i>" for c in comentarios[:15])
        if len(comentarios) > 15:
            linhas.append(f"<i>… e mais {len(comentarios) - 15} comentario(s).</i>")

    linhas.append("\n<i>Nenhuma linha aqui carrega quem respondeu.</i>")
    await update.effective_message.reply_text("\n".join(linhas), parse_mode=ParseMode.HTML)


async def notify_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        pratos = "\n".join(
            f"• {escape(l['item_name'])} — {escape(friendly_date(l['service_date']))}" for l in linhas
        )
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=f"⭐ <b>Um prato que voce guardou esta voltando!</b>\n\n{pratos}",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard([[("\U0001f4c5 Ver cardapio", "cardapio")]]),
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
        await reply(update, f"\U0001f37d️ <b>Ola, {escape(pessoa.name)}!</b>", main_menu())
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
            await reply(update, "Toque numa opcao \U0001f447", main_menu())
        else:
            await ask_name(update, context)
        return

    if step == "avaliacao_comentario":
        # Comentario e o unico texto livre que sai do app para a Apetit. Cortar
        # no limite evita que um desabafo longo vire dado identificavel por
        # volume de detalhe — e cabe na tela de quem vai ler.
        rascunho_avaliacao(context)["comentario"] = texto[:500]
        await submit_rating(update, context)
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
    elif step in ("restricoes_texto", "restricoes_confirma"):
        await confirm_restrictions(update, context, texto)
    else:
        await reply(update, "Use os botoes acima para continuar \U0001f60a")


async def handle_registration_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> bool:
    """Botoes de unidade, empresa e setor. Devolve se tratou o callback."""
    mapa = {
        "unit": ("_unidades", "unidade", ask_company, "Qual e o <b>refeitorio</b> onde voce almoca?"),
        "comp": ("_empresas", "empresa", ask_sector, "Em qual <b>empresa</b> voce trabalha?"),
        "setor": ("_setores", "setor", ask_goal, "Em qual <b>setor</b> voce trabalha?"),
    }
    for prefixo, (cache, campo, proximo, pergunta) in mapa.items():
        if not data.startswith(f"{prefixo}:"):
            continue
        escolha = data.removeprefix(f"{prefixo}:")
        dados = draft(context)
        if escolha == "outro":
            context.user_data[STEP] = campo
            await reply(update, pergunta, edit=True)
            return True
        opcoes = dados.get(cache, [])
        indice = int(escolha) if escolha.isdigit() else -1
        if 0 <= indice < len(opcoes):
            dados[campo] = opcoes[indice]
            await proximo(update, context, edit=True)
        return True
    return False


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if await handle_registration_choice(update, context, data):
        return
    if data in GOALS:
        draft(context)["objetivo"] = GOALS[data]
        await ask_restrictions(update, context, edit=True)
        return
    if data.startswith("restr:"):
        selecionadas = set(draft(context).get("restricoes", []))
        selecionadas.symmetric_difference_update({data.removeprefix("restr:")})
        draft(context)["restricoes"] = sorted(selecionadas)
        await ask_restrictions(update, context, edit=True)
        return
    if data == "restr_nenhuma":
        dados = draft(context)
        dados["restricoes"] = []
        dados["restricoes_livres"] = []
        await ask_consent(update, context, edit=True)
        return
    if data in ("livre_alergia", "livre_evitar"):
        dados = draft(context)
        termos = dados.get("restricoes_livres", [])
        if data == "livre_evitar":
            dados["evitar"] = termos
            dados["restricoes_livres"] = []
            aviso = (
                "\U0001f44c Anotado. Vou avisar quando " + escape(", ".join(termos))
                + " aparecer no nome do prato."
            )
        else:
            dados["evitar"] = []
            aviso = (
                "\U0001f6a8 Anotado como alergia. Vou avisar sempre que nao conseguir "
                "confirmar que o prato esta livre de " + escape(", ".join(termos)) + "."
            )
        await reply(update, aviso, [[("Continuar", "restr_ok")]], edit=True)
        return
    if data == "restr_lista":
        await ask_restrictions_list(update, context, edit=True)
        return
    if data == "restr_refazer":
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
            "\U0001f512 Sem o aceite eu nao consigo guardar nada nem te acompanhar.\n\n"
            "Se mudar de ideia, envie /start.",
            edit=True,
        )
        return
    if data == "recadastrar":
        context.user_data.clear()
        await ask_name(update, context, edit=True)
        return

    if data == "menu":
        await reply(update, "O que voce quer fazer?", main_menu(), edit=True)
        return
    if data == "cardapio":
        await show_menu(update, context, edit=True)
        return
    if data == "quanto":
        await show_portions(update, context, edit=True)
        return
    if data == "montar":
        await start_flow(update, context, edit=True)
        return
    if data.startswith("pick:"):
        code = data.removeprefix("pick:")
        bandeja = tray(context)
        if code in bandeja:
            bandeja.remove(code)
        else:
            bandeja.append(code)
        await show_flow_step(update, context)
        return
    if data == "flow_next":
        context.user_data[FLOW] = context.user_data.get(FLOW, 0) + 1
        await show_flow_step(update, context)
        return
    if data == "flow_prev":
        context.user_data[FLOW] = max(0, context.user_data.get(FLOW, 0) - 1)
        await show_flow_step(update, context)
        return
    if data == "flow_fim":
        await show_plate_summary(update, context, edit=True)
        return
    if data == "registrar":
        await register_meal(update, context)
        return
    if data == "registrar_sugestao":
        await register_suggestion(update, context)
        return
    if data == "fav_lista":
        await show_favorite_picker(update, context)
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
                main_menu(),
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
    if data == "avaliar":
        await ask_food(update, context, edit=True)
        return
    if data.startswith("aval_comida:"):
        rascunho_avaliacao(context)["comida"] = int(data.split(":", 1)[1])
        await ask_service(update, context)
        return
    if data.startswith("aval_atend:"):
        rascunho_avaliacao(context)["atendimento"] = int(data.split(":", 1)[1])
        await ask_missing(update, context)
        return
    if data.startswith("aval_faltou:"):
        faltou = data.split(":", 1)[1] == "sim"
        rascunho_avaliacao(context)["faltou"] = faltou
        if faltou:
            await ask_missing_what(update, context)
        else:
            await ask_comment(update, context)
        return
    if data.startswith("aval_tag:"):
        code = data.split(":", 1)[1]
        if code in MISSING_TAGS:
            tags = rascunho_avaliacao(context).setdefault("tags", [])
            if code in tags:
                tags.remove(code)
            else:
                tags.append(code)
        await ask_missing_what(update, context)
        return
    if data == "aval_comentario":
        await ask_comment(update, context)
        return
    if data == "aval_enviar":
        await submit_rating(update, context)
        return

    if data == "meu_dia":
        await show_day(update, context, edit=True)
        return
    if data == "favoritos":
        await show_favorites(update, context, edit=True)
        return
    if data == "progresso":
        await show_progress(update, context, edit=True)
        return
    if data == "perfil":
        await show_profile(update, context, edit=True)
        return
    if data == "ajuda":
        await show_help(update, context, edit=True)
        return
    if data == "meus_dados":
        await show_my_data(update, context, edit=True)
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

    await reply(update, "O que voce quer fazer?", main_menu(), edit=True)


async def register_commands(app: Application) -> None:
    """Publica o menu de comandos, para o Telegram mostrar as opcoes sozinho."""
    await app.bot.set_my_commands(COMMANDS)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Defina TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.")
    db().close()

    app = Application.builder().token(token).post_init(register_commands).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ajuda", show_help))
    app.add_handler(CommandHandler("recadastrar", restart_registration))
    app.add_handler(CommandHandler("cardapio", show_menu))
    app.add_handler(CommandHandler("montar", start_flow))
    app.add_handler(CommandHandler("quanto_pegar", show_portions))
    app.add_handler(CommandHandler("avaliar", ask_food))
    app.add_handler(CommandHandler("meu_dia", show_day))
    app.add_handler(CommandHandler("favoritos", show_favorites))
    app.add_handler(CommandHandler("progresso", show_progress))
    app.add_handler(CommandHandler("meus_dados", show_my_data))
    app.add_handler(CommandHandler("excluir_dados", confirm_delete))
    app.add_handler(CommandHandler("pendencias", show_pending))
    app.add_handler(CommandHandler("alergenico", declare_allergen))
    app.add_handler(CommandHandler("cobertura", show_coverage))
    app.add_handler(CommandHandler("relatorio", show_report))
    app.add_handler(CommandHandler("atendimento", show_service_report))
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
