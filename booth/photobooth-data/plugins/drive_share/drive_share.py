"""photobooth-app user plugin: the QR code of an item links to its Google Drive session folder.

photobooth-app loads every package in ./plugins/ under its working directory (we run it from
booth/photobooth-data) and registers the class named after the package: drive_share ->
DriveShare. When the frontend shows an item (the presenter after a capture, or the gallery)
it asks for the item's share links once; photobooth-app calls get_share_links on every plugin
and the frontend draws each link as a QR code.

This plugin holds no Drive code. It asks the uploader (booth/main.py) for the link over
http://127.0.0.1:<LINK_SERVER_PORT>/links, by media id and file name. If the uploader isn't
running or doesn't answer in time, the item simply shows no QR code; the booth never hangs.
"""
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

import pluggy

hookimpl = pluggy.HookimplMarker("photobooth-app")
logger = logging.getLogger(__name__)

# Slightly longer than the uploader's LINK_WAIT_SECONDS (default 2), which it may spend waiting
# for a session folder that is still being created.
TIMEOUT_SECONDS = 3.5


def _link_server_port():
    """LINK_SERVER_PORT from the environment, else from the repo's .env, else 8765."""
    port = os.environ.get("LINK_SERVER_PORT")
    if not port:
        try:
            from dotenv import dotenv_values, find_dotenv
            port = dotenv_values(find_dotenv(usecwd=True)).get("LINK_SERVER_PORT")
        except ImportError:
            port = None
    return int(port or 8765)


class DriveShare:
    def __init__(self):
        self.url = f"http://127.0.0.1:{_link_server_port()}/links"

    @hookimpl(tryfirst=True)
    def get_share_links(self, filepath_local: Path, identifier) -> list[str]:
        query = urllib.parse.urlencode({"id": str(identifier), "file": Path(filepath_local).name})
        try:
            with urllib.request.urlopen(f"{self.url}?{query}", timeout=TIMEOUT_SECONDS) as resp:
                links = json.load(resp)
        except (OSError, ValueError) as error:
            logger.warning(f"drive_share: no Drive link for {identifier} ({error!r}). Is booth/main.py running?")
            return []
        if not links:
            logger.info(f"drive_share: the uploader has no Drive link for {identifier}")
        return [link for link in links if isinstance(link, str)] if isinstance(links, list) else []
