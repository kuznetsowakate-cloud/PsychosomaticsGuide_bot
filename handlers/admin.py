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
    from services.daily_report import _build_report
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Данные по базе знаний (быстро)
    def _get_kb_stats():
        sources = sb.table("sources").select("id", count="exact").execute().count
        chunks = sb.table("chunks").select("id", count="exact").execute().count
        return sources, chunks

    wait_msg = await message.answer("⏳ Собираю статистику...")
    sources_count, chunks_count = await asyncio.to_thread(_get_kb_stats)
    report, _total_today = await asyncio.to_thread(_build_report)

    kb_line = f"📚 Источников: {sources_count} | 🧩 Чанков: {chunks_count}\n\n"
    full_text = kb_line + report

    await wait_msg.delete()
    if len(full_text) <= 4096:
        await message.answer(full_text, parse_mode="HTML")
    else:
        parts = [full_text[i:i + 4000] for i in range(0, len(full_text), 4000)]
        for part in parts:
            await message.answer(part, parse_mode="HTML")


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


@admin_router.message(Command("create_promo"))
async def cmd_create_promo(message: Message):
    """/create_promo <код> <план> <дней> — создать промокод."""
    from services.users import create_promo_code
    args = message.text.split()[1:]
    if len(args) != 3:
        await message.answer(
            "❌ Формат: <code>/create_promo КОД ПЛАН ДНИ</code>\n"
            "Пример: <code>/create_promo GIFT123 pro 30</code>\n\n"
            "Доступные планы: <code>pro</code>"
        )
        return

    code, plan, days_str = args
    if plan not in {"pro"}:
        await message.answer(
            f"❌ Неизвестный план: <code>{plan}</code>\n"
            f"Доступные: <code>pro</code>"
        )
        return

    try:
        days = int(days_str)
        if days <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Количество дней должно быть положительным числом."
        )
        return

    try:
        await create_promo_code(
            code=code, plan=plan, days=days,
            created_by=message.from_user.id,
        )
    except Exception as e:
        err = str(e).lower()
        if "unique" in err or "duplicate" in err:
            await message.answer(
                f"❌ Промокод <code>{code.upper()}</code> уже существует."
            )
        else:
            await message.answer(f"❌ Ошибка: {e}")
        return

    await message.answer(
        f"✅ <b>Промокод создан</b>\n\n"
        f"Код: <code>{code.upper()}</code>\n"
        f"Тариф: Pro 🌟\n"
        f"Срок: {days} дней"
    )


@admin_router.message(Command("delete_promo"))
async def cmd_delete_promo(message: Message):
    """/delete_promo <код> — удалить промокод."""
    args = message.text.split()[1:]
    if not args:
        await message.answer(
            "❌ Формат: <code>/delete_promo КОД</code>\n"
            "Пример: <code>/delete_promo GIFT123</code>"
        )
        return

    code = args[0].strip().upper()
    from supabase import create_client
    from config.settings import SUPABASE_URL, SUPABASE_KEY
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    result = await asyncio.to_thread(
        lambda: sb.table("promo_codes").select("*").eq("code", code).execute()
    )
    if not result.data:
        await message.answer(f"❌ Промокод <code>{code}</code> не найден.")
        return

    promo = result.data[0]
    await asyncio.to_thread(
        lambda: sb.table("promo_codes").delete().eq("code", code).execute()
    )

    revoked_user_id = None
    if promo["is_used"] and promo.get("used_by"):
        revoked_user_id = promo["used_by"]
        await asyncio.to_thread(
            lambda: sb.table("users").update({
                "plan": "free",
                "subscribed_until": None,
            }).eq("telegram_id", revoked_user_id).execute()
        )

    if revoked_user_id:
        await message.answer(
            f"🗑 Промокод <code>{code}</code> удалён.\n"
            f"Подписка пользователя id:{revoked_user_id} деактивирована."
        )
    else:
        await message.answer(
            f"🗑 Промокод <code>{code}</code> удалён (не был использован)."
        )


@admin_router.message(Command("promo_list"))
async def cmd_promo_list(message: Message):
    """Список всех промокодов."""
    from services.users import list_promo_codes
    promos = await list_promo_codes()

    if not promos:
        await message.answer("Промокодов ещё нет.")
        return

    lines = []
    for p in promos:
        status = "✅ использован" if p["is_used"] else "🟢 активен"
        used_info = (
            f" → id:{p['used_by']}" if p["is_used"] and p.get("used_by")
            else ""
        )
        lines.append(
            f"<code>{p['code']}</code> | "
            f"{p['plan']} {p['days']}д | {status}{used_info}"
        )

    await message.answer("🎟 <b>Промокоды</b>\n\n" + "\n".join(lines))


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
