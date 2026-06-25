FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright: installs Chromium + all system deps in one shot
RUN python -m playwright install --with-deps chromium

COPY . .

EXPOSE 3000

CMD ["python", "server.py"]
