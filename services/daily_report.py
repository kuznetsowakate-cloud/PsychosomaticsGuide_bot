"""Ежедневный отчёт для администратора: пользователи, запросы, расходы."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from supabase import create_client

from config.settings import (
    ADMIN_IDS, COST_PER_QUERY_RUB, SUPABASE_KEY, SUPABASE_URL,
)

logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))
REPORT_HOUR = 9  # 09:00 по Москве


def _build_report() -> str:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    now_msk = datetime.now(MOSCOW_TZ)

    # Начало сегодняшнего дня по МСК в UTC для фильтрации query_log
    today_start = now_msk.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).isoformat()

    users = (
        sb.table("users")
        .select("telegram_id, username, full_name, plan, subscribed_until, queries_today")
        .order("created_at")
        .execute()
        .data
    )

    # Запросы за сегодня (точно, из лога)
    today_log = (
        sb.table("query_log")
        .select("telegram_id")
        .gte("created_at", today_start)
        .execute()
        .data
    )
    today_per_user: dict[int, int] = {}
    for row in today_log:
        tid = row["telegram_id"]
        today_per_user[tid] = today_per_user.get(tid, 0) + 1

    # Всего запросов за всё время
    all_log = (
        sb.table("query_log")
        .select("telegram_id", count="exact")
        .execute()
    )
    total_all = all_log.count or 0

    all_per_user: dict[int, int] = {}
    for row in (all_log.data or []):
        tid = row["telegram_id"]
        all_per_user[tid] = all_per_user.get(tid, 0) + 1

    total_today = sum(today_per_user.values())
    cost_today = round(total_today * COST_PER_QUERY_RUB, 1)
    cost_all = round(total_all * COST_PER_QUERY_RUB, 1)
    date_str = now_msk.strftime("%d.%m.%Y")

    paid_count = sum(1 for u in users if u.get("plan", "free") != "free")
    free_count = len(users) - paid_count

    # Сортируем: сначала активные сегодня, потом по всем запросам
    sorted_users = sorted(
        users,
        key=lambda u: (
            -today_per_user.get(u["telegram_id"], 0),
            -all_per_user.get(u["telegram_id"], 0),
        ),
    )

    user_lines: list[str] = []
    for u in sorted_users:
        tid = u["telegram_id"]
        nick = f"@{u['username']}" if u.get("username") else f"id:{tid}"
        plan = u.get("plan", "free")
        subscribed_until = u.get("subscribed_until")
        q_today = today_per_user.get(tid, 0)
        q_total = all_per_user.get(tid, 0)

        if plan == "free":
            plan_str = "🆓"
        else:
            emoji = "🌟"
            if subscribed_until:
                until_dt = datetime.fromisoformat(subscribed_until)
                plan_str = f"{emoji}{until_dt.strftime('%d.%m')}"
            else:
                plan_str = emoji

        today_mark = f"<b>сег:{q_today}</b>" if q_today > 0 else f"сег:{q_today}"
        user_lines.append(f"{nick} | {plan_str} | {today_mark} всего:{q_total}")

    users_block = "\n".join(user_lines) if user_lines else "Пользователей нет."

    return (
        f"📊 <b>Статистика за {date_str}</b>\n"
        f"\n"
        f"👥 {len(users)} польз.  |  💳 платных: {paid_count}  |  🆓 free: {free_count}\n"
        f"📈 Сегодня: {total_today} зап. (~{cost_today} ₽)  |  Всего: {total_all} зап. (~{cost_all} ₽)\n"
        f"\n"
        f"<b>Пользователи:</b>\n"
        f"{users_block}\n"
        f"\n"
        f"💳 OpenAI: platform.openai.com → Billing\n"
        f"💳 Anthropic: console.anthropic.com → Billing"
    )


async def send_daily_report(bot) -> None:
    try:
        text = await asyncio.to_thread(_build_report)
        for admin_id in ADMIN_IDS:
            # Разбиваем если длиннее лимита Telegram
            if len(text) <= 4096:
                await bot.send_message(admin_id, text, parse_mode="HTML")
            else:
                parts = [text[i:i + 4000] for i in range(0, len(text), 4000)]
                for part in parts:
                    await bot.send_message(admin_id, part, parse_mode="HTML")
    except Exception as e:
        logger.error("Ошибка отправки ежедневного отчёта: %s", e)


async def daily_report_loop(bot) -> None:
    """Фоновая задача: отправляет отчёт каждый день в REPORT_HOUR:00 МСК."""
    while True:
        now = datetime.now(MOSCOW_TZ)
        next_report = now.replace(
            hour=REPORT_HOUR, minute=0, second=0, microsecond=0
        )
        if now >= next_report:
            next_report += timedelta(days=1)

        wait = (next_report - now).total_seconds()
        logger.info(
            "Следующий ежедневный отчёт через %.0f сек (%s МСК)",
            wait, next_report.strftime("%d.%m %H:%M"),
        )
        await asyncio.sleep(wait)
        await send_daily_report(bot)
