FROM python:3.11-slim-bookworm

# System dependencies: Chromium, virtual display, video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    xvfb \
    ffmpeg \
    libglib2.0-0 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libasound2 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir flask gunicorn && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 10000

CMD ["python", "server.py"]
