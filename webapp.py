# -*- coding: utf-8 -*-
"""
FastAPI server — serves Telegram Mini App
"""
import os
import json
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        message = data.get("message", "").strip()
        user_id = data.get("user_id") or str(uuid.uuid4())
        if not message:
            return JSONResponse({"error": "Пустое сообщение"}, status_code=400)

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
        message = data.get("message", "").strip()
        session_id = data.get("session_id", user["id"] + "_default")
        if not message:
            return JSONResponse({"error": "Пустое сообщение"}, status_code=400)

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
    co = await add_company(user["id"], data.get("name",""), data.get("tin",""),
                           data.get("tax_type","УСН"), data.get("activity",""))
    return JSONResponse({"ok": True, "company": co})

@app.delete("/api/user/me/company/{company_id}")
async def user_delete_company(company_id: str, user: dict = Depends(get_current_user)):
    await delete_company(user["id"], company_id)
    return JSONResponse({"ok": True})

@app.post("/api/user/me/active/{company_id}")
async def user_set_active(company_id: str, user: dict = Depends(get_current_user)):
    await set_active_company(user["id"], company_id)
    return JSONResponse({"ok": True})
