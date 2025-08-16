import asyncio
import os
import time
from typing import Callable, Optional

import aria2p

ARIA2_PORT = int(os.getenv("ARIA2_PORT", "6800"))
ARIA2_SECRET = os.getenv("RPC_SECRET", "secret123")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/data")

async def _spawn_aria2_rpc():
    """
    Start a temporary aria2c RPC server for this process.
    """
    proc = await asyncio.create_subprocess_exec(
        "aria2c",
        "--enable-rpc=true",
        "--rpc-listen-all=false",
        f"--rpc-listen-port={ARIA2_PORT}",
        f"--rpc-secret={ARIA2_SECRET}",
        "--continue=true",
        "--daemon=false",
        "--check-certificate=false",
        "--summary-interval=0",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--file-allocation=none",
        "--console-log-level=warn",
        cwd=DOWNLOAD_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Give aria2 a moment to boot
    await asyncio.sleep(0.6)
    return proc

async def download_with_progress(
    url: str,
    out_name: str,
    on_progress: Optional[Callable[[dict], asyncio.Future]] = None,
    timeout_sec: int = 60 * 60,
) -> str:
    """
    Download file using aria2 RPC and periodically call on_progress with:
    {
      'percent': float, 'downloaded': int, 'total': int,
      'speed': int (bytes/s), 'eta': int (seconds)
    }
    Returns the absolute file path on success.
    Raises RuntimeError on failure.
    """
    # Ensure download dir exists
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Start aria2 RPC for this download
    aria2_proc = await _spawn_aria2_rpc()

    try:
        # Connect aria2p
        api = None
        for _ in range(20):
            try:
                api = aria2p.API(
                    aria2p.Client(
                        host="http://localhost",
                        port=ARIA2_PORT,
                        secret=ARIA2_SECRET
                    )
                )
                # test
                _ = api.get_version()
                break
            except Exception:
                await asyncio.sleep(0.2)
        if api is None:
            raise RuntimeError("Failed to connect to aria2 RPC")

        # Add download
        options = {"dir": DOWNLOAD_DIR, "out": out_name}
        download = api.add_uris([url], options=options)

        start = time.time()
        last_update = 0.0

        while True:
            api.refresh()
            st = download.live
            status = download.status

            if status == "complete":
                break
            if status in ("error", "removed"):
                raise RuntimeError(f"aria2 status: {status}")

            total = int(st.total_length or 0)
            done = int(st.completed_length or 0)
            speed = int(st.download_speed or 0)

            # ETA
            eta = 0
            if speed > 0 and total > 0:
                remaining = total - done
                eta = max(0, int(remaining / speed))

            percent = 0.0
            if total > 0:
                percent = (done / total) * 100.0

            now = time.time()
            if on_progress and (now - last_update) >= 1.5:
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
        # Stop aria2
        if aria2_proc and aria2_proc.returncode is None:
            aria2_proc.terminate()
            try:
                await asyncio.wait_for(aria2_proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                aria2_proc.kill()
