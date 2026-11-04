# Hand-gesture capture trigger for photobooth-app.
#
# Owns the physical webcam, detects a thumbs-up (thumb pointing up, other fingers curled,
# either hand) with MediaPipe, and once it has been held for GESTURE_HOLD_SECONDS
# calls photobooth-app's action API -- the same thing the on-screen button does.
# Every frame is forwarded to the OBS Virtual Camera, which photobooth-app uses as its
# camera. The hand skeleton / progress ring is drawn into that stream only while the booth
# is idle (followed over /api/sse), so the countdown and the captured photos stay clean.
#
# MediaPipe hand landmarks: https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker
# pyvirtualcam (OBS backend): https://github.com/letmaik/pyvirtualcam

import argparse, json, math, os, sys, threading, time, urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
import pyvirtualcam
import requests
from dotenv import load_dotenv
from mediapipe.tasks.python import BaseOptions, vision

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")                               # shares the repo-root .env with main.py

photobooth_url = os.getenv("PHOTOBOOTH_URL", "http://localhost:8000").rstrip("/")
# "<action type>/<index>" from photobooth-app's config; collage/0 is the booth's only action.
trigger_action = os.getenv("GESTURE_ACTION", "collage/0")
# The physical webcam, by DirectShow name (as listed in photobooth-app's camera dropdown) or index.
camera_name = os.getenv("GESTURE_CAMERA", "Integrated Webcam")
virtual_camera = os.getenv("GESTURE_VIRTUAL_CAMERA", "OBS Virtual Camera")
frame_width = int(os.getenv("GESTURE_WIDTH", "1280"))           # keep in sync with the photobooth
frame_height = int(os.getenv("GESTURE_HEIGHT", "720"))          # camera backend's cam_resolution_*
frame_fps = int(os.getenv("GESTURE_FPS", "30"))
hold_seconds = float(os.getenv("GESTURE_HOLD_SECONDS", "3"))
# After a session ends, wait this long before showing the overlay / arming the trigger again.
cooldown_seconds = float(os.getenv("GESTURE_COOLDOWN_SECONDS", "3"))
# Hand detection runs on a downscaled copy; landmarks are normalised so drawing is unaffected.
detect_width = int(os.getenv("GESTURE_DETECT_WIDTH", "640"))
model_path = Path(os.getenv("GESTURE_MODEL", HERE / "models" / "hand_landmarker.task"))
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/"
             "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task")

# --- thumbs-up geometry -----------------------------------------------------------------
# Landmark indices (MediaPipe hand model): 0 wrist, then 4 joints per finger.
WRIST = 0
THUMB = (1, 2, 3, 4)                                            # CMC, MCP, IP, TIP
INDEX = (5, 6, 7, 8)                                            # MCP, PIP, DIP, TIP
MIDDLE = (9, 10, 11, 12)
RING = (13, 14, 15, 16)
PINKY = (17, 18, 19, 20)

FINGER_CURLED_MIN_DEG = 80          # bend above this -> finger curled
THUMB_STRAIGHT_MAX_DEG = 45
THUMB_UP_MAX_TILT_DEG = 40          # thumb direction vs straight up, in the camera image
THUMB_ABOVE_FIST_MIN = 0.3          # thumb tip above the highest finger joint, x palm length

def _angle(v1, v2):
    """Angle between two vectors in degrees."""
    cos = np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9)
    return math.degrees(math.acos(float(np.clip(cos, -1.0, 1.0))))

def _dist(p, a, b):
    return float(np.linalg.norm(p[a] - p[b]))

def _bend(p, finger):
    """How much a finger bends between its first and last segment (0 = straight)."""
    mcp, pip, dip, tip = finger
    return _angle(p[pip] - p[mcp], p[tip] - p[dip])

