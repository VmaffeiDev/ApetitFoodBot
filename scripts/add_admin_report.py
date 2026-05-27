from pathlib import Path

path = Path("bot.py")
text = path.read_text(encoding="utf-8")

report_data = '''

def admin_report_data() -> dict:
    with db() as conn:
        totals = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM clients) AS clients,
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
    return {"totals": totals, "recent": recent, "top": top}
'''

if "def admin_report_data(" not in text:
    text = text.replace("\ndef save_weekly_menu(", report_data + "\n\ndef save_weekly_menu(")

handler = '''

async def show_admin_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(tg_id(update)):
        await update.effective_message.reply_text("\\U0001f512 Apenas administradores podem ver o relatorio.")
        return
    report = admin_report_data()
    totals = report["totals"]
    lines = [
        "\\U0001f4ca Relatorio Apetit Bot",
        "",
        f"Clientes cadastrados: {totals['clients']}",
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
    lines.extend(["", "Pedidos recentes:"])
    if report["recent"]:
        lines.extend(f"- {row['name'] or 'Cliente'} pediu {row['dish_name']}" for row in report["recent"])
    else:
        lines.append("- Ainda sem pedidos recentes.")
    await update.effective_message.reply_text("\\n".join(lines))
'''

if "async def show_admin_report(" not in text:
    text = text.replace("\ndef parse_weekly_menu(", handler + "\n\ndef parse_weekly_menu(")

if 'CommandHandler("relatorio", show_admin_report)' not in text:
    text = text.replace(
        '    app.add_handler(CommandHandler("cardapio_semana", update_weekly_menu))\n',
        '    app.add_handler(CommandHandler("cardapio_semana", update_weekly_menu))\n'
        '    app.add_handler(CommandHandler("relatorio", show_admin_report))\n',
    )

path.write_text(text, encoding="utf-8")
print("Relatorio administrativo adicionado. Rode: python -m unittest discover -s tests")
