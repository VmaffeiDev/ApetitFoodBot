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
import cotton_menu

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
            CREATE TABLE IF NOT EXISTS daily_menu_components (
                menu_day TEXT NOT NULL,
                category TEXT NOT NULL,
                item_name TEXT NOT NULL,
                calories REAL,
                protein_grams REAL,
                menu_label TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (menu_day, category)
            );
            CREATE TABLE IF NOT EXISTS meal_selections (
                telegram_id INTEGER NOT NULL,
                menu_day TEXT NOT NULL,
                category TEXT NOT NULL,
                item_name TEXT NOT NULL,
                selected_at TEXT NOT NULL,
                PRIMARY KEY (telegram_id, menu_day)
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
        # Remove o cardapio comercial legado, que continha precos.
        # A operacao atual usa somente daily_menu_components, sem valores monetarios.
        conn.execute("DROP TABLE IF EXISTS menu_items")
        conn.execute("DROP TABLE IF EXISTS orders")
        seed_cotton_menu(conn)


def seed_cotton_menu(conn: sqlite3.Connection) -> None:
    timestamp = now_iso()
    for menu_day, category, item_name, calories, protein_grams in cotton_menu.iter_components():
        conn.execute(
            """
            INSERT INTO daily_menu_components (
                menu_day, category, item_name, calories, protein_grams, menu_label, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(menu_day, category) DO NOTHING
            """,
            (
                menu_day,
                category,
                item_name,
                calories,
                protein_grams,
                cotton_menu.MENU_LABEL,
                "Cardapio_Cotton_Setembro kcal-1.pdf",
                timestamp,
            ),
        )


def dish_key_from_name(name: str) -> str:
    for key, dish_name in DEFAULT_DISHES.items():
        if normalize(name) == normalize(dish_name):
            return key
    return normalize(name).replace(" ", "_")[:64]


def current_week_start() -> str:
    today = date.today()
    return today.fromordinal(today.toordinal() - today.weekday()).isoformat()


def menu_day_key(value: date | str | None = None) -> str:
    if value is None:
        return date.today().strftime("%m-%d")
    if isinstance(value, date):
        return value.strftime("%m-%d")
    raw = value.strip().lower().replace("set", "09")
    if len(raw) == 10:
        try:
            return date.fromisoformat(raw).strftime("%m-%d")
        except ValueError:
            pass
    if "/" in raw:
        day_value, month_value = raw.split("/", 1)
        return f"{int(month_value):02d}-{int(day_value):02d}"
    month_value, day_value = raw.split("-", 1)
    return f"{int(month_value):02d}-{int(day_value):02d}"


def menu_day_label(value: date | str | None = None) -> str:
    month_value, day_value = menu_day_key(value).split("-", 1)
    return f"{day_value}/{month_value}"


def list_daily_menu(menu_date: date | str | None = None) -> list[sqlite3.Row]:
    key = menu_day_key(menu_date)
    category_order = tuple(cotton_menu.CATEGORY_LABELS)
    order_sql = "CASE category " + " ".join(
        f"WHEN '{category}' THEN {index}" for index, category in enumerate(category_order)
    ) + " ELSE 999 END"
    with db() as conn:
        return conn.execute(
            f"SELECT * FROM daily_menu_components WHERE menu_day = ? ORDER BY {order_sql}",
            (key,),
        ).fetchall()


def load_daily_menu_component(menu_date: date | str, category: str) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM daily_menu_components WHERE menu_day = ? AND category = ?",
            (menu_day_key(menu_date), category),
        ).fetchone()


