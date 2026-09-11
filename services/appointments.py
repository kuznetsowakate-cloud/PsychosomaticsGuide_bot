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

_DATETIME_RE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s+(\d{1,2}):(\d{2})$"
)


# ── Разбор и форматирование даты/времени ───────────────────────────────────

def parse_appointment_datetime(
    text: str, now: datetime | None = None,
) -> datetime | None:
    """Парсит «15.09 14:00» / «15.09.2026 14:00» (время — МСК).

    Если год не указан и дата с ним уже в прошлом — переносим на
    следующий год. Возвращает None, если распознать не удалось или
    результат всё равно в прошлом.
    """
    match = _DATETIME_RE.match(text.strip())
    if not match:
        return None

    day, month, year_raw, hour, minute = match.groups()
    now = now or datetime.now(MOSCOW_TZ)

    if year_raw:
        year = int(year_raw)
        if year < 100:
            year += 2000
    else:
        year = now.year

    try:
        dt = datetime(
            year, int(month), int(day), int(hour), int(minute),
            tzinfo=MOSCOW_TZ,
        )
    except ValueError:
        return None

    if not year_raw and dt <= now:
        try:
            dt = dt.replace(year=year + 1)
        except ValueError:
            return None

    if dt <= now:
        return None

    return dt


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
