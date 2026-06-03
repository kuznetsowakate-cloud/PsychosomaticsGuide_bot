"""
rag.py — RAG поиск по всей базе + агрегированный ответ через Claude.

Логика:
1. Запрос пользователя → embed (OpenAI)
2. Векторный поиск по всей таблице chunks (Supabase RPC)
3. Получаем топ-15 релевантных чанков из разных источников
4. Claude агрегирует все найденные данные в единый ответ
"""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

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

CACHE_TTL_HOURS = 24    # время жизни кэша


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
    sources: list[str]                                    # названия источников
    chunks_used: list[int]                                # id чанков
    related_questions: list[str] = field(default_factory=list)  # смежные вопросы


# ── Промпт для Claude ──────────────────────────────────────────────────────

SYSTEM_PROMPT = """Ты — эксперт-консультант по психосоматике и психологии тела.
Твоя задача — давать точные, структурированные и поддерживающие ответы на основе предоставленных материалов из справочника.

Правила:
1. Используй ТОЛЬКО информацию из предоставленных фрагментов. Не выдумывай факты.
2. Если в материалах есть разные точки зрения — укажи обе.
3. Если информации недостаточно — честно скажи об этом.
4. Структурируй ответ: сначала психологическая причина, потом рекомендации.
5. Тон — профессиональный, тёплый, без медицинских диагнозов.
6. В конце всегда добавляй дисклеймер одной строкой.
7. Отвечай на русском языке.
8. Форматируй ответ ТОЛЬКО с помощью HTML-тегов Telegram: <b>жирный</b>, <i>курсив</i>, <code>код</code>.
   НЕ используй Markdown-символы: *, #, _, `, ---, >.
9. НИКОГДА не упоминай в тексте ответа названия книг, источников, авторов и номера фрагментов. Просто излагай информацию от своего лица."""

AGGREGATION_PROMPT = """На основе следующих фрагментов из справочника по психосоматике дай исчерпывающий агрегированный ответ на запрос пользователя.

ЗАПРОС: {query}

МАТЕРИАЛЫ ИЗ СПРАВОЧНИКА:
{context}

Составь единый структурированный ответ, объединяющий информацию из всех релевантных фрагментов.
Если в материалах есть разные точки зрения — перечисли их.
ВАЖНО: не упоминай в ответе названия источников, книг, авторов и номера фрагментов. Излагай информацию от своего лица, без ссылок на материалы.

Используй ТОЛЬКО HTML-теги (<b>, <i>) для форматирования. Никаких символов Markdown.

Структура ответа (строго в таком виде):

🔍 <b>Краткий ответ</b>
[краткий ответ на запрос]

📌 <b>Психологические причины</b>
[основные причины из материалов, подзаголовки оформляй через <b>Название</b>]

💡 <b>Рекомендации</b>
[практические советы и упражнения]

⚠️ <i>Информация носит образовательный характер и не заменяет консультацию специалиста.</i>"""


# ── Кэш запросов ──────────────────────────────────────────────────────────

def _query_hash(query: str) -> str:
    """Нормализуем запрос и возвращаем SHA-256 хэш."""
    normalized = " ".join(query.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


async def _get_cached(query: str) -> RAGResponse | None:
    """Ищем свежий кэш (< CACHE_TTL_HOURS). Возвращает None если не найдено."""
    q_hash = _query_hash(query)
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)
    ).isoformat()

    def _sync():
        return (
            supabase.table("query_cache")
            .select("answer, sources, chunks_used, id")
            .eq("query_hash", q_hash)
            .gte("created_at", cutoff)
            .limit(1)
            .execute()
        )

    result = await asyncio.to_thread(_sync)
    if not result.data:
        return None

    row = result.data[0]

    # Обновляем счётчик попаданий в фоне (не ждём)
    row_id = row["id"]
    asyncio.create_task(_increment_cache_hits(row_id))

    logger.info("RAG: кэш-хит для запроса (hash=%s…)", q_hash[:8])
    return RAGResponse(
        answer=row["answer"],
        sources=row["sources"] or [],
        chunks_used=row["chunks_used"] or [],
    )


