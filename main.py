    # ref: https://ragug.medium.com/how-to-upload-files-using-the-google-drive-api-in-python-ebefdfd63eab
# Watchdog: https://github.com/gorakhargosh/watchdog

import os, qrcode, time, threading
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build, MediaFileUpload
from googleapiclient.errors import HttpError
from datetime import datetime
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer
from PIL import Image
from dotenv import load_dotenv

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

def get_credentials():
    """Return OAuth user credentials, refreshing or authorizing as needed.

    Files (owned by the signed-in user) are uploaded against that user's own
    storage quota, so this works on a free Gmail account where a service
    account cannot (a service account has no quota of its own).

    - First run with no token.json: opens a browser once to authorize, then
      saves the refresh token to token.json.
    - Later runs / other machines: load token.json and refresh silently. Copy
      token.json between booths to avoid re-authorizing on each machine.
    """
    creds = None
    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
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
    ).execute()

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
    created_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    print(f'Created File ID: {created_file["id"]}')
    return created_file["id"]

def share_file(real_file_id):                                   # Permission Modification
    """Batch permission modification.
    Args:
        real_file_id: file Id
        real_user: User ID
        real_domain: Domain of the user ID
    Prints modified permissions

    Load pre-authorized user credentials from the environment.
    TODO(developer) - See https://developers.google.com/identity
    for guides on implementing OAuth2 for the application.
    """
    # creds, _ = google.auth.default()

    try:
        # create drive api client
        service = build("drive", "v3", credentials=credentials)
        ids = []
        file_id = real_file_id

        def callback(request_id, response, exception):
            if exception:
            # Handle error
                print(exception)
            else:
                print(f"Request_Id: {request_id}")
                print(f'Permission Id: {response.get("id")}')
                ids.append(response.get("id"))

        # pylint: disable=maybe-no-member
        batch = service.new_batch_http_request(callback=callback)
        anyone_permission = {
            "type": "anyone",
            "role": "reader",
        }
        batch.add(
            service.permissions().create(
                fileId=file_id,
                body=anyone_permission,
                fields="id",
            )
        )
        batch.execute()

    except HttpError as error:
        print(f"An error occurred: {error}")
        ids = None

    return ids

def qrcode_generate(id, timestamp):                                    # QRcode Generator by shared link
    img = qrcode.make(id)
    file_name = f"./qr_code_folder/{timestamp}.png"
    img.save(file_name)
    print("QRcode was generated")
    # Show immediately after generating; the file is already fully written above,
    # so there is no need to wait (and no need to wait for the upload thread).
    Image.open(file_name).show()

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

            try:
                # Create a folder for this session and make it readable by anyone with the link.
                # The share link only needs the folder id, so we can show the QR immediately
                # while the (slower) image uploads run in parallel.
                folder_id = create_folder(timestamp_str, main_folder_id)
                share_file(folder_id)
                shared_link = f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"
                print(shared_link)
            except HttpError as error:
                print(f"An error occurred creating the session folder: {error}")
                continue

            def upload_batch(paths, fid):                      # P2: upload the session's images
                for path in paths:
                    try:
                        upload_file(path, os.path.basename(path), fid)
                    except HttpError as error:
                        print(f"An error occurred uploading {path}: {error}")
                print("Done!!!!")

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

    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()