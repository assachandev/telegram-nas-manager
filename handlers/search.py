import asyncio
import logging
from datetime import datetime
from pathlib import Path
from aiogram import Router, types, F, Bot
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import NAS_ROOT_PATH, CATEGORIES, PAGE_SIZE, PROGRESS_BAR_LENGTH, is_authorized
from utils.storage import format_bytes, is_rate_limited, get_disk_usage, generate_progress_bar, safe_resolve

logger = logging.getLogger(__name__)
router = Router()

class FindState(StatesGroup):
    waiting_for_query = State()

# Cache for /find pagination: user_id -> {"query": str, "results": list}
# Limited to _MAX_FIND_CACHE entries to prevent unbounded memory growth
find_cache: dict = {}
FIND_PAGE_SIZE = 10
_MAX_FIND_CACHE = 200

def get_category_keyboard():
    """Build category selector keyboard for browsing files."""
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=f"📁 {category}", callback_data=f"list:{category}:0")
    builder.adjust(2)
    builder.row(types.InlineKeyboardButton(text="❌ Cancel", callback_data="search_cancel"))
    return builder.as_markup()

@router.callback_query(F.data == "search_cancel")
async def search_cancel(callback: types.CallbackQuery):
    """Cancel current search/browse operation."""
    await callback.message.edit_text("Cancelled.", parse_mode="HTML")
    await callback.answer()

@router.message(Command("search"))
async def cmd_search(message: types.Message):
    """Start file browser with category selection."""
    if not is_authorized(message.from_user.id):
        return
    await message.answer("<b>Browse</b>\nSelect a category:", parse_mode="HTML", reply_markup=get_category_keyboard())

@router.message(lambda m: m.text == "🔎 Find")
async def find_button(message: types.Message, state: FSMContext):
    """Start file search prompt."""
    if not is_authorized(message.from_user.id):
        return
    await state.set_state(FindState.waiting_for_query)
    await message.answer("<b>Find</b>\n\nEnter a filename to search across the NAS:", parse_mode="HTML")

@router.message(FindState.waiting_for_query)
async def find_query_received(message: types.Message, state: FSMContext):
    """Process user search query."""
    await state.clear()
    query = message.text.strip() if message.text else None
    if not query:
        await message.answer("❌ Please type a filename to search for.", parse_mode="HTML")
        return
    if len(query) > 200:
        await message.answer("❌ Search query too long (max 200 characters).", parse_mode="HTML")
        return
    await _run_find(message, query)

@router.message(Command("find"))
async def cmd_find(message: types.Message, command: CommandObject):
    """Search files by name: /find [filename]."""
    if not is_authorized(message.from_user.id):
        return
    query = command.args
    if not query:
        await message.answer("🔍 <b>Usage:</b> <code>/find [filename]</code>", parse_mode="HTML")
        return
    await _run_find(message, query)

async def _run_find(message: types.Message, query: str):
    """Execute file search across all directories."""
    query_lower = query.lower()
    nas_root = Path(NAS_ROOT_PATH)

    def do_search():
        results = []
        for file in nas_root.rglob("*"):
            if file.is_file() and query_lower in file.name.lower():
                if any(part.startswith('.') for part in file.parts):
                    continue
                rel_dir = str(file.parent.relative_to(nas_root))
                stat = file.stat()
                results.append((rel_dir, file.name, stat.st_size, stat.st_mtime))
        results.sort(key=lambda x: x[3], reverse=True)
        return results

    found = await asyncio.to_thread(do_search)

    if not found:
        await message.answer(f"No files found matching <code>{query}</code>.", parse_mode="HTML")
        return

    # Evict oldest entry if cache is full (LRU-style)
    if len(find_cache) >= _MAX_FIND_CACHE:
        oldest_key = next(iter(find_cache))
        find_cache.pop(oldest_key, None)

    find_cache[message.from_user.id] = {"query": query, "results": found}
    await _send_find_page(message, message.from_user.id, 0)

