# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Start the whole booth with one command and keep it running.

    uv run run.py                 gesture trigger + photobooth-app + uploader
    uv run run.py --no-gesture    touch-only booth: photobooth-app + uploader

A supervisor only: standard library, imports nothing from booth/ or gesture/. Each service
is started with `uv run` in its own folder -- so in its own .venv -- exactly as by hand.

  1. checks .env, the Google token, the project .venvs and photobooth-app's camera setting
  2. starts the gesture trigger and waits until it streams to the virtual camera
  3. starts photobooth-app and waits until its web UI answers
  4. starts the uploader (booth/main.py)
  5. restarts any service that exits, waiting 1 s, 2 s, 4 s ... up to 60 s between tries.
     photobooth-app reads the gesture trigger's virtual camera, so while the gesture trigger
     is down photobooth-app is stopped too, and started again once the camera streams.
  6. Ctrl+C stops everything.

Output goes to this console, prefixed with the service name, and to logs/<service>.log.
"""

import argparse, json, os, re, shutil, subprocess, sys, threading, time, urllib.error, urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BOOTH = ROOT / "booth"
GESTURE = ROOT / "gesture"
CONFIG_JSON = BOOTH / "photobooth-data" / "config" / "config.json"
LOG_DIR = ROOT / "logs"

BACKOFF_FIRST = 1.0             # seconds before the first restart; doubles on each crash ...
BACKOFF_MAX = 60.0              # ... up to this
STABLE_SECONDS = 60.0           # a service that ran this long starts over at BACKOFF_FIRST
STOP_GRACE_SECONDS = 15.0       # after Ctrl+C, time the services get to shut down cleanly
READY_TIMEOUT_SECONDS = 180.0   # a start that isn't ready by then is killed and retried
LOG_ROTATE_BYTES = 10_000_000   # logs/<service>.log moves to .log.1 at startup past this size

# gesture_trigger.py prints this once frames go out to the virtual camera (open_virtual_camera).
GESTURE_READY = re.compile(r"Streaming to virtual camera")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def rel(path):
    try:
        return Path(path).relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


# --- settings ---------------------------------------------------------------------------
def read_env(path):
    """KEY=VALUE pairs from a .env file, read the way python-dotenv reads simple files."""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()           # inline comment
        values[key] = value
    return values


def setting(env, name, default=""):
    """Like the services see it: a real environment variable wins over .env."""
    value = os.environ[name] if name in os.environ else env.get(name, "")
    return value or default


def camera_devices(config):
    """device_identifier of every camera backend photobooth-app uses (stills, video, multicam)."""
    cameras = config.get("cameras") or {}
    backends = cameras.get("group_backends") or []
    devices = set()
    for key in ("index_backend_stills", "index_backend_video", "index_backend_multicam"):
        index = cameras.get(key, 0)
        if isinstance(index, int) and 0 <= index < len(backends) and backends[index].get("enabled", True):
            device = (backends[index].get("backend_config") or {}).get("device_identifier")
            if device is not None:
                devices.add(str(device))
    return devices


def url_answers(url, timeout=1.0):
    """True if anything answers HTTP at url (an error status still counts as up)."""
    try:
        urllib.request.urlopen(url, timeout=timeout).close()
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def preflight(env, gesture):
    """(errors, warnings) found before anything starts."""
    errors, warnings = [], []

    if not shutil.which("uv"):
        errors.append("uv is not on PATH. Install it: https://docs.astral.sh/uv/")
    for project in ([BOOTH, GESTURE] if gesture else [BOOTH]):
        if not (project / ".venv").exists():
            errors.append(f"{rel(project)}/.venv is missing. Create it first: "
                          f"uv sync --locked --project {rel(project)}")

    if not (ROOT / ".env").exists():
        errors.append("No .env in the repo root. Copy .env.example to .env and set MAIN_FOLDER_ID.")
    elif setting(env, "MAIN_FOLDER_ID") in ("", "your_google_drive_folder_id"):
        errors.append("MAIN_FOLDER_ID is not set in .env (the Drive folder that holds the sessions).")

    token = BOOTH / setting(env, "TOKEN_FILE", "token.json")
    secret = BOOTH / setting(env, "CLIENT_SECRET_FILE", "client_secret.json")
    if not token.exists():
        if secret.exists():
            warnings.append(f"{rel(token)} not found: the uploader will open a browser to "
                            "authorize Google Drive when it starts.")
        else:
            errors.append(f"Neither {rel(token)} nor {rel(secret)} exists, so the uploader can't "
                          "sign in to Google Drive. Copy token.json from another booth, or put the "
                          "Desktop OAuth client_secret.json in booth/.")

    vcam = setting(env, "GESTURE_VIRTUAL_CAMERA", "OBS Virtual Camera")
    if not CONFIG_JSON.exists():
        warnings.append(f"{rel(CONFIG_JSON)} not found: photobooth-app will create a default one; "
                        "check its camera in the admin UI.")
    else:
        try:
            devices = camera_devices(json.loads(CONFIG_JSON.read_text(encoding="utf-8")))
        except ValueError as error:
            errors.append(f"{rel(CONFIG_JSON)} is not valid JSON: {error}")
        else:
            shown = ", ".join(repr(d) for d in sorted(devices)) or "not set"
            if gesture and vcam not in devices:
                errors.append(
                    f"photobooth-app's camera is {shown}, but the gesture trigger streams to {vcam!r}. "
                    f"Set the camera's device_identifier to {vcam!r} (admin UI or {rel(CONFIG_JSON)}), "
                    "or start with --no-gesture for a touch-only booth.")
            elif not gesture and vcam in devices:
                errors.append(
                    f"photobooth-app's camera is {vcam!r}, which only shows a picture while the gesture "
                    "trigger runs. For a touch-only booth set device_identifier to your webcam "
                    f"(e.g. 'Integrated Webcam') in the admin UI or {rel(CONFIG_JSON)}, "
                    "or start without --no-gesture.")

    url = setting(env, "PHOTOBOOTH_URL", "http://localhost:8000").rstrip("/")
    if url_answers(url):
        errors.append(f"Something already answers at {url}. Is photobooth-app already running? Stop it first.")

    return errors, warnings


# --- output -----------------------------------------------------------------------------
class Output:
    """Every line goes to the console with a [service] prefix and to logs/<service>.log."""
    COLORS = {"gesture": "35", "photobooth": "36", "uploader": "33", "run": "1"}

    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.files = {}
        self.lock = threading.Lock()
        self.color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
        if self.color and os.name == "nt":
            os.system("")                                       # turns on ANSI colours in conhost
        try:
            sys.stdout.reconfigure(errors="replace")
        except AttributeError:
            pass

    def _file(self, name):
        if name not in self.files:
            self.log_dir.mkdir(exist_ok=True)
            path = self.log_dir / f"{name}.log"
            if path.exists() and path.stat().st_size > LOG_ROTATE_BYTES:
                path.replace(path.with_suffix(".log.1"))
            self.files[name] = open(path, "a", encoding="utf-8", buffering=1)
        return self.files[name]

    def line(self, name, text, also_log=()):
        now = datetime.now()
        text = ANSI_ESCAPE.sub("", text.rstrip("\r\n"))
        tag = f"[{name}]".ljust(13)
        if self.color:
            tag = f"\x1b[{self.COLORS.get(name, '0')}m{tag}\x1b[0m"
        with self.lock:
            print(f"{now:%H:%M:%S} {tag}{text}", flush=True)
            for log in (name, *also_log):
                self._file(log).write(f"{now:%Y-%m-%d %H:%M:%S} {text}\n")

    def event(self, text, service=None):
        """A supervisor message; one about a service also lands in that service's log."""
        if service:
            self.line("run", f"{service}: {text}", also_log=(service,))
        else:
            self.line("run", text)


