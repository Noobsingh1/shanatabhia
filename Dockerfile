FROM python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    aria2 ffmpeg curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Workdir
WORKDIR /app

# Copy files
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data dir for downloads & thumbs
RUN mkdir -p /data /thumbs

# Non-root user
RUN useradd -m botuser
USER botuser

# Start the bot (worker)
CMD ["python", "-u", "app.py"]
