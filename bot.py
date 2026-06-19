import logging
import os
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
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

import nutrition

load_dotenv()
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
# O httpx inclui a URL completa da Bot API nos logs INFO; essa URL contem o token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DB_PATH = Path(os.getenv("APETIT_DB_PATH", "apetit.db"))
DEFAULT_USER_NAME = os.getenv("APETIT_USER_NAME", "Colaborador")
REGISTRATION_STEP = "registration_step"
NUTRITION_STEP = "nutrition_step"
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


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
                company TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL DEFAULT '',
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
            CREATE TABLE IF NOT EXISTS nutrition_profiles (
                telegram_id INTEGER PRIMARY KEY,
                weight_kg REAL NOT NULL,
                height_cm REAL NOT NULL,
                age INTEGER NOT NULL,
                sex TEXT NOT NULL,
                activity TEXT NOT NULL,
                goal TEXT NOT NULL,
                resting_calories INTEGER NOT NULL,
                maintenance_calories INTEGER NOT NULL,
                target_calories INTEGER NOT NULL,
                protein_grams INTEGER NOT NULL,
                consented_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gamification (
                telegram_id INTEGER PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0,
                streak INTEGER NOT NULL DEFAULT 0,
                last_active_date TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS gamification_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_key TEXT NOT NULL,
                points INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (telegram_id, event_type, event_key, event_date)
            );
            CREATE TABLE IF NOT EXISTS user_badges (
                telegram_id INTEGER NOT NULL,
                badge_key TEXT NOT NULL,
                awarded_at TEXT NOT NULL,
                PRIMARY KEY (telegram_id, badge_key)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "phone" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN phone TEXT NOT NULL DEFAULT ''")
        if "address" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        if "company" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN company TEXT NOT NULL DEFAULT ''")
        if "department" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN department TEXT NOT NULL DEFAULT ''")
        conn.execute("UPDATE clients SET phone = '', address = '' WHERE phone <> '' OR address <> ''")
        if "goal" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT 'Nao informado'")
        if "consent_accepted" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN consent_accepted INTEGER NOT NULL DEFAULT 0")
        if "consented_at" not in columns:
            conn.execute("ALTER TABLE clients ADD COLUMN consented_at TEXT NOT NULL DEFAULT ''")
        nutrition_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(nutrition_profiles)").fetchall()
        }
        if "consented_at" not in nutrition_columns:
            conn.execute("ALTER TABLE nutrition_profiles ADD COLUMN consented_at TEXT NOT NULL DEFAULT ''")
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


def compatible_menu_items(restriction: str, day: str | None = None) -> list[sqlite3.Row]:
    return [item for item in list_menu_items(day) if not restriction_conflict(restriction, item)]


def goal_score(goal: str, item: sqlite3.Row) -> int:
    text = item_search_text(item)
    goal_key = normalize(goal)
    keywords = {
        "perder peso": ("leve", "salada", "legumes", "sopa", "perda de peso", "grelhado", "abobora"),
        "ganhar massa": ("proteico", "frango", "patinho", "ovo", "lentilha", "grao de bico", "batata doce"),
        "manter equilibrio": ("equilibrado", "integral", "caseiro", "legumes", "arroz", "feijao"),
        "alimentacao mais saudavel": ("saudavel", "vegano", "integral", "legumes", "natural", "sem gluten", "quinoa"),
        "praticidade na rotina": ("pratico", "wrap", "bowl", "todos", "leve"),
    }
    for key, terms in keywords.items():
        if key in goal_key:
            score = sum(term in text for term in terms)
            if key == "ganhar massa" and "proteico" in text:
                score += 2
            return score
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
    return "combina com seu cadastro"


def recommend_item(
    telegram_id: int | None,
    restriction: str,
    goal: str = "",
    day: str | None = None,
) -> sqlite3.Row | None:
    compatible = compatible_menu_items(restriction, day)
    if not compatible:
        return None
    history: dict[str, int] = {}
    if telegram_id:
        for row in top_dishes(telegram_id, 10):
            history[row["dish_key"]] = row["total"]
    return max(
        compatible,
        key=lambda item: (
            history.get(item["dish_key"], 0) * 3 + goal_score(goal, item),
            goal_score(goal, item),
        ),
    )


def current_week_start() -> str:
    today = date.today()
    return today.fromordinal(today.toordinal() - today.weekday()).isoformat()


def current_day_name() -> str:
    return ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")[date.today().weekday()]


def save_employee(
    telegram_id: int,
    chat_id: int,
    name: str,
    phone: str,
    address: str,
    restriction: str,
    goal: str = "Nao informado",
    consent_accepted: bool = True,
    consented_at: str | None = None,
    company: str = "",
    department: str = "",
) -> None:
    timestamp = now_iso()
    consent_timestamp = consented_at or (timestamp if consent_accepted else "")
    consent_value = int(consent_accepted)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO clients (
                telegram_id, chat_id, name, phone, address, company, department, goal, restriction,
                consent_accepted, consented_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                name = excluded.name,
                phone = excluded.phone,
                address = excluded.address,
                company = excluded.company,
                department = excluded.department,
                goal = excluded.goal,
                restriction = excluded.restriction,
                consent_accepted = excluded.consent_accepted,
                consented_at = excluded.consented_at,
                updated_at = excluded.updated_at
            """,
            (
                telegram_id, chat_id, name, phone, address, company, department, goal, restriction,
                consent_value, consent_timestamp, timestamp, timestamp,
            ),
        )


def load_employee(telegram_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute("SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)).fetchone()


def save_nutrition_profile(
    telegram_id: int,
    weight_kg: float,
    height_cm: float,
    age: int,
    sex: str,
    activity: str,
    goal: str,
    consented_at: str | None = None,
) -> nutrition.NutritionTargets:
    targets = nutrition.calculate_targets(weight_kg, height_cm, age, sex, activity, goal)
    nutrition_consent = consented_at or now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO nutrition_profiles (
                telegram_id, weight_kg, height_cm, age, sex, activity, goal,
                resting_calories, maintenance_calories, target_calories,
                protein_grams, consented_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                weight_kg = excluded.weight_kg,
                height_cm = excluded.height_cm,
                age = excluded.age,
                sex = excluded.sex,
                activity = excluded.activity,
                goal = excluded.goal,
                resting_calories = excluded.resting_calories,
                maintenance_calories = excluded.maintenance_calories,
                target_calories = excluded.target_calories,
                protein_grams = excluded.protein_grams,
                consented_at = excluded.consented_at,
                updated_at = excluded.updated_at
            """,
            (
                telegram_id,
                weight_kg,
                height_cm,
                age,
                sex,
                activity,
                goal,
                targets.resting_calories,
                targets.maintenance_calories,
                targets.target_calories,
                targets.protein_grams,
                nutrition_consent,
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE clients SET goal = ?, updated_at = ? WHERE telegram_id = ?",
            (goal, now_iso(), telegram_id),
        )
    return targets


def load_nutrition_profile(telegram_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute("SELECT * FROM nutrition_profiles WHERE telegram_id = ?", (telegram_id,)).fetchone()


def gamification_summary(telegram_id: int) -> dict:
    with db() as conn:
        game = conn.execute("SELECT * FROM gamification WHERE telegram_id = ?", (telegram_id,)).fetchone()
        badges = conn.execute(
            "SELECT badge_key, awarded_at FROM user_badges WHERE telegram_id = ? ORDER BY awarded_at",
            (telegram_id,),
        ).fetchall()
    return {
        "points": game["points"] if game else 0,
        "streak": game["streak"] if game else 0,
        "last_active_date": game["last_active_date"] if game else "",
        "badges": badges,
    }


def award_points(telegram_id: int, event_type: str, points: int, event_key: str = "daily") -> dict:
    event_date = date.today().isoformat()
    timestamp = now_iso()
    with db() as conn:
        if event_type in {"follow_recommendation", "partial_recommendation"}:
            already_scored = conn.execute(
                """
                SELECT 1 FROM gamification_events
                WHERE telegram_id = ?
                  AND event_key = ?
                  AND event_date = ?
                  AND event_type IN ('follow_recommendation', 'partial_recommendation')
                """,
                (telegram_id, event_key, event_date),
            ).fetchone()
            if already_scored:
                return {"awarded": False, "points": 0, "new_badges": []}
        inserted = conn.execute(
            """
            INSERT OR IGNORE INTO gamification_events (
                telegram_id, event_type, event_key, points, event_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (telegram_id, event_type, event_key, points, event_date, timestamp),
        )
        if inserted.rowcount == 0:
            return {"awarded": False, "points": 0, "new_badges": []}

        current = conn.execute("SELECT * FROM gamification WHERE telegram_id = ?", (telegram_id,)).fetchone()
        current_points = current["points"] if current else 0
        current_streak = current["streak"] if current else 0
        last_active = current["last_active_date"] if current else ""
        streak = nutrition.next_streak(last_active or None, event_date, current_streak)
        total_points = current_points + points
        conn.execute(
            """
            INSERT INTO gamification (telegram_id, points, streak, last_active_date, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                points = excluded.points,
                streak = excluded.streak,
                last_active_date = excluded.last_active_date,
                updated_at = excluded.updated_at
            """,
            (telegram_id, total_points, streak, event_date, timestamp),
        )
        salad_events = conn.execute(
            "SELECT COUNT(*) FROM gamification_events WHERE telegram_id = ? AND event_type = 'salad'",
            (telegram_id,),
        ).fetchone()[0]
        earned = nutrition.earned_badges(total_points, streak, salad_events)
        existing = {
            row["badge_key"]
            for row in conn.execute("SELECT badge_key FROM user_badges WHERE telegram_id = ?", (telegram_id,))
        }
        new_badges = sorted(earned - existing)
        for badge_key in new_badges:
            conn.execute(
                "INSERT INTO user_badges (telegram_id, badge_key, awarded_at) VALUES (?, ?, ?)",
                (telegram_id, badge_key, timestamp),
            )
    return {"awarded": True, "points": points, "total_points": total_points, "streak": streak, "new_badges": new_badges}


def leaderboard(limit: int = 10) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT telegram_id, points, streak
            FROM gamification
            WHERE points > 0
            ORDER BY points DESC, streak DESC, updated_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


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


def employee_data_snapshot(telegram_id: int) -> dict:
    return {
        "employee": load_employee(telegram_id),
        "nutrition": load_nutrition_profile(telegram_id),
        "gamification": gamification_summary(telegram_id),
        "recent_orders": recent_orders(telegram_id, 10),
        "favorites": favorite_items(telegram_id, 10),
    }


def delete_employee_data(telegram_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM user_badges WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM gamification_events WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM gamification WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM nutrition_profiles WHERE telegram_id = ?", (telegram_id,))
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
                (SELECT COUNT(*) FROM clients) AS employees,
                (SELECT COUNT(*) FROM clients WHERE consent_accepted = 1) AS consented_employees,
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
        companies = conn.execute(
            """
            SELECT company, COUNT(*) AS total
            FROM clients
            GROUP BY company
            ORDER BY total DESC, company
            """
        ).fetchall()
    return {"totals": totals, "recent": recent, "top": top, "goals": goals, "companies": companies}


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
    if not user_id or context.user_data.get(REGISTRATION_STEP) or profile(context).get("registered"):
        return
    employee = load_employee(user_id)
    if employee:
        profile(context).update(
            {
                "telegram_id": employee["telegram_id"],
                "name": employee["name"],
                "company": employee["company"],
                "department": employee["department"],
                "goal": employee["goal"],
                "restriction": employee["restriction"],
                "consent_accepted": bool(employee["consent_accepted"]),
                "consented_at": employee["consented_at"],
                "registered": bool(
                    employee["consent_accepted"] and employee["company"] and employee["department"]
                ),
            }
        )


def registered(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(profile(context).get("registered"))


def has_registration_data(context: ContextTypes.DEFAULT_TYPE) -> bool:
    data = profile(context)
    return all(data.get(field) for field in ("name", "company", "department", "goal", "restriction"))


def needs_consent(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return has_registration_data(context) and not profile(context).get("consent_accepted")


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
        [("\U0001f3af Minha meta", "nutrition_profile"), ("\U0001f3c6 Meu progresso", "nutrition_progress")],
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
        [("\u2705 Aceito e quero continuar", "lgpd_accept")],
        [("\u274c Nao aceito", "lgpd_decline")],
    ]


def nutrition_sex_buttons() -> list[list[tuple[str, str]]]:
    return [[("Masculino", "nutrition_sex:male"), ("Feminino", "nutrition_sex:female")]]


def nutrition_activity_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("Sedentaria", "nutrition_activity:sedentary")],
        [("Leve", "nutrition_activity:light"), ("Moderada", "nutrition_activity:moderate")],
        [("Alta", "nutrition_activity:high")],
    ]


def nutrition_goal_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("Perder peso", "nutrition_goal:goal_weight_loss"), ("Ganhar massa", "nutrition_goal:goal_muscle_gain")],
        [("Manter equilibrio", "nutrition_goal:goal_maintenance")],
        [("Alimentacao saudavel", "nutrition_goal:goal_health"), ("Praticidade", "nutrition_goal:goal_practical")],
    ]


def nutrition_consent_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("Aceito criar minha meta", "nutrition_consent_accept")],
        [("Nao aceito", "nutrition_consent_decline")],
    ]


def menu_payload(day: str | None = None, restriction: str = "", goal: str = "") -> dict:
    items = list_menu_items(day)
    if not items:
        return {"text": "\U0001f614 Ainda nao temos pratos disponiveis para esse dia.", "buttons": [[("\U0001f957 Ver cardapio completo", "menu_all")]]}
    lines = ["\U0001f957 <b>Cardapio disponivel:</b>"]
    buttons = []
    for item in items:
        tags = f" - {escape(item['tags'])}" if item["tags"] else ""
        allergens = f"\nAlergenicos: {escape(item['allergens'])}" if item["allergens"] else ""
        conflict = restriction_conflict(restriction, item) if restriction else None
        safety = f"\n\U0001f6a0 Atencao: {escape(conflict)}" if conflict else "\n\u2705 Compativel com seu cadastro"
        signal = ""
        if goal:
            signal_key = nutrition.nutrition_signal(goal_score(goal, item), bool(conflict))
            signal_labels = {
                "green": "\U0001f7e2 Boa aderencia a sua meta",
                "yellow": "\U0001f7e1 Opcao compativel; ajuste a porcao",
                "red": "\U0001f534 Conflito com sua restricao",
            }
            signal = f"\nSemaforo: {signal_labels[signal_key]}"
        lines.append(
            f"<b>{escape(item['dish_name'])}</b> - {format_price(item['price_cents'])}\n"
            f"Dia: {escape(item['day_of_week'])}{tags}{allergens}{safety}{signal}"
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
    alternatives = compatible_menu_items(restriction, current_day_name())
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
    user_id = data.get("telegram_id")
    item = recommend_item(
        user_id,
        data.get("restriction", ""),
        data.get("goal", ""),
        current_day_name(),
    )
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
    context.user_data["nutrition_recommendation"] = item["dish_key"]
    nutrition_profile = load_nutrition_profile(user_id) if user_id else None
    if nutrition_profile:
        nutrition_text = (
            f"\n\n\U0001f3af Meta estimada do dia: {nutrition_profile['target_calories']} kcal e "
            f"{nutrition_profile['protein_grams']} g de proteina.\n"
            f"Porcao sugerida: {escape(nutrition.PORTION_GUIDANCE[nutrition_profile['goal']])}"
        )
    else:
        nutrition_text = "\n\nUse /minha_meta para calcular uma estimativa de calorias, proteina e porcao."
    signal_key = nutrition.nutrition_signal(goal_score(data.get("goal", ""), item))
    signal = "\U0001f7e2 Boa aderencia" if signal_key == "green" else "\U0001f7e1 Ajuste a porcao"
    buttons = [[(f"\U0001f37d Pedir {item['dish_name'][:30]}", f"dish:{item['dish_key']}")]]
    if nutrition_profile:
        buttons.append([("Segui em parte (+10)", "nutrition_partial")])
    buttons.append([("\U0001f957 Ver cardapio", "menu_today")])
    return {
        "text": (
            "\u2b50 <b>Minha recomendacao para hoje:</b>\n\n"
            f"<b>{escape(item['dish_name'])}</b> - {format_price(item['price_cents'])}\n"
            f"Semaforo: {signal}\n"
            f"Escolhi porque {reason} \U0001f33f"
            f"{nutrition_text}\n\n"
            "Estimativas educativas; necessidades individuais devem ser avaliadas por nutricionista."
        ),
        "buttons": buttons,
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
    consent_text = "Pendente"
    if data.get("consent_accepted"):
        consent_text = f"Aceito em {escape(data.get('consented_at') or 'data nao informada')}"
    return {
        "text": (
            "\U0001f464 <b>Seu perfil:</b>\n\n"
            f"<b>Nome:</b> {escape(data.get('name', DEFAULT_USER_NAME))}\n"
            f"<b>Empresa/unidade:</b> {escape(data.get('company', 'Nao informada'))}\n"
            f"<b>Setor:</b> {escape(data.get('department', 'Nao informado'))}\n"
            f"<b>Objetivo:</b> {escape(data.get('goal', 'Nao informado'))}\n"
            f"<b>Restricao alimentar:</b> {escape(data.get('restriction', 'Nao informado'))}\n"
            f"<b>Consentimento LGPD:</b> {consent_text}\n\n"
            f"<b>Mais pedidos:</b>\n{top_text}\n\n"
            f"<b>Historico recente:</b>\n{recent_text}"
        ),
        "buttons": [[("\u270f\ufe0f Atualizar cadastro", "restart_registration"), ("\u2705 Esta correto", "thanks")]],
    }


def nutrition_profile_payload(row: sqlite3.Row) -> dict:
    return {
        "text": (
            "\U0001f3af <b>Sua meta nutricional estimada</b>\n\n"
            f"<b>Peso:</b> {row['weight_kg']:g} kg\n"
            f"<b>Altura:</b> {row['height_cm']:g} cm\n"
            f"<b>Idade:</b> {row['age']} anos\n"
            f"<b>Atividade:</b> {escape(nutrition.ACTIVITY_LABELS[row['activity']])}\n"
            f"<b>Objetivo:</b> {escape(row['goal'])}\n\n"
            f"<b>Consentimento nutricional:</b> {escape(row['consented_at'])}\n\n"
            f"<b>Gasto em repouso:</b> {row['resting_calories']} kcal/dia\n"
            f"<b>Manutencao estimada:</b> {row['maintenance_calories']} kcal/dia\n"
            f"<b>Meta calorica estimada:</b> {row['target_calories']} kcal/dia\n"
            f"<b>Proteina estimada:</b> {row['protein_grams']} g/dia\n\n"
            f"<b>Porcao no almoco:</b> {escape(nutrition.PORTION_GUIDANCE[row['goal']])}\n\n"
            "Esta e uma estimativa educativa, nao uma prescricao. Gestantes, pessoas com doencas, "
            "restricoes clinicas ou necessidades especificas devem consultar nutricionista ou medico."
        ),
        "buttons": [
            [("Atualizar dados", "nutrition_restart"), ("Ver progresso", "nutrition_progress")],
            [("Ver ranking", "nutrition_ranking"), ("Ver recomendacao", "recommend")],
        ],
    }


def progress_payload(telegram_id: int) -> dict:
    summary = gamification_summary(telegram_id)
    badge_names = [nutrition.BADGES[row["badge_key"]] for row in summary["badges"]]
    badges = "\n".join(f"- {escape(name)}" for name in badge_names) or "- Nenhum badge ainda."
    return {
        "text": (
            "\U0001f3c6 <b>Seu progresso Apetit</b>\n\n"
            f"<b>Pontos:</b> {summary['points']}\n"
            f"<b>Streak:</b> {summary['streak']} dia(s)\n\n"
            f"<b>Badges:</b>\n{badges}\n\n"
            "Pontos: +5 por dica, +20 por seguir a recomendacao, +10 parcialmente, "
            "+10 por salada e +15 por fruta. Eventos repetidos no mesmo dia nao acumulam."
        ),
        "buttons": [[("Minha meta", "nutrition_profile"), ("Ranking", "nutrition_ranking")]],
    }


def ranking_payload(telegram_id: int) -> dict:
    rows = leaderboard()
    if not rows:
        text = "\U0001f3c6 O ranking ainda esta vazio. Acesse /minha_meta para comecar."
    else:
        lines = ["\U0001f3c6 <b>Ranking Apetit</b>", ""]
        for position, row in enumerate(rows, start=1):
            label = "Voce" if row["telegram_id"] == telegram_id else f"Colaborador {str(row['telegram_id'])[-4:]}"
            lines.append(f"{position}. {label} - {row['points']} pontos - streak {row['streak']}")
        lines.append("\nOs demais participantes aparecem com identificador pseudonimizado.")
        text = "\n".join(lines)
    return {"text": text, "buttons": [[("Meu progresso", "nutrition_progress"), ("Minha meta", "nutrition_profile")]]}


def award_order_gamification(
    telegram_id: int,
    item: sqlite3.Row,
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict]:
    if not load_nutrition_profile(telegram_id):
        return []
    awards = []
    dish_key = item["dish_key"]
    if context.user_data.get("nutrition_recommendation") == dish_key:
        awards.append(award_points(telegram_id, "follow_recommendation", 20, dish_key))
    item_text = item_search_text(item)
    if "salada" in item_text:
        awards.append(award_points(telegram_id, "salad", 10, dish_key))
    if any(term in item_text for term in ("fruta", "banana", "maca", "laranja", "mamao")):
        awards.append(award_points(telegram_id, "fruit", 15, dish_key))
    return awards


def gamification_note(awards: list[dict]) -> str:
    awarded = [award for award in awards if award.get("awarded")]
    if not awarded:
        return ""
    points = sum(award["points"] for award in awarded)
    badge_keys = {key for award in awarded for key in award.get("new_badges", [])}
    badge_text = ""
    if badge_keys:
        names = ", ".join(nutrition.BADGES[key] for key in sorted(badge_keys))
        badge_text = f" Novo badge: {names}."
    return f"\n\n\U0001f3c6 +{points} pontos!{badge_text}"


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "name"
    await send_text(
        update,
        (
            "\U0001f37d\ufe0f <b>Antes de usar o cardapio, preciso fazer seu cadastro funcional.</b>\n\n"
            "Assim consigo vincular sua empresa/unidade e considerar suas preferencias e restricoes \U0001f33f\n\n"
            "Qual e o seu nome completo?"
        ),
        context,
        edit=edit,
    )


async def ask_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "company"
    await send_text(update, "Obrigado, {name}! Qual e a empresa ou unidade onde voce trabalha?", context)


async def ask_department(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "department"
    await send_text(update, "Em qual setor ou area voce trabalha?", context)


async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "goal"
    await send_text(
        update,
        "Qual e o seu foco principal com a alimentacao agora?",
        context,
        goal_buttons(),
        edit=edit,
    )


async def ask_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "restriction"
    await send_text(
        update,
        "Para sua seguranca alimentar, selecione sua principal restricao:",
        context,
        restriction_buttons(),
        edit=edit,
    )


async def finish_registration(update: Update, context: ContextTypes.DEFAULT_TYPE, restriction: str, edit: bool = False) -> None:
    data = profile(context)
    data["restriction"] = restriction
    data["registered"] = False
    data["consent_accepted"] = False
    await ask_consent(update, context, edit=edit)


async def ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "consent"
    await send_text(
        update,
        (
            "\U0001f512 <b>Consentimento e privacidade</b>\n\n"
            "Para continuar, preciso do seu aceite para guardar nome, empresa/unidade, setor, "
            "objetivo, restricao alimentar, historico de refeicoes e pratos favoritos.\n\n"
            "Esses dados personalizam recomendacoes, registram pedidos e permitem avisos de cardapio. "
            "Voce pode consultar seus dados com /meus_dados e excluir tudo com /excluir_dados."
        ),
        context,
        consent_buttons(),
        edit=edit,
    )


async def complete_registration_after_consent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    edit: bool = False,
) -> None:
    data = profile(context)
    if not has_registration_data(context):
        await ask_name(update, context, edit=edit)
        return
    data["registered"] = True
    data["consent_accepted"] = True
    data["consented_at"] = now_iso()
    user_id = tg_id(update)
    current_chat_id = chat_id(update)
    if user_id:
        data["telegram_id"] = user_id
    if user_id and current_chat_id:
        save_employee(
            user_id,
            current_chat_id,
            data.get("name", DEFAULT_USER_NAME),
            "",
            "",
            data.get("restriction", "Nao informado"),
            data.get("goal", "Nao informado"),
            True,
            data["consented_at"],
            company=data.get("company", "Nao informada"),
            department=data.get("department", "Nao informado"),
        )
        nutrition_profile = load_nutrition_profile(user_id)
        if nutrition_profile:
            save_nutrition_profile(
                user_id,
                nutrition_profile["weight_kg"],
                nutrition_profile["height_cm"],
                nutrition_profile["age"],
                nutrition_profile["sex"],
                nutrition_profile["activity"],
                data.get("goal", "Manter equilibrio"),
                nutrition_profile["consented_at"],
            )
    context.user_data.pop(REGISTRATION_STEP, None)
    await send_text(
        update,
        (
            "\u2705 <b>Cadastro concluido!</b>\n\n"
            "<b>Nome:</b> {name}\n"
            f"<b>Empresa/unidade:</b> {escape(data.get('company', 'Nao informada'))}\n"
            f"<b>Setor:</b> {escape(data.get('department', 'Nao informado'))}\n"
            f"<b>Objetivo:</b> {escape(data.get('goal', 'Nao informado'))}\n"
            f"<b>Restricao alimentar:</b> {escape(data.get('restriction', 'Nao informado'))}\n"
            f"<b>Consentimento:</b> aceito em {escape(data['consented_at'])}\n\n"
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
        await send_text(update, "\U0001f4cb Voce ainda nao tem pedidos registrados.", context, main_buttons())
        return
    await send_text(update, "\U0001f4cb <b>Seu historico de pedidos:</b>\n\n" + "\n".join(f"- {escape(row['dish_name'])}" for row in rows), context, main_buttons())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_registration_gate(update, context)
        return
    await send_payload(
        update,
        menu_payload(
            day=current_day_name(),
            restriction=profile(context).get("restriction", ""),
            goal=profile(context).get("goal", ""),
        ),
        context,
    )


def nutrition_draft(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("nutrition_draft", {})


async def ask_nutrition_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    await send_text(
        update,
        (
            "\U0001f512 <b>Consentimento para a meta nutricional</b>\n\n"
            "Para calcular sua estimativa, a Apetit guardara peso, altura, idade, sexo usado pela formula, "
            "nivel de atividade, objetivo, metas calculadas, pontos, streak e badges.\n\n"
            "O ranking mostra somente um identificador pseudonimizado. Voce pode consultar tudo com "
            "/meus_dados e excluir com /excluir_dados. Deseja continuar?"
        ),
        context,
        nutrition_consent_buttons(),
        edit=edit,
    )


async def ask_nutrition_weight(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[NUTRITION_STEP] = "weight"
    context.user_data["nutrition_draft"] = {}
    await send_text(
        update,
        "\U0001f3af Vamos calcular uma meta educativa. Qual e o seu peso em kg? Exemplo: 72,5",
        context,
        edit=edit,
    )


async def ask_nutrition_height(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[NUTRITION_STEP] = "height"
    await send_text(update, "Qual e a sua altura em centimetros? Exemplo: 168", context)


async def ask_nutrition_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[NUTRITION_STEP] = "age"
    await send_text(update, "Qual e a sua idade? A equacao foi validada para pessoas de 19 a 78 anos.", context)


async def ask_nutrition_sex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[NUTRITION_STEP] = "sex"
    await send_text(
        update,
        "Selecione o sexo usado pela equacao de gasto energetico:",
        context,
        nutrition_sex_buttons(),
    )


async def ask_nutrition_activity(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[NUTRITION_STEP] = "activity"
    await send_text(update, "Como e seu nivel habitual de atividade fisica?", context, nutrition_activity_buttons(), edit=edit)


async def ask_nutrition_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[NUTRITION_STEP] = "goal"
    await send_text(update, "Qual objetivo deve orientar sua meta e o cardapio?", context, nutrition_goal_buttons(), edit=edit)


async def complete_nutrition_profile(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    user_id = tg_id(update)
    data = nutrition_draft(context)
    required = {"weight_kg", "height_cm", "age", "sex", "activity", "goal"}
    if not user_id or not required.issubset(data):
        await ask_nutrition_weight(update, context, edit=edit)
        return
    try:
        existing_profile = load_nutrition_profile(user_id)
        save_nutrition_profile(
            user_id,
            data["weight_kg"],
            data["height_cm"],
            data["age"],
            data["sex"],
            data["activity"],
            data["goal"],
            existing_profile["consented_at"] if existing_profile else None,
        )
    except ValueError as error:
        context.user_data.pop(NUTRITION_STEP, None)
        await send_text(update, f"Nao consegui calcular a meta: {escape(str(error))}", context, edit=edit)
        return
    profile(context)["goal"] = data["goal"]
    context.user_data.pop(NUTRITION_STEP, None)
    context.user_data.pop("nutrition_draft", None)
    award = award_points(user_id, "tip", 5, "nutrition_profile")
    row = load_nutrition_profile(user_id)
    payload = nutrition_profile_payload(row)
    payload["text"] += gamification_note([award])
    await send_payload(update, payload, context, edit=edit)


async def show_nutrition_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_registration_gate(update, context, edit=edit)
        return
    user_id = tg_id(update)
    if not user_id:
        return
    row = load_nutrition_profile(user_id)
    if not row:
        await ask_nutrition_consent(update, context, edit=edit)
        return
    award = award_points(user_id, "tip", 5, "nutrition_profile")
    payload = nutrition_profile_payload(row)
    payload["text"] += gamification_note([award])
    await send_payload(update, payload, context, edit=edit)


async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_registration_gate(update, context, edit=edit)
        return
    user_id = tg_id(update)
    if user_id:
        await send_payload(update, progress_payload(user_id), context, edit=edit)


async def show_ranking(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_registration_gate(update, context, edit=edit)
        return
    user_id = tg_id(update)
    if user_id:
        await send_payload(update, ranking_payload(user_id), context, edit=edit)


def parse_decimal(raw: str) -> float:
    return float(raw.strip().replace(",", "."))


async def handle_nutrition_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    step = context.user_data.get(NUTRITION_STEP)
    if not step:
        return False
    text = (update.message.text or "").strip()
    data = nutrition_draft(context)
    try:
        if step == "weight":
            value = parse_decimal(text)
            if not 35 <= value <= 300:
                raise ValueError("Informe um peso entre 35 e 300 kg.")
            data["weight_kg"] = value
            await ask_nutrition_height(update, context)
        elif step == "height":
            value = parse_decimal(text)
            if not 120 <= value <= 230:
                raise ValueError("Informe uma altura entre 120 e 230 cm.")
            data["height_cm"] = value
            await ask_nutrition_age(update, context)
        elif step == "age":
            value = int(text)
            if not 19 <= value <= 78:
                raise ValueError("A estimativa esta disponivel para a faixa validada de 19 a 78 anos.")
            data["age"] = value
            await ask_nutrition_sex(update, context)
        else:
            await send_text(update, "Use os botoes da mensagem anterior para continuar.", context)
    except ValueError as error:
        await send_text(update, escape(str(error)), context)
    return True


async def show_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = tg_id(update)
    if not user_id:
        await send_text(update, "Nao consegui identificar seu usuario. Tente novamente.", context)
        return
    snapshot = employee_data_snapshot(user_id)
    employee = snapshot["employee"]
    if not employee:
        await ask_registration_gate(update, context)
        return
    orders = snapshot["recent_orders"]
    favorites = snapshot["favorites"]
    nutrition_profile = snapshot["nutrition"]
    game = snapshot["gamification"]
    order_text = "\n".join(f"- {escape(row['dish_name'])}" for row in orders) or "Ainda sem pedidos registrados."
    favorite_text = "\n".join(f"- {escape(row['dish_name'])}" for row in favorites) or "Ainda sem pratos aguardados."
    if nutrition_profile:
        nutrition_text = (
            f"Peso: {nutrition_profile['weight_kg']:g} kg; altura: {nutrition_profile['height_cm']:g} cm; "
            f"idade: {nutrition_profile['age']}; atividade: {escape(nutrition.ACTIVITY_LABELS[nutrition_profile['activity']])}; "
            f"meta: {nutrition_profile['target_calories']} kcal e {nutrition_profile['protein_grams']} g de proteina; "
            f"consentimento: {escape(nutrition_profile['consented_at'])}."
        )
    else:
        nutrition_text = "Nenhum perfil nutricional salvo."
    await send_text(
        update,
        (
            "\U0001f512 <b>Seus dados salvos na Apetit</b>\n\n"
            f"<b>Nome:</b> {escape(employee['name'])}\n"
            f"<b>Empresa/unidade:</b> {escape(employee['company'] or 'Nao informada')}\n"
            f"<b>Setor:</b> {escape(employee['department'] or 'Nao informado')}\n"
            f"<b>Objetivo:</b> {escape(employee['goal'])}\n"
            f"<b>Restricao alimentar:</b> {escape(employee['restriction'])}\n"
            f"<b>Consentimento:</b> {'sim' if employee['consent_accepted'] else 'pendente'}\n"
            f"<b>Data do aceite:</b> {escape(employee['consented_at'] or 'Nao informado')}\n\n"
            f"<b>Historico recente:</b>\n{order_text}\n\n"
            f"<b>Pratos favoritos/aguardados:</b>\n{favorite_text}\n\n"
            f"<b>Perfil nutricional:</b>\n{nutrition_text}\n\n"
            f"<b>Gamificacao:</b> {game['points']} pontos, streak de {game['streak']} dia(s), "
            f"{len(game['badges'])} badge(s).\n\n"
            "Para apagar tudo, envie /excluir_dados."
        ),
        context,
        main_buttons(),
    )


async def confirm_delete_my_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    user_id = tg_id(update)
    if not user_id or not load_employee(user_id):
        context.user_data.clear()
        await send_text(update, "Nao encontrei dados cadastrados para excluir.", context)
        return
    await send_text(
        update,
        (
            "\u26a0\ufe0f <b>Excluir seus dados?</b>\n\n"
            "Vou apagar cadastro funcional, empresa/unidade, setor, objetivo, restricao alimentar, historico de refeicoes "
            "e pratos favoritos, perfil nutricional, pontos, streak e badges. Esta acao nao pode ser desfeita."
        ),
        context,
        [[("\u2705 Sim, excluir tudo", "delete_my_data_confirm")], [("\u274c Cancelar", "delete_my_data_cancel")]],
    )


def is_admin(user_id: int | None) -> bool:
    return bool(user_id is not None and user_id in ADMIN_TELEGRAM_IDS)


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
    if not is_admin(tg_id(update)):
        await update.effective_message.reply_text("\U0001f512 Apenas administradores podem listar o cardapio administrativo.")
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
        await update.effective_message.reply_text("\U0001f512 Apenas administradores podem ver o relatorio.")
        return
    report = admin_report_data()
    totals = report["totals"]
    lines = [
        "\U0001f4ca Relatorio Apetit Bot",
        "",
        f"Colaboradores cadastrados: {totals['employees']}",
        f"Colaboradores com consentimento: {totals['consented_employees']}",
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
    lines.extend(["", "Objetivos dos colaboradores:"])
    if report["goals"]:
        lines.extend(f"- {row['goal'] or 'Nao informado'}: {row['total']}" for row in report["goals"])
    else:
        lines.append("- Ainda sem colaboradores cadastrados.")
    lines.extend(["", "Colaboradores por empresa/unidade:"])
    if report["companies"]:
        lines.extend(f"- {row['company'] or 'Nao informada'}: {row['total']}" for row in report["companies"])
    else:
        lines.append("- Ainda sem colaboradores cadastrados.")
    lines.extend(["", "Pedidos recentes:"])
    if report["recent"]:
        lines.extend(f"- {row['name'] or 'Colaborador'} pediu {row['dish_name']}" for row in report["recent"])
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
            logger.exception("Falha ao notificar colaborador %s", row["telegram_id"])
    await update.effective_message.reply_text(f"\u2705 Cardapio semanal atualizado. Colaboradores notificados: {notified}.")


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
        await ask_company(update, context)
    elif step == "company":
        data["company"] = text
        await ask_department(update, context)
    elif step == "department":
        data["department"] = text
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
            delete_employee_data(user_id)
        context.user_data.clear()
        await send_text(
            update,
            "Sem o aceite, nao consigo guardar seus dados nem iniciar pedidos. Envie /start quando quiser continuar.",
            context,
            edit=True,
        )
        return
    if data == "delete_my_data_confirm":
        user_id = tg_id(update)
        if user_id:
            delete_employee_data(user_id)
        context.user_data.clear()
        await send_text(update, "Dados excluidos com sucesso. Envie /start para fazer um novo cadastro.", context, edit=True)
        return
    if data == "delete_my_data_cancel":
        await send_text(update, "Exclusao cancelada. Seus dados foram mantidos.", context, main_buttons(), edit=True)
        return
    if data == "nutrition_profile":
        await show_nutrition_goal(update, context, edit=True)
        return
    if data == "nutrition_progress":
        await show_progress(update, context, edit=True)
        return
    if data == "nutrition_ranking":
        await show_ranking(update, context, edit=True)
        return
    if data == "nutrition_consent_accept":
        await ask_nutrition_weight(update, context, edit=True)
        return
    if data == "nutrition_consent_decline":
        context.user_data.pop(NUTRITION_STEP, None)
        context.user_data.pop("nutrition_draft", None)
        await send_text(update, "Tudo bem. Nenhum dado nutricional foi salvo.", context, main_buttons(), edit=True)
        return
    if data == "nutrition_restart":
        await ask_nutrition_weight(update, context, edit=True)
        return
    if data.startswith("nutrition_sex:"):
        sex = data.removeprefix("nutrition_sex:")
        if sex not in nutrition.SEX_LABELS:
            await ask_nutrition_sex(update, context)
            return
        nutrition_draft(context)["sex"] = sex
        await ask_nutrition_activity(update, context, edit=True)
        return
    if data.startswith("nutrition_activity:"):
        activity = data.removeprefix("nutrition_activity:")
        if activity not in nutrition.ACTIVITY_FACTORS:
            await ask_nutrition_activity(update, context, edit=True)
            return
        nutrition_draft(context)["activity"] = activity
        await ask_nutrition_goal(update, context, edit=True)
        return
    if data.startswith("nutrition_goal:"):
        goal_key = data.removeprefix("nutrition_goal:")
        goal = GOALS.get(goal_key)
        if not goal:
            await ask_nutrition_goal(update, context, edit=True)
            return
        nutrition_draft(context)["goal"] = goal
        await complete_nutrition_profile(update, context, edit=True)
        return
    if data == "nutrition_partial":
        user_id = tg_id(update)
        recommendation = context.user_data.get("nutrition_recommendation")
        if not user_id or not load_nutrition_profile(user_id):
            await send_text(update, "Crie sua meta em /minha_meta antes de pontuar.", context, main_buttons(), edit=True)
            return
        if not recommendation:
            await send_text(update, "Abra uma recomendacao antes de registrar o acompanhamento.", context, main_buttons(), edit=True)
            return
        award = award_points(user_id, "partial_recommendation", 10, recommendation)
        note = gamification_note([award])
        message = "Acompanhamento parcial registrado." + (note or " Este evento ja foi pontuado hoje.")
        await send_text(update, message, context, [[("Meu progresso", "nutrition_progress")]], edit=True)
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
        await send_payload(
            update,
            menu_payload(
                day=current_day_name(),
                restriction=profile(context).get("restriction", ""),
                goal=profile(context).get("goal", ""),
            ),
            context,
            edit=True,
        )
        return
    if data == "menu_all":
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        await send_payload(
            update,
            menu_payload(
                restriction=profile(context).get("restriction", ""),
                goal=profile(context).get("goal", ""),
            ),
            context,
            edit=True,
        )
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
            awards = award_order_gamification(user_id, item, context)
        else:
            awards = []
        payload = order_payload(key)
        payload["text"] += gamification_note(awards)
        await send_payload(update, payload, context, edit=True)
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
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        await send_payload(update, recommendation_payload(context), context, edit=True)
        return
    await send_text(update, "Como posso ajudar? \U0001f60a", context, main_buttons(), edit=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if await handle_registration_message(update, context):
        return
    if not registered(context):
        await ask_registration_gate(update, context)
        return
    if await handle_nutrition_message(update, context):
        return
    text = normalize(update.message.text or "")
    item = find_menu_item_in_text(update.message.text or "")
    if item:
        user_id = tg_id(update)
        conflict = restriction_conflict(profile(context).get("restriction", ""), item)
        if conflict:
            await send_payload(
                update,
                safety_warning_payload(item, profile(context).get("restriction", ""), conflict),
                context,
            )
            return
        if user_id:
            record_order(user_id, item["dish_key"])
            context.user_data["last_order"] = item["dish_key"]
            awards = award_order_gamification(user_id, item, context)
        else:
            awards = []
        payload = order_payload(item["dish_key"])
        payload["text"] += gamification_note(awards)
        await send_payload(update, payload, context)
    elif "cardapio" in text or "tem hoje" in text or "o que tem" in text:
        await send_payload(
            update,
            menu_payload(
                day=current_day_name(),
                restriction=profile(context).get("restriction", ""),
                goal=profile(context).get("goal", ""),
            ),
            context,
        )
    elif "recomenda" in text or "sugestao" in text:
        await send_payload(update, recommendation_payload(context), context)
    elif "perfil" in text or "cadastro" in text:
        await send_payload(update, profile_payload(context), context)
    elif "historico" in text:
        await show_history(update, context)
    elif "minha meta" in text or "meta nutricional" in text:
        await show_nutrition_goal(update, context)
    elif "progresso" in text or "pontos" in text:
        await show_progress(update, context)
    elif "ranking" in text:
        await show_ranking(update, context)
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
    app.add_handler(CommandHandler("meus_dados", show_my_data))
    app.add_handler(CommandHandler("excluir_dados", confirm_delete_my_data))
    app.add_handler(CommandHandler("minha_meta", show_nutrition_goal))
    app.add_handler(CommandHandler("meu_progresso", show_progress))
    app.add_handler(CommandHandler("ranking", show_ranking))
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
