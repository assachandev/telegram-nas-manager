import asyncio
import logging
import shutil
from pathlib import Path
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import NAS_ROOT_PATH, is_authorized
from utils.storage import format_bytes, is_rate_limited, list_trash_items, empty_trash, get_unique_path

logger = logging.getLogger(__name__)
router = Router()

TRASH_PAGE_SIZE = 10

# Cache: user_id -> list of item names (for index-based callback data)
_trash_cache: dict = {}

def _get_nas_root() -> Path:
    return Path(NAS_ROOT_PATH)

@router.message(lambda m: m.text == "🗑 Trash")
async def cmd_trash(message: types.Message):
    """Open trash browser."""
    if not is_authorized(message.from_user.id):
        return
    await _show_trash(message, message.from_user.id, 0)

@router.callback_query(F.data.startswith("trash_page:"))
async def trash_page(callback: types.CallbackQuery):
    """Handle trash pagination (regenerates cache on each page)."""
    page = int(callback.data.split(":", 1)[1])
    await _show_trash(callback, callback.from_user.id, page)

async def _show_trash(target, user_id: int, page: int):
    """Display trash contents with pagination. Cache is regenerated to prevent stale indices."""
    nas_root = _get_nas_root()

    items = await asyncio.to_thread(list_trash_items, nas_root)
    total = len(items)

    # ALWAYS regenerate cache for this page to prevent stale index problems
    _trash_cache[user_id] = [item.name for item in items]

    if total == 0:
        text = "<b>🗑 Trash</b>\n\nTrash is empty."
        builder = InlineKeyboardBuilder()
        builder.button(text="❌ Close", callback_data="trash_close")
        markup = builder.as_markup()
        if isinstance(target, types.Message):
            await target.answer(text, parse_mode="HTML", reply_markup=markup)
        else:
            await target.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
            await target.answer()
        return

    total_pages = max(1, (total + TRASH_PAGE_SIZE - 1) // TRASH_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    builder = InlineKeyboardBuilder()
    page_items = items[page * TRASH_PAGE_SIZE:(page + 1) * TRASH_PAGE_SIZE]
    for idx, item in enumerate(page_items):
        global_idx = page * TRASH_PAGE_SIZE + idx
        try:
            size = item.stat().st_size if item.is_file() else sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
            size_str = format_bytes(size)
        except OSError:
            size_str = "?"
        icon = "📄" if item.is_file() else "📁"
        # Strip the leading timestamp prefix for display: {timestamp}_{name}
        display = item.name
        if "_" in display:
            display = display.split("_", 1)[1]
        builder.button(
            text=f"{icon} {display}  [{size_str}]",
            callback_data=f"trash_opts:{global_idx}"
        )
    builder.adjust(1)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️ Prev", callback_data=f"trash_page:{page-1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="Next ➡️", callback_data=f"trash_page:{page+1}"))
    if nav:
        builder.row(*nav)

    builder.row(types.InlineKeyboardButton(text="🗑 Empty Trash", callback_data="trash_empty_conf"))
    builder.row(types.InlineKeyboardButton(text="❌ Close", callback_data="trash_close"))

    text = f"<b>🗑 Trash</b>\n{total} item(s)  ·  Page {page+1}/{total_pages}"
    if isinstance(target, types.Message):
        await target.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await target.answer()

