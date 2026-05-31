import logging
import traceback

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery

from keyboards.inline import (
    kb_main_menu, kb_subscribe, kb_back, kb_after_answer, kb_chain_result,
)
from services.chain_calc import calculate_chain, parse_chain_input
from services.rag import rag_search
from services.users import (
    get_or_create_user, check_query_limit,
    increment_query_count, save_query_log,
    activate_subscription,
)
from config.settings import PLAN_PRICES
from texts.messages import (
    WELCOME, HELP_TEXT, SEARCH_PROMPT, THINKING,
    LIMIT_REACHED, NO_RESULTS, SUBSCRIBE_TEXT,
    PAYMENT_SUCCESS, CHAIN_PROMPT, CHAIN_PARSE_ERROR, CANCEL_TEXT,
)

logger = logging.getLogger(__name__)
user_router = Router()


class UserStates(StatesGroup):
    waiting_query = State()
    waiting_chain_input = State()


# ── /start ─────────────────────────────────────────────────────────────────

@user_router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )
    await message.answer(WELCOME, reply_markup=kb_main_menu())


# ── /help ──────────────────────────────────────────────────────────────────

@user_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(CANCEL_TEXT, reply_markup=kb_main_menu())


@user_router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=kb_back())


# ── /subscribe ─────────────────────────────────────────────────────────────

@user_router.message(Command("subscribe"))
async def cmd_subscribe(message: Message):
    await message.answer(SUBSCRIBE_TEXT, reply_markup=kb_subscribe())


# ── Callbacks: навигация ───────────────────────────────────────────────────

async def _remove_keyboard(callback: CallbackQuery) -> None:
    """Убираем кнопки у нажатого сообщения, чтобы не было «зависших» клавиатур."""
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


@user_router.callback_query(F.data == "action_help")
async def cb_help(callback: CallbackQuery):
    await _remove_keyboard(callback)
    await callback.message.answer(HELP_TEXT, reply_markup=kb_back())
    await callback.answer()


@user_router.callback_query(F.data == "action_subscribe")
async def cb_subscribe(callback: CallbackQuery):
    await _remove_keyboard(callback)
    await callback.message.answer(SUBSCRIBE_TEXT, reply_markup=kb_subscribe())
    await callback.answer()


@user_router.callback_query(F.data == "action_search")
async def cb_search(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_query)
    await _remove_keyboard(callback)
    await callback.message.answer(SEARCH_PROMPT)
    await callback.answer()


# ── Оплата через Telegram Stars ────────────────────────────────────────────

@user_router.callback_query(F.data.startswith("buy_"))
async def cb_buy(callback: CallbackQuery):
    plan = callback.data[4:]  # 'basic' или 'pro'
    stars = PLAN_PRICES.get(plan, 150)

    plan_names = {"basic": "Базовый", "pro": "Про"}
    plan_name = plan_names.get(plan, plan)

    await callback.message.answer_invoice(
        title=f"Подписка {plan_name}",
        description="Безлимитный доступ к справочнику по психосоматике на 30 дней",
        payload=f"sub_{plan}",
        currency="XTR",         # Telegram Stars
        prices=[LabeledPrice(label=f"Подписка {plan_name}", amount=stars)],
    )
    await callback.answer()


@user_router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)


@user_router.message(F.successful_payment)
async def on_payment(message: Message):
    payment = message.successful_payment
    payload = payment.invoice_payload     # 'sub_basic' или 'sub_pro'
    plan = payload.replace("sub_", "")
    stars = payment.total_amount

    await activate_subscription(
        telegram_id=message.from_user.id,
        plan=plan,
        payment_id=payment.telegram_payment_charge_id,
        amount=stars,
    )

    plan_names = {"basic": "Базовый ⭐", "pro": "Про 🌟"}
    await message.answer(
        PAYMENT_SUCCESS.format(plan=plan_names.get(plan, plan)),
        reply_markup=kb_main_menu(),
    )


# ── Основной обработчик запросов ───────────────────────────────────────────

@user_router.message(UserStates.waiting_query)
async def on_search_query(message: Message, state: FSMContext):
    await state.clear()
    await _process_query(message, message.text or "")


@user_router.callback_query(F.data == "action_chain")
async def cb_chain(callback: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_chain_input)
    await callback.message.answer(CHAIN_PROMPT)
    await callback.answer()


@user_router.message(UserStates.waiting_chain_input)
async def on_chain_input(message: Message, state: FSMContext):
    await state.clear()
    parsed = parse_chain_input(message.text or "")
    if not parsed:
        await message.answer(CHAIN_PARSE_ERROR, reply_markup=kb_chain_result())
        return

    result = calculate_chain(**parsed)

    if len(result) <= 4096:
        await message.answer(result, reply_markup=kb_chain_result(),
                             parse_mode="HTML")
    else:
        parts = [result[i:i + 4000] for i in range(0, len(result), 4000)]
        for i, part in enumerate(parts):
            kb = kb_chain_result() if i == len(parts) - 1 else None
            await message.answer(part, reply_markup=kb, parse_mode="HTML")


@user_router.message(F.text & ~F.text.startswith("/"))
async def on_plain_text(message: Message):
    """Любой текст без команды — обрабатываем как поисковый запрос."""
    await _process_query(message, message.text or "")


async def _process_query(message: Message, query: str):
    """Проверяем лимит, делаем RAG поиск, отправляем ответ."""
    if not query.strip():
        return

    telegram_id = message.from_user.id

    # Проверяем пользователя и лимит
    user = await get_or_create_user(
        telegram_id=telegram_id,
        username=message.from_user.username or "",
        full_name=message.from_user.full_name or "",
    )

    can_query, remaining = await check_query_limit(user)
    if not can_query:
        await message.answer(LIMIT_REACHED, reply_markup=kb_subscribe())
        return

    # Показываем "Ищу..."
    thinking_msg = await message.answer(THINKING)
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        # RAG поиск
        result = await rag_search(query)

        # Удаляем "Ищу..."
        await thinking_msg.delete()

        if not result.chunks_used:
            await message.answer(NO_RESULTS, reply_markup=kb_after_answer())
            return

        answer = result.answer
        if len(answer) <= 4096:
            await message.answer(
                answer, reply_markup=kb_after_answer(), parse_mode="HTML")
        else:
            parts = [answer[i:i+4000] for i in range(0, len(answer), 4000)]
            for i, part in enumerate(parts):
                kb = kb_after_answer() if i == len(parts) - 1 else None
                await message.answer(part, reply_markup=kb, parse_mode="HTML")

        # Обновляем статистику
        await increment_query_count(telegram_id)
        await save_query_log(
            telegram_id=telegram_id,
            query=query,
            response=result.answer,
            chunks_used=result.chunks_used,
        )

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
