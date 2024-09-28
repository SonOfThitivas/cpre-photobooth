# ref: https://ragug.medium.com/how-to-upload-files-using-the-google-drive-api-in-python-ebefdfd63eab
# Watchdog: https://github.com/gorakhargosh/watchdog

import os
from google.oauth2 import service_account
from googleapiclient.discovery import build, MediaFileUpload
from googleapiclient.errors import HttpError
from datetime import datetime
import qrcode
import time
from watchdog.events import FileSystemEvent, FileSystemEventHandler, FileCreatedEvent
from watchdog.observers import Observer
from PIL import Image

# Define the Google Drive API scopes and service account file path
SCOPES = ['https://www.googleapis.com/auth/drive']
SERVICE_ACCOUNT_FILE = "possible-dream-436907-s2-15ffd7fa994a.json"

# Create credentials using the service account file
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)

# Build the Google Drive service
drive_service = build('drive', 'v3', credentials=credentials)

new_pics_paths = []

class MyEventHandler(FileSystemEventHandler):
    def on_any_event(self, event: FileSystemEvent) -> None:
        print(event)
        print(event.src_path)
        new_pics_paths.append(event.src_path)
        print(len(new_pics_paths))

def create_folder(folder_name, parent_folder_id=None):
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

def upload_file(file_path, indx, parent_folder_id=None):
    """Create a folder in Google Drive and return its ID."""
    file_metadata = {
        'name': indx,
        'parents': [parent_folder_id] if parent_folder_id else []  # ID of the folder where you want to upload
    }
    media = MediaFileUpload(file_path, mimetype="image/jpeg")
    created_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

    print(f'Created File ID: {created_file["id"]}')
    return created_file["id"]

def share_file(real_file_id):
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

def qrcode_generate(id):
    img = qrcode.make(id)
    file_name = f"./qr_code_folder/{timestamp_str}.png"
    img.save(file_name)
    print("QRcode was generated")
    
    img_gen = Image.open(file_name)
    img_gen.show()

if __name__ == '__main__':
    # file created detector
    event_handler = MyEventHandler()
    observer = Observer()
    observer.schedule(event_handler, "./photobooth-data/media/processed/full", recursive=True, event_filter=[FileCreatedEvent])
    observer.start()
    
    try:
        while True:
            # timestamp
            timestamp = datetime.now()
            timestamp_str = timestamp.strftime("%d-%m-%Y_%H-%M-%S")
            
            time.sleep(1)
            
            # 5 pics were stored
            if len(new_pics_paths) == 5:
                tmp = new_pics_paths.copy()
                new_pics_paths.clear()
                
                # Create folder
                folder_id = create_folder(timestamp_str, "1Yg6dNUy126viCItc3r0pTJu_SjdcutBw")
                print(folder_id, type(folder_id))
                
                # Upload images
                for i, src_path in enumerate(tmp):
                    upload_file(src_path, i+1, folder_id)
                
                print(share_file(folder_id))

                # Shared link
                shared_link = f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing"
                print(shared_link)
                
                # QR code generator
                qrcode_generate(shared_link)
        
    finally:
        observer.stop()
        observer.join() 