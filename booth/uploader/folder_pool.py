"""Drive folders that are created and shared *before* a session needs one.

Creating and sharing a folder takes two Drive round trips (0.5-2 s on venue Wi-Fi). The pool
keeps one spare folder ready, named spare_name, so a new session gets its share link at once;
the spare is renamed to the session's timestamp and a new spare is made in the background.
Spares left over from an earlier run (same name, same parent) are reused at start.

All Drive calls run as jobs on the single Drive worker thread (googleapiclient isn't
thread-safe), so they never overlap with uploads.
"""
import threading

from .drive import DRIVE_ERRORS

SPARE_NAME = "_next session"


def folder_link(folder_id):
    return f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"


class Folder:
    """A Drive folder that may still be on its way. Becomes ready (id set) or failed."""

    def __init__(self):
        self.id = None
        self.error = None
        self._done = threading.Event()
        self._callbacks = []
        self._lock = threading.Lock()

    @property
    def link(self):
        return folder_link(self.id) if self.id else None

    @property
    def failed(self):
        return self.error is not None

    def wait(self, timeout):
        """The folder id once ready, None if it failed or isn't ready within timeout seconds."""
        self._done.wait(timeout)
        return self.id

    def when_ready(self, callback):
        """Call callback(folder) once the folder is ready (at once if it already is)."""
        with self._lock:
            if self.id is None:
                self._callbacks.append(callback)
                return
        callback(self)

    def resolve(self, folder_id):
        with self._lock:
            self.id, self.error = folder_id, None
            callbacks, self._callbacks = self._callbacks, []
        self._done.set()
        for callback in callbacks:
            callback(self)

    def fail(self, error):
        self.error = error
        self._done.set()


class FolderPool:
    """
    worker: submit(job) runs job() on the Drive worker thread, in order
    drive:  create_folder(name, parent_id) -> id, share(id), rename(id, name),
            find_folders(name, parent_id) -> [id]
    """

    def __init__(self, worker, drive, parent_id, spare_name=SPARE_NAME):
        self._worker = worker
        self._drive = drive
        self._parent = parent_id
        self._spare_name = spare_name
        self._spares = []
        self._lock = threading.Lock()

    def start(self):
        """Reuse spares from an earlier run, else prepare a new one (in the background)."""
        def adopt():
            try:
                found = self._drive.find_folders(self._spare_name, self._parent)
            except DRIVE_ERRORS as error:
                print(f"Could not look for spare Drive folders: {error!r}")
                found = []
            with self._lock:
                for folder_id in found:
                    folder = Folder()
                    folder.resolve(folder_id)
                    self._spares.append(folder)
            if found:
                print(f"Reusing {len(found)} spare Drive folder(s) from an earlier run")
            self.fill()
        self._worker.submit(adopt)

    def fill(self):
        """Make sure a spare is ready or on its way."""
        with self._lock:
            if self._spares:
                return
            folder = Folder()
            self._spares.append(folder)
        self._worker.submit(lambda: self._create(folder, self._spare_name))

    def take(self, name):
        """A shared folder for a new session (ready, or created next on the worker)."""
        with self._lock:
            while self._spares and self._spares[0].failed:
                self._spares.pop(0)
            folder = self._spares.pop(0) if self._spares else None
        if folder is None:
            folder = Folder()
            self._worker.submit(lambda: self._create(folder, name))
        else:
            self._worker.submit(lambda: self._rename(folder, name))
        self.fill()
        return folder

    def give_back(self, folder):
        """An unused session folder becomes a spare again."""
        if folder.failed:
            return
        with self._lock:
            self._spares.insert(0, folder)      # used next, before the newer spare
        self._worker.submit(lambda: self._rename(folder, self._spare_name))

    def ensure(self, folder, name):
        """On the worker thread: the folder's id, creating it again if that failed earlier.
        None if Drive still can't be reached."""
        if folder.id is None:
            self._create(folder, name)
        return folder.id

    # --- worker-thread jobs -----------------------------------------------------------------

    def _create(self, folder, name):
        try:
            folder_id = self._drive.create_folder(name, self._parent)
            self._drive.share(folder_id)
        except DRIVE_ERRORS as error:
            print(f"Could not create a shared Drive folder: {error!r}")
            folder.fail(error)
            return
        folder.resolve(folder_id)

    def _rename(self, folder, name):
        if folder.id is None:
            return                  # never created; ensure() names it on retry
        try:
            self._drive.rename(folder.id, name)
        except DRIVE_ERRORS as error:
            print(f"Could not rename Drive folder {folder.id} to {name}: {error!r}")
