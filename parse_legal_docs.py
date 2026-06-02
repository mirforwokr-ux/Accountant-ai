# -*- coding: utf-8 -*-
"""
parse_legal_docs.py — Профессиональный парсер правовых документов Узбекистана
=============================================================================
Источники: lex.uz | buxgalter.uz | gov.uz/soliq | cbu.uz | president.uz

Документы (Приоритет 1):
  1. Гражданский кодекс РУз (ч.1 и ч.2)
  2. Трудовой кодекс РУз
  3. Таможенный кодекс РУз
  4. Закон об ООО
  5. Закон об АО
  6. Закон о бухгалтерском учёте
  7. Закон о ЭСФ (электронные счета-фактуры)
  8. Закон о валютном регулировании
  9. Положения ЦБ РУз
  10. Постановления Президента по налогам
  11. Разъяснения ГНК

Запуск:
  pip install requests beautifulsoup4 lxml chromadb
  python parse_legal_docs.py

Возможности:
  - Автоматическое возобновление (сохраняет прогресс в parse_progress.json)
  - Rate limiting (не перегружает серверы)
  - Дедупликация записей в ChromaDB
  - Структурированные Q&A из статей
  - Логирование в parse_legal_docs.log
"""

import os
import re
import json
import time
import logging
import hashlib
import requests
import chromadb
from pathlib import Path
from datetime import datetime
from bs4 import BeautifulSoup
from chromadb.utils import embedding_functions

# ── Конфигурация ──────────────────────────────────────────────────────────────
_here = Path(__file__).parent
DB_PATH       = str(_here / "buxgalter_db")
PROGRESS_FILE = str(_here / "parse_progress.json")
LOG_FILE      = str(_here / "parse_legal_docs.log")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

DELAY_BETWEEN_REQUESTS = 1.5   # секунды между запросами
MAX_RETRIES            = 3
BATCH_SIZE             = 40    # записей за один батч в ChromaDB

# ── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── ChromaDB ──────────────────────────────────────────────────────────────────
chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn  = embedding_functions.DefaultEmbeddingFunction()
collection    = chroma_client.get_or_create_collection(
    name="buxgalter_uz",
    embedding_function=embedding_fn,
)

