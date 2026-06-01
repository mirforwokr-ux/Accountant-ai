# Buxgalter AI — Резюме проекта

## Что это
AI-эксперт по бухгалтерии и налогам Узбекистана. Telegram бот + веб-платформа.
Цель: платный SaaS для ИП, малого бизнеса, частных лиц. Freemium модель.

## Статус
- ✅ Telegram бот работает 24/7 на Railway
- ✅ FastAPI веб-сервер запущен (Mini App доступен в браузере)
- ✅ База знаний: 383 записи (373 статьи НК РУз + FAQ)
- ✅ Kalkulyatorlar: НДС, зарплата, налог на прибыль, УСН
- ✅ Память разговора (10 последних обменов)
- ✅ Два языка: русский / узбекский
- ✅ Экспертный промпт (анализ + риски + рекомендации)
- ⏳ Mini App в Telegram: работает в браузере, нужно зарегистрировать в BotFather
- ❌ Веб-платформа: дизайн готов в Figma, фронтенд не подключён к бэкенду

## Ключи и токены
```
TELEGRAM_BOT_TOKEN=<задан в Railway environment variables>
GROQ_API_KEY=<задан в Railway environment variables>
WEBAPP_URL=https://accountant-ai-production-b952.up.railway.app
```

## Файлы — C:\Users\L-tech\buxgalter ai\
```
bot.py          — Telegram бот (python-telegram-bot 21.9)
rag.py          — RAG система (Groq llama-3.3-70b + ChromaDB)
webapp.py       — FastAPI сервер (Mini App API)
start.py        — Единая точка запуска (бот + веб в одном процессе)
seed.py         — Загрузка базы знаний (45 тематических Q&A)
parse_nk.py     — Парсер PDF Налогового кодекса РУз
webapp/index.html — Mini App HTML (компании, калькуляторы, профиль)
requirements.txt
buxgalter_db/   — ChromaDB база данных (383 записи)
```

## GitHub
```
https://github.com/mirforwokr-ux/Accountant-ai
branch: main
```

## Railway
```
Проект: pleasing-comfort
Сервис: Accountant-ai
Домен: accountant-ai-production-b952.up.railway.app
Start Command: python start.py
```

## Стек
- Python 3.13
- python-telegram-bot 21.9
- Groq API (llama-3.3-70b-versatile) — бесплатно
- ChromaDB + DefaultEmbeddingFunction
- FastAPI + uvicorn
- Railway (деплой, бесплатный tier)

## База знаний — структура
| Уровень | Что есть | Что нужно |
|---------|----------|-----------|
| 1. Справочник | ✅ 373 статьи НК РУз | Готово |
| 2. FAQ | ✅ 45 вопросов | Нужно 500+ |
| 3. Регламенты | ❌ | 50+ пошаговых инструкций |
| 4. Кейсы | ❌ | 200+ реальных ситуаций |
| 5. Ошибки→Решения | ❌ | 100+ проблем |
| 6. Шаблоны документов | ❌ | Письма в ГНК, жалобы |
| 7. Лазейки | ❌ | Законная оптимизация |
| 8. Судебная практика | ❌ | Прецеденты РУз |
| 9. Профиль пользователя | ⏳ | В коде, нужен UI |

## Следующие шаги (приоритет)
1. **BotFather** → зарегистрировать Mini App URL (accountant-ai-production-b952.up.railway.app)
2. **Веб-платформа** → взять Figma дизайн → создать HTML/React фронтенд → подключить к FastAPI
3. **База знаний** → добавить уровни 3-5 (регламенты, кейсы, ошибки)
4. **Монетизация** → Payme/Click интеграция, счётчик запросов, подписки

## API эндпоинты (webapp.py)
```
GET  /                          → Mini App HTML
GET  /api/user/{user_id}        → данные пользователя
POST /api/user/{user_id}/company → добавить компанию
DEL  /api/user/{user_id}/company/{id} → удалить компанию
POST /api/user/{user_id}/active/{id}  → сменить активную компанию
```

## Дизайн (Figma / Claude Design)
Готов UI платформы "AI Accountant Uzbekistan":
- Dashboard с карточками компаний (Family Berries LLC/Export/Trade)
- My Companies с TIN, статусом VAT, compliance
- Document Review, Knowledge Base, Calendar, Notes
- Тикер валют USD/EUR/CNY/RUB вверху

## Для нового чата — вставить это:
"Продолжаем проект Buxgalter AI (AI-эксперт по налогам Узбекистана).
Бот @buxgalter_ai_uzbot работает на Railway.
Стек: Python + Groq + ChromaDB + FastAPI + Telegram Bot.
Файлы: C:\Users\L-tech\buxgalter ai\
GitHub: mirforwokr-ux/Accountant-ai
Следующая задача: создать веб-платформу — подключить Figma дизайн к FastAPI бэкенду."
