"""
Entry point для бота с Neon PostgreSQL БД
"""

import os
import sys
import asyncio
import logging
from dotenv import load_dotenv

# Загрузить переменные из .env
load_dotenv()

# Настроить логирование
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Проверить наличие Neon CONNECTION_STRING
NEON_CONNECTION_STRING = os.getenv("NEON_CONNECTION_STRING")
if not NEON_CONNECTION_STRING:
    logger.error("❌ ОШИБКА: Переменная NEON_CONNECTION_STRING не установлена в .env")
    logger.error("Смотрите NEON_SETUP.md для инструкций")
    sys.exit(1)

logger.info("🔗 Подключение к Neon PostgreSQL...")

async def main():
    """Запустить бота и API сервер"""
    try:
        from aiohttp import web
        from api_server import create_app
        from bot import main as bot_main

        host = os.getenv("API_HOST", "0.0.0.0")
        port = int(os.getenv("PORT", os.getenv("API_PORT", "8080")))

        # Запустить API сервер в фоне
        app = create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"🌐 API сервер запущен на {host}:{port}")

        # Запустить бота
        logger.info("🤖 Запуск Telegram бота...")
        await bot_main()
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Фатальная ошибка: {e}")
        sys.exit(1)
