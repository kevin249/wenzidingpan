"""桌面平台差异都收在这里：macOS 的工具窗与 Dock，Linux 的 Wayland 与托盘。

组件是一个「常驻在别人窗口上方、自己决定摆在哪」的悬浮窗，这类窗口恰好踩中
各平台差异最大的几处：macOS 的工具窗默认随应用失焦一起隐藏，Wayland 干脆不让
客户端自己摆窗口。这些补偿都不进业务代码，统一放在这个模块里。
"""

from __future__ import annotations

import os
import re
import socket
import sys
from typing import Any, Mapping

# X11 的本机 DISPLAY 形如 :0 或 :0.1，冒号后第一段是 socket 序号。
LOCAL_DISPLAY_RE = re.compile(r":(\d+)(?:\.\d+)?$")
X11_SOCKET_DIR = "/tmp/.X11-unix"

# NSApplicationActivationPolicyAccessory：有界面、但不进 Dock 也不进 ⌘-Tab。
_NS_ACCESSORY_POLICY = 1
# 出问题时的逃生开关：置为 1 就完全不碰 macOS 的激活策略。
ACCESSORY_OPT_OUT = "STOCK_TICKER_NO_MACOS_ACCESSORY"


def _x11_display_is_reachable(display: str) -> bool:
    """DISPLAY 指向的 X 服务器是不是真的能连上。

    Wayland 会话里 ``DISPLAY`` 通常由 XWayland 提供；但也可能是别处继承来的
    陈旧值。强行选 xcb 而 X 服务器不在，Qt 会直接 abort——那还不如留在 Wayland。
    所以先连一下 unix socket 确认，远程 DISPLAY 一律不认。
    """
    match = LOCAL_DISPLAY_RE.fullmatch(display.strip())
    if not match:
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            probe.connect(f"{X11_SOCKET_DIR}/X{match.group(1)}")
    except OSError:
        return False
    return True


def choose_qt_platform(env: Mapping[str, str]) -> str | None:
    """要不要替用户指定 Qt 平台插件；返回 ``None`` 表示按 Qt 自己的选择走。

    Wayland 下窗口位置完全归合成器管，``QWidget.move()`` 是空操作——拖动、
    位置记忆、鼠标穿透时的左上角把手会一起失灵，而这些正是本组件的核心交互。
    XWayland 在时就回退到 xcb，行为和 X11 会话完全一致。
    """
    if not sys.platform.startswith("linux"):
        return None
    if env.get("QT_QPA_PLATFORM"):
        return None  # 用户显式指定过，不去覆盖
    if not env.get("WAYLAND_DISPLAY"):
        return None
    display = env.get("DISPLAY", "")
    return "xcb" if display and _x11_display_is_reachable(display) else None


def apply_qt_platform(env: dict[str, str] | None = None) -> str | None:
    """把 :func:`choose_qt_platform` 的结果写进环境；必须在建 QApplication 之前调用。"""
    env = os.environ if env is None else env
    chosen = choose_qt_platform(env)
    if chosen:
        env["QT_QPA_PLATFORM"] = chosen
    return chosen


def keep_visible_when_inactive(widget: Any) -> None:
    """让 ``Qt.Tool`` 窗口在应用失焦后仍然留在屏幕上。

    macOS 把 ``Qt.Tool`` 映射成 NSPanel，而 NSPanel 默认随应用失活一起隐藏。
    盯盘组件几乎永远不是当前应用，不设这个属性的话在 macOS 上基本看不见。
    其他平台上这个属性无意义，Qt 会忽略。
    """
    from PySide6.QtCore import Qt

    widget.setAttribute(Qt.WA_MacAlwaysShowToolWindow, True)


def use_accessory_activation_policy(env: Mapping[str, str] | None = None) -> bool:
    """macOS：把进程降成附属应用——不占 Dock、不进 ⌘-Tab，像其他菜单栏小工具一样。

    Qt 没有对应 API，只能直接给 NSApplication 发消息。打包成 .app 之后更规矩的
    做法是在 Info.plist 里写 ``LSUIElement``，但以源码方式跑没有 plist 可写。
    整段都在 try 里，取不到符号就当没做过；真出问题可以用环境变量关掉。
    """
    env = os.environ if env is None else env
    if sys.platform != "darwin" or env.get(ACCESSORY_OPT_OUT):
        return False
    try:
        import ctypes
        import ctypes.util

        library = ctypes.util.find_library("objc")
        if not library:
            return False
        objc = ctypes.CDLL(library)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        # objc_msgSend 在 arm64 上必须按真实签名调用，参数才不会错位，
        # 所以每种签名单独绑一个函数指针，而不是反复改同一个的 argtypes。
        send_id = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", objc)
        )
        send_policy = ctypes.CFUNCTYPE(
            ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long
        )(("objc_msgSend", objc))

        app_class = objc.objc_getClass(b"NSApplication")
        if not app_class:
            return False
        shared = send_id(
            ctypes.c_void_p(app_class),
            ctypes.c_void_p(objc.sel_registerName(b"sharedApplication")),
        )
        if not shared:
            return False
        return bool(
            send_policy(
                ctypes.c_void_p(shared),
                ctypes.c_void_p(objc.sel_registerName(b"setActivationPolicy:")),
                _NS_ACCESSORY_POLICY,
            )
        )
    except Exception:  # 平台细节，失败了也不该拖垮启动
        return False


def startup_notes(*, platform_name: str, tray_available: bool) -> list[str]:
    """启动时值得提醒用户的平台限制，每条一行。"""
    notes: list[str] = []
    if platform_name == "wayland":
        notes.append(
            "当前是 Wayland 会话：窗口位置由合成器接管，拖动、位置记忆和"
            "鼠标穿透时的左上角把手都不会生效。装上 XWayland 后重开，"
            "或用 QT_QPA_PLATFORM=xcb 启动。"
        )
    if not tray_available:
        notes.append("系统托盘不可用：刷新 / 设置 / 退出请在组件上点右键。")
    return notes
