# -*- coding: utf-8 -*-
"""
notifications.py — Система уведомлений Buxgalter AI
====================================================
Каналы:
  • Email      — SMTP (Gmail / Brevo / SendGrid)
  • SMS        — Eskiz.uz (Узбекистан) / Playmobile.uz
  • Web Push   — Push API (браузерные уведомления, VAPID)
  • Telegram   — через Bot API

Налоговый календарь:
  • Автоматические напоминания о сроках отчётности
  • Персональные события пользователя
  • Push за 3 дня, 1 день и в день дедлайна

Запуск фонового планировщика:
  Добавить в start.py: asyncio.create_task(start_scheduler())

Env Variables (Railway):
  EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASSWORD
  ESKIZ_EMAIL, ESKIZ_PASSWORD  (eskiz.uz SMS API)
  VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_CLAIMS_EMAIL
  TELEGRAM_BOT_TOKEN  (уже есть)
"""

import os
import json
import time
import email
import smtplib
import logging
import asyncio
import aiosqlite
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

log = logging.getLogger("notifications")

DB_PATH = "users.db"

# ── Email конфигурация ─────────────────────────────────────────────────────────
EMAIL_HOST     = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT     = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USER     = os.getenv("EMAIL_USER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_FROM     = os.getenv("EMAIL_FROM", "Buxgalter AI <noreply@buxgalter.ai>")

# ── SMS Eskiz.uz ───────────────────────────────────────────────────────────────
ESKIZ_EMAIL    = os.getenv("ESKIZ_EMAIL", "")
ESKIZ_PASSWORD = os.getenv("ESKIZ_PASSWORD", "")
ESKIZ_BASE     = "https://notify.eskiz.uz/api"
ESKIZ_SENDER   = "4546"  # Зарегистрированное имя отправителя

# ── Telegram ───────────────────────────────────────────────────────────────────
TG_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_API         = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

# ── Web Push VAPID ─────────────────────────────────────────────────────────────
VAPID_PUBLIC   = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE  = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL    = os.getenv("VAPID_CLAIMS_EMAIL", "admin@buxgalter.ai")

WEBAPP_URL     = os.getenv("WEBAPP_URL", "https://accountant-ai-production-b952.up.railway.app")


# ════════════════════════════════════════════════════════════════════════════════
# DATABASE — таблицы уведомлений
# ════════════════════════════════════════════════════════════════════════════════

async def init_notifications_db():
    """Создаёт таблицы для уведомлений."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Настройки уведомлений пользователя
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notification_settings (
                user_id         TEXT PRIMARY KEY,
                email_enabled   INTEGER DEFAULT 1,
                sms_enabled     INTEGER DEFAULT 0,
                push_enabled    INTEGER DEFAULT 1,
                tg_enabled      INTEGER DEFAULT 0,
                phone           TEXT DEFAULT '',
                tg_chat_id      TEXT DEFAULT '',
                remind_days     INTEGER DEFAULT 3,
                language        TEXT DEFAULT 'ru',
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        # Push подписки браузеров
        await db.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT,
                endpoint        TEXT UNIQUE,
                p256dh          TEXT,
                auth            TEXT,
                created_at      REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        # Пользовательские события календаря
        await db.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id              TEXT PRIMARY KEY,
                user_id         TEXT,
                title           TEXT,
                description     TEXT DEFAULT '',
                event_date      TEXT,
                event_type      TEXT DEFAULT 'custom',
                remind_sent     INTEGER DEFAULT 0,
                created_at      REAL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        # Лог отправленных уведомлений
        await db.execute("""
            CREATE TABLE IF NOT EXISTS notification_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT,
                channel         TEXT,
                subject         TEXT,
                status          TEXT,
                sent_at         REAL
            )
        """)
        await db.commit()
    log.info("✓ Таблицы уведомлений инициализированы")


# ════════════════════════════════════════════════════════════════════════════════
# НАЛОГОВЫЙ КАЛЕНДАРЬ — фиксированные дедлайны РУз
# ════════════════════════════════════════════════════════════════════════════════

def get_tax_deadlines(year: int, month: int) -> list[dict]:
    """
    Возвращает налоговые дедлайны для заданного месяца.
    Источник: НК РУз, актуальные сроки 2025-2026.
    """
    deadlines = []

    # Ежемесячные
    monthly = [
        {"day": 20, "title": "УСН — уплата налога с оборота", "tax": "УСН", "type": "payment"},
        {"day": 20, "title": "Социальный налог — уплата", "tax": "Соцналог", "type": "payment"},
        {"day": 20, "title": "НДФЛ — уплата (налоговый агент)", "tax": "НДФЛ", "type": "payment"},
        {"day": 25, "title": "НДС — декларация и уплата", "tax": "НДС", "type": "filing"},
        {"day": 25, "title": "Налог на прибыль — авансовый платёж", "tax": "Прибыль", "type": "payment"},
    ]
    for d in monthly:
        try:
            deadlines.append({
                "date":        datetime(year, month, d["day"]).strftime("%Y-%m-%d"),
                "title":       d["title"],
                "tax":         d["tax"],
                "type":        d["type"],
                "recurring":   "monthly",
            })
        except ValueError:
            pass  # Если дня нет в месяце (например 31 февраля)

    # Квартальные (апрель, июль, октябрь, январь)
    if month in (4, 7, 10, 1):
        quarterly = [
            {"day": 20, "title": "УСН — квартальная декларация", "tax": "УСН", "type": "filing"},
            {"day": 25, "title": "НДС — квартальный отчёт", "tax": "НДС", "type": "filing"},
            {"day": 25, "title": "Налог на прибыль — квартальная декларация", "tax": "Прибыль", "type": "filing"},
        ]
        for d in quarterly:
            try:
                deadlines.append({
                    "date":      datetime(year, month, d["day"]).strftime("%Y-%m-%d"),
                    "title":     d["title"] + f" (Q{(month-1)//3 or 4})",
                    "tax":       d["tax"],
                    "type":      d["type"],
                    "recurring": "quarterly",
                })
            except ValueError:
                pass

    # Годовые (январь-февраль)
    if month == 2:
        annual = [
            {"day": 1,  "title": "Налог на имущество — годовая декларация", "tax": "Имущество", "type": "filing"},
            {"day": 1,  "title": "Земельный налог — годовая декларация", "tax": "Земля", "type": "filing"},
            {"day": 15, "title": "Годовая финансовая отчётность (баланс)", "tax": "Бухотчёт", "type": "filing"},
        ]
        for d in annual:
            deadlines.append({
                "date":      datetime(year, month, d["day"]).strftime("%Y-%m-%d"),
                "title":     d["title"],
                "tax":       d["tax"],
                "type":      d["type"],
                "recurring": "annual",
            })
    if month == 4:
        deadlines.append({
            "date":      datetime(year, 4, 1).strftime("%Y-%m-%d"),
            "title":     "3-НДФЛ / Декларация о доходах ИП за год",
            "tax":       "НДФЛ",
            "type":      "filing",
            "recurring": "annual",
        })

    return sorted(deadlines, key=lambda x: x["date"])


def get_upcoming_deadlines(days_ahead: int = 30) -> list[dict]:
    """Возвращает дедлайны на ближайшие N дней."""
    today = datetime.now()
    result = []
    # Проверяем текущий и следующий месяц
    for offset in range(2):
        m = today.month + offset
        y = today.year
        if m > 12:
            m -= 12
            y += 1
        for d in get_tax_deadlines(y, m):
            deadline_date = datetime.strptime(d["date"], "%Y-%m-%d")
            days_left = (deadline_date - today).days
            if 0 <= days_left <= days_ahead:
                d["days_left"] = days_left
                result.append(d)
    return sorted(result, key=lambda x: x["date"])


# ════════════════════════════════════════════════════════════════════════════════
# EMAIL
# ════════════════════════════════════════════════════════════════════════════════

async def send_email(to: str, subject: str, html: str, text: str = "") -> bool:
    """Отправляет HTML email через SMTP."""
    if not EMAIL_USER or not EMAIL_PASSWORD:
        log.warning("Email не настроен (EMAIL_USER / EMAIL_PASSWORD отсутствуют)")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_FROM
    msg["To"]      = to

    if text:
        msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        loop = asyncio.get_event_loop()
        def _send():
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as s:
                s.starttls()
                s.login(EMAIL_USER, EMAIL_PASSWORD)
                s.sendmail(EMAIL_USER, to, msg.as_string())
        await loop.run_in_executor(None, _send)
        log.info(f"✉️ Email отправлен: {to} | {subject}")
        return True
    except Exception as e:
        log.error(f"Ошибка email: {e}")
        return False


def build_deadline_email(deadlines: list[dict], user_name: str, lang: str = "ru") -> str:
    """Строит HTML письмо с дедлайнами."""
    rows = ""
    for d in deadlines:
        days = d.get("days_left", 0)
        urgency_color = "#DC4A4A" if days <= 1 else ("#C2820C" if days <= 3 else "#6D28D9")
        badge = f"{'СРОЧНО' if days == 0 else f'через {days} д.'}"
        rows += f"""
        <tr>
          <td style="padding:12px 16px;border-bottom:1px solid #f0eef8;">
            <b style="color:#1A1426">{d['title']}</b><br>
            <span style="font-size:12px;color:#847E92">{d['date']} · {d['tax']}</span>
          </td>
          <td style="padding:12px 16px;text-align:right;border-bottom:1px solid #f0eef8;white-space:nowrap">
            <span style="background:{urgency_color};color:white;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700">
              {badge}
            </span>
          </td>
        </tr>"""

    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"></head>
    <body style="margin:0;padding:0;background:#F6F5F9;font-family:-apple-system,sans-serif">
      <div style="max-width:560px;margin:30px auto">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#6D28D9,#4C1D95);padding:28px 30px;border-radius:16px 16px 0 0">
          <div style="color:white;font-size:20px;font-weight:800">⚖️ Buxgalter AI</div>
          <div style="color:rgba(255,255,255,.8);font-size:13px;margin-top:4px">Напоминание о налоговых сроках</div>
        </div>
        <!-- Body -->
        <div style="background:white;padding:28px 30px">
          <p style="font-size:15px;color:#1A1426;margin:0 0 20px">
            Здравствуйте, <b>{user_name}</b>! Приближаются важные налоговые дедлайны:
          </p>
          <table style="width:100%;border-collapse:collapse;border-radius:12px;overflow:hidden;border:1px solid #ece9f1">
            <thead>
              <tr style="background:#F6F5F9">
                <th style="padding:10px 16px;text-align:left;font-size:12px;color:#847E92;font-weight:600">СОБЫТИЕ</th>
                <th style="padding:10px 16px;text-align:right;font-size:12px;color:#847E92;font-weight:600">СРОК</th>
              </tr>
            </thead>
            <tbody>{rows}</tbody>
          </table>
          <div style="margin-top:20px;padding:14px 18px;background:#F5F1FD;border-radius:12px;border:1px solid #ede6fb">
            <p style="margin:0;font-size:13px;color:#5B21B6">
              💡 Задайте вопрос ИИ-Бухгалтеру — получите пошаговую инструкцию по подаче отчёта
            </p>
          </div>
          <div style="text-align:center;margin-top:24px">
            <a href="{WEBAPP_URL}" style="background:#6D28D9;color:white;padding:12px 28px;border-radius:12px;text-decoration:none;font-weight:700;font-size:14px">
              Открыть платформу →
            </a>
          </div>
        </div>
        <!-- Footer -->
        <div style="padding:16px 30px;text-align:center;font-size:11px;color:#ABA6B6">
          Вы получаете это письмо как пользователь Buxgalter AI.<br>
          <a href="{WEBAPP_URL}/settings" style="color:#6D28D9">Изменить настройки уведомлений</a>
        </div>
      </div>
    </body></html>"""


