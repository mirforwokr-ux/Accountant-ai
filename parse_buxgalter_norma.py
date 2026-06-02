# -*- coding: utf-8 -*-
"""
parse_buxgalter_norma.py — Краулер buxgalter.uz и norma.uz
===========================================================
Собирает практические статьи, разъяснения, комментарии к законам
и загружает в ChromaDB базу знаний Buxgalter AI.

Запуск:
  python parse_buxgalter_norma.py

Опции:
  python parse_buxgalter_norma.py --only buxgalter
  python parse_buxgalter_norma.py --only norma
  python parse_buxgalter_norma.py --limit 100   (максимум N статей с каждого)
"""

import re
import sys
import time
import logging
import hashlib
import argparse
import requests
import chromadb
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from chromadb.utils import embedding_functions

# ── Настройки ─────────────────────────────────────────────────────────────────
_here   = Path(__file__).parent
DB_PATH = str(_here / "buxgalter_db")
LOG_FILE = str(_here / "parse_buxgalter_norma.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.google.com/",
}

DELAY      = 2.0   # секунды между запросами
MAX_RETRY  = 3
BATCH_SIZE = 30

# ── ChromaDB ──────────────────────────────────────────────────────────────────
chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn  = embedding_functions.DefaultEmbeddingFunction()
collection    = chroma_client.get_or_create_collection(
    name="buxgalter_uz", embedding_function=embedding_fn)

# ══════════════════════════════════════════════════════════════════════════════
# КАТЕГОРИИ BUXGALTER.UZ
# ══════════════════════════════════════════════════════════════════════════════
BUXGALTER_CATEGORIES = [
    ("НДС",                 "https://buxgalter.uz/rubric/nds",                    "БУ НДС"),
    ("Налог на прибыль",    "https://buxgalter.uz/rubric/nalog-na-pribyl",        "БУ Прибыль"),
    ("НДФЛ",                "https://buxgalter.uz/rubric/ndfl",                   "БУ НДФЛ"),
    ("УСН",                 "https://buxgalter.uz/rubric/usn",                    "БУ УСН"),
    ("Зарплата",            "https://buxgalter.uz/rubric/zarplata",               "БУ Зарплата"),
    ("Бухгалтерский учёт",  "https://buxgalter.uz/rubric/bukhgalterskiy-uchet",   "БУ Бухучёт"),
    ("ВЭД и таможня",       "https://buxgalter.uz/rubric/vneshneekonomicheskaya-deyatelnost", "БУ ВЭД"),
    ("Акциз",               "https://buxgalter.uz/rubric/aktsiz",                 "БУ Акциз"),
    ("Страхование",         "https://buxgalter.uz/rubric/strakhovanie",           "БУ Страхование"),
    ("Социальный налог",    "https://buxgalter.uz/rubric/sotsialnyy-nalog",      "БУ Соцналог"),
    ("ООО и АО",            "https://buxgalter.uz/rubric/organizatsionno-pravovye-formy", "БУ ООО/АО"),
    ("ИП",                  "https://buxgalter.uz/rubric/individualnyy-predprinimatel", "БУ ИП"),
    ("Отчётность",          "https://buxgalter.uz/rubric/otchetnost",             "БУ Отчётность"),
    ("Основные средства",   "https://buxgalter.uz/rubric/osnovnye-sredstva",      "БУ ОС"),
    ("Дивиденды",           "https://buxgalter.uz/rubric/dividendy",              "БУ Дивиденды"),
    ("Налог на имущество",  "https://buxgalter.uz/rubric/nalog-na-imushchestvo", "БУ Имущество"),
    ("Земельный налог",     "https://buxgalter.uz/rubric/zemelnyy-nalog",         "БУ Земля"),
    ("Нерезиденты",         "https://buxgalter.uz/rubric/nerezidendy",            "БУ Нерезиденты"),
    ("ЭСФ и ЭДО",           "https://buxgalter.uz/rubric/elektronnye-scheta-faktury", "БУ ЭСФ"),
    ("ИНПС и пенсия",       "https://buxgalter.uz/rubric/inps",                   "БУ ИНПС"),
]

