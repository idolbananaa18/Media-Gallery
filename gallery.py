#!/usr/bin/env python3
"""Local media gallery. Run: python3 gallery.py [--media DIR] [--port N]
Serves on 127.0.0.1 only. Stdlib only; UI is embedded below."""
import argparse, hashlib, json, os, sys, re, struct, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote

HERE = Path(__file__).resolve().parent
KIND = {".gif": "gif"}
KIND.update({"." + e: "image" for e in "jpg jpeg png webp avif bmp".split()})
KIND.update({"." + e: "video" for e in "mp4 webm mov m4v".split()})
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".bmp": "image/bmp", ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
        ".avif": "image/avif", ".webp": "image/webp"}
STOP = set("img image vid video gif the and with copy final new untitled download file dsc pxl screenshot mov mp4 jpg jpeg png webm webp avif bmp".split())
LOCK = threading.Lock()
ROOT = HERE / "media"
DATA_FILE = HERE / "gallery_data.json"
INDEX, CACHE, VID = {}, {}, {}
CHUNK = 2_000_000  # max bytes per range response: frees sockets so many videos can stream at once
CODECS = {b"avc1": "H.264", b"hvc1": "H.265/HEVC", b"hev1": "H.265/HEVC", b"av01": "AV1", b"vp09": "VP9",
          b"vp08": "VP8", b"mp4v": "MPEG-4 Part 2", b"apch": "ProRes", b"apcn": "ProRes"}


def safe(rel):
    """Resolved path of a servable media file inside ROOT, else None."""
    try:
        p = (ROOT / rel).resolve()
        parts = p.relative_to(ROOT).parts
    except (ValueError, OSError):
        return None
    if not p.is_file() or p.suffix.lower() not in KIND or any(s.startswith(".") for s in parts): return None
    return p


def codec(p):
    """Best-effort video codec name from the mp4/mov header or trailer."""
    if p.suffix.lower() == ".webm": return "WebM (VP8/VP9/AV1)"
    try:
        size, n = p.stat().st_size, 4_000_000
        with open(p, "rb") as f:
            blob = f.read(n)
            if size > n: f.seek(max(n, size - n)); blob += f.read()
    except OSError:
        return None
    hits = [(blob.find(k), v) for k, v in CODECS.items() if k in blob]
    return min(hits)[1] if hits else None


def dims(p):
    """(w, h) for png/gif/jpeg via header parsing, else None."""
    try:
        with open(p, "rb") as f:
            h = f.read(32)
            if h[:8] == b"\x89PNG\r\n\x1a\n": return struct.unpack(">II", h[16:24])
            if h[:6] in (b"GIF87a", b"GIF89a"): return struct.unpack("<HH", h[6:10])
            if h[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    if not b: return None
                    if b != b"\xff": continue
                    m = f.read(1)
                    while m == b"\xff": m = f.read(1)
                    if not m: return None
                    m = m[0]
                    if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                        f.read(3); hh, w = struct.unpack(">HH", f.read(4)); return w, hh
                    if 0xD0 <= m <= 0xD9 or m in (0x00, 0x01): continue
                    f.seek(struct.unpack(">H", f.read(2))[0] - 2, 1)
    except Exception:
        pass
    return None


def norm(t):
    return re.sub(r"[^a-z0-9 \-]", "", str(t).lower()).strip()[:40]


def load_data():
    try:
        d = json.loads(DATA_FILE.read_text())
        return {k: d.get(k, {}) for k in ("added", "removed", "fav")}
    except Exception:
        return {"added": {}, "removed": {}, "fav": {}}


def save_data(d):
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, DATA_FILE)


def auto_tags(rel, kind, wh, mtime):
    tags = [kind]
    if wh:
        w, h = wh
        tags.append("square" if abs(w - h) <= 0.05 * max(w, h) else "landscape" if w > h else "portrait")
    tags.append(time.strftime("%Y-%m", time.localtime(mtime)))
    for tok in re.split(r"[^a-z]+", rel.rsplit(".", 1)[0].lower()):
        if len(tok) >= 3 and tok not in STOP and tok not in tags: tags.append(tok)
    return tags