# ════════════════════════════════════════════════════════════════════════════════
# SMS — Eskiz.uz
# ════════════════════════════════════════════════════════════════════════════════

_eskiz_token = {"token": "", "expires": 0}

async def eskiz_get_token() -> str:
    """Получает или обновляет JWT токен Eskiz."""
    if _eskiz_token["token"] and time.time() < _eskiz_token["expires"]:
        return _eskiz_token["token"]

    if not ESKIZ_EMAIL or not ESKIZ_PASSWORD:
        return ""

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{ESKIZ_BASE}/auth/login",
                data={"email": ESKIZ_EMAIL, "password": ESKIZ_PASSWORD}
            )
            data = resp.json()
            token = data.get("data", {}).get("token", "")
            if token:
                _eskiz_token["token"]   = token
                _eskiz_token["expires"] = time.time() + 25 * 3600  # 25 часов
                return token
    except Exception as e:
        log.error(f"Eskiz auth error: {e}")
    return ""


async def send_sms(phone: str, message: str) -> bool:
    """Отправляет SMS через Eskiz.uz."""
    # Нормализуем номер: +998901234567 → 998901234567
    phone = phone.replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not phone.startswith("998"):
        phone = "998" + phone.lstrip("0")

    token = await eskiz_get_token()
    if not token:
        log.warning("SMS не настроен (ESKIZ_EMAIL / ESKIZ_PASSWORD отсутствуют)")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{ESKIZ_BASE}/message/sms/send",
                data={
                    "mobile_phone": phone,
                    "message":      message[:160],  # SMS ограничен 160 символами
                    "from":         ESKIZ_SENDER,
                },
                headers={"Authorization": f"Bearer {token}"}
            )
            result = resp.json()
            if result.get("status") == "waiting":
                log.info(f"📱 SMS отправлен: {phone}")
                return True
            log.warning(f"SMS ответ: {result}")
    except Exception as e:
        log.error(f"SMS ошибка: {e}")
    return False


