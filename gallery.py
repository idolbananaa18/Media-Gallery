#!/usr/bin/env python3
"""Local media gallery. Run: python3 gallery.py [--media DIR] [--port N]
Serves on 127.0.0.1 only. Stdlib only; UI is embedded below."""
import argparse, hashlib, json, os, sys, re, struct, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote, quote
try:  # optional: with Pillow installed, big photos get cached ~1280px thumbnails for the wall (much smoother). Without it, nothing changes.
    from PIL import Image, ImageOps
except Exception:
    Image = ImageOps = None

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
THUMB_DIR = HERE / ".thumbs"
THUMB_MAX = 1280                      # longest side of a wall thumbnail, px
THUMB_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}   # gif stays animated/original; avif needs a Pillow plugin
THUMB_GATE = threading.Semaphore(2)   # decode at most 2 big photos at once so the machine stays responsive
USE_THUMBS = Image is not None
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


def mp4_dims(f, size):
    """Display (w, h) of the first video track in an mp4/mov, honoring 90/270 rotation."""
    pos = 0
    while pos + 8 <= size:
        f.seek(pos); h = f.read(8)
        if len(h) < 8: return None
        n, t = struct.unpack(">I4s", h); hdr = 8
        if n == 1: n = struct.unpack(">Q", f.read(8))[0]; hdr = 16
        elif n == 0: n = size - pos
        if n < hdr: return None
        if t == b"moov":
            blob, i = f.read(min(n - hdr, 8_000_000)), 0
            while True:
                i = blob.find(b"tkhd", i)
                if i < 0 or i + 100 > len(blob): return None
                v = blob[i + 4]; m = i + 4 + (52 if v == 1 else 40); o = m + 36
                w, hh = (x >> 16 for x in struct.unpack(">II", blob[o:o + 8]))
                if w and hh:
                    a, b, _, c, d = struct.unpack(">iiiii", blob[m:m + 20])
                    return (hh, w) if a == 0 and d == 0 and b and c else (w, hh)
                i += 4
        pos += n
    return None


def dims(p):
    """(w, h) from file headers for png/gif/jpeg/webp/bmp/mp4/mov, else None."""
    try:
        with open(p, "rb") as f:
            if p.suffix.lower() in (".mp4", ".m4v", ".mov"): return mp4_dims(f, os.fstat(f.fileno()).st_size)
            h = f.read(32)
            if h[:2] == b"BM": w, hh = struct.unpack("<ii", h[18:26]); return abs(w), abs(hh)
            if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
                f.seek(12); c = f.read(30); t = c[:4]
                if t == b"VP8X": return int.from_bytes(c[12:15], "little") + 1, int.from_bytes(c[15:18], "little") + 1
                if t == b"VP8 ": w, hh = struct.unpack("<HH", c[14:18]); return w & 0x3fff, hh & 0x3fff
                if t == b"VP8L": b = int.from_bytes(c[9:13], "little"); return (b & 0x3fff) + 1, ((b >> 14) & 0x3fff) + 1
                return None
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


