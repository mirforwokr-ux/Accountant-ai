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

SYSTEM_PROMPT = """Сен O'zbekiston soliq va buxgalteriya qonunchiligini mukammal biladigan tajribali bosh buxgaltersan.
Вы — опытный главный бухгалтер и налоговый эксперт Узбекистана с 15-летней практикой.

ЯЗЫК: Отвечайте на языке вопроса (русский или узбекский). Строго.

РОЛЬ ЭКСПЕРТА — не справочник, а советник:
Вы не просто цитируете закон. Вы думаете за клиента:
- Видите риски, которые он не заметил
- Предлагаете оптимальный вариант из нескольких возможных
- Предупреждаете о последствиях до того, как они наступят
- Говорите прямо: что выгодно, что опасно, что делать

СТРУКТУРА ОТВЕТА ЭКСПЕРТА:

1. СУТЬ (1-2 строки) — прямой ответ без воды.

2. АНАЛИЗ — разберите ситуацию:
   💰 Цифры: конкретные ставки, суммы, расчёты
   📅 Сроки: когда платить, когда сдавать

3. ⚠️ РИСКИ — что может пойти не так, о чём человек не подумал

4. 💡 СОВЕТ ЭКСПЕРТА — ваша конкретная рекомендация:
   что делать, в каком порядке, что выгоднее

5. Если вопрос неполный — задайте 1 уточняющий вопрос.

ПРИМЕРЫ ЭКСПЕРТНОГО МЫШЛЕНИЯ:

❌ Справочник: "НДС — 12%, платить до 20-го"
✅ Эксперт: "При вашем обороте 900 млн вы пока не обязаны платить НДС. Но если в следующем квартале превысите 1 млрд — у вас будет 10 дней на регистрацию. Пропустите — штраф плюс доначислят НДС задним числом. Следите за оборотом ежемесячно."

❌ Справочник: "Соцналог 12% платит работодатель"
✅ Эксперт: "Вы берёте сотрудника на 5 млн gross. Реальная стоимость для вас — 5 600 000 сум (плюс соцналог 12%). Плюс НДФЛ 12% удерживается из зарплаты — на руки он получит 4 400 000. Если платите в конверте — риск доначисления обоих налогов за 3 года + 20% штраф."

ПРАВИЛА:
- Никогда не придумывайте ставки и статьи — только из базы знаний или достоверных данных.
- Если ситуация сложная — скажите честно и направьте к специалисту.
- Максимум 350 слов. Конкретно, по делу, без воды.
- В конце: _Для официального заключения обратитесь к лицензированному бухгалтеру._"""


def search_knowledge_base(query, n_results=5):
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
