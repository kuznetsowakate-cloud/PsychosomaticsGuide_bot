import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
)
from config.settings import BOT_TOKEN, ADMIN_IDS
from handlers import user_router, admin_router
from services.fsm_storage import SupabaseStorage

# Railway автоматически устанавливает RAILWAY_PUBLIC_DOMAIN
_railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or (
    f"https://{_railway_domain}" if _railway_domain else ""
)
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", "8080"))

OLD_BOT_TOKEN = os.getenv("OLD_BOT_TOKEN", "")

OLD_BOT_REDIRECT_TEXT = (
    "👋 Привет!\n\n"
    "Бот расчета цепочки переехал в новое место, "
    "теперь это часть Справочника по психосоматике.\n\n"
    "🧠 <b>Что умеет новый Справочник по психосоматике:</b>\n\n"
    "🔍 <b>Умный поиск по психосоматике</b>\n"
    "Напиши симптом или орган — бот найдёт ответ сразу в нескольких "
    "источниках и даст структурированный ответ с психологическими "
    "причинами и рекомендациями. Доступ к большой базе знаний по "
    "психосоматике у тебя в кармане в любой момент!\n\n"
    "🔗 <b>Расчёт цепочки</b>\n"
    "Все как было раньше: введи возраст диагноза, сепарации и клиента — "
    "получи полный расчёт с параллелями и датами за секунду.\n\n"
    "👉 Переходи и попробуй прямо сейчас:\n"
    "@PsychosomaticsGuide_bot"
)


async def _run_redirect_bot() -> None:
    """Старый бот отвечает на любое сообщение редиректом в новый."""
    from aiogram.types import Message as TgMessage

    old_bot = Bot(
        token=OLD_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    old_dp = Dispatcher()

    @old_dp.message()
    async def _redirect(msg: TgMessage):
        await msg.answer(OLD_BOT_REDIRECT_TEXT)

    logger = logging.getLogger("redirect_bot")
    logger.info("Redirect-бот запущен (старый токен)")
    await old_bot.delete_webhook(drop_pending_updates=True)
    try:
        await old_dp.start_polling(old_bot)
    finally:
        await old_bot.session.close()


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger(__name__)

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не указан в .env!")
        return

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=SupabaseStorage())

    dp.include_router(admin_router)   # admin первым (проверка ADMIN_IDS)
    dp.include_router(user_router)

    await _set_commands(bot)
    logger.info("PsychosomaticsGuide Bot запущен")

    # Запускаем Google Drive синхронизацию если настроена
    drive_task = None
    if os.getenv("DRIVE_FOLDER_ID"):
        from services.drive_sync import drive_sync_loop
        drive_task = asyncio.create_task(drive_sync_loop(bot=bot))
        logger.info("Google Drive синхронизация активирована")
    else:
        logger.info(
            "DRIVE_FOLDER_ID не задан — синхронизация с Drive отключена"
        )

    # Ежедневный отчёт администратору в 09:00 МСК
    from services.daily_report import daily_report_loop
    report_task = asyncio.create_task(daily_report_loop(bot=bot))
    logger.info("Ежедневный отчёт запланирован на 09:00 МСК")

    # Редирект-бот (старый токен) — отвечает редиректом в новый
    redirect_task = None
    if OLD_BOT_TOKEN:
        redirect_task = asyncio.create_task(_run_redirect_bot())
        logger.info("Redirect-бот запущен (старый токен)")

    try:
        if WEBHOOK_URL:
            await _run_webhook(bot, dp, logger)
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
    finally:
        if drive_task:
            drive_task.cancel()
        if redirect_task:
            redirect_task.cancel()
        report_task.cancel()
        await bot.session.close()


async def _set_commands(bot: Bot) -> None:
    user_commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(
            command="my_plan",
            description="Мой тариф и подписка",
        ),
        BotCommand(command="chain", description="Расчёт цепочки"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="feedback", description="Написать разработчику"),
        BotCommand(command="help", description="Как пользоваться"),
        BotCommand(command="delete", description="Удалить мои данные"),
    ]
    admin_commands = user_commands + [
        BotCommand(
            command="admin",
            description="Панель администратора (admin)",
        ),
        BotCommand(command="stats", description="Статистика базы (admin)"),
        BotCommand(
            command="sources",
            description="Список источников (admin)",
        ),
        BotCommand(
            command="resync_empty",
            description="Повторная обработка файлов без чанков (admin)",
        ),
    ]

    await bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    for admin_id in ADMIN_IDS:
        try:
            await bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception:
            pass


async def _run_webhook(
    bot: Bot, dp: Dispatcher, logger: logging.Logger,
) -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import (
        SimpleRequestHandler, setup_application,
    )

    await bot.set_webhook(
        url=f"{WEBHOOK_URL}{WEBHOOK_PATH}",
        drop_pending_updates=True,
    )
    logger.info("Webhook установлен: %s%s", WEBHOOK_URL, WEBHOOK_PATH)

    app = web.Application()
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info("Webhook-сервер слушает порт %d", PORT)

    await asyncio.Event().wait()  # держим процесс живым


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен")
