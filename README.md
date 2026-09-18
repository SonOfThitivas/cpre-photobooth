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

- **Python 3.11+** (photobooth-app 9 requires it).
- **photobooth-app 9.0.0** (installed via `requirements.txt`). No separate `ffmpeg` / `libjpeg-turbo` needed — v9 bundles them.
- A **Google Cloud project** with the **Drive API enabled**.
- An **OAuth client ID of type _Desktop app_** → download it as `client_secret.json`.
- The **OAuth consent screen Published** ("In production"), so the refresh token doesn't expire every 7 days. Add your account as a test user if you keep it in Testing.
- A **Drive folder** you own, to hold the session sub-folders → note its **folder ID** (from the URL).

## Setup

```bash
python -m venv venv
.\venv\Scripts\activate
python -m pip install -r requirements.txt
```

Put your **`client_secret.json`** (the Desktop OAuth client) in the repo root, then create **`.env`**:

```.env
MAIN_FOLDER_ID={your google drive folder id}
# Optional (defaults shown):
CLIENT_SECRET_FILE=client_secret.json
TOKEN_FILE=token.json
WATCH_DIR=./photobooth-data/media/processed_full
PICS_PER_SESSION=2
```

`token.json`, `client_secret.json`, and `.env` are **gitignored** — they are secrets. Never commit them.

### First run — authorize once

Run `main.py` once; a browser opens → sign in → **Allow**. That writes `token.json`. (If you see "Google hasn't verified this app", choose **Advanced → Go to … → Allow** — it's your own client.)

### Other booths

Copy `token.json` to each machine (same repo layout). They refresh silently — no browser needed.

## Run

Two terminals, both in the venv.

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

Take a collage capture; `main.py` creates the Drive sub-folder, uploads the images, and pops the QR to the shared folder.

## Tuning to your app version / collage

- `WATCH_DIR` — the folder photobooth-app writes finished images to (default `media/processed_full`, unchanged in v9).
- `PICS_PER_SESSION` — number of image files a session drops into that folder (2 individual + composite varies by collage). Set it to match a real capture; a wrong value means sessions never fire or fire early.

## Other branches

- `main` — earlier state (photobooth-app 6.0.0, service-account auth).
- `option-a-rclone-drive` — an experiment using photobooth-app 9's **native** Rclone synchronizer; abandoned due to upstream bugs (see that branch's `docs/PIPELINE-OPTION-A.md`).
- `photobooth-app-4.0.0` — the original Oct 2024 version.

## 📖 Reference

photobooth-app — [github](https://github.com/photobooth-app/photobooth-app.git) · [docs](https://photobooth-app.org/)