# ══════════════════════════════════════════════════════════════════════════════
# КАТЕГОРИИ NORMA.UZ
# ══════════════════════════════════════════════════════════════════════════════
NORMA_CATEGORIES = [
    ("Новости законодательства",  "https://www.norma.uz/novosti_zakonodatelstva",     "НМ Законы"),
    ("Налогообложение",           "https://www.norma.uz/nalogooblozhenie",            "НМ Налоги"),
    ("Бухгалтерский учёт",        "https://www.norma.uz/zakonodatelstvo_v_sfere_bukhgalterskogo_ucheta", "НМ Бухучёт"),
    ("Трудовое право",            "https://www.norma.uz/trudovoe_pravo",              "НМ Труд"),
    ("ВЭД",                       "https://www.norma.uz/vneshneekonomicheskaya_deyatelnost", "НМ ВЭД"),
    ("Судебная практика",         "https://www.norma.uz/sudebnaya_praktika",          "НМ Суд"),
    ("Предпринимательство",       "https://www.norma.uz/predprinimatelstvo",          "НМ Бизнес"),
    ("Таможня",                   "https://www.norma.uz/tamozhennoe_regulirovanie",   "НМ Таможня"),
    ("Недвижимость",              "https://www.norma.uz/nedvizhimost",               "НМ Недвиж."),
    ("IT и цифровизация",         "https://www.norma.uz/tsifrovizatsiya",             "НМ IT"),
]


# ══════════════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════════════════

def fetch(url: str, session: requests.Session) -> str | None:
    for attempt in range(MAX_RETRY):
        try:
            time.sleep(DELAY)
            r = session.get(url, headers=HEADERS, timeout=25, allow_redirects=True)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text
            elif r.status_code == 403:
                log.warning(f"403 Forbidden — возможно нужен VPN: {url}")
                return None
            elif r.status_code == 404:
                return None
            log.warning(f"HTTP {r.status_code}: {url}")
        except requests.exceptions.RequestException as e:
            log.warning(f"Попытка {attempt+1}/{MAX_RETRY}: {e}")
            time.sleep(3 ** attempt)
    return None


