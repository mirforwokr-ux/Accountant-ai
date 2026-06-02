# -*- coding: utf-8 -*-
"""
security.py — Защита Buxgalter AI
===================================
• Rate limiting (IP + User)
• Security headers (HSTS, CSP, XSS, etc.)
• Input validation
• DDoS protection
• Suspicious activity logging
• Request size limits
"""

import os
import re
import time
import logging
import ipaddress
from collections import defaultdict
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("security")

# ── Конфигурация ───────────────────────────────────────────────
ALLOWED_ORIGINS = [
    "https://accountant-ai-production-b952.up.railway.app",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

# Rate limiting настройки
RATE_LIMITS = {
    "/api/chat":        {"requests": 30,  "window": 60},   # 30 req/мин на IP
    "/api/chat/v2":     {"requests": 30,  "window": 60},
    "/auth/callback":   {"requests": 10,  "window": 60},   # 10 попыток входа/мин
    "/auth/github":     {"requests": 20,  "window": 60},
    "/api/rates":       {"requests": 60,  "window": 60},
    "default":          {"requests": 100, "window": 60},   # глобальный лимит
}

# Максимальный размер тела запроса
MAX_REQUEST_SIZE = 50 * 1024  # 50 KB

# Время блокировки IP (секунды)
BLOCK_DURATION = 300  # 5 минут

# Подозрительные паттерны в запросах
SUSPICIOUS_PATTERNS = [
    r'<script[^>]*>',            # XSS
    r'javascript:',              # XSS
    r'on\w+\s*=',               # XSS events
    r"union\s+select",          # SQL injection
    r"drop\s+table",            # SQL injection
    r"insert\s+into",           # SQL injection
    r"exec\s*\(",               # Code injection
    r"\.\./\.\./",              # Path traversal
    r"etc/passwd",              # Path traversal
    r"cmd\.exe",                # RCE
    r"/bin/bash",               # RCE
]
SUSPICIOUS_RE = re.compile("|".join(SUSPICIOUS_PATTERNS), re.IGNORECASE)

# Хранилище для rate limiting (in-memory, reset при перезапуске)
_rate_data:  dict = defaultdict(list)    # {ip: [timestamps]}
_blocked_ips: dict = {}                  # {ip: unblock_time}
_failed_auth: dict = defaultdict(list)   # {ip: [timestamps]}


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def get_client_ip(request: Request) -> str:
    """Получает реальный IP с учётом прокси/Railway."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    return ip


def is_private_ip(ip: str) -> bool:
    """Проверяет, является ли IP приватным (localhost, Railway internal)."""
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def is_blocked(ip: str) -> bool:
    """Проверяет, заблокирован ли IP."""
    if ip in _blocked_ips:
        if time.time() < _blocked_ips[ip]:
            return True
        else:
            del _blocked_ips[ip]
    return False


def block_ip(ip: str, reason: str = ""):
    """Блокирует IP на BLOCK_DURATION секунд."""
    _blocked_ips[ip] = time.time() + BLOCK_DURATION
    log.warning(f"🔒 IP заблокирован: {ip} | Причина: {reason}")


def check_rate_limit(ip: str, path: str) -> bool:
    """
    Возвращает True если запрос разрешён, False если превышен лимит.
    Использует sliding window алгоритм.
    """
    if is_private_ip(ip):
        return True

    # Определяем лимит для пути
    limit_cfg = RATE_LIMITS.get(path, RATE_LIMITS["default"])
    max_requests = limit_cfg["requests"]
    window       = limit_cfg["window"]

    key  = f"{ip}:{path}"
    now  = time.time()
    cutoff = now - window

    # Очищаем устаревшие записи
    _rate_data[key] = [t for t in _rate_data[key] if t > cutoff]

    if len(_rate_data[key]) >= max_requests:
        # Автоблокировка при многократном превышении
        if len(_rate_data[key]) >= max_requests * 3:
            block_ip(ip, f"rate limit exceeded on {path}")
        return False

    _rate_data[key].append(now)
    return True


def detect_attack(body: str, ip: str) -> bool:
    """Детектирует атаки в теле запроса. True = атака обнаружена."""
    if SUSPICIOUS_RE.search(body):
        log.warning(f"⚠️ Подозрительный запрос от {ip}: {body[:100]}")
        # Не блокируем сразу — просто логируем
        return True
    return False


def record_failed_auth(ip: str):
    """Записывает неудачную попытку входа и блокирует при превышении."""
    now = time.time()
    _failed_auth[ip] = [t for t in _failed_auth[ip] if t > now - 300]
    _failed_auth[ip].append(now)
    if len(_failed_auth[ip]) >= 10:
        block_ip(ip, "too many failed auth attempts")


# ════════════════════════════════════════════════════════════
# MIDDLEWARE
# ════════════════════════════════════════════════════════════

class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Основной middleware безопасности:
    - Блокировка IP
    - Rate limiting
    - Размер запроса
    - Security headers
    """

    async def dispatch(self, request: Request, call_next):
        ip   = get_client_ip(request)
        path = request.url.path

        # 1. Проверка заблокированного IP
        if is_blocked(ip) and not is_private_ip(ip):
            log.warning(f"🚫 Заблокированный IP: {ip} → {path}")
            return JSONResponse(
                {"error": "Доступ временно ограничен. Попробуйте позже."},
                status_code=429
            )

        # 2. Rate limiting
        if not check_rate_limit(ip, path):
            log.warning(f"⏱️ Rate limit: {ip} → {path}")
            return JSONResponse(
                {"error": "Слишком много запросов. Подождите немного."},
                status_code=429,
                headers={"Retry-After": "60"}
            )

        # 3. Проверка размера запроса
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_REQUEST_SIZE:
            log.warning(f"📦 Слишком большой запрос: {ip} → {path} ({content_length} bytes)")
            return JSONResponse(
                {"error": "Запрос слишком большой."},
                status_code=413
            )

        # 4. Проверка тела на атаки (только для POST/PUT)
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.body()
                body_str = body.decode("utf-8", errors="ignore")
                if detect_attack(body_str, ip):
                    # Не блокируем, но логируем — могут быть ложные срабатывания
                    pass
            except Exception:
                pass

        # 5. Обрабатываем запрос
        response = await call_next(request)

        # 6. Добавляем security headers
        response.headers["X-Content-Type-Options"]   = "nosniff"
        response.headers["X-Frame-Options"]           = "DENY"
        response.headers["X-XSS-Protection"]          = "1; mode=block"
        response.headers["Referrer-Policy"]            = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]         = "geolocation=(), microphone=(), camera=()"
        response.headers["Strict-Transport-Security"]  = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"]    = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https:; "
            "connect-src 'self' https://accountant-ai-production-b952.up.railway.app "
            "https://cbu.uz https://github.com https://api.github.com;"
        )
        # Убираем лишнюю информацию о сервере
        response.headers.pop("server", None)
        response.headers.pop("x-powered-by", None)

        return response


