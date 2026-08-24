"""WebUI：token 校验、配置读写与实时下发。"""

from __future__ import annotations

import json

import pytest

from stockwidget.config import Store
from stockwidget.webui import SettingsServer


@pytest.fixture()
def server(tmp_path):
    applied = []
    store = Store(tmp_path / "config.json")
    instance = SettingsServer(store, on_change=applied.append)
    instance.applied = applied  # 测试里方便断言
    instance.store = store
    return instance


def _client(server):
    server.app.config["TESTING"] = True
    return server.app.test_client()


def test_requires_token(server):
    client = _client(server)
    assert client.get("/").status_code == 403
    assert client.get("/api/config").status_code == 403
    assert client.post("/api/config", json={}).status_code == 403


def test_settings_page_renders(server):
    response = _client(server).get(f"/?token={server.token}")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "行情组件设置" in body
    assert "东方财富" in body


def test_read_config_returns_providers(server):
    payload = _client(server).get(f"/api/config?token={server.token}").get_json()
    assert payload["config"]["provider"] == "eastmoney"
    assert [p["id"] for p in payload["providers"]][0] == "eastmoney"


def test_write_config_validates_persists_and_notifies(server):
    response = _client(server).post(
        f"/api/config?token={server.token}",
        json={
            "provider": "tencent",
            "symbols": "600519\n000001",
            "visible_rows": 999,  # 越界，应被夹到 30
            "font_family": "x;} body{display:none}",  # 注入，应被拒绝
        },
    )
    config = response.get_json()["config"]
    assert config["provider"] == "tencent"
    assert config["symbols"] == ["600519", "000001"]
    assert config["visible_rows"] == 30
    assert config["font_family"] == ""

    # 写盘
    assert json.loads(server.store.path.read_text(encoding="utf-8"))["provider"] == "tencent"
    # 实时下发给桌面窗口
    assert server.applied[-1].provider == "tencent"


def test_write_config_rejects_non_object_body(server):
    response = _client(server).post(f"/api/config?token={server.token}", json=["nope"])
    assert response.status_code == 400