def build_deadline_sms(deadlines: list[dict]) -> str:
    """Строит текст SMS о дедлайнах."""
    if not deadlines:
        return ""
    lines = ["Buxgalter AI: налоговые сроки!"]
    for d in deadlines[:3]:  # Максимум 3 в SMS
        days = d.get("days_left", 0)
        date = d["date"][5:]  # MM-DD
        label = "СЕГОДНЯ" if days == 0 else f"через {days}д"
        lines.append(f"• {d['tax']}: {d['title'][:30]} ({date}, {label})")
    lines.append(f"Подробнее: {WEBAPP_URL}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# TELEGRAM BOT — уведомления
# ════════════════════════════════════════════════════════════════════════════════

async def send_telegram_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    """Отправляет сообщение в Telegram."""
    if not TG_BOT_TOKEN or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{TG_API}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
            )
            return resp.status_code == 200
    except Exception as e:
        log.error(f"Telegram error: {e}")
        return False


def build_deadline_tg(deadlines: list[dict]) -> str:
    """Строит Telegram сообщение о дедлайнах."""
    lines = ["⚖️ <b>Buxgalter AI — Налоговые дедлайны</b>\n"]
    for d in deadlines:
        days = d.get("days_left", 0)
        icon = "🔴" if days <= 1 else ("🟡" if days <= 3 else "🟢")
        days_txt = "СЕГОДНЯ" if days == 0 else f"через {days} дн."
        lines.append(f"{icon} <b>{d['title']}</b>\n   📅 {d['date']} ({days_txt})\n")
    lines.append(f"\n👉 <a href='{WEBAPP_URL}'>Открыть платформу</a>")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════════
