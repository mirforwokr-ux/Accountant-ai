# -*- coding: utf-8 -*-
"""
Local script: parse Tax Code PDF → seed ChromaDB
Usage:
    1. pip install pymupdf chromadb
    2. python parse_nk.py --pdf "path\to\4674893.pdf"

After running - push buxgalter_db/ to GitHub.
"""
import os
import sys
import re
import argparse
import chromadb
from chromadb.utils import embedding_functions

_here = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_here, "buxgalter_db")


def clean_text(text):
    """Fix common OCR artifacts in Cyrillic PDFs."""
    # Collapse excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove page numbers and document codes
    text = re.sub(r'НБДЗ:\s*\S+\s*от\s*\S+\s*г\.', '', text)
    text = re.sub(r'\d{1,3}\s*$', '', text)
    return text.strip()


def extract_articles(pdf_path):
    """Extract articles from NK PDF using PyMuPDF."""
    try:
        import fitz
    except ImportError:
        print("Installing PyMuPDF...")
        os.system(f"{sys.executable} -m pip install pymupdf")
        import fitz

    print(f"Opening PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    print(f"Pages: {len(doc)}")

    # Extract all text
    full_text = ""
    for i, page in enumerate(doc):
        full_text += page.get_text()
        if i % 50 == 0:
            print(f"  Reading page {i+1}/{len(doc)}...")

    print(f"Total characters: {len(full_text):,}")

    # Split by article pattern (handles OCR'd "CTaTbH")
    pattern = r'(CTaTbH\s+\d+[.\s]|CTaTh[ЯяHh]\s+\d+[.\s])'
    parts = re.split(pattern, full_text)

    articles = []
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""

        num_match = re.search(r'\d+', header)
        if not num_match:
            continue
        num = int(num_match.group())

        # Take meaningful chunk of article text
        body_clean = clean_text(body[:1200])

        # Skip very short articles (likely just references)
        if len(body_clean) < 80:
            continue

        # Find title - first line of body
        lines = body_clean.split('.')
        title = lines[0].strip() if lines else ""

        articles.append({
            "num": num,
            "title": title[:100],
            "text": body_clean,
        })

    print(f"\nParsed {len(articles)} articles from PDF")
    return articles


def seed_database(articles, also_add_faq=True):
    """Seed ChromaDB with parsed articles."""
    chroma_client = chromadb.PersistentClient(path=DB_PATH)
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    collection = chroma_client.get_or_create_collection(
        name="buxgalter_uz",
        embedding_function=embedding_fn,
    )

    # Clear existing
    if collection.count() > 0:
        print(f"Clearing {collection.count()} existing records...")
        existing = collection.get()
        collection.delete(ids=existing["ids"])

    documents = []
    ids = []

    # Add parsed NK articles
    for idx, art in enumerate(articles):
        doc_text = (
            f"НК РУз Статья {art['num']}. {art['title']}. "
            f"{art['text']}"
        )
        documents.append(doc_text)
        ids.append(f"nk_{art['num']}_{idx}")

    # Add supplementary FAQ
    if also_add_faq:
        faq = get_faq_knowledge()
        for i, text in enumerate(faq):
            documents.append(text)
            ids.append(f"faq_{i}")

    # Add in batches of 50
    batch_size = 50
    total = len(documents)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        collection.add(
            documents=documents[start:end],
            ids=ids[start:end],
        )
        print(f"  Added {end}/{total} records...")

    print(f"\n✅ Total records in DB: {collection.count()}")
    print(f"   NK articles: {len(articles)}")
    if also_add_faq:
        print(f"   FAQ entries: {len(get_faq_knowledge())}")


def get_faq_knowledge():
    """Supplementary practical knowledge not easily parseable from PDF."""
    return [
        "НДС (QQS) в Узбекистане: ставка 12%. Обязательная регистрация при обороте свыше 1 млрд сум/год. "
        "Декларация ежемесячно до 20-го. Уплата до 20-го следующего месяца. "
        "Счёт-фактура только через Didox. НДС к уплате = НДС с продаж 12% — НДС с покупок 12%.",

        "Расчёт зарплаты 2024: Gross 5 млн сум → НДФЛ 12% = 600 тыс → на руки 4 400 000. "
        "Соцналог работодателя 12% = 600 тыс. Итого расходов работодателя 5 600 000 сум. "
        "МРОТ 2024 = 980 000 сум. Срок уплаты НДФЛ и соцналога — до 15-го следующего месяца.",

        "Штрафы и пени в Узбекистане 2024: пеня за просрочку — 0,045%/день от суммы долга. "
        "Штраф за несдачу декларации — 2 БРВ = 680 000 сум. "
        "Занижение налоговой базы — 20% от заниженной суммы. БРВ 2024 = 340 000 сум.",

        "Упрощённый налог (УСН) Узбекистан: 4% от выручки при обороте до 1 млрд сум/год. "
        "Заменяет НДС и налог на прибыль. Квартальная декларация до 20-го числа после квартала. "
        "При превышении 1 млрд — переход на ОСНО обязателен.",

        "Налог на прибыль Узбекистан: ставка 15%. Авансы ежеквартально до 10-го числа. "
        "Годовая декларация до 1 апреля. Убытки переносятся до 5 лет вперёд.",

        "Didox (didox.uz) — обязательная электронная система счетов-фактур для плательщиков НДС. "
        "Бумажные счета-фактуры не дают права на зачёт НДС. "
        "Подпись счёта-фактуры — в течение 30 дней через ЭЦП.",

        "Soliq.uz — портал налоговой службы. Сдача отчётности, проверка задолженности, "
        "регистрация плательщика НДС, справки, переписка с налоговой.",

        "Командировочные суточные 2024 (не облагаются НДФЛ): "
        "внутри Узбекистана — 2 БРВ = 680 000 сум/день; "
        "за рубежом — по нормам КМ РУз. Авансовый отчёт через 3 дня после возвращения.",

        "Амортизация основных средств Узбекистан: здания — 2,5%/год; "
        "компьютеры и оргтехника — 25%/год; автомобили — 15%/год; оборудование — 10-15%/год. "
        "Основное средство: стоимость более 6 БРВ (2 040 000 сум) и срок службы более 1 года.",

        "Блокировка банковского счёта налоговой: причины — несдача декларации, неуплата долга. "
        "Разблокировка: устранить причину → заявление через Soliq.uz → снятие за 1 рабочий день.",
    ]


def main():
    parser = argparse.ArgumentParser(description='Parse Uzbekistan Tax Code PDF into ChromaDB')
    parser.add_argument('--pdf', required=True, help='Path to NK PDF file')
    parser.add_argument('--no-faq', action='store_true', help='Skip FAQ supplement')
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"ERROR: PDF not found: {args.pdf}")
        sys.exit(1)

    articles = extract_articles(args.pdf)

    if not articles:
        print("ERROR: No articles parsed. Check PDF format.")
        sys.exit(1)

    seed_database(articles, also_add_faq=not args.no_faq)

    print(f"\n✅ Done! Database saved to: {DB_PATH}")
    print("\nNext steps:")
    print("  1. Edit .gitignore — remove 'buxgalter_db/' line")
    print("  2. git add buxgalter_db/")
    print("  3. git commit -m 'add pre-built knowledge base from NK PDF'")
    print("  4. git push")


if __name__ == "__main__":
    main()
