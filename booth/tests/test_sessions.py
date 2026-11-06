# Tests for the uploader's session state machine. Run from booth/:
#     uv run --with pytest python -m pytest tests
import pytest

from uploader.sessions import Sessions


class FakeFolder:
    def __init__(self, name):
        self.name = name

    def when_ready(self, callback):
        pass


class FakePool:
    def __init__(self):
        self.taken, self.returned = [], []

    def take(self, name):
        folder = FakeFolder(name)
        self.taken.append(folder)
        return folder

    def give_back(self, folder):
        self.returned.append(folder)


class FakeLinks:
    def __init__(self):
        self.keys = {}

    def add(self, key, folder):
        self.keys[key] = folder


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def booth():
    pool, links, uploads, clock = FakePool(), FakeLinks(), [], Clock()
    names = iter(f"session-{n}" for n in range(1, 100))
    sessions = Sessions(pool, links, lambda files, folder, name: uploads.append((files, folder, name)),
                        quiet_seconds=3, clock=clock, namer=lambda: next(names))
    return sessions, pool, links, uploads, clock


def run_job(sessions, files, present_id="collage-id", inserted=("collage-id",)):
    sessions.job_state("start")
    sessions.job_state("counting")
    for path in files:
        sessions.job_state("capture")
        sessions.file_created(path)
    sessions.job_state("completed")
    for media_id in inserted:
        sessions.media_inserted(media_id)
    sessions.job_state("present", present_id)
    sessions.job_state("finished", present_id)


def test_a_job_takes_one_folder_at_start(booth):
    sessions, pool, *_ = booth
    sessions.job_state("start")
    assert [f.name for f in pool.taken] == ["session-1"]


def test_a_job_uploads_its_files_when_it_finishes(booth):
    sessions, pool, links, uploads, _ = booth
    run_job(sessions, ["media/a.jpg", "media/b.jpg", "media/collage.jpg"])
    assert uploads == [(["media/a.jpg", "media/b.jpg", "media/collage.jpg"], pool.taken[0], "session-1")]
    assert len(pool.taken) == 1


def test_files_and_media_ids_point_to_the_session_folder_straight_away(booth):
    sessions, pool, links, uploads, _ = booth
    sessions.job_state("start")
    sessions.file_created("media/collage.jpg")
    sessions.media_inserted("collage-id")
    assert links.keys == {"collage.jpg": pool.taken[0], "collage-id": pool.taken[0]}
    assert uploads == []


def test_present_media_id_is_mapped(booth):
    sessions, pool, links, *_ = booth
    sessions.job_state("start")
    sessions.job_state("present", "presented-id")
    assert links.keys["presented-id"] is pool.taken[0]


def test_repeated_busy_states_stay_in_one_session(booth):
    sessions, pool, *_ = booth
    sessions.job_state("start")
    sessions.job_state("counting")   # e.g. sent again when the SSE stream reconnects mid-job
    sessions.job_state("counting")
    assert len(pool.taken) == 1


def test_an_aborted_job_without_files_gives_its_folder_back(booth):
    sessions, pool, links, uploads, _ = booth
    sessions.job_state("start")
    sessions.job_state(None)
    assert pool.returned == pool.taken
    assert uploads == []


def test_an_aborted_job_with_files_still_uploads_them(booth):
    sessions, pool, links, uploads, _ = booth
    sessions.job_state("start")
    sessions.file_created("media/a.jpg")
    sessions.job_state(None)
    assert uploads == [(["media/a.jpg"], pool.taken[0], "session-1")]
    assert pool.returned == []


def test_idle_events_without_a_session_do_nothing(booth):
    sessions, pool, links, uploads, _ = booth
    sessions.job_state(None)
    sessions.job_state("finished")
    assert pool.taken == [] and uploads == []


def test_each_job_gets_its_own_folder(booth):
    sessions, pool, links, uploads, _ = booth
    run_job(sessions, ["media/1.jpg"], present_id="one", inserted=["one"])
    run_job(sessions, ["media/2.jpg"], present_id="two", inserted=["two"])
    assert [u[2] for u in uploads] == ["session-1", "session-2"]
    assert links.keys["one"] is pool.taken[0] and links.keys["two"] is pool.taken[1]


# --- fallback: files with no SSE job (stream down, or files outside a job) ----------------

def test_files_without_a_job_open_a_session_closed_by_the_quiet_window(booth):
    sessions, pool, links, uploads, clock = booth
    sessions.file_created("media/a.jpg")
    clock.now += 2
    sessions.file_created("media/b.jpg")
    clock.now += 2.9
    sessions.tick()
    assert uploads == []
    clock.now += 0.2
    sessions.tick()
    assert uploads == [(["media/a.jpg", "media/b.jpg"], pool.taken[0], "session-1")]
    assert links.keys["a.jpg"] is pool.taken[0]


def test_tick_never_closes_a_job_session(booth):
    sessions, pool, links, uploads, clock = booth
    sessions.job_state("capture")
    sessions.file_created("media/a.jpg")
    clock.now += 60
    sessions.tick()
    assert uploads == []


def test_losing_the_stream_mid_job_falls_back_to_the_quiet_window(booth):
    sessions, pool, links, uploads, clock = booth
    sessions.job_state("capture")
    sessions.file_created("media/a.jpg")
    clock.now += 10
    sessions.sse_lost()
    sessions.file_created("media/collage.jpg")
    clock.now += 3
    sessions.tick()
    assert uploads == [(["media/a.jpg", "media/collage.jpg"], pool.taken[0], "session-1")]


def test_losing_the_stream_with_an_empty_session_gives_the_folder_back_after_the_quiet_window(booth):
    sessions, pool, links, uploads, clock = booth
    sessions.job_state("start")
    sessions.sse_lost()
    clock.now += 3
    sessions.tick()
    assert pool.returned == pool.taken and uploads == []


def test_a_new_job_closes_a_fallback_session_first(booth):
    sessions, pool, links, uploads, clock = booth
    sessions.file_created("media/late.jpg")
    sessions.job_state("start")
    assert uploads == [(["media/late.jpg"], pool.taken[0], "session-1")]
    assert len(pool.taken) == 2


def test_a_job_seen_after_a_reconnect_keeps_the_open_job_session(booth):
    sessions, pool, links, uploads, clock = booth
    sessions.job_state("capture")
    sessions.file_created("media/a.jpg")
    sessions.sse_lost()
    sessions.job_state("capture")       # stream back mid-job: the server re-sends the job state
    sessions.job_state("finished")
    assert uploads == [(["media/a.jpg"], pool.taken[0], "session-1")]
    assert len(pool.taken) == 1