def is_thumbs_up(points, image_points=None):
    """True if 21 hand landmarks form a thumbs-up: thumb straight and pointing up, all four
    fingers curled into a fist. Works for either hand, mirrored or not.

    `points` (x, y, z) give the hand's shape -- MediaPipe's metric world landmarks, so finger
    curl isn't squashed by perspective. `image_points` (x, y in pixels, y pointing down) say
    which way is up in the camera image; they default to `points`."""
    p = np.asarray(points, dtype=float)
    img = np.asarray(points if image_points is None else image_points, dtype=float)[:, :2]

    def curled(f):
        return _bend(p, f) > FINGER_CURLED_MIN_DEG or _dist(p, WRIST, f[3]) < _dist(p, WRIST, f[1])

    thumb_straight = _angle(p[THUMB[2]] - p[THUMB[1]], p[THUMB[3]] - p[THUMB[2]]) < THUMB_STRAIGHT_MAX_DEG
    tilt = _angle(img[THUMB[3]] - img[THUMB[1]], np.array([0.0, -1.0]))
    palm = float(np.linalg.norm(img[MIDDLE[0]] - img[WRIST]))
    above_fist = img[INDEX[0]:, 1].min() - img[THUMB[3], 1]       # landmarks 5..20 are the fingers
    return (thumb_straight
            and all(curled(f) for f in (INDEX, MIDDLE, RING, PINKY))
            and tilt <= THUMB_UP_MAX_TILT_DEG
            and above_fist >= THUMB_ABOVE_FIST_MIN * palm)

class HoldTimer:
    """Measures how long a gesture has been held, tolerating short detection dropouts."""
    def __init__(self, hold_seconds, grace_seconds=0.35):
        self.hold_seconds = hold_seconds
        self.grace_seconds = grace_seconds
        self.reset()

    def reset(self):
        self.started = None
        self.last_seen = None

    def update(self, seen, now):
        """Feed one frame's detection; returns progress 0..1 (1 = held long enough)."""
        if seen:
            if self.started is None or now - self.last_seen > self.grace_seconds:
                self.started = now
            self.last_seen = now
        elif self.started is not None and now - self.last_seen > self.grace_seconds:
            self.reset()
        if self.started is None:
            return 0.0
        return min(1.0, (now - self.started) / self.hold_seconds)

# --- photobooth-app link ----------------------------------------------------------------
# ProcessStateinfo targets while a job runs. A finished job ends with target "finished";
# a failed one sends an empty payload. Either means idle.
BUSY_STATES = {"start", "counting", "capture", "approval", "completed", "present"}

class BoothState:
    """Follows photobooth-app's job state over its /api/sse event stream (background thread)."""
    def __init__(self, base_url):
        self.url = f"{base_url}/api/sse"
        self.connected = False
        self.busy = False
        self.idle_since = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def idle_for(self):
        """Seconds the booth has been reachable and idle; 0 while busy or unreachable."""
        with self._lock:
            if not self.connected or self.busy:
                return 0.0
            return time.monotonic() - self.idle_since

    def _set(self, connected, busy):
        with self._lock:
            if connected and not busy and (not self.connected or self.busy):
                self.idle_since = time.monotonic()
            self.connected, self.busy = connected, busy

    def _run(self):
        while True:
            try:
                # The server pings every second, so a 5 s read timeout detects a dead link.
                with requests.get(self.url, stream=True, timeout=(3, 5),
                                  headers={"Accept": "text/event-stream"}) as resp:
                    resp.raise_for_status()
                    # An idle booth sends no ProcessStateinfo on connect; a running job does
                    # straight away, well inside the cooldown before the overlay can show.
                    self._set(True, False)
                    print(f"Connected to photobooth-app at {self.url}")
                    event = None
                    for line in resp.iter_lines(chunk_size=None, decode_unicode=True):
                        line = line.strip("\r")
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:") and event == "ProcessStateinfo":
                            target = (json.loads(line[5:].strip() or "{}") or {}).get("target")
                            self._set(True, target in BUSY_STATES)
            except (requests.RequestException, ValueError) as error:
                if self.connected:
                    print(f"Lost photobooth-app event stream: {error}")
            self._set(False, False)
            time.sleep(2)

