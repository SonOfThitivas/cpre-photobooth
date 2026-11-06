import json
import threading
import time
import urllib.request

import pytest

from uploader.folder_pool import Folder, folder_link
from uploader.links import LinkServer, LinkStore


def ready(folder_id):
    folder = Folder()
    folder.resolve(folder_id)
    return folder


def test_unknown_key_gives_no_link():
    assert LinkStore().lookup(["nope"], timeout=0) == []


def test_a_ready_folder_gives_its_link():
    store = LinkStore()
    store.add("collage.jpg", ready("abc"))
    assert store.lookup(["collage.jpg"], timeout=0) == [folder_link("abc")]


def test_first_known_key_wins():
    store = LinkStore()
    store.add("file.jpg", ready("by-file"))
    assert store.lookup(["media-id", "file.jpg"], timeout=0) == [folder_link("by-file")]


def test_waits_for_a_folder_that_is_still_being_created():
    store, folder = LinkStore(), Folder()
    store.add("id", folder)
    threading.Timer(0.1, folder.resolve, args=("late",)).start()
    assert store.lookup(["id"], timeout=2) == [folder_link("late")]


def test_waits_for_a_key_that_is_added_a_moment_later():
    store = LinkStore()
    threading.Timer(0.1, store.add, args=("id", ready("abc"))).start()
    assert store.lookup(["id"], timeout=2) == [folder_link("abc")]


def test_gives_up_after_the_timeout():
    store = LinkStore()
    store.add("id", Folder())
    started = time.monotonic()
    assert store.lookup(["id"], timeout=0.2) == []
    assert time.monotonic() - started < 1


def test_failed_folder_gives_no_link():
    store, folder = LinkStore(), Folder()
    folder.fail(OSError("down"))
    store.add("id", folder)
    assert store.lookup(["id"], timeout=1) == []


def test_links_are_saved_and_loaded_again(tmp_path):
    path = tmp_path / "links.json"
    store = LinkStore(path)
    folder = Folder()
    store.add("id", folder)
    assert not path.exists()            # only ready folders are saved
    folder.resolve("abc")
    assert json.loads(path.read_text(encoding="utf-8")) == {"id": "abc"}
    assert LinkStore(path).lookup(["id"], timeout=0) == [folder_link("abc")]


def test_a_broken_links_file_starts_empty(tmp_path):
    path = tmp_path / "links.json"
    path.write_text("{not json", encoding="utf-8")
    assert LinkStore(path).lookup(["id"], timeout=0) == []


@pytest.fixture
def server():
    store = LinkStore()
    srv = LinkServer(store, port=0, wait_seconds=0.2)
    srv.start()
    yield store, f"http://127.0.0.1:{srv.port}"
    srv.stop()


def get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.load(resp)


def test_server_answers_by_media_id_or_file(server):
    store, base = server
    store.add("collage.jpg", ready("abc"))
    assert get_json(f"{base}/links?id=unknown&file=collage.jpg") == (200, [folder_link("abc")])


def test_server_answers_an_empty_list_when_unknown(server):
    _, base = server
    assert get_json(f"{base}/links?id=x") == (200, [])


def test_server_rejects_other_paths(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"{base}/other", timeout=5)
    assert error.value.code == 404