# ── Список документов ─────────────────────────────────────────────────────────
DOCUMENTS = [
    {
        "id":    "civil_code_1",
        "name":  "Гражданский кодекс РУз (Часть 1)",
        "short": "ГК РУз ч.1",
        "urls":  ["https://lex.uz/docs/180550"],
        "type":  "lex",
    },
    {
        "id":    "civil_code_2",
        "name":  "Гражданский кодекс РУз (Часть 2)",
        "short": "ГК РУз ч.2",
        "urls":  ["https://lex.uz/docs/111181"],
        "type":  "lex",
    },
    {
        "id":    "labor_code",
        "name":  "Трудовой кодекс Республики Узбекистан",
        "short": "ТК РУз",
        "urls":  ["https://lex.uz/docs/6257291"],
        "type":  "lex",
    },
    {
        "id":    "customs_code",
        "name":  "Таможенный кодекс Республики Узбекистан",
        "short": "ТамК РУз",
        "urls":  ["https://lex.uz/docs/2876352"],
        "type":  "lex",
    },
    {
        "id":    "law_ooo",
        "name":  "Закон об обществах с ограниченной и дополнительной ответственностью",
        "short": "Закон об ООО",
        "urls":  ["https://lex.uz/ru/docs/18793"],
        "type":  "lex",
    },
    {
        "id":    "law_ao",
        "name":  "Закон об акционерных обществах",
        "short": "Закон об АО",
        "urls":  ["https://lex.uz/docs/14667"],
        "type":  "lex",
    },
    {
        "id":    "law_accounting",
        "name":  "Закон о бухгалтерском учёте",
        "short": "Закон о бухучёте",
        "urls":  ["https://lex.uz/docs/2931251"],
        "type":  "lex",
    },
    {
        "id":    "law_esf",
        "name":  "Закон об электронных счетах-фактурах (ЭСФ)",
        "short": "Закон о ЭСФ",
        "urls":  ["https://lex.uz/docs/4386771"],
        "type":  "lex",
    },
    {
        "id":    "law_currency",
        "name":  "Закон о валютном регулировании",
        "short": "Закон о валюте",
        "urls":  ["https://lex.uz/ru/docs/4562846", "https://lex.uz/acts/864231"],
        "type":  "lex",
    },
    {
        "id":    "cbu_regulations",
        "name":  "Положения Центрального банка Узбекистана",
        "short": "Положения ЦБУ",
        "urls":  ["https://lex.uz/ru/docs/8073658"],
        "type":  "lex",
    },
    {
        "id":    "president_tax_decrees",
        "name":  "Постановления Президента по налогам и бизнесу",
        "short": "ПП по налогам",
        "urls":  ["https://lex.uz/acts/40146"],
        "type":  "lex_acts",
    },
    {
        "id":    "gnk_explanations",
        "name":  "Разъяснения Государственной налоговой комитета",
        "short": "Разъяснения ГНК",
        "urls":  [
            "https://gov.uz/ru/soliq",
            "https://buxgalter.uz/publish/doc/text178085_informacionnoe_soobshchenie_mf_i_gnk_po_voprosam_nalogovogo_administrirovaniya",
        ],
        "type":  "info",
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# ПРОГРЕСС
# ══════════════════════════════════════════════════════════════════════════════

def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
# HTTP ЗАПРОСЫ
# ══════════════════════════════════════════════════════════════════════════════

def fetch_url(url: str, retries: int = MAX_RETRIES) -> str | None:
    for attempt in range(retries):
        try:
            time.sleep(DELAY_BETWEEN_REQUESTS)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.encoding = "utf-8"
            if resp.status_code == 200:
                return resp.text
            log.warning(f"HTTP {resp.status_code}: {url}")
        except Exception as e:
            log.warning(f"Попытка {attempt+1}/{retries} — ошибка: {e}")
            time.sleep(2 ** attempt)
    log.error(f"Не удалось получить: {url}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# ПАРСЕРЫ
# ══════════════════════════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    """Очищает и нормализует текст."""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text


def extract_articles_lex(html: str, doc_name: str, short: str) -> list[str]:
    """Парсит страницу lex.uz и извлекает статьи."""
    soup = BeautifulSoup(html, "lxml")
    articles = []

    # Убираем навигацию, шапку, подвал
    for tag in soup.find_all(['nav', 'header', 'footer', 'script', 'style', 'aside']):
        tag.decompose()

    # Ищем основной контент
    content_div = (
        soup.find("div", class_="document-content") or
        soup.find("div", class_="text-content") or
        soup.find("div", id="document") or
        soup.find("article") or
        soup.find("div", class_="doc-text") or
        soup.find("main") or
        soup.find("body")
    )

    if not content_div:
        return []

    full_text = content_div.get_text(separator="\n")
    lines = [clean_text(l) for l in full_text.splitlines() if clean_text(l)]

    # Паттерны для определения статей
    article_patterns = [
        re.compile(r'^(Статья\s+\d+[\.\-]?\d*)\s*[\.:]?\s*(.{5,})$', re.IGNORECASE),
        re.compile(r'^(Глава\s+\d+[\.\-]?\d*)\s*[\.:]?\s*(.{5,})$', re.IGNORECASE),
        re.compile(r'^(Раздел\s+[IVXLCDM\d]+)\s*[\.:]?\s*(.{5,})$', re.IGNORECASE),
        re.compile(r'^§\s*(\d+)\s*[\.:]?\s*(.{5,})$', re.IGNORECASE),
    ]

    current_article = None
    current_content = []

    def flush_article():
        nonlocal current_article, current_content
        if current_article and current_content:
            body = " ".join(current_content)
            if len(body) > 50:
                text = f"{short} {current_article}. {body}"
                articles.append(text[:2000])  # Ограничиваем длину
        current_article = None
        current_content = []

    for line in lines:
        if len(line) < 3:
            continue

        matched = False
        for pattern in article_patterns:
            m = pattern.match(line)
            if m:
                flush_article()
                current_article = m.group(1) + (" — " + m.group(2) if m.group(2) else "")
                matched = True
                break

        if not matched and current_article:
            if len(line) > 10:
                current_content.append(line)

    flush_article()

    # Если статьи не найдены — разбиваем на абзацы
    if not articles:
        paragraphs = [p.get_text(strip=True) for p in content_div.find_all('p') if len(p.get_text(strip=True)) > 80]
        for i, para in enumerate(paragraphs):
            text = f"{short} (фрагмент {i+1}): {clean_text(para)}"
            articles.append(text[:2000])

    log.info(f"  Извлечено статей/фрагментов: {len(articles)}")
    return articles


def extract_articles_buxgalter(html: str, doc_name: str, short: str) -> list[str]:
    """Парсит buxgalter.uz — разъяснения и комментарии."""
    soup = BeautifulSoup(html, "lxml")
    articles = []

    for tag in soup.find_all(['nav', 'header', 'footer', 'script', 'style']):
        tag.decompose()

    # Заголовок материала
    title_tag = soup.find('h1') or soup.find('h2')
    title = title_tag.get_text(strip=True) if title_tag else doc_name

    # Основной текст
    content = (
        soup.find("div", class_="article-body") or
        soup.find("div", class_="content") or
        soup.find("main") or
        soup.find("body")
    )

    if not content:
        return []

    paragraphs = content.find_all(['p', 'li'])
    full_text = []
    for p in paragraphs:
        t = clean_text(p.get_text())
        if len(t) > 40:
            full_text.append(t)

    # Разбиваем на чанки по ~500 символов
    chunk = []
    chunk_len = 0
    chunk_num = 0

    for text in full_text:
        if chunk_len + len(text) > 800 and chunk:
            chunk_num += 1
            entry = f"{short} — {title} (часть {chunk_num}): {' '.join(chunk)}"
            articles.append(entry[:2000])
            chunk = [text]
            chunk_len = len(text)
        else:
            chunk.append(text)
            chunk_len += len(text)

    if chunk:
        chunk_num += 1
        entry = f"{short} — {title} (часть {chunk_num}): {' '.join(chunk)}"
        articles.append(entry[:2000])

    log.info(f"  Извлечено фрагментов: {len(articles)}")
    return articles


def parse_document(doc: dict) -> list[str]:
    """Парсит один документ из всех его URL."""
    all_articles = []
    seen_hashes = set()

    for url in doc["urls"]:
        log.info(f"  Загружаю: {url}")
        html = fetch_url(url)
        if not html:
            log.warning(f"  Пропускаю URL (нет ответа): {url}")
            continue

        # Выбираем парсер по типу
        if "buxgalter.uz" in url:
            articles = extract_articles_buxgalter(html, doc["name"], doc["short"])
        else:
            articles = extract_articles_lex(html, doc["name"], doc["short"])

        # Дедупликация
        for art in articles:
            h = hashlib.md5(art[:200].encode()).hexdigest()
            if h not in seen_hashes and len(art.strip()) > 50:
                seen_hashes.add(h)
                all_articles.append(art)

    return all_articles


# ══════════════════════════════════════════════════════════════════════════════
# ЗАГРУЗКА В CHROMADB
# ══════════════════════════════════════════════════════════════════════════════

def load_to_chromadb(doc_id: str, articles: list[str]) -> int:
    """Загружает статьи в ChromaDB, пропуская дубликаты."""
    added = 0
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i + BATCH_SIZE]
        ids   = [f"{doc_id}_{i+j}" for j in range(len(batch))]

        # Проверяем существующие
        try:
            existing = collection.get(ids=ids)
            existing_ids = set(existing["ids"])
        except Exception:
            existing_ids = set()

        new_pairs = [(doc, id_) for doc, id_ in zip(batch, ids) if id_ not in existing_ids]
        if new_pairs:
            docs_list, ids_list = zip(*new_pairs)
            try:
                collection.add(documents=list(docs_list), ids=list(ids_list))
                added += len(new_pairs)
            except Exception as e:
                log.error(f"Ошибка добавления в ChromaDB: {e}")

    return added


# ══════════════════════════════════════════════════════════════════════════════
# ОСНОВНОЙ ПРОЦЕСС
# ══════════════════════════════════════════════════════════════════════════════

def main():
    start_time = datetime.now()
    log.info("=" * 70)
    log.info("Buxgalter AI — Парсинг правовых документов")
    log.info(f"Начало: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Текущий размер базы: {collection.count()} записей")
    log.info("=" * 70)

    progress = load_progress()
    total_added = 0
    total_docs  = len(DOCUMENTS)

    for idx, doc in enumerate(DOCUMENTS, 1):
        doc_id = doc["id"]

        # Пропускаем уже обработанные
        if progress.get(doc_id, {}).get("status") == "done":
            already = progress[doc_id].get("added", 0)
            log.info(f"[{idx}/{total_docs}] ✓ {doc['name']} — уже загружен ({already} записей)")
            continue

        log.info(f"\n[{idx}/{total_docs}] ══ {doc['name']} ══")

        try:
            articles = parse_document(doc)

            if not articles:
                log.warning(f"  Не удалось извлечь статьи из {doc['name']}")
                progress[doc_id] = {"status": "empty", "added": 0}
                save_progress(progress)
                continue

            added = load_to_chromadb(doc_id, articles)
            total_added += added

            progress[doc_id] = {
                "status":    "done",
                "added":     added,
                "extracted": len(articles),
                "timestamp": datetime.now().isoformat()
            }
            save_progress(progress)

            log.info(f"  ✅ {doc['name']}: извлечено={len(articles)}, добавлено={added}")
            log.info(f"  Итого в базе: {collection.count()} записей")

        except KeyboardInterrupt:
            log.info("\n⚠️  Прервано пользователем. Прогресс сохранён.")
            break
        except Exception as e:
            log.error(f"  ❌ Ошибка при обработке {doc['name']}: {e}")
            progress[doc_id] = {"status": "error", "error": str(e)}
            save_progress(progress)

    end_time = datetime.now()
    duration = (end_time - start_time).seconds

    log.info("\n" + "=" * 70)
    log.info(f"✅ Парсинг завершён!")
    log.info(f"Добавлено записей: {total_added}")
    log.info(f"Итого в базе: {collection.count()} записей")
    log.info(f"Время: {duration // 60} мин {duration % 60} сек")
    log.info(f"Прогресс сохранён: {PROGRESS_FILE}")
    log.info("=" * 70)

    # Итоговый отчёт
    print("\n📊 ОТЧЁТ:")
    for doc in DOCUMENTS:
        p = progress.get(doc["id"], {})
        status = p.get("status", "не обработан")
        added  = p.get("added", 0)
        icon   = "✅" if status == "done" else ("⚠️" if status == "empty" else "❌")
        print(f"  {icon} {doc['name']}: {added} записей")


if __name__ == "__main__":
    main()
