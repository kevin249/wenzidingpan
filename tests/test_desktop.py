"""平台适配：Wayland 回退、macOS 激活策略、启动提示。"""

from __future__ import annotations

import socket

import pytest

from stockwidget import desktop


@pytest.fixture()
def x11_socket_dir(tmp_path, monkeypatch):
    """把探测目录挪到临时目录，测试里可以真起一个 unix socket 冒充 X 服务器。"""
    monkeypatch.setattr(desktop, "X11_SOCKET_DIR", str(tmp_path))
    return tmp_path


def _listen(path) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    return server


def test_wayland_falls_back_to_xwayland_when_it_is_really_there(
    monkeypatch, x11_socket_dir
):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    server = _listen(x11_socket_dir / "X0")
    try:
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        assert desktop.choose_qt_platform(env) == "xcb"
        # :0.1 这种带屏幕号的写法同样指向 X0
        assert desktop.choose_qt_platform({**env, "DISPLAY": ":0.1"}) == "xcb"
    finally:
        server.close()


def test_wayland_stays_on_wayland_when_xwayland_is_missing(monkeypatch, x11_socket_dir):
    """DISPLAY 可能是继承来的陈旧值——强行选 xcb 而 X 不在，Qt 会直接 abort。"""
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.choose_qt_platform({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}) is None
    # 远程 DISPLAY 不认：连不了 unix socket，也无从确认
    server = _listen(x11_socket_dir / "X0")
    try:
        assert (
            desktop.choose_qt_platform(
                {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": "远程主机:0"}
            )
            is None
        )
    finally:
        server.close()


def test_qt_platform_is_left_alone_outside_wayland(monkeypatch, x11_socket_dir):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    server = _listen(x11_socket_dir / "X0")
    try:
        # 纯 X11 会话：本来就能定位窗口，不用管
        assert desktop.choose_qt_platform({"DISPLAY": ":0"}) is None
        # 用户自己指定过，绝不覆盖（冒烟脚本、无头测试都靠这个）
        assert (
            desktop.choose_qt_platform(
                {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "QT_QPA_PLATFORM": "offscreen"}
            )
            is None
        )
    finally:
        server.close()

    for platform in ("darwin", "win32"):
        monkeypatch.setattr(desktop.sys, "platform", platform)
        assert desktop.choose_qt_platform({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}) is None


def test_apply_qt_platform_only_writes_when_it_decided_something(monkeypatch, x11_socket_dir):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    server = _listen(x11_socket_dir / "X0")
    try:
        env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
        assert desktop.apply_qt_platform(env) == "xcb"
        assert env["QT_QPA_PLATFORM"] == "xcb"
    finally:
        server.close()

    env = {"DISPLAY": ":0"}
    assert desktop.apply_qt_platform(env) is None
    assert "QT_QPA_PLATFORM" not in env


def test_accessory_policy_is_a_no_op_off_macos(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "linux")
    assert desktop.use_accessory_activation_policy({}) is False


def test_accessory_policy_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    assert desktop.use_accessory_activation_policy({desktop.ACCESSORY_OPT_OUT: "1"}) is False


def test_accessory_policy_survives_a_missing_objc_runtime(monkeypatch):
    """取不到 ObjC 运行时就当没做过，不能把启动带崩。"""
    import ctypes.util

    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
    assert desktop.use_accessory_activation_policy({}) is False


def test_startup_notes_cover_wayland_and_a_missing_tray():
    quiet = desktop.startup_notes(platform_name="xcb", tray_available=True)
    assert quiet == []

    wayland = desktop.startup_notes(platform_name="wayland", tray_available=True)
    assert len(wayland) == 1
    assert "Wayland" in wayland[0] and "QT_QPA_PLATFORM=xcb" in wayland[0]

    trayless = desktop.startup_notes(platform_name="xcb", tray_available=False)
    assert len(trayless) == 1
    assert "右键" in trayless[0]

    assert len(desktop.startup_notes(platform_name="wayland", tray_available=False)) == 2
