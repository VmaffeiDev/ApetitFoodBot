from pathlib import Path


BOT = Path("bot.py")


def replace_between(text: str, start: str, end: str, new_block: str) -> str:
    start_index = text.index(start)
    end_index = text.index(end, start_index)
    return text[:start_index] + new_block.rstrip() + "\n\n" + text[end_index:]


def insert_before(text: str, marker: str, block: str) -> str:
    index = text.index(marker)
    return text[:index] + block.rstrip() + "\n\n" + text[index:]


def main() -> None:
    text = BOT.read_text(encoding="utf-8")

    if "consent_accepted INTEGER" not in text:
        text = text.replace(
            "                goal TEXT NOT NULL DEFAULT 'Nao informado',\n                restriction TEXT NOT NULL,\n                created_at TEXT NOT NULL,\n",
            "                goal TEXT NOT NULL DEFAULT 'Nao informado',\n                restriction TEXT NOT NULL,\n                consent_accepted INTEGER NOT NULL DEFAULT 0,\n                consented_at TEXT NOT NULL DEFAULT '',\n                created_at TEXT NOT NULL,\n",
            1,
        )
        text = text.replace(
            "        if \"goal\" not in columns:\n            conn.execute(\"ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT 'Nao informado'\")\n",
            "        if \"goal\" not in columns:\n            conn.execute(\"ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT 'Nao informado'\")\n        if \"consent_accepted\" not in columns:\n            conn.execute(\"ALTER TABLE clients ADD COLUMN consent_accepted INTEGER NOT NULL DEFAULT 0\")\n        if \"consented_at\" not in columns:\n            conn.execute(\"ALTER TABLE clients ADD COLUMN consented_at TEXT NOT NULL DEFAULT ''\")\n",
            1,
        )

    save_client_block = '''def save_client(
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
'''
    text = replace_between(text, "def save_client", "def load_client", save_client_block)

    if "def favorite_items" not in text:
        data_block = '''def favorite_items(telegram_id: int, limit: int = 10) -> list[sqlite3.Row]:
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
'''
        text = insert_before(text, "def recent_orders", data_block)

    if "consented_clients" not in text:
        text = text.replace(
            "                (SELECT COUNT(*) FROM clients) AS clients,\n                (SELECT COUNT(*) FROM orders) AS orders,\n",
            "                (SELECT COUNT(*) FROM clients) AS clients,\n                (SELECT COUNT(*) FROM clients WHERE consent_accepted = 1) AS consented_clients,\n                (SELECT COUNT(*) FROM orders) AS orders,\n",
            1,
        )
        text = text.replace(
            "        f\"Clientes cadastrados: {totals['clients']}\",\n        f\"Pedidos registrados: {totals['orders']}\",\n",
            "        f\"Clientes cadastrados: {totals['clients']}\",\n        f\"Clientes com consentimento: {totals['consented_clients']}\",\n        f\"Pedidos registrados: {totals['orders']}\",\n",
            1,
        )

    hydrate_block = '''def hydrate_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
'''
    text = replace_between(text, "def hydrate_profile", "def registered", hydrate_block)

    helpers_block = '''def registered(context: ContextTypes.DEFAULT_TYPE) -> bool:
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
'''
    text = replace_between(text, "def registered", "def user_name", helpers_block)

    if "def consent_buttons" not in text:
        consent_buttons = '''def consent_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("✅ Aceito e quero continuar", "lgpd_accept")],
        [("❌ Nao aceito", "lgpd_decline")],
    ]
'''
        text = insert_before(text, "def menu_payload", consent_buttons)

    profile_payload_block = '''def profile_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
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
'''
    text = replace_between(text, "def profile_payload", "async def ask_name", profile_payload_block)

    consent_flow_block = '''async def ask_consent(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
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
'''
    text = replace_between(text, "async def finish_registration", "async def start", consent_flow_block)

    start_block = '''async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
'''
    text = replace_between(text, "async def start", "def is_admin", start_block)

    registration_callback_block = '''async def handle_registration_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
'''
    text = replace_between(text, "async def handle_registration_message", "async def handle_message", registration_callback_block)

    message_block = '''async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
'''
    text = replace_between(text, "async def handle_message", "def main", message_block)

    main_block = '''def main() -> None:
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
'''
    text = replace_between(text, "def main", "if __name__ == \"__main__\"", main_block)

    required = [
        "consent_accepted INTEGER",
        "def ask_consent",
        "def delete_client_data",
        "async def show_my_data",
        'CommandHandler("meus_dados"',
        "TELEGRAM_WEBHOOK_URL",
    ]
    missing = [item for item in required if item not in text]
    if missing:
        raise SystemExit(f"Atualizacao incompleta. Marcadores ausentes: {missing}")

    BOT.write_text(text, encoding="utf-8")
    print("Atualizacao LGPD/deploy aplicada. Rode: python -m unittest discover -s tests")


if __name__ == "__main__":
    main()
