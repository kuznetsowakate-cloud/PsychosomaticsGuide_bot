"""Работа с пользователями в Supabase."""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from supabase import create_client

from config.settings import SUPABASE_URL, SUPABASE_KEY, PLAN_LIMITS, PLAN_DAYS, ADMIN_IDS

logger = logging.getLogger(__name__)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


async def get_or_create_user(telegram_id: int, username: str = "",
                             full_name: str = "") -> dict:
    """Получаем или создаём пользователя."""
    def _sync():
        result = supabase.table("users").select("*").eq(
            "telegram_id", telegram_id).execute()

        if result.data:
            user = result.data[0]
            supabase.table("users").update({
                "last_seen_at": datetime.now(timezone.utc).isoformat(),
            }).eq("telegram_id", telegram_id).execute()
            return user

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

    return await asyncio.to_thread(_sync)


async def check_query_limit(user: dict) -> tuple[bool, int]:
    """
    Проверяет лимит запросов.
    Возвращает (можно_делать_запрос, осталось_запросов).
    """
    if user.get("telegram_id") in ADMIN_IDS:
        return True, 999999

    plan = user.get("plan", "free")
    subscribed_until = user.get("subscribed_until")

    if plan != "free" and subscribed_until:
        expiry = datetime.fromisoformat(subscribed_until)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expiry:
            plan = "free"
            tid = user["telegram_id"]
            await asyncio.to_thread(
                lambda: supabase.table("users").update({
                    "plan": "free",
                    "subscribed_until": None,
                }).eq("telegram_id", tid).execute()
            )
            user["plan"] = "free"
            logger.info("Подписка истекла для %d, план сброшен на free", tid)

    limit = PLAN_LIMITS.get(plan, 3)

    reset_date = user.get("queries_reset_at")
    today = date.today().isoformat()

    if reset_date != today:
        tid = user["telegram_id"]
        await asyncio.to_thread(
            lambda: supabase.table("users").update({
                "queries_today": 0,
                "queries_reset_at": today,
            }).eq("telegram_id", tid).execute()
        )
        user["queries_today"] = 0

    used = user.get("queries_today", 0)
    remaining = max(0, limit - used)
    return remaining > 0, remaining


async def increment_query_count(telegram_id: int) -> None:
    """Увеличиваем счётчик запросов пользователя (атомарно через SQL)."""
    await asyncio.to_thread(
        lambda: supabase.rpc("increment_user_query_count", {
            "user_telegram_id": telegram_id,
        }).execute()
    )


async def save_query_log(telegram_id: int, query: str,
                         response: str, chunks_used: list[int]) -> None:
    """Сохраняем лог запроса."""
    def _sync():
        try:
            supabase.table("query_log").insert({
                "telegram_id": telegram_id,
                "query": query,
                "response": response,
                "chunks_used": chunks_used,
            }).execute()
        except Exception as e:
            logger.error("Ошибка сохранения лога: %s", e)

    await asyncio.to_thread(_sync)


async def accept_terms(telegram_id: int) -> None:
    """Фиксируем принятие пользовательского соглашения."""
    logger.info("accept_terms: saving for telegram_id=%d", telegram_id)
    try:
        result = await asyncio.to_thread(
            lambda: supabase.table("users").update({
                "terms_accepted": True,
                "terms_accepted_at": datetime.now(timezone.utc).isoformat(),
            }).eq("telegram_id", telegram_id).execute()
        )
        logger.info("accept_terms: done, rows=%s", result.data)
    except Exception as e:
        logger.warning(
            "accept_terms: timestamp update failed (%s), trying without timestamp", e
        )
        result = await asyncio.to_thread(
            lambda: supabase.table("users").update({
                "terms_accepted": True,
            }).eq("telegram_id", telegram_id).execute()
        )
        logger.info("accept_terms: fallback done, rows=%s", result.data)


async def activate_subscription(telegram_id: int, plan: str,
                                payment_id: str, amount: int,
                                days: int | None = None) -> None:
    """Активируем подписку пользователя."""
    _days = days if days is not None else PLAN_DAYS.get(plan, 30)
    until = (datetime.now(timezone.utc) + timedelta(days=_days)).isoformat()

    def _sync():
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

    await asyncio.to_thread(_sync)
    logger.info(
        "Подписка %s активирована для %d до %s", plan, telegram_id, until
    )


async def create_promo_code(
    code: str, plan: str, days: int, created_by: int,
) -> None:
    """Создаём промокод (только для администратора)."""
    await asyncio.to_thread(
        lambda: supabase.table("promo_codes").insert({
            "code": code.upper(),
            "plan": plan,
            "days": days,
            "created_by": created_by,
        }).execute()
    )


async def use_promo_code(telegram_id: int, code: str) -> dict:
    """
    Применяем промокод.
    Возвращает {"ok": True, "plan": str, "days": int}
    или {"ok": False, "error": "not_found" | "already_used"}.
    """
    def _sync():
        result = supabase.table("promo_codes").select("*").eq(
            "code", code.upper()
        ).execute()
        if not result.data:
            return {"ok": False, "error": "not_found"}
        promo = result.data[0]
        if promo["is_used"]:
            return {"ok": False, "error": "already_used"}
        supabase.table("promo_codes").update({
            "is_used": True,
            "used_by": telegram_id,
            "used_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", promo["id"]).execute()
        return {"ok": True, "plan": promo["plan"], "days": promo["days"]}

    result = await asyncio.to_thread(_sync)
    if not result["ok"]:
        return result

    await activate_subscription(
        telegram_id=telegram_id,
        plan=result["plan"],
        payment_id=f"promo_{code.upper()}",
        amount=0,
        days=result["days"],
    )
    return result


async def list_promo_codes() -> list[dict]:
    """Возвращает все промокоды для отображения в /promo_list."""
    result = await asyncio.to_thread(
        lambda: supabase.table("promo_codes").select("*").order(
            "created_at", desc=True
        ).execute()
    )
    return result.data or []
