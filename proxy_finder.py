#!/usr/bin/env python3
"""Fast proxy finder using Playwright (not requests). Tests if veoaifree loads without rate-limit."""
import sys, time, random, json, os
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as req
from playwright.sync_api import sync_playwright

URL = "https://veoaifree.com/grok-ai-video-generator/"
CACHE_FILE = os.path.join(os.path.dirname(__file__), "working_proxies.json")

def fetch_proxies():
    sources = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=1000&country=all&ssl=yes&anonymity=elite",
        "https://api.proxyscrape.com/v3/free-proxy-list/get?request=displayproxies&proxytype=http&country=all&timeout=1000",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
        "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    ]
    pxs = set()
    for url in sources:
        try:
            r = req.get(url, timeout=5)
            if r.status_code == 200:
                for line in r.text.split('\n'):
                    v = line.strip()
                    if v and ':' in v and not v.startswith('#'):
                        parts = v.split(':')
                        if len(parts) == 2 and parts[1].isdigit():
                            pxs.add(v)
        except:
            pass
    return list(pxs)

def test_proxy_pw(proxy):
    """Test a single proxy with Playwright. Returns proxy addr if page loads without rate-limit."""
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(
                headless=True,
                proxy={"server": f"http://{proxy}"},
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                      '--disable-blink-features=AutomationControlled']
            )
            ctx = br.new_context(
                viewport={'width': 1280, 'height': 720}, locale='en-US',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
            )
            pg = ctx.new_page()
            try:
                pg.goto(URL, timeout=8000, wait_until='domcontentloaded')
                body = pg.evaluate("()=>document.body?.innerText||''")
                br.close()
                if 'rate limit' in body.lower() or 'limit reached' in body.lower():
                    return None
                if len(body) > 500:
                    return proxy
            except:
                try: br.close()
                except: pass
    except:
        pass
    return None

def find_proxies(count=3, max_test=100):
    """Find `count` working proxies. Tests up to `max_test` total."""
    print("[proxy] Fetching proxy list...", flush=True)
    all_proxies = fetch_proxies()
    print(f"[proxy] {len(all_proxies)} scraped", flush=True)
    random.shuffle(all_proxies)
    to_test = all_proxies[:max_test]

    working = []
    print(f"[proxy] Testing {len(to_test)} with Playwright (5 parallel)...", flush=True)

    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(test_proxy_pw, p): p for p in to_test}
        done = 0
        for f in as_completed(futs, timeout=120):
            done += 1
            try:
                r = f.result()
                if r:
                    working.append(r)
                    print(f"  [{done}/{len(to_test)}] WORKING: {r}", flush=True)
                    if len(working) >= count:
                        break
                else:
                    if done % 10 == 0:
                        print(f"  [{done}/{len(to_test)}] tested...", flush=True)
            except:
                pass
            time.sleep(0.5)  # small delay between results

    if working:
        with open(CACHE_FILE, 'w') as f:
            json.dump({'working_proxies': working, 'time': time.time()}, f, indent=2)
        print(f"[proxy] Found {len(working)} working: {working}", flush=True)
    else:
        print("[proxy] NONE found", flush=True)

    return working

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    find_proxies(count=n)
