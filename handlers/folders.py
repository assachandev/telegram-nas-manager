import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Union
from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import NAS_ROOT_PATH, is_authorized
from utils.storage import safe_resolve, validate_folder_name, is_rate_limited

logger = logging.getLogger(__name__)
router = Router()

class FolderManager(StatesGroup):
    """FSM states for folder manager operations."""
    waiting_for_name = State()
    confirm_create = State()
    confirm_delete = State()
    waiting_for_rename = State()
    confirm_rename = State()

_MODE_TITLES = {
    "create": "<b>➕ Create folder</b>",
    "delete": "<b>🗑️ Delete folder</b>",
    "rename": "<b>✏️ Rename folder</b>",
}

async def get_folder_browser_keyboard(current_path_str: str, mode: str):
    """Build folder browser keyboard for navigate and operate on directories."""
    builder = InlineKeyboardBuilder()
    nas_root = Path(NAS_ROOT_PATH)

    current_path = safe_resolve(nas_root, current_path_str)
    if current_path is None or not current_path.exists():
        current_path = nas_root
        current_path_str = ""

    def list_dirs(path):
        return sorted([f.name for f in path.iterdir() if f.is_dir() and not f.name.startswith('.')])

    try:
        subfolders = await asyncio.to_thread(list_dirs, current_path)
        for folder in subfolders:
            rel_path = f"{current_path_str}/{folder}".strip("/")
            builder.button(text=f"📁 {folder}", callback_data=f"fdir_browse:{mode}:{rel_path}")
    except OSError as e:
        logger.error(f"Error listing subfolders: {e}")

    builder.adjust(2)

    action_builder = InlineKeyboardBuilder()
    if mode == "create":
        action_builder.button(text="➕ Create Folder Here", callback_data=f"fdir_mkdir_here:{current_path_str}")
    elif mode == "delete" and current_path_str != "":
        action_builder.button(text="🗑️ Delete This Folder", callback_data=f"fdir_rmdir_here:{current_path_str}")
    elif mode == "rename" and current_path_str != "":
        action_builder.button(text="✏️ Rename This Folder", callback_data=f"fdir_rname_here:{current_path_str}")

    if current_path_str != "":
        parent_path = "/".join(current_path_str.split("/")[:-1])
        action_builder.button(text="⬅️ Up One Level", callback_data=f"fdir_browse:{mode}:{parent_path}")

    action_builder.button(text="❌ Cancel", callback_data="fdir_cancel")
    action_builder.adjust(1)

    builder.attach(action_builder)
    return builder.as_markup()

@router.message(Command("mkdir"))
@router.callback_query(F.data == "folders_main_quick")
async def cmd_folder_manager(event: Union[types.Message, types.CallbackQuery], state: FSMContext):
    """Open folder manager menu (create/rename/delete)."""
    if not is_authorized(event.from_user.id):
        return

    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="➕  Create folder",  callback_data="fdir_mode:create")
    builder.button(text="✏️  Rename folder",   callback_data="fdir_mode:rename")
    builder.button(text="🗑️  Delete folder",   callback_data="fdir_mode:delete")
    builder.button(text="❌  Close",           callback_data="fdir_cancel")
    builder.adjust(1)

    text = "<b>📂 Folder manager</b>\n<i>What would you like to do?</i>"
    if isinstance(event, types.Message):
        await event.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await event.answer()