async def _send_find_page(target: types.Message | types.CallbackQuery, user_id: int, page: int):
    """Send paginated search results with file options."""
    cache = find_cache.get(user_id)
    if not cache:
        txt = "❌ Search expired, please run /find again."
        if isinstance(target, types.Message):
            await target.answer(txt)
        else:
            await target.message.edit_text(txt)
        return

    results = cache["results"]
    query = cache["query"]
    total = len(results)
    total_pages = max(1, (total + FIND_PAGE_SIZE - 1) // FIND_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    builder = InlineKeyboardBuilder()
    skipped_count = 0
    for rel_dir, name, size, mtime in results[page * FIND_PAGE_SIZE:(page + 1) * FIND_PAGE_SIZE]:
        size_str = format_bytes(size)
        cb = f"file_opts:{rel_dir}:{name}"
        if len(cb.encode()) <= 64:
            builder.button(text=f"📄 {name}  [{size_str}]", callback_data=cb)
        else:
            skipped_count += 1
            logger.warning(f"Skipped file (name too long for callback): {name}")
    builder.adjust(1)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️ Prev", callback_data=f"find_page:{user_id}:{page-1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="Next ➡️", callback_data=f"find_page:{user_id}:{page+1}"))
    if nav:
        builder.row(*nav)
    builder.row(types.InlineKeyboardButton(text="❌ Close", callback_data="search_cancel"))

    text = (
        f"<b>Search</b>  <code>{query}</code>\n"
        f"{total} result(s)  ·  Page {page+1} of {total_pages}"
    )
    if skipped_count > 0:
        text += f"\n⚠️ {skipped_count} file(s) with very long names not accessible"
    if isinstance(target, types.Message):
        await target.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("find_page:"))
async def find_page_callback(callback: types.CallbackQuery):
    """Handle pagination for search results."""
    _, user_id_str, page_str = callback.data.split(":", 2)
    # Only allow the original requester to paginate their results
    if str(callback.from_user.id) != user_id_str:
        await callback.answer("❌ Not your search.", show_alert=True)
        return
    await _send_find_page(callback, int(user_id_str), int(page_str))
    await callback.answer()

@router.callback_query(F.data.startswith("list:"))
async def list_files_in_category(callback: types.CallbackQuery):
    """Browse files in a category or folder."""
    _, rel_path, page_str = callback.data.split(":", 2)
    page = int(page_str)

    nas_root = Path(NAS_ROOT_PATH)
    path = safe_resolve(nas_root, rel_path)
    if path is None or not path.exists():
        await callback.answer("❌ Folder not found.", show_alert=True)
        return

    def get_contents():
        subdirs = sorted([f for f in path.iterdir() if f.is_dir() and not f.name.startswith('.')],
                         key=lambda f: f.name)
        files = sorted([f for f in path.iterdir() if f.is_file()],
                       key=lambda f: f.stat().st_mtime, reverse=True)
        return subdirs, files

    subdirs, all_files = await asyncio.to_thread(get_contents)

    entries = [("dir", d) for d in subdirs] + [("file", f) for f in all_files]
    total = len(entries)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    builder = InlineKeyboardBuilder()
    for kind, item in entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        if kind == "dir":
            sub_rel = f"{rel_path}/{item.name}"
            cb = f"list:{sub_rel}:0"
            if len(cb.encode()) <= 64:
                builder.button(text=f"📁 {item.name}/", callback_data=cb)
        else:
            size_str = format_bytes(item.stat().st_size)
            cb = f"file_opts:{rel_path}:{item.name}"
            if len(cb.encode()) <= 64:
                builder.button(text=f"📄 {item.name}  [{size_str}]", callback_data=cb)
    builder.adjust(1)

    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(text="⬅️ Prev", callback_data=f"list:{rel_path}:{page-1}"))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(text="Next ➡️", callback_data=f"list:{rel_path}:{page+1}"))
    if nav:
        builder.row(*nav)

    parts = rel_path.strip("/").split("/")
    if len(parts) > 1:
        parent_rel = "/".join(parts[:-1])
        builder.row(types.InlineKeyboardButton(text="⬅️ Up", callback_data=f"list:{parent_rel}:0"))
    else:
        builder.row(types.InlineKeyboardButton(text="⬅️ Categories", callback_data="back_to_categories"))
    builder.row(types.InlineKeyboardButton(text="❌ Cancel", callback_data="search_cancel"))

    display_path = rel_path.replace("/", " / ")
    await callback.message.edit_text(
        f"<b>{display_path}</b>\n"
        f"{len(subdirs)} folder(s)  ·  {len(all_files)} file(s)  ·  Page {page+1}/{total_pages}",
        parse_mode="HTML", reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data == "back_to_categories")
async def back_to_categories(callback: types.CallbackQuery):
    """Return to category selection."""
    await callback.message.edit_text("<b>Browse</b>\nSelect a category:", parse_mode="HTML", reply_markup=get_category_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("file_opts:"))
