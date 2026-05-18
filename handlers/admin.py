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
        "/sources — список источников\n\n"
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
