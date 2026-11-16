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
- Grid with search, type filter, tag filter (multi, AND), sort (newest/oldest/name/shuffle), lightbox with arrow keys, Esc, F to favorite.
- Customizing: tile size, tile shape, fill/whole fit, dark/black/light theme, accent color, blur until hover, video play on hover. Saved in browser localStorage.
- Range-request file serving (video seeking), path traversal and host-header checks.
- Files are served with a fixed MIME table (not the OS one) and `Content-Disposition: inline`; lightbox shows a message if a video can't be played.
## Known bugs
- Not tested in a real browser (only API via curl and page JS via jsdom): layout, hover play, lazy loading and mobile are unverified.
- Grid loads full-size originals as thumbnails; large libraries will be slow. Video tiles rely on the browser showing the first frame.
- Orientation tag missing for videos and webp/avif/bmp (no dimension parsing).
- Reported: browser prompted to download files. Attempted fix (fixed MIME + inline) is verified via headers only; cause not confirmed on the user's machine.
- mkv/avi and other formats browsers can't play are not supported.
- Rescan re-walks the folder each time; no file watcher.
## Decisions (do not undo without asking me)
- One file, `gallery.py`, stdlib only; UI embedded in it.
- Binds to 127.0.0.1 only; no accounts or auth.
- `media/` and `gallery_data.json` are git-ignored so media is never pushed.
- Tags are derived from folder/file names and metadata only, no ML classifier.
- UI settings live in browser localStorage; tag edits and favorites live in `gallery_data.json`.
## Next (ordered, each small enough for one session)
1. Test in a real browser and fix layout/hover issues.
2. Generate real thumbnails (ffmpeg if present, cached) for images, GIFs and videos.
3. Video duration/resolution tags via ffprobe when available.
4. Bulk select and bulk tag/delete.
## Last session
Fixed likely cause of download prompts: hard-coded MIME types for all supported extensions, `Content-Disposition: inline`, no reliance on system mimetypes. Added video error message in lightbox. Verified headers with curl (incl. uppercase .JPG) and jsdom UI test. Not confirmed in a real browser.