def effective(it, d):
    rem = set(d["removed"].get(it["id"], []))
    t = [x for x in it["auto"] if x not in rem]
    return t + [x for x in d["added"].get(it["id"], []) if x not in t]


def scan():
    d, items, vids = load_data(), [], {}
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = sorted(x for x in dn if not x.startswith("."))
        for n in sorted(fn):
            ext = os.path.splitext(n)[1].lower()
            if n.startswith(".") or ext not in KIND: continue
            p = Path(dp) / n
            rel = p.relative_to(ROOT).as_posix()
            try: st = p.stat()
            except OSError: continue
            key = (rel, st.st_mtime_ns, st.st_size)
            if key not in CACHE:
                k = KIND[ext]
                CACHE[key] = (k, dims(p) if k != "video" else None)
            k, wh = CACHE[key]
            url = "/f/" + quote(rel)
            if k == "video":  # extension-less URL + neutral type so download managers don't treat it as a video
                h = hashlib.sha1(rel.encode()).hexdigest()[:16]; vids[h] = rel; url = "/v/" + h
            it = {"id": rel, "name": os.path.basename(rel), "kind": k, "url": url, "w": wh[0] if wh else 0,
                  "h": wh[1] if wh else 0, "mtime": int(st.st_mtime), "size": st.st_size,
                  "auto": auto_tags(rel, k, wh, st.st_mtime)}
            items.append(it)
    with LOCK:
        INDEX.clear(); INDEX.update({i["id"]: i for i in items}); VID.clear(); VID.update(vids)
    for i in items:
        i["tags"] = effective(i, d); i["fav"] = i["id"] in d["fav"]
    return items


def edit(req):
    with LOCK:
        it = INDEX.get(req.get("id"))
        if not it: return None
        d = load_data(); i = it["id"]
        added, rem = set(d["added"].get(i, [])), set(d["removed"].get(i, []))
        for t in filter(None, map(norm, req.get("add", []))):
            added.add(t); rem.discard(t)
        for t in filter(None, map(norm, req.get("remove", []))):
            added.discard(t)
            if t in it["auto"]: rem.add(t)
        for k, v in (("added", added), ("removed", rem)):
            if v: d[k][i] = sorted(v)
            else: d[k].pop(i, None)
        if "fav" in req:
            if req["fav"]: d["fav"][i] = 1
            else: d["fav"].pop(i, None)
        save_data(d)
        out = dict(it); out["tags"] = effective(it, d); out["fav"] = i in d["fav"]
        return out


