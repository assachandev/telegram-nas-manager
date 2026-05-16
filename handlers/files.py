import asyncio
import logging
import uuid
from pathlib import Path
from aiogram import Router, types, F, Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import NAS_ROOT_PATH, CATEGORIES, MAX_FILE_SIZE_MB, is_authorized
from utils.storage import (
    format_bytes, sanitize_filename, get_unique_path, is_rate_limited,
    safe_resolve, validate_folder_name,
    cache_set, cache_get_fresh, cache_prune_expired,
)

logger = logging.getLogger(__name__)
router = Router()

# In-memory session for pending file uploads. Each session has its own UUID.
# Bound both ways: max 500 entries, plus a 1-hour TTL so stale sessions go away.
pending_files: dict = {}
_MAX_PENDING = 500
_PENDING_TTL = 3600  # seconds

class RenameState(StatesGroup):
    waiting_for_name = State()

async def get_folder_selection_keyboard(session_id: str, current_path_str: str, recommended_cat: str = None):
    """Build keyboard for browsing and selecting target folders (async I/O)."""
    builder = InlineKeyboardBuilder()

    nas_root = Path(NAS_ROOT_PATH)
    current_path = safe_resolve(nas_root, current_path_str)
    if current_path is None or not current_path.exists():
        current_path = nas_root
        current_path_str = ""

    if current_path_str == "" and recommended_cat:
        builder.button(
            text=f"⭐  Auto-sort to {recommended_cat}",
            callback_data=f"save_to:{session_id}:{recommended_cat}"
        )

    def list_dirs(path):
        return sorted([f.name for f in path.iterdir() if f.is_dir() and not f.name.startswith('.')])

    try:
        subfolders = await asyncio.to_thread(list_dirs, current_path)
        for folder in subfolders:
            rel_path = f"{current_path_str}/{folder}".strip("/")
            builder.button(text=f"📁 {folder}", callback_data=f"browse_save:{session_id}:{rel_path}")
    except OSError as e:
        logger.error(f"Error listing subfolders: {e}")

    builder.adjust(2)

    action_builder = InlineKeyboardBuilder()
    action_builder.button(text="✅  Save here", callback_data=f"save_to:{session_id}:{current_path_str}")

    if current_path_str != "":
        parent_path = "/".join(current_path_str.split("/")[:-1])
        action_builder.button(text="⬆️  Up one level", callback_data=f"browse_save:{session_id}:{parent_path}")

    action_builder.button(text="❌  Cancel", callback_data=f"cancel_up:{session_id}")
    action_builder.adjust(2)

    builder.attach(action_builder)
    return builder.as_markup()

