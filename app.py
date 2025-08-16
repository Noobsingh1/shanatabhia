import os
import asyncio
import logging
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

from dotenv import load_dotenv
load_dotenv()

from api_client import fetch_metadata
from downloader import download_with_progress
from uploader import upload_video_with_progress
from utils import human_bytes, format_bar, fmt_eta

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("7670198611:AAEwf0-xqEiBHocibNAXMRqz08TIVFWz8PM")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

TWO_GB = 2 * 1024 * 1024 * 1024

WELCOME_TEXT = (
    "👋 **Welcome to Terabox Video Bot!**\n\n"
    "Send me a Terabox share link and I will download and upload the video for you.\n\n"
    "⚠️ **Limit:** Files above 2GB are not supported.\n"
    "ℹ️ Send your Terabox link to start."
)

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(WELCOME_TEXT)

@dp.message(F.text)
async def handle_link(message: Message):
    text = (message.text or "").strip()
    if not text or ("terabox" not in text and "1024terabox" not in text):
        await message.reply("❌ Please send a valid Terabox share link.")
        return

    status = await message.reply("🔍 Fetching file info...")

    try:
        meta = await fetch_metadata(text)
        file_name = meta.get("file_name") or "video.mp4"
        size_bytes = int(meta.get("size_bytes") or 0)
        thumb_url = meta.get("thumbnail")
        dl_url = meta.get("download_link") or meta.get("link")

        if not dl_url:
            await status.edit_text(f"❌ Failed to get download link.\nContact {ADMIN_USERNAME}")
            return

        # 2GB limit
        if size_bytes > TWO_GB:
            await status.edit_text("⚠️ Sorry, only files below **2GB** are supported.")
            return

        # Announce start downloading
        await status.edit_text(
            f"⬇️ **Downloading File...**\n"
            f"`{file_name}`\n"
            f"Size: {human_bytes(size_bytes)}\n\n"
            f"{format_bar(0)} 0.0%\n"
            f"Speed: 0 MB/s • ETA: 00:00"
        )

        # Download thumbnail (if any)
        thumb_path: Optional[str] = None
        if thumb_url:
            try:
                import aiohttp, pathlib
                thumb_path = f"/thumbs/{os.path.basename(file_name)}.jpg"
                async with aiohttp.ClientSession() as sess:
                    async with sess.get(thumb_url) as r:
                        if r.status == 200:
                            data = await r.read()
                            pathlib.Path(thumb_path).parent.mkdir(parents=True, exist_ok=True)
                            with open(thumb_path, "wb") as f:
                                f.write(data)
            except Exception:
                thumb_path = None

        # Download via aria2 with progress
        async def on_dl_progress(p):
            bar = format_bar(p["percent"])
            spd = f"{human_bytes(p['speed'])}/s"
            eta = fmt_eta(p["eta"])
            pct = f"{p['percent']:.1f}%"
            text = (
                f"⬇️ **Downloading File...**\n"
                f"`{file_name}`\n"
                f"Size: {human_bytes(size_bytes)}\n\n"
                f"{bar} {pct}\n"
                f"Speed: {spd} • ETA: {eta}"
            )
            try:
                await status.edit_text(text)
            except Exception:
                pass

        out_path = await download_with_progress(
            url=dl_url,
            out_name=file_name,
            on_progress=on_dl_progress
        )

        # Switch to uploading
        await status.edit_text(
            "📤 **Uploading...**\n"
            f"`{file_name}`\n\n"
            f"{format_bar(0)} 0.0%"
        )

        async def on_ul_progress(sent: int, total: int):
            percent = 0.0 if total == 0 else (sent / total) * 100.0
            bar = format_bar(percent)
            try:
                await status.edit_text(
                    "📤 **Uploading...**\n"
                    f"`{file_name}`\n\n"
                    f"{bar} {percent:.1f}%"
                )
            except Exception:
                pass

        # Upload via Telethon with progress
        await upload_video_with_progress(
            chat_id=message.chat.id,
            file_path=out_path,
            caption="✅ Download Complete",
            thumb_path=thumb_path,
            file_name=file_name,
            progress_cb=on_ul_progress,
        )

        # Final notice
        try:
            await status.delete()
        except Exception:
            await status.edit_text("✅ Download Complete")

    except Exception as e:
        logging.exception("Error handling link")
        try:
            await status.edit_text(f"❌ Download failed. Contact {ADMIN_USERNAME}\n\n`{e}`")
        except Exception:
            await message.reply(f"❌ Download failed. Contact {ADMIN_USERNAME}\n\n`{e}`")