def upsert_daily_menu_component(
    menu_date: date | str,
    category: str,
    item_name: str,
    calories: float | None,
    protein_grams: float | None,
    menu_label: str = "Cardapio da unidade",
) -> None:
    if category not in cotton_menu.CATEGORY_LABELS:
        raise ValueError("Categoria de cardapio invalida.")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO daily_menu_components (
                menu_day, category, item_name, calories, protein_grams, menu_label, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(menu_day, category) DO UPDATE SET
                item_name = excluded.item_name,
                calories = excluded.calories,
                protein_grams = excluded.protein_grams,
                menu_label = excluded.menu_label,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                menu_day_key(menu_date), category, item_name, calories, protein_grams,
                menu_label, "Cadastro administrativo", now_iso(),
            ),
        )


def daily_main_choices(menu_date: date | str | None = None) -> list[sqlite3.Row]:
    return [row for row in list_daily_menu(menu_date) if row["category"] in {"main_1", "main_2", "main_option"}]


def recommend_daily_component(
    menu_date: date | str | None,
    restriction: str,
    goal: str,
) -> sqlite3.Row | None:
    choices = daily_main_choices(menu_date)
    if not choices:
        return None
    restriction_key = normalize(restriction)
    if "vegetariana" in restriction_key:
        choices = [row for row in choices if row["category"] == "main_option"]
    elif "frutos do mar" in restriction_key:
        choices = [row for row in choices if not any(term in normalize(row["item_name"]) for term in ("peixe", "tilapia"))]
    if not choices:
        return None

    goal_key = normalize(goal)
    if "ganhar massa" in goal_key:
        return max(choices, key=lambda row: (row["protein_grams"] or -1, -(row["calories"] or 10_000)))
    if "perder peso" in goal_key or "mais saudavel" in goal_key:
        known = [row for row in choices if row["calories"] is not None]
        return min(known or choices, key=lambda row: row["calories"] or 10_000)
    if "manter equilibrio" in goal_key:
        return max(
            choices,
            key=lambda row: (row["protein_grams"] or 0) / max(row["calories"] or 1, 1),
        )
    return choices[0]


def record_meal_selection(telegram_id: int, menu_date: date | str, category: str) -> sqlite3.Row:
    component = load_daily_menu_component(menu_date, category)
    if not component or category not in {"main_1", "main_2", "main_option"}:
        raise ValueError("Opcao de refeicao indisponivel para essa data.")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO meal_selections (telegram_id, menu_day, category, item_name, selected_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id, menu_day) DO UPDATE SET
                category = excluded.category,
                item_name = excluded.item_name,
                selected_at = excluded.selected_at
            """,
            (telegram_id, menu_day_key(menu_date), category, component["item_name"], now_iso()),
        )
    return component


def recent_meals(telegram_id: int, limit: int = 5) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT menu_day, category, item_name, selected_at
            FROM meal_selections
            WHERE telegram_id = ?
            ORDER BY selected_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()


def top_meals(telegram_id: int, limit: int = 3) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            """
            SELECT item_name, COUNT(*) AS total
            FROM meal_selections
            WHERE telegram_id = ?
            GROUP BY item_name
            ORDER BY total DESC, MAX(selected_at) DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ).fetchall()


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


def add_favorite_waitlist(telegram_id: int, dish_key: str) -> None:
    dish_name = DEFAULT_DISHES.get(dish_key, dish_key.replace("_", " ").title())
    with db() as conn:
        conn.execute(
            """
            INSERT INTO favorite_waitlist (telegram_id, dish_key, dish_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id, dish_key) DO NOTHING
            """,
            (telegram_id, dish_key, dish_name, now_iso()),
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
        "recent_meals": recent_meals(telegram_id, 10),
        "favorites": favorite_items(telegram_id, 10),
    }


