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
    assert "显示 B/S 波动点" in body
    assert "显示左侧股票名称" in body
    assert "股票名称字号" in body
    assert "暗盘字号" in body
    assert "填充走势图下方颜色" in body
    assert "字体颜色与字重" in body
    assert "跟随涨跌" in body


def test_read_config_returns_providers(server):
    payload = _client(server).get(f"/api/config?token={server.token}").get_json()
    assert payload["config"]["provider"] == "auto"
    assert [p["id"] for p in payload["providers"]][0] == "auto"


def test_write_config_validates_persists_and_notifies(server):
    response = _client(server).post(
        f"/api/config?token={server.token}",
        json={
            "provider": "tencent",
            "symbols": "600519\n000001",
            "visible_rows": 999,  # 越界，应被夹到 30
            "opacity": 0.4,
            "show_sparkline_fill": True,
            "stock_name_color": "#123456",
            "stock_price_color": "auto",
            "stock_percent_color": "#654321",
            "dark_trade_color": "#111111",
            "stock_name_bold": True,
            "stock_price_bold": False,
            "stock_percent_bold": True,
            "dark_trade_bold": True,
            "font_family": "x;} body{display:none}",  # 注入，应被拒绝
        },
    )
    config = response.get_json()["config"]
    assert config["provider"] == "tencent"
    assert config["symbols"] == ["600519", "000001"]
    assert config["visible_rows"] == 30
    assert config["opacity"] == 0.4
    assert config["show_sparkline_fill"] is True
    assert config["stock_name_color"] == "#123456"
    assert config["stock_price_color"] == "auto"
    assert config["stock_percent_color"] == "#654321"
    assert config["dark_trade_color"] == "#111111"
    assert config["stock_name_bold"] is True
    assert config["stock_price_bold"] is False
    assert config["stock_percent_bold"] is True
    assert config["dark_trade_bold"] is True
    assert config["font_family"] == ""

    # 写盘
    assert json.loads(server.store.path.read_text(encoding="utf-8"))["provider"] == "tencent"
    # 实时下发给桌面窗口
    assert server.applied[-1].provider == "tencent"


def test_write_config_rejects_non_object_body(server):
    response = _client(server).post(f"/api/config?token={server.token}", json=["nope"])
    assert response.status_code == 400


def test_search_endpoint_requires_token_and_returns_results(server, monkeypatch):
    monkeypatch.setattr(
        server.search, "query", lambda text: {"results": [{"code": "600519", "name": "贵州茅台"}], "error": None}
    )
    client = _client(server)
    assert client.get("/api/search?q=gzmt").status_code == 403
    payload = client.get(f"/api/search?q=gzmt&token={server.token}").get_json()
    assert payload["results"][0]["code"] == "600519"


def test_watchlist_endpoint_requires_token(server):
    assert _client(server).get("/api/watchlist").status_code == 403


def test_watchlist_endpoint_survives_provider_failure(server, monkeypatch):
    """名称只是锦上添花，取不到也得让设置页正常打开。"""
    import stockwidget.webui.server as server_module

    class _Boom:
        def fetch(self, symbols):
            raise RuntimeError("数据源挂了")

    monkeypatch.setattr(server_module.providers, "resolve", lambda _id: _Boom())
    payload = _client(server).get(f"/api/watchlist?token={server.token}").get_json()
    assert payload == {"names": {}}
