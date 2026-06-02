# -*- coding: utf-8 -*-
"""
payments.py — Платёжная система Buxgalter AI
=============================================
Поддерживаемые методы:
  • Payme      (click.uz API v2)
  • Click      (click.uz)
  • Uzum Bank  (Apelsin Pay)
  • Visa/Mastercard — через Payme и Click (они сами принимают карты)

Важно: карточные данные НИКОГДА не проходят через наш сервер.
Всё обрабатывается на стороне платёжных систем (PCI DSS).

Для подключения нужно:
  1. Зарегистрироваться как мерчант на payme.uz и click.uz
  2. Получить API ключи и добавить в Railway Variables
  3. Настроить webhook URL в личном кабинете мерчанта
"""

import os
import time
import hmac
import base64
import hashlib
import logging
import uuid
import aiosqlite
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse

log = logging.getLogger("payments")

# ── Конфигурация (из Railway environment variables) ────────────────────────────

# PAYME (merchant.payme.uz)
PAYME_ID     = os.getenv("PAYME_ID", "")          # Merchant ID
PAYME_KEY    = os.getenv("PAYME_KEY", "")          # Secret Key
PAYME_TEST   = os.getenv("PAYME_TEST", "true").lower() == "true"
PAYME_URL    = "https://checkout.paycom.uz" if not PAYME_TEST else "https://test.paycom.uz"

# CLICK (merchant.click.uz)
CLICK_SERVICE_ID  = os.getenv("CLICK_SERVICE_ID", "")   # Service ID
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")  # Merchant ID
CLICK_SECRET      = os.getenv("CLICK_SECRET", "")       # Secret Key

# UZUM BANK (pay.uzumbank.uz)
UZUM_MERCHANT_ID  = os.getenv("UZUM_MERCHANT_ID", "")
UZUM_SECRET       = os.getenv("UZUM_SECRET", "")
UZUM_URL          = "https://pay.uzumbank.uz/open-api"

# Наш сайт
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://accountant-ai-production-b952.up.railway.app")

# База данных
DB_PATH = "users.db"


# ── Тарифные планы ────────────────────────────────────────────────────────────

PLANS = {
    "pro_monthly": {
        "name":        "PRO — Месячная подписка",
        "price_uzs":   49900 * 100,   # 49,900 сум (в тийинах для Payme)
        "price_som":   49900,          # для отображения
        "requests":    -1,             # безлимит
        "duration":    30,             # дней
    },
    "pro_annual": {
        "name":        "PRO — Годовая подписка",
        "price_uzs":   399000 * 100,  # 399,000 сум
        "price_som":   399000,
        "requests":    -1,
        "duration":    365,
    },
}


# ════════════════════════════════════════════════════════════
# DATABASE — таблицы для платежей
# ════════════════════════════════════════════════════════════

async def init_payments_db():
    """Создаёт таблицы для платежей при первом запуске."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                plan_id         TEXT NOT NULL,
                amount          INTEGER NOT NULL,
                currency        TEXT DEFAULT 'UZS',
                provider        TEXT NOT NULL,
                provider_tx_id  TEXT,
                status          TEXT DEFAULT 'pending',
                created_at      REAL,
                paid_at         REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT UNIQUE NOT NULL,
                plan_id     TEXT NOT NULL,
                started_at  REAL,
                expires_at  REAL,
                payment_id  TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        # Индекс для быстрого поиска
        await db.execute("CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)")
        await db.commit()


async def create_payment_record(user_id: str, plan_id: str,
                                 amount: int, provider: str) -> str:
    """Создаёт запись о платеже в БД. Возвращает payment_id."""
    payment_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO payments (id,user_id,plan_id,amount,provider,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (payment_id, user_id, plan_id, amount, provider, time.time())
        )
        await db.commit()
    return payment_id


async def confirm_payment(payment_id: str, provider_tx_id: str):
    """Подтверждает платёж и активирует подписку."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Обновляем статус платежа
        await db.execute(
            "UPDATE payments SET status='paid', provider_tx_id=?, paid_at=? WHERE id=?",
            (provider_tx_id, time.time(), payment_id)
        )

        # Получаем данные платежа
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        payment = dict(await cur.fetchone())

        plan = PLANS.get(payment["plan_id"], {})
        duration = plan.get("duration", 30)
        now = time.time()
        expires = now + duration * 86400

        # Создаём или обновляем подписку
        await db.execute("""
            INSERT INTO subscriptions (id,user_id,plan_id,started_at,expires_at,payment_id)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                plan_id=excluded.plan_id,
                started_at=excluded.started_at,
                expires_at=excluded.expires_at,
                payment_id=excluded.payment_id
        """, (str(uuid.uuid4()), payment["user_id"], payment["plan_id"],
              now, expires, payment_id))

        # Обновляем план пользователя
        await db.execute(
            "UPDATE users SET plan='pro' WHERE id=?",
            (payment["user_id"],)
        )
        await db.commit()

    log.info(f"✅ Платёж подтверждён: {payment_id} | Пользователь: {payment['user_id']}")