async def show_file_options(callback: types.CallbackQuery):
    """Show download/rename/delete options for a file."""
    _, rel_dir, file_name = callback.data.split(":", 2)

    nas_root = Path(NAS_ROOT_PATH)
    file_path = safe_resolve(nas_root, rel_dir)
    if file_path is None:
        await callback.answer("❌ Invalid path.", show_alert=True)
        return
    file_path = file_path / file_name

    size_str = mtime_str = "N/A"
    if file_path.exists():
        stat = file_path.stat()
        size_str = format_bytes(stat.st_size)
        mtime_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")

    top_cat = rel_dir.split("/")[0]
    builder = InlineKeyboardBuilder()
    builder.button(text="📥 Download", callback_data=f"download:{rel_dir}:{file_name}")
    builder.button(text="✏️ Rename", callback_data=f"rename_ask:{rel_dir}:{file_name}")
    builder.button(text="🗑️ Delete", callback_data=f"del_conf:{rel_dir}:{file_name}")
    builder.button(text="⬅️ Back", callback_data=f"list:{top_cat}:0")
    builder.button(text="❌ Cancel", callback_data="search_cancel")
    builder.adjust(2)

    await callback.message.edit_text(
        f"<b>{file_name}</b>\n\n"
        f"<pre>"
        f"Path      /{rel_dir}/\n"
        f"Size      {size_str}\n"
        f"Modified  {mtime_str}"
        f"</pre>",
        parse_mode="HTML", reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("download:"))
async def send_file_to_user(callback: types.CallbackQuery, bot: Bot):
    """Download a file from the NAS."""
    _, rel_dir, file_name = callback.data.split(":", 2)

    nas_root = Path(NAS_ROOT_PATH)
    base = safe_resolve(nas_root, rel_dir)
    if base is None:
        await callback.answer("❌ Invalid path.", show_alert=True)
        return
    file_path = base / file_name

    if not file_path.exists():
        await callback.answer("❌ File no longer exists.", show_alert=True)
        return

    await callback.message.edit_text(f"Sending <code>{file_name}</code>...", parse_mode="HTML")
    try:
        await bot.send_document(callback.from_user.id, types.FSInputFile(str(file_path)))
        await callback.message.edit_text(f"✅ <b>Sent</b>\n<code>{file_name}</code>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error sending file: {e}")
        await callback.message.edit_text("❌ Failed to send file.", parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("del_conf:"))
async def delete_confirmation(callback: types.CallbackQuery):
    """Show delete confirmation for a file."""
    _, rel_dir, file_name = callback.data.split(":", 2)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔥 Yes, Move to Trash", callback_data=f"del_exec:{rel_dir}:{file_name}")
    builder.button(text="❌ Cancel", callback_data=f"file_opts:{rel_dir}:{file_name}")
    builder.adjust(2)

    await callback.message.edit_text(
        f"⚠️ <b>Move to Trash?</b>\n\n"
        f"<code>{file_name}</code>\n\n"
        f"This file will be moved to <code>.trash/</code> and can be recovered manually.",
        parse_mode="HTML", reply_markup=builder.as_markup()
    )
    await callback.answer()

@router.callback_query(F.data.startswith("del_exec:"))
async def delete_file_execution(callback: types.CallbackQuery):
    """Move file to trash (destructive operation with rate limit)."""
    _, rel_dir, file_name = callback.data.split(":", 2)

    if is_rate_limited(callback.from_user.id):
        await callback.answer("⏳ Too fast, wait a moment.", show_alert=True)
        return

    nas_root = Path(NAS_ROOT_PATH)
    base = safe_resolve(nas_root, rel_dir)
    if base is None:
        await callback.answer("❌ Invalid path.", show_alert=True)
        return
    file_path = base / file_name

    try:
        if file_path.exists():
            import time
            trash_dir = nas_root / ".trash"
            await asyncio.to_thread(lambda: trash_dir.mkdir(exist_ok=True))
            trash_dest = trash_dir / f"{int(time.time())}_{file_name}"
            await asyncio.to_thread(lambda: file_path.rename(trash_dest))
            await callback.message.edit_text(f"<b>Moved to Trash</b>\n<code>{file_name}</code>", parse_mode="HTML")
            logger.info(f"User {callback.from_user.id} moved to trash: {file_path}")
        else:
            await callback.answer("❌ File not found (may have been deleted).", show_alert=True)
    except OSError as e:
        logger.error(f"Error moving file to trash: {e}", exc_info=True)
        await callback.answer(f"❌ Error: {type(e).__name__}", show_alert=True)
    await callback.answer()

@router.callback_query(F.data == "check_space_quick")
async def check_space_quick(callback: types.CallbackQuery):
    """Show quick disk usage update."""
    usage = get_disk_usage(NAS_ROOT_PATH)
    if usage:
        bar = generate_progress_bar(usage['percent'], length=PROGRESS_BAR_LENGTH)
        used_pct = usage['percent']
        status_icon = "🟢" if used_pct < 70 else "🟡" if used_pct < 90 else "🔴"

        text = (
            f"<b>Storage</b>  {status_icon}\n"
            f"<code>{bar}  {used_pct:.1f}%</code>\n\n"
            f"{format_bytes(usage['used'])} used  ·  {format_bytes(usage['free'])} free"
        )
        await callback.message.edit_text(text, parse_mode="HTML")
    else:
        await callback.answer("❌ Error reading disk usage.", show_alert=True)
    await callback.answer()
