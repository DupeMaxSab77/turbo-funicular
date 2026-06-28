import os

try:
    _dotenv_path = os.path.join(os.getcwd(), '.env')
    if os.path.exists(_dotenv_path):
        with open(_dotenv_path, 'r', encoding='utf-8') as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith('#'): continue
                if '=' in _line:
                    _key, _val = _line.split('=', 1)
                    _key, _val = _key.strip(), _val.strip()
                    if (_val.startswith('"') and _val.endswith('"')) or (_val.startswith("'") and _val.endswith("'")):
                        _val = _val[1:-1]
                    if _key and _key not in os.environ:
                        os.environ[_key] = _val
except: pass

import time, uuid, threading, requests, json, re, random, queue, collections
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from playwright.sync_api import sync_playwright

app = Flask(__name__)
CORS(app)

PORT = int(os.environ.get('PORT', 3000))

# --- Paths ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(SCRIPT_DIR, 'jobs_storage.json')
UPLOADS_DIR = os.path.join(SCRIPT_DIR, 'uploads')
os.makedirs(UPLOADS_DIR, exist_ok=True)

# --- State ---
jobs = {}
jobs_lock = threading.Lock()
_last_save = [0.0]

# --- Proxy cache (auto-refreshed every 30s) ---
proxy_pool = collections.deque(maxlen=50)
proxy_pool_lock = threading.Lock()
tested_proxies = set()  # avoid retesting dead proxies

# --- Generation lock (only 1 Playwright browser at a time) ---
gen_lock = threading.Lock()

# --- Constants ---
URLS = {
    "grok": "https://veoaifree.com/grok-ai-video-generator/",
    "seedance": "https://veoaifree.com/seedance-2-0-video-generator-free/",
}
MODELS = {
    "grok": {"3.1": "Grok 4", "2.0": "Grok 4.5"},
    "seedance": {"2.0": "Seedance 2.0", "1.5": "Seedance"},
}
AD = ["clickiocdn", "google-analytics", "googletagmanager", "doubleclick",
      "facebook", "hotjar", "clarity", "adnxs", "taboola", "outbrain"]

def is_ad(u): return any(d in u.lower() for d in AD)

# ============================================================
#  PROXY SYSTEM (3-phase: HTTP → Playwright → Generate)
# ============================================================

