import html
import json
import logging
import time
import traceback
from datetime import datetime, timezone

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery,
    LinkPreviewOptions,
)

from keyboards.inline import (
    kb_main_menu, kb_more_menu, kb_subscribe, kb_back, kb_after_answer,
    kb_chain_result, kb_terms_accept, kb_delete_confirm, kb_clients_menu,
    kb_clients_list, kb_appt_delete_confirm, kb_template_edit,
)
from services.appointments import (
    create_appointment, list_upcoming_appointments, delete_appointment,
    set_template, parse_appointment_input, format_when, to_moscow,
    count_appointments_this_month,
)
from services.chain_calc import calculate_chain, parse_chain_input
from services.rag import rag_search
from services.users import (
    get_or_create_user, check_query_limit,
    increment_query_count, save_query_log,
    activate_subscription, accept_terms, use_promo_code,
)
from config.settings import (
    PLAN_PRICES, ADMIN_IDS, PROVIDER_TOKEN, FREE_APPOINTMENTS_PER_MONTH,
)
from texts.messages import (
    WELCOME, HELP_TEXT, SEARCH_PROMPT, THINKING,
    LIMIT_REACHED, NO_RESULTS,
    PAYMENT_SUCCESS, CHAIN_PROMPT, CHAIN_PARSE_ERROR, CANCEL_TEXT,
    MY_PLAN_FREE, MY_PLAN_PAID,
    FEEDBACK_PROMPT, FEEDBACK_SENT, FEEDBACK_RECEIVED,
    TERMS_PROMPT, DELETE_PROMPT, DELETE_CONFIRMED, DELETE_ADMIN_NOTIFY,
    APPT_MENU_TEXT, APPT_DATETIME_PROMPT, APPT_DATETIME_ERROR,
    APPT_NAME_PROMPT, APPT_SAVED, APPT_SAVE_ERROR,
    CLIENTS_LIST_EMPTY, CLIENTS_LIST_HEADER,
    CLIENTS_LIST_FOOTER, DELETE_CLIENT_USAGE, DELETE_CLIENT_NOT_FOUND,
    DELETE_CLIENT_DONE, TEMPLATE_PRO_ONLY, TEMPLATE_INFO, TEMPLATE_SAVED,
    TEMPLATE_RESET, DEFAULT_REMINDER_TEMPLATE, DEFAULT_FOLLOWUP_TEMPLATE,
    TEMPLATE_CMD_HINT, TEMPLATE_EDIT_HINT, APPT_DELETE_CONFIRM, PROMO_PROMPT,
    APPT_LIMIT_REACHED, APPT_FREE_LEFT,
)

logger = logging.getLogger(__name__)
user_router = Router()


class UserStates(StatesGroup):
    waiting_query = State()
    waiting_chain_input = State()
    waiting_feedback = State()
    waiting_appointment_datetime = State()
    waiting_appointment_name = State()
    waiting_promo = State()
    waiting_template = State()


# ── /start ─────────────────────────────────────────────────────────────────

@user_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    if not user.get("terms_accepted"):
        await message.answer(
            TERMS_PROMPT, reply_markup=kb_terms_accept(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    else:
        await message.answer(WELCOME, reply_markup=kb_main_menu())


# ── /cancel ────────────────────────────────────────────────────────────────

@user_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=kb_main_menu())


# ── /help ──────────────────────────────────────────────────────────────────

@user_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=kb_back())


# ── /delete ────────────────────────────────────────────────────────────────

@user_router.message(Command("delete"))
async def cmd_delete(message: Message):
    await message.answer(DELETE_PROMPT, reply_markup=kb_delete_confirm())


@user_router.callback_query(F.data == "action_delete")
async def cb_delete(callback: CallbackQuery):
    await _remove_keyboard(callback)
    await callback.message.answer(
        DELETE_PROMPT, reply_markup=kb_delete_confirm(),
    )
    await callback.answer()


