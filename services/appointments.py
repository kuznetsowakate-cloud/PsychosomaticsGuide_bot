"""
appointments.py — напоминания о клиентских записях.

Пользователь (психолог) вводит дату/время приёма и имя клиента.
Фоновый цикл reminders_loop дважды присылает психологу сообщение:
  1. за 24 часа до приёма — напоминание + текст для клиента
  2. через 24 часа после приёма — вопрос о самочувствии + текст для клиента

Текст для клиента психолог может настроить под себя (доступно на Pro),
иначе используется дефолтный шаблон из texts/messages.py.
"""

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from supabase import create_client

from config.settings import MOSCOW_TZ, SUPABASE_KEY, SUPABASE_URL
from texts.messages import (
    DEFAULT_FOLLOWUP_TEMPLATE, DEFAULT_REMINDER_TEMPLATE,
    FOLLOWUP_NOTIFY, REMINDER_NOTIFY,
)

logger = logging.getLogger(__name__)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

CHECK_INTERVAL_SECONDS = 15 * 60  # раз в 15 минут — точность не критична
# Не пытаемся досылать вопрос о самочувствии для встреч старше недели —
# такие записи обычно означают, что психолог давно не открывал бота.
FOLLOWUP_MAX_AGE_DAYS = 8

# Дата: «15.09», «15/09», «15-09», «15.09.2026», «15.09.26»,
# «15 сентября», «15 сент 2026», «сегодня», «завтра», «послезавтра».
_DATE = (
    r"(?:(?P<day>\d{1,2})[./-](?P<month>\d{1,2})"
    r"(?:[./-](?P<year>\d{4}|\d{2}))?"
    r"|(?P<wday>\d{1,2})\s+(?P<mword>[а-яё]{3,9})\.?(?:\s+(?P<wyear>\d{4}))?"
    r"|(?P<rel>сегодня|завтра|послезавтра))"
)
# Время: «14:00», «14.00», «14-00», «14», «14ч», «14 часов».
_TIME = (
    r"(?P<hour>\d{1,2})(?:[:.\-](?P<minute>\d{2}))?"
    r"(?:\s*(?:ч|час|часа|часов)\.?)?"
)
# Между датой и временем: пробел, запятая, «в».
_SEP = r"(?:\s*,\s*|\s+)(?:в\s+)?"
# Имя клиента после даты и времени (можно через запятую или тире).
_NAME = r"(?:\s*[,—–-]?\s+(?P<name>.+))?"

_DATETIME_PATTERNS = [
    re.compile(rf"^(?:в\s+)?{_DATE}{_SEP}{_TIME}{_NAME}$", re.IGNORECASE),
    re.compile(rf"^(?:в\s+)?{_TIME}{_SEP}{_DATE}{_NAME}$", re.IGNORECASE),
]

_MONTH_PREFIXES = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5,
    "июн": 6, "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11,
    "дек": 12,
}
_RELATIVE_DAYS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}


# ── Разбор и форматирование даты/времени ───────────────────────────────────

def _month_from_word(word: str) -> int | None:
    return _MONTH_PREFIXES.get(word.lower().replace("ё", "е")[:3])


def _build_datetime(groups: dict, now: datetime) -> datetime | None:
    """Собирает datetime из групп регулярки; None, если дата невалидна."""
    hour = int(groups["hour"])
    minute = int(groups["minute"] or 0)
    roll_year = False

    try:
        if groups["rel"]:
            base = now + timedelta(days=_RELATIVE_DAYS[groups["rel"].lower()])
            return base.replace(
                hour=hour, minute=minute, second=0, microsecond=0,
            )

        if groups["mword"]:
            day, month = int(groups["wday"]), _month_from_word(groups["mword"])
            if month is None:
                return None
            year_raw = groups["wyear"]
        else:
            day, month = int(groups["day"]), int(groups["month"])
            year_raw = groups["year"]

        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
        else:
            year, roll_year = now.year, True

        dt = datetime(year, month, day, hour, minute, tzinfo=MOSCOW_TZ)
        # Год не указан и дата уже прошла — значит, имеется в виду следующий
        if roll_year and dt <= now:
            dt = dt.replace(year=year + 1)
        return dt
    except ValueError:
        return None


def parse_appointment_input(
    text: str, now: datetime | None = None,
) -> tuple[datetime | None, str | None]:
    """Парсит «15.09 14:00 Анна» → (дата и время в МСК, имя клиента).

    Понимает разные варианты записи: «15.09 14.00», «15/09 в 14-00»,
    «15 сентября 14:00», «завтра в 14», «14:00 15.09» и т.п.
    Имя необязательно: для «15.09 14:00» вернётся (дата, None).
    Если распознать не удалось или время уже в прошлом — (None, None).
    """
    text = " ".join(text.split())
    now = now or datetime.now(MOSCOW_TZ)

    for pattern in _DATETIME_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        dt = _build_datetime(match.groupdict(), now)
        if dt and dt > now:
            name = (match.group("name") or "").strip() or None
            return dt, name

    return None, None


def parse_appointment_datetime(
    text: str, now: datetime | None = None,
) -> datetime | None:
    """То же, что parse_appointment_input, но возвращает только дату."""
    return parse_appointment_input(text, now)[0]


