"""Тесты для services/appointments.py — парсинг даты и рендер шаблонов."""
from datetime import datetime

import pytest

from config.settings import MOSCOW_TZ
from services.appointments import (
    format_when, parse_appointment_datetime, parse_appointment_input,
    render_template,
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


def test_parse_input_with_client_name():
    dt, name = parse_appointment_input("15.09 14:00 Анна Иванова", now=NOW)
    assert dt == datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    assert name == "Анна Иванова"


def test_parse_input_without_client_name():
    dt, name = parse_appointment_input("15.09.2026 14:00", now=NOW)
    assert dt == datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    assert name is None


def test_parse_input_invalid():
    assert parse_appointment_input("Анна 15.09", now=NOW) == (None, None)


@pytest.mark.parametrize("text", [
    "15.09 14:00",
    "15.09 14.00",
    "15.09 14-00",
    "15.09 в 14:00",
    "15.09, 14:00",
    "15/09 14:00",
    "15-09 14:00",
    "15.09.26 14:00",
    "15.09.2026 14.00",
    "15 сентября 14:00",
    "15 Сентября в 14.00",
    "15 сент 14:00",
    "15 сен. 2026 14:00",
    "14:00 15.09",
    "в 14.00 15.09",
    "15.09 14",
    "15.09 в 14 ч",
    "15.09   14:00",
])
def test_parse_input_formats(text):
    dt, name = parse_appointment_input(text, now=NOW)
    assert dt == datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    assert name is None


@pytest.mark.parametrize("text", [
    "15.09 14.00 Анна Иванова",
    "15.09, 14:00, Анна Иванова",
    "15 сентября в 14 Анна Иванова",
    "14.00 15.09 — Анна Иванова",
])
def test_parse_input_formats_with_name(text):
    dt, name = parse_appointment_input(text, now=NOW)
    assert dt == datetime(2026, 9, 15, 14, 0, tzinfo=MOSCOW_TZ)
    assert name == "Анна Иванова"


def test_parse_input_relative_dates():
    dt, name = parse_appointment_input("завтра в 14.30 Анна", now=NOW)
    assert dt == datetime(2026, 9, 12, 14, 30, tzinfo=MOSCOW_TZ)
    assert name == "Анна"

    dt, _ = parse_appointment_input("сегодня 18:00", now=NOW)
    assert dt == datetime(2026, 9, 11, 18, 0, tzinfo=MOSCOW_TZ)

    dt, _ = parse_appointment_input("послезавтра 9.15", now=NOW)
    assert dt == datetime(2026, 9, 13, 9, 15, tzinfo=MOSCOW_TZ)


def test_parse_input_today_in_past_rejected():
    assert parse_appointment_input("сегодня 08:00", now=NOW) == (None, None)


def test_parse_input_invalid_time_rejected():
    assert parse_appointment_input("15.09 25:00", now=NOW) == (None, None)
    assert parse_appointment_input("15.09 14:75", now=NOW) == (None, None)
