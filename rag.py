# -*- coding: utf-8 -*-
import os
import chromadb
from groq import Groq
from chromadb.utils import embedding_functions
from dotenv import load_dotenv

# Load .env from script directory, then parent directory (C:\Users\L-tech\.env)
_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, '.env'))
load_dotenv(os.path.join(_here, '..', '.env'))

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DB_PATH = os.path.join(_here, "buxgalter_db")

if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY not found! Add it to .env file.")

client = Groq(api_key=GROQ_API_KEY)

chroma_client = chromadb.PersistentClient(path=DB_PATH)
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="buxgalter_uz",
    embedding_function=embedding_fn,
)

SYSTEM_PROMPT = """Вы профессиональный консультант-бухгалтер по налоговому законодательству Узбекистана.
Всегда отвечайте на языке вопроса (русский или узбекский).
Давайте конкретные цифры: ставки налогов, сроки, штрафы.
Используйте контекст из базы знаний если он предоставлен.
Если информации нет - направляйте на lex.uz или soliq.uz.
Никогда не придумывайте ставки и статьи законов.
В конце добавляйте: Для точных расчётов обратитесь к сертифицированному бухгалтеру."""


def search_knowledge_base(query, n_results=3):
    try:
        count = collection.count()
        if count == 0:
            return ""
        results = collection.query(
            query_texts=[query],
            n_results=min(n_results, count),
        )
        if not results["documents"] or not results["documents"][0]:
            return ""
        return " | ".join(results["documents"][0])
    except Exception:
        return ""


def ask_buxgalter(user_message, history=None):
    context = search_knowledge_base(user_message)

    user_content = user_message
    if context:
        user_content = f"База знаний: {context}\n\nВопрос: {user_message}"

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add conversation history (last 10 messages to stay within token limits)
    if history:
        messages.extend(history[-10:])

    messages.append({"role": "user", "content": user_content})

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        max_tokens=1024,
        temperature=0.3,
    )
    return response.choices[0].message.content
