"""监听「全屏幕任意位置」的鼠标点击。

主题2 全展开后，收起条件是用户口径 2026-09-23：「鼠标移开不消失，任意屏幕
位置点击鼠标消失」。点其它程序、点桌面时本进程收不到 Qt 鼠标事件，win32 下
用 ``SetWindowsHookExW(WH_MOUSE_LL)`` 低级钩子兜住系统级输入；其它平台（或
钩子装不上时）退化为 QApplication 级事件过滤——只能看见本应用内的点击，
聊胜于无。

钩子只在 ``start()``/``stop()`` 之间安装（展开期间才存在），平时零开销。
回调发 ``clicked(QPoint)``，参数是**屏幕全局坐标**；连接方自行判断要不要
响应（例如点在价格热区上就放过，交给行内的点击逻辑去切换）。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, QPoint, Signal

if sys.platform == "win32":  # pragma: no cover - 平台分支，离屏测试走不到
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    WH_MOUSE_LL = 14
    HC_ACTION = 0
    # 左/右/中/侧键按下。收起认「点击」的宽口径：任何键按下都算。
    _BUTTON_DOWN = {0x0201, 0x0204, 0x0207, 0x020B}

    class _MSLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("pt", wintypes.POINT),
            ("mouseData", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    _HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,  # LRESULT
        ctypes.c_int,  # nCode
        wintypes.WPARAM,  # wParam（消息 id）
        ctypes.POINTER(_MSLLHOOKSTRUCT),  # lParam
    )
    _user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        _HOOKPROC,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    _user32.SetWindowsHookExW.restype = wintypes.HANDLE
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
    _user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    _user32.CallNextHookEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.WPARAM,
        ctypes.c_void_p,
    ]
    _user32.CallNextHookEx.restype = ctypes.c_ssize_t


class ScreenClickWatcher(QObject):
    """全屏幕点击监听。``clicked`` 带屏幕全局坐标；多次 start/stop 幂等。"""

    clicked = Signal(QPoint)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._using_app_filter = False
        self._hook: "int | None" = None
        # 钩子过程必须保活：ctypes 回调被 GC 后指针悬空，下一次点击就崩。
        self._proc = None

    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            return
        self._active = True
        if sys.platform == "win32" and self._install_hook():
            return
        # 非 win32、或钩子装不上（权限/会话限制）：退回应用内事件过滤。
        from PySide6.QtWidgets import QApplication

        target = QApplication.instance()
        if target is not None:
            target.installEventFilter(self)
            self._using_app_filter = True

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._hook is not None:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._proc = None
        if self._using_app_filter:
            self._using_app_filter = False
            from PySide6.QtWidgets import QApplication

            target = QApplication.instance()
            if target is not None:
                target.removeEventFilter(self)

    # ------------------------------------------------------------ 内部

    def _install_hook(self) -> bool:
        """装 WH_MOUSE_LL。必须在跑消息循环的 GUI 线程里调用。"""
        self._proc = _HOOKPROC(self._hook_proc)
        self._hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        if not self._hook:
            self._proc = None
            return False
        return True

    def _hook_proc(self, n_code: int, message: int, data) -> int:
        # 低级钩子回调有系统超时（默认 ~300ms），这里只发信号立即返回；
        # 真正的收起由接收方（QueuedConnection）回到事件循环里做。
        if n_code == HC_ACTION and message in _BUTTON_DOWN and data:
            pt = data.contents.pt
            self.clicked.emit(QPoint(int(pt.x), int(pt.y)))
        return int(_user32.CallNextHookEx(None, n_code, message, data))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt 命名
        if (
            event.type() == QEvent.MouseButtonPress
            and self._active
            and self._using_app_filter
        ):
            self.clicked.emit(event.globalPosition().toPoint())
        return super().eventFilter(watched, event)
