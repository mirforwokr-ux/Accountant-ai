# -*- coding: utf-8 -*-
"""
parse_fix404.py — Исправление 404 и добавление недостающих документов
Запуск: python parse_fix404.py
"""
import re, time, logging, hashlib, requests, chromadb
from pathlib import Path
from bs4 import BeautifulSoup
from chromadb.utils import embedding_functions

_here   = Path(__file__).parent
DB_PATH = str(_here / "buxgalter_db")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn  = embedding_functions.DefaultEmbeddingFunction()
collection    = chroma_client.get_or_create_collection(
    name="buxgalter_uz", embedding_function=embedding_fn)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
           "Accept-Language": "ru-RU,ru;q=0.9"}

FIX_DOCS = [
    # ── ЦБУ — правильные ID ───────────────────────────────────
    {"id": "cbu_law", "short": "Закон о ЦБУ",
     "urls": ["https://lex.uz/docs/2362923",   # Закон о ЦБ РУз
              "https://lex.uz/docs/2362924"]},  # Закон о банках и банковской деятельности
    # ── НК РУз — расширенный (акциз, имущество, земля) ───────
    {"id": "nk_extended", "short": "НК РУз расшир.",
     "urls": ["https://lex.uz/docs/3512881",   # НК РУз (последняя редакция от 2021+)
              "https://lex.uz/docs/39595"]},    # Закон о налогах и сборах (базовый)
    # ── Постановления Президента — актуальные ID ──────────────
    {"id": "pp_it_park", "short": "ПП IT-Park",
     "urls": ["https://lex.uz/docs/4753611",   # ПП об IT-парке
              "https://lex.uz/docs/3947616"]}, # ПП о малом бизнесе
    {"id": "pp_sez", "short": "ПП СЭЗ",
     "urls": ["https://lex.uz/docs/3210571",   # ПП о СЭЗ Навои
              "https://lex.uz/docs/2143961"]}, # ПП о СЭЗ Ангрен
    # ── НСБУ — Национальные стандарты бухучёта ────────────────
    {"id": "nsbu", "short": "НСБУ",
     "urls": ["https://lex.uz/docs/172992",    # НСБУ №1 Учётная политика
              "https://lex.uz/docs/172994",    # НСБУ №2 Запасы
              "https://lex.uz/docs/172996",    # НСБУ №3 Финансовые результаты
              "https://lex.uz/docs/172998",    # НСБУ №4 Финансовые вложения
              "https://lex.uz/docs/173000",    # НСБУ №5 ОС
              "https://lex.uz/docs/173002"]},  # НСБУ №6 НМА
    # ── Уголовный кодекс — экономические статьи ───────────────
    {"id": "uk_economy", "short": "УК РУз (экономика)",
     "urls": ["https://lex.uz/docs/111265"]},  # Уголовный кодекс РУз
    # ── Кодекс об административной ответственности ────────────
    {"id": "koap_tax", "short": "КоАО РУз (налоги)",
     "urls": ["https://lex.uz/docs/97661"]},   # Кодекс об административной ответственности
    # ── Закон об инвестиционной деятельности ──────────────────
    {"id": "law_invest", "short": "Закон об инвестициях",
     "urls": ["https://lex.uz/docs/2099702"]}, # Закон об инвестиционной деятельности
    # ── Дополнительные разъяснения ГНК / buxgalter.uz ─────────
    {"id": "gnk_extra", "short": "ГНК разъяснения",
     "urls": [
         "https://buxgalter.uz/publish/doc/text200234_poryadok_primeneniya_naloga_na_pribyl_dlya_yuridicheskih_lic",
         "https://buxgalter.uz/publish/doc/text198765_nds_dlya_nerezidentov_2023",
         "https://buxgalter.uz/publish/doc/text185432_usn_poryadok_primeneniya_2023",
     ]},
]


def fetch(url: str) -> str | None:
    for i in range(3):
        try:
            time.sleep(1.5)
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.encoding = "utf-8"
            if r.status_code == 200: return r.text
            log.warning(f"HTTP {r.status_code}: {url}")
            return None
        except Exception as e:
            log.warning(f"Попытка {i+1}: {e}")
            time.sleep(2**i)
    return None