def delete_employee_data(telegram_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM user_badges WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM gamification_events WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM gamification WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM nutrition_profiles WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM favorite_waitlist WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM meal_selections WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM clients WHERE telegram_id = ?", (telegram_id,))


def admin_report_data() -> dict:
    with db() as conn:
        totals = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM clients) AS employees,
                (SELECT COUNT(*) FROM clients WHERE consent_accepted = 1) AS consented_employees,
                (SELECT COUNT(*) FROM meal_selections) AS meals,
                (SELECT COUNT(*) FROM favorite_waitlist) AS favorites,
                (SELECT COUNT(DISTINCT menu_day) FROM daily_menu_components) AS menu_days
            """
        ).fetchone()
        recent = conn.execute(
            """
            SELECT c.name, m.item_name, m.menu_day, m.selected_at
            FROM meal_selections m
            LEFT JOIN clients c ON c.telegram_id = m.telegram_id
            ORDER BY m.selected_at DESC
            LIMIT 5
            """
        ).fetchall()
        top = conn.execute(
            """
            SELECT item_name, COUNT(*) AS total
            FROM meal_selections
            GROUP BY item_name
            ORDER BY total DESC, MAX(selected_at) DESC
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
            JOIN favorite_waitlist interest ON interest.dish_key = wm.dish_key
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


def format_nutrition_value(value: float | None) -> str:
    if value is None:
        return "nao informado"
    return f"{value:g}".replace(".", ",")


def menu_payload(
    day: str | None = None,
    restriction: str = "",
    goal: str = "",
    menu_date: date | str | None = None,
) -> dict:
    del day, goal  # parametros legados mantidos para compatibilidade com integracoes antigas
    menu_key = menu_day_key(menu_date)
    items = list_daily_menu(menu_key)
    if not items:
        return {
            "text": (
                f"\U0001f614 Ainda nao ha cardapio publicado para <b>{menu_day_label(menu_key)}</b>.\n\n"
                "As refeicoes sao fornecidas pela empresa; o bot nao realiza vendas nem cobrancas."
            ),
            "buttons": [[("\U0001f4c5 Ver cardapio de 01/09", "menu_date:09-01")]],
        }

    by_category = {row["category"]: row for row in items}
    lines = [
        f"\U0001f957 <b>Cardapio servido em {menu_day_label(menu_key)}</b>",
        f"<i>{escape(items[0]['menu_label'])}</i>",
        "",
        "<b>Pratos principais (escolha uma opcao)</b>",
    ]
    for category in ("main_1", "main_2", "main_option"):
        row = by_category.get(category)
        if row:
            protein = (
                f" | {format_nutrition_value(row['protein_grams'])} g proteina"
                if row["protein_grams"] is not None else ""
            )
            lines.append(
                f"- {escape(row['item_name'])} ({format_nutrition_value(row['calories'])} kcal{protein})"
            )

    sections = (
        ("Guarnicoes", ("side_1", "side_2")),
        ("Saladas", ("salad_1", "salad_2", "salad_3")),
        ("Arroz e feijao", ("rice_1", "rice_2", "beans")),
        ("Sobremesa e fruta", ("dessert", "fruit")),
        ("Bebida", ("drink",)),
    )
    for title, categories in sections:
        values = [by_category[category] for category in categories if category in by_category]
        if not values:
            continue
        lines.extend(["", f"<b>{title}</b>"])
        lines.extend(
            f"- {escape(row['item_name'])} ({format_nutrition_value(row['calories'])} kcal)"
            for row in values
        )

    if restriction and normalize(restriction) != "sem restricoes":
        lines.extend([
            "",
            f"\u26a0\ufe0f Restricao cadastrada: <b>{escape(restriction)}</b>. ",
            "O PDF nao informa ingredientes nem alergenicos; confirme a composicao com a equipe do restaurante.",
        ])
    lines.extend([
        "",
        "Valores nutricionais transcritos do cardapio fornecido pela operacao.",
        "A refeicao e um beneficio da empresa: <b>nao ha pagamento</b>.",
    ])
    buttons = [
        [(f"\u2705 Escolher {row['item_name'][:28]}", f"meal:{menu_key}:{row['category']}")]
        for row in daily_main_choices(menu_key)
    ]
    buttons.append([("\u2b50 Ver recomendacao", "recommend")])
    return {"text": "\n".join(lines), "buttons": buttons}


