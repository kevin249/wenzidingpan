from __future__ import annotations

from types import SimpleNamespace

import pytest

from stockwidget.app import WidgetApp
from stockwidget.hotkey import DEFAULT_WINDOW_TOGGLE_HOTKEY, parse_hotkey


def test_default_window_hotkey_is_ctrl_f2_and_parses_for_register_hotkey():
    parsed = parse_hotkey(DEFAULT_WINDOW_TOGGLE_HOTKEY)

    assert parsed.label == "Ctrl+F2"
    assert parsed.virtual_key == 0x71  # VK_F2
    assert parsed.modifiers & 0x0002  # MOD_CONTROL
    assert parsed.modifiers & 0x4000  # MOD_NOREPEAT


@pytest.mark.parametrize(
    "raw, label",
    [
        ("shift+ctrl+a", "Ctrl+Shift+A"),
        ("ALT+F24", "Alt+F24"),
        ("win+7", "Win+7"),
    ],
)
def test_hotkey_parser_normalizes_supported_combinations(raw, label):
    assert parse_hotkey(raw).label == label


@pytest.mark.parametrize("raw", ["", "Ctrl+", "Ctrl+Esc", "Hyper+F2", "Ctrl+F25"])
def test_hotkey_parser_rejects_unsupported_keys(raw):
    with pytest.raises(ValueError):
        parse_hotkey(raw)


class _Window:
    def __init__(self, visible: bool) -> None:
        self.visible = visible
        self.hidden = 0

    def isVisible(self) -> bool:
        return self.visible

    def hide(self) -> None:
        self.visible = False
        self.hidden += 1


def test_window_hotkey_action_is_a_real_toggle_not_hide_only():
    window = _Window(True)
    shown: list[bool] = []
    stub = SimpleNamespace(window=window, _show_window=lambda: shown.append(True))

    WidgetApp.toggle_window(stub)
    assert window.visible is False
    assert window.hidden == 1
    assert shown == []

    WidgetApp.toggle_window(stub)
    assert shown == [True]
