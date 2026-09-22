"""把配置、轮询线程、桌面窗口、托盘和 WebUI 接到一起。"""

from __future__ import annotations

import signal
import sys

from PySide6.QtCore import QEvent, QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from . import desktop, providers
from .config import Config, Store
from .hotkey import DEFAULT_WINDOW_TOGGLE_HOTKEY, WindowsGlobalHotkey
from .mcp_depth import DepthSnapshot, McpDepthPoller
from .mcp_notifications import McpNotification, McpNotificationListener
from .poller import Poller, Snapshot
from .ui.icon import tray_icon
from .ui.tray import Tray
from .ui.window import TickerWindow
from .webui import SettingsServer


class ConfigBridge(QObject):
    """WebUI 跑在另一个线程，配置变更通过信号回到界面线程。"""

    changed = Signal(object)


class WindowActivationFilter(QObject):
    """只在主行情窗口真正从后台切到前台时发信号。"""

    activated = Signal()

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.WindowActivate:
            self.activated.emit()
        return super().eventFilter(watched, event)


class WidgetApp:
    def __init__(self, argv: list[str] | None = None, store: Store | None = None) -> None:
        desktop.apply_qt_platform()
        self.qt = QApplication(argv if argv is not None else sys.argv)
        self.qt.setApplicationName("stock-ticker-widget")
        self.qt.setDesktopFileName("stock-ticker-widget")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setWindowIcon(tray_icon())
        desktop.use_accessory_activation_policy()

        self.store = store or Store()
        self.config = config = self.store.get()
        self._unread = 0
        self.tray: Tray | None = None

        self.window = TickerWindow(config)
        self._window_activation_filter = WindowActivationFilter(self.window)
        self._window_activation_filter.activated.connect(self._on_window_activated)
        self.window.installEventFilter(self._window_activation_filter)
        self._terminal_was_foreground = desktop.terminal_is_foreground()
        self._terminal_foreground_timer = QTimer(self.window)
        self._terminal_foreground_timer.setInterval(300)
        self._terminal_foreground_timer.timeout.connect(self._poll_terminal_foreground)
        if sys.platform == "win32" and desktop.terminal_window_handle() is not None:
            self._terminal_foreground_timer.start()
        self.window.restore_bounds(
            config.bounds, [screen.availableGeometry() for screen in self.qt.screens()]
        )
        self.window.refresh_requested.connect(self.refresh)
        self.window.settings_requested.connect(self.open_settings)
        self.window.quit_requested.connect(self.quit)
        self.window.bounds_changed.connect(self._save_bounds)
        self.window.bell_cleared.connect(self._clear_unread)
        self.qt.aboutToQuit.connect(self._flush_bounds)
        self.window.grayscale_requested.connect(
            lambda: self._apply_config(self.store.update({"grayscale": not self.store.get().grayscale}))
        )
        self.window.title_buttons_requested.connect(
            lambda: self._apply_config(
                self.store.update(
                    {"show_title_buttons": not self.store.get().show_title_buttons}
                )
            )
        )

        self.bridge = ConfigBridge()
        self.bridge.changed.connect(self._apply_config, Qt.QueuedConnection)
        self.server = SettingsServer(self.store, on_change=self.bridge.changed.emit)

        self.poller = Poller(config)
        self.poller.snapshot_ready.connect(self._on_snapshot, Qt.QueuedConnection)
        self.depth_poller = McpDepthPoller(config)
        self.depth_poller.depth_ready.connect(self._on_depth_snapshot, Qt.QueuedConnection)
        self.depth_poller.status_changed.connect(self._on_depth_status, Qt.QueuedConnection)
        self.notification_listener = McpNotificationListener(config)
        self.notification_listener.notification_ready.connect(
            self._on_mcp_notification, Qt.QueuedConnection
        )
        self.notification_listener.status_changed.connect(self._on_mcp_status, Qt.QueuedConnection)

        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = Tray(config)
            self.tray.toggle_action.triggered.connect(self.toggle_window)
            self.tray.refresh_action.triggered.connect(self.refresh)
            self.tray.settings_action.triggered.connect(self.open_settings)
            self.tray.quit_action.triggered.connect(self.quit)
            self.tray.on_top_action.toggled.connect(
                lambda checked: self._apply_config(self.store.update({"always_on_top": checked}))
            )
            self.tray.click_through_action.toggled.connect(
                lambda checked: self._apply_config(self.store.update({"click_through": checked}))
            )
            self.tray.title_buttons_action.toggled.connect(
                lambda checked: self._apply_config(
                    self.store.update({"show_title_buttons": checked})
                )
            )
            self.tray.normal_color_requested.connect(
                lambda color: self._apply_config(
                    self.store.update({"tray_icon_normal_color": color})
                )
            )
            self.tray.alert_color_requested.connect(
                lambda color: self._apply_config(
                    self.store.update({"tray_icon_alert_color": color})
                )
            )
            self.tray.activated.connect(self._on_tray_activated)

        self._local_window_shortcut: QShortcut | None = None
        self._global_window_hotkey: WindowsGlobalHotkey | None = None
        self._setup_window_hotkey()

    # ------------------------------------------------------------ 动作

    def refresh(self) -> None:
        self.poller.refresh_now()
        self.depth_poller.refresh_now()

    def open_settings(self) -> None:
        QDesktopServices.openUrl(QUrl(self.server.url))

    def toggle_window(self) -> None:
        if self.window.isVisible():
            self.window.hide()
        else:
            self._show_window()

    def _show_window(self) -> None:
        """确保窗口可见并提到前面；用于托盘和全局快捷键恢复窗口。"""
        if not self.window.isVisible():
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _setup_window_hotkey(self) -> None:
        """Windows 注册系统级 Ctrl+F2；其他平台退回 Qt 应用级快捷键。"""
        if sys.platform == "win32":
            hotkey = WindowsGlobalHotkey(DEFAULT_WINDOW_TOGGLE_HOTKEY)
            hotkey.activated.connect(self.toggle_window, Qt.QueuedConnection)
            hotkey.registration_failed.connect(
                self._on_hotkey_registration_failed, Qt.QueuedConnection
            )
            hotkey.registered.connect(
                lambda label: print(f"[快捷键] {label}：隐藏 / 显示窗口", flush=True),
                Qt.QueuedConnection,
            )
            self._global_window_hotkey = hotkey
            hotkey.start()
            return
        self._install_local_window_shortcut()

    def _install_local_window_shortcut(self) -> None:
        if self._local_window_shortcut is not None:
            return
        shortcut = QShortcut(QKeySequence(DEFAULT_WINDOW_TOGGLE_HOTKEY), self.window)
        shortcut.setContext(Qt.ApplicationShortcut)
        shortcut.activated.connect(self.toggle_window)
        self._local_window_shortcut = shortcut

    def _on_hotkey_registration_failed(self, reason: str) -> None:
        print(f"[快捷键] {reason}；退回应用内 {DEFAULT_WINDOW_TOGGLE_HOTKEY}", flush=True)
        self._install_local_window_shortcut()

    def quit(self) -> None:
        self._flush_bounds()
        if self._global_window_hotkey is not None:
            self._global_window_hotkey.stop()
            self._global_window_hotkey.wait(1000)
        self.poller.stop()
        self.poller.wait(2000)
        self.depth_poller.stop()
        self.depth_poller.wait(18000)
        self.notification_listener.stop()
        self.notification_listener.wait(9000)
        self.server.stop()
        self.qt.quit()

    # ------------------------------------------------------------ 回调

    def _on_tray_activated(self, reason) -> None:
        if reason != QSystemTrayIcon.Trigger:
            return

        had_unread = self._unread > 0
        if had_unread:
            # 有未读时，左键的首要语义是“我看到了”：清掉两处未读，并把窗口显示出来。
            # 不能再顺手 toggle，否则窗口本来可见时第一次点击反而把它藏掉，用户还得
            # 再点第二次才能看到行情。
            self._clear_unread()
            self.window.clear_mcp_notifications()
            self._show_window()
            return

        # 没有未读时，保留原来的显示 / 隐藏快捷操作。
        self.toggle_window()

    def _clear_unread(self) -> None:
        self._unread = 0
        if self.tray is not None:
            self.tray.set_unread(0)

    def _on_window_activated(self) -> None:
        """用户把行情窗口切回前台时，托盘未读即视为已查看。"""
        if self._unread > 0:
            self._clear_unread()

    def _poll_terminal_foreground(self) -> None:
        """Windows 下检测启动 Python 的终端从后台切回前台。"""
        foreground = desktop.terminal_is_foreground()
        if foreground and not self._terminal_was_foreground and self._unread > 0:
            self._clear_unread()
        self._terminal_was_foreground = foreground

    def _apply_config(self, config: Config) -> None:
        self.config = config
        if not (config.mcp_notifications_enabled and config.mcp_bell_tray_icon):
            self._clear_unread()
        self.window.apply_config(config)
        self.poller.apply_config(config)
        depth_poller = getattr(self, "depth_poller", None)
        if depth_poller is not None:
            depth_poller.apply_config(config)
        self.notification_listener.apply_config(config)
        if self.tray is not None:
            self.tray.apply_config(config)

    def _save_bounds(self, bounds: dict) -> None:
        self.store.update({"bounds": bounds})

    def _flush_bounds(self) -> None:
        self.window.flush_bounds()

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        listing = {p["id"]: p["label"] for p in providers.listing()}
        label = listing.get(snapshot.provider_id, snapshot.provider_id)
        if snapshot.effective_provider:
            label = f"自动 · {listing.get(snapshot.effective_provider, snapshot.effective_provider)}"
        self.window.update_snapshot(snapshot, label)

    def _on_depth_snapshot(self, snapshot: DepthSnapshot) -> None:
        self.window.update_depth(snapshot)

    def _on_depth_status(self, status: str) -> None:
        if status != "已关闭":
            print(f"[MCP千档] {status}", flush=True)

    def _on_mcp_status(self, status: str) -> None:
        if status != "已关闭":
            print(f"[MCP提醒] {status}", flush=True)

    def _on_mcp_notification(self, notification: McpNotification) -> None:
        timestamp = notification.created_at or "时间未知"
        print("\n======\n", flush=True)
        print(f"[MCP提醒] {timestamp} | {notification.title}", flush=True)
        if notification.body:
            print(notification.body, flush=True)
        if self.config.mcp_bell_terminal:
            desktop.ring_terminal_bell()
        if self.config.mcp_bell_toast and self.tray is not None:
            self.tray.notify(notification.title, notification.body)
        if self.config.mcp_bell_tray_icon and self.tray is not None:
            # 提醒到达时如果用户本来就在看行情窗口或启动 Python 的终端，就不制造未读。
            if self.window.isActiveWindow() or desktop.terminal_is_foreground():
                self._clear_unread()
            else:
                self._unread += 1
                self.tray.set_unread(self._unread)
        if self.config.mcp_bell_window:
            self.window.show_mcp_notification(notification.title, notification.body)

    # ------------------------------------------------------------ 启动

    def startup_notes(self) -> list[str]:
        return desktop.startup_notes(
            platform_name=self.qt.platformName(),
            tray_available=self.tray is not None,
        )

    def run(self) -> int:
        signal.signal(signal.SIGINT, lambda *_: self.quit())

        url = self.server.start()
        print(f"设置页：{url}")
        for note in self.startup_notes():
            print(f"[提示] {note}")
        self.window.show()
        if self.tray is not None:
            self.tray.show()
        self.poller.start()
        self.depth_poller.start()
        self.notification_listener.start()
        return self.qt.exec()


def main() -> int:
    return WidgetApp().run()
