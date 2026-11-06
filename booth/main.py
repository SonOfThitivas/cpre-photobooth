# The uploader: one shared Google Drive folder per photo session, its link shown as a QR code
# inside photobooth-app (by the drive_share plugin in photobooth-data/plugins/).
#
# Drive API upload ref: https://ragug.medium.com/how-to-upload-files-using-the-google-drive-api-in-python-ebefdfd63eab
# Watchdog: https://github.com/gorakhargosh/watchdog
#
#   photobooth-app /api/sse ──▶ booth_events ─┐
#   watchdog (WATCH_DIR)  ────────────────────┼─▶ sessions ─▶ folder_pool / links / uploads
#                                              │                    │
#   drive_share plugin ─GET /links──▶ link server ◀─────────────────┘   all Drive calls: drive worker
#
# See docs/PIPELINE.md.
import logging
import os
import time

from dotenv import load_dotenv
from watchdog.events import FileCreatedEvent, FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from uploader.booth_events import BoothEvents
from uploader.drive import DRIVE_ERRORS, DriveClient, DriveWorker, prefer_ipv4
from uploader.folder_pool import FolderPool
from uploader.links import LinkServer, LinkStore
from uploader.sessions import Sessions

# Show library warnings, e.g. googleapiclient's "Sleeping ... before retry" lines.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

load_dotenv()
main_folder_id = os.getenv("MAIN_FOLDER_ID")
# OAuth credential files (paths overridable via .env). token.json is portable:
# copy it to other booths and they run headlessly with no browser step.
token_file = os.getenv("TOKEN_FILE", "token.json")
client_secret_file = os.getenv("CLIENT_SECRET_FILE", "client_secret.json")
# The folder photobooth-app writes finished images into (every shot and the collage).
watch_dir = os.getenv("WATCH_DIR", "./photobooth-data/media/processed_full")
# Files that arrive while no photobooth-app job is followed (event stream down, or outside a
# job) form a session that ends once no new file has appeared for this many seconds.
session_quiet_seconds = float(os.getenv("SESSION_QUIET_SECONDS", "3"))
# photobooth-app, whose /api/sse event stream marks where each session starts and ends.
photobooth_url = os.getenv("PHOTOBOOTH_URL", "http://localhost:8000")
# Local port the drive_share plugin asks for share links (127.0.0.1 only).
link_server_port = int(os.getenv("LINK_SERVER_PORT", "8765"))
# How long a link request may wait for a session folder that is still being created.
link_wait_seconds = float(os.getenv("LINK_WAIT_SECONDS", "2"))
# Saved links, so gallery items of earlier sessions keep their QR code after a restart.
links_file = os.getenv("LINKS_FILE", "links.json")


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, sessions):
        self._sessions = sessions

    def on_any_event(self, event: FileSystemEvent) -> None:
        print(f"New file: {event.src_path}")
        self._sessions.file_created(event.src_path)


def make_uploader(worker, pool, drive):
    def upload(files, folder, name):
        """Queue a closed session's files for upload into its folder."""
        def job():
            folder_id = pool.ensure(folder, name)
            if folder_id is None:
                print(f"Session {name} NOT uploaded (no Drive folder); its photos stay on disk:")
                for path in files:
                    print(f"  {path}")
                return
            failed = []
            for path in files:
                try:
                    drive.upload(path, folder_id)
                except DRIVE_ERRORS as error:
                    print(f"Could not upload {path}: {error!r}")
                    failed.append(path)
            if failed:
                print(f"Session {name}: uploaded {len(files) - len(failed)} of {len(files)} "
                      "file(s); the rest stay on disk.")
            else:
                print(f"Session {name}: all {len(files)} file(s) uploaded")
        worker.submit(job)
    return upload


def main():
    prefer_ipv4()
    drive = DriveClient.connect(token_file, client_secret_file)
    worker = DriveWorker()
    pool = FolderPool(worker, drive, main_folder_id)
    pool.start()

    store = LinkStore(links_file)
    try:
        server = LinkServer(store, link_server_port, link_wait_seconds)
    except OSError as error:
        raise SystemExit(f"Can't serve share links on 127.0.0.1:{link_server_port} ({error}). "
                         "Is another uploader still running? Stop leftover booth processes with: "
                         "uv run stop.py (from the repo root), or set LINK_SERVER_PORT.")
    server.start()

    sessions = Sessions(pool, store, make_uploader(worker, pool, drive), session_quiet_seconds)
    BoothEvents(photobooth_url, sessions).start()

    observer = Observer()
    observer.schedule(NewFileHandler(sessions), watch_dir, recursive=True, event_filter=[FileCreatedEvent])
    observer.start()
    print(f"Watching {watch_dir}; share links on 127.0.0.1:{server.port} "
          f"(quiet window {session_quiet_seconds}s when no job is followed)")

    try:
        while True:
            time.sleep(0.2)
            sessions.tick()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
        server.stop()
        try:
            print("Finishing queued Drive uploads (Ctrl+C again to quit now)...")
            worker.wait_idle()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