def meal_selection_payload(menu_date: date | str, category: str) -> dict:
    item = load_daily_menu_component(menu_date, category)
    if not item:
        return {
            "text": "\U0001f614 Nao encontrei essa opcao no cardapio publicado.",
            "buttons": [[("\U0001f957 Ver cardapio", "menu_today")]],
        }
    return {
        "text": (
            f"\u2705 Escolha de refeicao registrada para <b>{{name}}</b>.\n\n"
            f"<b>{escape(item['item_name'])}</b>\n"
            f"Data do cardapio: {menu_day_label(menu_date)}\n\n"
            "Nao ha cobranca ou pagamento. Bom apetite \U0001f60a"
        ),
        "buttons": [[("\U0001f957 Voltar ao cardapio", f"menu_date:{menu_day_key(menu_date)}")]],
    }



def recommendation_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    data = profile(context)
    user_id = data.get("telegram_id")
    menu_key = context.user_data.get("active_menu_day") or menu_day_key()
    item = recommend_daily_component(menu_key, data.get("restriction", ""), data.get("goal", ""))
    if not item:
        return {
            "text": (
                f"\U0001f614 Ainda nao ha uma recomendacao para o cardapio de {menu_day_label(menu_key)}.\n\n"
                "Consulte outro dia publicado ou fale com a equipe do restaurante."
            ),
            "buttons": [[("\U0001f4c5 Ver cardapio de 01/09", "menu_date:09-01"), ("\U0001f464 Meu perfil", "profile")]],
        }
    goal_key = normalize(data.get("goal", ""))
    if "ganhar massa" in goal_key:
        reason = "e a opcao principal com maior quantidade de proteina informada"
    elif "perder peso" in goal_key or "mais saudavel" in goal_key:
        reason = "e a opcao principal com menor valor calorico informado"
    elif "manter equilibrio" in goal_key:
        reason = "oferece uma boa relacao entre proteina e calorias"
    elif "vegetariana" in normalize(data.get("restriction", "")):
        reason = "e a alternativa sem carne identificavel no cardapio"
    else:
        reason = "e uma das opcoes servidas nessa data"
    recommendation_key = f"{menu_key}:{item['category']}"
    context.user_data["nutrition_recommendation"] = recommendation_key
    nutrition_profile = load_nutrition_profile(user_id) if user_id else None
    if nutrition_profile:
        nutrition_text = (
            f"\n\n\U0001f3af Meta estimada do dia: {nutrition_profile['target_calories']} kcal e "
            f"{nutrition_profile['protein_grams']} g de proteina.\n"
            f"Porcao sugerida: {escape(nutrition.PORTION_GUIDANCE[nutrition_profile['goal']])}"
        )
    else:
        nutrition_text = "\n\nUse /minha_meta para calcular uma estimativa de calorias, proteina e porcao."
    kcal_text = format_nutrition_value(item["calories"])
    protein_text = format_nutrition_value(item["protein_grams"])
    buttons = [[(f"\u2705 Escolher {item['item_name'][:30]}", f"meal:{menu_key}:{item['category']}")]]
    if nutrition_profile:
        buttons.append([("Segui em parte (+10)", "nutrition_partial")])
    buttons.append([("\U0001f957 Ver cardapio", f"menu_date:{menu_key}")])
    return {
        "text": (
            f"\u2b50 <b>Recomendacao para {menu_day_label(menu_key)}:</b>\n\n"
            f"<b>{escape(item['item_name'])}</b>\n"
            f"{kcal_text} kcal | {protein_text} g de proteina\n"
            f"Escolhi porque {reason} \U0001f33f"
            f"{nutrition_text}\n\n"
            "O documento nao informa ingredientes ou alergenicos. Confirme restricoes com a equipe do restaurante. "
            "Estimativas educativas; necessidades individuais devem ser avaliadas por nutricionista."
        ),
        "buttons": buttons,
    }

