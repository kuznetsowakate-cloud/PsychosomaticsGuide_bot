import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from config.settings import BOT_TOKEN
from handlers import user_router, admin_router
from services.fsm_storage import SupabaseStorage

# Railway автоматически устанавливает RAILWAY_PUBLIC_DOMAIN
_railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or (
    f"https://{_railway_domain}" if _railway_domain else ""
)
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", "8080"))


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

    try:
        if WEBHOOK_URL:
            await _run_webhook(bot, dp, logger)
        else:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
    finally:
        if drive_task:
            drive_task.cancel()
        await bot.session.close()


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
