import os
import asyncio
import logging
import time
from typing import Optional, Callable

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import Command

import aiohttp
import aria2p
from aiohttp import web
from dotenv import load_dotenv

# ---------- Config / Env ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "@admin")
API_BASE = os.getenv("API_BASE", "https://open-dragonfly-vonex-c2746ec1.koyeb.app/download?url=")

PYROGRAM_API_ID = os.getenv("PYROGRAM_API_ID")
PYROGRAM_API_HASH = os.getenv("PYROGRAM_API_HASH")

ARIA2_PORT = int(os.getenv("ARIA2_PORT", "6800"))
RPC_SECRET = os.getenv("RPC_SECRET", "secret123")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/data")
COOKIES_FILE = os.getenv("COOKIES_FILE", "/app/cookies.txt")  # optional

HEALTH_PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("terabox-bot")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

TWO_GB = 2 * 1024 * 1024 * 1024

WELCOME_TEXT = (
    "👋 **Welcome to Terabox Video Bot!**\n\n"
    "Send me a Terabox share link and I will download and upload the video for you.\n\n"
    "⚠️ **Limit:** Files above 2GB are not supported.\n"
    "ℹ️ Send your Terabox link to start."
)

# ---------- Utils ----------
def human_bytes(n: int) -> str:
    if n is None:
        return "?"
    units = ["B", "KB", "MB", "GB", "TB"]
    s = 0
    f = float(n)
    while f >= 1024 and s < len(units)-1:
        f /= 1024
        s += 1
    return f"{f:.2f} {units[s]}"

def format_bar(percent: float, width: int = 22) -> str:
    if percent is None:
        percent = 0.0
    percent = max(0.0, min(100.0, percent))
    filled = int(round((percent/100.0) * width))
    return "█" * filled + "░" * (width - filled)

