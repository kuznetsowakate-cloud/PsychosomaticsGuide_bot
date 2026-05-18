"""
admin.py — хендлеры для администратора.

Команды:
  /admin   — список команд
  /stats   — статистика базы
  /sources — список источников

Материалы загружаются автоматически через Google Drive.
"""

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command, BaseFilter
from aiogram.types import Message

from config.settings import ADMIN_IDS

logger = logging.getLogger(__name__)


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id in ADMIN_IDS


admin_router = Router()
admin_router.message.filter(IsAdmin())


# ── /admin ─────────────────────────────────────────────────────────────────

@admin_router.message(Command("admin"))
async def cmd_admin(message: Message):
    await message.answer(
        "🔧 <b>Панель администратора</b>\n\n"
        "Доступные команды:\n"
        "/stats — статистика базы\n"
        "/sources — список источников\n"
        "/resync_empty — повторно обработать файлы Drive без чанков\n\n"
        "Материалы (PDF, JPEG, PNG) загружаются автоматически "
        "через Google Drive."
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


@admin_router.message(Command("resync_empty"))
async def cmd_resync_empty(message: Message):
    """Находит источники из Drive без чанков и повторно их обрабатывает."""
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    from services.drive_sync import sync_once
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    def _find_empty_sources():
        sources = sb.table("sources").select(
            "id, title, filename, google_drive_id"
        ).eq("is_active", True).not_.is_(
            "google_drive_id", "null"
        ).execute()
        if not sources.data:
            return []
        source_ids = [s["id"] for s in sources.data]
        chunks_result = sb.table("chunks").select(
            "source_id"
        ).in_("source_id", source_ids).execute()
        ids_with_chunks = {row["source_id"] for row in chunks_result.data}
        return [s for s in sources.data if s["id"] not in ids_with_chunks]

    status_msg = await message.answer("🔍 Ищу источники из Drive без чанков...")
    empty = await asyncio.to_thread(_find_empty_sources)

    if not empty:
        await status_msg.edit_text(
            "✅ Все источники из Drive содержат чанки. "
            "Повторная обработка не нужна."
        )
        return

    names = "\n".join(f"• {s['title']} ({s['filename']})" for s in empty)
    await status_msg.edit_text(
        f"📋 Найдено {len(empty)} источников без чанков:\n\n{names}\n\n"
        f"⏳ Удаляю записи и запускаю повторную загрузку..."
    )

    def _delete_sources(ids):
        for sid in ids:
            sb.table("sources").delete().eq("id", sid).execute()

    await asyncio.to_thread(_delete_sources, [s["id"] for s in empty])

    result = await sync_once(bot=message.bot)

    errors_text = (
        "\n\n❌ Ошибки:\n" + "\n".join(result["errors"])
        if result["errors"] else ""
    )
    await status_msg.edit_text(
        f"✅ <b>Готово</b>\n\n"
        f"📦 Загружено: {result['new']} из {len(empty)}"
        f"{errors_text}"
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
