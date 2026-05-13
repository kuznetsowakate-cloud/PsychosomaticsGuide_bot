"""Тесты для services/users.py — check_query_limit."""
import asyncio
from datetime import date
from unittest.mock import patch, MagicMock

from services.users import check_query_limit


def run(coro):
    return asyncio.run(coro)


def _user(plan="free", used=0, reset_date=None, telegram_id=1):
    return {
        "telegram_id": telegram_id,
        "plan": plan,
        "queries_today": used,
        "queries_reset_at": reset_date or date.today().isoformat(),
    }


def test_free_plan_within_limit():
    can, remaining = run(check_query_limit(_user(used=1)))
    assert can is True
    assert remaining == 2


def test_free_plan_at_limit():
    can, remaining = run(check_query_limit(_user(used=3)))
    assert can is False
    assert remaining == 0


def test_free_plan_over_limit():
    can, remaining = run(check_query_limit(_user(used=5)))
    assert can is False
    assert remaining == 0


def test_basic_plan_high_limit():
    can, remaining = run(check_query_limit(_user(plan="basic", used=100)))
    assert can is True


def test_resets_on_new_day():
    user = _user(used=3, reset_date="2000-01-01")
    mock_sb = MagicMock()
    mock_sb.table.return_value.update.return_value.eq.return_value.execute.return_value = None
    with patch("services.users.supabase", mock_sb):
        can, remaining = run(check_query_limit(user))
    assert can is True
    assert user["queries_today"] == 0


def test_remaining_decreases_with_usage():
    _, r1 = run(check_query_limit(_user(used=0)))
    _, r2 = run(check_query_limit(_user(used=1)))
    _, r3 = run(check_query_limit(_user(used=2)))
    assert r1 > r2 > r3
