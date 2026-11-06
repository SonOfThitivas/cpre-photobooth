<h1 align="center"><img src="https://raw.githubusercontent.com/photobooth-app/photobooth-app/main/assets/logo/logo-text-blue-transparent.png" alt="photobooth app logo" /></h1>

# 📸 Photobooth-app on Windows with Python 🐍

The original custom-uploader pipeline on **photobooth-app 9.0.0**, uploading to Google Drive with **OAuth** (so it works on a free Gmail account).

## Repo layout

Two **independent uv projects** — each has its own `pyproject.toml`, `uv.lock` and `.venv`. They are deliberately *not* a uv workspace, so photobooth-app's `opencv-python-headless` and MediaPipe's `opencv-contrib-python` never share an environment.

```
cpre-photobooth/
├─ .env, .env.example     shared settings for both projects
├─ booth/                 uv project → booth/.venv
│  ├─ main.py             Drive uploader + QR
│  ├─ photobooth-data/    photobooth-app's config, userdata, media, database
│  └─ token.json, client_secret.json   (gitignored)
├─ gesture/               uv project → gesture/.venv
│  └─ gesture_trigger.py  thumbs-up capture trigger
├─ run.py                 starts all three services and keeps them running (stdlib only)
├─ stop.py                stops booth processes left over from an earlier start
└─ docs/
```

## How it works

Two processes run side by side in `booth/`:

1. **`photobooth-app` 9.0.0** — camera + capture UI at `http://localhost:8000`. On each session it writes finished images into `booth/photobooth-data/media/processed_full/`.
2. **`main.py`** — watches that folder with `watchdog`. When a session's images land it:
   - creates a **new Google Drive sub-folder** (named with a timestamp) under `MAIN_FOLDER_ID`,
   - makes it **public** (anyone with the link: reader),
   - **uploads** the captures into it,
   - shows a **QR code** to the shared folder link.

This keeps the "one folder + one QR per session" experience, and by talking to the Google Drive API directly it avoids the rclone/native-sharing bugs in photobooth-app 9's synchronizer.

A third process, **`gesture/gesture_trigger.py`**, makes the booth hands-free: hold a **thumbs-up** for 3 seconds to start a capture. It owns the webcam and feeds photobooth-app through the OBS Virtual Camera. See [docs/GESTURE.md](docs/GESTURE.md).

## Why OAuth instead of a service account

A **service account has no Drive storage quota**, so it cannot upload files to a personal (`@gmail.com`) Drive — uploads fail with `storageQuotaExceeded`. Uploading **as your own Google user (OAuth)** charges the files to your account's 15 GB, which works on free Gmail. A service account only works if you have Google **Workspace** + a **Shared Drive**.

`main.py` uses `token.json`, which is **portable**: authorize once (browser), then copy `token.json` to your other booths — they run headlessly with no browser step.

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** — installs Python and both projects' dependencies. `booth/.python-version` pins **Python 3.14** (the verified build); uv downloads it if it's missing.
- **photobooth-app 9.0.0** (installed by `uv sync`). No separate `ffmpeg` / `libjpeg-turbo` needed — v9 bundles them.
- **OBS Studio** for the gesture trigger's virtual camera — see [docs/GESTURE.md](docs/GESTURE.md).
- A **Google Cloud project** with the **Drive API enabled**.
- An **OAuth client ID of type _Desktop app_** → download it as `client_secret.json`.
- The **OAuth consent screen Published** ("In production"), so the refresh token doesn't expire every 7 days. Add your account as a test user if you keep it in Testing.
- A **Drive folder** you own, to hold the session sub-folders → note its **folder ID** (from the URL).

## Setup

Create both environments from their lockfiles:

```bash
uv sync --locked --project booth
uv sync --locked --project gesture
```

Each `uv.lock` pins every package version of a known-good booth, so `--locked` reproduces it exactly (and fails instead of silently upgrading). `booth/uv.lock` holds photobooth-app 9.0.0 with all its dependencies plus the uploader's (`google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `watchdog`, `qrcode`, `pillow`, `python-dotenv`).

