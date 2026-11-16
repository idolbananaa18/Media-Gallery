# Project: Pornographic Media Gallery
## What it is
A smooth minimalistic but efficient and clean pornographic media gallery for videos gifs images, also has customizability. Also automatically tags media, to organize it.
## How to run/test
- Run: `python3 gallery.py [--media DIR] [--port N]` (defaults: `./media`, port 8765). Open http://127.0.0.1:8765. Python 3.8+ stdlib only, no installs.
- Drop files in `media/` (subfolders are fine), press Rescan in the UI after adding files.
- Test: run against a folder of sample files and hit `/api/items`, `/f/<path>` (with Range headers) and `POST /api/edit` with curl.
## Works now
- Single file `gallery.py`: local server (127.0.0.1 only) with the UI embedded.
- Main view is a floating wall: all media at its native aspect ratio (scaled to a common display area, ~340x340 px worth, so portrait/landscape/small/huge files look equally weighty) scattered on a large canvas (no overlaps, 44px gaps), each item swaying slowly. Mouse position pans across the whole canvas with easing; wall drifts on its own until the mouse moves. Only items near the viewport are in the DOM (culling).
- Hide (button, or H key; Esc brings it back) hides everything but the media. On touch devices a faint dot top-left restores controls.
- Click an item for a lightbox (arrows, Esc, click outside): tag add/remove.
- Scans images (jpg png webp avif bmp), GIFs and videos (mp4 webm mov m4v); hidden files/folders skipped. Native sizes read from file headers (png gif jpeg webp bmp mp4/mov incl. rotation); others are sized from the browser once loaded.
- Auto tags: type, orientation, month added, words from folder/file names. Manual tag add/remove (removing an auto tag hides it), saved in `gallery_data.json` (git-ignored). Search, type filter, multi-tag filter.
- Videos play muted in view, nearest to screen centre first, up to a cap (3-30, default 6), or on hover, or off; off-screen ones are unloaded.
- Settings (browser localStorage): zoom 25-150%, sway off/gentle/strong, theme, accent, blur until hover, preview mode, playing cap.
- Videos served from extension-less `/v/<hash>` URLs as `application/octet-stream` (aimed at IDM-style download managers; unverified). Range responses capped at 2 MB. Fixed MIME table for images, path traversal and host-header checks, codec message (`/api/codec`) if a video can't play.
## Known bugs
- Not tested in a real browser (only API via curl and page JS via jsdom): wall look/feel, sway performance, real playback and mobile are unverified.
- IDM avoidance (neutral URL/content type) is untested against IDM. Fallback: add 127.0.0.1 to IDM's download panel exceptions (Options > General > Customize IDM Download panels > Exceptions).
- Octet-stream video type is untested in Firefox/Safari.
- Items are normalised to the same display area, so the canvas size no longer depends on file resolution; tune `TILE` in the JS if tiles feel too big/small (Zoom also scales).
- Items whose size the server can't read (webm, avif) start as a placeholder size and snap to real size on load, so they can briefly overlap neighbours.
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
Fixed: wall now uses native aspect ratio at a uniform display area instead of native pixel size (`dsz()` in layout/fit). Earlier: replaced grid with the floating wall (scatter layout, culling, mouse pan with easing, idle drift, CSS sway), added hide mode (H), removed favorites/sorting/shape/fit. Server: dimensions from webp/bmp/mp4/mov headers, favorites removed. Fixed a view/layout race found in testing and idle drift not starting. Verified with jsdom (no overlaps in 176 items, culling, pan mapping, play cap, hide, filters, zoom, lightbox, tags) and header-parse checks. No real browser or IDM available.