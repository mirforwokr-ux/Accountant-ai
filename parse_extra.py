# -*- coding: utf-8 -*-
"""
parse_extra.py — Дополнительные правовые документы
===================================================
Электронная коммерция, ЭЦП, ЭДО, ЭСФ + исправление ЦБУ и ГНК
Запуск: python parse_extra.py
"""
import re
import time
import logging
import hashlib
import requests
import chromadb
from pathlib import Path
from bs4 import BeautifulSoup
from chromadb.utils import embedding_functions

_here = Path(__file__).parent
DB_PATH = str(_here / "buxgalter_db")
LOG_FILE = str(_here / "parse_extra.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn  = embedding_functions.DefaultEmbeddingFunction()
collection    = chroma_client.get_or_create_collection(
    name="buxgalter_uz",
    embedding_function=embedding_fn,
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

EXTRA_DOCS = [
    # ── ЭСФ и цифровые законы ──────────────────────────────────
    {
        "id":    "law_ecommerce",
        "name":  "Закон об электронной коммерции",
        "short": "Закон об э-коммерции",
        "urls":  ["https://lex.uz/ru/docs/6213428"],
    },
    {
        "id":    "law_eds",
        "name":  "Закон об электронной цифровой подписи (ЭЦП)",
        "short": "Закон об ЭЦП",
        "urls":  ["https://lex.uz/ru/docs/6234906"],
    },
    {
        "id":    "law_edo",
        "name":  "Закон об электронном документообороте",
        "short": "Закон об ЭДО",
        "urls":  ["https://lex.uz/docs/165074"],
    },
    {
        "id":    "pkm_522_esf",
        "name":  "Постановление КМ № 522 об ЭСФ",
        "short": "ПКМ-522 ЭСФ",
        "urls":  ["https://lex.uz/docs/4386771"],
    },
    {
        "id":    "law_accounting_v2",
        "name":  "Закон о бухгалтерском учёте (полная версия)",
        "short": "Закон о бухучёте",
        "urls":  ["https://lex.uz/acts/90764"],
    },
    # ── ЦБУ — исправленные URL ──────────────────────────────────
    {
        "id":    "cbu_main",
        "name":  "Центральный банк Узбекистана — основные положения",
        "short": "ЦБУ положения",
        "urls":  [
            "https://lex.uz/docs/14591",       # Закон о ЦБ РУз
            "https://lex.uz/docs/14589",       # Закон о банках
            "https://lex.uz/docs/112926",      # Закон о валютном регулировании (старый)
        ],
    },
    # ── ГНК — дополнительные разъяснения ────────────────────────
    {
        "id":    "gnk_tax_admin",
        "name":  "Налоговое администрирование — разъяснения ГНК",
        "short": "ГНК разъяснения",
        "urls":  [
            "https://buxgalter.uz/publish/doc/text178085_informacionnoe_soobshchenie_mf_i_gnk_po_voprosam_nalogovogo_administrirovaniya",
            "https://buxgalter.uz/publish/doc/text212120_kakimi_normami_dopolnen_zakon_ob_akcionernyh_obshchestvah",
            "https://buxgalter.uz/publish/doc/text151822_s_1_yanvarya_2020_g_elektronnye_scheta-faktury_stanut_obyazatelnymi",
        ],
    },
    # ── Постановления Президента — расширенный список ───────────
    {
        "id":    "pp_business_2023_2025",
        "name":  "Постановления Президента по бизнесу 2023-2025",
        "short": "ПП по бизнесу",
        "urls":  [
            "https://lex.uz/docs/6366713",   # ПП об упрощении регистрации
            "https://lex.uz/docs/5398584",   # ПП о налоговых льготах IT
            "https://lex.uz/docs/5030424",   # ПП о малом бизнесе
            "https://lex.uz/docs/4953434",   # ПП о СЭЗ
        ],
    },
    # ── Налоговый кодекс — дополнительные разделы ───────────────
    {
        "id":    "nk_additional",
        "name":  "НК РУз — дополнительные разделы (акциз, имущество, земля)",
        "short": "НК РУз доп.",
        "urls":  [
            "https://lex.uz/docs/4674887",   # НК РУз актуальная редакция
        ],
    },
]


def clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
    return text


def fetch(url: str) -> str | None:
    for attempt in range(3):
        try:
            time.sleep(1.5)
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.encoding = "utf-8"
            if r.status_code == 200:
                return r.text
            log.warning(f"HTTP {r.status_code}: {url}")
        except Exception as e:
            log.warning(f"Попытка {attempt+1}/3 — {e}")
            time.sleep(2 ** attempt)
    return None


def extract(html: str, short: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(['nav','header','footer','script','style','aside']):
        tag.decompose()

    content = (
        soup.find("div", class_="document-content") or
        soup.find("div", class_="text-content") or
        soup.find("div", id="document") or
        soup.find("article") or
        soup.find("main") or
        soup.find("body")
    )
    if not content:
        return []

    full_text = content.get_text(separator="\n")
    lines = [clean_text(l) for l in full_text.splitlines() if len(clean_text(l)) > 5]

    art_re = re.compile(
        r'^(Статья\s+\d+[\.\-]?\d*'
        r'|Глава\s+\d+'
        r'|Раздел\s+[IVXLCDM\d]+'
        r'|§\s*\d+'
        r'|Пункт\s+\d+'
        r')\s*[\.:\-]?\s*(.{0,200})$',
        re.IGNORECASE
    )

    articles = []
    cur_title = None
    cur_body  = []

    def flush():
        nonlocal cur_title, cur_body
        if cur_title and cur_body:
            body = " ".join(cur_body)
            if len(body) > 30:
                articles.append(f"{short} {cur_title}. {body}"[:2000])
        cur_title = None
        cur_body  = []

    for line in lines:
        m = art_re.match(line)
        if m:
            flush()
            cur_title = m.group(1) + ((" — " + m.group(2)) if m.group(2).strip() else "")
        elif cur_title and len(line) > 10:
            cur_body.append(line)

    flush()

    # Фоллбэк — параграфы
    if len(articles) < 3:
        paras = [clean_text(p.get_text()) for p in content.find_all('p') if len(p.get_text(strip=True)) > 80]
        articles = []
        chunk, clen, cidx = [], 0, 0
        for p in paras:
            if clen + len(p) > 800 and chunk:
                cidx += 1
                articles.append(f"{short} (фрагмент {cidx}): {' '.join(chunk)}"[:2000])
                chunk, clen = [p], len(p)
            else:
                chunk.append(p); clen += len(p)
        if chunk:
            articles.append(f"{short} (фрагмент {cidx+1}): {' '.join(chunk)}"[:2000])

    return articles


def load_to_db(doc_id: str, articles: list[str]) -> int:
    added = 0
    for i in range(0, len(articles), 40):
        batch = articles[i:i+40]
        ids   = [f"{doc_id}_{i+j}" for j in range(len(batch))]
        try:
            ex  = collection.get(ids=ids)
            exi = set(ex["ids"])
        except Exception:
            exi = set()
        new = [(d, id_) for d, id_ in zip(batch, ids) if id_ not in exi]
        if new:
            docs_l, ids_l = zip(*new)
            collection.add(documents=list(docs_l), ids=list(ids_l))
            added += len(new)
    return added


def main():
    log.info("=" * 65)
    log.info("Buxgalter AI — Дополнительный парсинг")
    log.info(f"Записей в базе до: {collection.count()}")
    log.info("=" * 65)

    total_added = 0

    for idx, doc in enumerate(EXTRA_DOCS, 1):
        log.info(f"\n[{idx}/{len(EXTRA_DOCS)}] {doc['name']}")
        all_arts  = []
        seen_hash = set()

        for url in doc["urls"]:
            log.info(f"  → {url}")
            html = fetch(url)
            if not html:
                log.warning(f"  Нет ответа")
                continue

            if "buxgalter.uz" in url:
                # Для buxgalter.uz — особый парсер
                soup = BeautifulSoup(html, "lxml")
                for t in soup.find_all(['nav','header','footer','script','style']):
                    t.decompose()
                title_el = soup.find('h1') or soup.find('h2')
                title = title_el.get_text(strip=True) if title_el else doc['name']
                body  = soup.find("div", class_="article-body") or soup.find("main") or soup.find("body")
                if body:
                    paras = [clean_text(p.get_text()) for p in body.find_all(['p','li']) if len(p.get_text(strip=True)) > 40]
                    chunk, clen, cidx = [], 0, 0
                    arts = []
                    for p in paras:
                        if clen + len(p) > 700 and chunk:
                            cidx += 1
                            arts.append(f"{doc['short']} — {title} (ч.{cidx}): {' '.join(chunk)}"[:2000])
                            chunk, clen = [p], len(p)
                        else:
                            chunk.append(p); clen += len(p)
                    if chunk:
                        arts.append(f"{doc['short']} — {title} (ч.{cidx+1}): {' '.join(chunk)}"[:2000])
                    articles = arts
                else:
                    articles = []
            else:
                articles = extract(html, doc["short"])

            for art in articles:
                h = hashlib.md5(art[:150].encode()).hexdigest()
                if h not in seen_hash and len(art.strip()) > 50:
                    seen_hash.add(h)
                    all_arts.append(art)

            log.info(f"  Извлечено: {len(articles)} фрагментов")

        if not all_arts:
            log.warning(f"  ⚠️ Ничего не извлечено")
            continue

        added = load_to_db(doc["id"], all_arts)
        total_added += added
        log.info(f"  ✅ Добавлено: {added} записей | Итого в базе: {collection.count()}")

    log.info("\n" + "=" * 65)
    log.info(f"✅ Дополнительный парсинг завершён!")
    log.info(f"Добавлено: {total_added} записей")
    log.info(f"Итого в базе: {collection.count()} записей")
    log.info("=" * 65)

    print("\n📊 ФИНАЛЬНЫЙ РАЗМЕР БАЗЫ ЗНАНИЙ:")
    print(f"   {collection.count()} записей")
    print(f"\n   +{total_added} новых из {len(EXTRA_DOCS)} документов")


if __name__ == "__main__":
    main()