Manage packages with uv only — `uv add` / `uv remove` / `uv lock --upgrade-package <name>` inside `booth/` or `gesture/` — never `pip install` into either `.venv`: `uv sync` makes an environment match its lock exactly and removes anything installed by hand.

Put your **`client_secret.json`** (the Desktop OAuth client) in **`booth/`**, then create **`.env`** in the repo root — copy `.env.example` and fill in your folder id:

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

Run `main.py` once; a browser opens → sign in → **Allow**. That writes `booth/token.json`. (If you see "Google hasn't verified this app", choose **Advanced → Go to … → Allow** — it's your own client.)

### Running on another machine (new booth)

A booth has **two** configs, with different portability:

**1. The uploader (`main.py`) — fully portable.** On the new machine, set up both projects (see [Setup](#setup)) and copy over just:

- `booth/token.json` — makes the machine upload as *your* Google user with **no browser step** (it already contains the refresh token + client id/secret). All booths then upload into the same Drive folder.
- `.env` — `MAIN_FOLDER_ID` is shared across booths.

You do **not** need `client_secret.json` on the extra machines once `token.json` exists.

**2. The photobooth-app config (`booth/photobooth-data/config/config.json`) — portable except the camera.** Copy your `config.json` to reuse all the creative settings (collage/merge definitions, canvas sizes, gallery, sharing, UI). Then fix the **one machine-specific field**:

- `cameras.group_backends[].backend_config.device_identifier` — this names the *specific* camera on your machine (e.g. `"Integrated Webcam"`). With the gesture trigger it is `"OBS Virtual Camera"` on every booth, and the machine-specific webcam name moves to `GESTURE_CAMERA` in `.env`. Without it, it must match that machine's camera, or capture won't start. Set it in photobooth-app's admin UI (it lists the available cameras), or edit `config.json` directly.
- If the new machine uses a different camera *type* (Raspberry Pi cam, DSLR/gphoto2), also change `backend_type` accordingly.
- The `hardwareinputoutput.gpio_*` (Pi pins) and `misc.cmd_shutdown`/`cmd_reboot` (Linux commands) only matter on those platforms; leave them otherwise. Regenerating `misc.secret` per install is optional but tidy.

> `config.json`, `token.json`, `client_secret.json`, and `.env` are all **gitignored** — copy them between machines manually (USB / secure transfer), never via the public repo.

## Run

From the repo root, one command starts all three services and keeps them running:

```bash
uv run run.py
```

Touch-only booth (no gesture trigger):

```bash
uv run run.py --no-gesture
```

Open `http://localhost:8000`, show a thumbs-up for 3 seconds (or press the on-screen button); `main.py` creates the Drive sub-folder, uploads the images, and pops the QR to the shared folder. **Ctrl+C** stops everything.

`run.py` is a small supervisor that uses only the standard library. It imports nothing from `booth/` or `gesture/`; it starts each service with `uv run` in its own folder, so each still runs in its own `.venv`. It:

1. **Checks before starting**, and refuses to start with a clear message if something is wrong: `uv` and both `.venv`s exist, `.env` has `MAIN_FOLDER_ID`, `booth/token.json` (or `client_secret.json`) exists, photobooth-app's camera matches the mode (`"OBS Virtual Camera"` with the gesture trigger, your real webcam with `--no-gesture`), the camera resolution in `.env` is valid, and nothing is already running on port 8000.
2. **Sets the camera resolution and frame rate from `.env`** (see below) in photobooth-app's `config.json`, and passes the same values to the gesture trigger.
3. **Starts in order, waiting until each one is really ready:** the gesture trigger (until it streams to the virtual camera), then photobooth-app (until `http://localhost:8000` answers), then the uploader.
4. **Restarts any service that exits**, waiting 1 s, 2 s, 4 s … up to 60 s between tries, so a permanently broken one doesn't spin. photobooth-app reads the gesture trigger's virtual camera, so while the gesture trigger is down photobooth-app is stopped too, and started again once the camera streams.
5. **Logs** every line to the console, prefixed `[gesture]` / `[photobooth]` / `[uploader]`, and to `logs/<service>.log` (gitignored). Supervisor messages (starts, crashes, restarts) are in `logs/run.log`.

`python run.py` works too, since it needs no packages.

**Camera resolution and frame rate:** set them once, in `.env`:

```
CAMERA_WIDTH=1280
CAMERA_HEIGHT=720
CAMERA_FPS=30
```

These are the defaults when unset. On every start, before photobooth-app is launched, `run.py` writes them into the active camera's `cam_resolution_width` / `cam_resolution_height` / `cam_framerate` in `booth/photobooth-data/config/config.json`, and the gesture trigger streams the virtual camera at the same size. When it changes `config.json` it logs the change (e.g. `WebcamPyav 1920x1080@30 -> 1280x720@30`) and keeps the previous file as `config.json_backup-<time>` (gitignored). `.env` wins: a resolution changed in photobooth-app's admin UI is replaced on the next `run.py` start. The older `GESTURE_WIDTH` / `GESTURE_HEIGHT` / `GESTURE_FPS` names still work. The collage `canvas_width` / `canvas_height` are **not** changed: they are the finished collage's size, and the photo slots in `merge_definition` are placed in canvas pixels, so change those yourself in the admin UI.

**"WinError 10048" / port 8000 already in use:** an earlier start is still running, usually because its terminal was closed instead of stopped with Ctrl+C. Stop the leftovers, then start again:

```bash
uv run stop.py
```

It stops only this repo's processes (photobooth-app, `main.py`, `gesture_trigger.py`, `run.py`); `--dry-run` lists them without stopping anything. If an unrelated program holds port 8000, it says so and leaves it running.

**Touch-only booth:** `--no-gesture` needs photobooth-app's camera `device_identifier` set back to your webcam (e.g. `"Integrated Webcam"`) in its admin UI or `config.json`. `run.py` checks this and won't start until it matches.

### Run by hand (three terminals)

To work on one service, or to see each service in its own window, start them yourself in three Windows Terminal tabs from the repo root, in this order. No venv activation — `uv run` picks the right `.venv` from the folder you're in.

**Tab 1 — gesture trigger** (first, so the virtual camera is already streaming when photobooth-app opens it)
```bash
cd gesture
uv run gesture_trigger.py
```

**Tab 2 — the photobooth app**
```bash
cd booth/photobooth-data
uv run photobooth
```
Open `http://localhost:8000`. (uv finds the `booth` project from the parent folder; running it from inside `photobooth-data` keeps all its `config/`, `database/`, and `media/` folders there.)

**Tab 3 — the uploader**
```bash
cd booth
uv run main.py
```

Touch-only booth by hand: set the camera back to your webcam as above, and run only tabs 2 and 3. Started by hand, nothing restarts a service that crashes, and nothing copies `CAMERA_WIDTH` / `CAMERA_HEIGHT` / `CAMERA_FPS` into `config.json` — after changing them, start once with `uv run run.py` (or set the same values in photobooth-app's admin UI).

## Tuning to your app version / collage

- `WATCH_DIR` — the folder photobooth-app writes finished images to (default `media/processed_full`, unchanged in v9).
- `SESSION_QUIET_SECONDS` — how long the watcher waits with no new file before treating a burst as one finished session (default `3`). A collage session writes several files (each individual shot **plus** the composite) over a second or two; the debounce groups them automatically, so you no longer hard-code a per-session count. Raise it only if a slow machine uploads before the collage file lands.

## Other branches

- `option-a-rclone-drive` — an experiment using photobooth-app 9's **native** Rclone synchronizer; abandoned due to upstream bugs (see that branch's `docs/PIPELINE-OPTION-A.md`).
- `photobooth-app-4.0.0` — the original Oct 2024 version.

## 📖 Reference

photobooth-app — [github](https://github.com/photobooth-app/photobooth-app.git) · [docs](https://photobooth-app.org/)
