<h1 align="center"><img src="https://raw.githubusercontent.com/photobooth-app/photobooth-app/main/assets/logo/logo-text-blue-transparent.png" alt="photobooth app logo" /></h1>

# 📸 Photobooth-app on Windows with Python 🐍

**Branch `legacy-oauth-v9`** — the original custom-uploader pipeline, updated for **photobooth-app 9.0.0** and switched from a Google **service account** to **OAuth** (so it works on a free Gmail account).

## How it works

Two processes run side by side:

1. **`photobooth.exe` / `photobooth-app` 9.0.0** — camera + capture UI at `http://localhost:8000`. On each session it writes finished images into `photobooth-data/media/processed_full/`.
2. **`main.py`** — watches that folder with `watchdog`. When a session's images land it:
   - creates a **new Google Drive sub-folder** (named with a timestamp) under `MAIN_FOLDER_ID`,
   - makes it **public** (anyone with the link: reader),
   - **uploads** the captures into it,
   - shows a **QR code** to the shared folder link.

This keeps the "one folder + one QR per session" experience, and by talking to the Google Drive API directly it avoids the rclone/native-sharing bugs in photobooth-app 9's synchronizer.

## Why OAuth instead of a service account

A **service account has no Drive storage quota**, so it cannot upload files to a personal (`@gmail.com`) Drive — uploads fail with `storageQuotaExceeded`. Uploading **as your own Google user (OAuth)** charges the files to your account's 15 GB, which works on free Gmail. A service account only works if you have Google **Workspace** + a **Shared Drive**.

`main.py` uses `token.json`, which is **portable**: authorize once (browser), then copy `token.json` to your other booths — they run headlessly with no browser step.

## Prerequisites

- **Python 3.11+** (photobooth-app 9 requires it). This repo's pinned build is **verified on Python 3.14** — check with `python --version`.
- **photobooth-app 9.0.0** (installed via `requirements.txt`). No separate `ffmpeg` / `libjpeg-turbo` needed — v9 bundles them.
- A **Google Cloud project** with the **Drive API enabled**.
- An **OAuth client ID of type _Desktop app_** → download it as `client_secret.json`.
- The **OAuth consent screen Published** ("In production"), so the refresh token doesn't expire every 7 days. Add your account as a test user if you keep it in Testing.
- A **Drive folder** you own, to hold the session sub-folders → note its **folder ID** (from the URL).

## Setup

Create the virtual environment (the repo standardises on **`.venv`**) and install everything:

```bash
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

`requirements.txt` is a **full pinned freeze** of a known-good environment, so that one
`pip install` reproduces a working booth. It installs **both** photobooth-app 9.0.0 (plus all
its dependencies — including the bundled `ffmpeg`/`libjpeg-turbo`, so nothing else is needed)
**and** the uploader's own direct dependencies (`google-api-python-client`,
`google-auth-oauthlib`, `google-auth-httplib2`, `watchdog`, `qrcode`, `pillow`,
`python-dotenv`). If you ever change dependencies, refresh the file with `pip freeze > requirements.txt`.

Put your **`client_secret.json`** (the Desktop OAuth client) in the repo root, then create **`.env`** — copy `.env.example` and fill in your folder id:

```bash
cp .env.example .env
```

```.env
MAIN_FOLDER_ID={your google drive folder id}
# Optional (defaults shown):
CLIENT_SECRET_FILE=client_secret.json
TOKEN_FILE=token.json
WATCH_DIR=./photobooth-data/media/processed_full
SESSION_QUIET_SECONDS=3
```

`token.json`, `client_secret.json`, and `.env` are **gitignored** — they are secrets. Never commit them.

### First run — authorize once

Run `main.py` once; a browser opens → sign in → **Allow**. That writes `token.json`. (If you see "Google hasn't verified this app", choose **Advanced → Go to … → Allow** — it's your own client.)

### Running on another machine (new booth)

A booth has **two** configs, with different portability:

**1. The uploader (`main.py`) — fully portable.** On the new machine, set up the venv (see [Setup](#setup)) and copy over just:

- `token.json` — makes the machine upload as *your* Google user with **no browser step** (it already contains the refresh token + client id/secret). All booths then upload into the same Drive folder.
- `.env` — `MAIN_FOLDER_ID` is shared across booths.

You do **not** need `client_secret.json` on the extra machines once `token.json` exists.

**2. The photobooth-app config (`photobooth-data/config/config.json`) — portable except the camera.** Copy your `config.json` to reuse all the creative settings (collage/merge definitions, canvas sizes, gallery, sharing, UI). Then fix the **one machine-specific field**:

- `cameras.group_backends[].backend_config.device_identifier` — this names the *specific* camera on your machine (e.g. `"Integrated Webcam"`). On a different machine it must match that machine's camera, or capture won't start. Set it in photobooth-app's admin UI (it lists the available cameras), or edit `config.json` directly.
- If the new machine uses a different camera *type* (Raspberry Pi cam, DSLR/gphoto2), also change `backend_type` accordingly.
- The `hardwareinputoutput.gpio_*` (Pi pins) and `misc.cmd_shutdown`/`cmd_reboot` (Linux commands) only matter on those platforms; leave them otherwise. Regenerating `misc.secret` per install is optional but tidy.

> `config.json`, `token.json`, `client_secret.json`, and `.env` are all **gitignored** — copy them between machines manually (USB / secure transfer), never via the public repo.

## Run

Two terminals, both in the venv.

**Terminal 1 — the photobooth app**
```bash
.\.venv\Scripts\activate
cd photobooth-data
photobooth.exe
```
Open `http://localhost:8000`. (`photobooth.exe` is installed into `.venv\Scripts` by the pip install; running it from inside `photobooth-data` keeps all its `config/`, `database/`, and `media/` folders there.)

**Terminal 2 — the uploader**
```bash
.\.venv\Scripts\activate
python main.py
```

Take a collage capture; `main.py` creates the Drive sub-folder, uploads the images, and pops the QR to the shared folder.

## Tuning to your app version / collage

- `WATCH_DIR` — the folder photobooth-app writes finished images to (default `media/processed_full`, unchanged in v9).
- `SESSION_QUIET_SECONDS` — how long the watcher waits with no new file before treating a burst as one finished session (default `3`). A collage session writes several files (each individual shot **plus** the composite) over a second or two; the debounce groups them automatically, so you no longer hard-code a per-session count. Raise it only if a slow machine uploads before the collage file lands.

## Other branches

- `main` — earlier state (photobooth-app 6.0.0, service-account auth).
- `option-a-rclone-drive` — an experiment using photobooth-app 9's **native** Rclone synchronizer; abandoned due to upstream bugs (see that branch's `docs/PIPELINE-OPTION-A.md`).
- `photobooth-app-4.0.0` — the original Oct 2024 version.

## 📖 Reference

photobooth-app — [github](https://github.com/photobooth-app/photobooth-app.git) · [docs](https://photobooth-app.org/)