def trigger_capture():
    """Start a photobooth-app job, like pressing the on-screen button."""
    url = f"{photobooth_url}/api/actions/{trigger_action}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 400:
            print(f"Booth busy, trigger ignored: {resp.text}")
            return
        resp.raise_for_status()
        print(f"Triggered {trigger_action}")
    except requests.RequestException as error:
        print(f"Trigger failed ({url}): {error}")

# --- camera in / out --------------------------------------------------------------------
def open_camera():
    """Open the physical webcam via DirectShow, by name (resolved with pygrabber) or index."""
    devices = []
    if sys.platform == "win32":
        from pygrabber.dshow_graph import FilterGraph
        devices = FilterGraph().get_input_devices()
    if camera_name.isdigit():
        index = int(camera_name)
    elif camera_name in devices:
        index = devices.index(camera_name)
    else:
        sys.exit(f"Camera {camera_name!r} not found. Set GESTURE_CAMERA to one of: {devices}")
    if index < len(devices) and devices[index] == virtual_camera:
        sys.exit(f"GESTURE_CAMERA points at {virtual_camera!r} -- that is this script's own output.")

    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
    cap.set(cv2.CAP_PROP_FPS, frame_fps)
    if not cap.isOpened():
        sys.exit(f"Could not open camera {camera_name!r} (index {index}). Is another app using it?")
    print(f"Camera {camera_name!r}: {int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
          f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ {cap.get(cv2.CAP_PROP_FPS):.0f} fps")
    return cap

def open_virtual_camera():
    try:
        vcam = pyvirtualcam.Camera(width=frame_width, height=frame_height, fps=frame_fps,
                                   fmt=pyvirtualcam.PixelFormat.BGR, device=virtual_camera)
    except RuntimeError as error:
        sys.exit(f"Could not open virtual camera {virtual_camera!r}: {error}\n"
                 "Install OBS Studio (it provides the virtual camera), then run this again.")
    print(f"Streaming to virtual camera {vcam.device!r}")      # run.py waits for this line
    return vcam

def ensure_model():
    if not model_path.exists():
        print(f"Downloading hand model to {model_path} ...")
        model_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = model_path.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(model_path)
    return model_path

# --- drawing ------------------------------------------------------------------------------
HAND_CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8),
                    (5, 9), (9, 10), (10, 11), (11, 12), (9, 13), (13, 14), (14, 15), (15, 16),
                    (13, 17), (17, 18), (18, 19), (19, 20), (0, 17)]
COLOR_ACTIVE = (90, 220, 110)       # BGR
COLOR_HAND = (210, 210, 210)
COLOR_TRACK = (90, 90, 90)

def _pixels(frame, landmarks):
    h, w = frame.shape[:2]
    return [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]

def draw_hand(frame, landmarks, color, progress=0.0):
    """Skeleton plus, while a thumbs-up is being held, a progress ring around the palm.
    No text: photobooth-app mirrors its live preview, so text would read backwards."""
    pts = _pixels(frame, landmarks)
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], color, 3, cv2.LINE_AA)
    for pt in pts:
        cv2.circle(frame, pt, 5, (255, 255, 255), -1, cv2.LINE_AA)
    if progress > 0:
        palm = np.array([pts[i] for i in (WRIST, INDEX[0], MIDDLE[0], RING[0], PINKY[0])])
        center = tuple(int(v) for v in palm.mean(axis=0))
        radius = max(40, int(1.4 * np.linalg.norm(np.subtract(pts[MIDDLE[0]], pts[WRIST]))))
        cv2.circle(frame, center, radius, COLOR_TRACK, 4, cv2.LINE_AA)
        cv2.ellipse(frame, center, (radius, radius), -90, 0, 360 * progress, COLOR_ACTIVE, 10, cv2.LINE_AA)

