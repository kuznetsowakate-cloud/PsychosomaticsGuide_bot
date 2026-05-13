"""Тесты для services/rag.py — чистые функции без сети."""
from services.rag import build_context, SearchResult


def _chunk(chunk_id=1, content="text", source_id=10,
           page=1, similarity=0.9) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        content=content,
        source_id=source_id,
        page_number=page,
        tags=[],
        similarity=similarity,
    )


def test_build_context_contains_content():
    ctx = build_context([_chunk(content="психосоматика")], {10: "Книга"})
    assert "психосоматика" in ctx


def test_build_context_contains_source_name():
    ctx = build_context([_chunk(source_id=1)], {1: "Луиза Хей"})
    assert "Луиза Хей" in ctx


def test_build_context_contains_page_number():
    ctx = build_context([_chunk(page=42)], {10: "Книга"})
    assert "стр. 42" in ctx


def test_build_context_no_page_when_none():
    chunk = SearchResult(
        chunk_id=1, content="text", source_id=1,
        page_number=None, tags=[], similarity=0.5,
    )
    ctx = build_context([chunk], {1: "Книга"})
    assert "стр." not in ctx


def test_build_context_unknown_source_fallback():
    ctx = build_context([_chunk(source_id=99)], {})
    assert "Источник 99" in ctx


def test_build_context_similarity_percentage():
    ctx = build_context([_chunk(similarity=0.75)], {10: "Книга"})
    assert "75%" in ctx


def test_build_context_multiple_chunks_separated():
    chunks = [_chunk(chunk_id=i, content=f"chunk{i}") for i in range(3)]
    ctx = build_context(chunks, {10: "Книга"})
    assert ctx.count("---") == 2