def profile_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    data = profile(context)
    user_id = data.get("telegram_id")
    top_text = "Ainda sem escolhas de refeicao."
    recent_text = "Ainda sem refeicoes registradas."
    if user_id:
        top = top_meals(user_id)
        recent = recent_meals(user_id)
        if top:
            top_text = "\n".join(f"- {escape(row['item_name'])}: {row['total']} escolha(s)" for row in top)
        if recent:
            recent_text = "\n".join(
                f"- {menu_day_label(row['menu_day'])}: {escape(row['item_name'])}" for row in recent
            )
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
            f"<b>Escolhas mais frequentes:</b>\n{top_text}\n\n"
            f"<b>Refeicoes recentes:</b>\n{recent_text}"
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


def award_meal_gamification(
    telegram_id: int,
    menu_date: date | str,
    category: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> list[dict]:
    if not load_nutrition_profile(telegram_id):
        return []
    event_key = f"{menu_day_key(menu_date)}:{category}"
    if context.user_data.get("nutrition_recommendation") == event_key:
        return [award_points(telegram_id, "follow_recommendation", 20, event_key)]
    return []


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
            "Esses dados personalizam recomendacoes, registram escolhas de refeicao e permitem avisos de cardapio. "
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
            "Agora posso te ajudar com o cardapio e suas escolhas de refeicao \U0001f60a"
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
                "Sou o bot interno da Apetit. Posso mostrar a refeicao servida no dia e ajudar na sua escolha \U0001f33f"
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
    rows = recent_meals(user_id, 10)
    if not rows:
        await send_text(update, "\U0001f4cb Voce ainda nao tem refeicoes registradas.", context, main_buttons())
        return
    await send_text(
        update,
        "\U0001f4cb <b>Seu historico de refeicoes:</b>\n\n"
        + "\n".join(f"- {menu_day_label(row['menu_day'])}: {escape(row['item_name'])}" for row in rows),
        context,
        main_buttons(),
    )


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    hydrate_profile(update, context)
    if not registered(context):
        await ask_registration_gate(update, context)
        return
    requested_date = context.args[0] if context.args else None
    try:
        menu_key = menu_day_key(requested_date)
    except (ValueError, TypeError):
        await send_text(update, "Use /cardapio ou informe uma data como /cardapio 01/09.", context, main_buttons())
        return
    context.user_data["active_menu_day"] = menu_key
    await send_payload(
        update,
        menu_payload(
            restriction=profile(context).get("restriction", ""),
            goal=profile(context).get("goal", ""),
            menu_date=menu_key,
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
    meals = snapshot["recent_meals"]
    favorites = snapshot["favorites"]
    nutrition_profile = snapshot["nutrition"]
    game = snapshot["gamification"]
    meal_text = (
        "\n".join(f"- {menu_day_label(row['menu_day'])}: {escape(row['item_name'])}" for row in meals)
        or "Ainda sem refeicoes registradas."
    )
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
            f"<b>Historico recente de refeicoes:</b>\n{meal_text}\n\n"
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
    if len(parts) < 5:
        await update.effective_message.reply_text(
            "Envie assim:\n\n"
            "/cardapio_add data | categoria | nome | kcal | proteina_g\n\n"
            "Exemplo:\n"
            "/cardapio_add 01/09 | main_1 | Strogonoff de carne | 134 | 12\n\n"
            "Categorias: " + ", ".join(cotton_menu.CATEGORY_LABELS)
        )
        return
    menu_date, category, name, raw_calories, raw_protein = parts[:5]
    try:
        calories = parse_decimal(raw_calories) if raw_calories else None
        protein = parse_decimal(raw_protein) if raw_protein else None
        upsert_daily_menu_component(menu_date, category, name, calories, protein)
    except ValueError as error:
        await update.effective_message.reply_text(f"Nao consegui atualizar o cardapio: {error}")
        return
    await update.effective_message.reply_text(
        f"\u2705 Cardapio de {menu_day_label(menu_date)} atualizado: {name}. Nao ha preco ou cobranca."
    )


async def list_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await update.effective_message.reply_text("\U0001f512 Apenas administradores podem listar o cardapio administrativo.")
        return
    raw_date = " ".join(context.args).strip() if context.args else None
    try:
        menu_key = menu_day_key(raw_date)
    except (ValueError, TypeError):
        await update.effective_message.reply_text("Use /cardapio_list 01/09.")
        return
    items = list_daily_menu(menu_key)
    if not items:
        await update.effective_message.reply_text(f"\U0001f614 Nenhum cardapio publicado para {menu_day_label(menu_key)}.")
        return
    lines = [f"\U0001f957 Cardapio publicado para {menu_day_label(menu_key)}:"]
    for item in items:
        label = cotton_menu.CATEGORY_LABELS.get(item["category"], item["category"])
        lines.append(f"- {label}: {item['item_name']} | {format_nutrition_value(item['calories'])} kcal")
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
        f"Escolhas de refeicao registradas: {totals['meals']}",
        f"Pratos aguardados/favoritos: {totals['favorites']}",
        f"Dias de cardapio publicados: {totals['menu_days']}",
        "",
        "Opcoes mais escolhidas:",
    ]
    if report["top"]:
        lines.extend(f"- {row['item_name']}: {row['total']} escolha(s)" for row in report["top"])
    else:
        lines.append("- Ainda sem refeicoes registradas.")
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
    lines.extend(["", "Refeicoes recentes:"])
    if report["recent"]:
        lines.extend(
            f"- {row['name'] or 'Colaborador'} escolheu {row['item_name']} ({menu_day_label(row['menu_day'])})"
            for row in report["recent"]
        )
    else:
        lines.append("- Ainda sem refeicoes recentes.")
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
            "Sem o aceite, nao consigo guardar seus dados nem registrar refeicoes. Envie /start quando quiser continuar.",
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
        menu_key = menu_day_key()
        context.user_data["active_menu_day"] = menu_key
        await send_payload(
            update,
            menu_payload(
                restriction=profile(context).get("restriction", ""),
                goal=profile(context).get("goal", ""),
                menu_date=menu_key,
            ),
            context,
            edit=True,
        )
        return
    if data.startswith("menu_date:"):
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        menu_key = menu_day_key(data.removeprefix("menu_date:"))
        context.user_data["active_menu_day"] = menu_key
        await send_payload(
            update,
            menu_payload(
                restriction=profile(context).get("restriction", ""),
                goal=profile(context).get("goal", ""),
                menu_date=menu_key,
            ),
            context,
            edit=True,
        )
        return
    if data.startswith("meal:"):
        if not registered(context):
            await ask_registration_gate(update, context, edit=True)
            return
        try:
            _, menu_key, category = data.split(":", 2)
        except ValueError:
            await send_text(update, "Opcao de refeicao invalida.", context, main_buttons(), edit=True)
            return
        user_id = tg_id(update)
        if user_id:
            try:
                record_meal_selection(user_id, menu_key, category)
            except ValueError as error:
                await send_text(update, escape(str(error)), context, main_buttons(), edit=True)
                return
            awards = award_meal_gamification(user_id, menu_key, category, context)
        else:
            awards = []
        payload = meal_selection_payload(menu_key, category)
        payload["text"] += gamification_note(awards)
        await send_payload(update, payload, context, edit=True)
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
    if "cardapio" in text or "tem hoje" in text or "o que tem" in text:
        menu_key = menu_day_key()
        context.user_data["active_menu_day"] = menu_key
        await send_payload(
            update,
            menu_payload(
                restriction=profile(context).get("restriction", ""),
                goal=profile(context).get("goal", ""),
                menu_date=menu_key,
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
