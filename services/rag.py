"""
rag.py — RAG поиск по всей базе + агрегированный ответ через Claude.

Логика:
1. Запрос пользователя → embed (OpenAI)
2. Векторный поиск по всей таблице chunks (Supabase RPC)
3. Получаем топ-15 релевантных чанков из разных источников
4. Claude агрегирует все найденные данные в единый ответ
"""

import asyncio
import logging
from dataclasses import dataclass

import anthropic
from openai import AsyncOpenAI
from supabase import create_client

from config.settings import (
    OPENAI_API_KEY, EMBEDDING_MODEL,
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    SUPABASE_URL, SUPABASE_KEY,
)

logger = logging.getLogger(__name__)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Параметры поиска
TOP_K = 15              # сколько чанков берём
MIN_SIMILARITY = 0.35   # минимальный порог схожести


@dataclass
class SearchResult:
    chunk_id: int
    content: str
    source_id: int
    page_number: int | None
    tags: list[str]
    similarity: float


@dataclass
class RAGResponse:
    answer: str
    sources: list[str]       # названия источников
    chunks_used: list[int]   # id чанков


# ── Промпт для Claude ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — эксперт-консультант по психосоматике и психологии тела.
Твоя задача — давать точные, структурированные и поддерживающие ответы на основе предоставленных материалов из справочника.

Правила:
1. Используй ТОЛЬКО информацию из предоставленных фрагментов. Не выдумывай факты.
2. Если в материалах есть противоречия между авторами — укажи обе точки зрения.
3. Если информации недостаточно — честно скажи об этом.
4. Структурируй ответ: сначала психологическая причина, потом рекомендации.
5. Тон — профессиональный, тёплый, без медицинских диагнозов.
6. В конце всегда добавляй дисклеймер одной строкой.
7. Отвечай на русском языке.
8. Форматируй ответ ТОЛЬКО с помощью HTML-тегов Telegram: <b>жирный</b>, <i>курсив</i>, <code>код</code>.
   НЕ используй Markdown-символы: *, #, _, `, ---, >."""

AGGREGATION_PROMPT = """На основе следующих фрагментов из справочника по психосоматике дай исчерпывающий агрегированный ответ на запрос пользователя.

ЗАПРОС: {query}

МАТЕРИАЛЫ ИЗ СПРАВОЧНИКА:
{context}

Составь единый структурированный ответ, объединяющий информацию из всех релевантных фрагментов.
Если разные авторы дают разные объяснения — перечисли все точки зрения.

Используй ТОЛЬКО HTML-теги (<b>, <i>) для форматирования. Никаких символов Markdown.

Структура ответа (строго в таком виде):

🔍 <b>Краткий ответ</b>
[краткий ответ на запрос]

📌 <b>Психологические причины</b>
[основные причины из материалов, подзаголовки оформляй через <b>Название</b>]

💡 <b>Рекомендации</b>
[практические советы и упражнения]

⚠️ <i>Информация носит образовательный характер и не заменяет консультацию специалиста.</i>"""


# ── Основные функции ───────────────────────────────────────────────────────

async def embed_query(query: str) -> list[float]:
    """Получаем вектор для поискового запроса."""
    response = await openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query,
    )
    return response.data[0].embedding


async def search_chunks(embedding: list[float]) -> list[SearchResult]:
    """Векторный поиск по всей базе через Supabase RPC."""
    result = await asyncio.to_thread(
        lambda: supabase.rpc("search_chunks", {
            "query_embedding": embedding,
            "match_count": TOP_K,
            "min_similarity": MIN_SIMILARITY,
        }).execute()
    )

    if not result.data:
        return []

    return [
        SearchResult(
            chunk_id=row["id"],
            content=row["content"],
            source_id=row["source_id"],
            page_number=row.get("page_number"),
            tags=row.get("tags") or [],
            similarity=row["similarity"],
        )
        for row in result.data
    ]


async def get_source_names(chunk_ids: list[int]) -> dict[int, str]:
    """Получаем названия источников для найденных чанков."""
    result = await asyncio.to_thread(
        lambda: supabase.rpc("get_sources_for_chunks", {
            "chunk_ids": chunk_ids,
        }).execute()
    )

    if not result.data:
        return {}

    return {
        row["source_id"]: row["title"] +
        (f" ({row['author']})" if row.get("author") else "")
        for row in result.data
    }


def build_context(
    chunks: list[SearchResult],
    source_names: dict[int, str],
) -> str:
    """Формируем контекст для Claude из найденных чанков."""
    parts = []
    for i, chunk in enumerate(chunks, start=1):
        source = source_names.get(chunk.source_id, f"Источник {chunk.source_id}")
        page = f", стр. {chunk.page_number}" if chunk.page_number else ""
        relevance = f"{chunk.similarity:.0%}"
        parts.append(
            f"[Фрагмент {i} | {source}{page} | релевантность: {relevance}]\n"
            f"{chunk.content}"
        )
    return "\n\n---\n\n".join(parts)


async def generate_answer(query: str, context: str, sources_line: str) -> str:
    """Асинхронный вызов Claude для генерации агрегированного ответа."""
    prompt = AGGREGATION_PROMPT.format(
        query=query,
        context=context,
        sources_line=sources_line,
    )

    message = await claude_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2500,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": prompt}],
    )

    return message.content[0].text


async def rag_search(query: str) -> RAGResponse:
    """
    Главная функция: принимает запрос, возвращает агрегированный ответ.
    """
    # 1. Векторизуем запрос
    logger.info("RAG: векторизация запроса...")
    embedding = await embed_query(query)

    # 2. Ищем похожие чанки по всей базе
    logger.info("RAG: поиск в базе...")
    chunks = await search_chunks(embedding)

    if not chunks:
        return RAGResponse(
            answer=(
                "К сожалению, по вашему запросу ничего не найдено в справочнике.\n\n"
                "Попробуйте переформулировать запрос или уточнить симптом/орган."
            ),
            sources=[],
            chunks_used=[],
        )

    logger.info("RAG: найдено %d чанков", len(chunks))

    # 3. Получаем названия источников
    chunk_ids = [c.chunk_id for c in chunks]
    source_names = await get_source_names(chunk_ids)

    # 4. Собираем уникальные источники для отображения
    unique_sources = list(dict.fromkeys(
        source_names.get(c.source_id, f"Источник {c.source_id}")
        for c in chunks
    ))
    sources_line = ", ".join(unique_sources)

    # 5. Строим контекст
    context = build_context(chunks, source_names)

    # 6. Генерируем агрегированный ответ через Claude
    logger.info("RAG: генерация ответа через Claude...")
    answer = await generate_answer(query, context, sources_line)

    return RAGResponse(
        answer=answer,
        sources=unique_sources,
        chunks_used=chunk_ids,
    )