@user_router.callback_query(F.data == "delete_confirm")
async def cb_delete_confirm(callback: CallbackQuery):
    user = callback.from_user
    if user.username:
        user_info = f"@{user.username} (id: {user.id})"
    else:
        name = user.full_name or str(user.id)
        user_info = f"{name} (id: {user.id})"

    notify = DELETE_ADMIN_NOTIFY.format(
        user_info=user_info,
        telegram_id=user.id,
    )
    for admin_id in ADMIN_IDS:
        try:
            await callback.message.bot.send_message(
                admin_id, notify, parse_mode="HTML",
            )
        except Exception:
            pass

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(DELETE_CONFIRMED)
    await callback.answer()


# ── /my_plan + /subscribe (объединены) ────────────────────────────────────

async def _plan_text(telegram_id: int, username: str, full_name: str) -> str:
    """Возвращает текст экрана тарифа/подписки для данного пользователя."""
    user = await get_or_create_user(
        telegram_id=telegram_id, username=username, full_name=full_name,
    )
    await check_query_limit(user)

    plan = user.get("plan", "free")
    used_today = user.get("queries_today", 0)

    if plan == "free":
        appts = await count_appointments_this_month(telegram_id)
        return MY_PLAN_FREE.format(
            used=used_today,
            appts=min(appts, FREE_APPOINTMENTS_PER_MONTH),
            appts_limit=FREE_APPOINTMENTS_PER_MONTH,
        )

    subscribed_until = user.get("subscribed_until", "")
    until_str = "—"
    if subscribed_until:
        dt = datetime.fromisoformat(subscribed_until)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        until_str = dt.strftime("%d.%m.%Y")

    return MY_PLAN_PAID.format(until=until_str, used=used_today)


