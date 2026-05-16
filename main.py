import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN, NAS_ROOT_PATH, CATEGORIES
from handlers import commands, files, search, folders, trash
from utils.storage import ensure_nas_structure
from utils.middleware import AuthMiddleware

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

def _validate_nas_path(nas_path: str) -> None:
    """Validate NAS path exists and is writable at startup."""
    path = Path(nas_path)
    if not path.exists():
        logger.critical(f"NAS path does not exist: {nas_path}")
        sys.exit(1)
    if not path.is_dir():
        logger.critical(f"NAS path is not a directory: {nas_path}")
        sys.exit(1)
    if not path.stat().st_mode & 0o200:
        logger.critical(f"NAS path is not writable: {nas_path}")
        sys.exit(1)
    logger.info(f"✓ NAS path validation passed: {nas_path}")

async def main():
    """Start the NAS Manager bot with full initialization."""
    _validate_nas_path(NAS_ROOT_PATH)
    ensure_nas_structure(NAS_ROOT_PATH, CATEGORIES)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Reject every update from non-authorized users before it reaches any
    # router. Defense in depth on top of the per-handler is_authorized
    # checks already present in commands.py / files.py.
    auth = AuthMiddleware()
    dp.message.middleware(auth)
    dp.callback_query.middleware(auth)
    dp.edited_message.middleware(auth)

    dp.include_router(commands.router)
    dp.include_router(files.router)
    dp.include_router(search.router)
    dp.include_router(folders.router)
    dp.include_router(trash.router)

    logger.info("=== Starting NAS Manager Bot ===")
    logger.info(f"NAS Root: {NAS_ROOT_PATH}")

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Critical error: {e}", exc_info=True)
        sys.exit(1)