@router.message(F.from_user.id.func(is_authorized), F.document | F.photo | F.video | F.audio)
async def handle_file_upload(message: types.Message):
    """Detect file upload and show folder browser for target selection."""
    if message.document:
        file_id = message.document.file_id
        orig_name = message.document.file_name or f"file_{file_id[:8]}"
        file_size = message.document.file_size
    elif message.photo:
        file_id = message.photo[-1].file_id
        orig_name = f"photo_{file_id[:8]}.jpg"
        file_size = message.photo[-1].file_size
    elif message.video:
        file_id = message.video.file_id
        orig_name = message.video.file_name or f"video_{file_id[:8]}.mp4"
        file_size = message.video.file_size
    elif message.audio:
        file_id = message.audio.file_id
        orig_name = message.audio.file_name or f"audio_{file_id[:8]}.mp3"
        file_size = message.audio.file_size
    else:
        return

    # Enforce the configured upload ceiling. Telegram itself caps bot
    # downloads at ~20 MB unless using a local Bot API, but the limit
    # here protects against filling /data with one giant payload.
    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size and file_size > max_bytes:
        await message.answer(
            f"❌ <b>File too large</b>\n"
            f"<i>{format_bytes(file_size)} exceeds the {MAX_FILE_SIZE_MB} MB limit.</i>\n"
            f"<i>Raise <code>MAX_FILE_SIZE_MB</code> in .env if you really need this size.</i>",
            parse_mode="HTML",
        )
        return

    clean_name = sanitize_filename(orig_name)
    ext = Path(clean_name).suffix.lower()

    recommended_cat = "Other"
    for cat, extensions in CATEGORIES.items():
        if ext in extensions:
            recommended_cat = cat
            break

    # Drop any session that hasn't been touched in an hour, then store.
    cache_prune_expired(pending_files, _PENDING_TTL)
    session_id = uuid.uuid4().hex[:16]
    cache_set(pending_files, session_id, {
        "name": clean_name,
        "size": file_size,
        "file_id": file_id,
        "recommended": recommended_cat
    }, _MAX_PENDING)

    keyboard = await get_folder_selection_keyboard(session_id, "", recommended_cat)
    await message.answer(
        f"<b>📥 Incoming file</b>\n"
        f"<code>{clean_name}</code>\n"
        f"<i>{format_bytes(file_size)}  ·  category guess: {recommended_cat}</i>\n\n"
        f"<b>Destination:</b>  <code>/</code>\n"
        f"<i>Pick a folder, or save here.</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith("browse_save:"))
async def browse_folders_for_save(callback: types.CallbackQuery):
    _, session_id, rel_path = callback.data.split(":", 2)

    data = cache_get_fresh(pending_files, session_id, _PENDING_TTL)
    if data is None:
        await callback.answer("❌ Session expired.", show_alert=True)
        return

    nas_root = Path(NAS_ROOT_PATH)
    if safe_resolve(nas_root, rel_path) is None:
        await callback.answer("❌ Invalid path.", show_alert=True)
        return

    display_path = rel_path if rel_path else "/"

    keyboard = await get_folder_selection_keyboard(session_id, rel_path)
    await callback.message.edit_text(
        f"<b>📥 Incoming file</b>\n"
        f"<code>{data['name']}</code>\n"
        f"<i>{format_bytes(data['size'])}</i>\n\n"
        f"<b>Destination:</b>  <code>/{display_path}</code>\n"
        f"<i>Pick a folder, or save here.</i>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data.startswith("save_to:"))
async def save_to_selected_path(callback: types.CallbackQuery, bot: Bot):
    _, session_id, rel_path = callback.data.split(":", 2)

    if cache_get_fresh(pending_files, session_id, _PENDING_TTL) is None:
        await callback.answer("❌ Session expired.", show_alert=True)
        return

    if is_rate_limited(callback.from_user.id):
        await callback.answer("⏳ Too fast, wait a moment.", show_alert=True)
        return

    nas_root = Path(NAS_ROOT_PATH)
    target_dir = safe_resolve(nas_root, rel_path)
    if target_dir is None:
        await callback.answer("❌ Invalid path.", show_alert=True)
        return

    raw = pending_files.pop(session_id, None)
    data = raw.get("value") if raw else None
    if data is None:
        await callback.answer("❌ Session expired.", show_alert=True)
        return

    target_path = get_unique_path(target_dir / data['name'])

    await callback.message.edit_text(
        f"⏳ <b>Saving…</b>\n<code>{data['name']}</code>\n<i>{format_bytes(data['size'])}</i>",
        parse_mode="HTML"
    )

    try:
        await asyncio.to_thread(lambda: target_dir.mkdir(parents=True, exist_ok=True))
        file_info = await bot.get_file(data['file_id'])
        await bot.download_file(file_info.file_path, destination=str(target_path))

        display_rel = str(target_dir.relative_to(nas_root))
        if display_rel == ".":
            display_rel = ""
        await callback.message.edit_text(
            f"✅ <b>Saved</b>\n"
            f"<code>{target_path.name}</code>\n"
            f"<i>{format_bytes(data['size'])}  →  /{display_rel}/</i>",
            parse_mode="HTML"
        )
        logger.info(f"User {callback.from_user.id} saved file to {target_path}")
    except OSError as e:
        logger.error(f"Error saving file: {e}", exc_info=True)
        await callback.message.edit_text("❌ <b>Save failed.</b> Please try again.", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Unexpected error saving file: {e}", exc_info=True)
        await callback.message.edit_text("❌ An unexpected error occurred.", parse_mode="HTML")

    await callback.answer()

@router.callback_query(F.data.startswith("cancel_up:"))
async def cancel_upload(callback: types.CallbackQuery):
    session_id = callback.data.split(":", 1)[1]
    pending_files.pop(session_id, None)
    await callback.message.edit_text("🚫 <b>Cancelled.</b> File not saved.", parse_mode="HTML")
    await callback.answer()

# --- RENAME FLOW ---

@router.callback_query(F.data.startswith("rename_ask:"))
async def rename_ask(callback: types.CallbackQuery, state: FSMContext):
    _, rel_dir, file_name = callback.data.split(":", 2)
    await state.set_state(RenameState.waiting_for_name)
    await state.update_data(rel_dir=rel_dir, file_name=file_name)
    await callback.message.edit_text(
        f"<b>✏️ Rename file</b>\n"
        f"Current:  <code>{file_name}</code>\n\n"
        f"<i>Send the new filename in your next message.</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(RenameState.waiting_for_name)
async def rename_execute(message: types.Message, state: FSMContext):
    if is_rate_limited(message.from_user.id):
        await message.answer("⏳ <b>Slow down a bit.</b> Try again in a moment.", parse_mode="HTML")
        return
    new_name = message.text.strip() if message.text else ""
    error = validate_folder_name(new_name)
    if error:
        await message.answer(f"❌ {error}", parse_mode="HTML")
        return

    data = await state.get_data()
    await state.clear()

    nas_root = Path(NAS_ROOT_PATH)
    base = safe_resolve(nas_root, data['rel_dir'])
    if base is None:
        await message.answer("❌ Invalid path.", parse_mode="HTML")
        return

    old_path = base / data['file_name']
    if not old_path.exists():
        await message.answer("❌ File no longer exists.", parse_mode="HTML")
        return

    new_path = get_unique_path(base / new_name)

    try:
        await asyncio.to_thread(lambda: old_path.rename(new_path))
        await message.answer(
            f"✅ <b>Renamed</b>\n"
            f"<code>{data['file_name']}</code>\n→ <code>{new_path.name}</code>",
            parse_mode="HTML"
        )
        logger.info(f"User {message.from_user.id} renamed {old_path} → {new_path}")
    except OSError as e:
        logger.error(f"Error renaming file: {e}")
        await message.answer("❌ Failed to rename file.", parse_mode="HTML")
