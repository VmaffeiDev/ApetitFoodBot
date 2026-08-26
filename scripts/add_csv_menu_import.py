from pathlib import Path

path = Path("bot.py")
text = path.read_text(encoding="utf-8")

if "import csv" not in text:
    text = text.replace(
        "import logging\nimport os\nimport sqlite3\nimport unicodedata\n",
        "import csv\nimport io\nimport logging\nimport os\nimport sqlite3\nimport unicodedata\n",
        1,
    )

FUNCS_ANCHOR = (
    '            (key, name, price, normalize(day) or "todos", ingredients, allergens, tags, int(available), timestamp, timestamp),\n'
    "        )\n"
    "    return key\n"
)

FUNCS_ADDITION = FUNCS_ANCHOR + '''
MENU_CSV_FIELD_ALIASES = {
    "name": {"nome", "prato", "name", "dish", "dish_name"},
    "price": {"preco", "price", "valor"},
    "day": {"dia", "day"},
    "ingredients": {"ingredientes", "ingredients"},
    "allergens": {"alergenicos", "allergens", "alergias"},
    "tags": {"tags", "categoria", "categorias"},
    "available": {"disponivel", "available", "status"},
}


def parse_menu_csv(text: str) -> list[dict]:
    first_line = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if ";" in first_line and first_line.count(";") >= first_line.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for raw_row in reader:
        if not raw_row:
            continue
        normalized_row = {}
        for raw_key, value in raw_row.items():
            if not raw_key:
                continue
            norm_key = normalize(raw_key)
            for target, aliases in MENU_CSV_FIELD_ALIASES.items():
                if norm_key in aliases:
                    normalized_row[target] = (value or "").strip()
                    break
        name = normalized_row.get("name", "").strip()
        if not name:
            continue
        available_raw = normalized_row.get("available", "sim")
        available = normalize(available_raw) not in {"nao", "no", "false", "0", "esgotado", ""}
        rows.append(
            {
                "name": name,
                "price": normalized_row.get("price", "0"),
                "day": normalized_row.get("day", "todos"),
                "ingredients": normalized_row.get("ingredients", ""),
                "allergens": normalized_row.get("allergens", ""),
                "tags": normalized_row.get("tags", ""),
                "available": available,
            }
        )
    return rows


def replace_menu_from_rows(rows: list[dict]) -> dict:
    keys_to_keep = set()
    saved = 0
    for row in rows:
        key = upsert_menu_item(
            row["name"],
            price_to_cents(str(row.get("price", "0"))),
            row.get("day", "todos"),
            row.get("ingredients", ""),
            row.get("allergens", ""),
            row.get("tags", ""),
            bool(row.get("available", True)),
        )
        keys_to_keep.add(key)
        saved += 1
    removed = 0
    with db() as conn:
        existing = {r["dish_key"] for r in conn.execute("SELECT dish_key FROM menu_items").fetchall()}
        to_remove = existing - keys_to_keep
        if to_remove:
            conn.executemany("DELETE FROM menu_items WHERE dish_key = ?", [(k,) for k in to_remove])
            removed = len(to_remove)
    return {"saved": saved, "removed": removed}
'''

if "def replace_menu_from_rows" not in text:
    text = text.replace(FUNCS_ANCHOR, FUNCS_ADDITION, 1)

HANDLER_ANCHOR = "async def handle_registration_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:"

HANDLER_CODE = '''async def handle_menu_csv_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.effective_message.document if update.effective_message else None
    if not document or not (document.file_name or "").lower().endswith(".csv"):
        return
    if not is_admin(tg_id(update)):
        await update.effective_message.reply_text("Apenas administradores podem atualizar o cardapio.")
        return
    try:
        telegram_file = await context.bot.get_file(document.file_id)
        raw_bytes = await telegram_file.download_as_bytearray()
        try:
            csv_text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            csv_text = raw_bytes.decode("latin-1")
        rows = parse_menu_csv(csv_text)
        if not rows:
            await update.effective_message.reply_text(
                "Nao encontrei pratos validos no CSV. Confira se a primeira linha tem os titulos "
                "(nome, preco, dia, ingredientes, alergenicos, tags, disponivel) e tente novamente."
            )
            return
        result = replace_menu_from_rows(rows)
    except Exception:
        logger.exception("Falha ao importar cardapio via CSV")
        await update.effective_message.reply_text("Nao consegui ler esse CSV. Verifique o formato e envie novamente.")
        return
    await update.effective_message.reply_text(
        f"Cardapio atualizado a partir do CSV!\\n"
        f"Pratos cadastrados/atualizados: {result['saved']}\\n"
        f"Pratos removidos (fora da nova planilha): {result['removed']}"
    )


''' + HANDLER_ANCHOR

if "async def handle_menu_csv_upload" not in text:
    text = text.replace(HANDLER_ANCHOR, HANDLER_CODE, 1)

REG_ANCHOR = "    app.add_handler(CallbackQueryHandler(handle_callback))\n"
REG_ADDITION = "    app.add_handler(MessageHandler(filters.Document.ALL, handle_menu_csv_upload))\n" + REG_ANCHOR

if "MessageHandler(filters.Document.ALL, handle_menu_csv_upload)" not in text:
    text = text.replace(REG_ANCHOR, REG_ADDITION, 1)

path.write_text(text, encoding="utf-8")
print("bot.py atualizado com importacao de cardapio via CSV.")
