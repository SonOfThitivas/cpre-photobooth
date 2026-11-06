import pytest

from uploader.folder_pool import Folder, FolderPool, folder_link


class InlineWorker:
    """Runs each job at once, in submit order, like the single Drive worker thread."""
    def submit(self, job):
        job()


class FakeDrive:
    def __init__(self, existing_spares=()):
        self.folders = {}           # id -> name
        self.shared = set()
        self.next_id = 1
        self.fail_creates = 0
        self.existing_spares = list(existing_spares)

    def create_folder(self, name, parent_id):
        if self.fail_creates:
            self.fail_creates -= 1
            raise OSError("network down")
        folder_id = f"id{self.next_id}"
        self.next_id += 1
        self.folders[folder_id] = name
        return folder_id

    def share(self, folder_id):
        self.shared.add(folder_id)

    def rename(self, folder_id, name):
        self.folders[folder_id] = name

    def find_folders(self, name, parent_id):
        return list(self.existing_spares)


@pytest.fixture
def drive():
    return FakeDrive()


def make_pool(drive):
    pool = FolderPool(InlineWorker(), drive, "parent", spare_name="_next")
    pool.start()
    return pool


def test_start_prepares_one_shared_spare(drive):
    make_pool(drive)
    assert drive.folders == {"id1": "_next"}
    assert drive.shared == {"id1"}


def test_take_hands_out_the_ready_spare_renamed_and_prepares_the_next(drive):
    pool = make_pool(drive)
    folder = pool.take("06-11-2026_10-00-00")
    assert folder.id == "id1"
    assert drive.folders == {"id1": "06-11-2026_10-00-00", "id2": "_next"}
    assert drive.shared == {"id1", "id2"}


def test_start_reuses_spares_left_from_an_earlier_run(drive):
    drive.existing_spares = ["old"]
    pool = make_pool(drive)
    assert drive.folders == {}              # nothing new created
    assert pool.take("s1").id == "old"


def test_a_folder_given_back_is_reused(drive):
    pool = make_pool(drive)
    first = pool.take("s1")
    pool.give_back(first)
    assert drive.folders["id1"] == "_next"
    assert pool.take("s2") is first


def test_a_failed_spare_is_skipped(drive):
    drive.fail_creates = 1
    pool = make_pool(drive)
    folder = pool.take("s1")
    assert folder.id == "id1"
    assert drive.folders["id1"] == "s1"


def test_ensure_retries_a_failed_folder(drive):
    drive.fail_creates = 2                  # the spare and the take-time folder both fail
    pool = make_pool(drive)
    folder = pool.take("s1")
    assert folder.failed
    seen = []
    folder.when_ready(lambda f: seen.append(f.id))
    assert pool.ensure(folder, "s1") == folder.id
    assert seen == [folder.id] and not folder.failed
    assert drive.folders[folder.id] == "s1"


def test_ensure_returns_none_while_drive_is_still_down(drive):
    drive.fail_creates = 4                  # spare, take-time folder, next spare, the retry
    pool = make_pool(drive)
    folder = pool.take("s1")
    assert pool.ensure(folder, "s1") is None


def test_folder_wait_and_callbacks():
    folder = Folder()
    seen = []
    folder.when_ready(lambda f: seen.append(f.link))
    assert folder.wait(0) is None
    folder.resolve("abc")
    assert folder.wait(0) == "abc"
    assert seen == [folder_link("abc")]
    folder.when_ready(lambda f: seen.append("again"))   # already ready: runs at once
    assert seen[-1] == "again"


def test_failed_folder_stops_waiting():
    folder = Folder()
    folder.fail(OSError("x"))
    assert folder.failed and folder.wait(5) is None
