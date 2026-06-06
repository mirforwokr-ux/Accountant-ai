# -*- coding: utf-8 -*-
"""
generate_faq.py — Генерация Q&A пар из существующих статей НК РУз
через Groq API (бесплатный llama-3.3-70b).

Принцип: берём статьи NK/ТК/ГК из ChromaDB → просим LLM сгенерировать
3 вопроса + ответа → добавляем обратно в ChromaDB.

Запуск: python generate_faq.py
Добавит ~800-1200 новых FAQ записей к существующим 4186.
"""

import os, json, time, hashlib, re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

import chromadb
from groq import Groq

# ── Конфиг ──────────────────────────────────────────────────
DB_PATH    = Path(__file__).parent / "buxgalter_db"
PROGRESS   = Path(__file__).parent / "faq_gen_progress.json"
COLLECTION = "buxgalter_uz"

GROQ_KEY   = os.getenv("GROQ_API_KEY", "")

# llama-3.1-8b-instant: 500K TPD (в 5x больше лимит чем 70b)
# llama-3.3-70b-versatile: 100K TPD (качественнее, но меньше лимит)
MODELS = [
    "llama-3.1-8b-instant",       # основной — быстрый, большой лимит
    "llama-3.3-70b-versatile",    # fallback — качественнее
    "llama-3.1-70b-versatile",    # резервный
]

BATCH      = 3      # статей за один запрос (меньше = меньше токенов)
DELAY      = 1.5    # секунды между запросами
MAX_TOKENS = 700    # компактные ответы
MAX_ARTICLE_LEN = 800  # обрезаем длинные статьи

# Статьи каких типов берём для генерации FAQ
SOURCE_PREFIXES = ["nk_", "labor_", "civil_", "koap_", "customs_", "const_"]

# ── Утилиты ─────────────────────────────────────────────────
def mid(text):
    return "faq_gen_" + hashlib.md5(text.encode()).hexdigest()[:12]

def load_prog():
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {"done_ids": [], "added": 0}

def save_prog(p):
    PROGRESS.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

def clean(t):
    return re.sub(r'\s+', ' ', str(t).strip())[:2000]


# ── Промпт для генерации Q&A ─────────────────────────────────
SYSTEM_PROMPT = """Ты — эксперт по налоговому и бухгалтерскому праву Узбекистана.
На основе предоставленного текста закона/кодекса генерируй ровно 3 пары вопрос-ответ.

Правила:
- Вопросы должны быть реальными, которые задают ИП и бухгалтеры
- Ответы — конкретные, со ссылкой на статью
- Используй русский язык
- Формат строго JSON массив:
[
  {"q": "вопрос", "a": "ответ со ссылкой на статью"},
  {"q": "...", "a": "..."},
  {"q": "...", "a": "..."}
]
Только JSON, без лишнего текста."""


