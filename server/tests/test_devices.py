"""摄像头枚举：只认 by-id 的 -video-index0。"""

import os

from server.vision import devices


def _touch_link(root, name, target):
    (root / name).symlink_to(target)


def test_lists_only_video_index0(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, "BY_ID", tmp_path)
    _touch_link(tmp_path, "usb-Cam_A-video-index0", "/dev/video21")
    _touch_link(tmp_path, "usb-Cam_A-video-index1", "/dev/video22")
    _touch_link(tmp_path, "usb-Cam_B-video-index0", "/dev/video31")

    found = devices.list_cameras()

    assert [item["path"] for item in found] == [
        str(tmp_path / "usb-Cam_A-video-index0"),
        str(tmp_path / "usb-Cam_B-video-index0"),
    ]


def test_label_is_readable(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, "BY_ID", tmp_path)
    _touch_link(tmp_path, "usb-ALC-251223-CX_USB_Camera_01.00.00-video-index0", "/dev/video21")

    assert devices.list_cameras()[0]["label"] == "ALC-251223-CX USB Camera 01.00.00"


def test_missing_device_is_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, "BY_ID", tmp_path)
    _touch_link(tmp_path, "usb-Gone-video-index0", "/dev/definitely-absent")

    assert devices.list_cameras()[0]["online"] is False


def test_no_directory_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(devices, "BY_ID", tmp_path / "nope")
    assert devices.list_cameras() == []
