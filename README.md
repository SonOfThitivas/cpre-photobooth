<h1 align="center"><img src="https://raw.githubusercontent.com/photobooth-app/photobooth-app/main/assets/logo/logo-text-blue-transparent.png" alt="photobooth app logo" /></h1>

# 📸 Photobooth-app on Windows with Python 🐍

## Desription
This repository contains photobooth sources that were used in Oct. 3-5 2024 ECE-KMUTNB Open House.

## 📸 Quick Start ⚙️

### Equipments
- [Latest stable Python](https://www.python.org/downloads/)
- [libjpeg-turbo-3.0.4-vc64.exe](https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.0.4/libjpeg-turbo-3.0.4-vc64.exe)
- [Latest ffmpeg-release](https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z)
- Follow this article to initiate [Google Drive API](https://ragug.medium.com/how-to-upload-files-using-the-google-drive-api-in-python-ebefdfd63eab) and download JSON file.\

Move these folders and files to your directory.

### ⚒️ Installation 💻
- Download and create virtual environment.
```bash
python -m pip install virtualenv
python -m virtualenv venv
```
- Activate virtual environment. (for Windows)
```bash
.\venv\Scripts\activate
```
- Install Python package with requirements.txt
```bash
python -m pip install -r requirements.txt
```
- Create .env contains environment variables.
```bash
echo > .env
```
in .env
```.env
MAIN_FOLDER_ID = {your google drive parent folder id}
SERVICE_ACCOUNT_FILE_PATH = {your JSON file path}
```

### 📁 Start
- Open two terminals and execute these to start. 

Terminal 1 (photoboot-data)
```bash
.\venv\Scripts\activate
cd photobooth-data
photobooth.exe
```
open browser with [localhost:8000](localhost:8000). 

Terminal 2 (main.py)
```bash
.\venv\Scripts\activate
py main.py
```
## Gallery
<img src="https://github.com/user-attachments/assets/ad9175a6-ff74-4718-ab42-42a0795a9305" width="720" height="480" />
<img src="https://github.com/user-attachments/assets/58e4fcdf-985a-4a1d-a69a-211f600a8d1e" width="720" height="480" />
<img src="https://github.com/user-attachments/assets/4a3883d6-90f3-4983-9eea-c7e68bc5a336" width="720" height="480" />
<img src="https://github.com/user-attachments/assets/5ec3a248-c8c7-49f9-bfab-4f43195e7f55" width="720" height="480" />
<img src="https://github.com/user-attachments/assets/7decee47-9dad-471e-8894-c6d984953809" width="720" height="480" />

For more information
## 📖 Reference
photobooth-app
- [github](https://github.com/photobooth-app/photobooth-app.git)
- [Documentation](https://photobooth-app.org/)

