# -*- coding: utf-8 -*-
"""
parse_lex_uz.py — Парсер статей НК РУз с lex.uz (чистый HTML, без OCR мусора)

Использует Selenium потому что lex.uz рендерится через JavaScript.

Запуск:
    python parse_lex_uz.py

Что делает:
    1. Открывает lex.uz/ru/docs/5014879 (НК РУз) через Selenium
    2. Находит все статьи (Статья 1, Статья 2, ...)
    3. Извлекает чистый текст каждой статьи
    4. Добавляет в ChromaDB (buxgalter_db/)

Требования:
    pip install selenium chromadb python-dotenv
    ChromeDriver уже установлен (install_selenium.bat)
"""
import os
import re
import time
import hashlib
import chromadb
from pathlib import Path
from dotenv import load_dotenv
from chromadb.utils import embedding_functions

load_dotenv()

# ── Конфиг ──────────────────────────────────────────────────────────────────
_here    = Path(__file__).parent
DB_PATH  = _here / "buxgalter_db"
LEX_URL  = "https://lex.uz/ru/docs/5014879"  # НК РУз

COLLECTION_NAME = "buxgalter_uz"

# ── Selenium ─────────────────────────────────────────────────────────────────
def get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=ru-RU")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver


def fetch_nk_articles(driver) -> list[dict]:
    """Открывает lex.uz и извлекает статьи НК РУз."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    print(f"Открываю {LEX_URL} ...")
    driver.get(LEX_URL)

    # Ждём загрузки основного контента
    wait = WebDriverWait(driver, 30)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "article, .document-content, .lex-content, #content")))
        print("Страница загружена.")
    except Exception:
        print("Таймаут ожидания — попробуем с тем, что есть.")

    time.sleep(3)  # дополнительное ожидание рендера

    # Получаем весь текст страницы
    full_text = driver.find_element(By.TAG_NAME, "body").text
    print(f"Получено символов: {len(full_text):,}")

    # Парсим статьи по паттерну "Статья N."
    articles = parse_articles_from_text(full_text)
    print(f"Найдено статей: {len(articles)}")
    return articles


def parse_articles_from_text(text: str) -> list[dict]:
    """Разбивает текст на статьи по маркеру 'Статья N.'"""
    # Нормализуем пробелы
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Паттерн для начала статьи
    pattern = re.compile(r'(?:^|\n)(Статья\s+(\d+)\.\s)', re.MULTILINE)
    matches = list(pattern.finditer(text))

    articles = []
    for i, match in enumerate(matches):
        num   = int(match.group(2))
        start = match.start(1)
        end   = matches[i + 1].start(1) if i + 1 < len(matches) else len(text)

        article_text = text[start:end].strip()

        # Заголовок = первая строка после "Статья N."
        lines = article_text.split('\n')
        title = ""
        if len(lines) > 1:
            title = lines[1].strip()[:120]

        # Обрезаем до 1500 символов (достаточно для embeddings)
        body = re.sub(r'\s+', ' ', article_text[:1500]).strip()

        if len(body) < 50:
            continue

        articles.append({
            "num":   num,
            "title": title,
            "text":  f"НК РУз Статья {num}. {title}\n{body}",
            "id":    f"nk_art_{num}",
        })

    return articles


# ── ChromaDB ─────────────────────────────────────────────────────────────────
def get_collection():
    client = chromadb.PersistentClient(path=str(DB_PATH))
    ef     = embedding_functions.DefaultEmbeddingFunction()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
    )


def remove_old_nk_articles(col):
    """Удаляет старые статьи НК перед добавлением новых."""
    try:
        # Получаем все ID которые начинаются с nk_art_
        existing = col.get(where={"source": "nk"})
        if existing and existing.get("ids"):
            col.delete(ids=existing["ids"])
            print(f"Удалено старых статей НК: {len(existing['ids'])}")
    except Exception:
        # Fallback: удаляем по ID напрямую
        ids_to_delete = [f"nk_art_{i}" for i in range(1, 600)]
        existing_ids  = [id_ for id_ in ids_to_delete if _id_exists(col, id_)]
        if existing_ids:
            col.delete(ids=existing_ids)
            print(f"Удалено старых статей НК: {len(existing_ids)}")


def _id_exists(col, id_: str) -> bool:
    try:
        r = col.get(ids=[id_])
        return bool(r and r.get("ids"))
    except Exception:
        return False


def seed_articles(articles: list[dict], col):
    """Добавляет статьи в ChromaDB батчами."""
    batch_size = 50
    added = 0

    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        col.upsert(
            ids       =[a["id"]   for a in batch],
            documents =[a["text"] for a in batch],
            metadatas =[{"source": "nk", "article_num": a["num"], "title": a["title"]} for a in batch],
        )
        added += len(batch)
        print(f"  Добавлено: {added}/{len(articles)}")

    return added


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  parse_lex_uz.py — Парсер НК РУз с lex.uz")
    print("=" * 60)

    driver = get_driver()
    try:
        articles = fetch_nk_articles(driver)
    finally:
        driver.quit()

    if not articles:
        print("\n❌ Статьи не найдены.")
        print("   Возможно lex.uz изменил структуру. Проверьте вручную.")
        return

    print(f"\nПримеры найденных статей:")
    for a in articles[:3]:
        print(f"  Статья {a['num']}: {a['title'][:60]}")

    col = get_collection()
    print(f"\nТекущий размер БД: {col.count()} записей")

    # Удаляем старые мусорные статьи НК
    remove_old_nk_articles(col)

    # Добавляем новые чистые
    added = seed_articles(articles, col)

    print(f"\n✅ Готово!")
    print(f"   Добавлено статей НК: {added}")
    print(f"   Итого в БД: {col.count()} записей")
    print(f"\nСледующий шаг:")
    print("   git add buxgalter_db/")
    print("   git commit -m 'fix: clean NK articles from lex.uz (no OCR artifacts)'")
    print("   git push origin main")


if __name__ == "__main__":
    main()
