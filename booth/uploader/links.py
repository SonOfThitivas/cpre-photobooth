"""Share links by media id or file name, served to photobooth-app's drive_share plugin.

photobooth-app's frontend asks for an item's share links once, when it shows the item (the
presenter after a capture, or the gallery). The drive_share plugin forwards that to
GET http://127.0.0.1:<port>/links?id=<media id>&file=<file name>, which answers a JSON list:
the session folder's link, or [] if there is none. If the key is known but its folder is
still being created, or the key isn't known yet, the answer waits up to wait_seconds.

Links of ready folders are saved to a JSON file, so gallery items of earlier sessions keep
their QR code after a restart. The server listens on 127.0.0.1 only.
"""
import json
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .folder_pool import folder_link


class LinkStore:
    def __init__(self, path=None):
        self._path = path
        self._pending = {}              # key -> Folder of a session (ready or not)
        self._saved = self._load()      # key -> folder id, from earlier sessions too
        self._changed = threading.Condition()

    def add(self, key, folder):
        with self._changed:
            self._pending[key] = folder
            self._changed.notify_all()
        folder.when_ready(lambda f: self._save(key, f.id))

    def lookup(self, keys, timeout):
        """[link] for the first key known (waiting up to timeout seconds for it), else []."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                folder = next((self._pending[k] for k in keys if k in self._pending), None)
                if folder is not None:
                    break
                saved = next((self._saved[k] for k in keys if k in self._saved), None)
                if saved:
                    return [folder_link(saved)]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._changed.wait(remaining)
        folder_id = folder.wait(max(0.0, deadline - time.monotonic()))
        return [folder_link(folder_id)] if folder_id else []

    def _load(self):
        if not self._path or not os.path.exists(self._path):
            return {}
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
            return {str(k): str(v) for k, v in data.items()}
        except (OSError, ValueError, AttributeError) as error:
            print(f"Could not read {self._path} ({error!r}); older gallery items have no link")
            return {}

    def _save(self, key, folder_id):
        with self._changed:
            self._saved[key] = folder_id
            if not self._path:
                return
            tmp = f"{self._path}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._saved, f, indent=0)
                os.replace(tmp, self._path)
            except OSError as error:
                print(f"Could not save {self._path}: {error!r}")


class LinkServer:
    def __init__(self, store, port, wait_seconds):
        store_, wait = store, wait_seconds

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                if url.path != "/links":
                    self.send_error(404)
                    return
                query = urllib.parse.parse_qs(url.query)
                keys = [v for name in ("id", "file") for v in query.get(name, []) if v]
                body = json.dumps(store_.lookup(keys, wait)).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass                    # one line per QR lookup would drown the upload log

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True

    @property
    def port(self):
        return self._server.server_address[1]

    def start(self):
        threading.Thread(target=self._server.serve_forever, name="link-server", daemon=True).start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