@user_router.message(Command("my_plan", "subscribe"))
async def cmd_my_plan(message: Message):
    text = await _plan_text(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    await message.answer(text, reply_markup=kb_subscribe())


# ── /chain ──────────────────────────────────────────────────────────────────

@user_router.message(Command("chain"))
async def cmd_chain(message: Message, state: FSMContext):
    await state.set_state(UserStates.waiting_chain_input)
    await message.answer(CHAIN_PROMPT)


# ── /promo ──────────────────────────────────────────────────────────────────

@user_router.message(Command("promo"))
async def cmd_promo(message: Message, state: FSMContext):
    args = message.text.split()[1:]
    if not args:
        await state.set_state(UserStates.waiting_promo)
        await message.answer(PROMO_PROMPT, reply_markup=kb_back())
        return

    await _apply_promo(message, state, args[0].strip())


@user_router.callback_query(F.data == "action_promo")
async def cb_promo(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_promo)
    await _remove_keyboard(callback)
    await callback.message.answer(PROMO_PROMPT, reply_markup=kb_back())
    await callback.answer()


@user_router.message(UserStates.waiting_promo, F.text)
async def on_promo_code(message: Message, state: FSMContext):
    await state.clear()
    await _apply_promo(message, state, message.text.strip())


async def _apply_promo(message: Message, state: FSMContext, code: str):
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    if not user.get("terms_accepted"):
        await state.set_data({"pending_promo": code})
        await message.answer(
            TERMS_PROMPT, reply_markup=kb_terms_accept(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    result = await use_promo_code(message.from_user.id, code)

    if not result["ok"]:
        if result["error"] == "not_found":
            await message.answer(
                "❌ Промокод не найден. Проверьте правильность ввода."
            )
        else:
            await message.answer("❌ Этот промокод уже был использован.")
        return

    await message.answer(
        f"🎉 <b>Промокод активирован!</b>\n\n"
        f"Тариф: <b>Pro 🌟</b>\n"
        f"Срок: <b>{result['days']} дней</b>\n\n"
        f"Теперь у вас безлимитный доступ к справочнику!",
        reply_markup=kb_main_menu(),
    )


# ── Напоминания клиентам ────────────────────────────────────────────────────

async def _appointments_left(actor) -> int | None:
    """Сколько записей клиентов ещё можно добавить в этом месяце.

    None — без ограничений (Pro или администратор).
    """
    user = await _get_user_for(actor)
    if actor.id in ADMIN_IDS or user.get("plan", "free") != "free":
        return None
    used = await count_appointments_this_month(actor.id)
    return max(FREE_APPOINTMENTS_PER_MONTH - used, 0)


async def _send_appt_limit_reached(message: Message) -> None:
    await message.answer(
        APPT_LIMIT_REACHED.format(limit=FREE_APPOINTMENTS_PER_MONTH),
        reply_markup=kb_subscribe(),
    )


async def _start_new_appointment(
    message: Message, state: FSMContext, actor,
) -> None:
    left = await _appointments_left(actor)
    if left == 0:
        await state.clear()
        await _send_appt_limit_reached(message)
        return

    prompt = APPT_DATETIME_PROMPT
    if left is not None:
        prompt += APPT_FREE_LEFT.format(
            left=left, limit=FREE_APPOINTMENTS_PER_MONTH,
        )
    await state.set_state(UserStates.waiting_appointment_datetime)
    await message.answer(prompt)


@user_router.message(Command("newclient"))
async def cmd_newclient(message: Message, state: FSMContext):
    await _start_new_appointment(message, state, message.from_user)


@user_router.callback_query(F.data == "action_clients")
async def cb_clients_menu(callback: CallbackQuery):
    await _remove_keyboard(callback)
    await callback.message.answer(
        APPT_MENU_TEXT, reply_markup=kb_clients_menu()
    )
    await callback.answer()


@user_router.callback_query(F.data == "action_newclient")
async def cb_newclient(callback: CallbackQuery, state: FSMContext):
    await _remove_keyboard(callback)
    await _start_new_appointment(
        callback.message, state, callback.from_user,
    )
    await callback.answer()


@user_router.message(UserStates.waiting_appointment_datetime)
async def on_appointment_datetime(message: Message, state: FSMContext):
    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    if not user.get("terms_accepted"):
        await state.clear()
        await message.answer(
            TERMS_PROMPT, reply_markup=kb_terms_accept(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    when_dt, client_name = parse_appointment_input(message.text or "")
    if not when_dt:
        await message.answer(APPT_DATETIME_ERROR)
        return  # остаёмся в том же состоянии — даём попробовать ещё раз

    if client_name:
        await _save_appointment(message, state, client_name, when_dt)
        return

    await state.set_data({"appointment_at": when_dt.isoformat()})
    await state.set_state(UserStates.waiting_appointment_name)
    await message.answer(APPT_NAME_PROMPT)


@user_router.message(UserStates.waiting_appointment_name)
async def on_appointment_name(message: Message, state: FSMContext):
    client_name = (message.text or "").strip()
    if not client_name:
        await message.answer(APPT_NAME_PROMPT)
        return

    data = await state.get_data()
    when_dt = datetime.fromisoformat(data["appointment_at"])
    await _save_appointment(message, state, client_name, when_dt)


async def _save_appointment(
    message: Message, state: FSMContext, client_name: str, when_dt: datetime,
) -> None:
    await state.clear()
    # Повторная проверка: лимит мог закончиться, пока вводили данные
    if await _appointments_left(message.from_user) == 0:
        await _send_appt_limit_reached(message)
        return

    try:
        await create_appointment(message.from_user.id, client_name, when_dt)
    except Exception:
        logger.exception(
            "Не удалось сохранить запись клиента для %d", message.from_user.id,
        )
        await message.answer(APPT_SAVE_ERROR)
        return

    await message.answer(
        APPT_SAVED.format(client_name=client_name, when=format_when(when_dt)),
    )


def _appt_label(appt: dict) -> str:
    when_dt = to_moscow(appt["appointment_at"])
    return f"{format_when(when_dt)} · {appt['client_name']}"


async def _clients_list(telegram_id: int):
    """Текст и клавиатура списка записей (с кнопками удаления)."""
    appointments = await list_upcoming_appointments(telegram_id)
    if not appointments:
        return CLIENTS_LIST_EMPTY, kb_clients_list([])

    lines = [_appt_label(appt) for appt in appointments]
    text = CLIENTS_LIST_HEADER + "\n".join(lines) + CLIENTS_LIST_FOOTER
    buttons = [(appt["id"], _appt_label(appt)) for appt in appointments]
    return text, kb_clients_list(buttons)


@user_router.message(Command("clients"))
async def cmd_clients(message: Message):
    text, kb = await _clients_list(message.from_user.id)
    await message.answer(text, reply_markup=kb)


@user_router.callback_query(F.data == "action_clients_list")
async def cb_clients_list(callback: CallbackQuery):
    text, kb = await _clients_list(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await _remove_keyboard(callback)
        await callback.message.answer(text, reply_markup=kb)
    await callback.answer()


@user_router.callback_query(F.data.startswith("appt_del_"))
async def cb_appt_delete(callback: CallbackQuery):
    appt_id = int(callback.data.removeprefix("appt_del_"))
    appointments = await list_upcoming_appointments(callback.from_user.id)
    appt = next((a for a in appointments if a["id"] == appt_id), None)
    if not appt:
        await callback.answer(DELETE_CLIENT_NOT_FOUND, show_alert=True)
        return
    await callback.message.edit_text(
        APPT_DELETE_CONFIRM.format(label=_appt_label(appt)),
        reply_markup=kb_appt_delete_confirm(appt_id),
    )
    await callback.answer()


@user_router.callback_query(F.data.startswith("appt_delok_"))
async def cb_appt_delete_ok(callback: CallbackQuery):
    appt_id = int(callback.data.removeprefix("appt_delok_"))
    deleted = await delete_appointment(callback.from_user.id, appt_id)
    await callback.answer(
        DELETE_CLIENT_DONE if deleted else DELETE_CLIENT_NOT_FOUND
    )
    text, kb = await _clients_list(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=kb)


@user_router.message(Command("delete_client"))
async def cmd_delete_client(message: Message):
    args = message.text.split()[1:]
    if not args or not args[0].isdigit():
        await message.answer(DELETE_CLIENT_USAGE)
        return

    deleted = await delete_appointment(message.from_user.id, int(args[0]))
    reply = DELETE_CLIENT_DONE if deleted else DELETE_CLIENT_NOT_FOUND
    await message.answer(reply)


# kind → (поле в users, заголовок, текст по умолчанию, команда)
_TEMPLATES = {
    "reminder": (
        "reminder_template", "Текст напоминания клиенту",
        DEFAULT_REMINDER_TEMPLATE, "/reminder_template",
    ),
    "followup": (
        "followup_template", "Текст вопроса о самочувствии",
        DEFAULT_FOLLOWUP_TEMPLATE, "/followup_template",
    ),
}


async def _get_user_for(actor) -> dict:
    user = await get_or_create_user(
        telegram_id=actor.id,
        username=actor.username or "",
        full_name=actor.full_name or "",
    )
    await check_query_limit(user)  # лениво сбрасывает истёкший план
    return user


def _template_info(user: dict, kind: str) -> str:
    field, title, default_text, _cmd = _TEMPLATES[kind]
    current = html.escape(user.get(field) or default_text)
    return TEMPLATE_INFO.format(title=title, current=current)


async def _handle_template_command(message: Message, kind: str) -> None:
    field, _title, _default, cmd = _TEMPLATES[kind]
    user = await _get_user_for(message.from_user)

    args = message.text.split(maxsplit=1)[1:]
    if not args:
        await message.answer(
            _template_info(user, kind) + TEMPLATE_CMD_HINT.format(cmd=cmd)
        )
        return

    if user.get("plan", "free") == "free":
        await message.answer(TEMPLATE_PRO_ONLY, reply_markup=kb_subscribe())
        return

    new_text = args[0].strip()
    if new_text.lower() == "reset":
        await set_template(message.from_user.id, field, None)
        await message.answer(TEMPLATE_RESET)
        return

    await set_template(message.from_user.id, field, new_text)
    await message.answer(TEMPLATE_SAVED)


@user_router.message(Command("reminder_template"))
async def cmd_reminder_template(message: Message):
    await _handle_template_command(message, "reminder")


@user_router.message(Command("followup_template"))
async def cmd_followup_template(message: Message):
    await _handle_template_command(message, "followup")


@user_router.callback_query(F.data.in_({"tpl_reminder", "tpl_followup"}))
async def cb_template(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.removeprefix("tpl_")
    user = await _get_user_for(callback.from_user)
    await _remove_keyboard(callback)

    if user.get("plan", "free") == "free":
        await callback.message.answer(_template_info(user, kind))
        await callback.message.answer(
            TEMPLATE_PRO_ONLY, reply_markup=kb_subscribe(),
        )
    else:
        await state.set_state(UserStates.waiting_template)
        await state.set_data({"template_kind": kind})
        await callback.message.answer(
            _template_info(user, kind) + TEMPLATE_EDIT_HINT,
            reply_markup=kb_template_edit(kind),
        )
    await callback.answer()


@user_router.callback_query(F.data.startswith("tpl_reset_"))
async def cb_template_reset(callback: CallbackQuery, state: FSMContext):
    kind = callback.data.removeprefix("tpl_reset_")
    if kind not in _TEMPLATES:
        await callback.answer()
        return
    await state.clear()
    await set_template(callback.from_user.id, _TEMPLATES[kind][0], None)
    await _remove_keyboard(callback)
    await callback.message.answer(
        TEMPLATE_RESET, reply_markup=kb_clients_menu(),
    )
    await callback.answer()


@user_router.message(UserStates.waiting_template, F.text)
async def on_template_text(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    kind = data.get("template_kind")
    if kind not in _TEMPLATES:
        return
    await set_template(
        message.from_user.id, _TEMPLATES[kind][0], message.text.strip(),
    )
    await message.answer(TEMPLATE_SAVED, reply_markup=kb_clients_menu())


# ── /feedback ───────────────────────────────────────────────────────────────

@user_router.message(Command("feedback"))
async def cmd_feedback(message: Message, state: FSMContext):
    await state.set_state(UserStates.waiting_feedback)
    await message.answer(FEEDBACK_PROMPT, reply_markup=kb_back())


@user_router.message(UserStates.waiting_feedback)
async def on_feedback(message: Message, state: FSMContext):
    await state.clear()
    text = message.text or ""
    if not text.strip():
        await message.answer(FEEDBACK_SENT, reply_markup=kb_main_menu())
        return

    user = message.from_user
    if user.username:
        user_info = f"@{user.username} (id: {user.id})"
    else:
        name = user.full_name or str(user.id)
        user_info = f"{name} (id: {user.id})"

    notify = FEEDBACK_RECEIVED.format(user_info=user_info, text=text)
    for admin_id in ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id, notify, parse_mode="HTML",
            )
        except Exception:
            pass

    await message.answer(FEEDBACK_SENT, reply_markup=kb_main_menu())


# ── Callbacks: принятие соглашения ────────────────────────────────────────

@user_router.callback_query(F.data == "terms_accept")
async def cb_accept_terms(callback: CallbackQuery, state: FSMContext):
    await callback.answer("✅ Условия приняты!")
    try:
        await accept_terms(callback.from_user.id)
    except Exception as e:
        logger.error("accept_terms failed for %d: %s", callback.from_user.id, e)
        await callback.message.answer(
            "⚠️ Не удалось сохранить согласие. Нажмите кнопку ещё раз.",
            reply_markup=kb_terms_accept(),
        )
        return

    data = await state.get_data()
    pending_promo = data.get("pending_promo")
    await state.clear()

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if pending_promo:
        result = await use_promo_code(callback.from_user.id, pending_promo)
        if result["ok"]:
            await callback.message.answer(
                f"🎉 <b>Промокод активирован!</b>\n\n"
                f"Тариф: <b>Pro 🌟</b>\n"
                f"Срок: <b>{result['days']} дней</b>\n\n"
                f"Теперь у вас безлимитный доступ к справочнику!",
                reply_markup=kb_main_menu(),
            )
        else:
            await callback.message.answer(WELCOME, reply_markup=kb_main_menu())
            error_text = (
                "❌ Промокод не найден."
                if result["error"] == "not_found"
                else "❌ Этот промокод уже был использован."
            )
            await callback.message.answer(error_text)
    else:
        await callback.message.answer(WELCOME, reply_markup=kb_main_menu())


# ── Callbacks: навигация ──────────────────────────────────────────────────

async def _remove_keyboard(callback: CallbackQuery) -> None:
    """Убираем кнопки, чтобы не было «зависших» клавиатур."""
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@user_router.callback_query(F.data == "action_back")
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _remove_keyboard(callback)
    await callback.message.answer(WELCOME, reply_markup=kb_main_menu())
    await callback.answer()


@user_router.callback_query(F.data == "action_more")
async def cb_more(callback: CallbackQuery):
    """Раскрываем подменю «Ещё» в том же сообщении."""
    try:
        await callback.message.edit_reply_markup(reply_markup=kb_more_menu())
    except Exception:
        await callback.message.answer(WELCOME, reply_markup=kb_more_menu())
    await callback.answer()


@user_router.callback_query(F.data == "action_menu_main")
async def cb_menu_main(callback: CallbackQuery):
    """Возврат из подменю «Ещё» к главному меню в том же сообщении."""
    try:
        await callback.message.edit_reply_markup(reply_markup=kb_main_menu())
    except Exception:
        await callback.message.answer(WELCOME, reply_markup=kb_main_menu())
    await callback.answer()


@user_router.callback_query(F.data == "action_help")
async def cb_help(callback: CallbackQuery):
    await _remove_keyboard(callback)
    await callback.message.answer(HELP_TEXT, reply_markup=kb_back())
    await callback.answer()


@user_router.callback_query(F.data == "action_subscribe")
async def cb_subscribe(callback: CallbackQuery):
    await _remove_keyboard(callback)
    text = await _plan_text(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username or "",
        full_name=callback.from_user.full_name or "",
    )
    await callback.message.answer(text, reply_markup=kb_subscribe())
    await callback.answer()


@user_router.callback_query(F.data == "action_search")
async def cb_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_query)
    await _remove_keyboard(callback)
    await callback.message.answer(SEARCH_PROMPT)
    await callback.answer()


@user_router.callback_query(F.data == "action_feedback")
async def cb_feedback(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_feedback)
    await _remove_keyboard(callback)
    await callback.message.answer(FEEDBACK_PROMPT, reply_markup=kb_back())
    await callback.answer()


# ── Оплата через Telegram Payments (ЮКасса) ───────────────────────────────

@user_router.callback_query(F.data.startswith("buy_"))
async def cb_buy(callback: CallbackQuery):
    plan = callback.data[4:]  # 'basic' или 'pro'
    price_rub = PLAN_PRICES.get(plan, 249)

    plan_names = {"pro": "Pro"}
    plan_name = plan_names.get(plan, plan)

    # YooKassa требует данные чека (фискализация).
    # need_email + send_email_to_provider: Telegram сам запрашивает email у плательщика.
    # vat_code=1 — без НДС; tax_system_code=2 — УСН (доходы).
    # Уточните эти коды у своего бухгалтера если система налогообложения другая.
    provider_data = json.dumps({
        "receipt": {
            "items": [
                {
                    "description": f"Подписка {plan_name} на 30 дней",
                    "quantity": "1.00",
                    "amount": {
                        "value": f"{price_rub}.00",
                        "currency": "RUB",
                    },
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "service",
                }
            ],
            "tax_system_code": 2,
        }
    }, ensure_ascii=False)

    await callback.message.answer_invoice(
        title=f"Подписка {plan_name}",
        description=(
            "Безлимитный доступ к справочнику по психосоматике на 30 дней"
        ),
        payload=f"sub_{plan}",
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(
            label=f"Подписка {plan_name}",
            amount=price_rub * 100,  # Telegram принимает копейки
        )],
        need_email=True,
        send_email_to_provider=True,
        provider_data=provider_data,
    )
    await callback.answer()


@user_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@user_router.message(F.successful_payment)
async def on_payment(message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload  # 'sub_basic' или 'sub_pro'
    plan = payload.replace("sub_", "")
    amount_rub = payment.total_amount // 100  # копейки → рубли

    await activate_subscription(
        telegram_id=message.from_user.id,
        plan=plan,
        payment_id=payment.telegram_payment_charge_id,
        amount=amount_rub,
    )

    plan_names = {"pro": "Pro 🌟"}
    await message.answer(
        PAYMENT_SUCCESS.format(plan=plan_names.get(plan, plan)),
        reply_markup=kb_main_menu(),
    )


# ── Основной обработчик запросов ──────────────────────────────────────────

@user_router.message(UserStates.waiting_query)
async def on_search_query(message: Message, state: FSMContext):
    await state.set_state(None)  # очищаем FSM-state, но сохраняем историю
    await _process_query(message, message.text or "", state)


@user_router.callback_query(F.data == "action_chain")
async def cb_chain(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_chain_input)
    await callback.message.answer(CHAIN_PROMPT)
    await callback.answer()


@user_router.message(UserStates.waiting_chain_input)
async def on_chain_input(message: Message, state: FSMContext):
    await state.clear()

    user = await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    if not user.get("terms_accepted"):
        await message.answer(
            TERMS_PROMPT, reply_markup=kb_terms_accept(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    parsed = parse_chain_input(message.text or "")
    if not parsed:
        await message.answer(
            CHAIN_PARSE_ERROR, reply_markup=kb_chain_result()
        )
        return

    result = calculate_chain(**parsed)

    if len(result) <= 4096:
        await message.answer(
            result, reply_markup=kb_chain_result(), parse_mode="HTML"
        )
    else:
        parts = [result[i:i + 4000] for i in range(0, len(result), 4000)]
        for i, part in enumerate(parts):
            kb = kb_chain_result() if i == len(parts) - 1 else None
            await message.answer(part, reply_markup=kb, parse_mode="HTML")


@user_router.message(F.text & ~F.text.startswith("/"))
async def on_plain_text(message: Message, state: FSMContext):
    """Любой текст без команды — обрабатываем как поисковый запрос."""
    await _process_query(message, message.text or "", state)


async def _process_query(
    message: Message, query: str, state: FSMContext, from_user=None,
):
    """Проверяем лимит, делаем RAG поиск, отправляем ответ."""
    if not query.strip():
        return

    actor = from_user or message.from_user
    telegram_id = actor.id

    user = await get_or_create_user(
        telegram_id=telegram_id,
        username=actor.username or "",
        full_name=actor.full_name or "",
    )

    # Проверяем принятие соглашения
    if not user.get("terms_accepted"):
        await message.answer(
            TERMS_PROMPT, reply_markup=kb_terms_accept(),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        return

    can_query, remaining = await check_query_limit(user)
    if not can_query:
        await message.answer(LIMIT_REACHED, reply_markup=kb_subscribe())
        return

    # Читаем историю диалога из FSM (сбрасываем если прошло > 30 мин)
    data = await state.get_data()
    history = []
    if time.time() - data.get("last_query_at", 0) < 1800:
        history = data.get("history", [])

    thinking_msg = await message.answer(THINKING)
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        result = await rag_search(query, history)

        await thinking_msg.delete()

        if not result.chunks_used:
            await message.answer(NO_RESULTS, reply_markup=kb_after_answer())
            return

        kb = kb_after_answer()

        answer = result.answer
        if len(answer) <= 4096:
            await message.answer(answer, reply_markup=kb, parse_mode="HTML")
        else:
            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            for i, part in enumerate(parts):
                await message.answer(
                    part,
                    reply_markup=kb if i == len(parts) - 1 else None,
                    parse_mode="HTML",
                )

        await increment_query_count(telegram_id)
        await save_query_log(
            telegram_id=telegram_id,
            query=query,
            response=result.answer,
            chunks_used=result.chunks_used,
        )

        new_history = (history + [{"q": query, "a": result.answer}])[-3:]
        await state.set_data({
            "history": new_history,
            "last_query_at": time.time(),
        })

    except Exception as e:
        logger.error(
            "Ошибка RAG для user %d: %s\n%s",
            telegram_id, e, traceback.format_exc(),
        )
        try:
            await thinking_msg.delete()
        except Exception:
            pass
        await message.answer(
            "Произошла ошибка при обработке запроса. Попробуйте ещё раз.",
            reply_markup=kb_back(),
        )
