from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import NAS_ROOT_PATH, PROGRESS_BAR_LENGTH, is_authorized
from utils.storage import get_disk_usage, generate_progress_bar, format_bytes

router = Router()

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Browse"), KeyboardButton(text="🔎 Find")],
        [KeyboardButton(text="📂 Folders"), KeyboardButton(text="🗑 Trash")],
        [KeyboardButton(text="📊 Storage")],
    ],
    resize_keyboard=True,
)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    if not is_authorized(message.from_user.id):
        await message.answer("⛔ <b>Access Denied</b>", parse_mode="HTML")
        return
    await message.answer(
        f"📂 <b>NAS Manager</b>",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

@router.message(Command("space"))
@router.message(lambda m: m.text == "📊 Storage")
async def cmd_space(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    usage = get_disk_usage(NAS_ROOT_PATH)
    if usage:
        used_pct = usage['percent']
        status = "🟢" if used_pct < 70 else "🟡" if used_pct < 90 else "🔴"
        text = (
            f"<b>📊 Storage</b> {status}\n"
            f"{used_pct:.0f}%  {format_bytes(usage['used'])} / {format_bytes(usage['total'])}"
        )
        await message.answer(text, parse_mode="HTML")
    else:
        await message.answer("❌ Storage error", parse_mode="HTML")

@router.message(lambda m: m.text == "🔍 Browse")
async def cmd_browse_shortcut(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    from handlers.search import get_category_keyboard
    await message.answer(
        "<b>Browse</b>\nSelect a category:",
        parse_mode="HTML",
        reply_markup=get_category_keyboard(),
    )

@router.message(lambda m: m.text == "📂 Folders")
async def cmd_folders_shortcut(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Create New Folder", callback_data="fdir_mode:create")
    builder.button(text="✏️ Rename Folder",     callback_data="fdir_mode:rename")
    builder.button(text="🗑️ Delete Folder",     callback_data="fdir_mode:delete")
    builder.button(text="❌ Cancel",             callback_data="fdir_cancel")
    builder.adjust(1)
    await message.answer(
        "<b>Folder Manager</b>\n\nSelect an operation:",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