class H(BaseHTTPRequestHandler):
    server_version = "MediaGallery"

    def log_message(self, *a): pass

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=()):
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra: self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    def host_ok(self):
        return self.headers.get("Host", "").rsplit(":", 1)[0].strip("[]") in ("127.0.0.1", "localhost")

    def do_HEAD(self): self.do_GET()

    def do_GET(self):
        if not self.host_ok(): return self.send(403, b"bad host")
        path = urlparse(self.path).path
        if path == "/": return self.send(200, PAGE.encode(), "text/html; charset=utf-8", [("Cache-Control", "no-store")])
        if path == "/api/items":
            return self.send(200, json.dumps({"root": str(ROOT), "items": scan()}).encode(), "application/json", [("Cache-Control", "no-store")])
        if path == "/api/codec":
            p = safe(parse_qs(urlparse(self.path).query).get("id", [""])[0])
            return self.send(200, json.dumps({"codec": codec(p) if p else None}).encode(), "application/json")
        if path.startswith("/v/"):
            with LOCK: rel = VID.get(path[3:])
            return self.file(safe(rel) if rel else None, True)
        if path.startswith("/f/"): return self.file(safe(unquote(path[3:])))
        self.send(404, b"not found")

    def do_POST(self):
        if not self.host_ok(): return self.send(403, b"bad host")
        if self.path != "/api/edit" or "application/json" not in self.headers.get("Content-Type", ""):
            return self.send(404, b"not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 100_000: raise ValueError
            req = json.loads(self.rfile.read(n))
            if not isinstance(req, dict): raise ValueError
        except Exception:
            return self.send(400, b"bad request")
        out = edit(req)
        if out is None: return self.send(404, b"unknown item")
        self.send(200, json.dumps(out).encode(), "application/json")

    def file(self, p, neutral=False):
        if not p: return self.send(404, b"not found")
        size = p.stat().st_size
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m[1] or m[2]) and size:
            if m[1]:
                start = int(m[1]); end = min(int(m[2]), size - 1) if m[2] else size - 1
            else:
                start = max(0, size - int(m[2]))
            if start > end or start >= size:
                return self.send(416, b"", extra=[("Content-Range", f"bytes */{size}")])
            code = 206; end = min(end, start + CHUNK - 1)
        hdr = [("Content-Type", "application/octet-stream" if neutral else MIME.get(p.suffix.lower(), "application/octet-stream")),
               ("Accept-Ranges", "bytes"), ("Content-Length", str(max(0, end - start + 1))), ("Cache-Control", "private, max-age=300")]
        if not neutral: hdr += [("Content-Disposition", "inline"), ("X-Content-Type-Options", "nosniff")]
        self.send_response(code)
        for k, v in hdr: self.send_header(k, v)
        if code == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or not size: return
        try:
            with open(p, "rb") as f:
                f.seek(start); left = end - start + 1
                while left > 0:
                    b = f.read(min(262144, left))
                    if not b: break
                    self.wfile.write(b); left -= len(b)
        except OSError: pass  # client aborted (normal for video seeking)


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gallery</title><style>
:root{--bg:#0e0e11;--fg:#ececf1;--mut:#8d8b96;--line:#25252b;--tile:#17171c;--ac:#c9a7ff;--fit:cover;--r:14px;--g:10px}
[data-theme=light]{--bg:#f6f5f3;--fg:#1d1c20;--mut:#77747d;--line:#e0ddd8;--tile:#eae7e2}
[data-theme=black]{--bg:#000;--tile:#101013;--line:#1e1e22}
*{box-sizing:border-box}html{scrollbar-width:thin;scrollbar-color:var(--line) transparent}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,select{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:99px;padding:7px 14px;transition:background .2s,border-color .2s,color .2s}
select{padding-right:8px}button{cursor:pointer}button:hover,select:hover{border-color:var(--mut)}
button.on{background:var(--ac);border-color:var(--ac);color:#141416}
:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
header{position:sticky;top:0;z-index:5;padding:14px 20px 8px;background:color-mix(in srgb,var(--bg) 80%,transparent);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.row+.row{margin-top:10px}
#q{flex:1;min-width:150px;background:var(--tile);border-color:transparent}#q:focus{border-color:var(--ac);outline:0}
#cnt{color:var(--mut);margin:0 4px}
.tags{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;mask-image:linear-gradient(90deg,#000 94%,transparent)}
.tags button{white-space:nowrap;padding:3px 12px;color:var(--mut);border-color:transparent;background:var(--tile)}.tags button.on{color:#141416;background:var(--ac)}
#grid{display:flex;gap:var(--g);padding:8px 20px 20px;align-items:flex-start}
.c{flex:1;min-width:0;display:flex;flex-direction:column;gap:var(--g)}
.t{position:relative;aspect-ratio:var(--ar);background:var(--tile);border-radius:var(--r);overflow:hidden;cursor:pointer;isolation:isolate}
.t img,.t video{display:block;width:100%;height:100%;object-fit:var(--fit);opacity:0;transition:opacity .6s ease,transform .6s ease;transform:scale(1.015)}
.t .ld{opacity:1;transform:none}
.t span{position:absolute;inset:auto 0 0;padding:26px 12px 9px;font-size:12px;color:#fff;background:linear-gradient(transparent,#000a);opacity:0;transition:opacity .25s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.t:hover span,.t:focus-visible span{opacity:1}
.t b{position:absolute;right:8px;top:8px;font-weight:500;font-size:11px;background:#0008;color:#fff;border-radius:99px;padding:1px 8px;backdrop-filter:blur(6px)}
.t i{position:absolute;left:9px;top:5px;color:var(--ac);font-style:normal;font-size:15px}
.bad::before{content:"Can't play";position:absolute;inset:0;display:grid;place-items:center;color:var(--mut);font-size:12px}
.blur .t img,.blur .t video{filter:blur(24px)}.blur .t:hover img,.blur .t:hover video{filter:none}
#empty{padding:80px 20px;text-align:center;color:var(--mut)}#end{height:1px}
#lb{position:fixed;inset:0;z-index:10;background:#000f;display:grid;grid-template-rows:1fr auto;color:#eee;visibility:hidden;opacity:0;transition:opacity .22s,visibility 0s .22s}
#lb.open{visibility:visible;opacity:1;transition:opacity .22s}
#stage{min-height:0;display:flex;flex-direction:column;gap:10px;align-items:center;justify-content:center;padding:14px}
#stage img,#stage video{max-width:100%;max-height:100%;border-radius:10px;animation:in .3s}@keyframes in{from{opacity:0;transform:scale(.985)}}
#bar{padding:10px 20px 16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#bar button,#bar input{border-color:#34343a;color:#eee}#bar button:hover{border-color:#888}#bar button.on{color:#141416}
#bar .nm{color:#9a98a2;margin-right:auto;max-width:36ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.chip{padding:2px 11px!important}
#set{position:fixed;right:20px;top:64px;z-index:8;background:var(--bg);border:1px solid var(--line);border-radius:18px;padding:16px;display:flex;gap:12px;flex-direction:column;width:270px;box-shadow:0 16px 48px #0006;opacity:0;pointer-events:none;transform:translateY(-6px);transition:opacity .18s,transform .18s}
#set.open{opacity:1;pointer-events:auto;transform:none}#set label{display:flex;justify-content:space-between;align-items:center;gap:10px;color:var(--mut)}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
@media(max-width:600px){header{padding:10px 12px 6px}#grid{padding:6px 12px 16px;--g:6px}}
</style></head><body>
<header><div class="row"><input id="q" type="search" placeholder="Search" aria-label="Search">
<span class="row" id="kinds"></span><span id="cnt"></span><select id="sort" aria-label="Sort"><option value="new">Newest</option><option value="old">Oldest</option><option value="name">Name</option><option value="rand">Shuffle</option></select>
<button id="re" title="Rescan folder">Rescan</button><button id="gear">Settings</button></div>
<div class="row tags" id="tags"></div></header>
<div id="set"><label>Tile size <input id="s_size" type="range" min="140" max="520"></label>
<label>Layout <select id="s_shape"><option value="nat">Natural</option><option value="1/1">Square</option><option value="4/3">Wide</option><option value="3/4">Tall</option></select></label>
<label>Fit (fixed layouts) <select id="s_fit"><option value="cover">Fill</option><option value="contain">Whole</option></select></label>
<label>Theme <select id="s_theme"><option value="dark">Dark</option><option value="black">Black</option><option value="light">Light</option></select></label>
<label>Accent <input id="s_ac" type="color"></label>
<label>Blur until hover <input id="s_blur" type="checkbox"></label>
<label>Video previews <select id="s_prev"><option value="all">Play visible</option><option value="hover">On hover</option><option value="off">Off</option></select></label>
<label>Playing at once <select id="s_cap"><option>4</option><option>8</option><option>12</option><option>24</option><option>60</option></select></label></div>
<main id="grid"></main><div id="end"></div><div id="empty" hidden></div>
<div id="lb"><div id="stage"></div><div id="bar"><span class="nm" id="nm"></span><span class="row" id="chips"></span>
<input id="add" placeholder="Add tag, Enter" aria-label="Add tag" size="14"><button id="fav">Favorite</button><button id="prev">Prev</button><button id="next">Next</button><button id="x">Close</button></div></div>
<script>
const $=s=>document.querySelector(s),esc=s=>String(s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
let S={size:240,shape:'nat',fit:'cover',theme:'dark',accent:'#c9a7ff',blur:false,preview:'all',cap:8,sort:'new',kind:'all'};
try{Object.assign(S,JSON.parse(localStorage.getItem('mg')||'{}'))}catch{}
const save=()=>{try{localStorage.setItem('mg',JSON.stringify(S))}catch{}};
let items=[],view=[],root='',shown=0,cur=-1,dirty=false,tagF=new Set(),seed=1,cs=[],hs=[];const BATCH=120,vis=new Set();
function applyS(){const r=document.documentElement;r.dataset.theme=S.theme;r.style.setProperty('--fit',S.fit);r.style.setProperty('--ac',S.accent);
 document.body.classList.toggle('blur',S.blur);$('#s_size').value=S.size;$('#s_shape').value=S.shape;$('#s_fit').value=S.fit;$('#s_theme').value=S.theme;
 $('#s_ac').value=S.accent;$('#s_blur').checked=S.blur;$('#s_prev').value=S.preview;$('#s_cap').value=S.cap;$('#sort').value=S.sort}
function set(k,v){S[k]=v;save();applyS()}
const bind=(id,ev,fn)=>{$(id)['on'+ev]=e=>fn(e.target)};
bind('#s_size','input',t=>{set('size',+t.value);render(1)});bind('#s_shape','change',t=>{set('shape',t.value);render(1)});
bind('#s_fit','change',t=>set('fit',t.value));bind('#s_theme','change',t=>set('theme',t.value));bind('#s_ac','input',t=>set('accent',t.value));
bind('#s_blur','change',t=>set('blur',t.checked));bind('#s_prev','change',t=>{set('preview',t.value);sync()});bind('#s_cap','change',t=>{set('cap',+t.value);sync()});
$('#gear').onclick=()=>$('#set').classList.toggle('open');
$('#sort').onchange=e=>{seed++;set('sort',e.target.value);render()};$('#q').oninput=()=>render();$('#re').onclick=load;
const hash=s=>{let h=seed;for(const c of s)h=(h*31+c.charCodeAt(0))|0;return h};
const ratio=it=>{if(S.shape!='nat'){const[a,b]=S.shape.split('/');return a/b}return it.w&&it.h?it.w/it.h:it.kind=='video'?16/9:1};
const nCols=()=>{const g=$('#grid'),sz=innerWidth<600?S.size*.55:S.size;return Math.max(1,Math.round((g.clientWidth-40)/(sz+10)))};
function build(){const n=nCols();$('#grid').innerHTML='<div class="c"></div>'.repeat(n);cs=[...$('#grid').children];hs=Array(n).fill(0)}
function render(keep){
 const q=$('#q').value.trim().toLowerCase();
 const kd=[['all','All'],['image','Images'],['gif','GIFs'],['video','Videos'],['fav','Favorites']];
 $('#kinds').innerHTML=kd.map(([k,l])=>`<button data-k="${k}" class="${S.kind==k?'on':''}">${l}</button>`).join('');
 const base=items.filter(i=>S.kind=='all'||(S.kind=='fav'?i.fav:i.kind==S.kind));
 const cnt={};base.forEach(i=>i.tags.forEach(t=>cnt[t]=(cnt[t]||0)+1));[...tagF].forEach(t=>{if(!cnt[t])tagF.delete(t)});
 const top=Object.entries(cnt).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,60);
 $('#tags').innerHTML=top.map(([t,n])=>`<button data-t="${esc(t)}" class="${tagF.has(t)?'on':''}">${esc(t)} ${n}</button>`).join('');
 view=base.filter(i=>[...tagF].every(t=>i.tags.includes(t))&&(!q||(i.name+' '+i.tags.join(' ')).toLowerCase().includes(q)));
 const o=S.sort;view.sort(o=='old'?(a,b)=>a.mtime-b.mtime:o=='name'?(a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}):o=='rand'?(a,b)=>hash(a.id)-hash(b.id):(a,b)=>b.mtime-a.mtime);
 const want=keep?Math.max(shown,BATCH):BATCH;$('#grid').querySelectorAll('video').forEach(v=>{v.pause();v.removeAttribute('src');v.load()});vio.disconnect();pio.disconnect();vis.clear();build();shown=0;more(want);
 $('#cnt').textContent=view.length+(view.length==1?' item':' items');
 const em=$('#empty');em.hidden=view.length>0;
 em.textContent=items.length?'Nothing matches these filters.':'No media yet. Put images, GIFs and videos in '+root+' (subfolders become tags), then press Rescan.'}
const vio=new IntersectionObserver(es=>es.forEach(e=>{const v=e.target;if(e.isIntersecting&&!v.src){v.src=v.dataset.src;vio.unobserve(v)}}),{rootMargin:'500px'});
const pio=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting)vis.add(e.target);else{vis.delete(e.target);e.target.pause()}});sync()},{threshold:.3});
const sent=new IntersectionObserver(es=>{if(es[0].isIntersecting)more()},{rootMargin:'1400px'});
function more(n=BATCH){const end=Math.min(shown+n,view.length);
 for(let i=shown;i<end;i++){const it=view[i],r=ratio(it),k=hs.indexOf(Math.min(...hs));hs[k]+=1/r+.03;
  cs[k].insertAdjacentHTML('beforeend',`<div class="t" data-i="${i}" tabindex="0" style="--ar:${r}">`+(it.kind=='video'?`<video data-src="${it.url}#t=0.1" muted loop playsinline preload="metadata" disablepictureinpicture></video>`:`<img src="${it.url}" loading="lazy" decoding="async" alt="">`)+`<span>${esc(it.name)}</span>`+(it.kind!='image'?`<b>${it.kind}</b>`:'')+(it.fav?'<i>★</i>':'')+'</div>')}
 shown=end;$('#grid').querySelectorAll('video:not([data-o])').forEach(v=>{v.dataset.o=1;vio.observe(v);pio.observe(v)});
 sent.unobserve($('#end'));sent.observe($('#end'))}
function sync(){const on=S.preview=='all'&&cur<0&&!document.hidden;let n=0;
 [...vis].sort((a,b)=>a.getBoundingClientRect().top-b.getBoundingClientRect().top).forEach(v=>{
  if(on&&v.src&&n<S.cap){n++;v.muted=true;v.play().catch(()=>{})}
  else if(S.preview!='hover'||cur>=0||!v.closest('.t').matches(':hover'))v.pause()})}
document.addEventListener('visibilitychange',sync);
let rz;addEventListener('resize',()=>{clearTimeout(rz);rz=setTimeout(()=>{if(nCols()!=cs.length)render(1)},200)});
const g=$('#grid');
['load','loadeddata','loadedmetadata'].forEach(ev=>g.addEventListener(ev,e=>{const m=e.target;m.classList.add('ld');
 if(m.tagName=='VIDEO'){if(S.shape=='nat'&&m.videoWidth)m.closest('.t').style.setProperty('--ar',m.videoWidth+'/'+m.videoHeight);sync()}},true));
g.addEventListener('error',e=>{if(e.target.closest)e.target.closest('.t')?.classList.add('bad')},true);
g.addEventListener('mouseenter',e=>{if(S.preview!='hover'||cur>=0||!e.target.classList?.contains('t'))return;const v=e.target.querySelector('video');if(v&&v.src){v.muted=true;v.play().catch(()=>{})}},true);
g.addEventListener('mouseleave',e=>{if(S.preview=='hover'&&e.target.classList?.contains('t')){const v=e.target.querySelector('video');if(v)v.pause()}},true);
$('#kinds').onclick=e=>{const k=e.target.dataset.k;if(k){set('kind',k);render()}};
$('#tags').onclick=e=>{const t=e.target.dataset.t;if(t){tagF.has(t)?tagF.delete(t):tagF.add(t);render()}};
const tileOf=e=>e.target.closest('.t');
g.onclick=e=>{const t=tileOf(e);if(t)open(+t.dataset.i)};g.onkeydown=e=>{if(e.key=='Enter'){const t=tileOf(e);if(t)open(+t.dataset.i)}};
function meta(){const it=view[cur];$('#nm').textContent=it.name;$('#fav').classList.toggle('on',it.fav);
 $('#chips').innerHTML=it.tags.map(t=>`<button class="chip" data-t="${esc(t)}" title="Remove tag">${esc(t)} ×</button>`).join('')}
function open(i){if(i<0||i>=view.length)return;cur=i;const it=view[i];$('#lb').classList.add('open');sync();
 $('#stage').innerHTML=it.kind=='video'?`<video src="${it.url}" controls controlsList="nodownload" autoplay loop></video>`:`<img src="${it.url}" alt="${esc(it.name)}">`;
 const vd=$('#stage video');if(vd)vd.onerror=async()=>{let c='';try{c=(await(await fetch('/api/codec?id='+encodeURIComponent(it.id))).json()).codec||''}catch{}
  $('#stage').insertAdjacentHTML('beforeend','<p style="color:#bbb;max-width:60ch">This browser can\'t play this video'+(c?' (codec: '+c+')':'')+'. Browsers play H.264 mp4 and VP9 webm reliably. Convert with: ffmpeg -i in.mov -c:v libx264 -c:a aac out.mp4</p>')};
 meta()}
function close(){cur=-1;$('#lb').classList.remove('open');$('#stage').innerHTML='';if(dirty){dirty=false;render(1)}else sync()}
$('#stage').onclick=e=>{if(e.target.id=='stage')close()};
async function edit(body){const it=view[cur];try{const r=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:it.id,...body})});
 if(!r.ok)throw 0;Object.assign(it,await r.json());dirty=true;meta()}catch{alert('Could not save. Is the server still running?')}}
$('#chips').onclick=e=>{const t=e.target.dataset.t;if(t)edit({remove:[t]})};
$('#add').onkeydown=e=>{e.stopPropagation();if(e.key=='Enter'&&e.target.value.trim()){const v=e.target.value.split(',');e.target.value='';edit({add:v})}};
$('#fav').onclick=()=>edit({fav:!view[cur].fav});$('#prev').onclick=()=>open(cur-1);$('#next').onclick=()=>open(cur+1);$('#x').onclick=close;
document.onkeydown=e=>{if(cur<0||e.target.tagName=='INPUT')return;
 if(e.key=='Escape')close();else if(e.key=='ArrowLeft')open(cur-1);else if(e.key=='ArrowRight')open(cur+1);else if(e.key=='f')$('#fav').click()};
async function load(){try{const r=await fetch('/api/items');const d=await r.json();items=d.items;root=d.root}catch{items=[];root='the media folder'}render()}
applyS();load();
</script></body></html>"""


class Srv(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError): super().handle_error(request, client_address)


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", default=str(HERE / "media")); ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    ROOT = Path(a.media).expanduser().resolve(); ROOT.mkdir(parents=True, exist_ok=True)
    srv = Srv(("127.0.0.1", a.port), H)
    print(f"Gallery: http://127.0.0.1:{a.port}  (media: {ROOT})")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