# ════════════════════════════════════════════════════════════
# ВАЛИДАЦИЯ ВХОДНЫХ ДАННЫХ
# ════════════════════════════════════════════════════════════

MAX_MESSAGE_LEN  = 2000   # символов
MAX_NAME_LEN     = 100
MAX_TIN_LEN      = 20
MAX_ACTIVITY_LEN = 200


def validate_chat_message(message: str) -> str:
    """Валидирует и очищает сообщение чата."""
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    message = message.strip()

    if len(message) > MAX_MESSAGE_LEN:
        raise HTTPException(
            status_code=400,
            detail=f"Сообщение слишком длинное (макс. {MAX_MESSAGE_LEN} символов)"
        )

    # Убираем нулевые байты и управляющие символы
    message = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', message)

    return message


def validate_company_data(data: dict) -> dict:
    """Валидирует данные компании."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="Название компании обязательно")
    if len(name) > MAX_NAME_LEN:
        raise HTTPException(status_code=400, detail="Название слишком длинное")

    tin = str(data.get("tin", "")).strip()
    if tin and not re.match(r'^[\d\s\-]+$', tin):
        raise HTTPException(status_code=400, detail="Некорректный формат ИНН")
    if len(tin) > MAX_TIN_LEN:
        raise HTTPException(status_code=400, detail="ИНН слишком длинный")

    tax_type = str(data.get("tax_type", "УСН")).strip()
    allowed_tax = {"УСН", "ОСН", "НДС", "ИП", "GNI", "ОСН+НДС"}
    if tax_type not in allowed_tax:
        tax_type = "УСН"

    activity = str(data.get("activity", "")).strip()
    if len(activity) > MAX_ACTIVITY_LEN:
        activity = activity[:MAX_ACTIVITY_LEN]

    # Очищаем от HTML
    for field in [name, tin, activity]:
        if SUSPICIOUS_RE.search(field):
            raise HTTPException(status_code=400, detail="Недопустимые символы в данных")

    return {
        "name":     name,
        "tin":      tin,
        "tax_type": tax_type,
        "activity": activity,
    }


def validate_jwt_token(token: str) -> str:
    """Базовая валидация формата JWT перед декодированием."""
    if not token or len(token) > 2000:
        raise HTTPException(status_code=401, detail="Некорректный токен")
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Некорректный формат токена")
    return token


# ════════════════════════════════════════════════════════════
# CORS — ограниченный список источников
# ════════════════════════════════════════════════════════════

def get_cors_config() -> dict:
    """Возвращает конфигурацию CORS."""
    env = os.getenv("ENVIRONMENT", "production")

    if env == "development":
        origins = ["*"]  # В разработке — всё разрешено
    else:
        origins = ALLOWED_ORIGINS

    return {
        "allow_origins":     origins,
        "allow_credentials": True,
        "allow_methods":     ["GET", "POST", "DELETE", "PUT"],
        "allow_headers":     ["Content-Type", "Authorization", "X-Requested-With"],
        "expose_headers":    ["X-Request-ID"],
        "max_age":           3600,
    }
