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

import time, uuid, threading, requests, json, re, random, queue
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

# --- Constants ---
URL = "https://veoaifree.com/grok-ai-video-generator/"
AD = ["clickiocdn", "google-analytics", "googletagmanager", "doubleclick",
      "facebook", "hotjar", "clarity", "adnxs", "taboola", "outbrain"]

def is_ad(u): return any(d in u.lower() for d in AD)

# ============================================================
#  PROXY SYSTEM (3-phase: HTTP → Playwright → Generate)
# ============================================================

def fetch_proxies():
    srcs = [
        "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=1000&country=all&ssl=yes&anonymity=elite",
        "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
        "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    ]
    pxs = set()
    for url in srcs:
        try:
            r = requests.get(url, timeout=5)
            for line in r.text.split('\n'):
                v = line.strip()
                if v and ':' in v and not v.startswith('#'):
                    parts = v.split(':')
                    if len(parts) == 2 and parts[1].isdigit():
                        pxs.add(v)
        except: pass
    return list(pxs)

def http_test(proxy):
    try:
        r = requests.get("https://veoaifree.com/",
                         proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                         timeout=2, headers={'User-Agent': 'Mozilla/5.0'})
        if r.status_code == 200: return proxy
    except: pass
    return None

def playwright_test(proxy):
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

def find_clean_proxy():
    """3-phase proxy finder. Returns a proxy that's alive and not rate-limited."""
    print("[proxy] Phase 1: HTTP filter...", flush=True)
    all_proxies = fetch_proxies()
    random.shuffle(all_proxies)
    alive = []
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(http_test, p): p for p in all_proxies[:300]}
        try:
            for f in as_completed(futs, timeout=20):
                try:
                    r = f.result()
                    if r: alive.append(r)
                except: pass
        except: pass
    print(f"[proxy] {len(alive)} alive", flush=True)

    if not alive: return None

    print("[proxy] Phase 2: Playwright filter...", flush=True)
    clean = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(playwright_test, p): p for p in alive[:20]}
        try:
            for f in as_completed(futs, timeout=60):
                try:
                    r = f.result()
                    if r:
                        clean.append(r)
                        print(f"[proxy] CLEAN: {r}", flush=True)
                        break  # got one, good enough
                except: pass
        except: pass
    return clean[0] if clean else None

# ============================================================
#  VIDEO GENERATION
# ============================================================

