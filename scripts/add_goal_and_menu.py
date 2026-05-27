from pathlib import Path

path = Path("bot.py")
text = path.read_text(encoding="utf-8")

if "GOALS = {" not in text:
    text = text.replace(
        "\n\nDEFAULT_DISHES = {",
        """

GOALS = {
    "goal_weight_loss": "Perder peso",
    "goal_muscle_gain": "Ganhar massa",
    "goal_maintenance": "Manter equilibrio",
    "goal_health": "Alimentacao mais saudavel",
    "goal_practical": "Praticidade na rotina",
}

DEFAULT_DISHES = {""",
        1,
    )

text = text.replace(
    "                address TEXT NOT NULL DEFAULT '',\n                restriction TEXT NOT NULL,",
    "                address TEXT NOT NULL DEFAULT '',\n                goal TEXT NOT NULL DEFAULT 'Nao informado',\n                restriction TEXT NOT NULL,",
)

if '"goal" not in columns' not in text:
    text = text.replace(
        '        if "address" not in columns:\n            conn.execute("ALTER TABLE clients ADD COLUMN address TEXT NOT NULL DEFAULT \'\'")\n        seed_default_menu(conn)',
        '        if "address" not in columns:\n            conn.execute("ALTER TABLE clients ADD COLUMN address TEXT NOT NULL DEFAULT \'\'")\n        if "goal" not in columns:\n            conn.execute("ALTER TABLE clients ADD COLUMN goal TEXT NOT NULL DEFAULT \'Nao informado\'")\n        seed_default_menu(conn)',
        1,
    )

seed_start = text.index("    items = [", text.index("def seed_default_menu"))
seed_end = text.index("    ]\n    for item in items:", seed_start) + len("    ]")
expanded_items = '''    items = [
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
    ]'''
text = text[:seed_start] + expanded_items + text[seed_end:]

recommend_block = '''
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
'''

rec_start = text.index("def goal_score(") if "def goal_score(" in text else text.index("def recommend_item(")
rec_end = text.index("\n\ndef current_week_start", rec_start)
text = text[:rec_start] + recommend_block + text[rec_end + 2 :]

save_client_block = '''
def save_client(telegram_id: int, chat_id: int, name: str, phone: str, address: str, restriction: str, goal: str = "Nao informado") -> None:
    timestamp = now_iso()
    with db() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(clients)").fetchall()}
        if "company" in columns:
            conn.execute(
                """
                INSERT INTO clients (telegram_id, chat_id, name, company, phone, address, goal, restriction, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    name = excluded.name,
                    company = excluded.company,
                    phone = excluded.phone,
                    address = excluded.address,
                    goal = excluded.goal,
                    restriction = excluded.restriction,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, chat_id, name, address, phone, address, goal, restriction, timestamp, timestamp),
            )
            return
        conn.execute(
            """
            INSERT INTO clients (telegram_id, chat_id, name, phone, address, goal, restriction, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                chat_id = excluded.chat_id,
                name = excluded.name,
                phone = excluded.phone,
                address = excluded.address,
                goal = excluded.goal,
                restriction = excluded.restriction,
                updated_at = excluded.updated_at
            """,
            (telegram_id, chat_id, name, phone, address, goal, restriction, timestamp, timestamp),
        )
'''
save_start = text.index("def save_client(")
save_end = text.index("\n\ndef load_client", save_start)
text = text[:save_start] + save_client_block + text[save_end + 2 :]

if '"goal": client["goal"],' not in text:
    text = text.replace('"address": client["address"],\n                "restriction": client["restriction"],', '"address": client["address"],\n                "goal": client["goal"],\n                "restriction": client["restriction"],')

buttons_start = text.index("def restriction_buttons(")
buttons_end = text.index("\n\ndef menu_payload", buttons_start)
buttons_block = '''def restriction_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("\\u2705 Nenhuma restricao", "restriction_none")],
        [("\\U0001f33e Sem gluten", "restriction_gluten"), ("\\U0001f95b Sem lactose", "restriction_lactose")],
        [("\\U0001f969 Vegetariana", "restriction_vegetarian"), ("\\U0001f420 Sem frutos do mar", "restriction_seafood")],
    ]


def goal_buttons() -> list[list[tuple[str, str]]]:
    return [
        [("\\U0001f343 Perder peso", "goal_weight_loss"), ("\\U0001f4aa Ganhar massa", "goal_muscle_gain")],
        [("\\u2696\\ufe0f Manter equilibrio", "goal_maintenance")],
        [("\\U0001f957 Alimentacao saudavel", "goal_health"), ("\\u23f1\\ufe0f Praticidade", "goal_practical")],
    ]'''
text = text[:buttons_start] + buttons_block + text[buttons_end:]

recommend_payload_start = text.index("def recommendation_payload(")
recommend_payload_end = text.index("\n\ndef profile_payload", recommend_payload_start)
recommend_payload = '''def recommendation_payload(context: ContextTypes.DEFAULT_TYPE) -> dict:
    data = profile(context)
    item = recommend_item(data.get("telegram_id"), data.get("restriction", ""), data.get("goal", ""))
    if not item:
        return {
            "text": (
                "\\U0001f614 Ainda nao encontrei uma recomendacao segura com o cardapio atual.\\n\\n"
                "Posso te mostrar o cardapio completo para voce escolher com calma?"
            ),
            "buttons": [[("\\U0001f957 Ver cardapio", "menu_today"), ("\\U0001f464 Meu perfil", "profile")]],
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
            "\\u2b50 <b>Minha recomendacao para hoje:</b>\\n\\n"
            f"<b>{escape(item['dish_name'])}</b> - {format_price(item['price_cents'])}\\n"
            f"Escolhi porque {reason} \\U0001f33f"
        ),
        "buttons": [[(f"\\U0001f37d Pedir {item['dish_name'][:30]}", f"dish:{item['dish_key']}")], [("\\U0001f957 Ver cardapio", "menu_today")]],
    }'''
