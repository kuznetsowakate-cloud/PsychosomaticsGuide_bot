"""Работа с пользователями в Supabase."""

import logging
from datetime import date, datetime, timedelta, timezone

from supabase import create_client

from config.settings import SUPABASE_URL, SUPABASE_KEY, PLAN_LIMITS, PLAN_DAYS

logger = logging.getLogger(__name__)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_or_create_user(telegram_id: int, username: str = "",
                        full_name: str = "") -> dict:
    """Получаем или создаём пользователя."""
    result = supabase.table("users").select("*").eq(
        "telegram_id", telegram_id).execute()

    if result.data:
        user = result.data[0]
        # Обновляем last_seen
        supabase.table("users").update({
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
        }).eq("telegram_id", telegram_id).execute()
        return user

    # Создаём нового
    new_user = {
        "telegram_id": telegram_id,
        "username": username,
        "full_name": full_name,
        "plan": "free",
        "queries_today": 0,
        "queries_reset_at": date.today().isoformat(),
    }
    result = supabase.table("users").insert(new_user).execute()
    return result.data[0]


def check_query_limit(user: dict) -> tuple[bool, int]:
    """
    Проверяет лимит запросов.
    Возвращает (можно_делать_запрос, осталось_запросов).
    """
    plan = user.get("plan", "free")
    limit = PLAN_LIMITS.get(plan, 3)

    # Сбрасываем счётчик если новый день
    reset_date = user.get("queries_reset_at")
    today = date.today().isoformat()

    if reset_date != today:
        supabase.table("users").update({
            "queries_today": 0,
            "queries_reset_at": today,
        }).eq("telegram_id", user["telegram_id"]).execute()
        user["queries_today"] = 0

    used = user.get("queries_today", 0)
    remaining = max(0, limit - used)
    return remaining > 0, remaining


def increment_query_count(telegram_id: int) -> None:
    """Увеличиваем счётчик запросов пользователя."""
    result = supabase.table("users").select(
        "queries_today").eq("telegram_id", telegram_id).execute()
    if result.data:
        current = result.data[0].get("queries_today", 0)
        supabase.table("users").update({
            "queries_today": current + 1,
        }).eq("telegram_id", telegram_id).execute()


def save_query_log(telegram_id: int, query: str,
                   response: str, chunks_used: list[int]) -> None:
    """Сохраняем лог запроса."""
    try:
        supabase.table("query_log").insert({
            "telegram_id": telegram_id,
            "query": query,
            "response": response,
            "chunks_used": chunks_used,
        }).execute()
    except Exception as e:
        logger.error("Ошибка сохранения лога: %s", e)


def activate_subscription(telegram_id: int, plan: str,
                           payment_id: str, amount: int) -> None:
    """Активируем подписку пользователя."""
    days = PLAN_DAYS.get(plan, 30)
    until = (datetime.now(timezone.utc) +
             timedelta(days=days)).isoformat()

    supabase.table("users").update({
        "plan": plan,
        "subscribed_until": until,
    }).eq("telegram_id", telegram_id).execute()

    supabase.table("payments").insert({
        "telegram_id": telegram_id,
        "plan": plan,
        "amount": amount,
        "payment_id": payment_id,
        "status": "paid",
        "paid_at": datetime.now(timezone.utc).isoformat(),
    }).execute()

    logger.info("Подписка %s активирована для %d до %s",
                plan, telegram_id, until)