def generate_video(prompt, model="3.1", aspect="VIDEO_ASPECT_RATIO_PORTRAIT", proxy=None):
    """Generate video. Returns dict with videoUrl or error."""
    with sync_playwright() as p:
        kw = {'headless': True, 'args': [
            '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
            '--disable-blink-features=AutomationControlled']}
        if proxy: kw['proxy'] = {"server": f"http://{proxy}"}
        br = p.chromium.launch(**kw)
        ctx = br.new_context(viewport={'width': 1280, 'height': 720}, locale='en-US',
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')
        ctx.add_init_script("()=>{Object.defineProperty(navigator,'webdriver',{get:()=>false});}")
        pg = ctx.new_page()
        pg.route("**/*", lambda r: r.abort() if is_ad(r.request.url) else r.continue_())

        try: pg.goto(URL, timeout=12000, wait_until='domcontentloaded')
        except Exception as e:
            br.close(); return {"error": f"Navigation failed: {e}"}

        body = pg.evaluate("()=>document.body?.innerText||''")
        if 'rate limit' in body.lower() or 'limit reached' in body.lower():
            br.close(); return {"error": "Rate limited on this proxy"}

        # popups + cookies
        pg.evaluate("()=>{document.querySelectorAll('#suOverlay,.su-overlay,.su-popup,#swContainer,[role=dialog],.modal-overlay,.overlay,.popup-overlay,.modal-backdrop').forEach(e=>e.remove());document.body.style.overflow='auto';'videoCounter=0;cookiClicked=1;ytPopup=1;ytHide=1;popupLockout=active'.split(';').forEach(c=>{document.cookie=c.trim()+';path=/;max-age=86400'});}")

        # fill form
        pg.evaluate("""([m,a,t])=>{
            for(const p of document.querySelectorAll('svg path')){if((p.getAttribute('d')||'').includes('M408')){const c=p.closest('svg')||p.closest('a')||p.closest('button')||p.parentElement;if(c)c.dispatchEvent(new MouseEvent('click',{bubbles:true}));break;}}
            setTimeout(()=>{document.querySelector('#modal').value=m;document.querySelector('#modal').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#aspect-ration').value=a;document.querySelector('#aspect-ration').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('#fn__include_textarea').value=t;document.querySelector('#fn__include_textarea').dispatchEvent(new Event('input',{bubbles:true}));},300);
        }""", [model, aspect, prompt])
        time.sleep(1)

        init = set(pg.evaluate("()=>[...new Set([...document.querySelectorAll('video,source,a,img')].map(e=>e.src||e.href||e.currentSrc||'').filter(Boolean))]"))

        vid = [None]
        def on_r(resp):
            u = resp.url
            if is_ad(u) or 'admin-ajax.php' not in u.lower() or resp.request.method != 'POST': return
            try:
                b = resp.text().strip()
                if not b: return
                if 'rate limit' in b.lower() or 'limit reached' in b.lower(): return
                if b.startswith('http') and any(x in b.lower() for x in ['.mp4', '.webm']):
                    vid[0] = b.replace('videos/', 'video/')
            except: pass
        pg.on('response', on_r)

        pg.locator('#generate_it').click(force=True, timeout=5000)

        t0 = time.time(); last_p = -1; p100 = None
        while time.time() - t0 < 300:
            e = int(time.time() - t0)
            if vid[0]: break
            if e % 15 == 0:
                b2 = pg.evaluate("()=>document.body?.innerText||''")
                if 'rate limit' in b2.lower() or 'limit reached' in b2.lower():
                    break
            try:
                pi = pg.evaluate("()=>{const el=document.querySelector('.show-percentage');if(el){const m=(el.textContent||'').match(/(\\d{1,3})\\s*%/);if(m)return parseInt(m[1]);}return null;}")
                if pi is not None and pi != last_p:
                    last_p = pi
                    if pi >= 100 and not p100: p100 = time.time()
            except: pass
            if p100 and e % 3 == 0:
                try:
                    us = pg.evaluate("()=>[...new Set([...document.querySelectorAll('ul.fn__generation_list video')].map(v=>v.src||v.currentSrc).filter(Boolean).concat([...document.querySelectorAll('a.only-video-download,a.downloader-video-btn')].map(a=>a.href).filter(Boolean)))]")
                    nu = [u for u in us if u not in init and ('.mp4' in u.lower() or '.webm' in u.lower())]
                    if nu: vid[0] = nu[0]; break
                except: pass
            if p100 and e % 5 == 0:
                try:
                    d = pg.evaluate("()=>({v:[...document.querySelectorAll('video')].map(v=>v.src||v.currentSrc).filter(Boolean),l:[...document.querySelectorAll('a[href]')].map(a=>a.href).filter(h=>h.includes('.mp4')||h.includes('.webm')||h.includes('blob:')||h.includes('upload'))})")
                    for v in d.get('v', []) + d.get('l', []):
                        if v and ('.mp4' in v or '.webm' in v): vid[0] = v; break
                    if vid[0]: break
                except: pass
            time.sleep(2 if p100 else 3)

        pg.remove_listener('response', on_r)
        br.close()

        if vid[0]:
            # Validate
            try:
                h = requests.head(vid[0], timeout=15, allow_redirects=True)
                return {"videoUrl": vid[0], "status": h.status_code,
                        "contentType": h.headers.get('content-type', '?'),
                        "contentLength": h.headers.get('content-length', '?')}
            except:
                return {"videoUrl": vid[0]}
        return {"error": "Video generation timed out or URL not found"}

def run_job(job_id, prompt, model, aspect):
    """Background job: find proxy → generate video."""
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['status'] = 'processing'
            jobs[job_id]['progress'] = 'Finding working proxy...'
    save_jobs()

    proxy = find_clean_proxy()
    if not proxy:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = 'No working proxy found'
                jobs[job_id]['progress'] = 'Failed'
        save_jobs()
        return

    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['progress'] = f'Generating with proxy {proxy}...'
    save_jobs()

    result = generate_video(prompt, model, aspect, proxy)

    with jobs_lock:
        if job_id in jobs:
            if 'error' in result:
                # Retry with different proxy
                print(f"[Job] Failed with {proxy}, retrying...", flush=True)
                proxy2 = find_clean_proxy()
                if proxy2 and proxy2 != proxy:
                    with jobs_lock:
                        if job_id in jobs:
                            jobs[job_id]['progress'] = f'Retry with proxy {proxy2}...'
                    save_jobs()
                    result = generate_video(prompt, model, aspect, proxy2)

            if 'error' in result:
                jobs[job_id]['status'] = 'failed'
                jobs[job_id]['error'] = result['error']
                jobs[job_id]['progress'] = 'Failed'
            else:
                jobs[job_id]['status'] = 'completed'
                jobs[job_id]['progress'] = 'Completed'
                jobs[job_id]['videoUrl'] = result['videoUrl']
                if 'contentType' in result:
                    jobs[job_id]['contentType'] = result['contentType']
                if 'contentLength' in result:
                    jobs[job_id]['contentLength'] = result['contentLength']
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
        'POST /api/generate': 'Start video generation',
        'GET /api/job/<id>': 'Check job status',
        'GET /api/jobs': 'List all jobs',
        'GET /api/status': 'Server status',
        'GET /api/proxy/find': 'Find a clean proxy',
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

    model = data.get('model', '3.1')
    aspect = data.get('aspect', 'portrait')
    aspect_val = "VIDEO_ASPECT_RATIO_LANDSCAPE" if aspect.lower() == 'landscape' else "VIDEO_ASPECT_RATIO_PORTRAIT"

    job_id = f"vid-{int(time.time() * 1000) % 1000000}"
    with jobs_lock:
        jobs[job_id] = {
            'id': job_id, 'status': 'queued', 'progress': 'Queued',
            'prompt': prompt, 'model': model, 'aspect': aspect_val,
            'videoUrl': None, 'error': None, 'createdAt': time.time() * 1000
        }
    save_jobs(throttle=False)

    threading.Thread(target=run_job, args=(job_id, prompt, model, aspect_val), daemon=True).start()
    return jsonify({'jobId': job_id, 'status': 'queued'})

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

    model = data.get('model', '3.1')
    aspect = data.get('aspect', 'portrait')
    aspect_val = "VIDEO_ASPECT_RATIO_LANDSCAPE" if aspect.lower() == 'landscape' else "VIDEO_ASPECT_RATIO_PORTRAIT"

    proxy = find_clean_proxy()
    if not proxy:
        return jsonify({'error': 'no working proxy'}), 503

    result = generate_video(prompt, model, aspect_val, proxy)
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
                 "aspect": {"type": "string"}}, "required": ["prompt"]}},
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
            jid = f"mcp-{int(time.time()*1000)%1000000}"
            with jobs_lock:
                jobs[jid] = {'id': jid, 'status': 'queued', 'progress': 'Queued via MCP',
                             'prompt': pr, 'model': md, 'aspect': asp,
                             'videoUrl': None, 'error': None, 'createdAt': time.time()*1000}
            save_jobs(throttle=False)
            threading.Thread(target=run_job, args=(jid, pr, md, asp), daemon=True).start()
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

if __name__ == '__main__':
    print(f"Server on http://0.0.0.0:{PORT}", flush=True)
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)
