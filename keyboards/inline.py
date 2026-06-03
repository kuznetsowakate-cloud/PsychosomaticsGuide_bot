from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔍 Поиск по справочнику", callback_data="action_search"
    )
    builder.button(text="🔗 Расчёт цепочки", callback_data="action_chain")
    builder.button(
        text="💳 Тариф и подписка", callback_data="action_subscribe"
    )
    builder.button(text="❓ Как пользоваться", callback_data="action_help")
    builder.button(
        text="💬 Написать разработчику",
        callback_data="action_feedback",
    )
    builder.adjust(1)
    return builder.as_markup()


def kb_subscribe() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🌟 Pro — 249 ₽/мес (безлимит)",
        callback_data="buy_basic",
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


def kb_after_answer_with_related(questions: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    n = min(len(questions), 3)
    for i, q in enumerate(questions[:3]):
        label = q if len(q) <= 60 else q[:57] + "…"
        builder.button(text=f"💭 {label}", callback_data=f"related_{i}")
    builder.button(text="🔍 Новый запрос", callback_data="action_search")
    builder.button(text="🏠 Меню", callback_data="action_back")
    builder.adjust(*([1] * n + [2]))
    return builder.as_markup()


def kb_chain_result() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔗 Новый расчёт", callback_data="action_chain")
    builder.button(text="🏠 Меню", callback_data="action_back")
    builder.adjust(2)
    return builder.as_markup()


def kb_terms_accept() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принимаю и начать", callback_data="terms_accept")
    builder.adjust(1)
    return builder.as_markup()


def kb_delete_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Да, удалить мои данные",
        callback_data="delete_confirm",
    )
    builder.button(text="← Отмена", callback_data="action_back")
    builder.adjust(1)
    return builder.as_markup()
