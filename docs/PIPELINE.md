# Photobooth → Google Drive → QR Pipeline

How this repo works end to end, how to run it, and what changed moving from photobooth-app
6.0.0 to the current `main` (photobooth-app **9.0.0**).

## Overview

This repo is **two cooperating processes**, not one app:

1. **`photobooth.exe` / `photobooth-app`** — the upstream [photobooth-app](https://github.com/photobooth-app/photobooth-app)
   (pinned in `booth/uv.lock`). It drives the camera, runs the capture UI at
   `http://localhost:8000`, and writes finished images into its media directory. It is
   configured entirely by `booth/photobooth-data/config/config.json` and `booth/photobooth-data/userdata/`.
2. **`main.py`** — a small custom "uploader" glue script we own. It does **not** touch the
   camera. It watches the photobooth's output folder, and when a session's images land it
   creates a Drive folder, makes it public, uploads them, and shows a QR code to the shared
   folder.

```
┌─────────────────────┐     writes images      ┌──────────────────────────────┐
│  photobooth-app 9    │ ─────────────────────▶ │ photobooth-data/media/        │
│  (photobooth.exe)    │                         │   processed_full/   (watched) │
│  camera + web UI     │                         └──────────────┬───────────────┘
│  localhost:8000      │                                        │ watchdog FileCreatedEvent
└─────────────────────┘                                        ▼
                                              ┌──────────────────────────────────┐
                                              │ main.py (uploader, OAuth)          │
                                              │ 1. burst settles → create Drive    │
                                              │    folder under MAIN_FOLDER_ID     │
                                              │ 2. share_file() → "anyone: reader" │
                                              │ 3. thread: show QR of shared link  │
                                              │ 4. thread: upload the burst's files│
                                              └──────────────────────────────────┘
```

An optional third process, `gesture/gesture_trigger.py`, sits *in front of* photobooth-app: it
owns the webcam, feeds it through the OBS Virtual Camera, and starts sessions from a thumbs-up
via `/api/actions`. It doesn't touch this upload pipeline — see [GESTURE.md](GESTURE.md).

We deliberately do **not** use photobooth-app 9's built-in Rclone synchronizer / share links:
upstream bugs break the share-link QR (see the `option-a-rclone-drive` branch). Talking to the
Drive API directly keeps "one folder + one QR per session".

## The `main.py` session loop

The watchdog handler appends every new file to `new_pics_paths` and stamps `last_event_time`.
The main loop polls every 100 ms:

- **Still writing** (no paths yet, or the last file arrived less than `SESSION_QUIET_SECONDS`
  ago): keep waiting.
- **Burst has settled**: snapshot and clear `new_pics_paths`, create a timestamped Drive folder
  under `MAIN_FOLDER_ID`, make it world-readable via `share_file()`, and build the
  `shared_link`. Then two threads run concurrently:
  - **QR thread** — renders the QR for `shared_link` to `qr_code_folder/` and shows it at once
    (the link only needs the folder id, so guests don't wait for uploads).
  - **Upload thread** — uploads each file in the burst. The loop `join()`s it before the next
    session, so the single Drive client is never used from two threads at once.

> A collage session writes several files (each individual shot **plus** the composite) over a
> second or two. The quiet-window debounce groups them automatically, so there is no
> hard-coded per-session count to keep in sync with the collage definition. Raise
> `SESSION_QUIET_SECONDS` only if a slow machine uploads before the collage file lands.

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
```

On the first run `main.py` opens a browser to authorize and writes `booth/token.json`. If Google
later rejects the saved token (`invalid_grant`), it falls back to the browser flow and
overwrites `token.json`.

Restore runtime assets that are **gitignored** and must exist locally (see `.gitignore`):
`booth/photobooth-data/media`, `booth/photobooth-data/userdata` (backgrounds/frames/fonts
referenced by `config.json`), `booth/photobooth-data/database`, `booth/photobooth-data/log`.

## Run

Two terminals (plus the gesture trigger's, see [GESTURE.md](GESTURE.md)); no activation needed.

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

Take a collage capture in the web UI; `main.py` uploads the result and pops the QR code.

## What changed 6.0.0 → 9.0.0

| Thing | 6.0.0 | main (9.0.0) | Note |
| --- | --- | --- | --- |
| Drive auth | Service account (`SERVICE_ACCOUNT_FILE_PATH`) | **OAuth** (`client_secret.json` → `token.json`) | A service account has no storage quota, so it can't upload to a free Gmail Drive. |
| Session trigger | `PICS_PER_SESSION` exact file count (default 2) | `SESSION_QUIET_SECONDS` quiet-window debounce (default 3) | No count to keep in sync with the collage layout. |
| Concurrency | `multiprocessing.Process` per upload + QR | `threading.Thread` for QR + uploads, joined per session | QR shows immediately; one Drive client, never shared across sessions. |
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
| **pillow** | 10.4.0 | 12.3.0 | Used by `main.py` to show the QR. |
| **simplejpeg** | 1.7.6 | 1.9.0 | Pulled by the app. |
| **PyTurboJPEG** | 1.7.7 | — | No longer needed (app bundles libjpeg-turbo). |
| **google-auth-oauthlib** | 1.2.1 | 1.4.1 | Used by `main.py` for the OAuth flow. |
| **watchdog** | 6.0.0 | 6.0.0 | `main.py` relies on `FileCreatedEvent` + `event_filter=`. |

New transitive deps from photobooth-app 9 include `av` (bundled ffmpeg) and `rclone-bin-api`
(the app's synchronizer, which our pipeline does not use).

### Our own code deps

`main.py` directly uses: `google-api-python-client`, `google-auth`, `google-auth-oauthlib`,
`google-auth-httplib2`, `watchdog`, `qrcode`, `pillow`, `python-dotenv`, plus stdlib
`threading`. All are declared in `booth/pyproject.toml` and pinned in `booth/uv.lock`.
