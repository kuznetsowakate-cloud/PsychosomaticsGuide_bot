"""Тесты лимита записей клиентов на бесплатном тарифе."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import FREE_APPOINTMENTS_PER_MONTH, MOSCOW_TZ
from handlers import user as user_handlers
from services import appointments


def run(coro):
    return asyncio.run(coro)


def _actor(telegram_id=1):
    return SimpleNamespace(id=telegram_id, username="u", full_name="U")


def _left(plan, used, telegram_id=1):
    with patch.object(
        user_handlers, "_get_user_for",
        AsyncMock(return_value={"plan": plan}),
    ), patch.object(
        user_handlers, "count_appointments_this_month",
        AsyncMock(return_value=used),
    ):
        return run(user_handlers._appointments_left(_actor(telegram_id)))


def test_free_user_has_slots_left():
    assert _left("free", 2) == FREE_APPOINTMENTS_PER_MONTH - 2


def test_free_user_at_limit():
    assert _left("free", FREE_APPOINTMENTS_PER_MONTH) == 0
    assert _left("free", FREE_APPOINTMENTS_PER_MONTH + 3) == 0


def test_pro_user_unlimited():
    assert _left("pro", 100) is None


def test_admin_unlimited():
    # ADMIN_IDS=123 задан в conftest.py
    assert _left("free", 100, telegram_id=123) is None


def test_count_uses_start_of_moscow_month():
    query = MagicMock()
    for method in ("table", "select", "eq", "gte"):
        getattr(query, method).return_value = query
    query.execute.return_value = SimpleNamespace(count=4)

    now = datetime(2026, 10, 6, 12, 0, tzinfo=MOSCOW_TZ)
    with patch.object(appointments, "supabase", query):
        assert run(appointments.count_appointments_this_month(1, now)) == 4

    month_start = query.gte.call_args.args[1]
    assert datetime.fromisoformat(month_start) == datetime(
        2026, 10, 1, 0, 0, tzinfo=MOSCOW_TZ,
    )
