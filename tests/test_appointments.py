"""Тесты для services/appointments.py — парсинг даты и рендер шаблонов."""
from datetime import datetime

from config.settings import MOSCOW_TZ
from services.appointments import (
    format_when, parse_appointment_datetime, render_template,
)

NOW = datetime(2026, 9, 11, 10, 0, tzinfo=MOSCOW_TZ)


def test_parse_without_year_future_date():
    dt = parse_appointment_datetime("15.09 14:00", now=NOW)
    assert dt == datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)


def test_parse_without_year_past_date_rolls_to_next_year():
    dt = parse_appointment_datetime("10.09 09:00", now=NOW)
    assert dt == datetime(2027, 9, 10, 9, 0, tzinfo=MOSCOW_TZ)


def test_parse_with_explicit_year():
    dt = parse_appointment_datetime("15.09.2026 14:00", now=NOW)
    assert dt == datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)


def test_parse_explicit_past_year_rejected():
    dt = parse_appointment_datetime("15.09.2020 14:00", now=NOW)
    assert dt is None


def test_parse_garbage_returns_none():
    assert parse_appointment_datetime("не дата", now=NOW) is None


def test_parse_invalid_calendar_date_returns_none():
    assert parse_appointment_datetime("31.02 10:00", now=NOW) is None


def test_render_template_substitutes_placeholders():
    when = datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    result = render_template(
        "Здравствуйте, {client_name}! Встреча {date} в {time}.",
        "Иванова Мария", when,
    )
    assert result == (
        "Здравствуйте, Иванова Мария! Встреча 15.09.2026 в 14:00."
    )


def test_render_template_falls_back_on_broken_placeholder():
    broken = "Текст с { незакрытой скобкой"
    when = datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    assert render_template(broken, "Иванова Мария", when) == broken


def test_format_when():
    when = datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    assert format_when(when) == "15.09.2026 14:00"
