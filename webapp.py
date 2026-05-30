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
