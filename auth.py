# -*- coding: utf-8 -*-
"""
auth.py — GitHub OAuth2 + JWT + SQLite
Buxgalter AI Web Platform
"""
import os
import uuid
import time
import aiosqlite
import httpx
from jose import jwt, JWTError
from fastapi import HTTPException, Request

# ── Config ───────────────────────────────────────────────────
GITHUB_CLIENT_ID     = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
WEBAPP_URL           = os.getenv("WEBAPP_URL", "https://accountant-ai-production-b952.up.railway.app")
JWT_SECRET           = os.getenv("JWT_SECRET", "buxgalter-secret-" + uuid.uuid4().hex)
JWT_ALGORITHM        = "HS256"
JWT_EXPIRE_DAYS      = 30
DB_PATH              = "users.db"

GITHUB_AUTH_URL    = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL   = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL    = "https://api.github.com/user"
GITHUB_EMAIL_URL   = "https://api.github.com/user/emails"
REDIRECT_URI       = f"{WEBAPP_URL}/auth/callback"
FREE_DAILY_LIMIT   = 20


# ════════════════════════════════════════════════════════════
# DATABASE
# ════════════════════════════════════════════════════════════

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id                  TEXT PRIMARY KEY,
                github_id           TEXT UNIQUE,
                email               TEXT,
                name                TEXT,
                avatar              TEXT,
                plan                TEXT DEFAULT 'free',
                requests_today      INTEGER DEFAULT 0,
                last_request_date   TEXT DEFAULT '',
                created_at          REAL,
                last_login          REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT,
                session_id  TEXT UNIQUE,
                title       TEXT,
                created_at  REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT,
                session_id  TEXT,
                role        TEXT,
                content     TEXT,
                created_at  REAL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id          TEXT PRIMARY KEY,
                user_id     TEXT,
                name        TEXT,
                tin         TEXT,
                tax_type    TEXT,
                activity    TEXT,
                is_active   INTEGER DEFAULT 0,
                created_at  REAL
            )
        """)
        await db.commit()


async def get_or_create_user(github_id: str, email: str, name: str, avatar: str) -> dict:
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE github_id=?", (github_id,))
        user = await cur.fetchone()
        if user:
            await db.execute(
                "UPDATE users SET last_login=?,name=?,avatar=?,email=? WHERE github_id=?",
                (now, name, avatar, email, github_id)
            )
            await db.commit()
            cur2 = await db.execute("SELECT * FROM users WHERE github_id=?", (github_id,))
            return dict(await cur2.fetchone())
        uid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO users (id,github_id,email,name,avatar,created_at,last_login) VALUES (?,?,?,?,?,?,?)",
            (uid, github_id, email, name, avatar, now, now)
        )
        await db.commit()
        return {"id": uid, "github_id": github_id, "email": email,
                "name": name, "avatar": avatar, "plan": "free", "requests_today": 0}


async def get_user_by_id(user_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


# ════════════════════════════════════════════════════════════
# CHAT HISTORY
# ════════════════════════════════════════════════════════════

async def save_message(user_id: str, session_id: str, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO chat_messages (user_id,session_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (user_id, session_id, role, content, time.time())
        )
        await db.commit()


async def get_session_history(user_id: str, session_id: str, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role,content FROM chat_messages WHERE user_id=? AND session_id=? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, session_id, limit)
        )
        rows = await cur.fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def get_user_sessions(user_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT session_id, title, created_at FROM chat_sessions "
            "WHERE user_id=? ORDER BY created_at DESC LIMIT 30",
            (user_id,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def create_session(user_id: str, session_id: str, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chat_sessions (user_id,session_id,title,created_at) VALUES (?,?,?,?)",
            (user_id, session_id, title, time.time())
        )
        await db.commit()


# ════════════════════════════════════════════════════════════
# COMPANIES
# ════════════════════════════════════════════════════════════

async def get_user_companies(user_id: str) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM companies WHERE user_id=? ORDER BY created_at", (user_id,))
        return [dict(r) for r in await cur.fetchall()]


async def add_company(user_id: str, name: str, tin: str, tax_type: str, activity: str) -> dict:
    cid = str(uuid.uuid4())
    now = time.time()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM companies WHERE user_id=?", (user_id,))
        count = (await cur.fetchone())[0]
        is_active = 1 if count == 0 else 0
        await db.execute(
            "INSERT INTO companies (id,user_id,name,tin,tax_type,activity,is_active,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (cid, user_id, name, tin, tax_type, activity, is_active, now)
        )
        await db.commit()
    return {"id": cid, "name": name, "tin": tin, "tax_type": tax_type, "activity": activity, "is_active": is_active}


async def delete_company(user_id: str, company_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM companies WHERE id=? AND user_id=?", (company_id, user_id))
        await db.commit()


async def set_active_company(user_id: str, company_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE companies SET is_active=0 WHERE user_id=?", (user_id,))
        await db.execute("UPDATE companies SET is_active=1 WHERE id=? AND user_id=?", (company_id, user_id))
        await db.commit()


# ════════════════════════════════════════════════════════════
# JWT
# ════════════════════════════════════════════════════════════

def create_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "iat": time.time(), "exp": time.time() + JWT_EXPIRE_DAYS * 86400},
        JWT_SECRET, algorithm=JWT_ALGORITHM
    )


def verify_token(token: str) -> str:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Токен недействителен или истёк")


async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else request.cookies.get("token", "")
    if not token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    user_id = verify_token(token)
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")
    return user


# ════════════════════════════════════════════════════════════
# GITHUB OAUTH FLOW
# ════════════════════════════════════════════════════════════

def get_github_auth_url() -> str:
    return (
        f"{GITHUB_AUTH_URL}"
        f"?client_id={GITHUB_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope=user:email"
    )


async def exchange_github_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Получаем access token
        token_resp = await client.post(
            GITHUB_TOKEN_URL,
            data={"client_id": GITHUB_CLIENT_ID, "client_secret": GITHUB_CLIENT_SECRET,
                  "code": code, "redirect_uri": REDIRECT_URI},
            headers={"Accept": "application/json"}
        )
        tokens = token_resp.json()
        access_token = tokens.get("access_token", "")
        if not access_token:
            raise HTTPException(status_code=400, detail="GitHub OAuth ошибка")

        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

        # Профиль пользователя
        user_resp = await client.get(GITHUB_USER_URL, headers=headers)
        user_data = user_resp.json()

        # Email (может быть приватным)
        email = user_data.get("email", "")
        if not email:
            email_resp = await client.get(GITHUB_EMAIL_URL, headers=headers)
            emails = email_resp.json()
            primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
            email = primary["email"] if primary else ""

    return {
        "id": str(user_data["id"]),
        "name": user_data.get("name") or user_data.get("login", ""),
        "email": email,
        "avatar": user_data.get("avatar_url", ""),
    }


# ════════════════════════════════════════════════════════════
# RATE LIMITING
# ════════════════════════════════════════════════════════════

async def check_and_increment_requests(user_id: str) -> tuple[bool, int]:
    today = time.strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT plan,requests_today,last_request_date FROM users WHERE id=?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False, 0
        if row["plan"] != "free":
            return True, 999
        count = row["requests_today"] if row["last_request_date"] == today else 0
        if count >= FREE_DAILY_LIMIT:
            return False, 0
        new_count = count + 1
        await db.execute(
            "UPDATE users SET requests_today=?,last_request_date=? WHERE id=?",
            (new_count, today, user_id)
        )
        await db.commit()
        return True, FREE_DAILY_LIMIT - new_count
