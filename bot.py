import logging
import os
import unicodedata
from html import escape

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

USER_NAME = os.getenv("APETIT_USER_NAME", "Mariana")


def normalize(text: str) -> str:
    without_accents = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return without_accents.lower().strip()


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


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
            "Como voc\u00ea prefere pratos com pouco queijo, a <b>lasanha</b> parece a melhor op\u00e7\u00e3o hoje \U0001f60a\n\n"
            "Quer saber se ela se encaixa na sua dieta?"
        ),
        "buttons": [
            [("\u2705 Quero a lasanha", "lasagna"), ("\u274c Ver outras", "other_options")],
            [("\U0001f957 Encaixa na minha dieta?", "diet_fit")],
        ],
    },
    "recommend": {
        "text": (
            "Hoje recomendo o <b>\U0001f41f Peixe Assado com Legumes</b>!\n\n"
            "Voc\u00ea avaliou pratos parecidos muito bem e ainda n\u00e3o escolheu peixe esta semana. "
            "\u00c9 uma op\u00e7\u00e3o leve, proteica e alinhada com seus objetivos \U0001f33f"
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
            "O estrogonofe de cogumelos <b>cont\u00e9m lactose</b>, e seu perfil indica intoler\u00e2ncia.\n\n"
            "N\u00e3o encontrei confirma\u00e7\u00e3o de compatibilidade com sua dieta registrada.\n\n"
            "Que tal a <b>lasanha de legumes</b>? Ela \u00e9 sem gl\u00faten e sem lactose \u2705"
        ),
        "buttons": [[("\U0001f957 Ver lasanha", "lasagna"), ("\u270f\ufe0f Atualizar meu perfil", "update_profile")]],
    },
}


