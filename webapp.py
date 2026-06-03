# -*- coding: utf-8 -*-
"""
FastAPI server — Buxgalter AI Web Platform
"""
import os
import json
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from security import SecurityMiddleware, get_cors_config, validate_chat_message, validate_company_data

app = FastAPI(
    title="Buxgalter AI",
    docs_url=None,   # Отключаем /docs в продакшне
    redoc_url=None,  # Отключаем /redoc
)

# Security middleware — первым
app.add_middleware(SecurityMiddleware)

# CORS — ограниченный список источников
app.add_middleware(CORSMiddleware, **get_cors_config())

# In-memory storage (upgrade to DB later)
USER_DATA: dict = {}

WEBAPP_DIR = Path(__file__).parent / "webapp"


@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = WEBAPP_DIR / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/api/user/{user_id}")
async def get_user(user_id: str):
    data = USER_DATA.get(user_id, {"companies": [], "active": None, "lang": "ru", "requests_left": 10})
    return JSONResponse(data)


@app.post("/api/user/{user_id}/company")
async def add_company(user_id: str, request: Request):
    body = await request.json()
    if user_id not in USER_DATA:
        USER_DATA[user_id] = {"companies": [], "active": None, "lang": "ru", "requests_left": 10}
    companies = USER_DATA[user_id]["companies"]
    # Avoid duplicates by name
    if not any(c["name"] == body.get("name") for c in companies):
        company = {
            "id": str(len(companies) + 1),
            "name": body.get("name", ""),
            "type": body.get("type", "ООО"),
            "tax": body.get("tax", "УСН"),
            "turnover": body.get("turnover", ""),
            "inn": body.get("inn", ""),
        }
        companies.append(company)
        if not USER_DATA[user_id]["active"]:
            USER_DATA[user_id]["active"] = company["id"]
    return JSONResponse({"ok": True, "companies": USER_DATA[user_id]["companies"]})


@app.delete("/api/user/{user_id}/company/{company_id}")
async def delete_company(user_id: str, company_id: str):
    if user_id in USER_DATA:
        USER_DATA[user_id]["companies"] = [
            c for c in USER_DATA[user_id]["companies"] if c["id"] != company_id
        ]
        if USER_DATA[user_id]["active"] == company_id:
            companies = USER_DATA[user_id]["companies"]
            USER_DATA[user_id]["active"] = companies[0]["id"] if companies else None
    return JSONResponse({"ok": True})


@app.post("/api/user/{user_id}/active/{company_id}")
async def set_active(user_id: str, company_id: str):
    if user_id in USER_DATA:
        USER_DATA[user_id]["active"] = company_id
    return JSONResponse({"ok": True})


# ============================================================
# НОВЫЕ ЭНДПОИНТЫ — веб-платформа
# ============================================================
import httpx
import asyncio
import uuid
from rag import ask_buxgalter

# Память чата для веб-пользователей
_web_history: dict = {}

def _get_history(uid: str) -> list:
    return _web_history.setdefault(uid, [])

def _save(uid: str, role: str, content: str):
    h = _get_history(uid)
    h.append({"role": role, "content": content})
    _web_history[uid] = h[-20:]  # последние 10 обменов


@app.post("/api/chat")
async def chat_endpoint(request: Request):
    try:
        data = await request.json()
        message = validate_chat_message(data.get("message", ""))
        user_id = data.get("user_id") or str(uuid.uuid4())
        # Ограничиваем длину user_id
        user_id = str(user_id)[:64]

        _save(user_id, "user", message)
        history = _get_history(user_id)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: ask_buxgalter(message, history[:-1])
        )

        _save(user_id, "assistant", response)
        return JSONResponse({"response": response, "user_id": user_id})

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/rates")
async def get_rates():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                "https://cbu.uz/uz/arkhiv-kursov-valyut/json/",
                headers={"User-Agent": "BuxgalterAI/1.0"}
            )
            all_rates = r.json()
        target = {"USD", "EUR", "CNY", "RUB"}
        order = ["USD", "EUR", "CNY", "RUB"]
        rates = [
            {"code": x["Ccy"], "rate": x["Rate"], "diff": x.get("Diff", "0")}
            for x in all_rates if x.get("Ccy") in target
        ]
        rates.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 9)
        return JSONResponse(rates)
    except Exception:
        return JSONResponse([
            {"code": "USD", "rate": "12845.38", "diff": "0"},
            {"code": "EUR", "rate": "13910.22", "diff": "0"},
            {"code": "CNY", "rate": "1809.45",  "diff": "0"},
            {"code": "RUB", "rate": "142.60",   "diff": "0"},
        ])