# --- supervisor -------------------------------------------------------------------------
class Service:
    def __init__(self, name, cwd, args, *, ready_line=None, ready_url=None,
                 ready_timeout=READY_TIMEOUT_SECONDS, requires=None, after=None):
        self.name, self.cwd, self.args = name, Path(cwd), list(args)
        self.ready_line = ready_line        # ready once the output matches this regex ...
        self.ready_url = ready_url          # ... or once this URL answers; neither: at once
        self.ready_timeout = ready_timeout
        self.requires = requires            # runs only while this service is ready
        self.after = after                  # first start waits until this one was ready once
        self.proc = None
        self.ready = False
        self.ever_ready = False
        self.ready_seen = False
        self.started_at = 0.0
        self.next_start = 0.0
        self.next_url_check = 0.0
        self.backoff = BACKOFF_FIRST

    @property
    def command(self):
        return " ".join([Path(self.args[0]).stem, *self.args[1:]])


def kill_tree(proc):
    """Stop a `uv run` process and everything it started."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()                                        # uv passes SIGTERM on
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


class Supervisor:
    def __init__(self, services, out, env):
        self.services, self.out, self.env = services, out, env

    def run(self):
        try:
            while True:
                self.tick(time.monotonic())
                time.sleep(0.25)
        except KeyboardInterrupt:
            # The console sent Ctrl+C to the services too; give them time to finish cleanly.
            self.out.event(f"stopping, waiting up to {STOP_GRACE_SECONDS:g}s (Ctrl+C again to force)")
            self.stop_all(STOP_GRACE_SECONDS)
        except BaseException:
            self.stop_all(0)
            raise
        self.out.event("stopped")
        return 0

    def tick(self, now):
        for svc in self.services:                               # in start order: deps first
            if svc.proc is not None:
                code = svc.proc.poll()
                if code is not None:
                    self.exited(svc, now, f"exited with code {code}")
                elif not svc.ready:
                    if self.check_ready(svc, now):
                        svc.ready = svc.ever_ready = True
                        if svc.ready_line or svc.ready_url:
                            self.out.event(f"ready after {now - svc.started_at:.0f}s", svc.name)
                    elif now - svc.started_at > svc.ready_timeout:
                        kill_tree(svc.proc)
                        self.exited(svc, now, f"not ready after {svc.ready_timeout:g}s")
            if svc.proc is None and now >= svc.next_start and self.can_start(svc):
                self.start(svc, now)

    def can_start(self, svc):
        if svc.requires and not svc.requires.ready:
            return False
        return not (svc.after and not svc.after.ever_ready)

    def check_ready(self, svc, now):
        if svc.ready_line:
            return svc.ready_seen
        if svc.ready_url:
            if now < svc.next_url_check:
                return False
            svc.next_url_check = now + 1.0
            return url_answers(svc.ready_url)
        return True

    def start(self, svc, now):
        self.out.event(f"starting `{svc.command}` in {rel(svc.cwd)}/", svc.name)
        svc.ready = svc.ready_seen = False
        svc.started_at = now
        try:
            svc.proc = subprocess.Popen(
                svc.args, cwd=svc.cwd, env=self.env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace", bufsize=1)
        except OSError as error:
            self.exited(svc, now, f"could not start ({error})")
            return
        threading.Thread(target=self.pump, args=(svc, svc.proc), daemon=True).start()

    def pump(self, svc, proc):
        for line in proc.stdout:
            if svc.ready_line and svc.proc is proc and svc.ready_line.search(line):
                svc.ready_seen = True
            self.out.line(svc.name, line)

    def exited(self, svc, now, reason, dependency=None):
        svc.proc = None
        svc.ready = False
        if dependency:
            svc.next_start = now
            self.out.event(f"stopped because {dependency} is down; starts again once it is ready",
                           svc.name)
        else:
            ran = now - svc.started_at
            if ran >= STABLE_SECONDS:
                svc.backoff = BACKOFF_FIRST
            svc.next_start = now + svc.backoff
            self.out.event(f"{reason} after {ran:.0f}s; restarting in {svc.backoff:g}s", svc.name)
            svc.backoff = min(svc.backoff * 2, BACKOFF_MAX)
        for other in self.services:
            if other.requires is svc and other.proc is not None:
                kill_tree(other.proc)
                self.exited(other, now, "", dependency=svc.name)

    def stop_all(self, grace):
        running = [s for s in self.services if s.proc is not None]
        deadline = time.monotonic() + grace
        try:
            for svc in running:
                try:
                    svc.proc.wait(max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    pass
        except KeyboardInterrupt:
            pass
        for svc in reversed(running):
            if svc.proc.poll() is None:
                self.out.event("killing", svc.name)
            kill_tree(svc.proc)


def build_services(env, gesture):
    uv = shutil.which("uv") or "uv"
    url = setting(env, "PHOTOBOOTH_URL", "http://localhost:8000").rstrip("/")
    gesture_svc = None
    if gesture:
        gesture_svc = Service("gesture", GESTURE, [uv, "run", "gesture_trigger.py"],
                              ready_line=GESTURE_READY)
    # Run from photobooth-data so its config/, database/ and media/ folders live there.
    photobooth = Service("photobooth", BOOTH / "photobooth-data",
                         [uv, "run", "--project", str(BOOTH), "photobooth"],
                         ready_url=url, requires=gesture_svc, after=gesture_svc)
    uploader = Service("uploader", BOOTH, [uv, "run", "main.py"], after=photobooth)
    return [s for s in (gesture_svc, photobooth, uploader) if s]


def child_env():
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    env.pop("VIRTUAL_ENV", None)        # set by `uv run run.py`; each service uses its own .venv
    return env


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Start the gesture trigger, photobooth-app and the uploader, and keep them running.")
    parser.add_argument("--no-gesture", action="store_true",
                        help="touch-only booth: don't start the gesture trigger "
                             "(photobooth-app's camera must then be the real webcam)")
    args = parser.parse_args(argv)
    gesture = not args.no_gesture

    out = Output(LOG_DIR)
    env = read_env(ROOT / ".env")
    errors, warnings = preflight(env, gesture)
    for warning in warnings:
        out.event(f"warning: {warning}")
    if errors:
        for error in errors:
            out.event(f"error: {error}")
        out.event("not starting")
        return 1

    services = build_services(env, gesture)
    out.event(f"running {', '.join(s.name for s in services)}; Ctrl+C stops everything; "
              f"logs in {rel(LOG_DIR)}/")
    return Supervisor(services, out, child_env()).run()


if __name__ == "__main__":
    sys.exit(main())
