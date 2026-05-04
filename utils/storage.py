import os
import shutil
import math
import logging
import re
import time
from pathlib import Path
from typing import Optional, Dict, Union
from collections import OrderedDict

logger = logging.getLogger(__name__)

_INVALID_NAME_CHARS = set('/\\\x00')
_MAX_FOLDER_NAME_LEN = 255
_MAX_RATE_DATA_ENTRIES = 10000  # Prevent unbounded growth

def get_disk_usage(path: Union[str, Path]) -> Optional[Dict[str, Union[int, float]]]:
    """Get disk usage statistics for a path (total, used, free, percent)."""
    try:
        total, used, free = shutil.disk_usage(path)
        percent = (used / total) * 100
        return {"total": total, "used": used, "free": free, "percent": percent}
    except Exception as e:
        logger.error(f"Error calculating disk usage for {path}: {e}")
        return None

def generate_progress_bar(percent: float, length: int = 15) -> str:
    """Generate a visual progress bar for disk usage."""
    filled_length = int(round(length * percent / 100))
    bar = "▰" * filled_length + "▱" * (length - filled_length)
    return bar

def format_bytes(size_bytes: int) -> str:
    """Format bytes to human-readable size (B, KB, MB, GB, etc)."""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def sanitize_filename(filename: str) -> str:
    """Remove dangerous characters from filename for safe storage."""
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\.\-]', '_', filename)
    if not filename:
        filename = "unnamed_file"
    return filename

def get_unique_path(target_path: Path) -> Path:
    """Get a unique path by appending (1), (2), etc if target exists."""
    if not target_path.exists():
        return target_path
    stem = target_path.stem
    suffix = target_path.suffix
    directory = target_path.parent
    counter = 1
    while True:
        new_path = directory / f"{stem} ({counter}){suffix}"
        if not new_path.exists():
            return new_path
        counter += 1

_rate_data: OrderedDict[int, float] = OrderedDict()

def is_rate_limited(user_id: int, min_interval: float = 2.0) -> bool:
    """Check and update rate limiter for destructive operations. Prevents spam."""
    now = time.time()
    if now - _rate_data.get(user_id, 0) < min_interval:
        return True

    _rate_data[user_id] = now

    # Clean up old entries if dict grows too large (LRU-style)
    if len(_rate_data) > _MAX_RATE_DATA_ENTRIES:
        _rate_data.popitem(last=False)  # Remove oldest (FIFO)

    return False

def safe_resolve(base: Path, rel_path: str) -> Optional[Path]:
    """Safely resolve relative path within base directory, preventing traversal attacks."""
    try:
        resolved = (base / rel_path).resolve()
        resolved.relative_to(base.resolve())
        return resolved
    except (ValueError, OSError):
        return None

def validate_folder_name(name: str) -> Optional[str]:
    """Validate folder name for safety and restrictions. Returns error message if invalid."""
    if not name:
        return "Name cannot be empty."
    if len(name) > _MAX_FOLDER_NAME_LEN:
        return f"Name too long (max {_MAX_FOLDER_NAME_LEN} characters)."
    if any(c in _INVALID_NAME_CHARS for c in name):
        return "Name contains invalid characters (/, \\, or null bytes)."
    if any(ord(c) < 32 for c in name):
        return "Name contains control characters."
    return None

def ensure_nas_structure(nas_path: str, categories: Dict) -> None:
    """Create NAS root and category folders if they don't exist."""
    nas_root = Path(nas_path)
    if not nas_root.exists():
        logger.warning(f"NAS path '{nas_root}' does not exist. Creating...")
        nas_root.mkdir(parents=True, exist_ok=True)
    for category in categories:
        cat_path = nas_root / category
        if not cat_path.exists():
            cat_path.mkdir(exist_ok=True)
            logger.info(f"Created category folder: {cat_path}")

def list_trash_items(nas_root: Path) -> list:
    """Return list of Path objects in .trash/, sorted newest first."""
    trash_dir = nas_root / ".trash"
    if not trash_dir.exists():
        return []
    items = sorted(
        [f for f in trash_dir.iterdir() if not f.name.startswith('.')],
        key=lambda f: f.stat().st_mtime,
        reverse=True
    )
    return items

def empty_trash(nas_root: Path) -> int:
    """Permanently delete all items in .trash/. Returns count deleted."""
    trash_dir = nas_root / ".trash"
    if not trash_dir.exists():
        return 0
    count = 0
    for item in trash_dir.iterdir():
        if item.name.startswith('.'):
            continue
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            count += 1
        except OSError as e:
            logger.error(f"Error deleting trash item {item}: {e}")
    return count
