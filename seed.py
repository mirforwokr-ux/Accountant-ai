# -*- coding: utf-8 -*-
"""
Run once to seed the knowledge base: python seed.py
"""
import os
import chromadb
from chromadb.utils import embedding_functions

_here = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_here, "buxgalter_db")

chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="buxgalter_uz",
    embedding_function=embedding_fn,
)

KNOWLEDGE = [
    "НДС (QQS) в Узбекистане: стандартная ставка 12%. Обязательная регистрация при обороте свыше 1 млрд сумов в год. Отчётность ежемесячно до 20-го числа.",
    "НДФЛ (JSHSHT) в Узбекистане: единая ставка 12% для резидентов. Работодатель удерживает и перечисляет ежемесячно до 15-го числа.",
    "Социальный налог (Ijtimoiy soliq): ставка 12% от фонда оплаты труда. Платит работодатель. Срок уплаты — до 15-го числа следующего месяца.",
    "Налог на прибыль юридических лиц: базовая ставка 15%. Для малого бизнеса на упрощённой системе — 4% от выручки (оборота).",
    "Упрощённый налог (УСН): 4% от выручки для малого бизнеса. Применяется если оборот не превышает 1 млрд сумов в год.",
    "Единый налоговый платёж (ЕНП): заменяет несколько налогов для микрофирм. Ставка зависит от вида деятельности — от 4% до 25%.",
    "Didox — электронная система выставления счетов-фактур в Узбекистане. Обязательна для плательщиков НДС. Сайт: didox.uz",
    "Soliq.uz — официальный портал налоговой службы Узбекистана. Здесь сдаётся налоговая отчётность, проверяется задолженность.",
    "Штрафы за несвоевременную сдачу налоговой отчётности: 2 БРВ (базовая расчётная величина) за каждый день просрочки.",
    "Базовая расчётная величина (БРВ) в 2024 году: 340,000 сумов. Используется для расчёта штрафов и социальных выплат.",
    "Налоговый период по НДС: месяц. Декларация сдаётся до 20-го числа месяца, следующего за отчётным.",
    "Земельный налог и налог на имущество юрлиц: уплачиваются авансами ежеквартально до 10-го числа второго месяца квартала.",
]

if collection.count() > 0:
    print(f"Knowledge base already has {collection.count()} records. Skipping.")
else:
    collection.add(
        documents=KNOWLEDGE,
        ids=[f"doc_{i}" for i in range(len(KNOWLEDGE))],
    )
    print(f"Seeded {len(KNOWLEDGE)} records into knowledge base.")