async def _increment_cache_hits(row_id: int) -> None:
    try:
        await asyncio.to_thread(
            lambda: supabase.rpc(
                "increment_cache_hits", {"cache_id": row_id}
            ).execute()
        )
    except Exception:
        pass  # счётчик — некритичная операция


async def _save_to_cache(query: str, response: RAGResponse) -> None:
    """Сохраняем результат в кэш. При конфликте хэша — обновляем."""
    q_hash = _query_hash(query)

    def _sync():
        supabase.table("query_cache").upsert({
            "query_hash": q_hash,
            "query_text": query[:500],
            "answer": response.answer,
            "sources": response.sources,
            "chunks_used": response.chunks_used,
            "hits": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="query_hash").execute()

    try:
        await asyncio.to_thread(_sync)
        logger.info("RAG: ответ сохранён в кэш (hash=%s…)", q_hash[:8])
    except Exception as e:
        logger.warning("RAG: не удалось сохранить кэш: %s", e)


# ── Основные функции ───────────────────────────────────────────────────────

async def embed_query(query: str) -> list[float]:
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
        source = source_names.get(
            chunk.source_id, f"Источник {chunk.source_id}"
        )
        page = f", стр. {chunk.page_number}" if chunk.page_number else ""
        relevance = f"{chunk.similarity:.0%}"
        parts.append(
            f"[Фрагмент {i} | {source}{page} | релевантность: {relevance}]\n"
            f"{chunk.content}"
        )
    return "\n\n---\n\n".join(parts)


async def generate_answer(
    query: str,
    context: str,
    sources_line: str,
    history: list[dict] | None = None,
) -> str:
    """Асинхронный вызов Claude для генерации агрегированного ответа."""
    history_block = ""
    if history:
        pairs = []
        for exchange in history[-2:]:
            q = exchange.get("q", "")
            a = exchange.get("a", "")[:400]
            pairs.append(f"Пользователь: {q}\nАссистент: {a}…")
        history_block = (
            "КОНТЕКСТ ПРЕДЫДУЩЕГО ДИАЛОГА (учитывай при ответе):\n"
            + "\n\n".join(pairs)
            + "\n\n"
        )

    prompt = history_block + AGGREGATION_PROMPT.format(
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


async def generate_related_questions(query: str) -> list[str]:
    """Генерирует 2-3 смежных вопроса через Claude Haiku (быстро и дёшево)."""
    try:
        message = await claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": (
                    f"Запрос о психосоматике: «{query}»\n\n"
                    f"Напиши ровно 3 смежных вопроса по теме — кратко (до 60 символов каждый), "
                    f"на русском, строго по одному на строку, без нумерации и пояснений."
                ),
            }],
        )
        lines = [
            ln.strip().lstrip("•-–—*123456789. )")
            for ln in message.content[0].text.strip().splitlines()
            if ln.strip()
        ]
        return [q for q in lines if len(q) > 5][:3]
    except Exception:
        return []


async def rag_search(
    query: str, history: list[dict] | None = None,
) -> RAGResponse:
    """
    Главная функция: принимает запрос, возвращает агрегированный ответ.
    Кэширует результат для запросов без истории диалога.
    """
    use_cache = not history  # с историей кэш не применяем — ответ зависит от контекста

    if use_cache:
        cached = await _get_cached(query)
        if cached:
            cached.related_questions = await generate_related_questions(query)
            return cached

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

    # 6. Генерируем ответ и смежные вопросы параллельно
    logger.info("RAG: генерация ответа через Claude...")
    answer, related_questions = await asyncio.gather(
        generate_answer(query, context, sources_line, history),
        generate_related_questions(query),
    )

    response = RAGResponse(
        answer=answer,
        sources=unique_sources,
        chunks_used=chunk_ids,
        related_questions=related_questions,
    )

    # 7. Сохраняем в кэш (только свежие запросы без истории)
    if use_cache:
        asyncio.create_task(_save_to_cache(query, response))

    return response
