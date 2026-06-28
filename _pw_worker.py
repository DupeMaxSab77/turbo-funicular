"""Standalone Playwright proxy test worker. Run via subprocess."""
import pickle, base64, sys, os, time, json

def _batch_playwright_test(proxies, max_clean=3):
    from playwright.sync_api import sync_playwright
    clean = []
    if not proxies: return clean
    test_url = "https://veoaifree.com/grok-ai-video-generator/"
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True, args=[
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            for px in proxies:
                if isinstance(px, tuple):
                    ptype, paddr = px
                    scheme = "socks5" if ptype == "socks5" else "http"
                    proxy_str = f"{scheme}://{paddr}"
                else:
                    proxy_str = f"http://{px}"
                ctx = br.new_context(proxy={"server": proxy_str},
                    viewport={'width': 1280, 'height': 720}, locale='en-US',
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
                pg = ctx.new_page()
                try:
                    t0 = time.time()
                    pg.goto(test_url, timeout=10000, wait_until='domcontentloaded')
                    elapsed = time.time() - t0
                    body = pg.evaluate("()=>document.body?.innerText||''")
                    if 'rate limit' not in body.lower() and 'limit reached' not in body.lower() and len(body) > 500 and elapsed < 4.5:
                        clean.append(px)
                        if len(clean) >= max_clean: break
                except: pass
                try: ctx.close()
                except: pass
            br.close()
    except: pass
    return clean

if __name__ == '__main__':
    data = pickle.loads(base64.b64decode(sys.argv[1]))
    proxies, max_clean = data
    result = _batch_playwright_test(proxies, max_clean=max_clean)
    print(json.dumps([[list(x) if isinstance(x, tuple) else x for x in result]]))
