import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

def _require(key: str, default: str = None) -> str:
    """Load and validate a required environment variable."""
    value = os.getenv(key, default)
    if not value:
        logger.error(f"Missing required environment variable: {key}")
        sys.exit(1)
    return value

BOT_TOKEN = _require("BOT_TOKEN")
TELEGRAM_CHAT_ID = int(_require("TELEGRAM_CHAT_ID"))
NAS_ROOT_PATH = os.getenv("NAS_PATH", "/data")

def is_authorized(user_id: int) -> bool:
    """Check if user_id is authorized (single-user mode)."""
    return user_id == TELEGRAM_CHAT_ID

CATEGORIES = {
    "Documents": [".pdf", ".docx", ".xlsx", ".txt", ".pptx", ".md"],
    "Media": [".png", ".jpg", ".jpeg", ".mp4", ".mov", ".gif", ".mkv", ".mp3", ".wav"],
    "Data": [".csv", ".json", ".sql", ".xml", ".yaml", ".yml"],
    "Scripts": [".py", ".sh", ".js", ".ts", ".go", ".c", ".cpp"],
    "Archives": [".zip", ".tar", ".gz", ".7z", ".rar"],
    "Other": []
}

PAGE_SIZE = 10
PROGRESS_BAR_LENGTH = 12
RATE_LIMIT_INTERVAL = float(os.getenv("RATE_LIMIT_INTERVAL", "2.0"))
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "500"))
