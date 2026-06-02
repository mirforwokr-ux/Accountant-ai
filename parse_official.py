# -*- coding: utf-8 -*-
"""
parse_official.py — Официальные государственные источники РУз
==============================================================
Парсит:
  • soliq.uz      — ГНК, разъяснения налоговой
  • president.uz  — Постановления Президента
  • mf.uz         — Министерство финансов
  • cbu.uz        — Центральный банк

Запуск:
  python parse_official.py              # все источники
  python parse_official.py --only soliq
  python parse_official.py --only president
  python parse_official.py --only mf
  python parse_official.py --only cbu
  python parse_official.py --limit 30
"""

import re
import time
import logging
import hashlib
import argparse
import requests
import chromadb
from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from chromadb.utils import embedding_functions

_here   = Path(__file__).parent
DB_PATH = str(_here / "buxgalter_db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(_here / "parse_official.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn  = embedding_functions.DefaultEmbeddingFunction()
collection    = chroma_client.get_or_create_collection(
    name="buxgalter_uz", embedding_function=embedding_fn)

DELAY   = 2.0
RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,uz;q=0.8",
}


# ════════════════════════════════════════════════════════════
# ИСТОЧНИКИ
# ════════════════════════════════════════════════════════════

SOURCES = {
    "soliq": {
        "name": "ГНК — Государственный налоговый комитет",
        "base": "https://soliq.uz",
        "categories": [
            ("ГНК Новости",        "https://soliq.uz/ru/news"),
            ("ГНК Разъяснения",    "https://soliq.uz/ru/press-center/articles"),
            ("ГНК НДС",            "https://soliq.uz/ru/press-center/articles?category=nds"),
            ("ГНК Прибыль",        "https://soliq.uz/ru/press-center/articles?category=nalog-na-pribyl"),
            ("ГНК НДФЛ",           "https://soliq.uz/ru/press-center/articles?category=ndfl"),
            ("ГНК УСН",            "https://soliq.uz/ru/press-center/articles?category=nalog-s-oborota"),
            ("ГНК Соцналог",       "https://soliq.uz/ru/press-center/articles?category=sotsialnyy-nalog"),
            ("ГНК FAQ",            "https://soliq.uz/ru/faq"),
            ("ГНК Сервисы",        "https://soliq.uz/ru/services"),
        ],
        "article_patterns": ["/ru/news/", "/ru/press-center/articles/", "/ru/faq/"],
        "content_selectors": [
            ".article-content", ".news-content", ".content-body",
            ".article-body", ".text-content", "article", ".main-content",
        ],
    },
    "president": {
        "name": "Президент Республики Узбекистан",
        "base": "https://president.uz",
        "categories": [
            ("ПП Постановления",   "https://president.uz/ru/lists/view/2"),
            ("ПП Указы",           "https://president.uz/ru/lists/view/1"),
            ("ПП Налоги",          "https://president.uz/ru/lists/view/2?q=налог"),
            ("ПП Бизнес",          "https://president.uz/ru/lists/view/2?q=предпринимательство"),
            ("ПП IT",              "https://president.uz/ru/lists/view/2?q=информационные+технологии"),
            ("ПП Инвестиции",      "https://president.uz/ru/lists/view/2?q=инвестиции"),
            ("ПП СЭЗ",             "https://president.uz/ru/lists/view/2?q=свободная+экономическая+зона"),
        ],
        "article_patterns": ["/ru/lists/view/", "/ru/documents/view/"],
        "content_selectors": [
            ".document-content", ".decree-content", ".content",
            ".article-text", "article", ".main-text",
        ],
    },
    "mf": {
        "name": "Министерство финансов Республики Узбекистан",
        "base": "https://mf.uz",
        "categories": [
            ("МФ Новости",         "https://mf.uz/ru/news/"),
            ("МФ Налоги",          "https://mf.uz/ru/tax/"),
            ("МФ Бюджет",          "https://mf.uz/ru/budget/"),
            ("МФ НК РУз",          "https://mf.uz/ru/legislation/tax-code/"),
            ("МФ МСФО",            "https://mf.uz/ru/accounting/ifrs/"),
            ("МФ НСБУ",            "https://mf.uz/ru/accounting/nsbu/"),
            ("МФ Публикации",      "https://mf.uz/ru/publications/"),
            ("МФ Разъяснения",     "https://mf.uz/ru/clarifications/"),
        ],
        "article_patterns": ["/ru/news/", "/ru/tax/", "/ru/budget/",
                              "/ru/legislation/", "/ru/accounting/", "/ru/publications/"],
        "content_selectors": [
            ".article-content", ".news-body", ".content-area",
            ".mf-content", "article .content", ".text", "main",
        ],
    },
    "cbu": {
        "name": "Центральный банк Республики Узбекистан",
        "base": "https://cbu.uz",
        "categories": [
            ("ЦБУ Новости",        "https://cbu.uz/ru/press-centre/news/"),
            ("ЦБУ Нормативы",      "https://cbu.uz/ru/legal-acts/"),
            ("ЦБУ Публикации",     "https://cbu.uz/ru/publications/"),
            ("ЦБУ Валюта",         "https://cbu.uz/ru/currency/"),
            ("ЦБУ Банки",          "https://cbu.uz/ru/financial-system/banks/"),
            ("ЦБУ Платежи",        "https://cbu.uz/ru/payment-system/"),
        ],
        "article_patterns": ["/ru/press-centre/news/", "/ru/legal-acts/",
                              "/ru/publications/", "/ru/currency/"],
        "content_selectors": [
            ".news-content", ".article-body", ".publication-content",
            ".content-block", ".legal-text", "article", "main",
        ],
    },
}


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def fetch(url: str, session: requests.Session) -> str | None:
    for attempt in range(RETRIES):
        try:
            time.sleep(DELAY)
            r = session.get(url, headers=HEADERS, timeout=25)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 401, 429):
                log.warning(f"  {r.status_code} — доступ закрыт: {url}")
                return None
            if r.status_code == 404:
                return None
        except Exception as e:
            log.warning(f"  Попытка {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def clean(text: str) -> str:
    text = re.sub(r'[\xa0​­]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def get_links(html: str, base: str, patterns: list[str]) -> list[str]:
    """Извлекает ссылки на статьи по паттернам."""
    soup  = BeautifulSoup(html, "lxml")
    links = []
    seen  = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base, href)
        if any(p in full for p in patterns) and full not in seen:
            # Исключаем служебные страницы
            if not any(x in full for x in ['?page=', '#', '/login', '/register', '/search']):
                seen.add(full)
                links.append(full)
    return links


def parse_article(html: str, short: str, url: str, selectors: list[str]) -> str | None:
    """Извлекает текст статьи."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(['script', 'style', 'nav', 'footer',
                            'header', 'aside', 'form', 'iframe']):
        t.decompose()

    # Заголовок
    title = ""
    for sel in ['h1.title', '.article-title', 'h1', '.page-title', '.doc-title']:
        el = soup.select_one(sel)
        if el:
            title = clean(el.get_text())
            if len(title) > 5:
                break

    # Контент
    content = None
    for sel in selectors:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 150:
            content = el
            break

    # Фоллбэк — самый большой блок
    if not content:
        best = max(
            ((len(d.get_text(strip=True)), d) for d in soup.find_all('div')
             if len(d.get_text(strip=True)) > 150),
            key=lambda x: x[0], default=(0, None)
        )
        content = best[1]

    if not content:
        return None

    # Параграфы
    paras = []
    for el in content.find_all(['p', 'li', 'h3', 'h4', 'td', 'blockquote']):
        t = clean(el.get_text())
        if len(t) > 30 and t not in paras:
            paras.append(t)

    if not paras:
        raw = clean(content.get_text())
        if len(raw) < 100:
            return None
        paras = [raw[i:i+500] for i in range(0, min(len(raw), 2500), 500)]

    body = " ".join(paras[:12])
    if len(body) < 80:
        return None

    entry = short
    if title:
        entry += f" — {title}"
    entry += f": {body}"
    return entry[:2000]


def save_to_db(prefix: str, articles: list[str]) -> int:
    added = 0
    for i in range(0, len(articles), 30):
        batch = articles[i:i+30]
        ids   = [f"{prefix}_{i+j}" for j in range(len(batch))]
        try:    exi = set(collection.get(ids=ids)["ids"])
        except: exi = set()
        new = [(d, id_) for d, id_ in zip(batch, ids) if id_ not in exi]
        if new:
            dl, il = zip(*new)
            collection.add(documents=list(dl), ids=list(il))
            added += len(new)
    return added


# ════════════════════════════════════════════════════════════
# КРАУЛЕР
# ════════════════════════════════════════════════════════════

def crawl_source(key: str, cfg: dict, session: requests.Session, limit: int) -> int:
    log.info(f"\n{'='*60}")
    log.info(f"🏛️  {cfg['name']}")
    log.info(f"{'='*60}")

    total_added = 0
    seen_urls   = set()

    for short, cat_url in cfg["categories"]:
        log.info(f"\n📂 {short} → {cat_url}")

        # Загружаем страницу категории
        html = fetch(cat_url, session)
        if not html:
            log.warning("  Страница недоступна — пропускаем")
            continue

        # Ищем ссылки на статьи
        links = get_links(html, cfg["base"], cfg["article_patterns"])

        # Пробуем дополнительные страницы пагинации
        for page in range(2, 6):
            if len(links) >= limit:
                break
            sep = "&" if "?" in cat_url else "?"
            page_url  = f"{cat_url}{sep}page={page}"
            page_html = fetch(page_url, session)
            if not page_html:
                break
            new_links = get_links(page_html, cfg["base"], cfg["article_patterns"])
            if not new_links or set(new_links) <= set(links):
                break
            links.extend(l for l in new_links if l not in links)
            log.info(f"  Страница {page}: +{len(new_links)} ссылок")

        links = [l for l in links if l not in seen_urls][:limit]
        log.info(f"  Ссылок для парсинга: {len(links)}")

        if not links:
            log.warning("  Ссылки не найдены")
            continue

        articles  = []
        seen_hash = set()

        for idx, url in enumerate(links):
            seen_urls.add(url)
            art_html = fetch(url, session)
            if not art_html:
                continue

            art = parse_article(art_html, short, url, cfg["content_selectors"])
            if art:
                h = hashlib.md5(art[:150].encode()).hexdigest()
                if h not in seen_hash:
                    seen_hash.add(h)
                    articles.append(art)

            if (idx + 1) % 10 == 0:
                log.info(f"  [{idx+1}/{len(links)}] собрано: {len(articles)}")

        if articles:
            prefix = f"{key}_{re.sub(r'[^a-z0-9]', '_', short.lower()[:20])}"
            added  = save_to_db(prefix, articles)
            total_added += added
            log.info(f"  ✅ +{added} записей | База: {collection.count()}")
        else:
            log.warning("  Статьи не извлечены (возможно JS-рендеринг или другая структура)")

    return total_added


# ════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only",  choices=list(SOURCES.keys()), help="Только один источник")
    ap.add_argument("--limit", type=int, default=40, help="Макс. статей на категорию")
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("Buxgalter AI — Официальные источники РУз")
    log.info(f"База до: {collection.count()} записей")
    log.info(f"Лимит на категорию: {args.limit}")
    log.info("=" * 60)

    session     = requests.Session()
    total_added = 0
    to_run      = {args.only: SOURCES[args.only]} if args.only else SOURCES

    try:
        for key, cfg in to_run.items():
            n = crawl_source(key, cfg, session, args.limit)
            total_added += n
            log.info(f"\n{cfg['name']}: +{n} записей")

    except KeyboardInterrupt:
        log.info("\n⚠️  Прервано. Данные сохранены.")

    log.info("\n" + "=" * 60)
    log.info(f"✅ ИТОГО ДОБАВЛЕНО: {total_added} записей")
    log.info(f"БАЗА ЗНАНИЙ: {collection.count()} записей")
    log.info("=" * 60)

    print(f"\n📊 Итог:")
    print(f"   База знаний: {collection.count()} записей")
    print(f"   Добавлено:   +{total_added} из официальных источников")


if __name__ == "__main__":
    main()
