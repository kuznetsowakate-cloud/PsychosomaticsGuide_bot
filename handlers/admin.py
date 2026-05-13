"""
admin.py — хендлеры для администратора.

Позволяет загружать PDF прямо через Telegram:
1. Отправить PDF файл боту
2. Бот спрашивает название, автора, теги
3. Файл сохраняется и обрабатывается автоматически
"""

import asyncio
import logging
import os
import tempfile

from aiogram import Router, F
from aiogram.filters import Command, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from config.settings import ADMIN_IDS
from services.ingest import ingest_pdf

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in ADMIN_IDS


admin_router = Router()
admin_router.message.filter(IsAdmin())


class AdminUpload(StatesGroup):
    waiting_title = State()
    waiting_author = State()
    waiting_tags = State()
    confirming = State()


# ── /admin ─────────────────────────────────────────────────────────────────

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    await message.answer(
        "🔧 <b>Панель администратора</b>\n\n"
        "Доступные команды:\n"
        "/stats — статистика базы\n"
        "/sources — список источников\n\n"
        "Отправьте PDF файл для загрузки в справочник."
    )


@admin_router.message(Command("stats"))
async def cmd_stats(message: Message):
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    def _get_stats():
        sources = len(sb.table("sources").select("id").execute().data)
        chunks = len(sb.table("chunks").select("id").execute().data)
        users = len(sb.table("users").select("telegram_id").execute().data)
        queries = len(sb.table("query_log").select("id").execute().data)
        return sources, chunks, users, queries

    sources_count, chunks_count, users_count, queries_count = (
        await asyncio.to_thread(_get_stats)
    )

    await message.answer(
        f"📊 <b>Статистика</b>\n\n"
        f"📚 Источников: {sources_count}\n"
        f"🧩 Чанков: {chunks_count}\n"
        f"👥 Пользователей: {users_count}\n"
        f"🔍 Запросов всего: {queries_count}"
    )


@admin_router.message(Command("sources"))
async def cmd_sources(message: Message):
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    result = await asyncio.to_thread(
        lambda: sb.table("sources").select(
            "id, title, author, is_active").order("id").execute()
    )

    if not result.data:
        await message.answer("База источников пуста.")
        return

    lines = []
    for s in result.data:
        status = "✅" if s["is_active"] else "❌"
        lines.append(
            f"{status} [{s['id']}] {s['title']}"
            + (f" — {s['author']}" if s.get("author") else "")
        )

    await message.answer(
        "📚 <b>Источники в базе:</b>\n\n" + "\n".join(lines))


# ── Загрузка PDF через бота ────────────────────────────────────────────────

@admin_router.message(F.document)
async def on_document(message: Message, state: FSMContext):
    doc = message.document
    if not doc.file_name.lower().endswith(".pdf"):
        await message.answer("Поддерживаются только PDF файлы.")
        return

    # Сохраняем file_id для последующего скачивания
    await state.update_data(
        file_id=doc.file_id,
        file_name=doc.file_name,
    )
    await state.set_state(AdminUpload.waiting_title)
    await message.answer(
        f"📄 Получен файл: <b>{doc.file_name}</b>\n\n"
        "Введите название источника (книга/автор):\n"
        "<i>Или отправьте /skip для использования имени файла</i>"
    )


@admin_router.message(AdminUpload.waiting_title)
async def on_title(message: Message, state: FSMContext):
    data = await state.get_data()
    title = (message.text or "").strip()

    if message.text == "/skip" or not title:
        title = data.get("file_name", "").replace(".pdf", "")

    await state.update_data(title=title)
    await state.set_state(AdminUpload.waiting_author)
    await message.answer(
        f"Название: <b>{title}</b>\n\n"
        "Введите имя автора:\n"
        "<i>/skip — без автора</i>"
    )


@admin_router.message(AdminUpload.waiting_author)
async def on_author(message: Message, state: FSMContext):
    author = (message.text or "").strip()
    if message.text == "/skip":
        author = ""

    await state.update_data(author=author)
    await state.set_state(AdminUpload.waiting_tags)
    await message.answer(
        "Введите теги через запятую:\n"
        "<i>Например: луиза хей, органы, эмоции</i>\n"
        "<i>/skip — без тегов</i>"
    )


@admin_router.message(AdminUpload.waiting_tags)
async def on_tags(message: Message, state: FSMContext):
    tags_raw = (message.text or "").strip()
    if message.text == "/skip":
        tags = []
    else:
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

    await state.update_data(tags=tags)
    data = await state.get_data()

    await state.set_state(AdminUpload.confirming)
    await message.answer(
        f"📋 <b>Подтверждение загрузки:</b>\n\n"
        f"📄 Файл: {data['file_name']}\n"
        f"📚 Название: {data['title']}\n"
        f"✍️ Автор: {data.get('author') or '—'}\n"
        f"🏷️ Теги: {', '.join(tags) or '—'}\n\n"
        "Загрузить? Отправьте <b>да</b> или <b>нет</b>"
    )


@admin_router.message(AdminUpload.confirming)
async def on_confirm(message: Message, state: FSMContext):
    answer = (message.text or "").strip().lower()

    if answer not in ("да", "yes", "y", "+"):
        await state.clear()
        await message.answer("Загрузка отменена.")
        return

    data = await state.get_data()
    await state.clear()

    status_msg = await message.answer("⏳ Скачиваю файл...")

    # Скачиваем PDF
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = os.path.join(tmpdir, data["file_name"])

        file = await message.bot.get_file(data["file_id"])
        await message.bot.download_file(file.file_path, file_path)

        await status_msg.edit_text("⏳ Обрабатываю PDF и загружаю в базу...")

        try:
            await ingest_pdf(
                file_path=file_path,
                title=data["title"],
                author=data.get("author", ""),
                tags=data.get("tags", []),
            )
            await status_msg.edit_text(
                f"✅ <b>Успешно загружено!</b>\n\n"
                f"📚 {data['title']} добавлен в справочник."
            )
        except Exception as e:
            logger.error("Ошибка загрузки PDF: %s", e)
            await status_msg.edit_text(
                f"❌ Ошибка при загрузке:\n<code>{e}</code>"
            )