def extract(html: str, short: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(['nav','header','footer','script','style']): t.decompose()
    body = (soup.find("div", class_="document-content") or
            soup.find("article") or soup.find("main") or soup.find("body"))
    if not body: return []

    lines = [re.sub(r'\s+',' ',l).strip() for l in body.get_text("\n").splitlines()
             if len(re.sub(r'\s+',' ',l).strip()) > 8]

    art_re = re.compile(r'^(Статья\s+\d+[\.\-]?\d*|Глава\s+\d+|Раздел\s+[\dIVXLC]+|§\s*\d+)\s*[\.:\-]?\s*(.{0,200})$', re.I)
    arts, cur_t, cur_b = [], None, []

    def flush():
        nonlocal cur_t, cur_b
        if cur_t and cur_b:
            s = " ".join(cur_b)
            if len(s) > 30: arts.append(f"{short} {cur_t}. {s}"[:2000])
        cur_t, cur_b = None, []

    for line in lines:
        m = art_re.match(line)
        if m:
            flush()
            cur_t = m.group(1) + (f" — {m.group(2)}" if m.group(2).strip() else "")
        elif cur_t and len(line) > 10:
            cur_b.append(line)
    flush()

    if len(arts) < 3:
        paras = [re.sub(r'\s+',' ',p.get_text()).strip() for p in body.find_all('p') if len(p.get_text(strip=True)) > 80]
        arts, chunk, cl, ci = [], [], 0, 0
        for p in paras:
            if cl + len(p) > 800 and chunk:
                ci += 1; arts.append(f"{short} (ч.{ci}): {' '.join(chunk)}"[:2000])
                chunk, cl = [p], len(p)
            else: chunk.append(p); cl += len(p)
        if chunk: arts.append(f"{short} (ч.{ci+1}): {' '.join(chunk)}"[:2000])

    return arts


def extract_buxgalter(html: str, short: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for t in soup.find_all(['nav','header','footer','script','style']): t.decompose()
    title_el = soup.find('h1') or soup.find('h2')
    title = title_el.get_text(strip=True) if title_el else short
    body = soup.find("div", class_="article-body") or soup.find("main") or soup.find("body")
    if not body: return []
    paras = [re.sub(r'\s+',' ',p.get_text()).strip() for p in body.find_all(['p','li']) if len(p.get_text(strip=True)) > 40]
    arts, chunk, cl, ci = [], [], 0, 0
    for p in paras:
        if cl + len(p) > 700 and chunk:
            ci += 1; arts.append(f"{short} — {title} (ч.{ci}): {' '.join(chunk)}"[:2000])
            chunk, cl = [p], len(p)
        else: chunk.append(p); cl += len(p)
    if chunk: arts.append(f"{short} — {title} (ч.{ci+1}): {' '.join(chunk)}"[:2000])
    return arts


def add(doc_id: str, articles: list[str]) -> int:
    added = 0
    for i in range(0, len(articles), 40):
        batch = articles[i:i+40]
        ids   = [f"{doc_id}_{i+j}" for j in range(len(batch))]
        try:    exi = set(collection.get(ids=ids)["ids"])
        except: exi = set()
        new = [(d,id_) for d,id_ in zip(batch,ids) if id_ not in exi]
        if new:
            dl, il = zip(*new)
            collection.add(documents=list(dl), ids=list(il))
            added += len(new)
    return added


def main():
    log.info(f"База до: {collection.count()} записей")
    total = 0
    for doc in FIX_DOCS:
        log.info(f"\n── {doc['short']} ──")
        arts, seen = [], set()
        for url in doc["urls"]:
            log.info(f"  {url}")
            html = fetch(url)
            if not html: continue
            extracted = (extract_buxgalter(html, doc["short"])
                        if "buxgalter.uz" in url
                        else extract(html, doc["short"]))
            for a in extracted:
                h = hashlib.md5(a[:150].encode()).hexdigest()
                if h not in seen and len(a.strip()) > 50:
                    seen.add(h); arts.append(a)
            log.info(f"  Извлечено: {len(extracted)}")

        if arts:
            n = add(doc["id"], arts)
            total += n
            log.info(f"  ✅ Добавлено: {n} | Итого: {collection.count()}")
        else:
            log.warning(f"  ⚠️ Пусто")

    log.info(f"\n{'='*55}")
    log.info(f"✅ Готово! Добавлено: {total}")
    log.info(f"ИТОГО В БАЗЕ: {collection.count()} записей")


if __name__ == "__main__":
    main()
