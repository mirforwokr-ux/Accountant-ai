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
    resize_keyboard=True, one_time_keyboard=True,
)

MENU_RU = ReplyKeyboardMarkup([
    ["НДС (QQS)", "НДФЛ / Соцналог"],
    ["Ставки налогов", "Сроки и штрафы"],
    ["Didox / Soliq.uz", "Другой вопрос"],
    ["🧮 Калькуляторы", "🌐 Сменить язык"],
], resize_keyboard=True)

MENU_UZ = ReplyKeyboardMarkup([
    ["QQS (НДС)", "JSHSHT / Ijtimoiy soliq"],
    ["Soliq stavkalari", "Muddatlar va jarima"],
    ["Didox / Soliq.uz", "Boshqa savol"],
    ["🧮 Kalkulyatorlar", "🌐 Tilni o'zgartirish"],
], resize_keyboard=True)

CALC_MENU_RU = ReplyKeyboardMarkup([
    ["📊 НДС с суммы", "📊 Выделить НДС"],
    ["💰 Зарплата (gross→net)", "📈 Налог на прибыль"],
    ["📉 Упрощённый налог (УСН)", "🏠 Назад"],
], resize_keyboard=True)

CALC_MENU_UZ = ReplyKeyboardMarkup([
    ["📊 QQS hisoblash", "📊 QQS ajratish"],
    ["💰 Ish haqi (gross→net)", "📈 Foyda solig'i"],
    ["📉 Soddalashtirilgan soliq", "🏠 Orqaga"],
], resize_keyboard=True)

# ── Topic hints ────────────────────────────────────────────────────────────
HINTS_RU = {
    "НДС (QQS)": "Как работает НДС в Узбекистане: ставка, кто платит, как считать, вычеты",
    "НДФЛ / Соцналог": "НДФЛ и социальный налог в Узбекистане: ставки, сроки уплаты, расчёт",
    "Ставки налогов": "Все основные ставки налогов в Узбекистане в 2024 году",
    "Сроки и штрафы": "Сроки сдачи налоговой отчётности и штрафы за нарушения в Узбекистане",
    "Didox / Soliq.uz": "Как работать с системами Didox и Soliq.uz",
    "Другой вопрос": None,
    "🧮 Калькуляторы": "__CALC__",
    "🌐 Сменить язык": "__LANG__",
}

HINTS_UZ = {
    "QQS (НДС)": "O'zbekistonda QQS qanday ishlaydi: stavka, kim to'laydi, hisoblash",
    "JSHSHT / Ijtimoiy soliq": "O'zbekistonda JSHSHT va ijtimoiy soliq: stavkalar, muddatlar",
    "Soliq stavkalari": "O'zbekistonda 2024 yilda barcha asosiy soliq stavkalari",
    "Muddatlar va jarima": "Soliq hisobotini topshirish muddatlari va jarimalar",
    "Didox / Soliq.uz": "Didox va Soliq.uz tizimlari bilan ishlash",
    "Boshqa savol": None,
    "🧮 Kalkulyatorlar": "__CALC__",
    "🌐 Tilni o'zgartirish": "__LANG__",
}

# Calculator states
CALC_STATES = {
    "nds_on":      "Введите сумму БЕЗ НДС (в сумах):",
    "nds_off":     "Введите сумму С НДС (в сумах) чтобы выделить НДС:",
    "salary":      "Введите начисленную зарплату (gross) в сумах:",
    "profit_rev":  "Введите выручку за период (в сумах):",
    "usn":         "Введите выручку за квартал (в сумах):",
}


# ── Helpers ────────────────────────────────────────────────────────────────
def get_lang(ctx): return ctx.user_data.get("lang", "ru")
def get_menu(ctx): return MENU_RU if get_lang(ctx) == "ru" else MENU_UZ
def get_hints(ctx): return HINTS_RU if get_lang(ctx) == "ru" else HINTS_UZ
def get_calc_menu(ctx): return CALC_MENU_RU if get_lang(ctx) == "ru" else CALC_MENU_UZ

