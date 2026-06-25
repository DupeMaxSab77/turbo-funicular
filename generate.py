#!/usr/bin/env python3
"""
All-in-one: scrapes proxies, tests with Playwright, generates video.
Keeps trying until one works.
"""
import sys, time, random, re, json, os
import requests as req
from concurrent.futures import ThreadPoolExecutor, as_completed
from playwright.sync_api import sync_playwright

URL = "https://veoaifree.com/grok-ai-video-generator/"
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "A golden retriever running through a field of sunflowers at sunset, cinematic slow motion, 4K"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "3.1"
ASPECT_VAL = "VIDEO_ASPECT_RATIO_PORTRAIT" if (sys.argv[3] if len(sys.argv) > 3 else "portrait").lower() == "portrait" else "VIDEO_ASPECT_RATIO_LANDSCAPE"
AD = ["clickiocdn", "google-analytics", "googletagmanager", "doubleclick", "facebook", "hotjar", "clarity", "adnxs", "taboola", "outbrain"]

def is_ad(u): return any(d in u.lower() for d in AD)

def fetch_proxies():
    srcs = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=1000&country=all&ssl=yes&anonymity=elite",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ]
    pxs = set()
    for url in srcs:
        try:
            r = req.get(url, timeout=5)
            for line in r.text.split('\n'):
                v = line.strip()
                if v and ':' in v and not v.startswith('#'):
                    parts = v.split(':')
                    if len(parts) == 2 and parts[1].isdigit(): pxs.add(v)
        except: pass
    return list(pxs)