def draw_status(frame, text):
    cv2.putText(frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(frame, text, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)

# --- main loop ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Hold a thumbs-up to start a photobooth-app capture.")
    parser.add_argument("--preview", action="store_true",
                        help="show a local window with the skeleton and trigger state (q to quit)")
    parser.add_argument("--no-vcam", action="store_true",
                        help="don't stream to the virtual camera (implies --preview)")
    parser.add_argument("--dry-run", action="store_true",
                        help="don't call photobooth-app; treat it as always idle (tuning the gesture)")
    args = parser.parse_args()
    preview = args.preview or args.no_vcam

    options = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(ensure_model())),
        running_mode=vision.RunningMode.VIDEO,
        num_hands=2,
        min_hand_detection_confidence=0.6,
        min_hand_presence_confidence=0.6,
        min_tracking_confidence=0.5)
    landmarker = vision.HandLandmarker.create_from_options(options)
    cap = open_camera()
    vcam = None if args.no_vcam else open_virtual_camera()
    booth = None if args.dry_run else BoothState(photobooth_url)

    hold = HoldTimer(hold_seconds)
    hide_until = 0.0            # after firing, keep the stream clean until SSE reports the job
    need_release = False        # after firing, the thumb must drop before the trigger re-arms
    released_since = None
    last_ts = 0
    print(f"Hold a thumbs-up for {hold_seconds:g}s to trigger {photobooth_url}/api/actions/{trigger_action}")

    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera read failed, retrying...")
                time.sleep(0.5)
                continue
            if frame.shape[1] != frame_width or frame.shape[0] != frame_height:
                frame = cv2.resize(frame, (frame_width, frame_height))
            now = time.monotonic()

            small = frame
            if frame_width > detect_width:
                small = cv2.resize(frame, (detect_width, round(frame_height * detect_width / frame_width)))
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
            last_ts = max(last_ts + 1, int(now * 1000))       # VIDEO mode needs increasing timestamps
            result = landmarker.detect_for_video(image, last_ts)
            # Hand shape from metric world landmarks (no perspective squash); "up" and drawing
            # from the image landmarks (normalised, so scale back to pixels for true angles).
            hands = [(lm, is_thumbs_up([(p.x, p.y, p.z) for p in world],
                                       [(p.x * frame_width, p.y * frame_height) for p in lm]))
                     for lm, world in zip(result.hand_landmarks, result.hand_world_landmarks)]
            gesture_seen = any(hit for _, hit in hands)

            idle_for = booth.idle_for() if booth else math.inf
            overlay_on = idle_for >= cooldown_seconds and now >= hide_until
            if need_release:
                if gesture_seen:
                    released_since = None
                elif released_since is None:
                    released_since = now
                elif now - released_since >= 0.5:
                    need_release = False
            armed = overlay_on and not need_release
            if armed:
                progress = hold.update(gesture_seen, now)
            else:
                hold.reset()
                progress = 0.0

            if armed and gesture_seen and progress >= 1.0:
                if booth:
                    threading.Thread(target=trigger_capture, daemon=True).start()
                else:
                    print(f"Dry run: would trigger {trigger_action}")
                hide_until = now + 5.0
                need_release = True
                released_since = None
                hold.reset()
                progress = 0.0
                overlay_on = False

            out = frame.copy() if overlay_on else frame
            if overlay_on:
                for landmarks, hit in hands:
                    draw_hand(out, landmarks, COLOR_ACTIVE if hit else COLOR_HAND, progress if hit else 0.0)
            if vcam:
                vcam.send(out)

            if preview:
                # The preview always shows the skeleton, even while the stream is kept clean.
                if not overlay_on:
                    out = frame.copy()
                    for landmarks, hit in hands:
                        draw_hand(out, landmarks, COLOR_ACTIVE if hit else COLOR_HAND)
                view = cv2.flip(out, 1)                         # mirror, like the booth UI
                if booth and not booth.connected:
                    status = "photobooth-app offline"
                elif now < hide_until or (booth and booth.busy):
                    status = "booth busy - overlay hidden"
                elif not overlay_on:
                    status = f"cooldown {cooldown_seconds - idle_for:.1f}s"
                elif need_release:
                    status = "lower the thumb to re-arm"
                elif progress > 0:
                    status = f"holding thumbs-up {progress * hold_seconds:.1f}/{hold_seconds:g}s"
                else:
                    status = "ready - show a thumbs-up"
                draw_status(view, status)
                cv2.imshow("Gesture trigger (preview)", view)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        if vcam:
            vcam.close()
        landmarker.close()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