RESPONSES = {
    "lasagna": {
        "text": (
            "\u00d3tima escolha! \U0001f33f\n\n"
            "A <b>Lasanha de Legumes</b> de hoje \u00e9 sem gl\u00faten, sem carne e preparada com pouco queijo. "
            "Combina\u00e7\u00e3o perfeita para voc\u00ea!\n\n"
            "Bom apetite, {name} \U0001f60a"
        ),
        "buttons": [[("\u2b50 Avaliar depois", "rate_later"), ("\U0001f514 Me lembrar amanh\u00e3", "remind_tomorrow")]],
    },
    "fish": {
        "text": (
            "\U0001f41f \u00d3tima escolha!\n\n"
            "O <b>Peixe Assado</b> \u00e9 rico em \u00f4mega-3 e vai te deixar com energia para a tarde toda.\n\n"
            "Bom almo\u00e7o! \U0001f60a"
        ),
        "buttons": [[("\u2b50 Avaliar depois", "rate_later")]],
    },
    "soup": {
        "text": (
            "\U0001f958 A <b>Sopa de Lentilha</b> \u00e9 vegana, sem gl\u00faten e super nutritiva. "
            "Uma escolha leve e equilibrada!\n\n"
            "Bom almo\u00e7o \U0001f60a"
        ),
        "buttons": [[("\u2b50 Avaliar depois", "rate_later")]],
    },
    "diet_fit": {
        "text": (
            "\u2705 <b>Sim!</b> A Lasanha de Legumes de hoje \u00e9:\n\n"
            "- Sem gl\u00faten \u2705\n"
            "- Vegetariana \u2705\n"
            "- Preparada com pouco queijo \u2705\n\n"
            "Atende todas as suas restri\u00e7\u00f5es registradas no perfil \U0001f33f"
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
    "profile": {
        "text": (
            "\U0001f464 <b>Seu perfil, {name}:</b>\n\n"
            "\u2705 Vegetariana\n"
            "\u2705 Sem gl\u00faten\n"
            "\u26a0\ufe0f Prefere pouco queijo\n"
            "\u26a0\ufe0f Prefere pratos leves\n\n"
            "Posso atualizar alguma informa\u00e7\u00e3o?"
        ),
        "buttons": [[("\u270f\ufe0f Atualizar", "update_profile"), ("\u2705 Est\u00e1 correto", "thanks")]],
    },
    "update_profile": {
        "text": (
            "\u270f\ufe0f Claro! Vou te fazer algumas perguntas para atualizar seu perfil nutricional.\n\n"
            "Voc\u00ea tem alguma dessas restri\u00e7\u00f5es?"
        ),
        "buttons": [
            [("\U0001f33e Sem gl\u00faten", "profile_gluten"), ("\U0001f95b Sem lactose", "profile_lactose")],
            [("\U0001f969 Vegetariana", "profile_vegetarian"), ("\U0001f420 Sem frutos do mar", "profile_seafood")],
        ],
    },
    "rate_later": {"text": "Perfeito! Te mando um lembrete para avaliar ap\u00f3s o almo\u00e7o \U0001f514", "buttons": []},
    "remind_tomorrow": {"text": "\U0001f514 Combinado! Te aviso amanh\u00e3 com o card\u00e1pio fresquinho \U0001f60a", "buttons": []},
    "thanks": {
        "text": "Sempre que precisar estou aqui \U0001f33f Bom almo\u00e7o!",
        "buttons": [[("\U0001f957 Ver card\u00e1pio", "menu_today")]],
    },
    "profile_gluten": {"text": "Registrado: restri\u00e7\u00e3o a gl\u00faten \u2705", "buttons": [[("\U0001f464 Ver perfil", "profile")]]},
    "profile_lactose": {"text": "Registrado: restri\u00e7\u00e3o a lactose \u2705", "buttons": [[("\U0001f464 Ver perfil", "profile")]]},
    "profile_vegetarian": {"text": "Registrado: prefer\u00eancia vegetariana \u2705", "buttons": [[("\U0001f464 Ver perfil", "profile")]]},
    "profile_seafood": {"text": "Registrado: sem frutos do mar \u2705", "buttons": [[("\U0001f464 Ver perfil", "profile")]]},
}


def render(template: str) -> str:
    return template.format(name=escape(USER_NAME))


async def send_payload(update: Update, payload: dict, edit: bool = False) -> None:
    text = render(payload["text"])
    reply_markup = kb(payload.get("buttons", [])) if payload.get("buttons") else None

    if edit and update.callback_query:
        await update.callback_query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        return

    if update.effective_message:
        await update.effective_message.reply_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_payload(update, FLOWS["start"])


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    payload = RESPONSES.get(query.data) or FLOWS.get(query.data)
    if not payload:
        payload = {
            "text": "Vamos juntos encontrar a melhor op\u00e7\u00e3o para voc\u00ea \U0001f60a\n\nComo posso ajudar hoje?",
            "buttons": FLOWS["start"]["buttons"],
        }

    await send_payload(update, payload, edit=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = normalize(update.message.text or "")

    if any(term in text for term in ["sem carne", "vegetariano", "vegetariana"]):
        await send_payload(update, FLOWS["no_meat"])
    elif any(term in text for term in ["recomenda", "sugere", "melhor opcao"]):
        await send_payload(update, FLOWS["recommend"])
    elif any(term in text for term in ["fria", "frio", "ruim", "reclamacao", "problema"]):
        await send_payload(update, FLOWS["complaint"])
    elif any(term in text for term in ["gostei", "bom", "otimo", "excelente"]):
        await send_payload(update, FLOWS["feedback"])
    elif any(term in text for term in ["estrogonofe", "lactose", "restricao"]):
        await send_payload(update, FLOWS["restriction"])
    elif any(term in text for term in ["cardapio", "tem hoje", "o que tem"]):
        await send_payload(update, FLOWS["menu_today"])
    elif "perfil" in text:
        await send_payload(update, RESPONSES["profile"])
    else:
        await send_payload(
            update,
            {
                "text": (
                    "Vamos juntos encontrar a melhor op\u00e7\u00e3o para voc\u00ea \U0001f60a\n\n"
                    "Quer ver o card\u00e1pio de hoje ou prefere uma recomenda\u00e7\u00e3o personalizada?"
                ),
                "buttons": [
                    [("\U0001f957 Card\u00e1pio", "menu_today"), ("\u2b50 Recomenda\u00e7\u00e3o", "recommend")],
                    [("\U0001f464 Meu perfil", "profile")],
                ],
            },
        )


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Defina TELEGRAM_BOT_TOKEN no arquivo .env antes de iniciar o bot.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Apetit Bot iniciado.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