def fmt_eta(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

# ---------- Health Server ----------
async def health_handler(_):
    return web.Response(text="ok", status=200)

async def start_health_server():
    app = web.Application()
    app.router.add_get("/health", health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    log.info(f"Health server listening on :{HEALTH_PORT}")

# ---------- Metadata fetch from API ----------
async def fetch_metadata(share_url: str) -> dict:
    url = API_BASE + aiohttp.helpers.quote(share_url, safe="")
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url, timeout=60) as r:
            r.raise_for_status()
            data = await r.json(content_type=None)
            return data or {}

# ---------- Aria2 RPC ----------
async def _spawn_aria2_rpc():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    args = [
        "aria2c",
        "--enable-rpc=true",
        "--rpc-listen-all=false",
        f"--rpc-listen-port={ARIA2_PORT}",
        f"--rpc-secret={RPC_SECRET}",
        "--continue=true",
        "--daemon=false",
        "--check-certificate=false",
        "--summary-interval=0",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--console-log-level=warn",
    ]
    if os.path.exists(COOKIES_FILE):
        args.append(f"--load-cookies={COOKIES_FILE}")

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=DOWNLOAD_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(0.7)
    return proc

async def download_with_progress(
    url: str,
    out_name: str,
    on_progress: Optional[Callable[[dict], asyncio.Future]] = None,
    timeout_sec: int = 60 * 60,
) -> str:
    aria2_proc = await _spawn_aria2_rpc()
    start_ts = time.time()
    try:
        api = None
        for _ in range(25):
            try:
                api = aria2p.API(aria2p.Client(host="http://localhost", port=ARIA2_PORT, secret=RPC_SECRET))
                _ = api.get_version()
                break
            except Exception:
                await asyncio.sleep(0.2)
        if api is None:
            raise RuntimeError("Failed to connect to aria2 RPC")

        options = {
            "dir": DOWNLOAD_DIR,
            "out": out_name,
            "header": [
                "User-Agent: Mozilla/5.0",
                "Referer: https://www.1024terabox.com/",
            ],
        }
        if os.path.exists(COOKIES_FILE):
            options["load-cookies"] = COOKIES_FILE

        download = api.add_uris([url], options=options)

        last_update = 0.0
        while True:
            if (time.time() - start_ts) > timeout_sec:
                try:
                    api.remove(download)
                except Exception:
                    pass
                raise RuntimeError("Download timeout")

            download.update()
            st = download.live
            status = download.status

            if status == "complete":
                break
            if status in ("error", "removed"):
                error_msg = ""
                try:
                    raw = api.client.tell_status(download.gid, ["status", "errorMessage"])
                    error_msg = raw.get("errorMessage") or ""
                except Exception:
                    pass
                raise RuntimeError(f"aria2 status: {status} {('- ' + error_msg) if error_msg else ''}".strip())

            total = int(st.total_length or 0)
            done = int(st.completed_length or 0)
            speed = int(st.download_speed or 0)

            eta = 0
            if speed > 0 and total > 0:
                eta = max(0, int((total - done) / speed))

            percent = (done / total) * 100.0 if total > 0 else 0.0

            now = time.time()
            if on_progress and (now - last_update) >= 1.2:
                last_update = now
                payload = {
                    "percent": percent,
                    "downloaded": done,
                    "total": total,
                    "speed": speed,
                    "eta": eta,
                }
                await on_progress(payload)

            await asyncio.sleep(0.7)

        return os.path.join(DOWNLOAD_DIR, out_name)
    finally:
        if aria2_proc and aria2_proc.returncode is None:
            aria2_proc.terminate()
            try:
                await asyncio.wait_for(aria2_proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                aria2_proc.kill()

# ---------- Upload (Pyrogram optional) ----------
USE_PYRO_UPLOAD = bool(PYROGRAM_API_ID and PYROGRAM_API_HASH)

async def upload_with_pyrogram_progress(chat_id: int, file_path: str, caption: str, file_name: str,
                                        progress_msg: Message):
    from pyrogram import Client
    app = Client(
        name="bot-uploader",
        api_id=int(PYROGRAM_API_ID),
        api_hash=PYROGRAM_API_HASH,
        bot_token=BOT_TOKEN,
        workdir="/tmp/pyro",
        no_updates=True,
    )

    async def progress(current, total):
        percent = 0 if total == 0 else (current / total) * 100.0
        try:
            await progress_msg.edit_text(
                "📤 **Uploading...**\n"
                f"`{os.path.basename(file_path)}`\n\n"
                f"{format_bar(percent)} {percent:.1f}%"
            )
        except Exception:
            pass

    await app.start()
    try:
        await app.send_video(
            chat_id=chat_id,
            video=file_path,
            file_name=file_name,
            caption=caption,
            progress=progress,
        )
    finally:
        await app.stop()

# ---------- Bot Handlers ----------
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
        dl_url = meta.get("download_link") or meta.get("link")

        if not dl_url:
            await status.edit_text(f"❌ Failed to get download link.\nContact {ADMIN_USERNAME}")
            return

        if size_bytes > TWO_GB:
            await status.edit_text("⚠️ Sorry, only files below **2GB** are supported.")
            return

        await status.edit_text(
            f"⬇️ **Downloading File...**\n"
            f"`{file_name}`\n"
            f"Size: {human_bytes(size_bytes)}\n\n"
            f"{format_bar(0)} 0.0%\n"
            f"Speed: 0 MB/s • ETA: 00:00"
        )

        async def on_dl_progress(p):
            bar = format_bar(p["percent"])
            spd = f"{human_bytes(p['speed'])}/s"
            eta = fmt_eta(p["eta"])
            pct = f"{p['percent']:.1f}%"
            txt = (
                f"⬇️ **Downloading File...**\n"
                f"`{file_name}`\n"
                f"Size: {human_bytes(size_bytes)}\n\n"
                f"{bar} {pct}\n"
                f"Speed: {spd} • ETA: {eta}"
            )
            try:
                await status.edit_text(txt)
            except Exception:
                pass

        out_path = await download_with_progress(
            url=dl_url,
            out_name=file_name,
            on_progress=on_dl_progress
        )

        await status.edit_text(
            "📤 **Uploading...**\n"
            f"`{file_name}`\n\n"
            f"{format_bar(0)} 0.0%"
        )

        if USE_PYRO_UPLOAD:
            await upload_with_pyrogram_progress(
                chat_id=message.chat.id,
                file_path=out_path,
                caption="✅ Download Complete",
                file_name=file_name,
                progress_msg=status,
            )
        else:
            from aiogram.types import FSInputFile
            try:
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=FSInputFile(out_path, filename=file_name),
                    caption="✅ Download Complete",
                )
            except Exception:
                await bot.send_document(
                    chat_id=message.chat.id,
                    document=FSInputFile(out_path, filename=file_name),
                    caption="✅ Download Complete",
                )
            try:
                await status.delete()
            except Exception:
                await status.edit_text("✅ Download Complete")

    except Exception as e:
        logging.exception("Error handling link")
        msg = f"❌ Download failed. Contact {ADMIN_USERNAME}\n\n`{e}`"
        try:
            await status.edit_text(msg)
        except Exception:
            await message.reply(msg)

# ---------- Entrypoint ----------
async def main():
    await asyncio.gather(
        start_health_server(),
        dp.start_polling(bot)
    )

if __name__ == "__main__":
    asyncio.run(main())
