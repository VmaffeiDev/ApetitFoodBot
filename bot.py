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
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.getenv("APETIT_DB_PATH", "apetit.db"))
DEFAULT_USER_NAME = os.getenv("APETIT_USER_NAME", "Cliente")
REGISTRATION_STEP = "registration_step"
ADMIN_TELEGRAM_IDS = {
    int(value.strip())
    for value in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",")
    if value.strip().isdigit()
}

RESTRICTIONS = {
    "restriction_none": "Sem restricoes",
    "restriction_gluten": "Sem gluten",
    "restriction_lactose": "Sem lactose",
    "restriction_vegetarian": "Vegetariana",
    "restriction_seafood": "Sem frutos do mar",
}

DEFAULT_DISHES = {
    "lasagna": "Lasanha de Legumes",
    "fish": "Peixe Assado com Legumes",
    "soup": "Sopa de Lentilha",
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


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
                ordered_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS favorite_waitlist (
                telegram_id INTEGER NOT NULL,
                dish_key TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (telegram_id, dish_key)
            );
            CREATE TABLE IF NOT EXISTS weekly_menu (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                dish_key TEXT NOT NULL,
                dish_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (week_start, dish_key)
            );
            CREATE TABLE IF NOT EXISTS menu_items (
                dish_key TEXT PRIMARY KEY,
                dish_name TEXT NOT NULL,
                price_cents INTEGER NOT NULL DEFAULT 0,
                day_of_week TEXT NOT NULL DEFAULT 'todos',
                ingredients TEXT NOT NULL DEFAULT '',
                allergens TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                available INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "phone" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        if "address" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        seed_default_menu(conn)


def dish_key_from_name(name: str) -> str:
    for key, dish_name in DEFAULT_DISHES.items():
        if normalize(name) == normalize(dish_name):
            return key
    return normalize(name).replace(" ", "_")[:64]


def price_to_cents(raw: str) -> int:
    cleaned = raw.strip().replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return int(round(float(cleaned) * 100))
    except ValueError:
        return 0


def format_price(cents: int) -> str:
    return f"R$ {cents / 100:.2f}".replace(".", ",")


def seed_default_menu(conn: sqlite3.Connection) -> None:
    timestamp = now_iso()
    items = [
        ("lasagna", "Lasanha de Legumes", 2890, "segunda", "Legumes, molho de tomate, massa sem gluten", "Pode conter lactose", "vegetariano, sem gluten", 1),
        ("fish", "Peixe Assado com Legumes", 3290, "terca", "Peixe, legumes, azeite, ervas", "Peixe", "leve, proteico", 1),
        ("soup", "Sopa de Lentilha", 2490, "quarta", "Lentilha, legumes, temperos naturais", "", "vegano, sem gluten", 1),
    ]
    for item in items:
        conn.execute(
            """
            INSERT INTO menu_items (
                dish_key, dish_name, price_cents, day_of_week, ingredients,
                allergens, tags, available, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dish_key) DO NOTHING
            """,
            (*item, timestamp, timestamp),
        )


def upsert_menu_item(name: str, price: int, day: str, ingredients: str, allergens: str, tags: str, available: bool) -> str:
    key = dish_key_from_name(name)
    timestamp = now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO menu_items (
                dish_key, dish_name, price_cents, day_of_week, ingredients,
                allergens, tags, available, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dish_key) DO UPDATE SET
                dish_name = excluded.dish_name,
                price_cents = excluded.price_cents,
                day_of_week = excluded.day_of_week,
                ingredients = excluded.ingredients,
                allergens = excluded.allergens,
                tags = excluded.tags,
                available = excluded.available,
                updated_at = excluded.updated_at
            """,
            (key, name, price, normalize(day) or "todos", ingredients, allergens, tags, int(available), timestamp, timestamp),
        )
    return key


def list_menu_items(day: str | None = None, only_available: bool = True) -> list[sqlite3.Row]:
    where, params = [], []
    if only_available:
        where.append("available = 1")
    if day:
        where.append("(day_of_week = ? OR day_of_week = 'todos')")
        params.append(normalize(day))
    sql_where = f"WHERE {' AND '.join(where)}" if where else ""
    with db() as conn:
        return conn.execute(f"SELECT * FROM menu_items {sql_where} ORDER BY day_of_week, dish_name", params).fetchall()


def load_menu_item(key: str) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute("SELECT * FROM menu_items WHERE dish_key = ?", (key,)).fetchone()


def find_menu_item_in_text(text: str) -> sqlite3.Row | None:
    normalized = normalize(text)
    for item in list_menu_items():
        if normalize(item["dish_name"]) in normalized:
            return item
    return None


def get_dish_name(key: str) -> str:
    item = load_menu_item(key)
    return item["dish_name"] if item else DEFAULT_DISHES.get(key, key.replace("_", " ").title())


def current_week_start() -> str:
    today = date.today()
    return today.fromordinal(today.toordinal() - today.weekday()).isoformat()


def save_client(telegram_id: int, chat_id: int, name: str, phone: str, address: str, restriction: str) -> None:
    timestamp = now_iso()
    with db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "company" in columns:
            conn.execute(
                """
                INSERT INTO clients (telegram_id, chat_id, name, company, phone, address, restriction, created_at, updated_at)
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


def load_client(telegram_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute("SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)).fetchone()


def record_order(telegram_id: int, dish_key: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO orders (telegram_id, dish_key, dish_name, ordered_at) VALUES (?, ?, ?, ?)",
            (telegram_id, dish_key, get_dish_name(dish_key), now_iso()),
        )


def add_favorite_waitlist(telegram_id: int, dish_key: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO favorite_waitlist (telegram_id, dish_key, dish_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id, dish_key) DO NOTHING
            """,
            (telegram_id, dish_key, get_dish_name(dish_key), now_iso()),
        )


def recent_orders(telegram_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT dish_name, ordered_at FROM orders WHERE telegram_id = ? ORDER BY ordered_at DESC LIMIT ?",
            (telegram_id, limit),
        ).fetchall()


def top_dishes(telegram_id: int, limit: int = 3) -> list[sqlite3.Row]:
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


def save_weekly_menu(dishes: list[tuple[str, str]]) -> list[sqlite3.Row]:
    week = current_week_start()
    timestamp = now_iso()
    with db() as conn:
        for key, name in dishes:
            conn.execute(
                """
                INSERT INTO weekly_menu (week_start, dish_key, dish_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(week_start, dish_key) DO UPDATE SET
                    dish_name = excluded.dish_name,
                    created_at = excluded.created_at
                """,
                (week, key, name, timestamp),
            )
        if not dishes:
            return []
        placeholders = ",".join("?" for _ in dishes)
        keys = [key for key, _ in dishes]
        return conn.execute(
            f"""
            SELECT c.telegram_id, c.chat_id, c.name, GROUP_CONCAT(wm.dish_name, ', ') AS dishes
            FROM weekly_menu wm
            JOIN (
                SELECT telegram_id, dish_key FROM favorite_waitlist
                UNION
                SELECT telegram_id, dish_key FROM orders GROUP BY telegram_id, dish_key HAVING COUNT(*) >= 2
            ) interest ON interest.dish_key = wm.dish_key
            JOIN clients c ON c.telegram_id = interest.telegram_id
            WHERE wm.week_start = ? AND wm.dish_key IN ({placeholders})
            GROUP BY c.telegram_id, c.chat_id, c.name
            """,
            (week, *keys),
        ).fetchall()


def tg_id(update: Update) -> int | None:
    return update.effective_user.id if update.effective_user else None


def chat_id(update: Update) -> int | None:
    return update.effective_chat.id if update.effective_chat else None


def profile(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("profile", {})


def hydrate_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = tg_id(update)
    if not user_id or profile(context).get("registered"):
        return
    client = load_client(user_id)
    if client:
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


def registered(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(profile(context).get("registered"))


def user_name(context: ContextTypes.DEFAULT_TYPE) -> str:
    return profile(context).get("name") or DEFAULT_USER_NAME


def render(text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    return text.format(name=escape(user_name(context)))


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows])


async def send_text(update: Update, text: str, context: ContextTypes.DEFAULT_TYPE, buttons=None, edit: bool = False) -> None:
    markup = keyboard(buttons) if buttons else None
    text = render(text, context)
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=markup)
    elif update.effective_message:
        await update.effective_message.reply_text(text=text, parse_mode=ParseMode.HTML, reply_markup=markup)


async def send_payload(update: Update, payload: dict, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    await send_text(update, payload["text"], context, payload.get("buttons"), edit)


def main_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("\U0001f957 Ver cardapio", "menu_today"), ("\u2b50 Me recomendar algo", "recommend")],
        [("\U0001f464 Meu perfil", "profile")],
    ]


def restriction_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("\u2705 Nenhuma restricao", "restriction_none")],
        [("\U0001f33e Sem gluten", "restriction_gluten"), ("\U0001f95b Sem lactose", "restriction_lactose")],
        [("\U0001f969 Vegetariana", "restriction_vegetarian"), ("\U0001f420 Sem frutos do mar", "restriction_seafood")],
    ]


def menu_payload(day: str | None = None) -> dict:
    items = list_menu_items(day)
    if not items:
        return {"text": "\U0001f614 Ainda nao temos pratos disponiveis para esse dia.", "buttons": [[("\U0001f957 Ver cardapio completo", "menu_today")]]}
    lines = ["\U0001f957 <b>Cardapio disponivel:</b>"]
    buttons = []
    for item in items:
        tags = f" - {escape(item['tags'])}" if item["tags"] else ""
        allergens = f"\nAlergenicos: {escape(item['allergens'])}" if item["allergens"] else ""
        lines.append(
            f"<b>{escape(item['dish_name'])}</b> - {format_price(item['price_cents'])}\n"
            f"Dia: {escape(item['day_of_week'])}{tags}{allergens}"
        )
        buttons.append([(f"\U0001f37d Pedir {item['dish_name'][:30]}", f"dish:{item['dish_key']}")])
    return {"text": "\n\n".join(lines), "buttons": buttons}


def order_payload(dish_key: str) -> dict:
    item = load_menu_item(dish_key)
    if not item:
        return {"text": "\U0001f614 Nao encontrei esse prato no cardapio atual.", "buttons": [[("\U0001f957 Ver cardapio", "menu_today")]]}
    allergens = f"\nAlergenicos: {escape(item['allergens'])}" if item["allergens"] else ""
    return {
        "text": (
            f"\u2705 Pedido registrado para <b>{{name}}</b>.\n\n"
            f"<b>{escape(item['dish_name'])}</b>\n"
            f"Valor: {format_price(item['price_cents'])}\n"
            f"Dia: {escape(item['day_of_week'])}{allergens}\n\n"
            "Bom apetite \U0001f60a"
        ),
        "buttons": [[("\u2b50 Avaliar depois", "rate_later")], [("\U0001f514 Me avise quando voltar", "favorite_last_order")]],
    }


def profile_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    data = profile(context)
    user_id = data.get("telegram_id")
    top_text = "Ainda sem historico de pedidos."
    recent_text = "Ainda sem pedidos registrados."
    if user_id:
        top = top_dishes(user_id)
        recent = recent_orders(user_id)
        if top:
            top_text = "\n".join(f"- {escape(row['dish_name'])}: {row['total']} pedido(s)" for row in top)
        if recent:
            recent_text = "\n".join(f"- {escape(row['dish_name'])}" for row in recent)
    return {
        "text": (
            "\U0001f464 <b>Seu perfil:</b>\n\n"
            f"<b>Nome:</b> {escape(data.get('name', DEFAULT_USER_NAME))}\n"
            f"<b>Telefone:</b> {escape(data.get('phone', 'Nao informado'))}\n"
            f"<b>Endereco/bairro:</b> {escape(data.get('address', 'Nao informado'))}\n"
            f"<b>Restricao alimentar:</b> {escape(data.get('restriction', 'Nao informado'))}\n\n"
            f"<b>Mais pedidos:</b>\n{top_text}\n\n"
            f"<b>Historico recente:</b>\n{recent_text}"
        ),
        "buttons": [[("\u270f\ufe0f Atualizar cadastro", "restart_registration"), ("\u2705 Esta correto", "thanks")]],
    }


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "name"
    await send_text(
        update,
        (
            "\U0001f37d\ufe0f <b>Antes de iniciar seu pedido, preciso fazer um cadastro rapido.</b>\n\n"
            "Assim consigo considerar suas restricoes e identificar seu pedido corretamente \U0001f33f\n\n"
            "Qual e o seu nome completo?"
        ),
        context,
        edit=edit,
    )


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "phone"
    await send_text(update, "Obrigado, {name}! \U0001f60a Agora me envie seu telefone para contato.", context)


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "address"
    await send_text(update, "Perfeito \U0001f4cd Agora me informe seu endereco ou bairro de entrega.", context)


async def ask_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "restriction"
    await send_text(update, "\U0001f33f Para sua seguranca alimentar, selecione sua principal restricao:", context, restriction_buttons())


async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, restriction: str, edit: bool = False) -> None:
    data = profile(context)
    data["restriction"] = restriction
    data["registered"] = True
    user_id = tg_id(update)
    current_chat_id = chat_id(update)
    if user_id:
        data["telegram_id"] = user_id
    if user_id and current_chat_id:
        save_client(
            user_id,
            current_chat_id,
            data.get("name", DEFAULT_USER_NAME),
            data.get("phone", "Nao informado"),
            data.get("address", "Nao informado"),
            restriction,
        )
    context.user_data.pop(REGISTRATION_STEP, None)
    await send_text(
        update,
        (
            "\u2705 <b>Cadastro concluido!</b>\n\n"
            "<b>Nome:</b> {name}\n"
            f"<b>Telefone:</b> {escape(data.get('phone', 'Nao informado'))}\n"
            f"<b>Endereco/bairro:</b> {escape(data.get('address', 'Nao informado'))}\n"
            f"<b>Restricao alimentar:</b> {escape(restriction)}\n\n"
            "Agora posso te ajudar com o cardapio e seus pedidos \U0001f60a"
        ),
        context,
        main_buttons(),
        edit,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if registered(context):
        await send_text(
            update,
            (
                "\U0001f37d\ufe0f <b>Ola, {name}!</b>\n\n"
                "Sou o bot da Apetit. Posso te ajudar com cardapio, pedidos, recomendacoes e avisos de pratos favoritos \U0001f33f"
            ),
            context,
            main_buttons(),
        )
        return
    await ask_name(update, context)


async def reset_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await ask_name(update, context)


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = tg_id(update)
    if not user_id or not registered(context):
        await ask_name(update, context)
        return
    rows = recent_orders(user_id, 10)
    if not rows:
        await send_text(update, "\U0001f4cb Voce ainda nao tem pedidos registrados.", context, main_buttons())
        return
    await send_text(update, "\U0001f4cb <b>Seu historico de pedidos:</b>\n\n" + "\n".join(f"- {escape(row['dish_name'])}" for row in rows), context, main_buttons())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_name(update, context)
        return
    await send_payload(update, menu_payload(), context)


def is_admin(user_id: int | None) -> bool:
    return not ADMIN_TELEGRAM_IDS or bool(user_id in ADMIN_TELEGRAM_IDS)


async def add_menu_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await update.effective_message.reply_text("\U0001f512 Apenas administradores podem cadastrar pratos.")
        return
    text = update.effective_message.text or ""
    raw = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 6:
        await update.effective_message.reply_text(
            "Envie assim:\n\n"
            "/cardapio_add Nome do prato | 29,90 | segunda | ingredientes | alergenicos | tags | disponivel\n\n"
            "Exemplo:\n"
            "/cardapio_add Frango Grelhado | 31,90 | quinta | frango, arroz, legumes | nenhum | proteico, caseiro | sim"
        )
        return
    name, price, day, ingredients, allergens, tags = parts[:6]
    available = len(parts) < 7 or normalize(parts[6]) not in {"nao", "no", "false", "0", "esgotado"}
    key = upsert_menu_item(name, price_to_cents(price), day, ingredients, allergens, tags, available)
    await update.effective_message.reply_text(f"\u2705 Prato cadastrado/atualizado: {name} ({key}).")


async def list_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    day = " ".join(context.args).strip() if context.args else None
    items = list_menu_items(day, only_available=False)
    if not items:
        await update.effective_message.reply_text("\U0001f614 Nenhum prato cadastrado.")
        return
    lines = ["\U0001f957 Cardapio cadastrado:"]
    for item in items:
        status = "disponivel" if item["available"] else "esgotado"
        lines.append(f"- {item['dish_name']} | {format_price(item['price_cents'])} | {item['day_of_week']} | {status} | {item['tags']}")
    await update.effective_message.reply_text("\n".join(lines))


def parse_weekly_menu(raw: str) -> list[tuple[str, str]]:
    items = []
    for line in raw.splitlines():
        name = line.strip(" -\t")
        if name:
            items.append((dish_key_from_name(name), name))
    return items


async def update_weekly_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await update.effective_message.reply_text("\U0001f512 Apenas administradores podem atualizar o cardapio semanal.")
        return
    text = update.effective_message.text or ""
    raw = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
    if not raw:
        await update.effective_message.reply_text("Envie assim:\n\n/cardapio_semana Lasanha de Legumes\nPeixe Assado com Legumes")
        return
    matches = save_weekly_menu(parse_weekly_menu(raw))
    notified = 0
    for row in matches:
        try:
            await context.bot.send_message(
                chat_id=row["chat_id"],
                text=(
                    f"\U0001f514 Ola, {escape(row['name'])}! Tem prato que aparece no seu historico/favoritos no cardapio desta semana:\n\n"
                    f"<b>{escape(row['dishes'])}</b>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard([[("\U0001f957 Ver cardapio", "menu_today")]]),
            )
            notified += 1
        except Exception:
            logger.exception("Falha ao notificar cliente %s", row["telegram_id"])
    await update.effective_message.reply_text(f"\u2705 Cardapio semanal atualizado. Clientes notificados: {notified}.")


async def handle_registration_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    step = context.user_data.get(REGISTRATION_STEP)
    if not step:
        return False
    text = (update.message.text or "").strip()
    if len(text) < 2:
        await send_text(update, "Me envie uma resposta um pouco mais completa, por favor \U0001f60a", context)
        return True
    data = profile(context)
    if step == "name":
        data["name"] = text
        await ask_phone(update, context)
    elif step == "phone":
        data["phone"] = text
        await ask_address(update, context)
    elif step == "address":
        data["address"] = text
        await ask_restriction(update, context)
    else:
        await ask_restriction(update, context)
    return True


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data in {"restart_registration", "update_profile"}:
        profile(context).pop("registered", None)
        await ask_name(update, context, edit=True)
        return
    if data in RESTRICTIONS:
        await finish_registration(update, context, RESTRICTIONS[data], edit=True)
        return
    if data == "profile":
        if not registered(context):
            await ask_name(update, context, edit=True)
            return
        await send_payload(update, profile_payload(context), context, edit=True)
        return
    if data == "menu_today":
        if not registered(context):
            await ask_name(update, context, edit=True)
            return
        await send_payload(update, menu_payload(), context, edit=True)
        return
    if data.startswith("dish:"):
        if not registered(context):
            await ask_name(update, context, edit=True)
            return
        key = data.removeprefix("dish:")
        user_id = tg_id(update)
        if user_id:
            record_order(user_id, key)
            context.user_data["last_order"] = key
        await send_payload(update, order_payload(key), context, edit=True)
        return
    if data in {"favorite_last_order", "notify_me"}:
        user_id = tg_id(update)
        last = context.user_data.get("last_order")
        if user_id and last:
            add_favorite_waitlist(user_id, last)
            await send_text(update, "\u2705 Combinado! Vou te avisar quando esse prato voltar ao cardapio \U0001f514", context, main_buttons(), edit=True)
        else:
            await send_text(update, "Escolha um prato primeiro para eu acompanhar \U0001f60a", context, main_buttons(), edit=True)
        return
    if data == "recommend":
        await send_text(update, "\u2b50 Minha sugestao de hoje e ver o cardapio disponivel e escolher uma opcao alinhada ao seu cadastro.", context, [[("\U0001f957 Ver cardapio", "menu_today")]], edit=True)
        return
    await send_text(update, "Como posso ajudar? \U0001f60a", context, main_buttons(), edit=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if await handle_registration_message(update, context):
        return
    if not registered(context):
        await ask_name(update, context)
        return
    text = normalize(update.message.text or "")
    item = find_menu_item_in_text(update.message.text or "")
    if item:
        user_id = tg_id(update)
        if user_id:
            record_order(user_id, item["dish_key"])
            context.user_data["last_order"] = item["dish_key"]
        await send_payload(update, order_payload(item["dish_key"]), context)
    elif "cardapio" in text or "tem hoje" in text or "o que tem" in text:
        await send_payload(update, menu_payload(), context)
    elif "perfil" in text or "cadastro" in text:
        await send_payload(update, profile_payload(context), context)
    elif "historico" in text:
        await show_history(update, context)
    else:
        await send_text(update, "Quer ver o cardapio ou acessar seu perfil? \U0001f60a", context, main_buttons())


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Defina TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.")
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recadastrar", reset_registration))
    app.add_handler(CommandHandler("historico", show_history))
    app.add_handler(CommandHandler("cardapio", show_menu))
    app.add_handler(CommandHandler("cardapio_add", add_menu_item))
    app.add_handler(CommandHandler("cardapio_list", list_admin_menu))
    app.add_handler(CommandHandler("cardapio_semana", update_weekly_menu))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Apetit Bot iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
