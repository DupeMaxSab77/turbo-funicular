FROM python:3.11-slim

# Full system deps for Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright deps
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 libatspi2.0-0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2t64 libwayland-client0 \
    libglib2.0-0 libsecret-1-0 \
    # Fonts
    fonts-noto-color-emoji fonts-freefont-ttf fonts-liberation \
    # Build tools
    wget curl gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright: install browser deps first, then browser binary
RUN python -m playwright install-deps chromium && \
    python -m playwright install chromium

# App
COPY . .

EXPOSE 3000

CMD ["python", "server.py"]
