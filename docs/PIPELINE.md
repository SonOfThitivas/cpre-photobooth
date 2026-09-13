# Photobooth → Google Drive → QR Pipeline

How this repo works end to end, how to run it, and what needs updating to move from the
last-known-good `photobooth-app-4.0.0` branch to the current `main` (photobooth-app 6.0.0).

## Overview

This repo is **two cooperating processes**, not one app:

1. **`photobooth.exe` / `photobooth-app`** — the upstream [photobooth-app](https://github.com/photobooth-app/photobooth-app)
   (pinned in `requirements.txt`). It drives the camera, runs the capture UI at
   `http://localhost:8000`, and writes finished images into its media directory. It is
   configured entirely by `photobooth-data/config/config.json` and `photobooth-data/userdata/`.
2. **`main.py`** — a small custom "uploader" glue script we own. It does **not** touch the
   camera. It watches the photobooth's output folder, and when a session's images land it
   creates a Drive folder, uploads them, makes them public, and shows a QR code to the shared
   folder.

```
┌─────────────────────┐     writes images      ┌──────────────────────────────┐
│  photobooth-app      │ ─────────────────────▶ │ photobooth-data/media/        │
│  (photobooth.exe)    │                         │   processed_full/   (watched) │
│  camera + web UI     │                         └──────────────┬───────────────┘
│  localhost:8000      │                                        │ watchdog FileCreatedEvent
└─────────────────────┘                                        ▼
                                              ┌──────────────────────────────────┐
                                              │ main.py (uploader)                 │
                                              │ 1. new session → create Drive      │
                                              │    folder under MAIN_FOLDER_ID     │
                                              │ 2. share_file() → "anyone: reader" │
                                              │ 3. upload each image (1.png, 2.png)│
                                              │ 4. qrcode of shared link → show    │
                                              └──────────────────────────────────┘
```

## The `main.py` state machine

`main.py` keeps two variables — `start` and `new_pics_paths` (filled by the watchdog handler) —
and polls every 50 ms:

- **First file of a session** (`start == False` and ≥1 new path): flip `start = True`, create a
  Drive folder named with the timestamp under `MAIN_FOLDER_ID`, make it world-readable via
  `share_file()`, and build the `shared_link`.
- **Session complete** (`start == True` and exactly **2** paths queued): upload each image in a
  separate `multiprocessing.Process`, reset `start = False`, spawn a process that renders and
  shows the QR code for `shared_link`.

> ⚠️ The completion trigger is the image count `PICS_PER_SESSION` (default **2**). It must equal
> the number of image files photobooth-app 6.0.0 drops into the watched folder per session. On
> `4.0.0` this was `5` and the watched path was `processed/full`. Both the count and the path are
> now `.env`-configurable (`PICS_PER_SESSION`, `WATCH_DIR`) — set them to match your installed
> 6.0.0 and verify against a real capture; a wrong count means sessions never fire or fire early.

## Prerequisites

- Latest stable **Python** (the upstream app targets 3.11+).
- **libjpeg-turbo** (Windows vc64 installer) and **ffmpeg** on `PATH` — required by photobooth-app.
- A **Google Cloud service account** with the Drive API enabled; download its JSON key.
- A Drive **parent folder** shared with the service account; note its folder ID.

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

Create `.env` in the repo root:

```.env
MAIN_FOLDER_ID={your google drive parent folder id}
SERVICE_ACCOUNT_FILE_PATH={path to your service-account JSON}
# Optional — couple the uploader to photobooth-app's output (defaults shown):
WATCH_DIR=./photobooth-data/media/processed_full
PICS_PER_SESSION=2
```

`WATCH_DIR` and `PICS_PER_SESSION` let you match `main.py` to your installed app version and
collage layout without editing code. Defaults equal the current `main` values.

Restore runtime assets that are **gitignored** and must exist locally (see `.gitignore`):
`photobooth-data/media`, `photobooth-data/userdata` (backgrounds/frames/fonts referenced by
`config.json`, e.g. `userdata/ieecon01.png`), `photobooth-data/database`, `photobooth-data/log`.

## Run

Two terminals, both inside the venv.

**Terminal 1 — the photobooth app**
```bash
.\venv\Scripts\activate
cd photobooth-data
photobooth.exe
```
Open `http://localhost:8000`.

**Terminal 2 — the uploader**
```bash
.\venv\Scripts\activate
py main.py
```

Take a collage capture in the web UI; `main.py` uploads the result and pops the QR code.

## What changed 4.0.0 → 6.0.0 (and what to verify)

Code (already committed on `main`):

| Thing | 4.0.0 | main (6.0.0) | Action |
| --- | --- | --- | --- |
| Watched folder | `photobooth-data/media/processed/full` | `processed_full` (now `WATCH_DIR`, same default) | Confirm the real 6.0.0 output path. |
| Session image count | `== 5` | `== 2` (now `PICS_PER_SESSION`, default 2) | Must match images per collage/session. |
| `subprocess` import | present | removed | Fine — it was unused. |
| `img/` templates | tracked | deleted | Old ECE-Christmas assets; supply per-event assets under `userdata/`. |

## Packages to update / review

Known-good `4.0.0` vs current `main`:

| Package | 4.0.0 | main | Note |
| --- | --- | --- | --- |
| **photobooth-app** | 4.0.0 | **6.0.0** | Two major versions. Re-read upstream changelog for media-path, config-schema, and collage changes — this is the root cause of the `main.py` path/count edits. |
| **numpy** | 1.26.4 | **2.2.3** | Major 1→2 (C-ABI break). Every native dep (opencv, matplotlib, simplejpeg, PyTurboJPEG) must be numpy-2 compatible. |
| **watchdog** | 5.0.3 | 6.0.0 | `main.py` relies on `FileCreatedEvent` + `event_filter=`; both still supported, but smoke-test the observer. |
| **fastapi** | 0.115.0 | 0.115.11 | Minor; pulled by the app. |
| **opencv** | `opencv-python` 4.10.0.84 only | **resolved**: now only `opencv-python-headless` 4.11.0.86 | Previously three variants were pinned at once (`opencv-python` 4.10.0.84, `opencv-python-headless` 4.11.0.86, `opencv-contrib-python` 4.11.0.86), all providing the same `cv2` module and clobbering each other. photobooth-app 6.0.0 declares `opencv-python-headless>=4.10.0.84`, so that one is kept and the other two removed. |

New transitive deps introduced by photobooth-app 6.0.0 (install as-is; relevant only on real
hardware): `pyrealsense2`, `wigglecam`, `gpiozero`, `pyserial`, `python-statemachine`,
`SQLAlchemy`, `psutil`, `matplotlib`, `piexif`, `pilgram2`, `simplejpeg`, `PyTurboJPEG`,
`sse-starlette`, `pydantic-settings`.

> Note: `requirements.txt` was originally saved as **UTF-16**; it has been re-saved as UTF-8
> (LF endings) so `pip` parses it reliably.

### Our own code deps (not currently pinned explicitly in our script's intent)

`main.py` directly uses: `google-api-python-client`, `google-auth`, `watchdog`, `qrcode`,
`pillow`, `python-dotenv`, plus stdlib `multiprocessing`. All are present in `requirements.txt`.
