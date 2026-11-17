# Project: Pornographic Media Gallery
## What it is
A smooth minimalistic but efficient and clean pornographic media gallery for videos gifs images, also has customizability. Also automatically tags media, to organize it.
## How to run/test
- Run: `python3 gallery.py [--media DIR] [--port N]` (defaults: `./media`, port 8765). Open http://127.0.0.1:8765. Python 3.8+ stdlib only, no installs.
- Drop files in `media/` (subfolders are fine), press Rescan in the UI after adding files.
- Test: run against a folder of sample files and hit `/api/items`, `/f/<path>` (with Range headers) and `POST /api/edit` with curl.
## Works now
- Single file `gallery.py`: local server (127.0.0.1 only) with the UI embedded.
- Main view is a floating wall: all media at its native aspect ratio (all items the same height, 340px; width follows each item's aspect ratio) scattered on a large canvas (no overlaps, 44px gaps), each item swaying slowly. Mouse position pans across the whole canvas with easing; wall drifts on its own until the mouse moves. Only items near the viewport are in the DOM (culling).
- Hide (button, or H key; Esc brings it back) hides everything but the media. On touch devices a faint dot top-left restores controls.
- Click an item for a lightbox (arrows, Esc, click outside): tag add/remove.
- Scans images (jpg png webp avif bmp), GIFs and videos (mp4 webm mov m4v); hidden files/folders skipped. Native sizes read from file headers (png gif jpeg webp bmp mp4/mov incl. rotation); others are sized from the browser once loaded.
- Auto tags: type, orientation, month added, words from folder/file names. Manual tag add/remove (removing an auto tag hides it), saved in `gallery_data.json` (git-ignored). Search, type filter, multi-tag filter.
- Videos play muted in view, nearest to screen centre first, up to a cap (3-30, default 6), or on hover, or off; off-screen ones are unloaded.
- Settings (browser localStorage): zoom 25-200%, hover grow 100-300% (default 102%), hover pull 0-100% (on hover the tile glides once toward where the screen centre is AT THAT MOMENT and stays there; target captured in pullTo() on mouseover, not tracked; 0=off; the unmoved .f is the ONLY hitbox (hover, click, video hover-preview); the moving/growing .g has pointer-events:none so it never blocks or triggers anything; eased by a CSS transition on the `translate` property. A per-frame JS follow version was tried and rejected as not wanted), video focus 0-100% (which videos play when capped: nearest screen centre at 0%, nearest the mouse at 100%; default 50%), show names on/off, spread 75-400% (scene size: tiles keep their size, gaps grow; mouse maps across the whole scene so it travels further), sway off/gentle/strong, theme, accent, blur until hover, preview mode, playing cap.
- Videos served from extension-less `/v/<hash>` URLs as `application/octet-stream` (aimed at IDM-style download managers; unverified). Range responses capped at 2 MB. Fixed MIME table for images, path traversal and host-header checks, codec message (`/api/codec`) if a video can't play.
## Known bugs
- Not tested in a real browser (only API via curl and page JS via jsdom): wall look/feel, sway performance, real playback and mobile are unverified.
- IDM avoidance (neutral URL/content type) is untested against IDM. Fallback: add 127.0.0.1 to IDM's download panel exceptions (Options > General > Customize IDM Download panels > Exceptions).
- Octet-stream video type is untested in Firefox/Safari.
- Items are normalised to the same display area, so the canvas size no longer depends on file resolution; tune `TILEH` in the JS if tiles feel too big/small (Zoom also scales).
- Items whose size the server can't read (webm, avif) are measured in the browser before layout (`measure()`, 4s per item, 6s total); any that time out still snap to real size on load and can overlap neighbours.
- Layout is random scatter: some wasted space/clusters; changes when the filtered set changes.
- Images load at full size; many large images in view can be heavy.
- Codec detection scans only the first/last 4 MB.
- mkv/avi and other formats browsers can't play are not supported.
- Rescan re-walks the folder each time; no file watcher.
## Decisions (do not undo without asking me)
- One file, `gallery.py`, stdlib only; UI embedded in it.
- Binds to 127.0.0.1 only; no accounts or auth.
- `media/` and `gallery_data.json` are git-ignored so media is never pushed.
- Tags are derived from folder/file names and metadata only, no ML classifier.
- UI settings live in browser localStorage; tag edits live in `gallery_data.json`.
- (User request) Main view is the floating wall at native ASPECT RATIO (not native resolution; changed on user's correction), mouse-panned. No favorites, no sorting, no tile shape/fit. Everything but the media can be hidden.
- (User request) Videos preview simultaneously, served via neutral `/v/` URLs.
## Next (ordered, each small enough for one session)
1. Test in a real browser (Windows, Chrome/Edge with IDM); tune sway, pan speed and layout density.
2. Generate cached downscaled previews (ffmpeg if present) so large libraries stay light, with native resolution on open.
3. Better layout: cluster by tag or fill gaps instead of pure random scatter.
4. Video duration/resolution tags via ffprobe when available.
## Last session
Fixed: wall now uses native aspect ratio at a uniform height (340px) instead of native pixel size (`dsz()` in layout/fit). Earlier: replaced grid with the floating wall (scatter layout, culling, mouse pan with easing, idle drift, CSS sway), added hide mode (H), removed favorites/sorting/shape/fit. Server: dimensions from webp/bmp/mp4/mov headers, favorites removed. Fixed a view/layout race found in testing and idle drift not starting. Verified with jsdom (no overlaps in 176 items, culling, pan mapping, play cap, hide, filters, zoom, lightbox, tags) and header-parse checks. No real browser or IDM available.

## Performance (measured, headless Chromium; relative numbers)
- Biggest costs on a heavy library, in order: decoding full-size photos for tiny tiles, per-tile sway animation + big soft shadow, playing videos.
- Wall tiles use cached thumbnails (`/t/<path>`, max 1280px, stored in `.thumbs/`, keyed by path+mtime+size) when Pillow is installed and the photo is bigger than 1280px; the lightbox always loads the original (`/f/...`). No Pillow = originals, nothing breaks. `--no-thumbs` disables. gif/avif/video are never thumbnailed. `.thumbs/` is never pruned automatically (safe to delete).
- Sway "Off" now removes the animation (`body.nosway`) instead of running it at zero amplitude. Settings has a Shadows checkbox (`body.noshadow`).

- Settings > "Run speed test..." (`bench()`): scripted fast sweeps under ~10 configs (header blur, sway, shadows, corners, animations, previews, media hidden, quarter of tiles, everything off), prints fps/p95/worst plus screen, DPR and GPU name, with a Copy button. Added because lag could not be reproduced in the (software-rendered) dev browser: per-version bisect showed no single feature caused it; fps dropped when tiles went from native resolution to uniform ~340px (more tiles/videos on screen).
- Video tiles are a poster `<img class="po">` (server: ffmpeg poster at `/p/<video hash>`, cached in `.thumbs/*.v.jpg`; if ffmpeg is missing or fails, the browser grabs one frame itself, 2 at a time, nearest first, cached per session). A real `<video>` is created only for tiles chosen to play (<= cap, nearest the video-focus point, with 2-slot hysteresis) and removed when no longer chosen (`startVid`/`stopVid` in `sync()`). Measured cause of the old lag: every live tile owned a `<video preload=metadata>`, so fast panning created/destroyed ~70 per 5s and fired `sync()` ~200 times.
- While the camera moves faster than `FAST` (350 px/s) `sync()` starts no new `<video>` elements (it only keeps/stops existing ones); once the camera has been calm for 200ms a settle-sync picks the real set. Measured: a 5s sweep created 52 video elements before this.
