# ============================================================
# ДОБАВИТЬ В webapp.py — новые эндпоинты
# ============================================================
# 1. Добавь в импорты:
#    import httpx
#    import asyncio
#    from rag import answer_question  # или как называется функция в rag.py
#
# 2. Добавь CORS (если не добавлен):
#    from fastapi.middleware.cors import CORSMiddleware
#    app.add_middleware(
#        CORSMiddleware,
#        allow_origins=["*"],
#        allow_methods=["*"],
#        allow_headers=["*"],
#    )
# ============================================================

from fastapi import Request
from fastapi.responses import JSONResponse
import httpx
import asyncio
import uuid

# ----------------------------------------------------------
# ПАМЯТЬ ЧАТА (для веб-пользователей)
# ----------------------------------------------------------
web_chat_history = {}  # { user_id: [ {role, content}, ... ] }

def get_web_history(user_id: str) -> list:
    if user_id not in web_chat_history:
        web_chat_history[user_id] = []
    return web_chat_history[user_id]

def add_to_web_history(user_id: str, role: str, content: str):
    history = get_web_history(user_id)
    history.append({"role": role, "content": content})
    # Оставляем только последние 10 обменов (20 сообщений)
    if len(history) > 20:
        web_chat_history[user_id] = history[-20:]

# ----------------------------------------------------------
# POST /api/chat
# Body: { "user_id": "...", "message": "..." }
# Response: { "response": "...", "user_id": "..." }
# ----------------------------------------------------------
@app.post("/api/chat")
async def chat_endpoint(request: Request):
    try:
        data = await request.json()
        message = data.get("message", "").strip()
        user_id = data.get("user_id", str(uuid.uuid4()))

        if not message:
            return JSONResponse({"error": "Сообщение не может быть пустым"}, status_code=400)

        # Добавляем в историю
        add_to_web_history(user_id, "user", message)
        history = get_web_history(user_id)

        # Вызываем RAG систему
        # АДАПТИРУЙ под свой rag.py — возможные варианты:
        # Вариант 1: response = rag.answer_question(message, history)
        # Вариант 2: response = rag.get_answer(message, str(user_id))
        # Вариант 3: rag_system = RAGSystem(); response = rag_system.query(message)

        loop = asyncio.get_event_loop()

        # === ЗАМЕНИ ЭТУ СТРОКУ под свой интерфейс rag.py ===
        response = await loop.run_in_executor(
            None,
            lambda: rag.answer_question(message, history[:-1])  # history без последнего сообщения
        )
        # ====================================================

        # Сохраняем ответ в историю
        add_to_web_history(user_id, "assistant", response)

        return JSONResponse({
            "response": response,
            "user_id": user_id
        })

    except Exception as e:
        return JSONResponse(
            {"error": f"Ошибка сервера: {str(e)}"},
            status_code=500
        )

# ----------------------------------------------------------
# GET /api/rates
# Response: [ { "Ccy": "USD", "Rate": "12845.38", "Diff": "12.5" }, ... ]
# ----------------------------------------------------------
@app.get("/api/rates")
async def get_exchange_rates():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                "https://cbu.uz/uz/arkhiv-kursov-valyut/json/",
                headers={"User-Agent": "BuxgalterAI/1.0"}
            )
            all_rates = resp.json()

        # Фильтруем нужные валюты
        target = {"USD", "EUR", "CNY", "RUB"}
        rates = [
            {
                "code": r["Ccy"],
                "rate": r["Rate"],
                "diff": r.get("Diff", "0"),
                "name": r.get("CcyNm_RU", r["Ccy"])
            }
            for r in all_rates
            if r.get("Ccy") in target
        ]

        # Порядок: USD, EUR, CNY, RUB
        order = ["USD", "EUR", "CNY", "RUB"]
        rates.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)

        return JSONResponse(rates)

    except Exception as e:
        # Фоллбэк — примерные курсы если API недоступен
        return JSONResponse([
            {"code": "USD", "rate": "12845.38", "diff": "0", "name": "Доллар США"},
            {"code": "EUR", "rate": "13910.22", "diff": "0", "name": "Евро"},
            {"code": "CNY", "rate": "1809.45",  "diff": "0", "name": "Юань"},
            {"code": "RUB", "rate": "142.60",   "diff": "0", "name": "Рубль"},
        ])

# ----------------------------------------------------------
# GET /api/user/{user_id}/history
# Response: { "history": [ {role, content}, ... ] }
# ----------------------------------------------------------
@app.get("/api/user/{user_id}/history")
async def get_chat_history(user_id: str):
    history = get_web_history(user_id)
    return JSONResponse({"history": history})

# ----------------------------------------------------------
# DELETE /api/user/{user_id}/history
# Очистить историю чата
# ----------------------------------------------------------
@app.delete("/api/user/{user_id}/history")
async def clear_chat_history(user_id: str):
    web_chat_history.pop(user_id, None)
    return JSONResponse({"status": "cleared"})
