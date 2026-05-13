"""Тесты для services/ingest.py — чистые функции без сети."""
from services.ingest import split_into_chunks


def test_split_empty_input():
    assert split_into_chunks([]) == []


def test_split_returns_required_keys():
    chunks = split_into_chunks([(1, "word " * 200)])
    assert len(chunks) >= 1
    for c in chunks:
        assert "content" in c
        assert "page_number" in c
        assert "chunk_index" in c


def test_split_assigns_page_number():
    chunks = split_into_chunks([(7, "word " * 200)])
    assert chunks[0]["page_number"] == 7


def test_split_chunk_index_sequential():
    chunks = split_into_chunks([(1, "word " * 2000)])
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))


def test_split_content_not_empty():
    chunks = split_into_chunks([(1, "Hello world " * 100)])
    assert all(c["content"].strip() for c in chunks)


def test_split_multiple_pages_covered():
    pages = [(i, "word " * 300) for i in range(1, 4)]
    chunks = split_into_chunks(pages)
    page_nums = {c["page_number"] for c in chunks}
    assert page_nums <= {1, 2, 3}
