import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import InputPeerUser
from telethon.errors import FloodWaitError

# We will reuse bot token for Telethon bot client
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telethon bot session name
SESSION_NAME = "bot_upload_session"

async def upload_video_with_progress(
    chat_id: int,
    file_path: str,
    caption: str = "",
    thumb_path: str | None = None,
    file_name: str | None = None,
    progress_cb=None,
):
    """
    Upload file to chat_id using Telethon bot API with progress callback.
    """
    # Telethon bot client (no api_id/api_hash needed for bot tokens)
    client = TelegramClient(SESSION_NAME, 0, "", bot_token=BOT_TOKEN)

    async with client:
        # Telethon handles filename from path; override if needed
        attributes = None
        thumb = thumb_path if thumb_path and os.path.exists(thumb_path) else None

        async def _progress(sent, total):
            if progress_cb:
                await progress_cb(sent, total)

        try:
            await client.send_file(
                entity=chat_id,
                file=file_path,
                caption=caption,
                thumb=thumb,
                force_document=False,  # send as video if possible
                file_name=file_name,
                progress_callback=_progress
            )
        except FloodWaitError as e:
            await asyncio.sleep(int(e.seconds))
            await client.send_file(
                entity=chat_id,
                file=file_path,
                caption=caption,
                thumb=thumb,
                force_document=False,
                file_name=file_name,
                progress_callback=_progress
            )