def fmt(n): return f"{n:,.0f}".replace(",", " ")

def parse_number(text):
    text = text.replace(" ", "").replace(",", "").replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


# ── Calculators ────────────────────────────────────────────────────────────
def calc_nds_on(amount):
    nds = amount * 0.12
    total = amount + nds
    return (
        f"📊 *Расчёт НДС (12%)*\n\n"
        f"Сумма без НДС:  `{fmt(amount)}` сум\n"
        f"НДС (12%):       `{fmt(nds)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Итого с НДС:    `{fmt(total)}` сум"
    )

def calc_nds_off(total):
    nds = total * 12 / 112
    amount = total - nds
    return (
        f"📊 *Выделение НДС (12%)*\n\n"
        f"Сумма с НДС:    `{fmt(total)}` сум\n"
        f"НДС (12/112):   `{fmt(nds)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Сумма без НДС:  `{fmt(amount)}` сум"
    )

def calc_salary(gross):
    ndfl = gross * 0.12
    net = gross - ndfl
    social = gross * 0.12
    total_employer = gross + social
    return (
        f"💰 *Расчёт зарплаты*\n\n"
        f"Начислено (gross):        `{fmt(gross)}` сум\n"
        f"НДФЛ 12% (удержан):      `{fmt(ndfl)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"На руки (net):            `{fmt(net)}` сум\n\n"
        f"*Расходы работодателя:*\n"
        f"Зарплата gross:           `{fmt(gross)}` сум\n"
        f"Соцналог 12%:             `{fmt(social)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Итого расходов:           `{fmt(total_employer)}` сум"
    )

def calc_profit(revenue, expenses):
    profit = revenue - expenses
    if profit <= 0:
        return (
            f"📈 *Расчёт налога на прибыль*\n\n"
            f"Выручка:    `{fmt(revenue)}` сум\n"
            f"Расходы:    `{fmt(expenses)}` сум\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"Прибыль:    `{fmt(profit)}` сум\n"
            f"Налог:       `0` сум (убыток)"
        )
    tax = profit * 0.15
    net = profit - tax
    return (
        f"📈 *Расчёт налога на прибыль (15%)*\n\n"
        f"Выручка:              `{fmt(revenue)}` сум\n"
        f"Расходы:              `{fmt(expenses)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Прибыль до налога:   `{fmt(profit)}` сум\n"
        f"Налог на прибыль 15%: `{fmt(tax)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Чистая прибыль:      `{fmt(net)}` сум"
    )

def calc_usn(revenue):
    tax = revenue * 0.04
    net = revenue - tax
    return (
        f"📉 *Упрощённый налог (УСН 4%)*\n\n"
        f"Выручка за квартал:  `{fmt(revenue)}` сум\n"
        f"Налог 4%:             `{fmt(tax)}` сум\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Остаток после налога: `{fmt(net)}` сум\n\n"
        f"_Срок уплаты: до 20-го числа месяца после квартала_"
    )


