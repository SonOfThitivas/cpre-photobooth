"""Which files belong to which photo session, and when a session is over.

A *job session* follows photobooth-app's job over its SSE stream: it opens on the first busy
job state (start, counting, ...) and closes on "finished", or on the empty payload of a failed
or cancelled job. Its Drive folder is taken from the pool at the very start, so the share link
exists long before the frontend asks for it.

A *file session* is the fallback for files that arrive with no job open (the stream is down,
or a file comes outside a job): it opens on the first file and closes once no new file has
arrived for quiet_seconds -- the uploader's original behaviour. A job session whose stream is
lost mid-job closes the same way.

Every file name and media id seen in a session is linked to its folder at once, so the
photobooth-app plugin can find the link by either. Pure logic: no Drive, no network, no
threads of its own (the methods are safe to call from several threads).
"""
import os
import threading
import time
from datetime import datetime


def timestamp_name():
    return datetime.now().strftime("%d-%m-%Y_%H-%M-%S")


class Session:
    def __init__(self, name, folder, from_job, now):
        self.name = name
        self.folder = folder
        self.from_job = from_job        # opened by an SSE job (else by a file)
        self.stream_lost = False        # job session whose SSE stream dropped
        self.files = []
        self.last_activity = now

    @property
    def ends_on_quiet(self):
        return not self.from_job or self.stream_lost


class Sessions:
    """
    pool:    take(name) -> folder, give_back(folder)
    links:   add(key, folder)
    upload:  upload(files, folder, name), called once per closed session that has files
    """

    def __init__(self, pool, links, upload, quiet_seconds, clock=time.monotonic, namer=timestamp_name):
        self._pool = pool
        self._links = links
        self._upload = upload
        self._quiet = quiet_seconds
        self._clock = clock
        self._namer = namer
        self._current = None
        self._lock = threading.RLock()

    # --- SSE ----------------------------------------------------------------------------

    def job_state(self, target, present_id=None):
        """A ProcessStateinfo event: target is the job's new state, None for a failed or
        cancelled job (empty payload)."""
        with self._lock:
            if target is None:
                if self._current and self._current.from_job:
                    self._close()
                return
            if target == "finished":
                if self._current and self._current.from_job:
                    self._map(present_id)
                    self._close()
                return
            # A busy state.
            if self._current and not self._current.from_job:
                self._close()           # files from outside a job belong to their own session
            if self._current is None:
                self._open(from_job=True)
            self._current.stream_lost = False
            self._current.last_activity = self._clock()
            self._map(present_id)

    def media_inserted(self, media_id):
        """A DbInsert event: photobooth-app added a gallery item (e.g. the collage)."""
        with self._lock:
            if self._current:
                self._map(media_id)

    def sse_lost(self):
        """The SSE stream dropped: an open job session now ends on the quiet window."""
        with self._lock:
            if self._current and self._current.from_job:
                self._current.stream_lost = True
                self._current.last_activity = self._clock()

    # --- files ----------------------------------------------------------------------------

    def file_created(self, path):
        with self._lock:
            if self._current is None:
                self._open(from_job=False)
            self._current.files.append(path)
            self._current.last_activity = self._clock()
            self._map(os.path.basename(path))

    def tick(self):
        """Call regularly: closes a session that ends on the quiet window once it is quiet."""
        with self._lock:
            session = self._current
            if session and session.ends_on_quiet and self._clock() - session.last_activity >= self._quiet:
                self._close()

    # --- internals ------------------------------------------------------------------------

    def _open(self, from_job):
        name = self._namer()
        folder = self._pool.take(name)
        self._current = Session(name, folder, from_job, self._clock())
        print(f"Session {name} started ({'photobooth job' if from_job else 'new files'})")
        folder.when_ready(lambda f, name=name: print(f"Session {name}: {f.link}"))

    def _map(self, key):
        if key:
            self._links.add(str(key), self._current.folder)

    def _close(self):
        session, self._current = self._current, None
        if session.files:
            print(f"Session {session.name} complete: {len(session.files)} file(s)")
            self._upload(list(session.files), session.folder, session.name)
        else:
            print(f"Session {session.name} ended with no files; its folder is kept for the next one")
            self._pool.give_back(session.folder)
