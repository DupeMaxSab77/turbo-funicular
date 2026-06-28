#!/bin/bash
pkill -9 -f chromium 2>/dev/null
pkill -9 -f "python server" 2>/dev/null
sleep 1
echo '{}' > /root/turbo-funicular/jobs_storage.json
cd /root/turbo-funicular
source venv/bin/activate
exec python server.py
