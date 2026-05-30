# -*- coding: utf-8 -*-
import os
import sys
import logging

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, '.env'))
load_dotenv(os.path.join(_here, '..', '.env'))

sys.path.insert(0, _here)
from rag import ask_buxgalter

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ── Menus ──────────────────────────────────────────────────────────────────
LANG_MENU = ReplyKeyboardMarkup(
    [["🇷🇺 Русский", "🇺🇿 O'zbek"]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

MENU_RU = ReplyKeyboardMarkup(
    [
        ["НДС (QQS)", "НДФЛ / Соцналог"],
        ["Ставки налогов", "Сроки и штрафы"],
        ["Didox / Soliq.uz", "Другой вопрос"],
        ["🌐 Сменить язык"],
    ],
    resize_keyboard=True,
)

MENU_UZ = ReplyKeyboardMarkup(
    [
        ["QQS (НДС)", "JSHSHT / Ijtimoiy soliq"],
        ["Soliq stavkalari", "Muddatlar va jarima"],
        ["Didox / Soliq.uz", "Boshqa savol"],
        ["🌐 Tilni o'zgartirish"],
    ],
    resize_keyboard=True,
)

# ── Topic hints ────────────────────────────────────────────────────────────
HINTS_RU = {
    "НДС (QQS)": "Расскажи про НДС в Узбекистане: ставка, кто платит, как считать",
    "НДФЛ / Соцналог": "Расскажи про НДФЛ и социальный налог в Узбекистане: ставки, сроки",
    "Ставки налогов": "Какие основные ставки налогов в Узбекистане в 2024 году?",
    "Сроки и штрафы": "Какие сроки сдачи налоговой отчётности и штрафы за нарушения в Узбекистане?",
    "Didox / Soliq.uz": "Как работать с системами Didox и Soliq.uz?",
    "Другой вопрос": None,
    "🌐 Сменить язык": "__LANG__",
}

HINTS_UZ = {
    "QQS (НДС)": "O'zbekistonda QQS haqida: stavka, kim to'laydi, qanday hisoblanadi",
    "JSHSHT / Ijtimoiy soliq": "O'zbekistonda JSHSHT va ijtimoiy soliq haqida: stavkalar, muddatlar",
    "Soliq stavkalari": "O'zbekistonda 2024 yilda asosiy soliq stavkalari qanday?",
    "Muddatlar va jarima": "O'zbekistonda soliq hisobotini topshirish muddatlari va jarimalar",
    "Didox / Soliq.uz": "Didox va Soliq.uz tizimlari bilan qanday ishlash kerak?",
    "Boshqa savol": None,
    "🌐 Tilni o'zgartirish": "__LANG__",
}


def get_lang(context) -> str:
    return context.user_data.get("lang", "ru")


def get_menu(context):
    return MENU_RU if get_lang(context) == "ru" else MENU_UZ


def get_hints(context):
    return HINTS_RU if get_lang(context) == "ru" else HINTS_UZ


# ── Handlers ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выберите язык / Tilni tanlang:",
        reply_markup=LANG_MENU,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Language selection
    if text == "🇷🇺 Русский":
        context.user_data["lang"] = "ru"
        await update.message.reply_text(
            "Язык выбран: Русский.\nЗадайте вопрос по бухгалтерии Узбекистана:",
            reply_markup=MENU_RU,
        )
        return
    if text == "🇺🇿 O'zbek":
        context.user_data["lang"] = "uz"
        await update.message.reply_text(
            "Til tanlandi: O'zbek.\nO'zbekiston buxgalteriyasi bo'yicha savol bering:",
            reply_markup=MENU_UZ,
        )
        return

    hints = get_hints(context)

    # Menu button pressed
    if text in hints:
        action = hints[text]
        if action == "__LANG__":
            await update.message.reply_text(
                "Выберите язык / Tilni tanlang:",
                reply_markup=LANG_MENU,
            )
            return
        if action is None:
            msg = "Напишите ваш вопрос:" if get_lang(context) == "ru" else "Savolingizni yozing:"
            await update.message.reply_text(msg, reply_markup=get_menu(context))
            return
        query = action
    else:
        query = text

    # Typing indicator
    try:
        await update.message.chat.send_action("typing")
    except Exception:
        pass

    try:
        answer = ask_buxgalter(query)
        await update.message.reply_text(answer, reply_markup=get_menu(context))
    except Exception as e:
        logger.error(f"Error in ask_buxgalter: {e}")
        if get_lang(context) == "ru":
            msg = "Произошла ошибка. Попробуйте снова."
        else:
            msg = "Xatolik yuz berdi. Qayta urining."
        await update.message.reply_text(msg, reply_markup=get_menu(context))


def main():
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not found!")
        sys.exit(1)

    print("Buxgalter AI Bot starting...")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
