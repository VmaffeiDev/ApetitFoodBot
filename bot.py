import logging
import os
import sqlite3
import unicodedata
from datetime import UTC, date, datetime
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

GOALS = {
    "goal_weight_loss": "Perder peso",
    "goal_muscle_gain": "Ganhar massa",
    "goal_maintenance": "Manter equilibrio",
    "goal_health": "Alimentacao mais saudavel",
    "goal_practical": "Praticidade na rotina",
}

DEFAULT_DISHES = {
    "lasagna": "Lasanha de Legumes",
    "fish": "Peixe Assado com Legumes",
    "soup": "Sopa de Lentilha",
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
                goal TEXT NOT NULL DEFAULT 'Nao informado',
                restriction TEXT NOT NULL,
                consent_accepted INTEGER NOT NULL DEFAULT 0,
                consented_at TEXT NOT NULL DEFAULT '',
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
        if "goal" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT 'Nao informado'")
        if "consent_accepted" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN consent_accepted INTEGER NOT NULL DEFAULT 0")
        if "consented_at" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN consented_at TEXT NOT NULL DEFAULT ''")
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
        ("frango_grelhado_com_arroz_integral", "Frango Grelhado com Arroz Integral", 3190, "segunda", "Frango, arroz integral, legumes, azeite", "", "proteico, integral, ganho de massa", 1),
        ("omelete_de_legumes", "Omelete de Legumes", 2690, "segunda", "Ovos, cenoura, abobrinha, tomate, ervas", "Ovo", "leve, proteico, vegetariano", 1),
        ("salada_proteica_com_grao_de_bico", "Salada Proteica com Grao de Bico", 2790, "terca", "Grao de bico, folhas, tomate, pepino, azeite", "", "leve, vegano, proteico, perda de peso", 1),
        ("patinho_moido_com_batata_doce", "Patinho Moido com Batata Doce", 3490, "quarta", "Patinho moido, batata doce, brocolis, cenoura", "", "proteico, ganho de massa", 1),
        ("bowl_de_quinoa_com_legumes", "Bowl de Quinoa com Legumes", 3090, "quinta", "Quinoa, legumes assados, folhas, sementes", "", "vegano, integral, saudavel", 1),
        ("tilapia_com_pure_de_abobora", "Tilapia com Pure de Abobora", 3390, "quinta", "Tilapia, abobora, legumes, ervas", "Peixe", "leve, proteico, perda de peso", 1),
        ("macarrao_integral_com_frango", "Macarrao Integral com Frango", 3290, "sexta", "Macarrao integral, frango, molho de tomate, ervas", "Gluten", "integral, proteico, energia", 1),
        ("carne_de_panela_com_arroz_e_feijao", "Carne de Panela com Arroz e Feijao", 3590, "sexta", "Carne bovina, arroz, feijao, legumes", "", "caseiro, proteico, energia", 1),
        ("wrap_integral_de_frango", "Wrap Integral de Frango", 2990, "todos", "Pao integral, frango, folhas, cenoura, molho leve", "Gluten", "pratico, proteico", 1),
        ("creme_de_abobora_com_lentilha", "Creme de Abobora com Lentilha", 2590, "todos", "Abobora, lentilha, temperos naturais", "", "leve, vegano, sem gluten", 1),
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


def item_search_text(item: sqlite3.Row) -> str:
    return normalize(
        " ".join(
            [
                item["dish_name"] or "",
                item["ingredients"] or "",
                item["allergens"] or "",
                item["tags"] or "",
            ]
        )
    )


def restriction_conflict(restriction: str, item: sqlite3.Row) -> str | None:
    text = item_search_text(item)
    for safe_phrase in ("sem gluten", "sem lactose", "sem carne", "vegetariano", "vegetariana", "vegano", "vegana"):
        text = text.replace(safe_phrase, "")
    restriction_key = normalize(restriction)
    checks = {
        "sem gluten": (("gluten", "trigo", "farinha"), "contem gluten"),
        "sem lactose": (("lactose", "leite", "queijo", "creme", "manteiga"), "contem lactose ou derivados de leite"),
        "vegetariana": (("carne", "frango", "peixe", "bovina", "suina", "porco", "camarao"), "nao esta marcado como vegetariano"),
        "sem frutos do mar": (("peixe", "camarao", "frutos do mar", "marisco"), "contem peixe ou frutos do mar"),
    }
    for key, (terms, reason) in checks.items():
        if key in restriction_key and any(term in text for term in terms):
            return reason
    return None


def compatible_menu_items(restriction: str) -> list[sqlite3.Row]:
    return [item for item in list_menu_items() if not restriction_conflict(restriction, item)]



def goal_score(goal: str, item: sqlite3.Row) -> int:
    text = item_search_text(item)
    goal_key = normalize(goal)
    keywords = {
        "perder peso": ("leve", "salada", "legumes", "sopa", "perda de peso", "grelhado", "abobora"),
        "ganhar massa": ("proteico", "frango", "patinho", "ovo", "lentilha", "grao de bico", "batata doce", "ganho de massa"),
        "manter equilibrio": ("equilibrado", "integral", "caseiro", "legumes", "arroz", "feijao"),
        "alimentacao mais saudavel": ("saudavel", "vegano", "integral", "legumes", "natural", "sem gluten", "quinoa"),
        "praticidade na rotina": ("pratico", "wrap", "bowl", "todos", "leve"),
    }
    for key, terms in keywords.items():
        if key in goal_key:
            return sum(1 for term in terms if term in text)
    return 0


def goal_reason(goal: str) -> str:
    goal_key = normalize(goal)
    if "perder peso" in goal_key:
        return "ajuda no seu objetivo de perder peso com uma opcao mais leve"
    if "ganhar massa" in goal_key:
        return "tem perfil mais proteico para apoiar ganho de massa"
    if "manter equilibrio" in goal_key:
        return "combina com uma rotina equilibrada"
    if "alimentacao mais saudavel" in goal_key:
        return "prioriza uma escolha mais natural e saudavel"
    if "praticidade na rotina" in goal_key:
        return "funciona bem para uma rotina mais pratica"
    return "ele combina com seu cadastro"


def recommend_item(telegram_id: int | None, restriction: str, goal: str = "") -> sqlite3.Row | None:
    compatible = compatible_menu_items(restriction)
    if not compatible:
        return None
    history = {}
    if telegram_id:
        for row in top_dishes(telegram_id, 10):
            history[row["dish_key"]] = row["total"]
    return max(
        compatible,
        key=lambda item: (history.get(item["dish_key"], 0) * 3 + goal_score(goal, item), goal_score(goal, item)),
    )
def current_week_start() -> str:
    today = date.today()
    return today.fromordinal(today.toordinal() - today.weekday()).isoformat()



def save_client(
    telegram_id: int,
    chat_id: int,
    name: str,
    phone: str,
    address: str,
    restriction: str,
    goal: str = "Nao informado",
    consent_accepted: bool = True,
    consented_at: str | None = None,
) -> None:
    timestamp = now_iso()
    consent_timestamp = consented_at or (timestamp if consent_accepted else "")
    consent_value = 1 if consent_accepted else 0
    with db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "company" in columns:
            conn.execute(
                """
                INSERT INTO clients (
                    telegram_id, chat_id, name, company, phone, address, goal,
                    restriction, consent_accepted, consented_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    name = excluded.name,
                    company = excluded.company,
                    phone = excluded.phone,
                    address = excluded.address,
                    goal = excluded.goal,
                    restriction = excluded.restriction,
                    consent_accepted = excluded.consent_accepted,
                    consented_at = excluded.consented_at,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_id,
                    chat_id,
                    name,
                    address,
                    phone,
                    address,
                    goal,
                    restriction,
                    consent_value,
                    consent_timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            return
        conn.execute(
            """
            INSERT INTO clients (
                telegram_id, chat_id, name, phone, address, goal, restriction,
                consent_accepted, consented_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                name = excluded.name,
                phone = excluded.phone,
                address = excluded.address,
                goal = excluded.goal,
                restriction = excluded.restriction,
                consent_accepted = excluded.consent_accepted,
                consented_at = excluded.consented_at,
                updated_at = excluded.updated_at
            """,
            (
                telegram_id,
                chat_id,
                name,
                phone,
                address,
                goal,
                restriction,
                consent_value,
                consent_timestamp,
                timestamp,
                timestamp,
            ),
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


def favorite_items(telegram_id: int, limit: int = 10) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT dish_key, dish_name, created_at
            FROM favorite_waitlist
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()


def client_data_snapshot(telegram_id: int) -> dict:
    return {
        "client": load_client(telegram_id),
        "recent_orders": recent_orders(telegram_id, 10),
        "favorites": favorite_items(telegram_id, 10),
    }


def delete_client_data(telegram_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM favorite_waitlist WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM orders WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM clients WHERE telegram_id = ?", (telegram_id,))

def recent_orders(telegram_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT dish_key, dish_name, ordered_at FROM orders WHERE telegram_id = ? ORDER BY ordered_at DESC LIMIT ?",
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



def admin_report_data() -> dict:
    with db() as conn:
        totals = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM clients) AS clients,
                (SELECT COUNT(*) FROM clients WHERE consent_accepted = 1) AS consented_clients,
                (SELECT COUNT(*) FROM orders) AS orders,
                (SELECT COUNT(*) FROM favorite_waitlist) AS favorites,
                (SELECT COUNT(*) FROM menu_items WHERE available = 1) AS available_items
            """
        ).fetchone()
        recent = conn.execute(
            """
            SELECT c.name, o.dish_name, o.ordered_at
            FROM orders o
            LEFT JOIN clients c ON c.telegram_id = o.telegram_id
            ORDER BY o.ordered_at DESC
            LIMIT 5
            """
        ).fetchall()
        top = conn.execute(
            """
            SELECT dish_name, COUNT(*) AS total
            FROM orders
            GROUP BY dish_key, dish_name
            ORDER BY total DESC, MAX(ordered_at) DESC
            LIMIT 5
            """
        ).fetchall()
        goals = conn.execute(
            """
            SELECT goal, COUNT(*) AS total
            FROM clients
            GROUP BY goal
            ORDER BY total DESC, goal
            """
        ).fetchall()
    return {"totals": totals, "recent": recent, "top": top, "goals": goals}


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
                "goal": client["goal"],
                "restriction": client["restriction"],
                "consent_accepted": bool(client["consent_accepted"]),
                "consented_at": client["consented_at"],
                "registered": bool(client["consent_accepted"]),
            }
        )