@app.get("/api/user/{user_id}/history")
async def get_history(user_id: str):
    return JSONResponse({"history": _get_history(user_id)})


@app.delete("/api/user/{user_id}/history")
async def clear_history(user_id: str):
    _web_history.pop(user_id, None)
    return JSONResponse({"ok": True})


# ============================================================

# ============================================================
# AUTH ROUTES — GitHub OAuth2
# ============================================================
from auth import (
    init_db, get_current_user, get_github_auth_url, exchange_github_code,
    get_or_create_user, create_token, get_user_companies, add_company,
    delete_company, set_active_company, get_user_sessions, get_session_history,
    create_session, save_message as save_msg_db, check_and_increment_requests,
    WEBAPP_URL
)
from fastapi import Depends
from fastapi.responses import RedirectResponse

@app.on_event("startup")
async def startup():
    await init_db()

@app.get("/auth/github")
async def auth_github():
    return RedirectResponse(get_github_auth_url())

# Обратная совместимость — старый Google редирект теперь идёт на GitHub
@app.get("/auth/google")
async def auth_google_compat():
    return RedirectResponse(get_github_auth_url())

@app.get("/auth/callback")
async def auth_callback(code: str = ""):
    if not code:
        return RedirectResponse("/?error=no_code")
    try:
        gh_user = await exchange_github_code(code)
        user = await get_or_create_user(
            github_id=gh_user["id"],
            email=gh_user.get("email", ""),
            name=gh_user.get("name", ""),
            avatar=gh_user.get("avatar", ""),
        )
        token = create_token(user["id"])
        return RedirectResponse(f"/?token={token}")
    except Exception as e:
        return RedirectResponse(f"/?error={str(e)[:60]}")

@app.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return JSONResponse({
        "id": user["id"],
        "name": user["name"],
        "email": user.get("email", ""),
        "avatar": user.get("avatar", ""),
        "plan": user.get("plan", "free"),
    })

@app.post("/auth/logout")
async def auth_logout():
    return JSONResponse({"ok": True})


