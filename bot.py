import logging
import os
import sqlite3
import unicodedata
from datetime import date, datetime
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


load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DEFAULT_USER_NAME = os.getenv("APETIT_USER_NAME", "Mariana")
REGISTRATION_STEP = "registration_step"
DB_PATH = Path(os.getenv("APETIT_DB_PATH", "apetit.db"))
ADMIN_TELEGRAM_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().isdigit()
}

ORDER_ACTIONS = {
    "menu_today",
    "recommend",
    "no_meat",
    "lasagna",
    "fish",
    "soup",
    "diet_fit",
    "other_options",
}

RESTRICTIONS = {
    "restriction_none": "Sem restricoes",
    "restriction_gluten": "Sem gluten",
    "restriction_lactose": "Sem lactose",
    "restriction_vegetarian": "Vegetariana",
    "restriction_seafood": "Sem frutos do mar",
}

DISHES = {
    "lasagna": "Lasanha de Legumes",
    "fish": "Peixe Assado com Legumes",
    "soup": "Sopa de Lentilha",
}

ORDER_CALLBACKS = set(DISHES)


def normalize(text: str) -> str:
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return without_accents.lower().strip()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                telegram_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                restriction TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                dish_key TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                ordered_at TEXT NOT NULL,
                FOREIGN KEY (telegram_id) REFERENCES clients (telegram_id)
            );

            CREATE TABLE IF NOT EXISTS favorite_waitlist (
                telegram_id INTEGER NOT NULL,
                dish_key TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (telegram_id, dish_key),
                FOREIGN KEY (telegram_id) REFERENCES clients (telegram_id)
            );

            CREATE TABLE IF NOT EXISTS weekly_menu (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                dish_key TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (week_start, dish_key)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "phone" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        if "address" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN address TEXT NOT NULL DEFAULT ''")


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def dish_key_from_name(dish_name: str) -> str:
    normalized = normalize(dish_name)
    for key, name in DISHES.items():
        if normalize(name) == normalized:
            return key
    return normalized.replace(" ", "_")[:64]


def current_week_start() -> str:
    today = date.today()
    monday = today.fromordinal(today.toordinal() - today.weekday())
    return monday.isoformat()


def load_client(telegram_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM clients WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()


def save_client(
    telegram_id: int,
    chat_id: int,
    name: str,
    phone: str,
    address: str,
    restriction: str,
) -> None:
    timestamp = now_iso()
    with db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "company" in columns:
            conn.execute(
                """
                INSERT INTO clients (
                    telegram_id, chat_id, name, company, phone, address, restriction, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    name = excluded.name,
                    company = excluded.company,
                    phone = excluded.phone,
                    address = excluded.address,
                    restriction = excluded.restriction,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, chat_id, name, address, phone, address, restriction, timestamp, timestamp),
            )
            return

        conn.execute(
            """
            INSERT INTO clients (telegram_id, chat_id, name, phone, address, restriction, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                name = excluded.name,
                phone = excluded.phone,
                address = excluded.address,
                restriction = excluded.restriction,
                updated_at = excluded.updated_at
            """,
            (telegram_id, chat_id, name, phone, address, restriction, timestamp, timestamp),
        )


def record_order(telegram_id: int, dish_key: str) -> None:
    dish_name = DISHES[dish_key]
    with db() as conn:
        conn.execute(
            """
            INSERT INTO orders (telegram_id, dish_key, dish_name, ordered_at)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_id, dish_key, dish_name, now_iso()),
        )


def add_favorite_waitlist(telegram_id: int, dish_key: str) -> None:
    dish_name = DISHES[dish_key]
    with db() as conn:
        conn.execute(
            """
            INSERT INTO favorite_waitlist (telegram_id, dish_key, dish_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id, dish_key) DO NOTHING
            """,
            (telegram_id, dish_key, dish_name, now_iso()),
        )


def top_dishes_for_client(telegram_id: int, limit: int = 3) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT dish_key, dish_name, COUNT(*) AS total
            FROM orders
            WHERE telegram_id = ?
            GROUP BY dish_key, dish_name
            ORDER BY total DESC, MAX(ordered_at) DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()


def recent_orders_for_client(telegram_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT dish_name, ordered_at
            FROM orders
            WHERE telegram_id = ?
            ORDER BY ordered_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()


def save_weekly_menu(dishes: list[tuple[str, str]]) -> list[sqlite3.Row]:
    week_start = current_week_start()
    timestamp = now_iso()
    with db() as conn:
        for dish_key, dish_name in dishes:
            conn.execute(
                """
                INSERT INTO weekly_menu (week_start, dish_key, dish_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(week_start, dish_key) DO UPDATE SET
                    dish_name = excluded.dish_name,
                    created_at = excluded.created_at
                """,
                (week_start, dish_key, dish_name, timestamp),
            )

        placeholders = ",".join("?" for _ in dishes)
        if not placeholders:
            return []

        dish_keys = [dish_key for dish_key, _ in dishes]
        return conn.execute(
            f"""
            SELECT c.telegram_id, c.chat_id, c.name, GROUP_CONCAT(wm.dish_name, ', ') AS dishes
            FROM weekly_menu wm
            JOIN (
                SELECT telegram_id, dish_key FROM favorite_waitlist
                UNION
                SELECT telegram_id, dish_key
                FROM orders
                GROUP BY telegram_id, dish_key
                HAVING COUNT(*) >= 2
            ) interest ON interest.dish_key = wm.dish_key
            JOIN clients c ON c.telegram_id = interest.telegram_id
            WHERE wm.week_start = ? AND wm.dish_key IN ({placeholders})
            GROUP BY c.telegram_id, c.chat_id, c.name
            """,
            (week_start, *dish_keys),
        ).fetchall()


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


def profile(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("profile", {})


def telegram_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def chat_id(update: Update) -> int | None:
    return update.effective_chat.id if update.effective_chat else None


def hydrate_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = telegram_id(update)
    if not user_id or profile(context).get("registered"):
        return

    client = load_client(user_id)
    if not client:
        return

    profile(context).update(
        {
            "telegram_id": client["telegram_id"],
            "name": client["name"],
            "phone": client["phone"],
            "address": client["address"],
            "restriction": client["restriction"],
            "registered": True,
        }
    )


def is_registered(context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_profile = profile(context)
    return bool(user_profile.get("registered"))


def user_name(context: ContextTypes.DEFAULT_TYPE | None = None) -> str:
    if context:
        saved_name = profile(context).get("name")
        if saved_name:
            return saved_name
    return DEFAULT_USER_NAME


def render(template: str, context: ContextTypes.DEFAULT_TYPE | None = None) -> str:
    return template.format(name=escape(user_name(context)))


def registration_restriction_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("\u2705 Nenhuma restricao", "restriction_none")],
        [("\U0001f33e Sem gluten", "restriction_gluten"), ("\U0001f95b Sem lactose", "restriction_lactose")],
        [("\U0001f969 Vegetariana", "restriction_vegetarian"), ("\U0001f420 Sem frutos do mar", "restriction_seafood")],
    ]


def build_profile_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    user_profile = profile(context)
    telegram_id_value = user_profile.get("telegram_id")
    name = escape(user_profile.get("name", user_name(context)))
    phone = escape(user_profile.get("phone", "Nao informado"))
    address = escape(user_profile.get("address", "Nao informado"))
    restriction = escape(user_profile.get("restriction", "Nao informado"))
    favorites_text = "Ainda sem historico de pedidos."
    history_text = "Ainda sem pedidos registrados."

    if telegram_id_value:
        top_dishes = top_dishes_for_client(telegram_id_value)
        recent_orders = recent_orders_for_client(telegram_id_value)
        if top_dishes:
            favorites_text = "\n".join(
                f"- {escape(row['dish_name'])}: {row['total']} pedido(s)" for row in top_dishes
            )
        if recent_orders:
            history_text = "\n".join(f"- {escape(row['dish_name'])}" for row in recent_orders)

    return {
        "text": (
            f"\U0001f464 <b>Seu perfil:</b>\n\n"
            f"<b>Nome:</b> {name}\n"
            f"<b>Telefone:</b> {phone}\n"
            f"<b>Endereco/bairro:</b> {address}\n"
            f"<b>Restricao alimentar:</b> {restriction}\n\n"
            f"<b>Mais pedidos:</b>\n{favorites_text}\n\n"
            f"<b>Historico recente:</b>\n{history_text}\n\n"
            "Posso atualizar alguma informacao?"
        ),
        "buttons": [[("\u270f\ufe0f Atualizar cadastro", "restart_registration"), ("\u2705 Esta correto", "thanks")]],
    }


FLOWS = {
    "start": {
        "text": (
            "\U0001f37d\ufe0f <b>Ol\u00e1, {name}!</b> Que bom ter voc\u00ea aqui \U0001f60a\n\n"
            "Sou o bot da <b>Apetit</b>. Estou aqui para te ajudar com o card\u00e1pio "
            "do restaurante corporativo, fazer recomenda\u00e7\u00f5es personalizadas e registrar "
            "seu feedback.\n\n"
            "Como posso te ajudar hoje?"
        ),
        "buttons": [
            [("\U0001f957 Ver card\u00e1pio de hoje", "menu_today"), ("\u2b50 Me recomendar algo", "recommend")],
            [("\U0001f464 Meu perfil", "profile")],
        ],
    },
    "menu_today": {
        "text": (
            "Hoje temos:\n\n"
            "\U0001f957 <b>Lasanha de Legumes</b> - sem gl\u00faten \u2705\n"
            "\U0001f344 <b>Estrogonofe de Cogumelos</b> - cont\u00e9m lactose \u26a0\ufe0f\n"
            "\U0001f41f <b>Peixe Assado com Legumes</b> \u2705\n"
            "\U0001f958 <b>Sopa de Lentilha</b> - vegana \u2705\n\n"
            "Qual op\u00e7\u00e3o te interessa?"
        ),
        "buttons": [
            [("\U0001f957 Lasanha", "lasagna"), ("\U0001f41f Peixe", "fish")],
            [("\U0001f958 Sopa", "soup"), ("\U0001f504 Outras op\u00e7\u00f5es", "other_options")],
        ],
    },
    "no_meat": {
        "text": (
            "Hoje temos duas op\u00e7\u00f5es sem carne:\n\n"
            "\U0001f957 <b>Lasanha de Legumes</b> - sem gl\u00faten \u2705\n"
            "\U0001f344 <b>Estrogonofe de Cogumelos</b> - cont\u00e9m lactose \u26a0\ufe0f\n\n"
            "Como voc\u00ea informou sua restri\u00e7\u00e3o no cadastro, vou considerar isso antes de recomendar qualquer prato.\n\n"
            "Quer saber se a lasanha se encaixa na sua dieta?"
        ),
        "buttons": [
            [("\u2705 Quero a lasanha", "lasagna"), ("\u274c Ver outras", "other_options")],
            [("\U0001f957 Encaixa na minha dieta?", "diet_fit")],
        ],
    },
    "recommend": {
        "text": (
            "Hoje recomendo o <b>\U0001f41f Peixe Assado com Legumes</b>!\n\n"
            "Considerei seu cadastro e o card\u00e1pio do dia antes de sugerir essa op\u00e7\u00e3o. "
            "\u00c9 uma escolha leve, proteica e alinhada com seus objetivos \U0001f33f"
        ),
        "buttons": [[("\U0001f44d Gostei, vou de peixe!", "fish"), ("\U0001f504 Ver outras op\u00e7\u00f5es", "other_options")]],
    },
    "complaint": {
        "text": (
            "Sentimos muito por isso \U0001f614 Sua experi\u00eancia \u00e9 muito importante para n\u00f3s.\n\n"
            "O problema foi mais relacionado a:"
        ),
        "buttons": [
            [("\U0001f321 Temperatura", "complaint_temperature"), ("\U0001f615 Sabor", "complaint_taste")],
            [("\u26a0\ufe0f Os dois", "complaint_both")],
        ],
    },
    "feedback": {
        "text": (
            "Ficamos muito felizes em saber! \U0001f60a\n\n"
            "Trabalhamos todos os dias para oferecer refei\u00e7\u00f5es que fazem diferen\u00e7a no seu dia. "
            "<i>Equil\u00edbrio \u00e9 o caminho</i> \U0001f33f\n\n"
            "Posso te avisar quando esse prato voltar ao card\u00e1pio?"
        ),
        "buttons": [[("\u2705 Sim, me avise!", "notify_me"), ("N\u00e3o, obrigada", "thanks")]],
    },
    "restriction": {
        "text": (
            "\u26a0\ufe0f <b>Aten\u00e7\u00e3o, {name}!</b>\n\n"
            "O estrogonofe de cogumelos <b>cont\u00e9m lactose</b>.\n\n"
            "Antes de confirmar qualquer pedido, eu cruzo essa informa\u00e7\u00e3o com o seu cadastro.\n\n"
            "Que tal a <b>lasanha de legumes</b>? Ela \u00e9 sem gl\u00faten e sem lactose \u2705"
        ),
        "buttons": [[("\U0001f957 Ver lasanha", "lasagna"), ("\u270f\ufe0f Atualizar cadastro", "restart_registration")]],
    },
}


RESPONSES = {
    "lasagna": {
        "text": (
            "\u00d3tima escolha! \U0001f33f\n\n"
            "A <b>Lasanha de Legumes</b> de hoje \u00e9 sem gl\u00faten, sem carne e preparada com pouco queijo. "
            "Combina\u00e7\u00e3o perfeita para voc\u00ea!\n\n"
            "Pedido registrado para <b>{name}</b>. Bom apetite \U0001f60a"
        ),
        "buttons": [
            [("\u2b50 Avaliar depois", "rate_later"), ("\U0001f514 Me lembrar amanh\u00e3", "remind_tomorrow")],
            [("\U0001f514 Me avise quando voltar", "favorite_last_order")],
        ],
    },
    "fish": {
        "text": (
            "\U0001f41f \u00d3tima escolha!\n\n"
            "O <b>Peixe Assado</b> \u00e9 rico em \u00f4mega-3 e vai te deixar com energia para a tarde toda.\n\n"
            "Pedido registrado. Bom almo\u00e7o! \U0001f60a"
        ),
        "buttons": [[("\u2b50 Avaliar depois", "rate_later"), ("\U0001f514 Me avise quando voltar", "favorite_last_order")]],
    },
    "soup": {
        "text": (
            "\U0001f958 A <b>Sopa de Lentilha</b> \u00e9 vegana, sem gl\u00faten e super nutritiva. "
            "Uma escolha leve e equilibrada!\n\n"
            "Pedido registrado. Bom almo\u00e7o \U0001f60a"
        ),
        "buttons": [[("\u2b50 Avaliar depois", "rate_later"), ("\U0001f514 Me avise quando voltar", "favorite_last_order")]],
    },
    "diet_fit": {
        "text": (
            "\u2705 <b>Sim!</b> A Lasanha de Legumes de hoje \u00e9:\n\n"
            "- Sem gl\u00faten \u2705\n"
            "- Vegetariana \u2705\n"
            "- Preparada com pouco queijo \u2705\n\n"
            "Atende as principais restri\u00e7\u00f5es registradas no seu cadastro \U0001f33f"
        ),
        "buttons": [[("\U0001f957 Perfeito, vou de lasanha!", "lasagna")]],
    },
    "other_options": FLOWS["menu_today"],
    "complaint_temperature": {
        "text": (
            "Entendido e registrado! J\u00e1 notificamos a equipe do restaurante \U0001f4cb\n\n"
            "Para a sua pr\u00f3xima refei\u00e7\u00e3o, posso sugerir um prato servido logo ap\u00f3s o preparo "
            "para garantir temperatura ideal."
        ),
        "buttons": [[("\U0001f37d\ufe0f Ver sugest\u00e3o", "hot_suggestion")]],
    },
    "complaint_taste": {
        "text": (
            "Obrigado por avisar. Registrei o ponto sobre sabor para a equipe avaliar com carinho \U0001f4cb\n\n"
            "Quer que eu sugira uma op\u00e7\u00e3o mais leve para a pr\u00f3xima refei\u00e7\u00e3o?"
        ),
        "buttons": [[("\u2b50 Ver recomenda\u00e7\u00e3o", "recommend")]],
    },
    "complaint_both": {
        "text": (
            "Sinto muito pela experi\u00eancia, {name}. Registrei temperatura e sabor para acompanhamento da equipe \U0001f4cb\n\n"
            "Na pr\u00f3xima refei\u00e7\u00e3o, posso priorizar uma op\u00e7\u00e3o preparada mais pr\u00f3xima do hor\u00e1rio de servir."
        ),
        "buttons": [[("\U0001f37d\ufe0f Ver sugest\u00e3o", "hot_suggestion")]],
    },
    "hot_suggestion": {
        "text": (
            "Para amanh\u00e3, vou priorizar te avisar sobre pratos que saem diretamente da cozinha \U0001f525\n\n"
            "Registrado! Voc\u00ea receber\u00e1 uma notifica\u00e7\u00e3o no hor\u00e1rio do almo\u00e7o."
        ),
        "buttons": [[("\u2705 Obrigada!", "thanks")]],
    },
    "notify_me": {
        "text": (
            "\u2705 Combinado! Vou te notificar quando ele voltar ao card\u00e1pio \U0001f514\n\n"
            "\u00c9 sempre bom saber que nosso trabalho faz diferen\u00e7a no seu dia \U0001f33f"
        ),
        "buttons": [[("\U0001f957 Ver card\u00e1pio de hoje", "menu_today")]],
    },
    "favorite_saved": {
        "text": (
            "\u2705 Combinado! Registrei esse prato como favorito.\n\n"
            "Quando ele aparecer em uma nova atualiza\u00e7\u00e3o do card\u00e1pio semanal, eu te aviso por aqui \U0001f514"
        ),
        "buttons": [[("\U0001f957 Ver card\u00e1pio", "menu_today")]],
    },
    "update_profile": {
        "text": "\u270f\ufe0f Vamos atualizar seu cadastro. Para come\u00e7ar, qual \u00e9 o seu nome completo?",
        "buttons": [],
    },
    "rate_later": {"text": "Perfeito! Te mando um lembrete para avaliar ap\u00f3s o almo\u00e7o \U0001f514", "buttons": []},
    "remind_tomorrow": {"text": "\U0001f514 Combinado! Te aviso amanh\u00e3 com o card\u00e1pio fresquinho \U0001f60a", "buttons": []},
    "thanks": {
        "text": "Sempre que precisar estou aqui \U0001f33f Bom almo\u00e7o!",
        "buttons": [[("\U0001f957 Ver card\u00e1pio", "menu_today")]],
    },
}


async def send_text(
    update: Update,
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
    buttons: list[list[tuple[str, str]]] | None = None,
    edit: bool = False,
) -> None:
    reply_markup = kb(buttons) if buttons else None
    rendered_text = render(text, context)

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text=rendered_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        return

    if update.effective_message:
        await update.effective_message.reply_text(
            text=rendered_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )


async def send_payload(
    update: Update,
    payload: dict,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    await send_text(update, payload["text"], context, payload.get("buttons"), edit)


async def ask_registration_name(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    context.user_data[REGISTRATION_STEP] = "name"
    await send_text(
        update,
        (
            "\U0001f37d\ufe0f <b>Antes de iniciar seu pedido, preciso fazer um cadastro r\u00e1pido.</b>\n\n"
            "Assim consigo considerar suas restri\u00e7\u00f5es e identificar seu pedido corretamente.\n\n"
            "Qual \u00e9 o seu nome completo?"
        ),
        context,
        edit=edit,
    )


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "phone"
    await send_text(
        update,
        "Obrigado, {name}! Agora me envie seu telefone para contato.",
        context,
    )


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "address"
    await send_text(
        update,
        "Perfeito. Agora me informe seu endereco ou bairro de entrega.",
        context,
    )


async def ask_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "restriction"
    await send_text(
        update,
        "Perfeito. Para sua seguran\u00e7a alimentar, selecione sua principal restri\u00e7\u00e3o:",
        context,
        registration_restriction_buttons(),
    )


async def finish_registration(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    restriction: str,
    edit: bool = False,
) -> None:
    user_profile = profile(context)
    user_id = telegram_id(update)
    current_chat_id = chat_id(update)
    user_profile["restriction"] = restriction
    user_profile["registered"] = True
    if user_id:
        user_profile["telegram_id"] = user_id
    if user_id and current_chat_id:
        save_client(
            user_id,
            current_chat_id,
            user_profile.get("name", DEFAULT_USER_NAME),
            user_profile.get("phone", "Nao informado"),
            user_profile.get("address", "Nao informado"),
            restriction,
        )
    context.user_data.pop(REGISTRATION_STEP, None)

    await send_text(
        update,
        (
            "\u2705 <b>Cadastro conclu\u00eddo!</b>\n\n"
            "<b>Nome:</b> {name}\n"
            f"<b>Telefone:</b> {escape(user_profile.get('phone', 'Nao informado'))}\n"
            f"<b>Endereco/bairro:</b> {escape(user_profile.get('address', 'Nao informado'))}\n"
            f"<b>Restricao alimentar:</b> {escape(restriction)}\n\n"
            "Agora sim, posso te ajudar com o card\u00e1pio e seus pedidos."
        ),
        context,
        FLOWS["start"]["buttons"],
        edit=edit,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if is_registered(context):
        await send_payload(update, FLOWS["start"], context)
        return
    await ask_registration_name(update, context)


async def reset_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await ask_registration_name(update, context)


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = telegram_id(update)
    if not user_id or not is_registered(context):
        await ask_registration_name(update, context)
        return

    recent_orders = recent_orders_for_client(user_id, limit=10)
    if not recent_orders:
        await send_text(update, "Voce ainda nao tem pedidos registrados.", context, FLOWS["start"]["buttons"])
        return

    lines = "\n".join(f"- {escape(row['dish_name'])}" for row in recent_orders)
    await send_text(
        update,
        f"\U0001f4cb <b>Seu historico de pedidos:</b>\n\n{lines}",
        context,
        FLOWS["start"]["buttons"],
    )


def parse_weekly_menu(raw_text: str) -> list[tuple[str, str]]:
    dishes = []
    for line in raw_text.splitlines():
        dish_name = line.strip(" -\t")
        if not dish_name:
            continue
        dishes.append((dish_key_from_name(dish_name), dish_name))
    return dishes


async def update_weekly_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = telegram_id(update)
    if ADMIN_TELEGRAM_IDS and user_id not in ADMIN_TELEGRAM_IDS:
        await update.effective_message.reply_text("Apenas administradores podem atualizar o cardapio semanal.")
        return

    message_text = update.effective_message.text or ""
    parts = message_text.split(maxsplit=1)
    raw_text = parts[1].strip() if len(parts) > 1 else ""
    if not raw_text:
        await update.effective_message.reply_text(
            "Envie assim:\n\n"
            "/cardapio_semana Lasanha de Legumes\n"
            "Peixe Assado com Legumes\n"
            "Sopa de Lentilha"
        )
        return

    dishes = parse_weekly_menu(raw_text)
    matches = save_weekly_menu(dishes)
    notified = 0
    for row in matches:
        try:
            await context.bot.send_message(
                chat_id=row["chat_id"],
                text=(
                    f"\U0001f514 Oi, {escape(row['name'])}! "
                    f"Tem prato que aparece no seu historico/favoritos no cardapio desta semana:\n\n"
                    f"<b>{escape(row['dishes'])}</b>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb([[("\U0001f957 Ver cardapio", "menu_today")]]),
            )
            notified += 1
        except Exception:
            logger.exception("Falha ao notificar cliente %s", row["telegram_id"])

    await update.effective_message.reply_text(
        f"Cardapio semanal atualizado com {len(dishes)} prato(s). Clientes notificados: {notified}."
    )


async def handle_registration_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    step = context.user_data.get(REGISTRATION_STEP)
    if not step:
        return False

    text = (update.message.text or "").strip()
    if len(text) < 2:
        await send_text(update, "Me envie uma resposta um pouquinho mais completa, por favor.", context)
        return True

    user_profile = profile(context)
    if step == "name":
        user_profile["name"] = text
        await ask_phone(update, context)
        return True

    if step == "phone":
        user_profile["phone"] = text
        await ask_address(update, context)
        return True

    if step == "address":
        user_profile["address"] = text
        await ask_restriction(update, context)
        return True

    await ask_restriction(update, context)
    return True


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    query = update.callback_query
    await query.answer()

    if query.data in {"start_register", "restart_registration", "update_profile"}:
        profile(context).pop("registered", None)
        await ask_registration_name(update, context, edit=True)
        return

    if query.data in RESTRICTIONS:
        await finish_registration(update, context, RESTRICTIONS[query.data], edit=True)
        return

    if query.data in {"favorite_last_order", "notify_me"}:
        last_order = context.user_data.get("last_order")
        user_id = telegram_id(update)
        if last_order and user_id:
            add_favorite_waitlist(user_id, last_order)
            await send_payload(update, RESPONSES["favorite_saved"], context, edit=True)
        else:
            await send_text(
                update,
                "Me diga primeiro qual prato voce quer acompanhar, escolhendo uma opcao do cardapio.",
                context,
                FLOWS["start"]["buttons"],
                edit=True,
            )
        return

    if query.data == "profile":
        if not is_registered(context):
            await ask_registration_name(update, context, edit=True)
            return
        await send_payload(update, build_profile_payload(context), context, edit=True)
        return

    if query.data in ORDER_ACTIONS and not is_registered(context):
        await ask_registration_name(update, context, edit=True)
        return

    if query.data in ORDER_CALLBACKS:
        user_id = telegram_id(update)
        if user_id:
            record_order(user_id, query.data)
            context.user_data["last_order"] = query.data

    payload = RESPONSES.get(query.data) or FLOWS.get(query.data)
    if not payload:
        payload = {
            "text": "Vamos juntos encontrar a melhor op\u00e7\u00e3o para voc\u00ea \U0001f60a\n\nComo posso ajudar hoje?",
            "buttons": FLOWS["start"]["buttons"],
        }

    await send_payload(update, payload, context, edit=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if await handle_registration_message(update, context):
        return

    text = normalize(update.message.text or "")

    if not is_registered(context):
        await ask_registration_name(update, context)
        return

    if any(term in text for term in ["sem carne", "vegetariano", "vegetariana"]):
        await send_payload(update, FLOWS["no_meat"], context)
    elif "lasanha" in text:
        user_id = telegram_id(update)
        if user_id:
            record_order(user_id, "lasagna")
            context.user_data["last_order"] = "lasagna"
        await send_payload(update, RESPONSES["lasagna"], context)
    elif "peixe" in text:
        user_id = telegram_id(update)
        if user_id:
            record_order(user_id, "fish")
            context.user_data["last_order"] = "fish"
        await send_payload(update, RESPONSES["fish"], context)
    elif "sopa" in text:
        user_id = telegram_id(update)
        if user_id:
            record_order(user_id, "soup")
            context.user_data["last_order"] = "soup"
        await send_payload(update, RESPONSES["soup"], context)
    elif any(term in text for term in ["recomenda", "sugere", "melhor opcao"]):
        await send_payload(update, FLOWS["recommend"], context)
    elif any(term in text for term in ["fria", "frio", "ruim", "reclamacao", "problema"]):
        await send_payload(update, FLOWS["complaint"], context)
    elif any(term in text for term in ["gostei", "bom", "otimo", "excelente"]):
        await send_payload(update, FLOWS["feedback"], context)
    elif any(term in text for term in ["estrogonofe", "lactose", "restricao"]):
        await send_payload(update, FLOWS["restriction"], context)
    elif any(term in text for term in ["cardapio", "tem hoje", "o que tem"]):
        await send_payload(update, FLOWS["menu_today"], context)
    elif "perfil" in text or "cadastro" in text:
        await send_payload(update, build_profile_payload(context), context)
    else:
        await send_text(
            update,
            (
                "Vamos juntos encontrar a melhor op\u00e7\u00e3o para voc\u00ea \U0001f60a\n\n"
                "Quer ver o card\u00e1pio de hoje ou prefere uma recomenda\u00e7\u00e3o personalizada?"
            ),
            context,
            [
                [("\U0001f957 Card\u00e1pio", "menu_today"), ("\u2b50 Recomenda\u00e7\u00e3o", "recommend")],
                [("\U0001f464 Meu perfil", "profile")],
            ],
        )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Defina TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.")

    init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recadastrar", reset_registration))
    app.add_handler(CommandHandler("historico", show_history))
    app.add_handler(CommandHandler("cardapio_semana", update_weekly_menu))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Apetit Bot iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
