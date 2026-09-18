# ref: https://ragug.medium.com/how-to-upload-files-using-the-google-drive-api-in-python-ebefdfd63eab
# Watchdog: https://github.com/gorakhargosh/watchdog

import os, multiprocessing, qrcode, time
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
# PICS_PER_SESSION must equal the number of images produced per capture session.
watch_dir = os.getenv("WATCH_DIR", "./photobooth-data/media/processed_full")
pics_per_session = int(os.getenv("PICS_PER_SESSION", "2"))

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

new_pics_paths = []                                             # contain images file paths

class MyEventHandler(FileSystemEventHandler):                   # Track the files that created in the folder
    def on_any_event(self, event: FileSystemEvent) -> None:
        print(event)
        print(event.src_path)
        new_pics_paths.append(event.src_path)
        # print(len(new_pics_paths))

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

def upload_file(file_path, indx, parent_folder_id=None):        # Upload images to google drive
    """Create a folder in Google Drive and return its ID."""
    file_metadata = {
        'name': indx,
        'parents': [parent_folder_id] if parent_folder_id else []  # ID of the folder where you want to upload
    }
    media = MediaFileUpload(file_path, mimetype="image/jpeg")
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
    time.sleep(5) 
    img = Image.open(file_name)
    img.show()

if __name__ == '__main__':
    # important variables
    n_pic = 0
    start = False
    
    # file created detector
    event_handler = MyEventHandler()
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=True, event_filter=[FileCreatedEvent])
    observer.start()
    
    try:
        while True:
            time.sleep(0.05)
            
            # timestamp
            timestamp = datetime.now()
            timestamp_str = timestamp.strftime("%d-%m-%Y_%H-%M-%S")
                
            # First picture created
            if( start == False) and len(new_pics_paths) > 0:
                start = True                # start condition
                # Create folder
                folder_id = create_folder(timestamp_str, main_folder_id)
                print(folder_id, type(folder_id))
                # modify permision to anyone
                print(share_file(folder_id))
                # Shared link
                shared_link = f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"
                print(shared_link)
                    
            elif (start == True) and len(new_pics_paths) == pics_per_session:
                time.sleep(1)
                
                for i in range(len(new_pics_paths)):
                    multiprocessing.Process(
                        target=upload_file,
                        args=(new_pics_paths.pop(), f"{n_pic+1}.png", folder_id)).start()
                    n_pic += 1
                
                start = False
                print("Done!!!!")

                # show qrcode
                multiprocessing.Process(
                    target=qrcode_generate,
                    args=(shared_link, timestamp_str)
                ).start()
                
                n_pic = 0
        
    finally:
        observer.stop()
        observer.join() 