def thumb_path(p, st):
    """Path of the cached thumbnail for p, creating it if needed; None means 'use the original'."""
    if not USE_THUMBS: return None
    out = THUMB_DIR / (hashlib.sha1(f"{p}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()[:24] + ".jpg")
    if out.is_file(): return out
    png = out.with_suffix(".png")
    if png.is_file(): return png
    with THUMB_GATE:
        if out.is_file(): return out
        if png.is_file(): return png
        try:
            THUMB_DIR.mkdir(exist_ok=True)
            with Image.open(p) as im:
                if im.format == "JPEG": im.draft("RGB", (THUMB_MAX, THUMB_MAX))  # decode at reduced size: much faster
                im = ImageOps.exif_transpose(im)
                im.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)
                alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
                dest, tmp = (png, png.with_suffix(".tmp")) if alpha else (out, out.with_suffix(".tmp"))
                (im.convert("RGBA") if alpha else im.convert("RGB")).save(tmp, "PNG" if alpha else "JPEG", **({} if alpha else {"quality": 85}))
            os.replace(tmp, dest)
            return dest
        except Exception:
            return None


def norm(t):
    return re.sub(r"[^a-z0-9 \-]", "", str(t).lower()).strip()[:40]


def load_data():
    try:
        d = json.loads(DATA_FILE.read_text())
        return {k: d.get(k, {}) for k in ("added", "removed")}
    except Exception:
        return {"added": {}, "removed": {}}


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
                CACHE[key] = (k, dims(p))
            k, wh = CACHE[key]
            url = "/f/" + quote(rel)
            if k == "video":  # extension-less URL + neutral type so download managers don't treat it as a video
                h = hashlib.sha1(rel.encode()).hexdigest()[:16]; vids[h] = rel; url = "/v/" + h
            th = "/t/" + quote(rel) if USE_THUMBS and k == "image" and ext in THUMB_EXT and wh and max(wh) > THUMB_MAX else None
            it = {"id": rel, "name": os.path.basename(rel), "kind": k, "url": url, "th": th, "w": wh[0] if wh else 0,
                  "h": wh[1] if wh else 0, "mtime": int(st.st_mtime), "size": st.st_size,
                  "auto": auto_tags(rel, k, wh, st.st_mtime)}
            items.append(it)
    with LOCK:
        INDEX.clear(); INDEX.update({i["id"]: i for i in items}); VID.clear(); VID.update(vids)
    for i in items:
        i["tags"] = effective(i, d)
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
        save_data(d)
        out = dict(it); out["tags"] = effective(it, d)
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
        if path.startswith("/t/"):
            p = safe(unquote(path[3:]))
            if not p: return self.file(None)
            try: t = thumb_path(p, p.stat())
            except OSError: t = None
            return self.file(t or p)
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
:root{--bg:#0b0b0e;--fg:#ececf1;--mut:#8d8b96;--line:#2a2a31;--tile:#17171c;--ac:#c9a7ff;--sw:1}
[data-theme=light]{--bg:#f1efec;--fg:#1d1c20;--mut:#77747d;--line:#dbd8d2;--tile:#e6e3de}
[data-theme=black]{--bg:#000;--tile:#101013;--line:#1e1e22}
*{box-sizing:border-box}html,body{height:100%;margin:0;overflow:hidden;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
button,input,select{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:99px;padding:6px 13px;transition:background .2s,border-color .2s,color .2s}
select{padding-right:8px}button{cursor:pointer}button:hover,select:hover{border-color:var(--mut)}
button.on{background:var(--ac);border-color:var(--ac);color:#141416}:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
#wall{position:fixed;inset:0;overflow:hidden;touch-action:none}
#world{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform;transition:opacity .25s}#world.out{opacity:0}
.m{position:absolute;left:0;top:0}.m:hover{z-index:3}
.f{position:relative;cursor:pointer;animation:sw var(--du) ease-in-out var(--de) infinite alternate}
.f:hover{animation-play-state:paused}.nosway .f{animation:none}.noshadow .g>img,.noshadow .g>video{box-shadow:none}
@keyframes sw{from{transform:translate3d(0,0,0) rotate(calc(var(--rt) * var(--sw) * -1))}to{transform:translate3d(calc(var(--dx) * var(--sw)),calc(var(--dy) * var(--sw)),0) rotate(calc(var(--rt) * var(--sw)))}}
.g{pointer-events:none;position:relative;width:100%;height:100%;translate:var(--hx,0px) var(--hy,0px);transition:transform .4s ease,translate .6s cubic-bezier(.22,.8,.25,1)}.f:hover>.g{transform:scale(var(--hs,1.02))}.nolabels .g>span{display:none}
.g>img,.g>video{display:block;width:100%;height:100%;border-radius:8px;background:var(--tile);opacity:0;box-shadow:0 14px 44px #0007;transition:opacity .9s ease}
.g>.ld{opacity:1}
.g>span{position:absolute;inset:auto 0 0;padding:28px 12px 9px;font-size:12px;color:#fff;background:linear-gradient(transparent,#000b);border-radius:0 0 8px 8px;opacity:0;transition:opacity .25s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;pointer-events:none}
.f:hover .g>span{opacity:1}
.g>.bad::after{content:"Can't play"}
.blur .g>img,.blur .g>video{filter:blur(26px)}.blur .f:hover .g>img,.blur .f:hover .g>video{filter:none}
header,#set,#hint{transition:opacity .35s,transform .35s,visibility 0s}
header{position:fixed;z-index:5;top:14px;left:20px;right:20px;padding:10px 12px;border-radius:20px;background:color-mix(in srgb,var(--bg) 72%,transparent);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);border:1px solid var(--line)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.row+.row{margin-top:8px}
#q{flex:1;min-width:140px;background:var(--tile);border-color:transparent}#q:focus{border-color:var(--ac);outline:0}#cnt{color:var(--mut);margin:0 4px}
.tags{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;mask-image:linear-gradient(90deg,#000 94%,transparent)}
.tags button{white-space:nowrap;padding:2px 11px;color:var(--mut);border-color:transparent;background:var(--tile)}.tags button.on{color:#141416;background:var(--ac)}
#set{position:fixed;right:20px;top:calc(14px + var(--hh,96px) + 8px);z-index:6;background:var(--bg);border:1px solid var(--line);border-radius:18px;padding:16px;display:flex;gap:12px;flex-direction:column;width:270px;box-shadow:0 16px 48px #0006;opacity:0;pointer-events:none;transform:translateY(-6px)}
#set.open{opacity:1;pointer-events:auto;transform:none}#set label{display:flex;justify-content:space-between;align-items:center;gap:10px;color:var(--mut)}
#empty{position:fixed;inset:0;display:grid;place-items:center;text-align:center;padding:40px;color:var(--mut);pointer-events:none}#empty[hidden]{display:none}
#hint{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--tile);border:1px solid var(--line);border-radius:99px;padding:7px 16px;color:var(--mut);opacity:0;pointer-events:none;z-index:9}#hint.show{opacity:1}
#dot{display:none;position:fixed;left:10px;top:10px;width:28px;height:28px;padding:0;border-radius:50%;opacity:.12;z-index:9}
@media(pointer:coarse){body.hide #dot{display:block}}
body.hide header,body.hide #set,body.hide .g>span,body.hide #bar{opacity:0!important;visibility:hidden;pointer-events:none;transform:translateY(-8px)}
#lb{position:fixed;inset:0;z-index:10;background:#000f;display:grid;grid-template-rows:1fr auto;color:#eee;visibility:hidden;opacity:0;transition:opacity .22s,visibility 0s .22s}
#lb.open{visibility:visible;opacity:1;transition:opacity .22s}
#stage{min-height:0;display:flex;flex-direction:column;gap:10px;align-items:center;justify-content:center;padding:14px}
#stage img,#stage video{max-width:100%;max-height:100%;border-radius:10px;animation:in .3s}@keyframes in{from{opacity:0;transform:scale(.985)}}
#bar{padding:10px 20px 16px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#bar button,#bar input{border-color:#34343a;color:#eee}#bar button:hover{border-color:#888}
#bar .nm{color:#9a98a2;margin-right:auto;max-width:36ch;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.chip{padding:2px 11px!important}
@media(prefers-reduced-motion:reduce){.f{animation:none}*{transition:none!important}}
@media(max-width:600px){header{left:8px;right:8px;top:8px}}
</style></head><body>
<div id="wall"><div id="world"></div></div>
<header><div class="row"><input id="q" type="search" placeholder="Search" aria-label="Search">
<span class="row" id="kinds"></span><span id="cnt"></span><button id="re" title="Rescan folder">Rescan</button><button id="gear">Settings</button><button id="hide" title="Hide controls (H)">Hide</button></div>
<div class="row tags" id="tags"></div></header>
<div id="set"><label>Zoom <input id="s_zoom" type="range" min="25" max="200" step="5"></label>
<label title="How much a tile grows when hovered (100% = no growth)">Hover grow <input id="s_hover" type="range" min="100" max="300" step="1"></label>
<label title="Scene size: media stays the same size, gaps grow, and the mouse travels further across it">Spread <input id="s_spread" type="range" min="75" max="400" step="25"></label>
<label>Sway <select id="s_sway"><option value="0">Off</option><option value="1">Gentle</option><option value="2.5">Strong</option></select></label>
<label>Theme <select id="s_theme"><option value="dark">Dark</option><option value="black">Black</option><option value="light">Light</option></select></label>
<label>Accent <input id="s_ac" type="color"></label>
<label>Blur until hover <input id="s_blur" type="checkbox"></label>
<label>Show names <input id="s_labels" type="checkbox"></label>
<label title="Soft shadow under each tile. Turning it off makes big walls noticeably smoother">Shadows <input id="s_shadows" type="checkbox"></label>
<label title="On hover, the tile slides toward the centre of your screen. 0 = off, 100 = all the way to the centre. Its original spot stays the hover area.">Hover pull <input id="s_pull" type="range" min="0" max="100" step="5"></label>
<label title="Which videos play (when limited by the playing cap): those nearest the centre of the screen (0%) or nearest your mouse (100%)">Video focus <input id="s_vf" type="range" min="0" max="100" step="10"></label>
<label>Video previews <select id="s_prev"><option value="all">Play in view</option><option value="hover">On hover</option><option value="off">Off</option></select></label>
<label>Playing at once <select id="s_cap"><option>3</option><option>6</option><option>10</option><option>16</option><option>30</option></select></label></div>
<div id="empty" hidden></div><div id="hint">Press H to bring the controls back</div><button id="dot" aria-label="Show controls"></button>
<div id="lb"><div id="stage"></div><div id="bar"><span class="nm" id="nm"></span><span class="row" id="chips"></span>
<input id="add" placeholder="Add tag, Enter" aria-label="Add tag" size="14"><button id="prev">Prev</button><button id="next">Next</button><button id="x">Close</button></div></div>
<script>
const $=s=>document.querySelector(s),esc=s=>String(s).replace(/[&<>"']/g,c=>'&#'+c.charCodeAt(0)+';');
let S={zoom:1,hover:1.02,pull:0,shadows:true,vfocus:.5,labels:true,spread:1,sway:1,theme:'dark',accent:'#c9a7ff',blur:false,preview:'all',cap:6,kind:'all'};
try{Object.assign(S,JSON.parse(localStorage.getItem('mg')||'{}'))}catch{}
if(!['all','image','gif','video'].includes(S.kind))S.kind='all';
if(!(S.zoom>=.25&&S.zoom<=2))S.zoom=1;if(!(S.hover>=1&&S.hover<=3))S.hover=1.02;if(!(S.vfocus>=0&&S.vfocus<=1))S.vfocus=.5;if(!(S.pull>=0&&S.pull<=1))S.pull=0;if(typeof S.labels!='boolean')S.labels=true;if(typeof S.shadows!='boolean')S.shadows=true;if(!(S.spread>=.75&&S.spread<=4))S.spread=1;if(![3,6,10,16,30].includes(+S.cap))S.cap=6;if(![0,1,2.5].includes(+S.sway))S.sway=1;
const save=()=>{try{localStorage.setItem('mg',JSON.stringify(S))}catch{}};
let items=[],view=[],root='',cur=-1,dirty=false,tagF=new Set(),pos=[],W=0,H=0,cx=0,cy=0,tx=0,ty=0,fx=.5,fy=.5,lastMove=-1e9,snap=true,lc={x:1e9,y:1e9},rt,qt,rz;
const live=new Map(),world=$('#world');
function applyS(){const r=document.documentElement;r.dataset.theme=S.theme;r.style.setProperty('--ac',S.accent);r.style.setProperty('--sw',S.sway);r.style.setProperty('--hs',S.hover);
 document.body.classList.toggle('blur',S.blur);document.body.classList.toggle('nolabels',!S.labels);document.body.classList.toggle('nosway',!(S.sway>0));document.body.classList.toggle('noshadow',!S.shadows);$('#s_shadows').checked=S.shadows;$('#s_labels').checked=S.labels;$('#s_pull').value=Math.round(S.pull*100);$('#s_vf').value=Math.round(S.vfocus*100);$('#s_zoom').value=S.zoom*100;$('#s_spread').value=S.spread*100;$('#s_hover').value=Math.round(S.hover*100);$('#s_sway').value=String(S.sway);$('#s_theme').value=S.theme;$('#s_ac').value=S.accent;
 $('#s_blur').checked=S.blur;$('#s_prev').value=S.preview;$('#s_cap').value=S.cap;r.style.setProperty('--hh',$('header').offsetHeight+'px')}
function set(k,v){S[k]=v;save();applyS()}
const bind=(id,ev,fn)=>{$(id)['on'+ev]=e=>fn(e.target)};
bind('#s_zoom','input',t=>{set('zoom',t.value/100);relayout(true)});bind('#s_spread','input',t=>{set('spread',t.value/100);relayout(true)});bind('#s_hover','input',t=>set('hover',t.value/100));bind('#s_labels','change',t=>set('labels',t.checked));bind('#s_shadows','change',t=>set('shadows',t.checked));bind('#s_pull','input',t=>{set('pull',t.value/100);if(hov)pullTo(hov)});bind('#s_vf','input',t=>{set('vfocus',t.value/100);sync()});bind('#s_sway','change',t=>set('sway',+t.value));bind('#s_theme','change',t=>set('theme',t.value));
bind('#s_ac','input',t=>set('accent',t.value));bind('#s_blur','change',t=>set('blur',t.checked));bind('#s_prev','change',t=>{set('preview',t.value);sync()});bind('#s_cap','change',t=>{set('cap',+t.value);sync()});
$('#gear').onclick=()=>$('#set').classList.toggle('open');$('#re').onclick=load;$('#q').oninput=()=>{clearTimeout(qt);qt=setTimeout(render,220)};
function hide(on){document.body.classList.toggle('hide',on);if(on){$('#set').classList.remove('open');const h=$('#hint');h.classList.add('show');setTimeout(()=>h.classList.remove('show'),3200)}}
$('#hide').onclick=()=>hide(true);$('#dot').onclick=()=>hide(false);
const h32=s=>{let h=2166136261;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,16777619)}return h>>>0};
const rng=a=>()=>{a|=0;a=a+0x6D2B79F5|0;let t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296};
function strip(){const kd=[['all','All'],['image','Images'],['gif','GIFs'],['video','Videos']];
 $('#kinds').innerHTML=kd.map(([k,l])=>`<button data-k="${k}" class="${S.kind==k?'on':''}">${l}</button>`).join('');
 const base=items.filter(i=>S.kind=='all'||i.kind==S.kind),cnt={};base.forEach(i=>i.tags.forEach(t=>cnt[t]=(cnt[t]||0)+1));[...tagF].forEach(t=>{if(!cnt[t])tagF.delete(t)});
 const top=Object.entries(cnt).sort((a,b)=>b[1]-a[1]||a[0].localeCompare(b[0])).slice(0,60);
 $('#tags').innerHTML=top.map(([t,n])=>`<button data-t="${esc(t)}" class="${tagF.has(t)?'on':''}">${esc(t)} ${n}</button>`).join('');
 const q=$('#q').value.trim().toLowerCase();
 const nv=base.filter(i=>[...tagF].every(t=>i.tags.includes(t))&&(!q||(i.name+' '+i.tags.join(' ')).toLowerCase().includes(q)));
 $('#cnt').textContent=nv.length+(nv.length==1?' item':' items');document.documentElement.style.setProperty('--hh',$('header').offsetHeight+'px');return nv}
function render(first){const nv=strip(),em=$('#empty');em.hidden=nv.length>0;
 em.textContent=items.length?'Nothing matches these filters.':'No media yet. Put images, GIFs and videos in '+root+' (subfolders become tags), then press Rescan.';relayout(!first,nv)}
function relayout(fade,nv){clearTimeout(rt);const go=()=>{if(nv)view=nv;rebuild()};if(fade){world.classList.add('out');rt=setTimeout(()=>{go();world.classList.remove('out')},230)}else go()}
function rebuild(){live.forEach(drop);live.clear();world.innerHTML='';layout();snap=true;cull()}
const TILEH=340;/* every item is this tall; width follows the item's native aspect ratio */
function dsz(w,h){if(!(w>0&&h>0))return{w:TILEH,h:TILEH};return{w:Math.max(1,Math.round(TILEH*w/h)),h:TILEH}}
function layout(){const z=S.zoom,vw=innerWidth,vh=innerHeight,GAP=44;
 const L=view.map((it,i)=>({i,...dsz(it.w||(it.kind=='video'?16:1),it.h||(it.kind=='video'?9:1))}));pos=new Array(L.length);
 let A=0,mw=0;L.forEach(t=>{A+=(t.w+GAP)*(t.h+GAP);mw=Math.max(mw,t.w)});const K=S.spread;A*=1.8*K*K;
 W=Math.max(vw/z*1.5*K,Math.sqrt(A*1.6),mw+2*GAP);H=Math.max(vh/z*1.5*K,A/W);
 const R=rng(L.length*7919+1),cell=320,grid=new Map();
 const cells=(x,y,w,h,f)=>{for(let a=Math.floor(x/cell);a<=Math.floor((x+w)/cell);a++)for(let b=Math.floor(y/cell);b<=Math.floor((y+h)/cell);b++)f(a*65537+b)};
 const hit=(x,y,w,h)=>{let bad=false;cells(x-GAP,y-GAP,w+2*GAP,h+2*GAP,k=>{if(bad)return;const l=grid.get(k);if(l)for(const q of l)if(x<q.x+q.w+GAP&&x+w+GAP>q.x&&y<q.y+q.h+GAP&&y+h+GAP>q.y){bad=true;break}});return bad};
 L.slice().sort((a,b)=>b.w*b.h-a.w*a.h).forEach(t=>{let x,y,ok=false;
  for(let k=0;k<60&&!ok;k++){x=GAP+R()*Math.max(0,W-t.w-2*GAP);y=GAP+R()*Math.max(0,H-t.h-2*GAP);if(y+t.h+GAP<=H&&!hit(x,y,t.w,t.h))ok=true}
  if(!ok){x=GAP+R()*Math.max(0,W-t.w-2*GAP);y=H+GAP;H+=t.h+2*GAP}
  const p={x,y,w:t.w,h:t.h};pos[t.i]=p;cells(x,y,t.w,t.h,k=>{if(!grid.has(k))grid.set(k,[]);grid.get(k).push(p)})});
 world.style.width=W+'px';world.style.height=H+'px'}
function mk(i){const it=view[i],p=pos[i],h=h32(it.id),m=document.createElement('div');m.className='m';m.dataset.i=i;m.style.transform=`translate3d(${p.x}px,${p.y}px,0)`;
 m.innerHTML=`<div class="f" style="width:${p.w}px;height:${p.h}px;--dx:${h%17-8}px;--dy:${(h>>>5)%17-8}px;--rt:${((h>>>9)%9-4)/10}deg;--du:${9+(h>>>13)%9}s;--de:-${(h>>>3)%10}s"><div class="g">`
 +(it.kind=='video'?`<video src="${it.url}" muted loop playsinline preload="metadata" disablepictureinpicture></video>`:`<img src="${it.th||it.url}" decoding="async" alt="">`)+`<span>${esc(it.name)}</span></div></div>`;
 world.appendChild(m);return m}
let hov=null;const ptr={x:0,y:0,has:false};let vdirty=false,lastSync=0;
/* Hover pull: the target is captured ONCE when the pointer enters a tile (where the screen centre is at that moment) and then stays fixed,
   so the tile glides there and stays put, even if the wall pans afterwards. The unmoved .f remains the hover hitbox; only .g moves. */
function pullTo(m){const f=m.firstChild,p=pos[+m.dataset.i];if(!f||!p)return;const z=S.zoom,k=cur<0?S.pull:0;
 const dx=k?Math.round((innerWidth/2-((p.x+p.w/2)*z-cx))*k/z):0,dy=k?Math.round((innerHeight/2-((p.y+p.h/2)*z-cy))*k/z):0;
 f.style.setProperty('--hx',dx+'px');f.style.setProperty('--hy',dy+'px')}
function unpull(m){const f=m&&m.firstChild;if(f){f.style.removeProperty('--hx');f.style.removeProperty('--hy')}}
world.addEventListener('mouseover',e=>{const m=e.target.closest&&e.target.closest('.m');if(m&&m!==hov){unpull(hov);hov=m;pullTo(m)}});
world.addEventListener('mouseout',e=>{const m=e.target.closest&&e.target.closest('.m');if(m&&m===hov&&!(e.relatedTarget&&m.contains(e.relatedTarget))){unpull(m);hov=null}});
function drop(m){if(m===hov)hov=null;const v=m.querySelector('video');if(v){v.pause();v.removeAttribute('src');v.load()}m.remove()}
function cull(){const z=S.zoom,mg=500,x0=(cx-mg)/z,y0=(cy-mg)/z,x1=(cx+innerWidth+mg)/z,y1=(cy+innerHeight+mg)/z;
 for(let i=0;i<pos.length;i++){const p=pos[i],v=p.x<x1&&p.x+p.w>x0&&p.y<y1&&p.y+p.h>y0;
  if(v&&!live.has(i))live.set(i,mk(i));else if(!v&&live.has(i)){drop(live.get(i));live.delete(i)}}
 lc={x:cx,y:cy};sync()}
function sync(){vdirty=false;lastSync=performance.now();const z=S.zoom,on=S.preview=='all'&&cur<0&&!document.hidden,x0=cx/z,y0=cy/z,x1=(cx+innerWidth)/z,y1=(cy+innerHeight)/z,fw=ptr.has?S.vfocus:0,mx=(cx+innerWidth/2*(1-fw)+ptr.x*fw)/z,my=(cy+innerHeight/2*(1-fw)+ptr.y*fw)/z,vs=[];
 live.forEach((m,i)=>{const v=m.querySelector('video');if(!v)return;const p=pos[i];
  vs.push({m,v,inV:p.x<x1&&p.x+p.w>x0&&p.y<y1&&p.y+p.h>y0,d:Math.hypot(p.x+p.w/2-mx,p.y+p.h/2-my)})});
 vs.sort((a,b)=>a.d-b.d);let n=0;
 vs.forEach(o=>{if(on&&o.inV&&n<S.cap){n++;o.v.muted=true;o.v.play().catch(()=>{})}
  else if(S.preview!='hover'||cur>=0||!o.m.matches(':hover'))o.v.pause()})}
let last=0;function frame(t){requestAnimationFrame(frame);const dt=Math.min(100,t-last||16);last=t;if(!W)return;
 const z=S.zoom,vw=innerWidth,vh=innerHeight,Ws=W*z,Hs=H*z;let a=fx,b=fy;
 if(t-lastMove>7000){a=.5+.4*Math.sin(t/23000);b=.5+.4*Math.cos(t/31000)}
 tx=Ws>vw?a*(Ws-vw):(Ws-vw)/2;ty=Hs>vh?b*(Hs-vh):(Hs-vh)/2;
 if(snap){cx=tx;cy=ty;snap=false}else{const k=1-Math.exp(-dt/280);cx+=(tx-cx)*k;cy+=(ty-cy)*k}
 world.style.transform=`translate3d(${-cx}px,${-cy}px,0) scale(${z})`;if(vdirty&&S.vfocus>0&&t-lastSync>250)sync();if(Math.abs(cx-lc.x)+Math.abs(cy-lc.y)>90)cull()}
addEventListener('pointermove',e=>{if(e.target.closest&&e.target.closest('header,#set,#lb'))return;
 fx=Math.min(1,Math.max(0,(e.clientX/innerWidth-.08)/.84));fy=Math.min(1,Math.max(0,(e.clientY/innerHeight-.08)/.84));ptr.x=e.clientX;ptr.y=e.clientY;ptr.has=true;vdirty=true;lastMove=performance.now()});
addEventListener('resize',()=>{clearTimeout(rz);rz=setTimeout(()=>relayout(true),300)});document.addEventListener('visibilitychange',sync);
function fit(m){const f=m.closest('.f');if(!f)return;const i=+f.parentNode.dataset.i,it=view[i],p=pos[i],w=m.naturalWidth||m.videoWidth,h=m.naturalHeight||m.videoHeight;
 if(it&&p&&w&&h&&(it.w!=w||it.h!=h)){it.w=w;it.h=h;const d=dsz(w,h);p.w=d.w;p.h=d.h;f.style.width=d.w+'px';f.style.height=d.h+'px'}}
['load','loadeddata','loadedmetadata'].forEach(ev=>world.addEventListener(ev,e=>{e.target.classList.add('ld');fit(e.target);if(e.target.tagName=='VIDEO')sync()},true));
world.addEventListener('error',e=>e.target.classList?.add('bad'),true);
world.addEventListener('mouseenter',e=>{if(S.preview!='hover'||cur>=0||!e.target.classList?.contains('m'))return;const v=e.target.querySelector('video');if(v){v.muted=true;v.play().catch(()=>{})}},true);
world.addEventListener('mouseleave',e=>{if(S.preview=='hover'&&e.target.classList?.contains('m')){const v=e.target.querySelector('video');if(v)v.pause()}},true);
world.onclick=e=>{const m=e.target.closest('.m');if(m)open(+m.dataset.i)};
$('#kinds').onclick=e=>{const k=e.target.dataset.k;if(k){set('kind',k);render()}};
$('#tags').onclick=e=>{const t=e.target.dataset.t;if(t){tagF.has(t)?tagF.delete(t):tagF.add(t);render()}};
function meta(){const it=view[cur];$('#nm').textContent=it.name;$('#chips').innerHTML=it.tags.map(t=>`<button class="chip" data-t="${esc(t)}" title="Remove tag">${esc(t)} ×</button>`).join('')}
function open(i){if(i<0||i>=view.length)return;cur=i;const it=view[i];$('#lb').classList.add('open');sync();
 $('#stage').innerHTML=it.kind=='video'?`<video src="${it.url}" controls controlsList="nodownload" autoplay loop></video>`:`<img src="${it.url}" alt="${esc(it.name)}">`;
 const vd=$('#stage video');if(vd)vd.onerror=async()=>{let c='';try{c=(await(await fetch('/api/codec?id='+encodeURIComponent(it.id))).json()).codec||''}catch{}
  $('#stage').insertAdjacentHTML('beforeend','<p style="color:#bbb;max-width:60ch">This browser can\'t play this video'+(c?' (codec: '+c+')':'')+'. Browsers play H.264 mp4 and VP9 webm reliably. Convert with: ffmpeg -i in.mov -c:v libx264 -c:a aac out.mp4</p>')};
 meta()}
function close(){cur=-1;$('#lb').classList.remove('open');$('#stage').innerHTML='';if(dirty){dirty=false;strip()}sync()}
$('#stage').onclick=e=>{if(e.target.id=='stage')close()};
async function edit(body){const it=view[cur];try{const r=await fetch('/api/edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:it.id,...body})});
 if(!r.ok)throw 0;Object.assign(it,await r.json());dirty=true;meta()}catch{alert('Could not save. Is the server still running?')}}
$('#chips').onclick=e=>{const t=e.target.dataset.t;if(t)edit({remove:[t]})};
$('#add').onkeydown=e=>{e.stopPropagation();if(e.key=='Enter'&&e.target.value.trim()){const v=e.target.value.split(',');e.target.value='';edit({add:v})}};
$('#prev').onclick=()=>open(cur-1);$('#next').onclick=()=>open(cur+1);$('#x').onclick=close;
document.onkeydown=e=>{if(e.target.tagName=='INPUT')return;
 if(cur>=0){if(e.key=='Escape')close();else if(e.key=='ArrowLeft')open(cur-1);else if(e.key=='ArrowRight')open(cur+1)}
 else if(e.key=='h'||e.key=='H')hide(!document.body.classList.contains('hide'));else if(e.key=='Escape')hide(false)};
/* The server can't read some sizes (webm, avif...): measure those in the browser BEFORE layout so tiles never resize/overlap later. */
function probe(it){return new Promise(ok=>{const v=it.kind=='video',e=document.createElement(v?'video':'img');let t;
 const end=()=>{clearTimeout(t);if(v){e.removeAttribute('src');e.load()}else e.src='';ok()};
 e.onload=e.onloadedmetadata=()=>{const w=e.naturalWidth||e.videoWidth,h=e.naturalHeight||e.videoHeight;if(w&&h){it.w=w;it.h=h}end()};e.onerror=end;t=setTimeout(end,4000);
 if(v)e.preload='metadata';e.src=it.url})}
async function measure(list){const q=list.filter(i=>!(i.w>0&&i.h>0));let n=0;
 await Promise.race([Promise.all(Array.from({length:Math.min(8,q.length)},async()=>{while(n<q.length)await probe(q[n++])})),new Promise(r=>setTimeout(r,6000))])}
async function load(){try{const r=await fetch('/api/items');const d=await r.json();items=d.items;root=d.root;await measure(items)}catch{items=[];root='the media folder'}render(!W)}
applyS();requestAnimationFrame(frame);load();
</script></body></html>"""


class Srv(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], ConnectionError): super().handle_error(request, client_address)


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--media", default=str(HERE / "media")); ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-thumbs", action="store_true", help="don't generate wall thumbnails (default: on when Pillow is installed)")
    a = ap.parse_args()
    global USE_THUMBS
    if a.no_thumbs: USE_THUMBS = False
    ROOT = Path(a.media).expanduser().resolve(); ROOT.mkdir(parents=True, exist_ok=True)
    srv = Srv(("127.0.0.1", a.port), H)
    print(f"Gallery: http://127.0.0.1:{a.port}  (media: {ROOT})")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == "__main__":
    main()
