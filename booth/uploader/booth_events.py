"""Follow photobooth-app's job over its /api/sse event stream (standard library only).

Events used:
- ProcessStateinfo: data {"target": <job state>, "jobmodel": {"present_mediaitem_id": ...}}.
  Busy targets: start, counting, capture, approval, completed, present. A job ends with
  "finished"; a failed or cancelled job sends an empty payload {}.
- DbInsert: data is the new gallery item, {"id": ..., "processed": ...}. Only items shown in
  the gallery (e.g. the collage) are sent, not a collage's individual shots.

The server pings every second, so a short read timeout detects a dead link. When the stream
drops, Sessions.sse_lost() lets an open job end on the quiet window; we reconnect every few
seconds (photobooth-app re-sends the running job's state on connect).
"""
import json
import threading
import time
import urllib.request


def parse_events(lines):
    """Yield (event, data) from an SSE byte-line stream."""
    event, data = None, []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if event or data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


def dispatch(event, data, sessions):
    """Forward one SSE event to Sessions; anything else or unreadable is ignored."""
    if event not in ("ProcessStateinfo", "DbInsert"):
        return
    try:
        payload = json.loads(data or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    if event == "ProcessStateinfo":
        jobmodel = payload.get("jobmodel") or {}
        sessions.job_state(payload.get("target"), jobmodel.get("present_mediaitem_id"))
    elif payload.get("id"):
        sessions.media_inserted(str(payload["id"]))


class BoothEvents:
    def __init__(self, base_url, sessions, retry_seconds=2.0):
        self.url = f"{base_url.rstrip('/')}/api/sse"
        self._sessions = sessions
        self._retry = retry_seconds

    def start(self):
        threading.Thread(target=self._run, name="booth-events", daemon=True).start()

    def _run(self):
        connected = False
        while True:
            try:
                request = urllib.request.Request(self.url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(request, timeout=5) as resp:
                    connected = True
                    print(f"Following photobooth-app jobs at {self.url}")
                    for event, data in parse_events(resp):
                        dispatch(event, data, self._sessions)
            except (OSError, ValueError) as error:
                if connected:
                    print(f"Lost photobooth-app event stream ({error!r}); "
                          "sessions end on the quiet window until it is back")
            if connected:
                self._sessions.sse_lost()
                connected = False
            time.sleep(self._retry)