def generate_qa(client, articles, model_idx=0):
    """Генерируем Q&A с автоматическим выбором модели и retry при rate limit."""
    if model_idx >= len(MODELS):
        return []

    model = MODELS[model_idx]
    # Обрезаем статьи чтобы экономить токены
    text = "\n\n---\n\n".join(
        f"Фрагмент {i+1}:\n{clean(a)[:MAX_ARTICLE_LEN]}"
        for i, a in enumerate(articles)
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": f"Текст закона:\n{text}\n\nСгенерируй Q&A:"}
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.5,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            return json.loads(match.group())

    except json.JSONDecodeError:
        pass

    except Exception as e:
        err_str = str(e)
        # Дневной лимит токенов (TPD) — ждём указанное время
        if '429' in err_str and 'tokens per day' in err_str:
            # Парсим время ожидания из ошибки
            wait_match = re.search(r'try again in (\d+)m([\d.]+)s', err_str)
            if wait_match:
                wait_sec = int(wait_match.group(1)) * 60 + float(wait_match.group(2))
            else:
                wait_sec = 300
            # Пробуем следующую модель
            next_model = MODELS[model_idx + 1] if model_idx + 1 < len(MODELS) else None
            if next_model:
                print(f"\n    📊 Лимит {model} исчерпан → пробуем {next_model}")
                return generate_qa(client, articles, model_idx + 1)
            else:
                wait_min = int(wait_sec / 60)
                print(f"\n    ⏳ Все модели исчерпаны. Ждём {wait_min} мин до сброса лимита...")
                time.sleep(min(wait_sec, 3600))  # максимум 1 час ожидания
        # Rate limit (RPM) — просто ждём
        elif '429' in err_str:
            print(f"    ⏱️  Rate limit (RPM), ждём 15 сек...")
            time.sleep(15)
            return generate_qa(client, articles, model_idx)  # retry той же моделью
        else:
            print(f"    ⚠️  Ошибка [{model}]: {str(e)[:100]}")
            time.sleep(3)

    return []


def main():
    print("=" * 58)
    print("  Buxgalter AI — Генератор FAQ из статей НК/ТК/ГК")
    print("=" * 58)

    if not GROQ_KEY:
        print("❌  GROQ_API_KEY не задан!")
        print("   Добавь в файл .env: GROQ_API_KEY=gsk_...")
        return

    client = Groq(api_key=GROQ_KEY)
    chroma = chromadb.PersistentClient(path=str(DB_PATH))
    col    = chroma.get_or_create_collection(COLLECTION)

    prog = load_prog()
    before = col.count()
    print(f"  Записей в БД: {before}")
    print(f"  Уже обработано: {len(prog['done_ids'])} статей")

    # ── Загружаем все документы из ChromaDB ─────────────────
    print("\n  Загружаем статьи из ChromaDB...")
    all_docs = col.get(include=["documents"])
    ids   = all_docs["ids"]
    docs  = all_docs["documents"]
    print(f"  Всего статей: {len(ids)}")

    # Фильтруем: только исходные статьи нужных типов, не сгенерированные
    source_pairs = [
        (i, d) for i, d in zip(ids, docs)
        if any(i.startswith(p) for p in SOURCE_PREFIXES)
        and i not in prog["done_ids"]
        and len(d or "") > 150
    ]
    print(f"  К обработке: {len(source_pairs)} статей")

    if not source_pairs:
        print("  ✅ Все статьи уже обработаны!")
        return

    # ── Генерация батчами ────────────────────────────────────
    total_added = 0
    total_batches = (len(source_pairs) + BATCH - 1) // BATCH
    print(f"  Батчей: {total_batches} × {BATCH} статей\n")

    for batch_num in range(0, len(source_pairs), BATCH):
        batch = source_pairs[batch_num:batch_num + BATCH]
        batch_ids   = [x[0] for x in batch]
        batch_texts = [x[1] for x in batch]

        bnum = batch_num // BATCH + 1
        print(f"  [{bnum:3d}/{total_batches}] Генерируем Q&A...", end=" ", flush=True)

        qa_pairs = generate_qa(client, batch_texts, model_idx=0)

        if not qa_pairs:
            print("⚠️  пусто")
            prog["done_ids"].extend(batch_ids)
            save_prog(prog)
            time.sleep(DELAY)
            continue

        # Добавляем в ChromaDB
        added = 0
        for pair in qa_pairs:
            q = clean(pair.get("q", ""))
            a = clean(pair.get("a", ""))
            if len(q) < 10 or len(a) < 20:
                continue
            doc = f"Вопрос: {q}\nОтвет: {a}"
            doc_id = mid(q)
            try:
                col.upsert(ids=[doc_id], documents=[doc])
                added += 1
            except Exception:
                pass

        total_added += added
        prog["done_ids"].extend(batch_ids)
        prog["added"] += added
        save_prog(prog)

        print(f"✅ +{added} Q&A  (итого: {total_added})")
        time.sleep(DELAY)

    # ── Итог ─────────────────────────────────────────────────
    after = col.count()
    print("\n" + "=" * 58)
    print(f"  ИТОГ: добавлено {after - before} FAQ записей")
    print(f"  Всего в БД: {after}")
    print("=" * 58)

    # Обновляем PROJECT_RESUME
    resume = Path(__file__).parent / "PROJECT_RESUME.md"
    if resume.exists():
        txt = resume.read_text(encoding="utf-8")
        txt = re.sub(r"База знаний: \d+ записей", f"База знаний: {after} записей", txt)
        resume.write_text(txt, encoding="utf-8")
        print(f"  PROJECT_RESUME.md → {after} записей")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⚠️  Прерван. Прогресс сохранён — продолжишь с того же места.")
