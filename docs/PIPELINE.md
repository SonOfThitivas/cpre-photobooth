# Photobooth → Google Drive → QR Pipeline

How this repo works end to end, how to run it, and what changed moving from photobooth-app
6.0.0 to the current `main` (photobooth-app **9.0.0**).

## Overview

This repo is **two cooperating processes** plus a small plugin, not one app:

1. **`photobooth.exe` / `photobooth-app`** — the upstream [photobooth-app](https://github.com/photobooth-app/photobooth-app)
   (pinned in `booth/uv.lock`). It drives the camera, runs the capture UI at
   `http://localhost:8000`, and writes finished images into its media directory. It is
   configured entirely by `booth/photobooth-data/config/config.json` and `booth/photobooth-data/userdata/`.
2. **`main.py`** — the uploader we own (its modules are in `booth/uploader/`). It does **not**
   touch the camera. It follows photobooth-app's job over its `/api/sse` event stream, watches
   the output folder, gives each session one public Drive folder, uploads the session's images
   into it, and serves the folder link to photobooth-app.
3. **`drive_share` plugin** (`booth/photobooth-data/plugins/drive_share/`) — a photobooth-app
   user plugin with no Drive code. photobooth-app draws its own QR code for an item (in the
   presenter after a capture and in the gallery); the plugin supplies the link by asking `main.py`.

```
                    ┌──────────────────────── photobooth-app 9 (process 1) ────────────────────────┐
 guest ──▶ kiosk UI │ frontend ──GET /api/share/download/{id}──▶ share router                       │
   ▲     (shows QR) │                                              │ pluggy get_share_links(file, id) │
   │                │                                              ▼                                  │
   │                │                       plugins/drive_share/drive_share.py                        │
   │                │                       GET 127.0.0.1:8765/links?id=&file=  ≤3.5 s, [] on error   │
   │                │  /api/sse (ProcessStateinfo, DbInsert)      writes media/processed_full/*         │
   │                └──────┬──────────────────────────────────────────────▲──────────┬────────────────┘
   │                       │ SSE                                          │ HTTP     │ watchdog
   │                ┌──────▼──────────────── uploader (process 2, booth/main.py) ─────┴────────────────┐
   │                │ booth_events ──▶ sessions (state machine) ◀── new files                          │
   │                │                     │         │                                                 │
   │                │                     │         └──▶ links  {media id / file name → folder}       │
   │                │                     ▼               └─ link server, saved to links.json          │
   │                │                 folder_pool (1 spare folder, already created + shared)            │
   │                │                     │                                                            │
   │                │                 drive worker (one thread, jobs in order, retries) ────────────────┼──▶ Google Drive
   └── phone scans ─┴───────────────────────────────────────────────────────────────────────────────────┘   <timestamp>/
```

An optional third process, `gesture/gesture_trigger.py`, sits *in front of* photobooth-app: it
owns the webcam, feeds it through the OBS Virtual Camera, and starts sessions from a thumbs-up
via `/api/actions`. It doesn't touch this upload pipeline — see [GESTURE.md](GESTURE.md).

We deliberately do **not** use photobooth-app 9's built-in Rclone synchronizer / share links:
upstream bugs break the share-link QR (see the `option-a-rclone-drive` branch), and its links
point at single files, not one folder per session. Keep the synchronizer disabled (it is in the
committed config): with `enabled_share_links` on, its links would show next to ours.

## The uploader

`booth/main.py` only wires the modules together:

| Module | Job |
|---|---|
| `uploader/booth_events.py` | SSE client for `PHOTOBOOTH_URL/api/sse` (standard library). Forwards `ProcessStateinfo` (job state + `present_mediaitem_id`) and `DbInsert` (new gallery item id) to `sessions`. Reconnects every 2 s. |
| `uploader/sessions.py` | Pure state machine: which files and media ids belong to which session, and when a session ends. |
| `uploader/folder_pool.py` | Keeps one Drive folder named `_next session` created and shared in advance; reuses leftover ones at start. |
| `uploader/links.py` | Media id / file name → folder link. Serves `GET 127.0.0.1:LINK_SERVER_PORT/links?id=&file=` and saves ready links to `LINKS_FILE`. |
| `uploader/drive.py` | OAuth credentials, the Drive calls, and the **single worker thread** that runs every Drive call in order (`googleapiclient` isn't thread-safe). |

### One session

```
job start/counting ─▶ take the spare folder (renamed to the timestamp) → link known; a new spare is made
new file (watchdog) ─▶ added to the session; its file name → folder
DbInsert / present  ─▶ media id → folder (the collage, the presented item)
present             ─▶ frontend asks the plugin → plugin asks the uploader → link at once → QR on screen
finished            ─▶ session closed; its files are queued for upload into the folder
empty payload       ─▶ failed/cancelled job: files (if any) are uploaded anyway; an empty session
                       gives its folder back to the pool
```

Why it works this way (checked in photobooth-app 9.0.0's code):

- The frontend asks for an item's links **once** (`/api/share/download/{id}`, 600 ms after it
  shows the item) and never again, even after `[]`. So the link must exist by then: that is why
  folders are created in advance, and why the link server may wait up to `LINK_WAIT_SECONDS`.
- Job order: `start → counting → capture (→ approval) → completed`, then the results are added
  to the database (`DbInsert`), then `present`, then `finished`. Every file is on disk before
  `present`, so uploading at `finished` never reads a half-written file.
- `DbInsert` is sent only for gallery items, not for a collage's individual shots, so the files
  of a session still come from watchdog; SSE supplies the start, the end and the media ids.

**Fallback — quiet window.** Files that arrive while no job is followed (stream down, or a file
outside a job) form a *file session*: it opens on the first file and closes once no new file has
arrived for `SESSION_QUIET_SECONDS`, the uploader's original behaviour. A job session whose
stream drops mid-job ends the same way; if the stream comes back during the job, the session
carries on. Links are looked up by file name too, so the in-app QR still works in this mode.

**When Drive or the network fails:**

- Every Drive request retries up to `DRIVE_RETRIES` (4) times with googleapiclient's
  exponential backoff (~2, 4, 8, 16 s); the `Sleeping … before retry` warnings show in the log.
- If a spare folder can't be created, the next session creates its own; if that fails too, the
  upload job tries once more before giving up. Then the session is logged as `NOT uploaded`
  with its file list, the photos stay in `WATCH_DIR`, and the item shows no QR (never a link to
  a folder guests can't open).
- A failed file upload doesn't stop the rest; the log ends with `uploaded n of m file(s)`.
- If the uploader isn't running, the plugin returns no link and the item simply has no QR; the
  booth UI never waits more than ~3.5 s for it.
- Connections go **IPv4 first**: on networks with broken IPv6, httplib2 gave up on the first
  (IPv6) address that timed out, so every Drive call failed with `WinError 10060` after ~21 s
  while browsers worked. IPv6 remains the fallback.
- Ctrl+C lets queued uploads finish first (Ctrl+C again quits at once).

### Tests

```bash
cd booth
uv run --with pytest python -m pytest tests
```

They cover the session state machine, the folder pool, the link store and server, the SSE
parsing, and the plugin against a real link server. They don't touch Drive.

## Prerequisites

- **uv**. `booth/.python-version` pins **Python 3.14** (the verified build); the lock needs 3.12+.
- No separate **libjpeg-turbo** or **ffmpeg** install — photobooth-app 9 bundles them.
- A **Google Cloud project** with the Drive API enabled and a **Desktop app OAuth client**
  (`client_secret.json`), with the consent screen **published**. See the README for details.
- A Drive **parent folder** you own; note its folder ID.

## Setup

photobooth-app and `main.py` live in the `booth/` uv project (`booth/.venv`); the gesture
trigger is a separate uv project in `gesture/`. Create the booth environment from its lock:

```bash
uv sync --locked --project booth
```

Put `client_secret.json` in `booth/`. Copy `.env.example` to `.env` in the repo root and fill it in
(`main.py` finds it by walking up from `booth/`; paths below are relative to `booth/`):

```.env
MAIN_FOLDER_ID={your google drive parent folder id}
# Optional (defaults shown):
CLIENT_SECRET_FILE=client_secret.json
TOKEN_FILE=token.json
WATCH_DIR=./photobooth-data/media/processed_full
SESSION_QUIET_SECONDS=3
PHOTOBOOTH_URL=http://localhost:8000
LINK_SERVER_PORT=8765
LINK_WAIT_SECONDS=2
LINKS_FILE=links.json
```

photobooth-app's `uisettings.gallery_show_qrcode` must be on (it is in the committed
`config.json`) for the QR to show; `qrcode_text_above` / `qrcode_text_below` set its captions.

On the first run `main.py` opens a browser to authorize and writes `booth/token.json`. If Google
later rejects the saved token (`invalid_grant`), it falls back to the browser flow and
overwrites `token.json`.

Restore runtime assets that are **gitignored** and must exist locally (see `.gitignore`):
`booth/photobooth-data/media`, `booth/photobooth-data/userdata` (backgrounds/frames/fonts
referenced by `config.json`), `booth/photobooth-data/database`, `booth/photobooth-data/log`.

## Run

From the repo root, one command starts the gesture trigger, photobooth-app and `main.py` in
order and restarts any that crash (`--no-gesture` for a touch-only booth; see the README's **Run**):

```bash
uv run run.py
```

Open `http://localhost:8000`. Logs are in `logs/<service>.log`.

**By hand** — two terminals (plus the gesture trigger's, see [GESTURE.md](GESTURE.md)); no
activation needed. Nothing restarts a crashed service this way.

**Terminal 1 — the photobooth app**
```bash
cd booth/photobooth-data
uv run photobooth
```
Open `http://localhost:8000`.

**Terminal 2 — the uploader**
```bash
cd booth
uv run main.py
```

Take a collage capture in the web UI: the presenter shows the collage with the QR code to its
Drive folder, and `main.py` uploads the images.

## What changed 6.0.0 → 9.0.0

| Thing | 6.0.0 | main (9.0.0) | Note |
| --- | --- | --- | --- |
| Drive auth | Service account (`SERVICE_ACCOUNT_FILE_PATH`) | **OAuth** (`client_secret.json` → `token.json`) | A service account has no storage quota, so it can't upload to a free Gmail Drive. |
| Session trigger | `PICS_PER_SESSION` exact file count (default 2) | photobooth-app's job over `/api/sse` (start → `finished`); `SESSION_QUIET_SECONDS` quiet window as the fallback | No count to keep in sync with the collage layout. |
| Concurrency | `multiprocessing.Process` per upload + QR | One Drive worker thread runs every Drive call in order | The single Drive client is never used from two threads. |
| QR code | Image viewer window (`Image.show()`) | photobooth-app's own QR (presenter + gallery) via the `drive_share` plugin | Link ready at `present` thanks to a folder created in advance; no window to come up minimized. |
| Watched folder | `processed_full` | `processed_full` (`WATCH_DIR`) | Unchanged in v9. |
| Environment | `venv` + pip `requirements.txt` | uv project `booth/` → `booth/.venv` + `uv.lock` | Exact, locked installs with `uv sync --locked`. |
| libjpeg-turbo / ffmpeg | Manual install on `PATH` | Bundled | Nothing extra to install. |

## Packages

Key pins, 6.0.0 vs current `main` (`booth/uv.lock` pins every package; it was locked to
the previously verified pip freeze, so versions are unchanged):

| Package | 6.0.0 | main | Note |
| --- | --- | --- | --- |
| **photobooth-app** | 6.0.0 | **9.0.0** | Three major versions; requires Python 3.11+. |
| **numpy** | 2.2.3 | 2.5.3 | Still numpy 2. |
| **opencv-python-headless** | 4.11.0.86 | **5.0.0.93** | Major bump, pulled by the app. Keep only the headless variant — multiple opencv packages clobber the same `cv2` module. |
| **fastapi** | 0.115.11 | 0.141.1 | Pulled by the app. |
| **pillow** | 10.4.0 | 12.3.0 | Pulled by the app; no longer a direct dependency (the app draws the QR). |
| **simplejpeg** | 1.7.6 | 1.9.0 | Pulled by the app. |
| **PyTurboJPEG** | 1.7.7 | — | No longer needed (app bundles libjpeg-turbo). |
| **google-auth-oauthlib** | 1.2.1 | 1.4.1 | Used by `main.py` for the OAuth flow. |
| **watchdog** | 6.0.0 | 6.0.0 | `main.py` relies on `FileCreatedEvent` + `event_filter=`. |

New transitive deps from photobooth-app 9 include `av` (bundled ffmpeg) and `rclone-bin-api`
(the app's synchronizer, which our pipeline does not use).

### Our own code deps

`main.py` directly uses: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`,
`google-auth-httplib2`, `watchdog`, `python-dotenv`, plus the standard library (`threading`,
`urllib`, `http.server`). All are declared in `booth/pyproject.toml` and pinned in
`booth/uv.lock`. `qrcode` and `pillow` were dropped as direct dependencies. The `drive_share`
plugin uses only `pluggy` (from photobooth-app), the standard library and, if present,
`python-dotenv`.
