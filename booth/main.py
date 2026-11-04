    # ref: https://ragug.medium.com/how-to-upload-files-using-the-google-drive-api-in-python-ebefdfd63eab
# Watchdog: https://github.com/gorakhargosh/watchdog

import logging, os, qrcode, socket, time, threading
import httplib2
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.auth.exceptions import GoogleAuthError, RefreshError
from googleapiclient.discovery import build, MediaFileUpload
from googleapiclient.errors import HttpError
from datetime import datetime
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer
from PIL import Image
from dotenv import load_dotenv

# Connect over IPv4 first. Some networks hand out IPv6 addresses that can't reach the internet.
# Windows tries IPv6 first, and httplib2 (under the Drive client) gives up on the first address
# that times out instead of trying the next one -- so every Drive call failed with WinError
# 10060 after ~21 s while browsers and curl, which fall back to IPv4, worked fine.
# Keeps IPv6 as the fallback, so an IPv6-only network still works.
_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4_first(*args, **kwargs):
    return sorted(_getaddrinfo(*args, **kwargs), key=lambda info: info[0] != socket.AF_INET)
socket.getaddrinfo = _getaddrinfo_ipv4_first

# Show library warnings, e.g. googleapiclient's "Sleeping ... before retry" lines.
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# Load the .env file
load_dotenv()
main_folder_id = os.getenv("MAIN_FOLDER_ID")
# OAuth credential files (paths overridable via .env). token.json is portable:
# copy it to other booths and they run headlessly with no browser step.
token_file = os.getenv("TOKEN_FILE", "token.json")
client_secret_file = os.getenv("CLIENT_SECRET_FILE", "client_secret.json")

# Pipeline coupling to photobooth-app's output (override in .env per app version/collage).
# WATCH_DIR must be the folder photobooth-app writes finished images into.
watch_dir = os.getenv("WATCH_DIR", "./photobooth-data/media/processed_full")
# A capture session writes several files in a burst (in collage mode: one file per
# individual shot plus the final collage). Instead of relying on an exact count
# (which breaks whenever the collage definition changes and races the folder-creation
# network calls), we treat a burst as finished once no new file has appeared for
# SESSION_QUIET_SECONDS, then upload everything collected in that burst.
session_quiet_seconds = float(os.getenv("SESSION_QUIET_SECONDS", "3"))

# Define the Google Drive API scope
SCOPES = ['https://www.googleapis.com/auth/drive']

# Each Drive request is retried this many times on network errors, 5xx and rate limits, with
# googleapiclient's exponential backoff (~2, 4, 8, 16 s). A blip on the venue network then
# delays the QR instead of losing the session.
DRIVE_RETRIES = 4
# What a Drive call can still raise once its retries are used up: an API error, a network error
# (TimeoutError, ConnectionError, ... are OSErrors), an httplib2 error, or a failed token refresh.
DRIVE_ERRORS = (HttpError, OSError, httplib2.HttpLib2Error, GoogleAuthError)

