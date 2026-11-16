#!/usr/bin/env python3
"""Local media gallery. Run: python3 gallery.py [--media DIR] [--port N]
Serves on 127.0.0.1 only. Stdlib only; UI is embedded below."""
import argparse, json, os, sys, re, struct, threading, time
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
INDEX, CACHE = {}, {}
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
    d, items = load_data(), []
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
            it = {"id": rel, "name": os.path.basename(rel), "kind": k, "url": "/f/" + quote(rel), "w": wh[0] if wh else 0,
                  "h": wh[1] if wh else 0, "mtime": int(st.st_mtime), "size": st.st_size,
                  "auto": auto_tags(rel, k, wh, st.st_mtime)}
            items.append(it)
    with LOCK:
        INDEX.clear(); INDEX.update({i["id"]: i for i in items})
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
        if path.startswith("/f/"): return self.file(unquote(path[3:]))
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

    def file(self, rel):
        p = safe(rel)
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
            code = 206
        ctype = MIME.get(p.suffix.lower(), "application/octet-stream")
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Accept-Ranges", "bytes"), ("Content-Length", str(max(0, end - start + 1))),
                     ("Cache-Control", "private, max-age=300"), ("Content-Disposition", "inline"), ("X-Content-Type-Options", "nosniff")):
            self.send_header(k, v)
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
:root{--bg:#121214;--fg:#e8e6ea;--mut:#8b8892;--line:#2a2a2f;--tile:#1c1c20;--ac:#c9a7ff;--s:220px;--ar:1/1;--fit:cover}
[data-theme=light]{--bg:#f5f4f2;--fg:#1d1c20;--mut:#76737c;--line:#dedbd6;--tile:#e8e5e0}
[data-theme=black]{--bg:#000;--tile:#111;--line:#222}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,select{font:inherit;color:inherit;background:var(--tile);border:1px solid var(--line);border-radius:8px;padding:6px 11px}
button{cursor:pointer}button:hover{border-color:var(--mut)}button.on{background:var(--ac);border-color:var(--ac);color:#111}
:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
header{position:sticky;top:0;z-index:5;background:var(--bg);padding:12px 16px 8px;border-bottom:1px solid var(--line)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.row+.row{margin-top:8px}
#q{flex:1;min-width:160px}.tags{overflow-x:auto;flex-wrap:nowrap;scrollbar-width:thin;padding-bottom:4px}
.tags button{white-space:nowrap;border-radius:99px;padding:3px 10px;color:var(--mut)}.tags button.on{color:#111}
#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--s),1fr));gap:6px;padding:12px 16px}
.t{position:relative;aspect-ratio:var(--ar);background:var(--tile);border-radius:6px;overflow:hidden;cursor:pointer}
.t img,.t video{width:100%;height:100%;object-fit:var(--fit);display:block}
.t b{position:absolute;left:6px;bottom:6px;font-weight:500;font-size:11px;background:#000a;color:#fff;border-radius:4px;padding:1px 6px}
.t i{position:absolute;right:6px;top:4px;color:var(--ac);font-style:normal}
.blur .t img,.blur .t video{filter:blur(22px)}.blur .t:hover img,.blur .t:hover video{filter:none}
#empty{padding:60px 16px;text-align:center;color:var(--mut)}
#lb{position:fixed;inset:0;z-index:10;background:#000e;display:none;grid-template-rows:1fr auto;color:#eee}
#lb.open{display:grid}#stage{min-height:0;display:flex;align-items:center;justify-content:center;padding:12px}
#stage img,#stage video{max-width:100%;max-height:100%;border-radius:4px}
#bar{padding:10px 16px 14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;background:#111;border-top:1px solid #333}
#bar button,#bar input{background:#1c1c20;border-color:#333;color:#eee}#bar .nm{color:#999;margin-right:auto;max-width:40ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip{border-radius:99px!important;padding:2px 10px!important}
#set{position:fixed;right:16px;top:60px;z-index:8;background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:14px;display:none;gap:10px;flex-direction:column;width:250px}
#set.open{display:flex}#set label{display:flex;justify-content:space-between;align-items:center;gap:10px}
@media(max-width:600px){:root{--s:130px}#grid{padding:8px;gap:4px}}
@media(prefers-reduced-motion:no-preference){#lb.open{animation:f .12s}}@keyframes f{from{opacity:0}}
</style></head><body>
<header><div class="row"><input id="q" type="search" placeholder="Search names and tags" aria-label="Search">
<span class="row" id="kinds"></span><select id="sort" aria-label="Sort"><option value="new">Newest</option><option value="old">Oldest</option><option value="name">Name</option><option value="rand">Shuffle</option></select>
<button id="re" title="Rescan folder">Rescan</button><button id="gear">Settings</button></div>
<div class="row tags" id="tags"></div></header>
<div id="set"><label>Tile size <input id="s_size" type="range" min="110" max="480"></label>
<label>Tile shape <select id="s_shape"><option value="1/1">Square</option><option value="4/3">Wide</option><option value="3/4">Tall</option></select></label>
<label>Image fit <select id="s_fit"><option value="cover">Fill tile</option><option value="contain">Show whole</option></select></label>
<label>Theme <select id="s_theme"><option value="dark">Dark</option><option value="black">Black</option><option value="light">Light</option></select></label>
<label>Accent <input id="s_ac" type="color"></label>
<label>Blur until hover <input id="s_blur" type="checkbox"></label>
<label>Play videos on hover <input id="s_hover" type="checkbox"></label></div>
<main id="grid"></main><div id="empty" hidden></div>
<div id="lb"><div id="stage"></div><div id="bar"><span class="nm" id="nm"></span><span class="row" id="chips"></span>
<input id="add" placeholder="Add tag, Enter" aria-label="Add tag" size="14"><button id="fav">Favorite</button><button id="prev">Prev</button><button id="next">Next</button><button id="x">Close</button></div></div>
<script>
const $=s=>document.querySelector(s),esc=s=>String(s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
let S={size:220,shape:'1/1',fit:'cover',theme:'dark',accent:'#c9a7ff',blur:false,hover:true,sort:'new',kind:'all'};
try{Object.assign(S,JSON.parse(localStorage.getItem('mg')||'{}'))}catch{}
const save=()=>{try{localStorage.setItem('mg',JSON.stringify(S))}catch{}};
let items=[],view=[],root='',shown=0,cur=-1,tagF=new Set(),seed=1;const BATCH=150;
function applyS(){const r=document.documentElement;r.dataset.theme=S.theme;r.style.setProperty('--s',S.size+'px');r.style.setProperty('--ar',S.shape);
 r.style.setProperty('--fit',S.fit);r.style.setProperty('--ac',S.accent);document.body.classList.toggle('blur',S.blur);
 $('#s_size').value=S.size;$('#s_shape').value=S.shape;$('#s_fit').value=S.fit;$('#s_theme').value=S.theme;$('#s_ac').value=S.accent;
 $('#s_blur').checked=S.blur;$('#s_hover').checked=S.hover;$('#sort').value=S.sort}
function set(k,v){S[k]=v;save();applyS()}
$('#s_size').oninput=e=>set('size',+e.target.value);$('#s_shape').onchange=e=>set('shape',e.target.value);$('#s_fit').onchange=e=>set('fit',e.target.value);
$('#s_theme').onchange=e=>set('theme',e.target.value);$('#s_ac').oninput=e=>set('accent',e.target.value);
$('#s_blur').onchange=e=>set('blur',e.target.checked);$('#s_hover').onchange=e=>set('hover',e.target.checked);
$('#gear').onclick=()=>$('#set').classList.toggle('open');
$('#sort').onchange=e=>{seed++;set('sort',e.target.value);render()};$('#q').oninput=()=>render();$('#re').onclick=load;
function hash(s){let h=seed;for(const c of s)h=(h*31+c.charCodeAt(0))|0;return h}
function render(){
 const q=$('#q').value.trim().toLowerCase();
 const kd=[['all','All'],['image','Images'],['gif','GIFs'],['video','Videos'],['fav','Favorites']];
 $('#kinds').innerHTML=kd.map(([k,l])=>`<button data-k="${k}" class="${S.kind==k?'on':''}">${l}</button>`).join('');
 const base=items.filter(i=>S.kind=='all'||(S.kind=='fav'?i.fav:i.kind==S.kind));
 const cnt={};base.forEach(i=>i.tags.forEach(t=>cnt[t]=(cnt[t]||0)+1));
 [...tagF].forEach(t=>{if(!cnt[t])tagF.delete(t)});
 const top=Object.entries(cnt).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,60);
 $('#tags').innerHTML=top.map(([t,n])=>`<button data-t="${esc(t)}" class="${tagF.has(t)?'on':''}">${esc(t)} ${n}</button>`).join('');
 view=base.filter(i=>[...tagF].every(t=>i.tags.includes(t))&&(!q||(i.name+' '+i.tags.join(' ')).toLowerCase().includes(q)));
 const o=S.sort;view.sort(o=='old'?(a,b)=>a.mtime-b.mtime:o=='name'?(a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}):o=='rand'?(a,b)=>hash(a.id)-hash(b.id):(a,b)=>b.mtime-a.mtime);
 $('#grid').innerHTML='';shown=0;more();
 const em=$('#empty');em.hidden=view.length>0;
 em.textContent=items.length?'Nothing matches these filters.':'No media yet. Put images, GIFs and videos in '+root+' (subfolders become tags), then press Rescan.'}
const vio=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){const v=e.target;if(!v.src)v.src=v.dataset.src;vio.unobserve(v)}}),{rootMargin:'300px'});
const sent=new IntersectionObserver(es=>{if(es[0].isIntersecting)more()},{rootMargin:'800px'});
function more(){const g=$('#grid');sent.disconnect();if(shown>=view.length)return;let h='';
 for(let n=shown;n<Math.min(shown+BATCH,view.length);n++){const it=view[n];
  h+=`<div class="t" data-i="${n}" tabindex="0" title="${esc(it.name)}">`+(it.kind=='video'?`<video data-src="${it.url}#t=0.1" muted loop playsinline preload="metadata"></video>`:`<img src="${it.url}" loading="lazy" alt="${esc(it.name)}">`)+(it.kind!='image'?`<b>${it.kind}</b>`:'')+(it.fav?'<i>★</i>':'')+'</div>'}
 shown=Math.min(shown+BATCH,view.length);g.insertAdjacentHTML('beforeend',h);g.querySelectorAll('video:not([src])').forEach(v=>vio.observe(v));
 if(g.lastElementChild)sent.observe(g.lastElementChild)}
$('#kinds').onclick=e=>{const k=e.target.dataset.k;if(k){set('kind',k);render()}};
$('#tags').onclick=e=>{const t=e.target.dataset.t;if(t){tagF.has(t)?tagF.delete(t):tagF.add(t);render()}};
const tileOf=e=>e.target.closest('.t');
$('#grid').onclick=e=>{const t=tileOf(e);if(t)open(+t.dataset.i)};
$('#grid').onkeydown=e=>{if(e.key=='Enter'){const t=tileOf(e);if(t)open(+t.dataset.i)}};
$('#grid').addEventListener('mouseenter',e=>{const v=e.target.tagName=='DIV'&&e.target.querySelector('video');if(v&&S.hover&&v.src)v.play().catch(()=>{})},true);
$('#grid').addEventListener('mouseleave',e=>{const v=e.target.tagName=='DIV'&&e.target.querySelector('video');if(v)v.pause()},true);
function open(i){if(i<0||i>=view.length)return;cur=i;const it=view[i];$('#lb').classList.add('open');
 $('#stage').innerHTML=it.kind=='video'?`<video src="${it.url}" controls controlsList="nodownload" autoplay loop></video>`:`<img src="${it.url}" alt="${esc(it.name)}">`;
 const vd=$('#stage video');if(vd)vd.onerror=async()=>{let c='';try{c=(await(await fetch('/api/codec?id='+encodeURIComponent(it.id))).json()).codec||''}catch{}
  $('#stage').insertAdjacentHTML('beforeend','<p style="color:#bbb;max-width:60ch">This browser can\'t play this video'+(c?' (codec: '+c+')':'')+'. Browsers play H.264 mp4 and VP9 webm reliably. Convert with: ffmpeg -i in.mov -c:v libx264 -c:a aac out.mp4</p>')};
 $('#nm').textContent=it.name;$('#fav').classList.toggle('on',it.fav);
 $('#chips').innerHTML=it.tags.map(t=>`<button class="chip" data-t="${esc(t)}" title="Remove tag">${esc(t)} ×</button>`).join('')}
function close(){cur=-1;$('#lb').classList.remove('open');$('#stage').innerHTML='';render()}
async function edit(body){const it=view[cur];try{const r=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:it.id,...body})});
 if(!r.ok)throw 0;const n=await r.json();Object.assign(it,n);const j=items.findIndex(x=>x.id==it.id);if(j>=0)items[j]=it;
 const st=$('#stage video');const t=st?st.currentTime:0;open(cur);const v=$('#stage video');if(v&&t)v.currentTime=t}catch{alert('Could not save. Is the server still running?')}}
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
