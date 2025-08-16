import aiohttp
import asyncio
import os

API_BASE = os.getenv("API_BASE", "https://open-dragonfly-vonex-c2746ec1.koyeb.app/download?url=")

async def fetch_metadata(share_url: str, timeout: int = 40) -> dict:
    url = API_BASE + share_url.strip()
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()
