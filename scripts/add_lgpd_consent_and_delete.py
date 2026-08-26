from pathlib import Path

path = Path("bot.py")
text = path.read_text(encoding="utf-8")

# 1. Add consent_accepted / consented_at columns to the clients table schema.
SCHEMA_ANCHOR = (
    "                restriction TEXT NOT NULL,\n"
    "                created_at TEXT NOT NULL,\n"
    "                updated_at TEXT NOT NULL\n"
    "            );\n"
)
SCHEMA_ADDITION = (
    "                restriction TEXT NOT NULL,\n"
    "                consent_accepted INTEGER NOT NULL DEFAULT 0,\n"
    "                consented_at TEXT NOT NULL DEFAULT '',\n"
    "                created_at TEXT NOT NULL,\n"
    "                updated_at TEXT NOT NULL\n"
    "            );\n"
)

if "consent_accepted INTEGER NOT NULL DEFAULT 0" not in text:
    if SCHEMA_ANCHOR not in text:
        print("SCHEMA_ANCHOR_NOT_FOUND")
        raise SystemExit(1)
    text = text.replace(SCHEMA_ANCHOR, SCHEMA_ADDITION, 1)

# 2. Add the ALTER TABLE migration fallback for the two new columns.
MIGRATION_ANCHOR = (
    "        if \"goal\" not in columns:\n"
    "            conn.execute(\"ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT 'Nao informado'\")\n"
    "        seed_default_menu(conn)\n"
)
MIGRATION_ADDITION = (
    "        if \"goal\" not in columns:\n"
    "            conn.execute(\"ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT 'Nao informado'\")\n"
    "        if \"consent_accepted\" not in columns:\n"
    "            conn.execute(\"ALTER TABLE clients ADD COLUMN consent_accepted INTEGER NOT NULL DEFAULT 0\")\n"
    "        if \"consented_at\" not in columns:\n"
    "            conn.execute(\"ALTER TABLE clients ADD COLUMN consented_at TEXT NOT NULL DEFAULT ''\")\n"
    "        seed_default_menu(conn)\n"
)

if 'ALTER TABLE clients ADD COLUMN consent_accepted' not in text:
    if MIGRATION_ANCHOR not in text:
        print("MIGRATION_ANCHOR_NOT_FOUND")
        raise SystemExit(1)
    text = text.replace(MIGRATION_ANCHOR, MIGRATION_ADDITION, 1)

# 3. Extend save_client's signature with optional consent params.
SIGNATURE_ANCHOR = (
    'def save_client(telegram_id: int, chat_id: int, name: str, phone: str, '
    'address: str, restriction: str, goal: str = "Nao informado") -> None:'
)
SIGNATURE_ADDITION = (
    'def save_client(telegram_id: int, chat_id: int, name: str, phone: str, '
    'address: str, restriction: str, goal: str = "Nao informado", '
    'consent: bool = False, consented_at: str | None = None) -> None:'
)

if SIGNATURE_ANCHOR not in text and SIGNATURE_ADDITION not in text:
    print("SIGNATURE_ANCHOR_NOT_FOUND")
    raise SystemExit(1)
if SIGNATURE_ANCHOR in text:
    text = text.replace(SIGNATURE_ANCHOR, SIGNATURE_ADDITION, 1)

# 4. Update the real/active INSERT ... ON CONFLICT block used by save_client.
INSERT_ANCHOR = (
    '            INSERT INTO clients (telegram_id, chat_id, name, phone, address, goal, restriction, created_at, updated_at)\n'
    '            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)\n'
    '            ON CONFLICT(telegram_id) DO UPDATE SET\n'
    '                chat_id = excluded.chat_id,\n'
    '                name = excluded.name,\n'
    '                phone = excluded.phone,\n'
    '                address = excluded.address,\n'
    '                goal = excluded.goal,\n'
    '                restriction = excluded.restriction,\n'
    '                updated_at = excluded.updated_at\n'
    '            """,\n'
    '            (telegram_id, chat_id, name, phone, address, goal, restriction, timestamp, timestamp),\n'
    '        )\n'
)
INSERT_ADDITION = (
    '            INSERT INTO clients (telegram_id, chat_id, name, phone, address, goal, restriction, consent_accepted, consented_at, created_at, updated_at)\n'
    '            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n'
    '            ON CONFLICT(telegram_id) DO UPDATE SET\n'
    '                chat_id = excluded.chat_id,\n'
    '                name = excluded.name,\n'
    '                phone = excluded.phone,\n'
    '                address = excluded.address,\n'
    '                goal = excluded.goal,\n'
    '                restriction = excluded.restriction,\n'
    '                consent_accepted = excluded.consent_accepted,\n'
    '                consented_at = excluded.consented_at,\n'
    '                updated_at = excluded.updated_at\n'
    '            """,\n'
    '            (telegram_id, chat_id, name, phone, address, goal, restriction, int(consent), consented_at or "", timestamp, timestamp),\n'
    '        )\n'
)

if 'consent_accepted, consented_at, created_at, updated_at)' not in text:
    if INSERT_ANCHOR not in text:
        print("INSERT_ANCHOR_NOT_FOUND")
        raise SystemExit(1)
    text = text.replace(INSERT_ANCHOR, INSERT_ADDITION, 1)

# 5. Add delete_client_data() and favorite_items() right after load_client().
LOAD_CLIENT_ANCHOR = (
    'def load_client(telegram_id: int) -> sqlite3.Row | None:\n'
    '    with db() as conn:\n'
    '        return conn.execute("SELECT * FROM clients WHERE telegram_id = ?", (telegram_id,)).fetchone()\n'
)
NEW_FUNCS = LOAD_CLIENT_ANCHOR + '''

def delete_client_data(telegram_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM orders WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM favorite_waitlist WHERE telegram_id = ?", (telegram_id,))
        conn.execute("DELETE FROM clients WHERE telegram_id = ?", (telegram_id,))


def favorite_items(telegram_id: int) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT dish_key, dish_name FROM favorite_waitlist WHERE telegram_id = ? ORDER BY created_at",
            (telegram_id,),
        ).fetchall()
'''

if "def delete_client_data" not in text:
    if LOAD_CLIENT_ANCHOR not in text:
        print("LOAD_CLIENT_ANCHOR_NOT_FOUND")
        raise SystemExit(1)
    text = text.replace(LOAD_CLIENT_ANCHOR, NEW_FUNCS, 1)

path.write_text(text, encoding="utf-8")
print("bot.py atualizado com consentimento LGPD (consent_accepted/consented_at) e exclusao de dados (delete_client_data/favorite_items).")