async def get_user_subscription(user_id: str) -> dict | None:
    """Возвращает активную подписку пользователя."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM subscriptions WHERE user_id=? AND expires_at > ?",
            (user_id, time.time())
        )
        row = await cur.fetchone()
        if row:
            s = dict(row)
            s["days_left"] = int((s["expires_at"] - time.time()) / 86400)
            return s
    return None


async def check_and_expire_subscriptions():
    """Деактивирует истёкшие подписки. Вызывать периодически."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM subscriptions WHERE expires_at < ?",
            (time.time(),)
        )
        expired = await cur.fetchall()
        for row in expired:
            await db.execute(
                "UPDATE users SET plan='free' WHERE id=?", (row[0],)
            )
        if expired:
            await db.execute(
                "DELETE FROM subscriptions WHERE expires_at < ?", (time.time(),)
            )
            await db.commit()
            log.info(f"Истекло {len(expired)} подписок")


# ════════════════════════════════════════════════════════════
# PAYME INTEGRATION
# ════════════════════════════════════════════════════════════

def payme_generate_link(payment_id: str, amount_uzs: int,
                         description: str, user_email: str = "") -> str:
    """
    Генерирует ссылку на оплату через Payme.
    Пользователь вводит карту напрямую на сайте Payme — мы данных не видим.
    """
    params = f"m={PAYME_ID};ac.order_id={payment_id};a={amount_uzs}"
    if user_email:
        params += f";c.email={user_email}"

    encoded = base64.b64encode(params.encode()).decode()
    return f"{PAYME_URL}/{encoded}"


def payme_verify_webhook(request_data: dict, auth_header: str) -> bool:
    """Проверяет подпись webhook от Payme."""
    if not PAYME_KEY:
        return False
    expected = base64.b64encode(f"{PAYME_ID}:{PAYME_KEY}".encode()).decode()
    return auth_header == f"Basic {expected}"


async def payme_handle_webhook(data: dict) -> dict:
    """Обрабатывает webhook от Payme."""
    method = data.get("method")
    params = data.get("params", {})
    request_id = data.get("id")

    if method == "CheckPerformTransaction":
        order_id = params.get("account", {}).get("order_id")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM payments WHERE id=?", (order_id,))
            payment = await cur.fetchone()
        if not payment:
            return {"id": request_id, "error": {"code": -31050, "message": "Order not found"}}
        return {"id": request_id, "result": {"allow": True}}

    elif method == "CreateTransaction":
        order_id = params.get("account", {}).get("order_id")
        provider_tx_id = params.get("id")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE payments SET provider_tx_id=?, status='processing' WHERE id=?",
                (provider_tx_id, order_id)
            )
            await db.commit()
        return {"id": request_id, "result": {
            "create_time": int(time.time() * 1000),
            "transaction": provider_tx_id,
            "state": 1
        }}

    elif method == "PerformTransaction":
        provider_tx_id = params.get("id")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM payments WHERE provider_tx_id=?", (provider_tx_id,))
            payment = await cur.fetchone()
        if payment:
            await confirm_payment(payment["id"], provider_tx_id)
        return {"id": request_id, "result": {
            "perform_time": int(time.time() * 1000),
            "transaction": provider_tx_id,
            "state": 2
        }}

    elif method == "CancelTransaction":
        return {"id": request_id, "result": {"cancel_time": int(time.time() * 1000), "state": -1}}

    elif method == "CheckTransaction":
        provider_tx_id = params.get("id")
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM payments WHERE provider_tx_id=?", (provider_tx_id,))
            payment = await cur.fetchone()
        state = 2 if payment and payment["status"] == "paid" else 1
        return {"id": request_id, "result": {"state": state, "transaction": provider_tx_id}}

    return {"id": request_id, "error": {"code": -32601, "message": "Method not found"}}


