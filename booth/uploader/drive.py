"""Google Drive access: OAuth credentials, the Drive calls we use, and the one worker thread
that makes them.

googleapiclient isn't thread-safe, so every Drive call runs on DriveWorker's single thread,
one job at a time, in the order submitted. Importing this module has no side effects; call
prefer_ipv4() and DriveClient.connect() from the entry point.
"""
import os
import queue
import socket
import threading
import traceback

import httplib2
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import MediaFileUpload, build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]

# Each Drive request is retried this many times on network errors, 5xx and rate limits, with
# googleapiclient's exponential backoff (~2, 4, 8, 16 s). A blip on the venue network then
# delays the upload instead of losing the session.
DRIVE_RETRIES = 4
# What a Drive call can still raise once its retries are used up: an API error, a network error
# (TimeoutError, ConnectionError, ... are OSErrors), an httplib2 error, or a failed token refresh.
DRIVE_ERRORS = (HttpError, OSError, httplib2.HttpLib2Error, GoogleAuthError)

FOLDER_MIME = "application/vnd.google-apps.folder"


def prefer_ipv4():
    """Connect over IPv4 first. Some networks hand out IPv6 addresses that can't reach the
    internet. Windows tries IPv6 first, and httplib2 (under the Drive client) gives up on the
    first address that times out instead of trying the next one -- so every Drive call failed
    with WinError 10060 after ~21 s while browsers and curl, which fall back to IPv4, worked
    fine. Keeps IPv6 as the fallback, so an IPv6-only network still works."""
    original = socket.getaddrinfo

    def getaddrinfo_ipv4_first(*args, **kwargs):
        return sorted(original(*args, **kwargs), key=lambda info: info[0] != socket.AF_INET)
    socket.getaddrinfo = getaddrinfo_ipv4_first


def get_credentials(token_file, client_secret_file):
    """Return OAuth user credentials, refreshing or authorizing as needed.

    Files (owned by the signed-in user) are uploaded against that user's own storage quota,
    so this works on a free Gmail account where a service account cannot (a service account
    has no quota of its own).

    - First run with no token.json: opens a browser once to authorize, then saves the refresh
      token to token.json.
    - Later runs / other machines: load token.json and refresh silently. Copy token.json
      between booths to avoid re-authorizing on each machine.
    - If Google rejects the refresh token (invalid_grant: revoked, password changed, or
      expired because the consent screen is still in Testing), fall back to the browser flow
      and overwrite token.json.
    """
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                print(f"Saved token rejected ({e}); re-authorizing in the browser...")
                creds = None
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())
    return creds


class DriveClient:
    """The Drive calls the uploader makes. Each raises one of DRIVE_ERRORS once its retries
    are used up. Use from the DriveWorker thread only."""

    def __init__(self, service):
        self._service = service

    @classmethod
    def connect(cls, token_file, client_secret_file):
        return cls(build("drive", "v3", credentials=get_credentials(token_file, client_secret_file)))

    def create_folder(self, name, parent_id):
        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id] if parent_id else []}
        folder = self._service.files().create(body=body, fields="id").execute(num_retries=DRIVE_RETRIES)
        print(f"Created Folder ID: {folder['id']}")
        return folder["id"]

    def share(self, file_id):
        """Make a file/folder readable by anyone with the link."""
        permission = self._service.permissions().create(
            fileId=file_id, body={"type": "anyone", "role": "reader"}, fields="id",
        ).execute(num_retries=DRIVE_RETRIES)
        return permission.get("id")

    def rename(self, file_id, name):
        self._service.files().update(fileId=file_id, body={"name": name}, fields="id") \
            .execute(num_retries=DRIVE_RETRIES)

    def find_folders(self, name, parent_id):
        """Ids of the (not trashed) folders called name directly inside parent_id."""
        escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        query = f"name = '{escaped}' and mimeType = '{FOLDER_MIME}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        result = self._service.files().list(q=query, fields="files(id)", pageSize=100) \
            .execute(num_retries=DRIVE_RETRIES)
        return [f["id"] for f in result.get("files", [])]

    def upload(self, file_path, parent_id):
        """Upload a single file into parent_id and return its id."""
        body = {"name": os.path.basename(file_path), "parents": [parent_id] if parent_id else []}
        mimetype = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
        media = MediaFileUpload(file_path, mimetype=mimetype)
        created = self._service.files().create(body=body, media_body=media, fields="id") \
            .execute(num_retries=DRIVE_RETRIES)
        print(f"Created File ID: {created['id']}")
        return created["id"]


class DriveWorker:
    """One thread that runs Drive jobs one at a time, in the order they were submitted."""

    def __init__(self):
        self._jobs = queue.Queue()
        threading.Thread(target=self._run, name="drive-worker", daemon=True).start()

    def submit(self, job):
        self._jobs.put(job)

    def wait_idle(self):
        """Block until every submitted job has run."""
        self._jobs.join()

    def _run(self):
        while True:
            job = self._jobs.get()
            try:
                job()
            except Exception:       # a bug in one job must not stop the uploads after it
                traceback.print_exc()
            finally:
                self._jobs.task_done()
