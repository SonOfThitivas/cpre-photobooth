"""The photobooth-app plugin against a real link server."""
import importlib.util
from pathlib import Path

import pytest

from uploader.folder_pool import Folder, folder_link
from uploader.links import LinkServer, LinkStore

PLUGIN = Path(__file__).parents[1] / "photobooth-data" / "plugins" / "drive_share" / "drive_share.py"


@pytest.fixture
def plugin_module():
    spec = importlib.util.spec_from_file_location("drive_share_under_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def store_and_port():
    store = LinkStore()
    server = LinkServer(store, port=0, wait_seconds=0.2)
    server.start()
    yield store, server.port
    server.stop()


def test_class_name_matches_photobooth_app_naming(plugin_module):
    # photobooth-app instantiates "drive_share".title().replace("_", "")
    assert hasattr(plugin_module, "drive_share".title().replace("_", ""))


def test_returns_the_session_link(plugin_module, store_and_port, monkeypatch):
    store, port = store_and_port
    monkeypatch.setenv("LINK_SERVER_PORT", str(port))
    folder = Folder()
    folder.resolve("abc")
    store.add("0c6f-uuid", folder)
    plugin = plugin_module.DriveShare()
    links = plugin.get_share_links(filepath_local=Path("media/processed_full/x.jpg"), identifier="0c6f-uuid")
    assert links == [folder_link("abc")]


def test_finds_the_link_by_file_name(plugin_module, store_and_port, monkeypatch):
    store, port = store_and_port
    monkeypatch.setenv("LINK_SERVER_PORT", str(port))
    folder = Folder()
    folder.resolve("abc")
    store.add("x.jpg", folder)
    links = plugin_module.DriveShare().get_share_links(filepath_local=Path("media/x.jpg"), identifier="other")
    assert links == [folder_link("abc")]


def test_no_uploader_means_no_qr_code(plugin_module, monkeypatch):
    monkeypatch.setenv("LINK_SERVER_PORT", "9")          # nothing listens there
    assert plugin_module.DriveShare().get_share_links(filepath_local=Path("x.jpg"), identifier="id") == []
