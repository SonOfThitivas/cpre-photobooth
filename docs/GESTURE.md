# Hands-free trigger: hold a thumbs-up

`gesture/gesture_trigger.py` lets guests start a capture without touching the screen: hold a
**thumbs-up** (thumb pointing up, other fingers curled into a fist; either hand) for **3 seconds**.
It runs as a third process next to photobooth-app and `main.py`, and changes neither of them.

## How it fits in

```
 Physical webcam ──▶ gesture_trigger.py ──pyvirtualcam──▶ "OBS Virtual Camera" ──▶ photobooth-app 9
                     (gesture/.venv)                                               (camera backend:
                     • MediaPipe hand landmarks                                     WebcamPyav, dshow)
                     • 👍 held 3 s ─── GET /api/actions/collage/0 ──────────────────▶ starts the job
                     • overlay on/off ◀── GET /api/sse (ProcessStateinfo) ─────────── job state
                                                                                        │ writes images
                                                                                        ▼
                                                                         processed_full/ ──▶ main.py
```

- **Video.** On Windows only one process can open the webcam, so the gesture script owns it and
  forwards every frame to the OBS Virtual Camera. photobooth-app's camera
  (`cameras.group_backends[0].backend_config.device_identifier` in `config.json`) is set to
  `"OBS Virtual Camera"`, so the booth's live preview and its photos both come from that stream.
- **Trigger.** `GET http://localhost:8000/api/actions/collage/0` (`GESTURE_ACTION`) is exactly
  what the on-screen button does. While a job runs photobooth-app answers `400`, so a second
  trigger can never start a parallel session.
- **Clean photos.** The skeleton and progress ring are drawn into the stream **only while the
  booth is idle**. The script follows photobooth-app's `/api/sse` event stream: any of
  `start / counting / capture / approval / completed / present` means busy; `finished` (or an
  empty payload after a failed job) means idle. The overlay disappears the instant the trigger
  fires (before the 4 s countdown starts), stays off for the whole collage job, and returns
  `GESTURE_COOLDOWN_SECONDS` after it ends. If the event stream is down the overlay stays off,
  so a capture started from the screen is never labelled either.
- **No text in the stream.** photobooth-app mirrors its live preview (`livestream_mirror_effect`),
  so the stream overlay is shapes only. Status text appears only in the optional local preview window.
- **Re-arm.** After a session the guest must lower the thumb (about 0.5 s) before the trigger
  arms again, so someone still holding the pose can't start a second session by accident.

### The thumbs-up rule

The hand's shape is checked on MediaPipe's metric *world* landmarks (no perspective squash);
"up" is checked on the *image* landmarks, so it means up in the camera picture. Works for
left or right hand:

- index, middle, ring and pinky curled (bend > 80°, or tip closer to the wrist than the middle joint);
- thumb straight (bend < 45°);
- thumb pointing up: within 40° of vertical, so thumbs-down and sideways thumbs don't count;
- thumb tip above every finger joint by at least 0.3 × palm length, so a fist with the thumb
  resting on it doesn't count.

The 3 s hold tolerates detection dropouts of up to 0.35 s. Thresholds are constants at the top
of `gesture_trigger.py`; `gesture/tests/` covers the rule and the hold timer on synthetic hands.

### How many hands

`GESTURE_NUM_HANDS` (default `2`, minimum `1`) is the most hands MediaPipe tracks per frame;
a thumbs-up on any of them triggers. MediaPipe sets **no maximum**. The practical limit is how
many hands are large and clear enough in the frame to be found, so a higher value doesn't help
with guests standing too far from the camera.

With the default `2`, a group with more raised hands can miss a thumbs-up: once two hands are
tracked, MediaPipe mostly stops looking for new ones. For groups, set about two per guest
(e.g. `6` for three people). The cost: while fewer hands than the limit are tracked, palm
detection runs on every frame, and each tracked hand adds a landmark pass. Everything runs in the
loop that feeds the virtual camera, so too high a value on a slow laptop shows up as a choppy
booth preview. After raising it, watch the booth preview with a full group in frame for stutter.

## Setup (once per booth)

1. **OBS Studio** — install it from <https://obsproject.com/>; it provides the
   "OBS Virtual Camera" device. You don't need to keep OBS open.
2. **uv** — <https://docs.astral.sh/uv/>.
3. The gesture venv — its own uv project, separate from `booth/.venv`, so MediaPipe's
   `opencv-contrib-python` never overwrites photobooth-app's `opencv-python-headless`:

   ```bash
   uv sync --locked --project gesture
   ```

   The first run of the script downloads the MediaPipe hand model (~8 MB) to
   `gesture/models/` (gitignored).
4. Check `GESTURE_CAMERA` in `.env` matches your webcam's name (default `Integrated Webcam`; the
   script prints the available names if it can't find it). All settings are in `.env.example`.

## Run

`uv run run.py` from the repo root starts all three services in the right order (see the
README's **Run**). It waits for this script's `Streaming to virtual camera` line before it
starts photobooth-app, and restarts photobooth-app whenever this script restarts.

The stream's size and frame rate are `CAMERA_WIDTH` / `CAMERA_HEIGHT` / `CAMERA_FPS` in `.env`
(default 1280×720 at 30 fps; the older `GESTURE_WIDTH` / `GESTURE_HEIGHT` / `GESTURE_FPS` still
work). `run.py` writes the same values into photobooth-app's camera in `config.json` before it
starts it, so the two always match. If the webcam can't deliver that size, frames are resized
to it.

By hand: start the gesture tab **first**, so the virtual camera is already streaming when
photobooth-app opens it; then photobooth-app and `main.py` in their own tabs. If
photobooth-app was started first, restart it. By hand, `config.json` isn't updated from `.env`:
after changing the camera settings, start once with `uv run run.py`.

```bash
cd gesture
uv run gesture_trigger.py              # normal booth operation
uv run gesture_trigger.py --preview    # plus a local window with skeleton + trigger state
uv run gesture_trigger.py --no-vcam --dry-run   # tune the gesture: no OBS, no booth, just the window
uv run pytest                           # tests
```

## Touch-only booth again

Set `device_identifier` back to the webcam (`"Integrated Webcam"`) in photobooth-app's admin UI
or `booth/photobooth-data/config/config.json`, then start with `uv run run.py --no-gesture`
(or run only photobooth-app and `main.py` by hand).
