<h1 align="center"><img src="https://raw.githubusercontent.com/photobooth-app/photobooth-app/main/assets/logo/logo-text-blue-transparent.png" alt="photobooth app logo" /></h1>

# 📸 Photobooth-app on Windows with Python 🐍

## ⚒️ Installation 💻
To use the photobooth first install following system dependencies:

- [Latest stable Python](https://www.python.org/downloads/) Please use the link, the Microsoft Store version is not recommended.
  
- [Latest libjpeg-turbo-X.X.X-vc64](https://github.com/libjpeg-turbo/libjpeg-turbo/releases): Ensure to use the -vc64 variant and unpack it to C: so it will be automatically detected.

  For example version [libjpeg-turbo-3.0.4-vc64.exe](https://github.com/libjpeg-turbo/libjpeg-turbo/releases/download/3.0.4/libjpeg-turbo-3.0.4-vc64.exe)

- [Latest ffmpeg-release](https://ffmpeg.org/download.html): Choose the windows releases from gyan.dev. Look for the release builds, for example ffmpeg-release-full.7z. Download the folder, unpack it to C: and add the path to the executable ffmpeg.exe to system path's. Check that in a CLI you can start ffmpeg. If it starts, photobooth can use it also. If you don't need the video feature, you don't need to install ffmpeg.

  By the way, you can [click here](https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z) to download.

## ⚒️ Install photobooth app 📸
Several ways to install:
1. Method A: Install using pipx (easiest, recommended for Bookworm)
2. Method B: Install using venv
3. Method C: Install globally (recommended for Windows)

But I will recommend you to take Method B.</p>

### Method B: Install using venv
Use the following commands to install in a virtual environment:

Create empty directory & change to new dir:
```powershell
mkdir ~/photobooth-app && cd ~/photobooth-app
```
Initialize a new venv called myenv. Allow import of system-site-packages as picamera2 is globally installed via apt in system-site:
```powershell
python -m venv --system-site-packages myenv
```
Activate the newly created env
```powershell
source myenv/bin/activate
```
Install photobooth-app
```powershell
python -m pip install --prefer-binary photobooth-app
```
Note: --prefer-binary is added to avoid compiling opencv and instead prefer the wheel which installs within minutes instead hours.

## 📁 First start
### Create data folder
The photobooth-app automatically uses the current folder as data folder. All images, logs and config files will be stored in this folder.

```powershell
mkdir ~/photobooth-data
```
Start the app
```powershell
cd ~/photobooth-data
```
Following only if installed via venv method
```powershell
source ~/photobooth-app/myenv/bin/activate
```
Start app. Current dir will be used as working-directory!
```powershell
photobooth
```
Browse to [http://localhost:8000](http://localhost:8000/) and see if the app is working properly. Per default the app uses a generated image and displays a timer only. No camera is started at this point. You need to continue setting up the cameras.

**NOTE** > You must run `photobooth` in the folder `photobooth-app` to store the data correctly

## ⚙️ Camera Setup 📸
The photobooth app supports cameras utilizing multiple backends:

- picamera2 backend supports Raspberry Pi Camera Modules
- gphoto2 backend supports DSLR cameras on Linux platforms
- digicamcontrol backend supports DSLR cameras on Windows platforms (not yet implemented!)
- opencv2 backend supports USB webcameras on Linux and Windows platforms
- v4l2 backend supports USB webcameras on Linux
Two backends can be used simultaneously in hybrid mode. The first backend is used as main backend to capture high quality still images. The second backend is used as live backend to stream video preview only.

In this example, we will use **opencv2** backend supports **USB webcameras** on **Linux** and **Windows** platforms

### Webcam
USB-webcams are integrated via two backends:

- Opencv2 (Linux/Windows) and
- v4l2 (Linux only).
On Linux prefer v4l2 backend because it is more efficient in directly streaming MJPG data instead image frames like the opencv2 implementation.

To use the webcam choose opencv2 or v4l2 as backend.

Both backends use a camera device index to open the camera. To find which indexes are available on your system issue the following commands.

check v4l2 indexes:
```powershell
python -c "from photobooth.services.backends.webcamv4l import *; print(available_camera_indexes())"
```
check opencv2 indexes:
```powershell
python -c "from photobooth.services.backends.webcamcv2 import *; print(available_camera_indexes())"
```
The command returns an array of indexes for which a webcam was detected.

Now finish setup:

- Set the index in the [admin center](http://localhost/#/admin/config) (Default password: 0000), config, tab backends.
- set the backend (cv2 or v4l) as main backend
- Consider changing the resolution requested from the camera on common tab.

---

## 📖 Reference

photobooth-app
- [github](https://github.com/photobooth-app/photobooth-app.git)
- [Documentation](https://photobooth-app.org/)

