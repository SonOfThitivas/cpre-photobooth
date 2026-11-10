# Tests for run.py's camera sync. Run from the repo root (run.py needs no packages):
#     uv run --no-project --with pytest python -m pytest tests
import json

import pytest

import run

CAMERA_VARS = [name for setting in run.CAMERA_SETTINGS for name in setting[:2]]


@pytest.fixture(autouse=True)
def clean_environ(monkeypatch):
    for name in CAMERA_VARS:
        monkeypatch.delenv(name, raising=False)


def webcam(width=1920, height=1080, fps=30, device="OBS Virtual Camera"):
    return {"enabled": True, "backend_config": {
        "orientation": "1: 0°", "backend_type": "WebcamPyav", "device_identifier": device,
        "cam_resolution_width": width, "cam_resolution_height": height, "cam_framerate": fps}}


def write_config(path, backends, stills=0, video=0, multicam=0, newline="\n"):
    config = {"cameras": {"index_backend_stills": stills, "index_backend_video": video,
                          "index_backend_multicam": multicam, "group_backends": backends}}
    path.write_bytes(json.dumps(config, indent=2, ensure_ascii=False)
                     .replace("\n", newline).encode("utf-8"))
    return path


def read_backends(path):
    return [b["backend_config"] for b in json.loads(path.read_text(encoding="utf-8"))["cameras"]["group_backends"]]


MODE_720 = {"cam_resolution_width": 1280, "cam_resolution_height": 720, "cam_framerate": 30}


# --- camera_mode -------------------------------------------------------------------------
def test_camera_mode_defaults_match_gesture_trigger():
    assert run.camera_mode({}) == MODE_720


def test_camera_mode_reads_camera_vars():
    env = {"CAMERA_WIDTH": "1920", "CAMERA_HEIGHT": "1080", "CAMERA_FPS": "60"}
    assert run.camera_mode(env) == {"cam_resolution_width": 1920, "cam_resolution_height": 1080,
                                    "cam_framerate": 60}


def test_camera_mode_falls_back_to_old_gesture_names():
    env = {"GESTURE_WIDTH": "640", "GESTURE_HEIGHT": "480", "CAMERA_HEIGHT": "360"}
    assert run.camera_mode(env) == {"cam_resolution_width": 640, "cam_resolution_height": 360,
                                    "cam_framerate": 30}


def test_camera_mode_real_environment_wins_over_env_file(monkeypatch):
    monkeypatch.setenv("CAMERA_FPS", "15")
    assert run.camera_mode({"CAMERA_FPS": "60"})["cam_framerate"] == 15


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "12.5"])
def test_camera_mode_rejects_bad_values(raw):
    with pytest.raises(ValueError, match="CAMERA_WIDTH"):
        run.camera_mode({"CAMERA_WIDTH": raw})


def test_camera_mode_error_names_the_old_variable_it_came_from():
    with pytest.raises(ValueError, match="GESTURE_FPS"):
        run.camera_mode({"GESTURE_FPS": "fast"})


def test_camera_env_hands_resolved_values_to_services():
    assert run.camera_env(MODE_720) == {"CAMERA_WIDTH": "1280", "CAMERA_HEIGHT": "720",
                                        "CAMERA_FPS": "30"}


# --- sync_camera -------------------------------------------------------------------------
def test_sync_writes_mode_and_backs_up(tmp_path):
    path = write_config(tmp_path / "config.json", [webcam()])
    original = path.read_bytes()

    changes, skipped = run.sync_camera(path, MODE_720)

    assert changes == ["WebcamPyav 1920x1080@30 -> 1280x720@30"]
    assert skipped == []
    backend = read_backends(path)[0]
    assert {k: backend[k] for k in MODE_720} == MODE_720
    assert backend["orientation"] == "1: 0°"                     # other fields untouched
    backups = list(tmp_path.glob("config.json_backup-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == original
    assert not (tmp_path / "config.json.tmp").exists()


def test_sync_without_change_does_not_touch_file(tmp_path):
    path = write_config(tmp_path / "config.json", [webcam(1280, 720, 30)])
    before = path.read_bytes()

    assert run.sync_camera(path, MODE_720) == ([], [])
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("config.json_backup-*"))


def test_sync_keeps_layout_and_crlf_line_endings(tmp_path):
    path = write_config(tmp_path / "config.json", [webcam(1280, 720, 30)], newline="\r\n")
    expected = path.read_bytes().decode("utf-8")
    write_config(path, [webcam()], newline="\r\n")

    run.sync_camera(path, MODE_720)

    assert path.read_bytes().decode("utf-8") == expected


def test_sync_changes_only_backends_in_use(tmp_path):
    unused = webcam(device="Integrated Webcam")
    path = write_config(tmp_path / "config.json", [unused, webcam(), webcam(640, 480)],
                        stills=1, video=2, multicam=1)

    changes, _ = run.sync_camera(path, MODE_720)

    assert len(changes) == 2
    first, second, third = read_backends(path)
    assert first["cam_resolution_width"] == 1920                 # not used: left alone
    assert second["cam_resolution_width"] == third["cam_resolution_width"] == 1280


def test_sync_skips_disabled_backend(tmp_path):
    disabled = webcam()
    disabled["enabled"] = False
    path = write_config(tmp_path / "config.json", [disabled])

    assert run.sync_camera(path, MODE_720) == ([], [])
    assert read_backends(path)[0]["cam_resolution_width"] == 1920


def test_sync_skips_backend_without_resolution_fields(tmp_path):
    dslr = {"enabled": True, "backend_config": {"backend_type": "Gphoto2"}}
    path = write_config(tmp_path / "config.json", [dslr])
    before = path.read_bytes()

    assert run.sync_camera(path, MODE_720) == ([], ["Gphoto2"])
    assert path.read_bytes() == before


def test_camera_devices_still_lists_active_devices():
    config = {"cameras": {"index_backend_stills": 0, "index_backend_video": 1,
                          "group_backends": [webcam(), webcam(device="Integrated Webcam")]}}
    assert run.camera_devices(config) == {"OBS Virtual Camera", "Integrated Webcam"}
