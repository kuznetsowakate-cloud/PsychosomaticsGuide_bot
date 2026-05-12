import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config.settings import BOT_TOKEN
from handlers import user_router, admin_router


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
    dp = Dispatcher(storage=MemoryStorage())

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
        logger.info("DRIVE_FOLDER_ID не задан — синхронизация с Drive отключена")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        if drive_task:
            drive_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен")