def to_moscow(value: str) -> datetime:
    """Приводит timestamptz из Supabase к datetime в МСК."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MOSCOW_TZ)


def format_when(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M")


def render_template(template: str, client_name: str, when_dt: datetime) -> str:
    """Подставляет {client_name}/{date}/{time}. При кривом шаблоне (лишние
    фигурные скобки) возвращает исходный текст как есть."""
    values = {
        "client_name": client_name,
        "date": when_dt.strftime("%d.%m.%Y"),
        "time": when_dt.strftime("%H:%M"),
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


# ── CRUD записей ────────────────────────────────────────────────────────────

async def create_appointment(
    telegram_id: int, client_name: str, appointment_at: datetime,
) -> int:
    def _sync():
        return supabase.table("client_appointments").insert({
            "telegram_id": telegram_id,
            "client_name": client_name,
            "appointment_at": appointment_at.isoformat(),
        }).execute()

    result = await asyncio.to_thread(_sync)
    return result.data[0]["id"]


async def list_upcoming_appointments(telegram_id: int) -> list[dict]:
    def _sync():
        now_iso = datetime.now(timezone.utc).isoformat()
        return (
            supabase.table("client_appointments")
            .select("id, client_name, appointment_at")
            .eq("telegram_id", telegram_id)
            .gt("appointment_at", now_iso)
            .order("appointment_at")
            .execute()
        )

    result = await asyncio.to_thread(_sync)
    return result.data or []


async def delete_appointment(telegram_id: int, appointment_id: int) -> bool:
    """Удаляет запись, если она принадлежит этому пользователю."""
    def _sync():
        return (
            supabase.table("client_appointments")
            .delete()
            .eq("id", appointment_id)
            .eq("telegram_id", telegram_id)
            .execute()
        )

    result = await asyncio.to_thread(_sync)
    return bool(result.data)


async def set_template(
    telegram_id: int, field: str, text: str | None,
) -> None:
    """field: 'reminder_template' или 'followup_template'.

    text=None сбрасывает шаблон на дефолтный.
    """
    def _sync():
        supabase.table("users").update({field: text}).eq(
            "telegram_id", telegram_id
        ).execute()

    await asyncio.to_thread(_sync)


# ── Фоновый цикл напоминаний ────────────────────────────────────────────────

def _pending_reminders(now_utc: datetime) -> list[dict]:
    cutoff = (now_utc + timedelta(hours=24)).isoformat()
    result = (
        supabase.table("client_appointments")
        .select("id, telegram_id, client_name, appointment_at")
        .eq("reminder_sent", False)
        .gt("appointment_at", now_utc.isoformat())
        .lte("appointment_at", cutoff)
        .execute()
    )
    return result.data or []


def _pending_followups(now_utc: datetime) -> list[dict]:
    cutoff = (now_utc - timedelta(hours=24)).isoformat()
    min_at = (now_utc - timedelta(days=FOLLOWUP_MAX_AGE_DAYS)).isoformat()
    result = (
        supabase.table("client_appointments")
        .select("id, telegram_id, client_name, appointment_at")
        .eq("followup_sent", False)
        .gte("appointment_at", min_at)
        .lte("appointment_at", cutoff)
        .execute()
    )
    return result.data or []


def _user_template(telegram_id: int, field: str) -> str | None:
    result = (
        supabase.table("users").select(field)
        .eq("telegram_id", telegram_id).execute()
    )
    if not result.data:
        return None
    return result.data[0].get(field)


def _mark_sent(appointment_id: int, field: str) -> None:
    supabase.table("client_appointments").update({field: True}).eq(
        "id", appointment_id
    ).execute()


async def _send_pair(
    bot, telegram_id: int, notify_text: str, client_text: str,
) -> None:
    """Уведомление психологу + отдельным сообщением копируемый текст."""
    await bot.send_message(telegram_id, notify_text, parse_mode="HTML")
    copy_block = f"<code>{html.escape(client_text)}</code>"
    await bot.send_message(telegram_id, copy_block, parse_mode="HTML")


async def _process_reminders(bot) -> None:
    now_utc = datetime.now(timezone.utc)

    due = await asyncio.to_thread(_pending_reminders, now_utc)
    for appt in due:
        try:
            template = await asyncio.to_thread(
                _user_template, appt["telegram_id"], "reminder_template"
            )
            when_dt = to_moscow(appt["appointment_at"])
            client_text = render_template(
                template or DEFAULT_REMINDER_TEMPLATE,
                appt["client_name"], when_dt,
            )
            notify_text = REMINDER_NOTIFY.format(
                when=format_when(when_dt), client_name=appt["client_name"],
            )
            await _send_pair(
                bot, appt["telegram_id"], notify_text, client_text,
            )
            await asyncio.to_thread(_mark_sent, appt["id"], "reminder_sent")
        except Exception as e:
            logger.error(
                "Не удалось отправить напоминание (id=%s): %s",
                appt.get("id"), e,
            )

    due = await asyncio.to_thread(_pending_followups, now_utc)
    for appt in due:
        try:
            template = await asyncio.to_thread(
                _user_template, appt["telegram_id"], "followup_template"
            )
            when_dt = to_moscow(appt["appointment_at"])
            client_text = render_template(
                template or DEFAULT_FOLLOWUP_TEMPLATE,
                appt["client_name"], when_dt,
            )
            notify_text = FOLLOWUP_NOTIFY.format(
                when=format_when(when_dt), client_name=appt["client_name"],
            )
            await _send_pair(
                bot, appt["telegram_id"], notify_text, client_text,
            )
            await asyncio.to_thread(_mark_sent, appt["id"], "followup_sent")
        except Exception as e:
            logger.error(
                "Не удалось отправить фоллоу-ап (id=%s): %s",
                appt.get("id"), e,
            )


async def reminders_loop(bot) -> None:
    """Фоновая задача: раз в CHECK_INTERVAL_SECONDS проверяет записи."""
    logger.info(
        "Напоминания о клиентах: цикл запущен (интервал %d сек)",
        CHECK_INTERVAL_SECONDS,
    )
    while True:
        try:
            await _process_reminders(bot)
        except Exception as e:
            logger.error("Ошибка в цикле напоминаний: %s", e)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
