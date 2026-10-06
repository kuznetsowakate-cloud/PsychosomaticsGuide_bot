from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔍 Поиск по справочнику", callback_data="action_search"
    )
    builder.button(text="🔗 Цепочка", callback_data="action_chain")
    builder.button(text="📅 Работа с клиентами", callback_data="action_clients")
    builder.button(text="⚙️ Ещё", callback_data="action_more")
    builder.adjust(1, 2, 1)
    return builder.as_markup()


def kb_more_menu() -> InlineKeyboardMarkup:
    """Подменю «Ещё» — служебные пункты, которые нужны реже."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="💳 Тариф и подписка", callback_data="action_subscribe"
    )
    builder.button(text="🎁 Промокод", callback_data="action_promo")
    builder.button(text="❓ Как пользоваться", callback_data="action_help")
    builder.button(
        text="💬 Написать разработчику",
        callback_data="action_feedback",
    )
    builder.button(
        text="🗑 Удалить мои данные", callback_data="action_delete"
    )
    builder.button(text="← Назад", callback_data="action_menu_main")
    builder.adjust(1)
    return builder.as_markup()


def kb_clients_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="➕ Добавить запись", callback_data="action_newclient"
    )
    builder.button(
        text="📋 Список записей", callback_data="action_clients_list"
    )
    builder.button(
        text="✏️ Текст напоминания (Pro)", callback_data="tpl_reminder"
    )
    builder.button(
        text="✏️ Текст вопроса о самочувствии (Pro)",
        callback_data="tpl_followup",
    )
    builder.button(text="← Меню", callback_data="action_back")
    builder.adjust(1)
    return builder.as_markup()


def kb_clients_list(items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    """Список записей: по кнопке удаления на каждую запись.

    items — пары (id записи, подпись кнопки).
    """
    builder = InlineKeyboardBuilder()
    for appt_id, label in items:
        builder.button(text=f"🗑 {label}", callback_data=f"appt_del_{appt_id}")
    builder.button(text="← Назад", callback_data="action_clients")
    builder.adjust(1)
    return builder.as_markup()


def kb_appt_delete_confirm(appt_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗑 Да, удалить", callback_data=f"appt_delok_{appt_id}"
    )
    builder.button(text="← Отмена", callback_data="action_clients_list")
    builder.adjust(1)
    return builder.as_markup()


def kb_template_edit(kind: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="↩️ Вернуть стандартный текст",
        callback_data=f"tpl_reset_{kind}",
    )
    builder.button(text="← Назад", callback_data="action_clients")
    builder.adjust(1)
    return builder.as_markup()


def kb_subscribe() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🌟 Pro — 249 ₽/мес (безлимит)",
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
