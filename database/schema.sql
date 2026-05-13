-- ============================================================
-- PsychosomaticsGuide Bot — Supabase Schema
-- Выполнить в Supabase SQL Editor
-- ============================================================

-- Включаем расширение для векторного поиска
create extension if not exists vector;

-- ============================================================
-- ИСТОЧНИКИ (книги, PDF)
-- ============================================================
create table if not exists sources (
    id              bigserial primary key,
    title           text not null,
    author          text,
    filename        text,
    tags            text[],          -- теги: ['луиза хей', 'органы', 'позвоночник']
    is_active       boolean default true,
    google_drive_id text unique,     -- ID файла в Google Drive (для деdup)
    uploaded_at     timestamptz default now()
);

-- ============================================================
-- ЧАНКИ КОНТЕНТА (нарезанные фрагменты PDF)
-- ============================================================
create table if not exists chunks (
    id          bigserial primary key,
    source_id   bigint references sources(id) on delete cascade,
    content     text not null,           -- текст чанка
    embedding   vector(1536),            -- вектор OpenAI text-embedding-3-small
    page_number int,
    chunk_index int,                     -- порядковый номер в документе
    tags        text[],                  -- наследуется от source + ручные
    created_at  timestamptz default now()
);

-- Индекс для быстрого векторного поиска (HNSW)
create index if not exists chunks_embedding_idx
    on chunks using hnsw (embedding vector_cosine_ops)
    with (m = 16, ef_construction = 64);

-- ============================================================
-- ПОЛЬЗОВАТЕЛИ
-- ============================================================
create table if not exists users (
    telegram_id     bigint primary key,
    username        text,
    full_name       text,
    plan            text default 'free',   -- 'free' | 'basic' | 'pro'
    queries_today   int default 0,
    queries_reset_at date default current_date,
    subscribed_until timestamptz,
    created_at      timestamptz default now(),
    last_seen_at    timestamptz default now()
);

-- ============================================================
-- ИСТОРИЯ ЗАПРОСОВ
-- ============================================================
create table if not exists query_log (
    id              bigserial primary key,
    telegram_id     bigint references users(telegram_id),
    query           text not null,
    response        text,
    chunks_used     bigint[],    -- id чанков, использованных для ответа
    created_at      timestamptz default now()
);

-- ============================================================
-- ПЛАТЕЖИ
-- ============================================================
create table if not exists payments (
    id              bigserial primary key,
    telegram_id     bigint references users(telegram_id),
    plan            text not null,
    amount          int not null,           -- в копейках
    currency        text default 'XTR',
    payment_id      text unique,            -- id от платёжной системы
    status          text default 'pending', -- 'pending' | 'paid' | 'failed'
    created_at      timestamptz default now(),
    paid_at         timestamptz
);

-- ============================================================
-- FSM СОСТОЯНИЯ (персистентное хранилище для aiogram)
-- ============================================================
create table if not exists fsm_states (
    key     text primary key,   -- bot_id:chat_id:user_id:destiny
    state   text,
    data    jsonb default '{}'
);

-- ============================================================
-- RPC ФУНКЦИЯ: векторный поиск по чанкам
-- ============================================================
create or replace function search_chunks(
    query_embedding vector(1536),
    match_count     int default 15,
    min_similarity  float default 0.3
)
returns table (
    id          bigint,
    content     text,
    source_id   bigint,
    page_number int,
    tags        text[],
    similarity  float
)
language sql stable
as $$
    select
        c.id,
        c.content,
        c.source_id,
        c.page_number,
        c.tags,
        1 - (c.embedding <=> query_embedding) as similarity
    from chunks c
    where c.embedding is not null
      and 1 - (c.embedding <=> query_embedding) >= min_similarity
    order by c.embedding <=> query_embedding
    limit match_count;
$$;

-- ============================================================
-- RPC ФУНКЦИЯ: атомарное увеличение счётчика запросов
-- ============================================================
create or replace function increment_user_query_count(user_telegram_id bigint)
returns void
language sql
as $$
    update users
    set queries_today = queries_today + 1
    where telegram_id = user_telegram_id;
$$;

-- ============================================================
-- RPC ФУНКЦИЯ: получить источники для найденных чанков
-- ============================================================
create or replace function get_sources_for_chunks(chunk_ids bigint[])
returns table (
    source_id bigint,
    title     text,
    author    text
)
language sql stable
as $$
    select distinct s.id, s.title, s.author
    from sources s
    join chunks c on c.source_id = s.id
    where c.id = any(chunk_ids)
      and s.is_active = true;
$$;
