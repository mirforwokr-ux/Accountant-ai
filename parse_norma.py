# -*- coding: utf-8 -*-
"""
parse_norma.py — Парсер norma.uz
=================================
Собирает правовые статьи, разъяснения, комментарии к законам Узбекистана.

Запуск:
  python parse_norma.py               # все категории
  python parse_norma.py --limit 20    # 20 статей на категорию (тест)
  python parse_norma.py --cat taxes   # только налоги
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
        logging.FileHandler(str(_here / "parse_norma.log"), encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn  = embedding_functions.DefaultEmbeddingFunction()
collection    = chroma_client.get_or_create_collection(
    name="buxgalter_uz", embedding_function=embedding_fn)

BASE    = "https://www.norma.uz"
DELAY   = 2.5
RETRIES = 3

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://www.norma.uz/",
}

# Категории norma.uz с короткими метками для ChromaDB
CATEGORIES = {
    "taxes": [
        ("НМ Налоги",   "https://www.norma.uz/nalogooblozhenie"),
        ("НМ НДС",      "https://www.norma.uz/nalogooblozhenie/nds"),
        ("НМ Прибыль",  "https://www.norma.uz/nalogooblozhenie/nalog-na-pribyl"),
        ("НМ НДФЛ",     "https://www.norma.uz/nalogooblozhenie/ndfl"),
        ("НМ УСН",      "https://www.norma.uz/nalogooblozhenie/nalog-s-oborota"),
        ("НМ Акциз",    "https://www.norma.uz/nalogooblozhenie/aktsiznyj-nalog"),
    ],
    "accounting": [
        ("НМ Бухучёт",  "https://www.norma.uz/zakonodatelstvo_v_sfere_bukhgalterskogo_ucheta"),
        ("НМ НСБУ",     "https://www.norma.uz/zakonodatelstvo_v_sfere_bukhgalterskogo_ucheta/nсбу"),
        ("НМ Отчёт",    "https://www.norma.uz/zakonodatelstvo_v_sfere_bukhgalterskogo_ucheta/finansovaya-otchetnost"),
    ],
    "labor": [
        ("НМ Труд",     "https://www.norma.uz/trudovoe_pravo"),
        ("НМ Зарплата", "https://www.norma.uz/trudovoe_pravo/zarplata"),
        ("НМ Отпуск",   "https://www.norma.uz/trudovoe_pravo/otpusk"),
    ],
    "business": [
        ("НМ Бизнес",   "https://www.norma.uz/predprinimatelstvo"),
        ("НМ ООО",      "https://www.norma.uz/predprinimatelstvo/ooo"),
        ("НМ ИП",       "https://www.norma.uz/predprinimatelstvo/ip"),
        ("НМ Регистр.", "https://www.norma.uz/predprinimatelstvo/registratsiya"),
    ],
    "trade": [
        ("НМ ВЭД",      "https://www.norma.uz/vneshneekonomicheskaya_deyatelnost"),
        ("НМ Таможня",  "https://www.norma.uz/tamozhennoe_regulirovanie"),
        ("НМ Валюта",   "https://www.norma.uz/valyutnoe-regulirovanie"),
    ],
    "law": [
        ("НМ Законы",   "https://www.norma.uz/novosti_zakonodatelstva"),
        ("НМ Суд",      "https://www.norma.uz/sudebnaya_praktika"),
        ("НМ IT",       "https://www.norma.uz/tsifrovizatsiya"),
        ("НМ Недвиж.",  "https://www.norma.uz/nedvizhimost"),
    ],
}


# ────────────────────────────────────────────
def fetch(url: str, session: requests.Session) -> str | None:
    for attempt in range(RETRIES):
        try:
            time.sleep(DELAY)
            r = session.get(url, headers=HEADERS, timeout=20)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text
            if r.status_code in (403, 401):
                log.warning(f"  Доступ закрыт ({r.status_code}): {url}")
                return None
            if r.status_code == 404:
                return None
            log.warning(f"  HTTP {r.status_code}: {url}")
        except Exception as e:
            log.warning(f"  Попытка {attempt+1}: {e}")
            time.sleep(2 ** attempt)
    return None


def clean(text: str) -> str:
    text = re.sub(r'[\xa0​­]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


# ────────────────────────────────────────────
# Сбор ссылок на статьи
# ────────────────────────────────────────────
def get_article_links(cat_url: str, session: requests.Session, limit: int) -> list[str]:
    links = []
    page  = 1

    while len(links) < limit:
        url  = cat_url if page == 1 else f"{cat_url}?page={page}"
        html = fetch(url, session)
        if not html:
            break

        soup  = BeautifulSoup(html, "lxml")
        found = []

        # Паттерн 1: прямые ссылки на статьи norma.uz
        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Статьи вида: /novosti_zakonodatelstva/12345 или /nalogooblozhenie/nds/12345
            if re.search(r'/\d{4,}$', href) or re.search(r'/\d{4,}\?', href):
                full = urljoin(BASE, href)
                if full not in links and full not in found and BASE in full:
                    found.append(full)

        # Паттерн 2: ссылки с классами статей
        if not found:
            for a in soup.find_all("a", class_=re.compile(r'title|article|item|name', re.I), href=True):
                full = urljoin(BASE, a["href"])
                if full not in links and full not in found and BASE in full and full != cat_url:
                    found.append(full)

        # Паттерн 3: заголовки h2/h3 со ссылками
        if not found:
            for tag in soup.find_all(['h2', 'h3', 'h4']):
                a = tag.find('a', href=True)
                if a:
                    full = urljoin(BASE, a['href'])
                    if BASE in full and full != cat_url and full not in links:
                        found.append(full)

        if not found:
            log.info(f"  Стр.{page}: ссылки не найдены — останавливаемся")
            break

        # Фильтруем служебные ссылки
        found = [u for u in found if not any(x in u for x in
                 ['/login', '/register', '/search', '/rss', '/sitemap', '#'])]

        links.extend(found)
        log.info(f"  Стр.{page}: +{len(found)} ссылок (итого {len(links)})")

        # Следующая страница
        next_a = (soup.find("a", rel="next") or
                  soup.find("a", string=re.compile(r"Следующая|›|>>", re.I)) or
                  soup.find("li", class_=re.compile(r"next", re.I)))
        if not next_a or page >= 15:
            break
        page += 1

    return links[:limit]


# ────────────────────────────────────────────
# Парсинг одной статьи
# ────────────────────────────────────────────
def parse_article(url: str, short: str, session: requests.Session) -> str | None:
    html = fetch(url, session)
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(['script', 'style', 'nav', 'footer', 'header',
                            'aside', 'form', 'iframe']):
        t.decompose()

    # Заголовок
    title = ""
    for sel in ['h1.title', 'h1.article-title', 'h1.entry-title', 'h1', '.article-title', '.title']:
        el = soup.select_one(sel)
        if el:
            title = clean(el.get_text())
            break

    # Основной контент — пробуем разные контейнеры
    content = None
    for sel in [
        '.article-body', '.article-content', '.article-text',
        '.entry-content', '.content-text', '.doc-content',
        '#article-content', '#content', '.text-body',
        'article', 'main', '.main-content',
    ]:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 200:
            content = el
            break

    # Фоллбэк — ищем самый большой div
    if not content:
        divs = [(len(d.get_text(strip=True)), d) for d in soup.find_all('div')
                if len(d.get_text(strip=True)) > 200]
        if divs:
            content = max(divs, key=lambda x: x[0])[1]

    if not content:
        return None

    # Извлекаем текст
    paragraphs = []
    for elem in content.find_all(['p', 'li', 'td', 'h3', 'h4', 'blockquote']):
        text = clean(elem.get_text())
        if len(text) > 35 and text not in paragraphs:
            paragraphs.append(text)

    # Если параграфов нет — берём сырой текст
    if not paragraphs:
        raw = clean(content.get_text())
        if len(raw) > 150:
            paragraphs = [raw[i:i+500] for i in range(0, min(len(raw), 2500), 500)]

    if not paragraphs:
        return None

    body = " ".join(paragraphs[:14])
    if len(body) < 80:
        return None

    # Финальная запись
    entry = short
    if title:
        entry += f" — {title}"
    entry += f": {body}"

    return entry[:2000]


# ────────────────────────────────────────────
# Загрузка в ChromaDB
# ────────────────────────────────────────────
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


# ────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Парсер norma.uz для Buxgalter AI")
    ap.add_argument("--limit", type=int, default=40,
                    help="Макс. статей на категорию (default: 40)")
    ap.add_argument("--cat", choices=list(CATEGORIES.keys()),
                    help="Только одна группа категорий")
    args = ap.parse_args()

    log.info("=" * 60)
    log.info("Buxgalter AI — Парсер norma.uz")
    log.info(f"База до: {collection.count()} записей")
    log.info(f"Лимит на категорию: {args.limit}")
    log.info("=" * 60)

    cats_to_run = {args.cat: CATEGORIES[args.cat]} if args.cat else CATEGORIES
    session     = requests.Session()
    total_added = 0
    seen_urls   = set()

    try:
        for group_name, cat_list in cats_to_run.items():
            log.info(f"\n── Группа: {group_name.upper()} ──")

            for short, cat_url in cat_list:
                log.info(f"\n📂 {short} → {cat_url}")

                links = get_article_links(cat_url, session, args.limit)
                log.info(f"  Ссылок: {len(links)}")

                if not links:
                    log.warning("  Нет ссылок — категория закрыта или пуста")
                    continue

                articles, seen_hashes = [], set()

                for idx, url in enumerate(links):
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    art = parse_article(url, short, session)
                    if art:
                        h = hashlib.md5(art[:150].encode()).hexdigest()
                        if h not in seen_hashes:
                            seen_hashes.add(h)
                            articles.append(art)

                    if (idx + 1) % 5 == 0:
                        log.info(f"  [{idx+1}/{len(links)}] собрано {len(articles)}")

                if articles:
                    prefix = f"nm_{re.sub(r'[^a-z]', '_', short.lower())}"
                    added  = save_to_db(prefix, articles)
                    total_added += added
                    log.info(f"  ✅ +{added} записей | База: {collection.count()}")
                else:
                    log.warning("  Статьи пусты или закрыты")

    except KeyboardInterrupt:
        log.info("\n⚠️  Прервано. Данные в ChromaDB сохранены.")

    log.info("\n" + "=" * 60)
    log.info(f"✅ Готово! Добавлено: {total_added} записей")
    log.info(f"ИТОГО В БАЗЕ: {collection.count()} записей")
    log.info("=" * 60)
    print(f"\n📊 База знаний: {collection.count()} записей (+{total_added} из norma.uz)")


if __name__ == "__main__":
    main()
