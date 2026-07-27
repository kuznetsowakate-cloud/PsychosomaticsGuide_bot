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


def _nick(telegram_id: int, info: dict) -> str:
    if info.get("username"):
        return f"@{info['username']}"
    return info.get("full_name") or f"id:{telegram_id}"


def _plan_str(info: dict) -> str:
    plan = info.get("plan", "free")
    if plan == "free":
        return "🆓"
    subscribed_until = info.get("subscribed_until")
    if subscribed_until:
        until_dt = datetime.fromisoformat(subscribed_until)
        return f"🌟{until_dt.strftime('%d.%m')}"
    return "🌟"


def _build_report() -> tuple[str, int]:
    """Возвращает (текст отчёта, кол-во запросов за сутки)."""
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    now_msk = datetime.now(MOSCOW_TZ)

    # Начало сегодняшнего дня по МСК в UTC для фильтрации query_log/payments
    today_start = now_msk.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).isoformat()

    # Общие счётчики — только count, без выгрузки всех строк
    total_users = (
        sb.table("users").select("telegram_id", count="exact")
        .limit(1).execute().count or 0
    )
    paid_users = (
        sb.table("users").select("telegram_id", count="exact")
        .neq("plan", "free").limit(1).execute().count or 0
    )
    free_users = total_users - paid_users

    total_all = (
        sb.table("query_log").select("id", count="exact")
        .limit(1).execute().count or 0
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

    total_today = sum(today_per_user.values())
    cost_today = round(total_today * COST_PER_QUERY_RUB, 1)
    cost_all = round(total_all * COST_PER_QUERY_RUB, 1)
    date_str = now_msk.strftime("%d.%m.%Y")

    # Подтягиваем инфо только по тем, кто сегодня пользовался ботом
    active_ids = list(today_per_user.keys())
    users_info: dict[int, dict] = {}
    if active_ids:
        rows = (
            sb.table("users")
            .select("telegram_id, username, full_name, plan, subscribed_until")
            .in_("telegram_id", active_ids)
            .execute()
            .data
        )
        users_info = {u["telegram_id"]: u for u in rows}

    active_sorted = sorted(active_ids, key=lambda tid: -today_per_user[tid])
    if active_sorted:
        active_block = "\n".join(
            f"{_nick(tid, users_info.get(tid, {}))} | "
            f"{_plan_str(users_info.get(tid, {}))} | "
            f"{today_per_user[tid]} зап."
            for tid in active_sorted
        )
    else:
        active_block = "Сегодня никто не пользовался ботом."

    # Реальные (не промо) платежи за сутки
    payments_today = (
        sb.table("payments")
        .select("telegram_id, plan, amount, paid_at")
        .eq("status", "paid")
        .gt("amount", 0)
        .gte("paid_at", today_start)
        .order("paid_at")
        .execute()
        .data
    )

    payments_block = ""
    if payments_today:
        payer_ids = {p["telegram_id"] for p in payments_today}
        missing_ids = [tid for tid in payer_ids if tid not in users_info]
        if missing_ids:
            rows = (
                sb.table("users")
                .select("telegram_id, username, full_name")
                .in_("telegram_id", missing_ids)
                .execute()
                .data
            )
            for u in rows:
                users_info[u["telegram_id"]] = u

        payment_lines = []
        for p in payments_today:
            info = users_info.get(p["telegram_id"], {})
            nick = _nick(p["telegram_id"], info)
            payment_lines.append(f"{nick} | {p['plan']} | {p['amount']} ₽")
        payments_lines = "\n".join(payment_lines)
        payments_block = f"\n\n💳 <b>Оплатили за сутки:</b>\n{payments_lines}"

    users_line = (
        f"👥 {total_users} польз.  |  💳 платных: {paid_users}  |  "
        f"🆓 free: {free_users}"
    )
    queries_line = (
        f"📈 Сегодня: {total_today} зап. (~{cost_today} ₽)  |  "
        f"Всего: {total_all} зап. (~{cost_all} ₽)"
    )

    text = (
        f"📊 <b>Статистика за {date_str}</b>\n"
        f"\n"
        f"{users_line}\n"
        f"{queries_line}\n"
        f"\n"
        f"<b>Активны за сутки:</b>\n"
        f"{active_block}"
        f"{payments_block}\n"
        f"\n"
        f"💳 OpenAI: platform.openai.com → Billing\n"
        f"💳 Anthropic: console.anthropic.com → Billing"
    )
    return text, total_today


async def send_daily_report(bot) -> None:
    try:
        text, total_today = await asyncio.to_thread(_build_report)
        if total_today == 0:
            logger.info(
                "Ежедневный отчёт пропущен — за сутки не было запросов"
            )
            return
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
