# -*- coding: utf-8 -*-
"""
fix_ocr.py — Удаляет 390 статей НК РУз с OCR-мусором из ChromaDB.

OCR-артефакты возникли из-за неправильного чтения шрифтов PDF (PyMuPDF).
Чистые статьи НК нужно заново спарсить через parse_nk.py с PDF-файлом.

Запуск: python fix_ocr.py
После: python parse_nk.py --pdf "путь/к/НК.pdf"
"""
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "buxgalter_db" / "chroma.sqlite3"

# OCR-мусор выглядит как латинские буквы вместо кириллицы
# "HaJiorosoe" вместо "Налоговое", "CTaTbH" вместо "Статья" и т.д.
OCR_PATTERN = re.compile(
    r'[A-Za-z][A-Za-z]{2,}[oaeuiHJITl]'  # подозрительные латинские слова внутри русского текста
)

def is_garbled(text: str) -> bool:
    """True если текст содержит OCR-артефакты."""
    if not text or not text.startswith("НК РУз Статья"):
        return False
    # Считаем долю латинских символов (не цифр, не пробелов)
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    total = sum(1 for c in text if c.isalpha())
    if total == 0:
        return False
    ratio = latin / total
    return ratio > 0.15  # >15% латиницы → мусор

def fix_ocr():
    db = sqlite3.connect(str(DB_PATH))

    # Шаг 1: найти все rowid статей с мусором
    rows = db.execute(
        "SELECT rowid, id FROM embeddings"
    ).fetchall()

    # Получаем тексты из fulltext таблицы
    content_rows = db.execute(
        "SELECT rowid, c0 FROM embedding_fulltext_search_content"
    ).fetchall()
    content_map = {r[0]: r[1] for r in content_rows}

    garbled_ids = []
    for rowid, emb_id in rows:
        text = content_map.get(rowid, "")
        if is_garbled(text):
            garbled_ids.append((rowid, emb_id, text[:60]))

    print(f"Найдено статей с OCR-мусором: {len(garbled_ids)}")
    if not garbled_ids:
        print("✅ OCR-мусора нет, всё чисто!")
        db.close()
        return

    print("\nПримеры:")
    for _, emb_id, snippet in garbled_ids[:3]:
        print(f"  {snippet}...")

    confirm = input(f"\nУдалить {len(garbled_ids)} записей? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Отменено.")
        db.close()
        return

    # Шаг 2: удаляем из всех таблиц ChromaDB
    emb_rowids = [r[0] for r in garbled_ids]
    emb_ids    = [r[1] for r in garbled_ids]

    placeholders = ",".join("?" * len(emb_rowids))

    db.execute(f"DELETE FROM embeddings WHERE rowid IN ({placeholders})", emb_rowids)
    db.execute(f"DELETE FROM embedding_metadata WHERE id IN ({placeholders})", emb_ids)
    db.execute(
        f"DELETE FROM embedding_fulltext_search_content WHERE rowid IN ({placeholders})",
        emb_rowids
    )

    # Пересобираем FTS индекс
    try:
        db.execute("INSERT INTO embedding_fulltext_search(embedding_fulltext_search) VALUES('rebuild')")
    except Exception:
        pass  # не критично

    db.commit()

    # Финальный счётчик
    remaining = db.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    db.close()

    print(f"\n✅ Удалено: {len(garbled_ids)} статей с OCR-мусором")
    print(f"📊 Осталось в БД: {remaining} записей")
    print("\nДальнейшие шаги:")
    print("  1. Запустить: python parse_nk.py --pdf 'путь/к/НК_РУз.pdf'")
    print("  2. git add buxgalter_db/")
    print("  3. git commit -m 'fix: replace garbled NK articles with clean text'")
    print("  4. git push")

if __name__ == "__main__":
    fix_ocr()