@router.callback_query(F.data.startswith("trash_opts:"))
async def trash_item_options(callback: types.CallbackQuery):
    """Show options for trash item (restore/delete/view)."""
    idx = int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id

    cache = _trash_cache.get(user_id, [])
    if idx >= len(cache):
        await callback.answer("❌ Item not found. Please refresh trash.", show_alert=True)
        return

    item_name = cache[idx]
    nas_root = _get_nas_root()
    item_path = nas_root / ".trash" / item_name

    if not item_path.exists():
        await callback.answer("❌ Item no longer exists (may have been permanently deleted).", show_alert=True)
        return

    try:
        size = item_path.stat().st_size if item_path.is_file() else sum(f.stat().st_size for f in item_path.rglob('*') if f.is_file())
        size_str = format_bytes(size)
    except OSError:
        size_str = "?"

    display = item_name
    if "_" in display:
        display = display.split("_", 1)[1]

    builder = InlineKeyboardBuilder()
    builder.button(text="🔁 Restore", callback_data=f"trash_restore:{idx}")
    builder.button(text="❌ Delete Forever", callback_data=f"trash_del:{idx}")
    builder.button(text="⬅️ Back", callback_data="trash_page:0")
    builder.adjust(2)

    await callback.message.edit_text(
        f"<b>{display}</b>\n\n"
        f"Size  {size_str}\n\n"
        f"Restore moves it to <code>/Restored/</code>.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("trash_restore:"))
async def trash_restore(callback: types.CallbackQuery):
    """Restore item from trash to /Restored/ (destructive operation with rate limit)."""
    idx = int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id

    if is_rate_limited(user_id):
        await callback.answer("⏳ Too fast, wait a moment.", show_alert=True)
        return

    cache = _trash_cache.get(user_id, [])
    if idx >= len(cache):
        await callback.answer("❌ Item not found. Please refresh trash.", show_alert=True)
        return

    item_name = cache[idx]
    nas_root = _get_nas_root()
    item_path = nas_root / ".trash" / item_name

    if not item_path.exists():
        await callback.answer("❌ Item no longer exists.", show_alert=True)
        return

    display = item_name
    if "_" in display:
        display = display.split("_", 1)[1]

    restore_dir = nas_root / "Restored"
    dest = get_unique_path(restore_dir / display)

    try:
        await asyncio.to_thread(lambda: restore_dir.mkdir(exist_ok=True))
        await asyncio.to_thread(lambda: item_path.rename(dest))
        await callback.message.edit_text(
            f"✅ <b>Restored</b>\n\n"
            f"<code>{display}</code>\n→ <code>/Restored/</code>",
            parse_mode="HTML"
        )
        logger.info(f"User {user_id} restored trash item: {item_name} → {dest}")
    except OSError as e:
        logger.error(f"Error restoring trash item: {e}")
        await callback.answer("❌ Failed to restore item.", show_alert=True)

    await callback.answer()

@router.callback_query(F.data.startswith("trash_del:"))
async def trash_delete_permanent(callback: types.CallbackQuery):
    """Permanently delete trash item (destructive operation with rate limit)."""
    idx = int(callback.data.split(":", 1)[1])
    user_id = callback.from_user.id

    if is_rate_limited(user_id):
        await callback.answer("⏳ Too fast, wait a moment.", show_alert=True)
        return

    cache = _trash_cache.get(user_id, [])
    if idx >= len(cache):
        await callback.answer("❌ Item not found. Please refresh trash.", show_alert=True)
        return

    item_name = cache[idx]
    nas_root = _get_nas_root()
    item_path = nas_root / ".trash" / item_name

    display = item_name
    if "_" in display:
        display = display.split("_", 1)[1]

    try:
        if item_path.exists():
            if item_path.is_dir():
                await asyncio.to_thread(lambda: shutil.rmtree(item_path))
            else:
                await asyncio.to_thread(lambda: item_path.unlink())
            await callback.message.edit_text(
                f"🗑 <b>Deleted</b>\n<code>{display}</code>",
                parse_mode="HTML"
            )
            logger.info(f"User {user_id} permanently deleted: {item_name}")
        else:
            await callback.answer("❌ Item already gone.", show_alert=True)
    except OSError as e:
        logger.error(f"Error permanently deleting trash item: {e}")
        await callback.answer("❌ Failed to delete item.", show_alert=True)

    await callback.answer()

@router.callback_query(F.data == "trash_empty_conf")
async def trash_empty_confirm(callback: types.CallbackQuery):
    """Show empty trash confirmation."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔥 Yes, Empty All", callback_data="trash_empty_exec")
    builder.button(text="❌ Cancel", callback_data="trash_page:0")
    builder.adjust(2)
    await callback.message.edit_text(
        "⚠️ <b>Empty Trash?</b>\n\nAll items will be <b>permanently deleted</b>. This cannot be undone.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "trash_empty_exec")
async def trash_empty_execute(callback: types.CallbackQuery):
    """Permanently delete all trash (destructive operation with rate limit)."""
    if is_rate_limited(callback.from_user.id):
        await callback.answer("⏳ Too fast, wait a moment.", show_alert=True)
        return

    nas_root = _get_nas_root()
    count = await asyncio.to_thread(empty_trash, nas_root)
    _trash_cache.pop(callback.from_user.id, None)

    await callback.message.edit_text(
        f"🗑 <b>Trash Emptied</b>\n{count} item(s) permanently deleted.",
        parse_mode="HTML"
    )
    logger.info(f"User {callback.from_user.id} emptied trash ({count} items)")
    await callback.answer()

@router.callback_query(F.data == "trash_close")
async def trash_close(callback: types.CallbackQuery):
    """Close trash browser."""
    await callback.message.edit_text("Closed.", parse_mode="HTML")
    await callback.answer()