def get_credentials():
    """Return OAuth user credentials, refreshing or authorizing as needed.

    Files (owned by the signed-in user) are uploaded against that user's own
    storage quota, so this works on a free Gmail account where a service
    account cannot (a service account has no quota of its own).

    - First run with no token.json: opens a browser once to authorize, then
      saves the refresh token to token.json.
    - Later runs / other machines: load token.json and refresh silently. Copy
      token.json between booths to avoid re-authorizing on each machine.
    - If Google rejects the refresh token (invalid_grant: revoked, password
      changed, or expired because the consent screen is still in Testing),
      fall back to the browser flow and overwrite token.json.
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

# Create OAuth credentials and build the Google Drive service
credentials = get_credentials()
drive_service = build('drive', 'v3', credentials=credentials)

new_pics_paths = []                                            # contain images file paths
last_event_time = 0.0                                           # monotonic time of the most recent image file

class MyEventHandler(FileSystemEventHandler):                   # Track the files that created in the folder
    def on_any_event(self, event: FileSystemEvent) -> None:
        global last_event_time
        print(f"New file: {event.src_path}")
        new_pics_paths.append(event.src_path)
        last_event_time = time.monotonic()

def create_folder(folder_name, parent_folder_id=None):          # Create new specific folders
    """Create a folder in Google Drive and return its ID."""
    folder_metadata = {
        'name': folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        'parents': [parent_folder_id] if parent_folder_id else []
    }

    created_folder = drive_service.files().create(
        body=folder_metadata,
        fields='id'
    ).execute(num_retries=DRIVE_RETRIES)

    print(f'Created Folder ID: {created_folder["id"]}')
    return created_folder["id"]

def upload_file(file_path, name=None, parent_folder_id=None):   # Upload images to google drive
    """Upload a single file to Google Drive and return its ID."""
    file_metadata = {
        'name': name or os.path.basename(file_path),
        'parents': [parent_folder_id] if parent_folder_id else []  # ID of the folder where you want to upload
    }
    mimetype = "image/png" if file_path.lower().endswith(".png") else "image/jpeg"
    media = MediaFileUpload(file_path, mimetype=mimetype)
    created_file = drive_service.files().create(
        body=file_metadata, media_body=media, fields='id').execute(num_retries=DRIVE_RETRIES)

    print(f'Created File ID: {created_file["id"]}')
    return created_file["id"]

def share_file(real_file_id):                                   # Permission Modification
    """Make a file/folder readable by anyone with the link and return the permission id.

    Raises (one of DRIVE_ERRORS) if that fails, so a QR is never shown for a folder
    guests can't open.
    """
    permission = drive_service.permissions().create(
        fileId=real_file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute(num_retries=DRIVE_RETRIES)
    print(f'Permission Id: {permission.get("id")}')
    return permission.get("id")

def qrcode_generate(id, timestamp):                                    # QRcode Generator by shared link
    img = qrcode.make(id)
    file_name = f"./qr_code_folder/{timestamp}.png"
    img.save(file_name)
    print("QRcode was generated")
    # Show immediately after generating; the file is already fully written above,
    # so there is no need to wait (and no need to wait for the upload thread).
    Image.open(file_name).show()

def upload_batch(paths, fid):                                   # P2: upload the session's images
    failed = []
    for path in paths:
        try:
            upload_file(path, os.path.basename(path), fid)
        except DRIVE_ERRORS as error:
            print(f"Could not upload {path}: {error!r}")
            failed.append(path)
    if failed:
        print(f"Uploaded {len(paths) - len(failed)} of {len(paths)} file(s); the rest stay on disk.")
    else:
        print("Done!!!!")

def process_session(batch, timestamp_str):
    """Create + share the session's Drive folder, show its QR, upload the images.

    Network or Drive failures are logged, never raised: the uploader keeps running for the
    next session, and the photos stay on disk in WATCH_DIR.
    """
    try:
        # Create a folder for this session and make it readable by anyone with the link.
        # The share link only needs the folder id, so we can show the QR immediately
        # while the (slower) image uploads run in parallel.
        folder_id = create_folder(timestamp_str, main_folder_id)
        share_file(folder_id)
    except DRIVE_ERRORS as error:
        print(f"Could not create the session folder on Drive: {error!r}")
        print(f"Session {timestamp_str} NOT uploaded; its photos stay on disk:")
        for path in batch:
            print(f"  {path}")
        return
    shared_link = f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"
    print(shared_link)

    # P1: generate + show the QR (background), P2: upload the images (background).
    # They run concurrently; we wait for the uploads before handling the next
    # session so a single Drive client is never used from two threads at once.
    qr_thread = threading.Thread(
        target=qrcode_generate, args=(shared_link, timestamp_str), daemon=True)
    upload_thread = threading.Thread(
        target=upload_batch, args=(batch, folder_id), daemon=True)
    qr_thread.start()
    upload_thread.start()
    upload_thread.join()

if __name__ == '__main__':
    os.makedirs("qr_code_folder", exist_ok=True)

    # file created detector
    event_handler = MyEventHandler()
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=True, event_filter=[FileCreatedEvent])
    observer.start()
    print(f"Watching {watch_dir} for new photos (quiet window {session_quiet_seconds}s)...")

    try:
        while True:
            time.sleep(0.1)

            # Nothing captured yet, or the burst is still being written: keep waiting.
            if not new_pics_paths:
                continue
            if time.monotonic() - last_event_time < session_quiet_seconds:
                continue

            # Burst has settled -> take a snapshot of this session and reset for the next.
            batch = list(new_pics_paths)
            new_pics_paths.clear()

            timestamp_str = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
            print(f"Session complete: {len(batch)} file(s) -> {timestamp_str}")
            process_session(batch, timestamp_str)

    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()