# WEB PUSH
# ════════════════════════════════════════════════════════════════════════════════

async def save_push_subscription(user_id: str, subscription: dict) -> bool:
    """Сохраняет Push подписку браузера в БД."""
    endpoint = subscription.get("endpoint", "")
    keys     = subscription.get("keys", {})
    p256dh   = keys.get("p256dh", "")
    auth     = keys.get("auth", "")

    if not endpoint:
        return False

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO push_subscriptions
            (user_id, endpoint, p256dh, auth, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, endpoint, p256dh, auth, time.time()))
        await db.commit()
    return True


async def send_web_push(user_id: str, title: str, body: str, url: str = "") -> int:
    """
    Отправляет Web Push уведомление всем браузерам пользователя.
    Требует: pip install pywebpush + VAPID ключи.
    Возвращает количество успешно отправленных.
    """
    if not VAPID_PRIVATE or not VAPID_PUBLIC:
        log.warning("Web Push не настроен (VAPID ключи отсутствуют)")
        return 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        log.warning("pywebpush не установлен: pip install pywebpush")
        return 0

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM push_subscriptions WHERE user_id=?", (user_id,))
        subs = await cur.fetchall()

    sent  = 0
    payload = json.dumps({
        "title": title,
        "body":  body,
        "icon":  f"{WEBAPP_URL}/favicon.ico",
        "url":   url or WEBAPP_URL,
    })

    for sub in subs:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda s=sub: webpush(
                subscription_info={
                    "endpoint": s["endpoint"],
                    "keys":     {"p256dh": s["p256dh"], "auth": s["auth"]},
                },
                data=payload,
                vapid_private_key=VAPID_PRIVATE,
                vapid_claims={"sub": f"mailto:{VAPID_EMAIL}"},
            ))
            sent += 1
        except Exception as e:
            log.warning(f"Push error для {sub['endpoint'][:50]}: {e}")

    if sent:
        log.info(f"🔔 Push отправлен {sent}/{len(subs)} браузерам: {user_id}")
    return sent


# ════════════════════════════════════════════════════════════════════════════════
# НАСТРОЙКИ УВЕДОМЛЕНИЙ ПОЛЬЗОВАТЕЛЯ
# ════════════════════════════════════════════════════════════════════════════════

