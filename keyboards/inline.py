from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔍 Поиск по справочнику", callback_data="action_search"
    )
    builder.button(text="🔗 Расчёт цепочки", callback_data="action_chain")
    builder.button(text="💳 Подписка", callback_data="action_subscribe")
    builder.button(text="❓ Как пользоваться", callback_data="action_help")
    builder.button(text="💬 Написать разработчику", callback_data="action_feedback")
    builder.adjust(1)
    return builder.as_markup()


def kb_subscribe() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="⭐ Базовый — 150 Stars/мес (безлимит)",
        callback_data="buy_basic",
    )
    builder.button(
        text="🌟 Про — 300 Stars/мес (приоритет + история)",
        callback_data="buy_pro",
    )
    builder.button(text="← Назад", callback_data="action_back")
    builder.adjust(1)
    return builder.as_markup()


def kb_back() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="← Главное меню", callback_data="action_back")
    return builder.as_markup()


def kb_after_answer() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Новый запрос", callback_data="action_search")
    builder.button(text="🏠 Меню", callback_data="action_back")
    builder.adjust(2)
    return builder.as_markup()


def kb_chain_result() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Новый расчёт", callback_data="action_chain")
    builder.button(text="🏠 Меню", callback_data="action_back")
    builder.adjust(2)
    return builder.as_markup()
