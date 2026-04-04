import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from api import create_app
from config import TOKEN
from database.session import init_db
from handlers import router
from middlewares.session import DbSessionMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

WEBAPP_PORT = 8080


async def main() -> None:
    await init_db()
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    await bot.set_my_commands([
        BotCommand(command="menu", description="Главное меню"),
    ])

    dp = Dispatcher()
    dp.message.middleware(DbSessionMiddleware())
    dp.callback_query.middleware(DbSessionMiddleware())
    dp.include_router(router)

    fastapi_app = create_app()
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=WEBAPP_PORT, log_level="info")
    server = uvicorn.Server(config)

    logger.info("Starting bot polling + webapp on port %s", WEBAPP_PORT)
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            server.serve(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