async def get_notification_settings(user_id: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM notification_settings WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
    if row:
        return dict(row)
    # Дефолт
    return {
        "user_id":      user_id,
        "email_enabled": 1,
        "sms_enabled":   0,
        "push_enabled":  1,
        "tg_enabled":    0,
        "phone":         "",
        "tg_chat_id":    "",
        "remind_days":   3,
        "language":      "ru",
    }


async def save_notification_settings(user_id: str, settings: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO notification_settings
            (user_id, email_enabled, sms_enabled, push_enabled, tg_enabled,
             phone, tg_chat_id, remind_days, language)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            int(settings.get("email_enabled", 1)),
            int(settings.get("sms_enabled", 0)),
            int(settings.get("push_enabled", 1)),
            int(settings.get("tg_enabled", 0)),
            str(settings.get("phone", "")),
            str(settings.get("tg_chat_id", "")),
            int(settings.get("remind_days", 3)),
            str(settings.get("language", "ru")),
        ))
        await db.commit()


# ════════════════════════════════════════════════════════════════════════════════
# ПОЛЬЗОВАТЕЛЬСКИЕ СОБЫТИЯ КАЛЕНДАРЯ
# ════════════════════════════════════════════════════════════════════════════════

async def add_calendar_event(user_id: str, title: str, event_date: str,
                              description: str = "", event_type: str = "custom") -> str:
    import uuid
    event_id = str(uuid.uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO calendar_events (id, user_id, title, description, event_date, event_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event_id, user_id, title, description, event_date, event_type, time.time()))
        await db.commit()
    return event_id


async def get_user_events(user_id: str, from_date: str = None, days: int = 60) -> list:
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    until = (datetime.strptime(from_date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM calendar_events WHERE user_id=? AND event_date BETWEEN ? AND ? ORDER BY event_date",
            (user_id, from_date, until))
        return [dict(r) for r in await cur.fetchall()]


async def delete_calendar_event(user_id: str, event_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM calendar_events WHERE id=? AND user_id=?", (event_id, user_id))
        await db.commit()


# ════════════════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ПЛАНИРОВЩИК — фоновая задача
# ════════════════════════════════════════════════════════════════════════════════

async def _send_notifications_for_user(user: dict, settings: dict, deadlines: list):
    """Отправляет уведомления пользователю по всем настроенным каналам."""
    if not deadlines:
        return

    user_id   = user["id"]
    email     = user.get("email", "")
    user_name = user.get("name", "Пользователь")

    tasks = []

    # Email
    if settings.get("email_enabled") and email:
        html = build_deadline_email(deadlines, user_name)
        subject = f"⚠️ Налоговые дедлайны: {', '.join(set(d['tax'] for d in deadlines[:3]))}"
        tasks.append(send_email(email, subject, html))

    # SMS
    if settings.get("sms_enabled") and settings.get("phone"):
        sms_text = build_deadline_sms(deadlines)
        tasks.append(send_sms(settings["phone"], sms_text))

    # Telegram
    if settings.get("tg_enabled") and settings.get("tg_chat_id"):
        tg_text = build_deadline_tg(deadlines)
        tasks.append(send_telegram_message(settings["tg_chat_id"], tg_text))

    # Web Push
    if settings.get("push_enabled"):
        title  = f"⚠️ {len(deadlines)} налоговых дедлайна приближается"
        first  = deadlines[0]
        body   = f"{first['title']} — {first['date']}"
        tasks.append(send_web_push(user_id, title, body))

    # Запускаем всё параллельно
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

        # Логируем
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO notification_log (user_id, channel, subject, status, sent_at) VALUES (?,?,?,?,?)",
                (user_id, "multi", f"{len(deadlines)} дедлайнов", "sent", time.time())
            )
            await db.commit()


async def run_daily_notifications():
    """Ежедневная задача — проверяет дедлайны и отправляет уведомления."""
    log.info("🔔 Запуск ежедневных уведомлений...")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE email != '' OR plan != ''")
        users = [dict(r) for r in await cur.fetchall()]

    for user in users:
        settings = await get_notification_settings(user["id"])
        remind_days = settings.get("remind_days", 3)
        deadlines = get_upcoming_deadlines(days_ahead=remind_days)

        # Только те что наступают через remind_days дней или сегодня
        target_deadlines = [
            d for d in deadlines
            if d.get("days_left", 99) in (0, 1, remind_days)
        ]

        if target_deadlines:
            await _send_notifications_for_user(user, settings, target_deadlines)

    log.info(f"✅ Уведомления отправлены {len(users)} пользователям")


async def start_scheduler():
    """Запускает планировщик уведомлений. Добавить в start.py."""
    log.info("📅 Планировщик уведомлений запущен")
    while True:
        now = datetime.now()
        # Отправляем каждый день в 9:00
        if now.hour == 9 and now.minute < 5:
            try:
                await run_daily_notifications()
            except Exception as e:
                log.error(f"Scheduler error: {e}")
        await asyncio.sleep(300)  # Проверяем каждые 5 минут