def fetch_proxies():
    srcs = [
        # HTTP proxies
        ("http", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=2000&country=all&ssl=yes&anonymity=elite"),
        ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
        ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
        # SOCKS5 proxies (often faster/more stable)
        ("socks5", "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=2000&country=all&anonymity=elite"),
        ("socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
        ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
    ]
    pxs = []  # list of (type, proxy) tuples
    for ptype, url in srcs:
        try:
            r = requests.get(url, timeout=5)
            for line in r.text.split('\n'):
                v = line.strip()
                if v and ':' in v and not v.startswith('#'):
                    parts = v.split(':')
                    if len(parts) == 2 and parts[1].isdigit():
                        pxs.append((ptype, v))
        except: pass
    random.shuffle(pxs)
    return pxs

def http_test(proxy_tuple):
    """Quick HTTP reachability test. Returns (type, proxy) if alive, else None."""
    ptype, proxy = proxy_tuple
    scheme = "socks5" if ptype == "socks5" else "http"
    try:
        r = requests.get("https://veoaifree.com/",
                         proxies={"http": f"{scheme}://{proxy}", "https": f"{scheme}://{proxy}"},
                         timeout=3, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'})
        if r.status_code == 200: return proxy_tuple
    except: pass
    return None

def _fast_http_filter(proxies, limit=500, workers=60, timeout_s=15):
    """Fast parallel HTTP test. Returns list of alive proxies."""
    alive = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(http_test, p): p for p in proxies[:limit]}
        done, _ = concurrent.futures.wait(futs, timeout=timeout_s)
        for f in done:
            try:
                r = f.result()
                if r: alive.append(r)
            except: pass
    return alive

def _batch_playwright_test(proxies, max_clean=3):
    """Test proxies using ONE shared Playwright browser. Returns clean proxies."""
    clean = []
    if not proxies: return clean
    test_url = URLS["grok"]
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(headless=True, args=[
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            for px in proxies:
                # px can be tuple (type, addr) or string (legacy)
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
                    pg.goto(test_url, timeout=7000, wait_until='domcontentloaded')
                    body = pg.evaluate("()=>document.body?.innerText||''")
                    if 'rate limit' not in body.lower() and 'limit reached' not in body.lower() and len(body) > 500:
                        clean.append(px)
                        print(f"[proxy] CLEAN: {px}", flush=True)
                        if len(clean) >= max_clean: break
                except: pass
                try: ctx.close()
                except: pass
            br.close()
    except Exception as e:
        print(f"[proxy] Playwright batch error: {e}", flush=True)
    return clean

def find_clean_proxy():
    """Get a clean proxy. Validates with Playwright before returning."""
    candidates = []
    with proxy_pool_lock:
        while proxy_pool and len(candidates) < 10:
            candidates.append(proxy_pool.popleft())

    if candidates:
        clean = _batch_playwright_test(candidates, max_clean=1)
        if clean:
            # Put back untested ones
            with proxy_pool_lock:
                for px in candidates:
                    if px not in clean and px not in proxy_pool:
                        proxy_pool.append(px)
            return clean[0]
        # None passed Playwright, return None
        return None

    # Pool empty — do a full scan
    print("[proxy] Pool empty, scanning...", flush=True)
    return _scan_for_proxy()

def _scan_for_proxy():
    """Fast scan: HTTP filter → shared Playwright test. Returns one clean proxy or None."""
    print("[proxy] HTTP filtering...", flush=True)
    all_proxies = fetch_proxies()
    random.shuffle(all_proxies)
    alive = _fast_http_filter(all_proxies, limit=500, workers=60, timeout_s=15)
    print(f"[proxy] {len(alive)} alive", flush=True)
    if not alive: return None

    print("[proxy] Playwright testing...", flush=True)
    clean = _batch_playwright_test(alive[:20])
    return clean[0] if clean else None

def proxy_refresh_loop():
    """Background thread: HTTP-tests proxies every 30s, fills pool.
    Playwright validation happens on-demand in find_clean_proxy."""
    print("[proxy-refresh] Starting auto-refresh loop (every 30s)", flush=True)
    while True:
        try:
            all_proxies = fetch_proxies()
            random.shuffle(all_proxies)

            new_proxies = [p for p in all_proxies if p not in tested_proxies]
            alive = _fast_http_filter(new_proxies, limit=300, workers=40, timeout_s=12)

            for p in new_proxies[:300]:
                tested_proxies.add(p)

            with proxy_pool_lock:
                for px in alive:
                    if px not in proxy_pool:
                        proxy_pool.append(px)

            print(f"[proxy-refresh] Pool: {len(proxy_pool)} proxies ({len(alive)} alive this cycle)", flush=True)

            if len(tested_proxies) > 3000:
                tested_proxies.clear()

        except Exception as e:
            print(f"[proxy-refresh] Error: {e}", flush=True)

        time.sleep(30)

# ============================================================
#  VIDEO GENERATION
# ============================================================

def generate_video(prompt, model="3.1", aspect="VIDEO_ASPECT_RATIO_PORTRAIT", proxy=None, generator="grok"):
    """Generate video. proxy can be a string (legacy) or tuple (type, addr)."""
    page_url = URLS.get(generator, URLS["grok"])
    br = None
    try:
        with sync_playwright() as p:
            kw = {'headless': True, 'args': [
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                '--disable-blink-features=AutomationControlled']}
            if proxy:
                if isinstance(proxy, tuple):
                    ptype, paddr = proxy
                    scheme = "socks5" if ptype == "socks5" else "http"
                    kw['proxy'] = {"server": f"{scheme}://{paddr}"}
                else:
                    kw['proxy'] = {"server": f"http://{proxy}"}
            br = p.chromium.launch(**kw)
            ctx = br.new_context(viewport={'width': 1280, 'height': 720}, locale='en-US',
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
            ctx.add_init_script("()=>{Object.defineProperty(navigator,'webdriver',{get:()=>false});}")
            pg = ctx.new_page()
            pg.route("**/*", lambda r: r.abort() if is_ad(r.request.url) else r.continue_())

            nav_timeout = 20000 if proxy else 12000
            try: pg.goto(page_url, timeout=nav_timeout, wait_until='domcontentloaded')
            except Exception as e:
                return {"error": f"Navigation failed: {e}"}

            body = pg.evaluate("()=>document.body?.innerText||''")
            if 'rate limit' in body.lower() or 'limit reached' in body.lower():
                return {"error": "Rate limited on this proxy"}

            # popups + cookies
            pg.evaluate("()=>{document.querySelectorAll('#suOverlay,.su-overlay,.su-popup,#swContainer,[role=dialog],.modal-overlay,.overlay,.popup-overlay,.modal-backdrop').forEach(e=>e.remove());document.body.style.overflow='auto';'videoCounter=0;cookiClicked=1;ytPopup=1;ytHide=1;popupLockout=active'.split(';').forEach(c=>{document.cookie=c.trim()+';path=/;max-age=86400'});}")
            time.sleep(1)

            # fill form
            pg.evaluate("""([m,a,t])=>{
                for(const p of document.querySelectorAll('svg path')){if((p.getAttribute('d')||'').includes('M408')){const c=p.closest('svg')||p.closest('a')||p.closest('button')||p.parentElement;if(c)c.dispatchEvent(new MouseEvent('click',{bubbles:true}));break;}}
                setTimeout(()=>{document.querySelector('#modal').value=m;document.querySelector('#modal').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#aspect-ration').value=a;document.querySelector('#aspect-ration').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#fn__include_textarea').value=t;document.querySelector('#fn__include_textarea').dispatchEvent(new Event('input',{bubbles:true}));},500);
            }""", [model, aspect, prompt])
            time.sleep(1.5)

            init = set(pg.evaluate("()=>[...new Set([...document.querySelectorAll('video,source,a,img')].map(e=>e.src||e.href||e.currentSrc||'').filter(Boolean))]"))

            vid = [None]
            rate_limited = [False]
            def on_r(resp):
                u = resp.url
                if is_ad(u): return
                if 'admin-ajax.php' in u.lower() and resp.request.method == 'POST':
                    try:
                        b = resp.text().strip()
                        if not b: return
                        if 'rate limit' in b.lower() or 'limit reached' in b.lower():
                            rate_limited[0] = True
                            return
                        if b.startswith('http') and any(x in b.lower() for x in ['.mp4', '.webm']):
                            vid[0] = b.replace('videos/', 'video/')
                            print(f"[gen] AJAX URL: {vid[0]}", flush=True)
                        elif 'limit' not in b.lower() and len(b) > 10:
                            print(f"[gen] AJAX resp ({len(b)} chars): {b[:200]}", flush=True)
                    except: pass
                # Also catch video URLs in any response
                if any(x in u.lower() for x in ['.mp4', '.webm']) and 'admin-ajax' not in u.lower():
                    if u not in init:
                        vid[0] = u
            pg.on('response', on_r)

            print(f"[gen] Clicking generate...", flush=True)
            try: pg.locator('#generate_it').click(force=True, timeout=5000)
            except Exception as e:
                return {"error": f"Click failed: {e}"}

            t0 = time.time(); last_p = -1; p100 = None; last_change = time.time()
            while time.time() - t0 < 150:
                e = int(time.time() - t0)
                if vid[0]: break
                if rate_limited[0]: break
                # Early abort: no progress after 60s (generous for slow proxies)
                if e > 60 and last_p == -1:
                    # Double check - maybe page shows rate limit
                    try:
                        b2 = pg.evaluate("()=>document.body?.innerText||''")
                        if 'rate limit' in b2.lower() or 'limit reached' in b2.lower():
                            rate_limited[0] = True
                            break
                    except: pass
                    break
                # Stuck at same % for 45s
                if p100 is None and last_p > 0 and (time.time() - last_change) > 45:
                    break
                # Check progress
                try:
                    pi = pg.evaluate("()=>{const el=document.querySelector('.show-percentage');if(el){const m=(el.textContent||'').match(/(\\d{1,3})\\s*%/);if(m)return parseInt(m[1]);}return null;}")
                    if pi is not None and pi != last_p:
                        last_p = pi
                        last_change = time.time()
                        print(f"[gen] Progress: {pi}%", flush=True)
                        if pi >= 100 and not p100: p100 = time.time()
                except: pass
                # After 100%, check for video more aggressively
                if p100 and e % 2 == 0:
                    try:
                        us = pg.evaluate("()=>[...new Set([...document.querySelectorAll('ul.fn__generation_list video')].map(v=>v.src||v.currentSrc).filter(Boolean).concat([...document.querySelectorAll('a.only-video-download,a.downloader-video-btn')].map(a=>a.href).filter(Boolean)).concat([...document.querySelectorAll('video source')].map(s=>s.src).filter(Boolean)))]")
                        nu = [u for u in us if u not in init and ('.mp4' in u.lower() or '.webm' in u.lower() or 'blob:' in u.lower())]
                        if nu:
                            vid[0] = nu[0]
                            print(f"[gen] Found video in DOM: {vid[0][:80]}", flush=True)
                            break
                    except: pass
                # After 100%, also scan all links
                if p100 and e % 5 == 0:
                    try:
                        d = pg.evaluate("()=>{const urls=[];document.querySelectorAll('video').forEach(v=>{if(v.src)urls.push(v.src);if(v.currentSrc)urls.push(v.currentSrc)});document.querySelectorAll('a[href]').forEach(a=>{const h=a.href;if(h.includes('.mp4')||h.includes('.webm')||h.includes('blob:')||h.includes('upload'))urls.push(h)});return[...new Set(urls)]}")
                        for v in d:
                            if v and ('.mp4' in v or '.webm' in v) and v not in init:
                                vid[0] = v
                                print(f"[gen] Found video in links: {vid[0][:80]}", flush=True)
                                break
                        if vid[0]: break
                    except: pass
                time.sleep(2 if p100 else 3)

            pg.remove_listener('response', on_r)

            if rate_limited[0] and not vid[0]:
                return {"error": "Rate limited on this proxy"}

            if vid[0]:
                # Validate video URL
                try:
                    h = requests.head(vid[0], timeout=15, allow_redirects=True)
                    ct = h.headers.get('content-type', '?')
                    cl = h.headers.get('content-length', '?')
                    print(f"[gen] Validated: {h.status_code} {ct} {cl}", flush=True)
                    return {"videoUrl": vid[0], "status": h.status_code,
                            "contentType": ct, "contentLength": cl}
                except:
                    return {"videoUrl": vid[0]}
            return {"error": "Video generation timed out or URL not found"}
    except Exception as e:
        return {"error": f"Fatal: {e}"}
    finally:
        if br:
            try: br.close()
            except: pass

def run_job(job_id, prompt, model, aspect, generator="grok"):
    """Background job: generate video. Tries direct first, then proxies as fallback."""
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['status'] = 'processing'
            jobs[job_id]['progress'] = 'Waiting for turn...'
    save_jobs()

    # Only 1 Playwright browser at a time
    print(f"[Job] {job_id} waiting for gen_lock...", flush=True)
    with gen_lock:
        print(f"[Job] {job_id} acquired gen_lock", flush=True)
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]['progress'] = 'Generating...'
        save_jobs()

        # Try direct first
        result = generate_video(prompt, model, aspect, None, generator)
        if 'error' not in result:
            _finish_job(job_id, result)
            return

        print(f"[Job] {job_id} direct failed: {result.get('error','?')}", flush=True)

        # Fallback: try proxies from pool
        proxies_to_try = []
        with proxy_pool_lock:
            while proxy_pool and len(proxies_to_try) < 3:
                proxies_to_try.append(proxy_pool.popleft())

        for i, proxy in enumerate(proxies_to_try):
            label = f"{proxy[0]}://{proxy[1]}" if isinstance(proxy, tuple) else str(proxy)
            with jobs_lock:
                if job_id in jobs:
                    jobs[job_id]['progress'] = f'Proxy retry {i+1}/{len(proxies_to_try)}: {label}'
            save_jobs()

            print(f"[Job] {job_id} proxy try {i+1}: {label}", flush=True)
            result = generate_video(prompt, model, aspect, proxy, generator)

            if 'error' not in result:
                _finish_job(job_id, result)
                return

            print(f"[Job] {job_id} failed: {result.get('error','?')}", flush=True)

    # All failed
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['status'] = 'failed'
            jobs[job_id]['error'] = result.get('error', 'All attempts failed') if result else 'All attempts failed'
            jobs[job_id]['progress'] = 'Failed'
    save_jobs()

def _finish_job(job_id, result):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['status'] = 'completed'
            jobs[job_id]['progress'] = 'Completed'
            jobs[job_id]['videoUrl'] = result['videoUrl']
            if 'contentType' in result: jobs[job_id]['contentType'] = result['contentType']
            if 'contentLength' in result: jobs[job_id]['contentLength'] = result['contentLength']
    save_jobs()

# ============================================================
#  JOB STORAGE
# ============================================================

def save_jobs(throttle=True):
    try:
        now = time.time()
        if throttle and (now - _last_save[0]) < 3: return
        _last_save[0] = now
        with jobs_lock:
            data = dict(jobs)
        with open(JOBS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except: pass

def load_jobs():
    global jobs
    try:
        if os.path.exists(JOBS_FILE):
            with open(JOBS_FILE) as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    with jobs_lock: jobs.update(loaded)
    except: pass

# ============================================================
#  API ROUTES
# ============================================================

@app.route('/')
def root():
    return jsonify({'message': 'Video Generation API', 'endpoints': {
        'POST /api/generate': 'Start video generation (body: prompt, generator: grok|seedance, model, aspect)',
        'GET /api/job/<id>': 'Check job status',
        'GET /api/jobs': 'List all jobs',
        'GET /api/status': 'Server status',
        'GET /api/proxy/find': 'Find a clean proxy',
    }, 'generators': {
        'grok': {'url': 'grok-ai-video-generator', 'models': MODELS['grok']},
        'seedance': {'url': 'seedance-2-0-video-generator-free', 'models': MODELS['seedance']},
    }})

@app.route('/api/status')
def status():
    with jobs_lock:
        counts = {}
        for j in jobs.values():
            s = j.get('status', 'unknown')
            counts[s] = counts.get(s, 0) + 1
    return jsonify({'status': 'ok', 'jobs': counts, 'total': len(jobs)})

@app.route('/api/generate', methods=['POST'])
def api_generate():
    data = request.json or {}
    prompt = data.get('prompt', '')
    if not prompt: return jsonify({'error': 'prompt required'}), 400
    if len(prompt) < 15: return jsonify({'error': 'prompt must be 15+ chars'}), 400

    generator = data.get('generator', 'grok')
    if generator not in URLS: return jsonify({'error': f'Invalid generator: {generator}. Use grok or seedance'}), 400

    model = data.get('model', '3.1')
    # Validate model for generator
    valid_models = list(MODELS.get(generator, {}).keys())
    if valid_models and model not in valid_models:
        return jsonify({'error': f'Invalid model {model} for {generator}. Use: {valid_models}'}), 400

    aspect = data.get('aspect', 'portrait')
    aspect_val = "VIDEO_ASPECT_RATIO_LANDSCAPE" if aspect.lower() == 'landscape' else "VIDEO_ASPECT_RATIO_PORTRAIT"

    job_id = f"vid-{int(time.time() * 1000) % 1000000}"
    with jobs_lock:
        jobs[job_id] = {
            'id': job_id, 'status': 'queued', 'progress': 'Queued',
            'prompt': prompt, 'model': model, 'aspect': aspect_val,
            'generator': generator,
            'videoUrl': None, 'error': None, 'createdAt': time.time() * 1000
        }
    save_jobs(throttle=False)

    threading.Thread(target=run_job, args=(job_id, prompt, model, aspect_val, generator), daemon=True).start()
    return jsonify({'jobId': job_id, 'status': 'queued', 'generator': generator})

@app.route('/api/job/<job_id>')
def api_job(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job: return jsonify({'error': 'not found'}), 404
    return jsonify(job)

@app.route('/api/jobs')
def api_jobs():
    with jobs_lock:
        all_jobs = list(jobs.values())
    all_jobs.sort(key=lambda j: j.get('createdAt', 0), reverse=True)
    return jsonify({'total': len(all_jobs), 'jobs': all_jobs[:50]})

@app.route('/api/proxy/find')
def api_proxy_find():
    """Find a clean proxy (for external use)."""
    proxy = find_clean_proxy()
    if proxy:
        return jsonify({'proxy': proxy, 'status': 'clean'})
    return jsonify({'error': 'no clean proxy found'}), 404

@app.route('/api/quick-generate', methods=['POST'])
def api_quick_generate():
    """Synchronous generation - waits for result (up to 5 min)."""
    data = request.json or {}
    prompt = data.get('prompt', '')
    if not prompt: return jsonify({'error': 'prompt required'}), 400
    if len(prompt) < 15: return jsonify({'error': 'prompt must be 15+ chars'}), 400

    generator = data.get('generator', 'grok')
    if generator not in URLS: return jsonify({'error': f'Invalid generator: {generator}'}), 400

    model = data.get('model', '3.1')
    aspect = data.get('aspect', 'portrait')
    aspect_val = "VIDEO_ASPECT_RATIO_LANDSCAPE" if aspect.lower() == 'landscape' else "VIDEO_ASPECT_RATIO_PORTRAIT"

    proxy = find_clean_proxy()
    if not proxy:
        return jsonify({'error': 'no working proxy'}), 503

    result = generate_video(prompt, model, aspect_val, proxy, generator)
    if 'error' in result:
        return jsonify(result), 500
    return jsonify(result)

# ============================================================
#  MCP SERVER
# ============================================================

mcp_sessions = {}
mcp_lock = threading.Lock()

def handle_mcp(data):
    if not isinstance(data, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    mid = data.get("id"); method = data.get("method"); params = data.get("params", {})
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
            "serverInfo": {"name": "VideoGen-MCP", "version": "2.0.0"}}}
    if method == "notifications/initialized": return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "generate_video", "description": "Generate video from prompt",
             "inputSchema": {"type": "object", "properties": {
                 "prompt": {"type": "string"}, "model": {"type": "string"},
                 "aspect": {"type": "string"},
                 "generator": {"type": "string", "enum": ["grok", "seedance"],
                               "description": "grok (Grok 4/4.5) or seedance (Seedance 2.0/1.5)"}},
                 "required": ["prompt"]}},
            {"name": "get_job", "description": "Get job status",
             "inputSchema": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}},
            {"name": "list_jobs", "description": "List all jobs",
             "inputSchema": {"type": "object", "properties": {}}}
        ]}}
    if method == "tools/call":
        tn = params.get("name"); args = params.get("arguments", {})
        if tn == "generate_video":
            pr = args.get("prompt")
            if not pr: return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "Missing prompt"}}
            md = args.get("model", "3.1")
            asp = "VIDEO_ASPECT_RATIO_LANDSCAPE" if "landscape" in args.get("aspect", "").lower() else "VIDEO_ASPECT_RATIO_PORTRAIT"
            gen = args.get("generator", "grok")
            if gen not in URLS: gen = "grok"
            jid = f"mcp-{int(time.time()*1000)%1000000}"
            with jobs_lock:
                jobs[jid] = {'id': jid, 'status': 'queued', 'progress': 'Queued via MCP',
                             'prompt': pr, 'model': md, 'aspect': asp, 'generator': gen,
                             'videoUrl': None, 'error': None, 'createdAt': time.time()*1000}
            save_jobs(throttle=False)
            threading.Thread(target=run_job, args=(jid, pr, md, asp, gen), daemon=True).start()
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"Job: {jid}"}]}}
        if tn == "get_job":
            tid = args.get("job_id")
            with jobs_lock: j = jobs.get(tid)
            if not j: return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "Not found"}], "isError": True}}
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(j)}]}}
        if tn == "list_jobs":
            with jobs_lock: aj = list(jobs.values())
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(aj[:20])}]}}
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown tool: {tn}"}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}}

@app.route('/api/mcp/sse')
def mcp_sse():
    sid = f"sess-{uuid.uuid4().hex[:12]}"
    q = queue.Queue()
    with mcp_lock: mcp_sessions[sid] = q
    def gen():
        base = request.url_root.rstrip('/')
        yield f"event: endpoint\ndata: {base}/api/mcp/messages?session_id={sid}\n\n"
        try:
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except queue.Empty: yield ": ping\n\n"
        except GeneratorExit: pass
        finally:
            with mcp_lock: mcp_sessions.pop(sid, None)
    return Response(gen(), content_type='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'})

@app.route('/api/mcp/messages', methods=['POST'])
def mcp_messages():
    sid = request.args.get('session_id')
    data = request.json or {}
    resp = handle_mcp(data)
    if not resp: return '', 202
    if sid:
        with mcp_lock: q = mcp_sessions.get(sid)
        if q: q.put(resp); return '', 200
    return jsonify(resp)

# ============================================================
#  STARTUP
# ============================================================

load_jobs()

# Start proxy auto-refresh
threading.Thread(target=proxy_refresh_loop, daemon=True).start()

if __name__ == '__main__':
    print(f"Server on http://0.0.0.0:{PORT}", flush=True)
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