# ════════════════════════════════════════════════════════════
# CLICK INTEGRATION
# ════════════════════════════════════════════════════════════

def click_generate_link(payment_id: str, amount_som: int,
                         description: str) -> str:
    """
    Генерирует ссылку на оплату через Click.
    Поддерживает Uzcard, Humo, Visa, Mastercard.
    """
    return (
        f"https://my.click.uz/services/pay"
        f"?service_id={CLICK_SERVICE_ID}"
        f"&merchant_id={CLICK_MERCHANT_ID}"
        f"&amount={amount_som}"
        f"&transaction_param={payment_id}"
        f"&return_url={WEBAPP_URL}/payment/success"
    )


def click_verify_sign(click_trans_id: str, service_id: str,
                       merchant_trans_id: str, amount: str,
                       action: str, sign_time: str,
                       sign_string: str) -> bool:
    """Проверяет подпись от Click."""
    if not CLICK_SECRET:
        return False
    raw = (f"{click_trans_id}{service_id}{CLICK_SECRET}"
           f"{merchant_trans_id}{amount}{action}{sign_time}")
    expected = hashlib.md5(raw.encode()).hexdigest()
    return hmac.compare_digest(expected, sign_string)


async def click_handle_prepare(data: dict) -> dict:
    """Обрабатывает Prepare запрос от Click."""
    payment_id = data.get("merchant_trans_id")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        payment = await cur.fetchone()
    if not payment:
        return {"error": -5, "error_note": "Payment not found"}
    return {
        "click_trans_id":    data.get("click_trans_id"),
        "merchant_trans_id": payment_id,
        "merchant_prepare_id": payment_id,
        "error":             0,
        "error_note":        "Success"
    }


async def click_handle_complete(data: dict) -> dict:
    """Обрабатывает Complete запрос от Click (подтверждение оплаты)."""
    payment_id    = data.get("merchant_trans_id")
    click_trans_id = data.get("click_trans_id")
    error          = int(data.get("error", 0))

    if error == 0:
        await confirm_payment(payment_id, str(click_trans_id))

    return {
        "click_trans_id":    click_trans_id,
        "merchant_trans_id": payment_id,
        "merchant_confirm_id": payment_id,
        "error":             0,
        "error_note":        "Success"
    }


# ════════════════════════════════════════════════════════════
# UZUM BANK INTEGRATION
# ════════════════════════════════════════════════════════════

async def uzum_create_payment(payment_id: str, amount_som: int,
                               description: str) -> dict:
    """
    Создаёт платёж через Uzum Bank.
    Возвращает URL для перенаправления пользователя.
    """
    if not UZUM_MERCHANT_ID or not UZUM_SECRET:
        return {"error": "Uzum не настроен"}

    payload = {
        "serviceId":      UZUM_MERCHANT_ID,
        "orderId":        payment_id,
        "amount":         amount_som * 100,  # в тийинах
        "currency":       "UZS",
        "description":    description,
        "returnUrl":      f"{WEBAPP_URL}/payment/success",
        "cancelUrl":      f"{WEBAPP_URL}/payment/cancel",
        "callbackUrl":    f"{WEBAPP_URL}/payment/uzum/notify",
    }

    sign_raw = f"{payment_id}{amount_som * 100}{UZUM_SECRET}"
    payload["sign"] = hashlib.sha256(sign_raw.encode()).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{UZUM_URL}/create",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            result = resp.json()
            if result.get("status") == "success":
                return {"url": result.get("redirectUrl"), "payment_id": payment_id}
    except Exception as e:
        log.error(f"Uzum error: {e}")

    return {"error": "Не удалось создать платёж Uzum Bank"}


async def uzum_handle_webhook(data: dict) -> dict:
    """Обрабатывает уведомление от Uzum Bank."""
    order_id   = data.get("orderId")
    status     = data.get("status")
    trans_id   = data.get("transactionId", "")

    if status == "PAID":
        await confirm_payment(order_id, trans_id)

    return {"status": "ok"}
