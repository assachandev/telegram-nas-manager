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
        await message.answer("⛔ <b>Access denied</b>", parse_mode="HTML")
        return
    await message.answer(
        "<b>🗄️  NAS Manager</b>\n"
        "<i>Send any file to upload, or tap a button below.</i>\n\n"
        "🔍 <b>Browse</b>  —  walk through categories\n"
        "🔎 <b>Find</b>  —  search filenames\n"
        "📂 <b>Folders</b>  —  create / rename / delete\n"
        "🗑 <b>Trash</b>  —  restore or empty\n"
        "📊 <b>Storage</b>  —  disk usage",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )

@router.message(Command("space"))
@router.message(lambda m: m.text == "📊 Storage")
async def cmd_space(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    usage = get_disk_usage(NAS_ROOT_PATH)
    if not usage:
        await message.answer("❌ <b>Storage error</b>", parse_mode="HTML")
        return
    used_pct = usage['percent']
    if used_pct < 70:
        status, mood = "🟢", "plenty of room"
    elif used_pct < 90:
        status, mood = "🟡", "getting tight"
    else:
        status, mood = "🔴", "almost full"
    bar = generate_progress_bar(used_pct, PROGRESS_BAR_LENGTH)
    text = (
        f"<b>📊 Storage</b>  {status}\n"
        f"<code>{bar}</code>  {used_pct:.0f}%\n\n"
        f"<b>Used:</b>  {format_bytes(usage['used'])}\n"
        f"<b>Free:</b>  {format_bytes(usage['free'])}\n"
        f"<b>Total:</b> {format_bytes(usage['total'])}\n\n"
        f"<i>{mood}</i>"
    )
    await message.answer(text, parse_mode="HTML")

@router.message(lambda m: m.text == "🔍 Browse")
async def cmd_browse_shortcut(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    from handlers.search import get_category_keyboard
    await message.answer(
        "<b>🔍 Browse</b>\n<i>Pick a category to walk into:</i>",
        parse_mode="HTML",
        reply_markup=get_category_keyboard(),
    )

@router.message(lambda m: m.text == "📂 Folders")
async def cmd_folders_shortcut(message: types.Message):
    if not is_authorized(message.from_user.id):
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="➕  Create folder",  callback_data="fdir_mode:create")
    builder.button(text="✏️  Rename folder",   callback_data="fdir_mode:rename")
    builder.button(text="🗑️  Delete folder",   callback_data="fdir_mode:delete")
    builder.button(text="❌  Cancel",          callback_data="fdir_cancel")
    builder.adjust(1)
    await message.answer(
        "<b>📂 Folder manager</b>\n<i>What would you like to do?</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup(),
    )
