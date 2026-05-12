"""
ingest.py — загрузка PDF в Supabase с векторизацией.

Использование:
    python -m services.ingest --file pdfs/book.pdf --title "Луиза Хей" --author "Луиза Хей" --tags "луиза хей,органы"
    python -m services.ingest --folder pdfs/          # все PDF из папки
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import pdfplumber
import tiktoken
from openai import AsyncOpenAI
from supabase import create_client

from config.settings import (
    OPENAI_API_KEY, EMBEDDING_MODEL,
    SUPABASE_URL, SUPABASE_KEY,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Параметры нарезки
CHUNK_TOKENS = 500      # размер чанка в токенах
OVERLAP_TOKENS = 80     # перекрытие между чанками
BATCH_SIZE = 20         # сколько чанков векторизуем за раз


def extract_text_from_pdf(path: str) -> list[tuple[int, str]]:
    """Возвращает список (номер_страницы, текст)."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                pages.append((i, text))
    return pages


def split_into_chunks(
    pages: list[tuple[int, str]],
    chunk_tokens: int = CHUNK_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> list[dict]:
    """
    Нарезает текст на чанки с перекрытием.
    Возвращает список {'content': str, 'page_number': int, 'chunk_index': int}
    """
    enc = tiktoken.get_encoding("cl100k_base")
    chunks = []
    chunk_index = 0

    # Объединяем весь текст, сохраняя привязку к странице
    tokens_with_pages: list[tuple[int, int]] = []  # (token_id, page_num)
    for page_num, text in pages:
        page_tokens = enc.encode(text)
        for tok in page_tokens:
            tokens_with_pages.append((tok, page_num))

    total = len(tokens_with_pages)
    step = chunk_tokens - overlap_tokens
    pos = 0

    while pos < total:
        end = min(pos + chunk_tokens, total)
        slice_tokens = [t for t, _ in tokens_with_pages[pos:end]]
        slice_pages = [p for _, p in tokens_with_pages[pos:end]]

        text = enc.decode(slice_tokens)
        page_num = slice_pages[len(slice_pages) // 2]  # средняя страница

        chunks.append({
            "content": text,
            "page_number": page_num,
            "chunk_index": chunk_index,
        })
        chunk_index += 1
        pos += step

    return chunks


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Векторизуем батч текстов через OpenAI."""
    response = await openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


async def upload_source(
    title: str,
    author: str,
    filename: str,
    tags: list[str],
) -> int:
    """Создаём запись источника, возвращаем source_id."""
    result = supabase.table("sources").insert({
        "title": title,
        "author": author,
        "filename": filename,
        "tags": tags,
        "is_active": True,
    }).execute()
    return result.data[0]["id"]


async def upload_chunks(
    chunks: list[dict],
    source_id: int,
    source_tags: list[str],
) -> int:
    """Векторизуем и загружаем чанки в Supabase. Возвращает кол-во загруженных."""
    total = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i:i + BATCH_SIZE]
        texts = [c["content"] for c in batch]

        logger.info("Векторизация чанков %d-%d / %d ...",
                    i + 1, i + len(batch), len(chunks))
        embeddings = await embed_batch(texts)

        rows = []
        for chunk, embedding in zip(batch, embeddings):
            rows.append({
                "source_id": source_id,
                "content": chunk["content"],
                "embedding": embedding,
                "page_number": chunk["page_number"],
                "chunk_index": chunk["chunk_index"],
                "tags": source_tags,
            })

        supabase.table("chunks").insert(rows).execute()
        total += len(rows)
        logger.info("Загружено %d чанков (всего: %d)", len(rows), total)

    return total


async def ingest_pdf(
    file_path: str,
    title: str,
    author: str = "",
    tags: list[str] | None = None,
) -> None:
    tags = tags or []
    filename = Path(file_path).name

    logger.info("=== Начинаем обработку: %s ===", filename)

    # 1. Извлекаем текст
    logger.info("Извлекаем текст из PDF...")
    pages = extract_text_from_pdf(file_path)
    logger.info("Извлечено страниц с текстом: %d", len(pages))

    # 2. Нарезаем на чанки
    chunks = split_into_chunks(pages)
    logger.info("Нарезано чанков: %d", len(chunks))

    # 3. Создаём источник в БД
    source_id = await upload_source(title, author, filename, tags)
    logger.info("Источник создан, source_id=%d", source_id)

    # 4. Загружаем чанки с эмбеддингами
    total = await upload_chunks(chunks, source_id, tags)

    logger.info("=== Готово! Загружено чанков: %d ===", total)


async def ingest_folder(folder_path: str) -> None:
    """Загружает все PDF из папки, запрашивая метаданные интерактивно."""
    pdfs = list(Path(folder_path).glob("*.pdf"))
    if not pdfs:
        logger.warning("PDF файлы не найдены в %s", folder_path)
        return

    for pdf_path in pdfs:
        print(f"\nФайл: {pdf_path.name}")
        title = input("  Название (Enter = имя файла): ").strip() or pdf_path.stem
        author = input("  Автор: ").strip()
        tags_raw = input("  Теги через запятую: ").strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        await ingest_pdf(str(pdf_path), title, author, tags)


def main():
    parser = argparse.ArgumentParser(
        description="Загрузка PDF в Supabase для RAG")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="Путь к PDF файлу")
    group.add_argument("--folder", help="Папка с PDF файлами")

    parser.add_argument("--title", help="Название источника")
    parser.add_argument("--author", default="", help="Автор")
    parser.add_argument("--tags", default="",
                        help="Теги через запятую")

    args = parser.parse_args()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    if args.file:
        title = args.title or Path(args.file).stem
        asyncio.run(ingest_pdf(args.file, title, args.author, tags))
    else:
        asyncio.run(ingest_folder(args.folder))


if __name__ == "__main__":
    main()