@router.callback_query(F.data.startswith("fdir_mode:"))
async def start_browsing(callback: types.CallbackQuery, state: FSMContext):
    """Start folder browser for selected operation mode."""
    mode = callback.data.split(":", 1)[1]
    await state.update_data(mode=mode)

    title = _MODE_TITLES.get(mode, "<b>📂 Folders</b>")
    keyboard = await get_folder_browser_keyboard("", mode)
    await callback.message.edit_text(
        f"{title}\n<i>Current:</i>  <code>/</code>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

@router.callback_query(F.data.startswith("fdir_browse:"))
async def browse_folder_manager(callback: types.CallbackQuery, state: FSMContext):
    """Browse folders to select target for create/rename/delete."""
    _, mode, path = callback.data.split(":", 2)

    nas_root = Path(NAS_ROOT_PATH)
    if path and safe_resolve(nas_root, path) is None:
        await callback.answer("❌ Invalid path", show_alert=True)
        return

    display_path = path if path else "/"
    title = _MODE_TITLES.get(mode, "<b>📂 Folders</b>")
    keyboard = await get_folder_browser_keyboard(path, mode)
    await callback.message.edit_text(
        f"{title}\n<i>Current:</i>  <code>/{display_path}</code>",
        parse_mode="HTML",
        reply_markup=keyboard
    )
    await callback.answer()

# --- CREATE FLOW ---

@router.callback_query(F.data.startswith("fdir_mkdir_here:"))
async def ask_new_folder_name(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user for new folder name (CREATE flow)."""
    path = callback.data.split(":", 1)[1]
    await state.update_data(parent_path=path)
    await state.set_state(FolderManager.waiting_for_name)

    parent = path if path else "/"
    await callback.message.edit_text(
        f"<b>➕ Create folder</b>\n"
        f"<i>Inside</i>  <code>/{parent}</code>\n\n"
        f"<i>Send the new folder name in your next message.</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(FolderManager.waiting_for_name)
async def process_new_name(message: types.Message, state: FSMContext):
    """Validate folder name and show confirmation."""
    folder_name = message.text.strip() if message.text else ""
    error = validate_folder_name(folder_name)
    if error:
        await message.answer(f"❌ {error}", parse_mode="HTML")
        return

    data = await state.get_data()
    await state.update_data(folder_name=folder_name)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅  Create",  callback_data="fdir_confirm_create")
    builder.button(text="❌  Cancel",  callback_data="fdir_cancel")
    builder.adjust(2)

    parent = data.get('parent_path') or ""
    parent_disp = f"/{parent}" if parent else "/"
    await state.set_state(FolderManager.confirm_create)
    await message.answer(
        f"<b>➕ Create folder?</b>\n"
        f"<code>{folder_name}</code>\n"
        f"<i>inside</i>  <code>{parent_disp}</code>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@router.callback_query(FolderManager.confirm_create, F.data == "fdir_confirm_create")
async def execute_create(callback: types.CallbackQuery, state: FSMContext):
    """Execute folder creation."""
    if is_rate_limited(callback.from_user.id):
        await callback.answer("⏳ Slow down a bit.", show_alert=False)
        return
    data = await state.get_data()

    nas_root = Path(NAS_ROOT_PATH)
    target = safe_resolve(nas_root, str(Path(data['parent_path']) / data['folder_name']))
    if target is None:
        await callback.message.edit_text("❌ Invalid path.", parse_mode="HTML")
        await state.clear()
        await callback.answer()
        return

    try:
        await asyncio.to_thread(lambda: target.mkdir(parents=True, exist_ok=True))
        await callback.message.edit_text(
            f"✅ <b>Folder created</b>\n<code>{data['folder_name']}</code>",
            parse_mode="HTML",
        )
        logger.info(f"User {callback.from_user.id} created folder: {target}")
    except OSError as e:
        logger.error(f"Error creating folder: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ <b>Create failed</b>\n<i>{type(e).__name__}</i>",
            parse_mode="HTML",
        )

    await state.clear()
    await callback.answer()

# --- RENAME FLOW ---

@router.callback_query(F.data.startswith("fdir_rname_here:"))
async def ask_rename_folder(callback: types.CallbackQuery, state: FSMContext):
    """Prompt user for new folder name (RENAME flow)."""
    path = callback.data.split(":", 1)[1]
    await state.update_data(rename_path=path)
    await state.set_state(FolderManager.waiting_for_rename)

    old_name = path.split("/")[-1] if path else ""
    await callback.message.edit_text(
        f"<b>✏️ Rename folder</b>\n"
        f"Current:  <code>{old_name}</code>\n\n"
        f"<i>Send the new name in your next message.</i>",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(FolderManager.waiting_for_rename)
async def process_rename_name(message: types.Message, state: FSMContext):
    """Validate new folder name and show confirmation."""
    new_name = message.text.strip() if message.text else ""
    error = validate_folder_name(new_name)
    if error:
        await message.answer(f"❌ {error}", parse_mode="HTML")
        return

    data = await state.get_data()
    await state.update_data(rename_new_name=new_name)

    old_name = data['rename_path'].split("/")[-1]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅  Rename",  callback_data="fdir_confirm_rename")
    builder.button(text="❌  Cancel",  callback_data="fdir_cancel")
    builder.adjust(2)

    await state.set_state(FolderManager.confirm_rename)
    await message.answer(
        f"<b>✏️ Rename folder?</b>\n"
        f"<code>{old_name}</code>  →  <code>{new_name}</code>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@router.callback_query(FolderManager.confirm_rename, F.data == "fdir_confirm_rename")
async def execute_rename(callback: types.CallbackQuery, state: FSMContext):
    """Execute folder rename."""
    if is_rate_limited(callback.from_user.id):
        await callback.answer("⏳ Slow down a bit.", show_alert=False)
        return
    data = await state.get_data()

    nas_root = Path(NAS_ROOT_PATH)
    target = safe_resolve(nas_root, data['rename_path'])
    if target is None or not target.exists() or not target.is_dir():
        await callback.message.edit_text("❌ Folder not found", parse_mode="HTML")
        await state.clear()
        await callback.answer()
        return

    new_path = target.parent / data['rename_new_name']
    if new_path.exists():
        await callback.message.edit_text(
            f"❌ Already exists: <code>{data['rename_new_name']}</code>",
            parse_mode="HTML"
        )
        await state.clear()
        await callback.answer()
        return

    try:
        await asyncio.to_thread(lambda: target.rename(new_path))
        await callback.message.edit_text(
            f"✅ <b>Renamed</b>\n<code>{target.name}</code>  →  <code>{new_path.name}</code>",
            parse_mode="HTML",
        )
        logger.info(f"User {callback.from_user.id} renamed folder: {target} → {new_path}")
    except OSError as e:
        logger.error(f"Error renaming folder: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ <b>Rename failed</b>\n<i>{type(e).__name__}</i>",
            parse_mode="HTML",
        )

    await state.clear()
    await callback.answer()

# --- DELETE FLOW ---

@router.callback_query(F.data.startswith("fdir_rmdir_here:"))
async def confirm_folder_delete(callback: types.CallbackQuery, state: FSMContext):
    """Show delete confirmation for folder (DELETE flow)."""
    path = callback.data.split(":", 1)[1]
    await state.update_data(delete_path=path)

    builder = InlineKeyboardBuilder()
    builder.button(text="🗑️  Move to trash",  callback_data="fdir_confirm_delete_exec")
    builder.button(text="❌  Keep it",         callback_data="fdir_cancel")
    builder.adjust(1)

    await state.set_state(FolderManager.confirm_delete)
    await callback.message.edit_text(
        f"⚠️ <b>Move folder to trash?</b>\n"
        f"<code>/{path}</code>\n"
        f"<i>You can restore from 🗑 Trash later.</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(FolderManager.confirm_delete, F.data == "fdir_confirm_delete_exec")
async def execute_delete(callback: types.CallbackQuery, state: FSMContext):
    """Execute folder deletion to trash."""
    if is_rate_limited(callback.from_user.id):
        await callback.answer("⏳ Slow down a bit.", show_alert=False)
        return
    data = await state.get_data()

    nas_root = Path(NAS_ROOT_PATH)
    target = safe_resolve(nas_root, data['delete_path'])
    if target is None:
        await callback.message.edit_text("❌ Invalid path", parse_mode="HTML")
        await state.clear()
        await callback.answer()
        return

    try:
        if target.exists() and target.is_dir():
            trash_dir = nas_root / ".trash"
            await asyncio.to_thread(lambda: trash_dir.mkdir(exist_ok=True))
            trash_dest = trash_dir / f"{int(time.time())}_{target.name}"
            await asyncio.to_thread(lambda: shutil.move(str(target), str(trash_dest)))
            await callback.message.edit_text(
                f"🗑️ <b>Folder moved to trash</b>\n<code>/{data['delete_path']}</code>",
                parse_mode="HTML",
            )
            logger.info(f"User {callback.from_user.id} moved folder to trash: {target} → {trash_dest}")
        else:
            await callback.answer("❌ Folder not found", show_alert=True)
    except OSError as e:
        logger.error(f"Error moving folder to trash: {e}", exc_info=True)
        await callback.message.edit_text(
            f"❌ <b>Delete failed</b>\n<i>{type(e).__name__}</i>",
            parse_mode="HTML",
        )

    await state.clear()
    await callback.answer()

# --- CANCEL ---

@router.callback_query(F.data == "fdir_cancel")
async def cancel_folder_op(callback: types.CallbackQuery, state: FSMContext):
    """Cancel folder manager operation."""
    await state.clear()
    await callback.message.edit_text("🚪 <b>Closed.</b>", parse_mode="HTML")
    await callback.answer()