def registered(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(profile(context).get("registered"))


def has_registration_data(context: ContextTypes.DEFAULT_TYPE) -> bool:
    data = profile(context)
    return bool(data.get("name") and data.get("phone") and data.get("address") and data.get("restriction"))


def needs_consent(context: ContextTypes.DEFAULT_TYPE) -> bool:
    data = profile(context)
    return has_registration_data(context) and not data.get("consent_accepted")


async def ask_registration_gate(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    if needs_consent(context):
        await ask_consent(update, context, edit=edit)
    else:
        await ask_name(update, context, edit=edit)

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


def goal_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("\U0001f343 Perder peso", "goal_weight_loss"), ("\U0001f4aa Ganhar massa", "goal_muscle_gain")],
        [("\u2696\ufe0f Manter equilibrio", "goal_maintenance")],
        [("\U0001f957 Alimentacao saudavel", "goal_health"), ("\u23f1\ufe0f Praticidade", "goal_practical")],
    ]

def consent_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("✅ Aceito e quero continuar", "lgpd_accept")],
        [("❌ Nao aceito", "lgpd_decline")],
    ]

def menu_payload(day: str | None = None, restriction: str = "") -> dict:
    items = list_menu_items(day)
    if not items:
        return {"text": "\U0001f614 Ainda nao temos pratos disponiveis para esse dia.", "buttons": [[("\U0001f957 Ver cardapio completo", "menu_today")]]}
    lines = ["\U0001f957 <b>Cardapio disponivel:</b>"]
    buttons = []
    for item in items:
        tags = f" - {escape(item['tags'])}" if item["tags"] else ""
        allergens = f"\nAlergenicos: {escape(item['allergens'])}" if item["allergens"] else ""
        conflict = restriction_conflict(restriction, item) if restriction else None
        safety = f"\n\U0001f6a0 Atencao: {escape(conflict)}" if conflict else "\n\u2705 Compativel com seu cadastro"
        lines.append(
            f"<b>{escape(item['dish_name'])}</b> - {format_price(item['price_cents'])}\n"
            f"Dia: {escape(item['day_of_week'])}{tags}{allergens}{safety}"
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



def safety_warning_payload(item: sqlite3.Row, restriction: str, reason: str) -> dict:
    alternatives = compatible_menu_items(restriction)
    buttons = [[(f"\U0001f957 Pedir {alt['dish_name'][:28]}", f"dish:{alt['dish_key']}")] for alt in alternatives[:3]]
    buttons.append([("\U0001f957 Ver cardapio", "menu_today"), ("\U0001f464 Meu perfil", "profile")])
    return {
        "text": (
            "\u26a0\ufe0f <b>Antes de confirmar, preciso te avisar:</b>\n\n"
            f"O prato <b>{escape(item['dish_name'])}</b> {escape(reason)} e pode nao combinar com sua restricao cadastrada: "
            f"<b>{escape(restriction)}</b>.\n\n"
            "Por seguranca, nao registrei esse pedido. Separei algumas opcoes mais adequadas para voce \U0001f33f"
        ),
        "buttons": buttons,
    }


def recommendation_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    data = profile(context)
    item = recommend_item(data.get("telegram_id"), data.get("restriction", ""), data.get("goal", ""))
    if not item:
        return {
            "text": (
                "\U0001f614 Ainda nao encontrei uma recomendacao segura com o cardapio atual.\n\n"
                "Posso te mostrar o cardapio completo para voce escolher com calma?"
            ),
            "buttons": [[("\U0001f957 Ver cardapio", "menu_today"), ("\U0001f464 Meu perfil", "profile")]],
        }
    recent_keys = {row["dish_key"] for row in recent_orders(data.get("telegram_id"), 10)} if data.get("telegram_id") else set()
    reason = "ele combina com seu cadastro"
    if goal_score(data.get("goal", ""), item):
        reason = goal_reason(data.get("goal", ""))
    elif item["dish_key"] in recent_keys:
        reason = "voce ja pediu antes e ele continua compativel com seu cadastro"
    elif item["tags"]:
        reason = f"ele tem perfil {escape(item['tags'])} e respeita seu cadastro"
    return {
        "text": (
            "\u2b50 <b>Minha recomendacao para hoje:</b>\n\n"
            f"<b>{escape(item['dish_name'])}</b> - {format_price(item['price_cents'])}\n"
            f"Escolhi porque {reason} \U0001f33f"
        ),
        "buttons": [[(f"\U0001f37d Pedir {item['dish_name'][:30]}", f"dish:{item['dish_key']}")], [("\U0001f957 Ver cardapio", "menu_today")]],
    }

def profile_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    data = profile(context)
    user_id = data.get("telegram_id")
    top_text = "Ainda sem historico de pedidos."
    recent_text = "Ainda sem pedidos registrados."
    if user_id:
        top = top_dishes(user_id)
        recent = recent_orders(user_id)
        newline = chr(10)
        if top:
            top_text = newline.join(f"- {escape(row['dish_name'])}: {row['total']} pedido(s)" for row in top)
        if recent:
            recent_text = newline.join(f"- {escape(row['dish_name'])}" for row in recent)
    consent_text = "Aceito"
    if data.get("consented_at"):
        consent_text = f"Aceito em {escape(data.get('consented_at'))}"
    elif not data.get("consent_accepted"):
        consent_text = "Pendente"
    return {
        "text": f"""
👤 <b>Seu perfil:</b>

<b>Nome:</b> {escape(data.get('name', DEFAULT_USER_NAME))}
<b>Telefone:</b> {escape(data.get('phone', 'Nao informado'))}
<b>Endereco/bairro:</b> {escape(data.get('address', 'Nao informado'))}
<b>Objetivo:</b> {escape(data.get('goal', 'Nao informado'))}
<b>Restricao alimentar:</b> {escape(data.get('restriction', 'Nao informado'))}
<b>Consentimento LGPD:</b> {consent_text}

<b>Mais pedidos:</b>
{top_text}

<b>Historico recente:</b>
{recent_text}
""".strip(),
        "buttons": [[("✏️ Atualizar cadastro", "restart_registration"), ("✅ Esta correto", "thanks")]],
    }

async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "name"
    await send_text(
        update,
        (
            "\U0001f37d\ufe0f <b>Antes de iniciar seu pedido, preciso fazer um cadastro rapido.</b>\n\n"
            "Assim consigo considerar suas restricoes, objetivo e identificar seu pedido corretamente \U0001f33f\n\n"
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


async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "goal"
    await send_text(
        update,
        "\U0001f3af Qual e o seu foco principal com a alimentacao agora?",
        context,
        goal_buttons(),
        edit=edit,
    )


async def ask_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "restriction"
    await send_text(update, "\U0001f33f Para sua seguranca alimentar, selecione sua principal restricao:", context, restriction_buttons(), edit=edit)


async def ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "consent"
    await send_text(
        update,
        """
🔒 <b>Consentimento e privacidade</b>

Para continuar, preciso do seu aceite para guardar nome, telefone, endereco/bairro, objetivo, restricao alimentar, historico de pedidos e pratos favoritos/aguardados.

Usamos esses dados para personalizar recomendacoes, registrar pedidos e avisar quando um prato que voce gosta voltar ao cardapio 🔔

Voce pode consultar seus dados com /meus_dados e excluir tudo quando quiser com /excluir_dados.
""".strip(),
        context,
        consent_buttons(),
        edit=edit,
    )


async def complete_registration_after_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    data = profile(context)
    data["registered"] = True
    data["consent_accepted"] = True
    data["consented_at"] = now_iso()
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
            data.get("restriction", "Nao informado"),
            data.get("goal", "Nao informado"),
            True,
            data["consented_at"],
        )
    context.user_data.pop(REGISTRATION_STEP, None)
    await send_text(
        update,
        f"""
✅ <b>Cadastro concluido!</b>

<b>Nome:</b> {{name}}
<b>Telefone:</b> {escape(data.get('phone', 'Nao informado'))}
<b>Endereco/bairro:</b> {escape(data.get('address', 'Nao informado'))}
<b>Objetivo:</b> {escape(data.get('goal', 'Nao informado'))}
<b>Restricao alimentar:</b> {escape(data.get('restriction', 'Nao informado'))}
<b>Consentimento:</b> aceito em {escape(data['consented_at'])}

Agora posso te ajudar com o cardapio e seus pedidos 😊
""".strip(),
        context,
        main_buttons(),
        edit,
    )


async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, restriction: str, edit: bool = False) -> None:
    data = profile(context)
    data["restriction"] = restriction
    data["consent_accepted"] = False
    await ask_consent(update, context, edit=edit)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if registered(context):
        await send_text(
            update,
            (
                "🍽️ <b>Ola, {name}!</b>\n\n"
                "Sou o bot da Apetit. Posso te ajudar com cardapio, pedidos, recomendacoes e avisos de pratos favoritos 🌿"
            ),
            context,
            main_buttons(),
        )
        return
    await ask_registration_gate(update, context)


async def reset_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await ask_name(update, context)


async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = tg_id(update)
    if not user_id or not registered(context):
        await ask_registration_gate(update, context)
        return
    rows = recent_orders(user_id, 10)
    if not rows:
        await send_text(update, "📋 Voce ainda nao tem pedidos registrados.", context, main_buttons())
        return
    await send_text(update, "📋 <b>Seu historico de pedidos:</b>\n\n" + chr(10).join(f"- {escape(row['dish_name'])}" for row in rows), context, main_buttons())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_registration_gate(update, context)
        return
    await send_payload(update, menu_payload(restriction=profile(context).get("restriction", "")), context)


async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = tg_id(update)
    if not user_id:
        await send_text(update, "😔 Nao consegui identificar seu usuario agora. Tente novamente em instantes.", context)
        return
    snapshot = client_data_snapshot(user_id)
    client = snapshot["client"]
    if not client:
        await ask_registration_gate(update, context)
        return
    newline = chr(10)
    orders = snapshot["recent_orders"]
    favorites = snapshot["favorites"]
    order_text = newline.join(f"- {escape(row['dish_name'])}" for row in orders) if orders else "Ainda sem pedidos registrados."
    favorite_text = newline.join(f"- {escape(row['dish_name'])}" for row in favorites) if favorites else "Ainda sem pratos aguardados."
    consent = "sim" if client["consent_accepted"] else "pendente"
    await send_text(
        update,
        f"""
🔒 <b>Seus dados salvos na Apetit</b>

<b>Nome:</b> {escape(client['name'])}
<b>Telefone:</b> {escape(client['phone'])}
<b>Endereco/bairro:</b> {escape(client['address'])}
<b>Objetivo:</b> {escape(client['goal'])}
<b>Restricao alimentar:</b> {escape(client['restriction'])}
<b>Consentimento:</b> {consent}
<b>Data do aceite:</b> {escape(client['consented_at'] or 'Nao informado')}

<b>Historico recente:</b>
{order_text}

<b>Pratos favoritos/aguardados:</b>
{favorite_text}

Para apagar tudo, envie /excluir_dados.
""".strip(),
        context,
        main_buttons(),
    )


async def confirm_delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = tg_id(update)
    if not user_id or not load_client(user_id):
        context.user_data.clear()
        await send_text(update, "📋 Nao encontrei dados cadastrados para excluir.", context)
        return
    await send_text(
        update,
        """
⚠️ <b>Excluir seus dados?</b>

Vou apagar seu cadastro, telefone, endereco/bairro, objetivo, restricao alimentar, historico de pedidos e pratos favoritos/aguardados.

Depois disso, para pedir novamente sera necessario fazer um novo cadastro.
""".strip(),
        context,
        [[("✅ Sim, excluir tudo", "delete_my_data_confirm")], [("❌ Cancelar", "delete_my_data_cancel")]],
    )

def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_TELEGRAM_IDS


async def deny_admin(update: Update, action: str) -> None:
    if not ADMIN_TELEGRAM_IDS:
        await update.effective_message.reply_text(
            "\U0001f512 Nenhum administrador configurado. "
            "Defina ADMIN_TELEGRAM_IDS no .env com os IDs de Telegram autorizados para liberar os comandos administrativos."
        )
        return
    await update.effective_message.reply_text(f"\U0001f512 Apenas administradores podem {action}.")


async def add_menu_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await deny_admin(update, "cadastrar pratos")
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
    if not is_admin(tg_id(update)):
        await deny_admin(update, "ver o cardapio cadastrado")
        return
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



async def show_admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await deny_admin(update, "ver o relatorio")
        return
    report = admin_report_data()
    totals = report["totals"]
    lines = [
        "\U0001f4ca Relatorio Apetit Bot",
        "",
        f"Clientes cadastrados: {totals['clients']}",
        f"Clientes com consentimento: {totals['consented_clients']}",
        f"Pedidos registrados: {totals['orders']}",
        f"Pratos aguardados/favoritos: {totals['favorites']}",
        f"Pratos disponiveis no cardapio: {totals['available_items']}",
        "",
        "Pratos mais pedidos:",
    ]
    if report["top"]:
        lines.extend(f"- {row['dish_name']}: {row['total']} pedido(s)" for row in report["top"])
    else:
        lines.append("- Ainda sem pedidos.")
    lines.extend(["", "Objetivos dos clientes:"])
    if report["goals"]:
        lines.extend(f"- {row['goal'] or 'Nao informado'}: {row['total']}" for row in report["goals"])
    else:
        lines.append("- Ainda sem clientes cadastrados.")
    lines.extend(["", "Pedidos recentes:"])
    if report["recent"]:
        lines.extend(f"- {row['name'] or 'Cliente'} pediu {row['dish_name']}" for row in report["recent"])
    else:
        lines.append("- Ainda sem pedidos recentes.")
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
        await deny_admin(update, "atualizar o cardapio semanal")
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
        await send_text(update, "Me envie uma resposta um pouco mais completa, por favor 😊", context)
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
        await ask_goal(update, context)
    elif step == "goal":
        data["goal"] = text
        await ask_restriction(update, context)
    elif step == "consent":
        await ask_consent(update, context)
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
    if data == "lgpd_accept":
        await complete_registration_after_consent(update, context, edit=True)
        return
    if data == "lgpd_decline":
        user_id = tg_id(update)
        if user_id:
            delete_client_data(user_id)
        context.user_data.clear()
        await send_text(update, "🔒 Sem o aceite, nao consigo guardar seus dados nem iniciar pedidos por aqui.\n\nQuando quiser continuar, envie /start e fazemos o cadastro novamente 😊", context, edit=True)
        return
    if data == "delete_my_data_confirm":
        user_id = tg_id(update)
        if user_id:
            delete_client_data(user_id)
        context.user_data.clear()
        await send_text(update, "✅ Dados excluidos com sucesso. Se quiser pedir novamente, envie /start para fazer um novo cadastro 🌿", context, edit=True)
        return
    if data == "delete_my_data_cancel":
        await send_text(update, "Tudo bem, mantive seus dados como estavam 😊", context, main_buttons(), edit=True)
        return
    if data in GOALS:
        profile(context)["goal"] = GOALS[data]
        await ask_restriction(update, context, edit=True)
        return
    if data in RESTRICTIONS:
        await finish_registration(update, context, RESTRICTIONS[data], edit=True)
        return
    if data == "profile":
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        await send_payload(update, profile_payload(context), context, edit=True)
        return
    if data == "menu_today":
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        await send_payload(update, menu_payload(restriction=profile(context).get("restriction", "")), context, edit=True)
        return
    if data.startswith("dish:"):
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        key = data.removeprefix("dish:")
        user_id = tg_id(update)
        item = load_menu_item(key)
        if not item:
            await send_payload(update, order_payload(key), context, edit=True)
            return
        conflict = restriction_conflict(profile(context).get("restriction", ""), item)
        if conflict:
            await send_payload(update, safety_warning_payload(item, profile(context).get("restriction", ""), conflict), context, edit=True)
            return
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
            await send_text(update, "✅ Combinado! Vou te avisar quando esse prato voltar ao cardapio 🔔", context, main_buttons(), edit=True)
        else:
            await send_text(update, "Escolha um prato primeiro para eu acompanhar 😊", context, main_buttons(), edit=True)
        return
    if data == "recommend":
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        await send_payload(update, recommendation_payload(context), context, edit=True)
        return
    await send_text(update, "Como posso ajudar? 😊", context, main_buttons(), edit=True)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if await handle_registration_message(update, context):
        return
    if not registered(context):
        await ask_registration_gate(update, context)
        return
    text = normalize(update.message.text or "")
    item = find_menu_item_in_text(update.message.text or "")
    if item:
        user_id = tg_id(update)
        conflict = restriction_conflict(profile(context).get("restriction", ""), item)
        if conflict:
            await send_payload(update, safety_warning_payload(item, profile(context).get("restriction", ""), conflict), context)
            return
        if user_id:
            record_order(user_id, item["dish_key"])
            context.user_data["last_order"] = item["dish_key"]
        await send_payload(update, order_payload(item["dish_key"]), context)
    elif "cardapio" in text or "tem hoje" in text or "o que tem" in text:
        await send_payload(update, menu_payload(restriction=profile(context).get("restriction", "")), context)
    elif "recomenda" in text or "sugestao" in text or "sugestão" in text:
        await send_payload(update, recommendation_payload(context), context)
    elif "perfil" in text or "cadastro" in text:
        await send_payload(update, profile_payload(context), context)
    elif "historico" in text:
        await show_history(update, context)
    else:
        await send_text(update, "Quer ver o cardapio ou acessar seu perfil? 😊", context, main_buttons())

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
    app.add_handler(CommandHandler("meus_dados", show_my_data))
    app.add_handler(CommandHandler("excluir_dados", confirm_delete_my_data))
    app.add_handler(CommandHandler("cardapio_add", add_menu_item))
    app.add_handler(CommandHandler("cardapio_list", list_admin_menu))
    app.add_handler(CommandHandler("cardapio_semana", update_weekly_menu))
    app.add_handler(CommandHandler("relatorio", show_admin_report))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").rstrip("/")
    if webhook_url:
        webhook_path = os.getenv("TELEGRAM_WEBHOOK_PATH", "telegram-webhook").strip("/")
        port = int(os.getenv("PORT", "8000"))
        secret_token = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or None
        logger.info("Apetit Bot iniciado em webhook na porta %s.", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=webhook_path,
            webhook_url=f"{webhook_url}/{webhook_path}",
            secret_token=secret_token,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Apetit Bot iniciado em polling.")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