def clean(text: str) -> str:
    text = re.sub(r'[\xa0​­]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def add_to_db(doc_id_prefix: str, articles: list[str]) -> int:
    added = 0
    for i in range(0, len(articles), BATCH_SIZE):
        batch = articles[i:i+BATCH_SIZE]
        ids   = [f"{doc_id_prefix}_{i+j}" for j in range(len(batch))]
        try:    exi = set(collection.get(ids=ids)["ids"])
        except: exi = set()
        new = [(d, id_) for d, id_ in zip(batch, ids) if id_ not in exi]
        if new:
            dl, il = zip(*new)
            collection.add(documents=list(dl), ids=list(il))
            added += len(new)
    return added


# ══════════════════════════════════════════════════════════════════════════════
# ПАРСЕР BUXGALTER.UZ
# ══════════════════════════════════════════════════════════════════════════════

def get_buxgalter_article_links(category_url: str, session: requests.Session, limit: int = 50) -> list[str]:
    """Собирает ссылки на статьи из категории."""
    links = []
    page  = 1
    base  = "https://buxgalter.uz"

    while len(links) < limit:
        url  = f"{category_url}?page={page}" if page > 1 else category_url
        html = fetch(url, session)
        if not html:
            break

        soup = BeautifulSoup(html, "lxml")

        # Ищем ссылки на статьи
        found = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/publish/doc/" in href or "/doc/text" in href:
                full = urljoin(base, href)
                if full not in links and full not in found:
                    found.append(full)

        if not found:
            break

        links.extend(found)
        log.info(f"  Страница {page}: найдено {len(found)} ссылок")

        # Проверяем наличие следующей страницы
        next_btn = soup.find("a", string=re.compile(r"Следующая|›|>>|next", re.I))
        if not next_btn:
            break
        page += 1

    return links[:limit]


def parse_buxgalter_article(url: str, short: str, session: requests.Session) -> str | None:
    """Парсит одну статью buxgalter.uz."""
    html = fetch(url, session)
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        t.decompose()

    # Заголовок
    title_el = (soup.find("h1", class_=re.compile(r'title|heading', re.I)) or
                soup.find("h1") or soup.find("h2"))
    title = clean(title_el.get_text()) if title_el else ""

    # Основной контент
    content = (
        soup.find("div", class_=re.compile(r'article.body|content|doc.text|text', re.I)) or
        soup.find("div", class_="article-body") or
        soup.find("div", class_="content") or
        soup.find("div", id=re.compile(r'content|article', re.I)) or
        soup.find("article") or
        soup.find("main")
    )

    if not content:
        return None

    # Извлекаем параграфы
    paragraphs = []
    for elem in content.find_all(['p', 'li', 'h3', 'h4']):
        text = clean(elem.get_text())
        if len(text) > 30:
            paragraphs.append(text)

    if not paragraphs:
        text = clean(content.get_text())
        if len(text) > 100:
            paragraphs = [text[i:i+600] for i in range(0, min(len(text), 3000), 600)]

    if not paragraphs:
        return None

    # Сборка
    body = " ".join(paragraphs[:15])  # максимум 15 абзацев
    if len(body) < 80:
        return None

    result = f"{short}"
    if title:
        result += f" — {title}"
    result += f": {body}"

    return result[:2000]


def crawl_buxgalter(session: requests.Session, limit_per_cat: int = 50) -> int:
    log.info("\n" + "="*65)
    log.info("BUXGALTER.UZ — старт краулинга")
    log.info("="*65)

    total_added = 0
    seen_urls   = set()

    for cat_name, cat_url, short in BUXGALTER_CATEGORIES:
        log.info(f"\n📂 {cat_name} ({cat_url})")

        # Получаем ссылки
        article_links = get_buxgalter_article_links(cat_url, session, limit=limit_per_cat)
        log.info(f"  Найдено ссылок: {len(article_links)}")

        articles  = []
        seen_hash = set()

        for i, url in enumerate(article_links):
            if url in seen_urls:
                continue
            seen_urls.add(url)

            article = parse_buxgalter_article(url, short, session)
            if article:
                h = hashlib.md5(article[:150].encode()).hexdigest()
                if h not in seen_hash:
                    seen_hash.add(h)
                    articles.append(article)

            if (i + 1) % 10 == 0:
                log.info(f"  Обработано {i+1}/{len(article_links)} статей...")

        if articles:
            doc_id = f"bx_{re.sub(r'[^a-z0-9]', '_', cat_name.lower()[:20])}"
            added  = add_to_db(doc_id, articles)
            total_added += added
            log.info(f"  ✅ Добавлено: {added}/{len(articles)} | База: {collection.count()}")
        else:
            log.warning(f"  ⚠️ Статьи не извлечены")

    return total_added


# ══════════════════════════════════════════════════════════════════════════════
# ПАРСЕР NORMA.UZ
# ══════════════════════════════════════════════════════════════════════════════

def get_norma_article_links(category_url: str, session: requests.Session, limit: int = 50) -> list[str]:
    """Собирает ссылки на статьи norma.uz."""
    links = []
    page  = 1
    base  = "https://www.norma.uz"

    while len(links) < limit:
        url  = f"{category_url}?page={page}" if page > 1 else category_url
        html = fetch(url, session)
        if not html:
            break

        soup = BeautifulSoup(html, "lxml")
        found = []

        for a in soup.find_all("a", href=True):
            href = a["href"]
            # norma.uz статьи: /novosti_zakonodatelstva/NNNN или /doc/NNNN
            if re.search(r'/(novosti|nalogooblozhenie|trudovoe|sudebnaya|predprinimatelstvo|bukhgalter|tamozhenn|nedvizh|tsifr|vneshne)/\d+', href):
                full = urljoin(base, href)
                if full not in links and full not in found and full != category_url:
                    found.append(full)

        if not found:
            # Пробуем другой паттерн
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if re.match(r'^/[a-z_]+/\d+$', href):
                    full = urljoin(base, href)
                    if full not in links and full not in found:
                        found.append(full)

        if not found:
            break

        links.extend(found)
        log.info(f"  Страница {page}: найдено {len(found)} ссылок")

        next_btn = soup.find("a", string=re.compile(r"Следующая|›|>>|next|\d+", re.I),
                             class_=re.compile(r"next|page|pager", re.I))
        if not next_btn or page >= 10:
            break
        page += 1

    return links[:limit]


def parse_norma_article(url: str, short: str, session: requests.Session) -> str | None:
    """Парсит одну статью norma.uz."""
    html = fetch(url, session)
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(['script', 'style', 'nav', 'footer', 'header']):
        t.decompose()

    # Заголовок
    title_el = soup.find("h1") or soup.find("h2")
    title = clean(title_el.get_text()) if title_el else ""

    # Основной контент (norma.uz использует разные классы)
    content = (
        soup.find("div", class_=re.compile(r'article|content|text|doc|main', re.I)) or
        soup.find("div", id=re.compile(r'content|article|doc', re.I)) or
        soup.find("article") or
        soup.find("main")
    )

    if not content:
        # Используем весь body
        content = soup.find("body")
        if not content:
            return None

    paragraphs = []
    for elem in content.find_all(['p', 'li', 'h3', 'h4', 'td']):
        text = clean(elem.get_text())
        if len(text) > 40:
            paragraphs.append(text)

    if not paragraphs:
        return None

    body = " ".join(paragraphs[:12])
    if len(body) < 80:
        return None

    result = f"{short}"
    if title:
        result += f" — {title}"
    result += f": {body}"

    return result[:2000]


def crawl_norma(session: requests.Session, limit_per_cat: int = 40) -> int:
    log.info("\n" + "="*65)
    log.info("NORMA.UZ — старт краулинга")
    log.info("="*65)

    total_added = 0
    seen_urls   = set()

    for cat_name, cat_url, short in NORMA_CATEGORIES:
        log.info(f"\n📂 {cat_name} ({cat_url})")

        article_links = get_norma_article_links(cat_url, session, limit=limit_per_cat)
        log.info(f"  Найдено ссылок: {len(article_links)}")

        articles  = []
        seen_hash = set()

        for i, url in enumerate(article_links):
            if url in seen_urls:
                continue
            seen_urls.add(url)

            article = parse_norma_article(url, short, session)
            if article:
                h = hashlib.md5(article[:150].encode()).hexdigest()
                if h not in seen_hash:
                    seen_hash.add(h)
                    articles.append(article)

            if (i + 1) % 10 == 0:
                log.info(f"  Обработано {i+1}/{len(article_links)} статей...")

        if articles:
            doc_id = f"nm_{re.sub(r'[^a-z0-9]', '_', cat_name.lower()[:20])}"
            added  = add_to_db(doc_id, articles)
            total_added += added
            log.info(f"  ✅ Добавлено: {added}/{len(articles)} | База: {collection.count()}")
        else:
            log.warning(f"  ⚠️ Статьи не извлечены (возможно требуется авторизация)")

    return total_added


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Краулер buxgalter.uz и norma.uz")
    parser.add_argument("--only",  choices=["buxgalter", "norma"], help="Только один сайт")
    parser.add_argument("--limit", type=int, default=50, help="Макс. статей на категорию")
    args = parser.parse_args()

    log.info("="*65)
    log.info("Buxgalter AI — Краулер buxgalter.uz + norma.uz")
    log.info(f"Записей в базе до: {collection.count()}")
    log.info(f"Лимит статей на категорию: {args.limit}")
    log.info("="*65)

    session     = requests.Session()
    session.headers.update(HEADERS)
    total_added = 0

    try:
        if args.only != "norma":
            log.info("\n🌐 Начинаем buxgalter.uz...")
            n = crawl_buxgalter(session, limit_per_cat=args.limit)
            total_added += n
            log.info(f"\nbuxgalter.uz итого: +{n} записей")

        if args.only != "buxgalter":
            log.info("\n🌐 Начинаем norma.uz...")
            n = crawl_norma(session, limit_per_cat=args.limit)
            total_added += n
            log.info(f"\nnorma.uz итого: +{n} записей")

    except KeyboardInterrupt:
        log.info("\n⚠️ Прервано пользователем. Прогресс сохранён в ChromaDB.")

    log.info("\n" + "="*65)
    log.info(f"✅ ГОТОВО! Добавлено: {total_added} записей")
    log.info(f"ИТОГО В БАЗЕ: {collection.count()} записей")
    log.info("="*65)

    # Итог по категориям
    print(f"\n📊 ФИНАЛЬНЫЙ РАЗМЕР БАЗЫ: {collection.count()} записей")
    print(f"   +{total_added} новых из buxgalter.uz + norma.uz")


if __name__ == "__main__":
    main()