# ============================================================
# /api/chat/v2 — с авторизацией и историей в БД
# ============================================================
@app.post("/api/chat/v2")
async def chat_v2(request: Request, user: dict = Depends(get_current_user)):
    try:
        data = await request.json()
        message    = validate_chat_message(data.get("message", ""))
        session_id = str(data.get("session_id", user["id"] + "_default"))[:64]

        allowed, remaining = await check_and_increment_requests(user["id"])
        if not allowed:
            return JSONResponse({
                "error": "Дневной лимит 20 запросов исчерпан. Перейдите на PRO.",
                "limit_reached": True
            }, status_code=429)

        await save_msg_db(user["id"], session_id, "user", message)
        history = await get_session_history(user["id"], session_id)

        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ask_buxgalter(message, history[:-1])
        )

        await save_msg_db(user["id"], session_id, "assistant", response)
        await create_session(user["id"], session_id, message[:60])

        return JSONResponse({
            "response": response,
            "session_id": session_id,
            "requests_remaining": remaining,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/chat/sessions")
async def get_sessions(user: dict = Depends(get_current_user)):
    return JSONResponse({"sessions": await get_user_sessions(user["id"])})

@app.get("/api/chat/history/{session_id}")
async def get_history_v2(session_id: str, user: dict = Depends(get_current_user)):
    return JSONResponse({"history": await get_session_history(user["id"], session_id)})


# ============================================================
# /api/user/me — компании с привязкой к аккаунту
# ============================================================
@app.get("/api/user/me/companies")
async def user_companies(user: dict = Depends(get_current_user)):
    companies = await get_user_companies(user["id"])
    active = next((c["id"] for c in companies if c.get("is_active")), None)
    return JSONResponse({"companies": companies, "active_company": active})

@app.post("/api/user/me/company")
async def user_add_company(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    validated = validate_company_data(data)
    co = await add_company(user["id"], validated["name"], validated["tin"],
                           validated["tax_type"], validated["activity"])
    return JSONResponse({"ok": True, "company": co})

@app.delete("/api/user/me/company/{company_id}")
async def user_delete_company(company_id: str, user: dict = Depends(get_current_user)):
    await delete_company(user["id"], company_id)
    return JSONResponse({"ok": True})

@app.post("/api/user/me/active/{company_id}")
async def user_set_active(company_id: str, user: dict = Depends(get_current_user)):
    await set_active_company(user["id"], company_id)
    return JSONResponse({"ok": True})


# ============================================================
# PAYMENTS — Payme, Click, Uzum Bank
# ============================================================
from payments import (
    init_payments_db, create_payment_record, get_user_subscription,
    payme_generate_link, payme_handle_webhook, payme_verify_webhook,
    click_generate_link, click_handle_prepare, click_handle_complete,
    uzum_create_payment, uzum_handle_webhook,
    PLANS, confirm_payment
)

@app.on_event("startup")
async def startup_payments():
    await init_payments_db()


# ── Тарифы ──────────────────────────────────────────────────

@app.get("/api/plans")
async def get_plans():
    """Возвращает доступные тарифные планы."""
    return JSONResponse({
        "plans": [
            {
                "id":       plan_id,
                "name":     plan["name"],
                "price":    plan["price_som"],
                "currency": "UZS",
                "duration": plan["duration"],
                "requests": "Безлимит" if plan["requests"] == -1 else plan["requests"],
            }
            for plan_id, plan in PLANS.items()
        ]
    })


@app.get("/api/subscription")
async def get_subscription(user: dict = Depends(get_current_user)):
    """Статус подписки текущего пользователя."""
    sub = await get_user_subscription(user["id"])
    return JSONResponse({
        "plan":       user.get("plan", "free"),
        "active":     sub is not None,
        "expires_at": sub["expires_at"] if sub else None,
        "days_left":  sub["days_left"] if sub else 0,
    })


# ── Создание платежа ─────────────────────────────────────────

@app.post("/api/payment/create")
async def create_payment(request: Request, user: dict = Depends(get_current_user)):
    """
    Создаёт платёж и возвращает URL для перенаправления.
    Body: { "plan_id": "pro_monthly", "provider": "payme"|"click"|"uzum" }
    """
    data = await request.json()
    plan_id  = str(data.get("plan_id", "pro_monthly"))
    provider = str(data.get("provider", "payme")).lower()

    if plan_id not in PLANS:
        raise HTTPException(status_code=400, detail="Неверный тариф")
    if provider not in ("payme", "click", "uzum"):
        raise HTTPException(status_code=400, detail="Неверный провайдер")

    plan = PLANS[plan_id]

    # Создаём запись платежа в БД
    payment_id = await create_payment_record(
        user_id=user["id"],
        plan_id=plan_id,
        amount=plan["price_uzs"],
        provider=provider
    )

    # Генерируем ссылку на оплату
    if provider == "payme":
        url = payme_generate_link(
            payment_id=payment_id,
            amount_uzs=plan["price_uzs"],
            description=plan["name"],
            user_email=user.get("email", "")
        )
        return JSONResponse({"url": url, "payment_id": payment_id, "provider": "payme"})

    elif provider == "click":
        url = click_generate_link(
            payment_id=payment_id,
            amount_som=plan["price_som"],
            description=plan["name"]
        )
        return JSONResponse({"url": url, "payment_id": payment_id, "provider": "click"})

    elif provider == "uzum":
        result = await uzum_create_payment(
            payment_id=payment_id,
            amount_som=plan["price_som"],
            description=plan["name"]
        )
        if "error" in result:
            raise HTTPException(status_code=502, detail=result["error"])
        return JSONResponse({"url": result["url"], "payment_id": payment_id, "provider": "uzum"})


# ── Webhooks (уведомления от платёжных систем) ───────────────

@app.post("/payment/payme/notify")
async def payme_notify(request: Request):
    """Webhook от Payme — подтверждение оплаты."""
    auth = request.headers.get("Authorization", "")
    if not payme_verify_webhook({}, auth):
        raise HTTPException(status_code=401)
    data = await request.json()
    result = await payme_handle_webhook(data)
    return JSONResponse(result)


@app.post("/payment/click/prepare")
async def click_prepare(request: Request):
    """Click Prepare запрос."""
    data = await request.json()
    result = await click_handle_prepare(data)
    return JSONResponse(result)


@app.post("/payment/click/complete")
async def click_complete(request: Request):
    """Click Complete запрос — подтверждение оплаты."""
    data = await request.json()
    result = await click_handle_complete(data)
    return JSONResponse(result)


@app.post("/payment/uzum/notify")
async def uzum_notify(request: Request):
    """Webhook от Uzum Bank."""
    data = await request.json()
    result = await uzum_handle_webhook(data)
    return JSONResponse(result)


# ── Статус платежа ───────────────────────────────────────────

@app.get("/payment/success")
async def payment_success():
    """Пользователь вернулся после успешной оплаты."""
    html = """<html><head><meta http-equiv="refresh" content="2;url=/"></head>
    <body style="display:flex;align-items:center;justify-content:center;height:100vh;
    font-family:sans-serif;background:#F6F5F9">
    <div style="text-align:center">
        <div style="font-size:64px">✅</div>
        <h2 style="color:#6D28D9">Оплата прошла успешно!</h2>
        <p style="color:#847E92">Перенаправляем вас на платформу...</p>
    </div></body></html>"""
    return HTMLResponse(html)


@app.get("/payment/cancel")
async def payment_cancel():
    """Пользователь отменил оплату."""
    html = """<html><head><meta http-equiv="refresh" content="3;url=/"></head>
    <body style="display:flex;align-items:center;justify-content:center;height:100vh;
    font-family:sans-serif;background:#F6F5F9">
    <div style="text-align:center">
        <div style="font-size:64px">❌</div>
        <h2 style="color:#DC4A4A">Оплата отменена</h2>
        <p style="color:#847E92">Возвращаем вас назад...</p>
    </div></body></html>"""
    return HTMLResponse(html)


# ── GDPR / Личные данные ─────────────────────────────────────

@app.delete("/api/user/me/delete")
async def delete_account(user: dict = Depends(get_current_user)):
    """
    Полное удаление аккаунта и всех данных пользователя.
    Требование GDPR и Закона РУз о персональных данных.
    """
    import aiosqlite as _aio
    async with _aio.connect("users.db") as db:
        uid = user["id"]
        await db.execute("DELETE FROM chat_messages WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM chat_sessions WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM companies WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM subscriptions WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM payments WHERE user_id=?", (uid,))
        await db.execute("DELETE FROM users WHERE id=?", (uid,))
        await db.commit()
    return JSONResponse({"ok": True, "message": "Аккаунт и все данные удалены"})


@app.get("/api/user/me/data-export")
async def export_user_data(user: dict = Depends(get_current_user)):
    """
    Экспорт всех данных пользователя (право на переносимость данных).
    """
    import aiosqlite as _aio
    async with _aio.connect("users.db") as db:
        db.row_factory = _aio.Row
        uid = user["id"]
        msgs = await (await db.execute(
            "SELECT role,content,created_at FROM chat_messages WHERE user_id=? ORDER BY created_at",
            (uid,))).fetchall()
        companies = await (await db.execute(
            "SELECT name,tin,tax_type,activity FROM companies WHERE user_id=?",
            (uid,))).fetchall()
        payments_list = await (await db.execute(
            "SELECT plan_id,amount,provider,status,created_at,paid_at FROM payments WHERE user_id=?",
            (uid,))).fetchall()

    return JSONResponse({
        "user": {
            "id":         user["id"],
            "email":      user.get("email"),
            "name":       user.get("name"),
            "plan":       user.get("plan"),
            "created_at": user.get("created_at"),
        },
        "companies":  [dict(r) for r in companies],
        "payments":   [dict(r) for r in payments_list],
        "chat_count": len(msgs),
        "export_date": time.time(),
    })


# ============================================================
# NOTIFICATIONS — Email, SMS, Push, Calendar
# ============================================================
import time as _time
from notifications import (
    init_notifications_db, get_notification_settings, save_notification_settings,
    save_push_subscription, send_web_push, add_calendar_event,
    get_user_events, delete_calendar_event, get_upcoming_deadlines,
    run_daily_notifications, start_scheduler, VAPID_PUBLIC
)

@app.on_event("startup")
async def startup_notifications():
    await init_notifications_db()
    # Запускаем планировщик в фоне
    asyncio.create_task(start_scheduler())


# ── Настройки уведомлений ────────────────────────────────────

@app.get("/api/notifications/settings")
async def get_notif_settings(user: dict = Depends(get_current_user)):
    settings = await get_notification_settings(user["id"])
    return JSONResponse(settings)

@app.post("/api/notifications/settings")
async def update_notif_settings(request: Request, user: dict = Depends(get_current_user)):
    data = await request.json()
    # Валидация телефона
    phone = str(data.get("phone", "")).strip()
    if phone and not any(c.isdigit() for c in phone):
        raise HTTPException(400, "Некорректный номер телефона")
    await save_notification_settings(user["id"], data)
    return JSONResponse({"ok": True})


# ── Web Push ──────────────────────────────────────────────────

@app.get("/api/notifications/vapid-key")
async def get_vapid_key():
    """Возвращает публичный VAPID ключ для браузерного Push."""
    return JSONResponse({"publicKey": VAPID_PUBLIC})

@app.post("/api/notifications/subscribe")
async def push_subscribe(request: Request, user: dict = Depends(get_current_user)):
    """Сохраняет Push подписку браузера."""
    subscription = await request.json()
    ok = await save_push_subscription(user["id"], subscription)
    return JSONResponse({"ok": ok})

@app.post("/api/notifications/test-push")
async def test_push(user: dict = Depends(get_current_user)):
    """Отправляет тестовое Push уведомление."""
    sent = await send_web_push(
        user["id"],
        title="✅ Buxgalter AI",
        body="Push уведомления работают! Вы будете получать напоминания о сроках.",
    )
    return JSONResponse({"ok": sent > 0, "sent": sent})


# ── Налоговый календарь ───────────────────────────────────────

@app.get("/api/calendar/deadlines")
async def get_deadlines(days: int = 30, user: dict = Depends(get_current_user)):
    """Возвращает ближайшие налоговые дедлайны."""
    deadlines = get_upcoming_deadlines(days_ahead=min(days, 90))
    return JSONResponse({"deadlines": deadlines})

@app.get("/api/calendar/events")
async def get_events(user: dict = Depends(get_current_user)):
    """Пользовательские события + налоговые дедлайны на 60 дней."""
    user_events = await get_user_events(user["id"])
    tax_deadlines = get_upcoming_deadlines(days_ahead=60)
    return JSONResponse({
        "user_events": user_events,
        "tax_deadlines": tax_deadlines,
    })

@app.post("/api/calendar/events")
async def create_event(request: Request, user: dict = Depends(get_current_user)):
    """Создаёт пользовательское событие."""
    data = await request.json()
    title = str(data.get("title", "")).strip()[:200]
    date  = str(data.get("date", "")).strip()
    desc  = str(data.get("description", "")).strip()[:500]
    if not title or not date:
        raise HTTPException(400, "Название и дата обязательны")
    event_id = await add_calendar_event(user["id"], title, date, desc)
    return JSONResponse({"ok": True, "event_id": event_id})

@app.delete("/api/calendar/events/{event_id}")
async def remove_event(event_id: str, user: dict = Depends(get_current_user)):
    await delete_calendar_event(user["id"], event_id)
    return JSONResponse({"ok": True})


# ── Ручной запуск уведомлений (для тестирования) ─────────────

@app.post("/api/notifications/send-now")
async def send_notifications_now(user: dict = Depends(get_current_user)):
    """Немедленно отправляет уведомления текущему пользователю (для теста)."""
    from notifications import _send_notifications_for_user
    settings  = await get_notification_settings(user["id"])
    deadlines = get_upcoming_deadlines(days_ahead=30)
    if deadlines:
        await _send_notifications_for_user(user, settings, deadlines[:5])
    return JSONResponse({
        "ok":       True,
        "sent_to":  [ch for ch in ["email","sms","push","telegram"]
                     if settings.get(f"{ch}_enabled")],
        "deadlines": len(deadlines),
    })
