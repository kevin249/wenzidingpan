"""窗口显示/隐藏快捷键。

Windows 使用 ``RegisterHotKey`` 注册系统级快捷键，因此即使焦点在别的程序里也能
触发；其他平台由 ``WidgetApp`` 退回 Qt 的应用级快捷键，不额外引入第三方依赖。
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

DEFAULT_WINDOW_TOGGLE_HOTKEY = "Ctrl+F2"

_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_PM_NOREMOVE = 0x0000
_HOTKEY_ID = 0x5754  # "WT"，进程内只注册这一组快捷键。

_MODIFIERS = {
    "ctrl": ("Ctrl", _MOD_CONTROL),
    "control": ("Ctrl", _MOD_CONTROL),
    "alt": ("Alt", _MOD_ALT),
    "shift": ("Shift", _MOD_SHIFT),
    "win": ("Win", _MOD_WIN),
    "meta": ("Win", _MOD_WIN),
}
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")
_FKEY_RE = re.compile(r"f([1-9]|1\d|2[0-4])$", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedHotkey:
    label: str
    modifiers: int
    virtual_key: int


def parse_hotkey(text: str) -> ParsedHotkey:
    """把 ``Ctrl+F2`` 这类文本转成 Windows ``RegisterHotKey`` 参数。

    支持 Ctrl / Alt / Shift / Win 组合 F1~F24、A~Z、0~9。修饰键顺序和大小写会
    自动标准化，便于日志与测试稳定显示。
    """
    parts = [part.strip() for part in str(text or "").split("+") if part.strip()]
    if not parts:
        raise ValueError("快捷键不能为空")

    key_text = parts[-1]
    modifier_names: set[str] = set()
    modifiers = _MOD_NOREPEAT
    for raw in parts[:-1]:
        item = _MODIFIERS.get(raw.lower())
        if item is None:
            raise ValueError(f"不支持的修饰键：{raw}")
        canonical, flag = item
        modifier_names.add(canonical)
        modifiers |= flag

    match = _FKEY_RE.fullmatch(key_text)
    if match:
        number = int(match.group(1))
        virtual_key = 0x70 + number - 1  # VK_F1 == 0x70
        key_label = f"F{number}"
    elif len(key_text) == 1 and key_text.upper() in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        key_label = key_text.upper()
        virtual_key = ord(key_label)
    else:
        raise ValueError(f"不支持的按键：{key_text}")

    ordered = [name for name in _MODIFIER_ORDER if name in modifier_names]
    return ParsedHotkey("+".join([*ordered, key_label]), modifiers, virtual_key)


class WindowsGlobalHotkey(QThread):
    """在独立 Windows 消息线程里注册并监听一个系统级快捷键。"""

    activated = Signal()
    registration_failed = Signal(str)
    registered = Signal(str)

    def __init__(self, hotkey: str = DEFAULT_WINDOW_TOGGLE_HOTKEY, parent=None) -> None:
        super().__init__(parent)
        self.parsed = parse_hotkey(hotkey)
        self._thread_id = 0

    def run(self) -> None:  # noqa: D102 - QThread 入口
        if sys.platform != "win32":
            self.registration_failed.emit("当前平台不支持 Windows 全局快捷键")
            return

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        message = wintypes.MSG()

        # 先显式创建线程消息队列，stop() 才能稳定用 PostThreadMessage(WM_QUIT) 唤醒它。
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, _PM_NOREMOVE)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        registered = bool(
            user32.RegisterHotKey(
                None,
                _HOTKEY_ID,
                self.parsed.modifiers,
                self.parsed.virtual_key,
            )
        )
        if not registered:
            self._thread_id = 0
            self.registration_failed.emit(
                f"{self.parsed.label} 注册失败，可能已被其他程序占用"
            )
            return

        self.registered.emit(self.parsed.label)
        try:
            while not self.isInterruptionRequested():
                result = int(user32.GetMessageW(ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                if message.message == _WM_HOTKEY and int(message.wParam) == _HOTKEY_ID:
                    self.activated.emit()
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._thread_id = 0

    def stop(self) -> None:
        self.requestInterruption()
        thread_id = self._thread_id
        if sys.platform == "win32" and thread_id:
            ctypes.windll.user32.PostThreadMessageW(thread_id, _WM_QUIT, 0, 0)
