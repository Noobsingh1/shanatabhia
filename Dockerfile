FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data dirs
RUN mkdir -p /data /thumbs && chown -R 1000:1000 /data /thumbs

# Non-root user
RUN useradd -m -u 1000 botuser
USER botuser

# Healthcheck (Koyeb/Railway/Render will hit this)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

ENV XDG_DOWNLOAD_DIR=/data

CMD ["python", "-u", "main.py"]
