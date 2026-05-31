"""Ежедневный отчёт для администратора: пользователи, запросы, расходы."""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from supabase import create_client

from config.settings import (
    ADMIN_IDS, COST_PER_QUERY_RUB, OPENAI_API_KEY, SUPABASE_KEY, SUPABASE_URL,
)

logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))
REPORT_HOUR = 9  # 09:00 по Москве

_OPENAI_BALANCE_URL = "https://api.openai.com/v1/organization/balance"


def _fetch_openai_balance() -> str:
    """Запрашивает баланс OpenAI. Работает только с admin/organization ключом."""
    try:
        req = urllib.request.Request(
            _OPENAI_BALANCE_URL,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        available = (data.get("available") or [{}])[0]
        amount = available.get("amount", 0)
        currency = available.get("currency", "usd").upper()
        return f"${amount:.2f} {currency}"
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return "⚠️ нет доступа — нужен Organization Admin key"
        return f"⚠️ ошибка {e.code}"
    except Exception:
        return "⚠️ недоступно"


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

    # Формируем блок по каждому пользователю
    user_lines: list[str] = []
    total_today = sum(today_per_user.values())

    for u in users:
        tid = u["telegram_id"]
        nick = f"@{u['username']}" if u.get("username") else "—"
        name = u.get("full_name") or ""
        plan = u.get("plan", "free")
        subscribed_until = u.get("subscribed_until")
        q_today = today_per_user.get(tid, 0)
        q_total = all_per_user.get(tid, 0)

        if plan == "free":
            plan_str = "🆓 Free"
        else:
            emoji = "⭐" if plan == "basic" else "🌟"
            label = "Basic" if plan == "basic" else "Pro"
            if subscribed_until:
                until_dt = datetime.fromisoformat(subscribed_until)
                until_str = until_dt.strftime("%d.%m.%Y")
                plan_str = f"{emoji} {label} до {until_str}"
            else:
                plan_str = f"{emoji} {label}"

        header = f"<b>{nick}</b>" + (f" | {name}" if name else "")
        user_lines.append(
            f"{header}\n"
            f"  Тариф: {plan_str}\n"
            f"  Сегодня: {q_today} зап. | Всего: {q_total}"
        )

    cost_today = round(total_today * COST_PER_QUERY_RUB, 1)
    cost_all = round(total_all * COST_PER_QUERY_RUB, 1)
    date_str = now_msk.strftime("%d.%m.%Y")

    users_block = "\n\n".join(user_lines) if user_lines else "Пользователей нет."
    openai_balance = _fetch_openai_balance()

    return (
        f"📊 <b>Статистика за {date_str}</b>\n"
        f"\n"
        f"👥 <b>Пользователи ({len(users)})</b>\n"
        f"\n"
        f"{users_block}\n"
        f"\n"
        f"📈 <b>Итого сегодня</b>\n"
        f"Запросов: {total_today} (~{cost_today} ₽)\n"
        f"\n"
        f"📦 <b>За всё время</b>\n"
        f"Запросов: {total_all} (~{cost_all} ₽)\n"
        f"\n"
        f"💳 <b>Баланс API</b>\n"
        f"OpenAI: <b>{openai_balance}</b>\n"
        f"Anthropic: проверить вручную → console.anthropic.com/settings/billing\n"
        f"<i>(Anthropic не предоставляет API для проверки баланса)</i>"
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