def quick_test(proxy):
    """HTTP test - 2s timeout. Returns proxy if it responds at all."""
    try:
        r = req.get("https://veoaifree.com/", proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                    timeout=2, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200:
            return proxy
    except: pass
    return None

def try_generate(proxy):
    """Try generating with given proxy. Returns video URL or None."""
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True, proxy={"server": f"http://{proxy}"},
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                      '--disable-blink-features=AutomationControlled'])
            ctx = br.new_context(viewport={'width': 1280, 'height': 720}, locale='en-US',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
            ctx.add_init_script("()=>{Object.defineProperty(navigator,'webdriver',{get:()=>false});}")
            pg = ctx.new_page()
            pg.route("**/*", lambda r: r.abort() if is_ad(r.request.url) else r.continue_())

            try: pg.goto(URL, timeout=12000, wait_until='domcontentloaded')
            except: br.close(); return None

            body = pg.evaluate("()=>document.body?.innerText||''")
            if 'rate limit' in body.lower() or 'limit reached' in body.lower():
                br.close(); return None

            # popups + cookies
            pg.evaluate("()=>{document.querySelectorAll('#suOverlay,.su-overlay,.su-popup,#swContainer,[role=dialog],.modal-overlay,.overlay,.popup-overlay,.modal-backdrop').forEach(e=>e.remove());document.body.style.overflow='auto';'videoCounter=0;cookiClicked=1;ytPopup=1;ytHide=1;popupLockout=active'.split(';').forEach(c=>{document.cookie=c.trim()+';path=/;max-age=86400'});}")

            # fill
            pg.evaluate("""([m,a,t])=>{
                for(const p of document.querySelectorAll('svg path')){if((p.getAttribute('d')||'').includes('M408')){const c=p.closest('svg')||p.closest('a')||p.closest('button')||p.parentElement;if(c)c.dispatchEvent(new MouseEvent('click',{bubbles:true}));break;}}
                setTimeout(()=>{document.querySelector('#modal').value=m;document.querySelector('#modal').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#aspect-ration').value=a;document.querySelector('#aspect-ration').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#fn__include_textarea').value=t;document.querySelector('#fn__include_textarea').dispatchEvent(new Event('input',{bubbles:true}));},300);
            }""", [MODEL, ASPECT_VAL, PROMPT])
            time.sleep(1)

            init = set(pg.evaluate("()=>[...new Set([...document.querySelectorAll('video,source,a,img')].map(e=>e.src||e.href||e.currentSrc||'').filter(Boolean))]"))

            vid = [None]
            def on_r(resp):
                u = resp.url
                if is_ad(u) or 'admin-ajax.php' not in u.lower() or resp.request.method != 'POST': return
                try:
                    b = resp.text().strip()
                    if not b: return
                    if 'rate limit' in b.lower() or 'limit reached' in b.lower():
                        print(f"    [{proxy}] RATE LIMITED in AJAX", flush=True); return
                    if b.startswith('http') and any(x in b.lower() for x in ['.mp4', '.webm']):
                        vid[0] = b.replace('videos/', 'video/')
                        print(f"    [{proxy}] VIDEO: {vid[0][:120]}", flush=True)
                except: pass
            pg.on('response', on_r)

            print(f"    [{proxy}] Clicking generate...", flush=True)
            pg.locator('#generate_it').click(force=True, timeout=5000)

            t0 = time.time(); last_p = -1; p100 = None
            while time.time() - t0 < 300:
                e = int(time.time() - t0)
                if vid[0]: break
                if e % 15 == 0:
                    b2 = pg.evaluate("()=>document.body?.innerText||''")
                    if 'rate limit' in b2.lower() or 'limit reached' in b2.lower():
                        print(f"    [{proxy}] RATE LIMITED at {e}s", flush=True); break
                try:
                    pi = pg.evaluate("()=>{const el=document.querySelector('.show-percentage');if(el){const m=(el.textContent||'').match(/(\\d{1,3})\\s*%/);if(m)return parseInt(m[1]);}return null;}")
                    if pi is not None and pi != last_p:
                        last_p = pi
                        if pi % 20 == 0 or pi >= 90: print(f"    [{proxy}] {pi}%", flush=True)
                        if pi >= 100 and not p100: p100 = time.time(); print(f"    [{proxy}] 100%!", flush=True)
                except: pass
                if p100 and e % 3 == 0:
                    try:
                        us = pg.evaluate("()=>[...new Set([...document.querySelectorAll('ul.fn__generation_list video')].map(v=>v.src||v.currentSrc).filter(Boolean).concat([...document.querySelectorAll('a.only-video-download,a.downloader-video-btn')].map(a=>a.href).filter(Boolean)))]")
                        nu = [u for u in us if u not in init and ('.mp4' in u.lower() or '.webm' in u.lower())]
                        if nu: vid[0] = nu[0]; print(f"    [{proxy}] DOM: {nu[0][:120]}", flush=True); break
                    except: pass
                if p100 and e % 5 == 0:
                    try:
                        d = pg.evaluate("()=>({v:[...document.querySelectorAll('video')].map(v=>v.src||v.currentSrc).filter(Boolean),l:[...document.querySelectorAll('a[href]')].map(a=>a.href).filter(h=>h.includes('.mp4')||h.includes('.webm')||h.includes('blob:')||h.includes('upload'))})")
                        for v in d.get('v', []) + d.get('l', []):
                            if v and ('.mp4' in v or '.webm' in v): vid[0] = v; print(f"    [{proxy}] P100: {v[:120]}", flush=True); break
                        if vid[0]: break
                    except: pass
                time.sleep(2 if p100 else 3)

            pg.remove_listener('response', on_r)
            if not vid[0]:
                pg.screenshot(path=f"/root/turbo-funicular/uploads/debug-{proxy.replace(':', '_')}.png")
            br.close()
            return vid[0]
    except Exception as e:
        print(f"    [{proxy}] ERROR: {e}", flush=True)
        return None


def quick_pw_test(proxy):
    """Quick Playwright test - just check if page loads without rate limit."""
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True, proxy={"server": f"http://{proxy}"},
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            ctx = br.new_context(viewport={'width': 1280, 'height': 720}, locale='en-US',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            pg = ctx.new_page()
            try:
                pg.goto(URL, timeout=8000, wait_until='domcontentloaded')
                body = pg.evaluate("()=>document.body?.innerText||''")
                br.close()
                if 'rate limit' in body.lower() or 'limit reached' in body.lower(): return None
                if len(body) > 500: return proxy
            except:
                try: br.close()
                except: pass
    except: pass
    return None

print("START", flush=True)
all_proxies = fetch_proxies()
print(f"Pool: {len(all_proxies)}", flush=True)
random.shuffle(all_proxies)

# Phase 1: HTTP test to filter dead proxies (fast, parallel)
print("\n=== PHASE 1: HTTP filter ===", flush=True)
alive = []
with ThreadPoolExecutor(max_workers=20) as ex:
    futs = {ex.submit(quick_test, p): p for p in all_proxies[:300]}
    try:
        for f in as_completed(futs, timeout=20):
            try:
                r = f.result()
                if r: alive.append(r)
            except: pass
    except: pass
print(f"Alive after HTTP test: {len(alive)}", flush=True)

# Phase 2: Playwright test on alive ones (slower, 2 parallel)
print("\n=== PHASE 2: Playwright filter ===", flush=True)
clean = []
with ThreadPoolExecutor(max_workers=2) as ex:
    futs = {ex.submit(quick_pw_test, p): p for p in alive[:30]}
    try:
        for f in as_completed(futs, timeout=90):
            try:
                r = f.result()
                if r:
                    clean.append(r)
                    print(f"  CLEAN: {r}", flush=True)
            except: pass
    except: pass
print(f"Clean proxies: {len(clean)}", flush=True)

# Phase 3: Generate with clean proxies
print("\n=== PHASE 3: Generate ===", flush=True)
for i, px in enumerate(clean[:10]):
    print(f"\n--- ATTEMPT {i+1}/{min(len(clean),10)} proxy={px} ---", flush=True)
    result = try_generate(px)
    if result:
        print(f"\n{'='*50}", flush=True)
        print(f"SUCCESS! Video: {result}", flush=True)
        try:
            h = req.head(result, timeout=15, allow_redirects=True)
            print(f"HEAD: {h.status_code} type={h.headers.get('content-type','?')} size={h.headers.get('content-length','?')}", flush=True)
        except Exception as e: print(f"HEAD: {e}", flush=True)
        sys.exit(0)

print("\nFAILED all attempts", flush=True)
sys.exit(1)