text = text[:recommend_payload_start] + recommend_payload + text[recommend_payload_end:]

if "<b>Objetivo:</b>" not in text:
    text = text.replace(
        '            f"<b>Endereco/bairro:</b> {escape(data.get(\'address\', \'Nao informado\'))}\\\\n"\n            f"<b>Restricao alimentar:</b>',
        '            f"<b>Endereco/bairro:</b> {escape(data.get(\'address\', \'Nao informado\'))}\\\\n"\n            f"<b>Objetivo:</b> {escape(data.get(\'goal\', \'Nao informado\'))}\\\\n"\n            f"<b>Restricao alimentar:</b>',
    )

registration_start = text.index("async def ask_name(")
registration_end = text.index("\n\nasync def start", registration_start)
registration_block = '''async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "name"
    await send_text(
        update,
        (
            "\\U0001f37d\\ufe0f <b>Antes de iniciar seu pedido, preciso fazer um cadastro rapido.</b>\\n\\n"
            "Assim consigo considerar suas restricoes, objetivo e identificar seu pedido corretamente \\U0001f33f\\n\\n"
            "Qual e o seu nome completo?"
        ),
        context,
        edit=edit,
    )


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "phone"
    await send_text(update, "Obrigado, {name}! \\U0001f60a Agora me envie seu telefone para contato.", context)


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[REGISTRATION_STEP] = "address"
    await send_text(update, "Perfeito \\U0001f4cd Agora me informe seu endereco ou bairro de entrega.", context)


async def ask_goal(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "goal"
    await send_text(
        update,
        "\\U0001f3af Qual e o seu foco principal com a alimentacao agora?",
        context,
        goal_buttons(),
        edit=edit,
    )


async def ask_restriction(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    context.user_data[REGISTRATION_STEP] = "restriction"
    await send_text(update, "\\U0001f33f Para sua seguranca alimentar, selecione sua principal restricao:", context, restriction_buttons(), edit=edit)


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
            data.get("goal", "Nao informado"),
        )
    context.user_data.pop(REGISTRATION_STEP, None)
    await send_text(
        update,
        (
            "\\u2705 <b>Cadastro concluido!</b>\\n\\n"
            "<b>Nome:</b> {name}\\n"
            f"<b>Telefone:</b> {escape(data.get('phone', 'Nao informado'))}\\n"
            f"<b>Endereco/bairro:</b> {escape(data.get('address', 'Nao informado'))}\\n"
            f"<b>Objetivo:</b> {escape(data.get('goal', 'Nao informado'))}\\n"
            f"<b>Restricao alimentar:</b> {escape(restriction)}\\n\\n"
            "Agora posso te ajudar com o cardapio e seus pedidos \\U0001f60a"
        ),
        context,
        main_buttons(),
        edit,
    )'''
text = text[:registration_start] + registration_block + text[registration_end:]

handler_start = text.index("async def handle_registration_message(")
handler_end = text.index("\n\nasync def handle_callback", handler_start)
handler_block = '''async def handle_registration_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    step = context.user_data.get(REGISTRATION_STEP)
    if not step:
        return False
    text = (update.message.text or "").strip()
    if len(text) < 2:
        await send_text(update, "Me envie uma resposta um pouco mais completa, por favor \\U0001f60a", context)
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
    else:
        await ask_restriction(update, context)
    return True'''
text = text[:handler_start] + handler_block + text[handler_end:]

if "if data in GOALS:" not in text:
    text = text.replace(
        '    if data in {"restart_registration", "update_profile"}:\n        profile(context).pop("registered", None)\n        await ask_name(update, context, edit=True)\n        return\n    if data in RESTRICTIONS:',
        '    if data in {"restart_registration", "update_profile"}:\n        profile(context).pop("registered", None)\n        await ask_name(update, context, edit=True)\n        return\n    if data in GOALS:\n        profile(context)["goal"] = GOALS[data]\n        await ask_restriction(update, context, edit=True)\n        return\n    if data in RESTRICTIONS:',
    )

if 'SELECT goal, COUNT(*) AS total' not in text:
    text = text.replace(
        '        ).fetchall()\n    return {"totals": totals, "recent": recent, "top": top}',
        '        ).fetchall()\n        goals = conn.execute(\n            """\n            SELECT goal, COUNT(*) AS total\n            FROM clients\n            GROUP BY goal\n            ORDER BY total DESC, goal\n            """\n        ).fetchall()\n    return {"totals": totals, "recent": recent, "top": top, "goals": goals}',
        1,
    )

if "Objetivos dos clientes:" not in text:
    text = text.replace(
        '    else:\n        lines.append("- Ainda sem pedidos.")\n    lines.extend(["", "Pedidos recentes:"])',
        '    else:\n        lines.append("- Ainda sem pedidos.")\n    lines.extend(["", "Objetivos dos clientes:"])\n    if report["goals"]:\n        lines.extend(f"- {row[\'goal\'] or \'Nao informado\'}: {row[\'total\']}" for row in report["goals"])\n    else:\n        lines.append("- Ainda sem clientes cadastrados.")\n    lines.extend(["", "Pedidos recentes:"])',
        1,
    )

path.write_text(text, encoding="utf-8")
print("Objetivos do cliente e cardapio ampliado adicionados. Rode: python -m unittest discover -s tests")
