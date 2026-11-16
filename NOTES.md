# Project: Pornographic Media Gallery
## What it is
A smooth minimalistic but efficient and clean pornographic media gallery for videos gifs images, also has customizability. Also automatically tags media, to organize it.
## How to run/test
- Run: `python3 gallery.py [--media DIR] [--port N]` (defaults: `./media`, port 8765). Open http://127.0.0.1:8765. Python 3.8+ stdlib only, no installs.
- Drop files in `media/` (subfolders are fine), press Rescan in the UI after adding files.
- Test: run against a folder of sample files and hit `/api/items`, `/f/<path>` (with Range headers) and `POST /api/edit` with curl.
## Works now
- Single file `gallery.py`: local server (127.0.0.1 only) with the UI embedded.
- Scans images (jpg png webp avif bmp), GIFs and videos (mp4 webm mov m4v); hidden files/folders are skipped.
- Auto tags: type, orientation (png/gif/jpeg only), month added, plus words from folder and file names.
- Manual tag add/remove (removing an auto tag hides it), favorites. Saved in `gallery_data.json` (git-ignored).
- Masonry grid keeping natural proportions (or fixed square/wide/tall), fade-in loading, infinite scroll, sticky blurred header, search, type filter, multi-tag filter, sort, lightbox (arrows, Esc, F, click outside to close).
- Videos preview in the grid: all visible ones play muted at once (cap 4-60, default 8), or on hover, or off. Off-screen ones pause; lightbox pauses them.
- Customizing: tile size, layout, fit, dark/black/light theme, accent color, blur until hover, preview mode and cap. Saved in browser localStorage.
- Videos are served from extension-less `/v/<hash>` URLs as `application/octet-stream`, aimed at keeping IDM-style download managers from offering to download them. Unverified against IDM.
- Range responses are capped at 2 MB so many videos can stream without hogging browser connections.
- Fixed MIME table for images, path traversal and host-header checks, codec message (`/api/codec`) if a video can't play.
## Known bugs
- Not tested in a real browser (only API via curl and page JS via jsdom): layout, masonry, real playback, mobile are unverified.
- IDM avoidance (neutral URL/content type) is untested against IDM. Fallback: add 127.0.0.1 to IDM's download panel exceptions (Options > General > Customize IDM Download panels > Exceptions).
- Octet-stream video type is untested in Firefox/Safari.
- Grid loads full-size originals as thumbnails; large libraries will be slow.
- Video tile proportions start at 16:9 and snap to the real ratio once metadata loads (slight shift, rotated phone videos may differ until then).
- Orientation tag missing for videos and webp/avif/bmp.
- Codec detection scans only the first/last 4 MB; untested with moov-at-end files.
- mkv/avi and other formats browsers can't play are not supported.
- Rescan re-walks the folder each time; no file watcher.
## Decisions (do not undo without asking me)
- One file, `gallery.py`, stdlib only; UI embedded in it.
- Binds to 127.0.0.1 only; no accounts or auth.
- `media/` and `gallery_data.json` are git-ignored so media is never pushed.
- Tags are derived from folder/file names and metadata only, no ML classifier.
- UI settings live in browser localStorage; tag edits and favorites live in `gallery_data.json`.
- (Added at user's request) Videos are previewed simultaneously in the grid, served via neutral `/v/` URLs; layout is masonry with natural proportions by default.
## Next (ordered, each small enough for one session)
1. Test in a real browser (Windows, Chrome/Edge with IDM) and fix layout/playback issues.
2. Generate real thumbnails/short preview clips (ffmpeg if present, cached) so big libraries and many simultaneous videos stay light.
3. Video duration/resolution tags via ffprobe when available.
4. Bulk select and bulk tag/delete.
## Last session
Rebuilt UI: masonry with natural proportions, fade-in, blurred sticky header, animated settings/lightbox, simultaneous muted video previews with cap and off-screen pausing. Server: videos via `/v/<hash>` as octet-stream, 2 MB range chunks, grid teardown releases videos. Verified with curl (ranges, bytes, 404s, headers) and jsdom (render, filters, play cap, settings, lightbox, edits). No real browser or IDM available.