# ── Handlers ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Выберите язык / Tilni tanlang:",
        reply_markup=LANG_MENU,
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    state = context.user_data.get("state")

    # ── Language selection ─────────────────────────────────────────────────
    if text == "🇷🇺 Русский":
        context.user_data["lang"] = "ru"
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "Язык: Русский ✅\nЗадайте вопрос или выберите раздел:",
            reply_markup=MENU_RU,
        )
        return

    if text == "🇺🇿 O'zbek":
        context.user_data["lang"] = "uz"
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "Til: O'zbek ✅\nSavol bering yoki bo'limni tanlang:",
            reply_markup=MENU_UZ,
        )
        return

    # ── Back / Orqaga ──────────────────────────────────────────────────────
    if text in ("🏠 Назад", "🏠 Orqaga"):
        context.user_data.pop("state", None)
        await update.message.reply_text(
            "Главное меню:" if get_lang(context) == "ru" else "Asosiy menyu:",
            reply_markup=get_menu(context),
        )
        return

    # ── Calculator state machine ───────────────────────────────────────────
    if state:
        num = parse_number(text)
        if num is None:
            await update.message.reply_text(
                "Введите число (например: 5000000):" if get_lang(context) == "ru"
                else "Raqam kiriting (masalan: 5000000):"
            )
            return

        result = None

        if state == "nds_on":
            result = calc_nds_on(num)
        elif state == "nds_off":
            result = calc_nds_off(num)
        elif state == "salary":
            result = calc_salary(num)
        elif state == "profit_rev":
            context.user_data["profit_rev"] = num
            context.user_data["state"] = "profit_exp"
            await update.message.reply_text(
                "Введите расходы за период (в сумах):"
                if get_lang(context) == "ru" else "Xarajatlarni kiriting (so'mda):"
            )
            return
        elif state == "profit_exp":
            revenue = context.user_data.pop("profit_rev", 0)
            result = calc_profit(revenue, num)
        elif state == "usn":
            result = calc_usn(num)

        context.user_data.pop("state", None)

        if result:
            await update.message.reply_text(
                result, parse_mode="Markdown", reply_markup=get_calc_menu(context)
            )
        return

    # ── Calculator menu buttons ────────────────────────────────────────────
    calc_triggers = {
        "📊 НДС с суммы":            ("nds_on",     "Введите сумму БЕЗ НДС (в сумах):"),
        "📊 QQS hisoblash":           ("nds_on",     "QQS siz summa kiriting (so'mda):"),
        "📊 Выделить НДС":            ("nds_off",    "Введите сумму С НДС (в сумах):"),
        "📊 QQS ajratish":            ("nds_off",    "QQS bilan summa kiriting (so'mda):"),
        "💰 Зарплата (gross→net)":    ("salary",     "Введите начисленную зарплату (gross) в сумах:"),
        "💰 Ish haqi (gross→net)":    ("salary",     "Hisoblangan ish haqini kiriting (so'mda):"),
        "📈 Налог на прибыль":        ("profit_rev", "Введите выручку за период (в сумах):"),
        "📈 Foyda solig'i":           ("profit_rev", "Daromadni kiriting (so'mda):"),
        "📉 Упрощённый налог (УСН)":  ("usn",        "Введите выручку за квартал (в сумах):"),
        "📉 Soddalashtirilgan soliq":  ("usn",        "Chorak daromadini kiriting (so'mda):"),
    }

    if text in calc_triggers:
        st, prompt = calc_triggers[text]
        context.user_data["state"] = st
        await update.message.reply_text(prompt)
        return

    # ── Main menu buttons ──────────────────────────────────────────────────
    hints = get_hints(context)

    if text in hints:
        action = hints[text]
        if action == "__LANG__":
            context.user_data.pop("state", None)
            await update.message.reply_text(
                "Выберите язык / Tilni tanlang:", reply_markup=LANG_MENU
            )
            return
        if action == "__CALC__":
            await update.message.reply_text(
                "🧮 Выберите калькулятор:" if get_lang(context) == "ru"
                else "🧮 Kalkulyatorni tanlang:",
                reply_markup=get_calc_menu(context),
            )
            return
        if action is None:
            await update.message.reply_text(
                "Напишите ваш вопрос:" if get_lang(context) == "ru"
                else "Savolingizni yozing:",
                reply_markup=get_menu(context),
            )
            return
        query = action
    else:
        query = text

    # ── AI answer ──────────────────────────────────────────────────────────
    try:
        await update.message.chat.send_action("typing")
    except Exception:
        pass

    try:
        answer = ask_buxgalter(query)
        await update.message.reply_text(answer, reply_markup=get_menu(context))
    except Exception as e:
        logger.error(f"Error: {e}")
        msg = ("Произошла ошибка. Попробуйте снова." if get_lang(context) == "ru"
               else "Xatolik yuz berdi. Qayta urining.")
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
