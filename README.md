<div align="center">

# telegram-nas-manager

A private NAS file management bot for Telegram — upload, browse, search, and organize files directly from your phone.

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![aiogram](https://img.shields.io/badge/aiogram-3.x-26A5E4?style=flat-square&logo=telegram&logoColor=white)](https://github.com/aiogram/aiogram)
[![License](https://img.shields.io/badge/License-MIT-6B7280?style=flat-square)](LICENSE)

</div>

---

## Features

- **Upload** — Send any file; the bot detects its category and suggests the right folder. Navigate the full directory tree to pick a custom destination.
- **Browse** — 🔍 Browse NAS by category with pagination and file details.
- **Search** — 🔎 Recursive filename search across the entire NAS with pagination.
- **Rename** — Rename files inline from the file options menu.
- **Folder Manager** — 📂 Create, rename, or delete directories via interactive menu.
- **Trash** — 🗑 Move deleted items to `.trash/`. View, restore, or permanently delete.
- **Storage Monitor** — 📊 Disk usage with status indicator (🟢 🟡 🔴).
- **Single-user auth** — One authorized Telegram ID. All others blocked.
- **Security** — Path traversal protection, filename validation, input sanitization.

---

## UI Design

Clean, minimal interface — emoji + bold headers, no verbose text.

```
📂 NAS Manager
├── 🔍 Browse     — Category selection
├── 🔎 Find       — Filename search  
├── 📂 Folders    — Create/rename/delete
├── 🗑 Trash      — Restore/delete items
└── 📊 Storage    — Disk usage & status
```

All messages are concise with icons for quick scanning. Error messages use ❌, confirmations use ✅.

---

## Tech Stack

| Layer | Tool |
|---|---|
| Language | Python 3.12+ |
| Bot Framework | [aiogram](https://github.com/aiogram/aiogram) v3 |
| Runtime | Docker |

---

## Project Structure

```
telegram-nas-manager/
├── main.py              # Entry point
├── config.py            # Config, auth
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── setup.sh
├── handlers/
│   ├── commands.py      # /start, /space, keyboard buttons
│   ├── files.py         # Upload flow, rename
│   ├── search.py        # Browser, /find, file actions
│   ├── folders.py       # Create/delete folder wizards
│   └── trash.py         # Trash viewer and management
└── utils/
    └── storage.py       # Disk utils, path validation, sanitization
```

---

## Setup

### 1. Clone

```bash
git clone https://github.com/AssachanDev/telegram-nas-manager.git
cd telegram-nas-manager
```

### 2. Configure

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `BOT_TOKEN` | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID — get from [@userinfobot](https://t.me/userinfobot) |
| `HOST_DATA_DIR` | Absolute path to your NAS directory on the host |
| `RATE_LIMIT_INTERVAL` | Seconds between destructive operations per user (default 2.0) |
| `MAX_FILE_SIZE_MB` | Upload size ceiling (default 500) |

### 3. Run

```bash
bash setup.sh
```

Logs: `docker compose logs -f`

---

## Usage

All operations via a persistent Reply Keyboard — no slash commands needed.

| Button | Action |
|---|---|
| 🔍 Browse | Browse by category, view files with sizes |
| 🔎 Find | Search by filename (recursive) |
| 📂 Folders | Create, rename, or delete directories |
| 🗑 Trash | View deleted items, restore or delete |
| 📊 Storage | Show disk usage with status |
| _(upload file)_ | Auto-categorize and select destination |

---

## File Categories

Auto-suggested destination based on extension:

| Category | Extensions |
|---|---|
| Documents | `.pdf` `.docx` `.xlsx` `.txt` `.pptx` `.md` |
| Media | `.png` `.jpg` `.jpeg` `.mp4` `.mov` `.gif` `.mp3` `.wav` `.mkv` |
| Data | `.csv` `.json` `.sql` `.xml` `.yaml` `.yml` |
| Scripts | `.py` `.sh` `.js` `.ts` `.go` `.c` `.cpp` |
| Archives | `.zip` `.tar` `.gz` `.7z` `.rar` |
| Other | _(everything else)_ |

---

## Trash & Recovery

Files and folders are moved to `.trash/` with a Unix timestamp prefix — never immediately deleted.

```
/mnt/nas/.trash/
├── 1711900000_report.pdf
└── 1711900050_old-project/
```

From the **Trash** menu you can:
- **Restore** an item — it lands in `/Restored/` at the root of your NAS, not the original location. If a name collision happens it gets a `(1)`, `(2)` suffix.
- **Delete forever** — gone, no further recovery.
- **Empty Trash** — nukes everything in `.trash/` at once.

---

## Operations

### Updating

```bash
git pull
bash setup.sh         # rebuilds the image and restarts the container
```

The bot's only state on the host is the NAS directory itself; in-memory caches (active search results, pending uploads) reset on restart. Anyone mid-flow needs to start over.

### Backups

The bot writes everything to `HOST_DATA_DIR`. Back that path up with whatever you use for the rest of your NAS — rsync, restic, ZFS snapshots, Synology backup, etc. There's no separate database to dump.

### Logs

```bash
docker compose logs -f
```

Every destructive action (rename, delete, move-to-trash, restore, empty-trash, folder create/rename/delete) is logged with the user ID and target path, so there's an audit trail.

### Authorization

The bot is single-user — only the Telegram ID set in `TELEGRAM_CHAT_ID` can reach any handler. An [auth middleware](utils/middleware.py) drops every other update before it gets routed, so neither commands nor lingering callback buttons work for outsiders.

---

## License

[MIT](LICENSE)
