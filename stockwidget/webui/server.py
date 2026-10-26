"""设置用的本地 WebUI。

所有参数都在浏览器里改，改完立刻下发到桌面窗口，同时写回 JSON 配置文件。
服务只监听回环地址，并带一个随机 token。
"""

from __future__ import annotations

import logging
import secrets
import math
import threading
from typing import Callable

from flask import Flask, abort, jsonify, render_template, request

from .. import mcp_bs, providers
from ..config import (
    COMPONENT_REGIONS,
    GRAYSCALE_LEVEL_MAX,
    GRAYSCALE_LEVEL_MIN,
    MIN_OPACITY,
    Config,
    Store,
)
from ..market_hours import active_updates_allowed
from ..search import StockSearch

DEFAULT_HOST = "127.0.0.1"


class SettingsServer:
    """在后台线程里跑的 Flask 应用。"""

    def __init__(
        self,
        store: Store,
        on_change: Callable[[Config], None],
        host: str = DEFAULT_HOST,
        port: int = 0,
        on_resize: Callable[[dict], None] | None = None,
    ) -> None:
        self.store = store
        self.on_change = on_change
        self.on_resize = on_resize
        self._layout_metrics: dict = {}
        self.host = host
        self.token = secrets.token_urlsafe(16)
        self.search = StockSearch()
        self._port = port
        self._thread: threading.Thread | None = None
        self.app = self._build_app()

    def publish_layout_metrics(self, metrics: dict) -> None:
        # GUI 线程构造一次新快照后原子替换；Flask 线程不访问 QWidget。
        self._layout_metrics = metrics

    def _build_app(self) -> Flask:
        app = Flask(__name__)
        app.config["JSON_AS_ASCII"] = False

        def require_token() -> None:
            supplied = request.args.get("token") or request.headers.get("X-Widget-Token")
            if not secrets.compare_digest(str(supplied or ""), self.token):
                abort(403)

        @app.get("/")
        def index():
            require_token()
            page = render_template(
                "settings.html",
                token=self.token,
                providers=providers.listing(),
                config=self.store.get().to_dict(),
                component_regions=COMPONENT_REGIONS,
                min_opacity=MIN_OPACITY,
                grayscale_level_min=GRAYSCALE_LEVEL_MIN,
                grayscale_level_max=GRAYSCALE_LEVEL_MAX,
            )
            return page.replace(
                "</body>",
                '<script src="/static/mcp_events.js"></script>\n  </body>',
            )

        @app.get("/api/config")
        def read_config():
            require_token()
            return jsonify(
                {"config": self.store.get().to_dict(), "providers": providers.listing()}
            )

        @app.post("/api/config")
        def write_config():
            require_token()
            patch = request.get_json(silent=True)
            if not isinstance(patch, dict):
                return jsonify({"error": "请求体必须是 JSON 对象"}), 400
            config = self.store.update(patch)
            self.on_change(config)
            return jsonify({"config": config.to_dict()})

        @app.get("/api/layout-metrics")
        def read_layout_metrics():
            require_token()
            return jsonify(self._layout_metrics)

        @app.post("/api/window-size")
        def resize_window():
            require_token()
            values = request.get_json(silent=True)
            if not isinstance(values, dict):
                return jsonify({"error": "宽高必须是 JSON 对象"}), 400
            dimensions = {}
            for axis in ("width", "height"):
                value = values.get(axis, 0)
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not 0 <= value <= 8192 or not math.isfinite(value)):
                    return jsonify({"error": "宽高必须为 0–8192 的数字"}), 400
                dimensions[axis] = round(value)
            if self.on_resize is None:
                return jsonify({"error": "桌面窗口尚未连接"}), 503
            self.on_resize(dimensions)
            return jsonify({"accepted": True}), 202

        @app.get("/api/mcp-bs-source")
        def read_mcp_bs_source():
            require_token()
            return jsonify({"source": mcp_bs.get_source()})

        @app.post("/api/mcp-bs-source")
        def write_mcp_bs_source():
            require_token()
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return jsonify({"error": "请求体必须是 JSON 对象"}), 400
            try:
                source = mcp_bs.set_source(str(payload.get("source") or ""))
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            return jsonify({"source": source})

        @app.get("/api/mcp-events")
        def mcp_events():
            require_token()
            rows = mcp_bs.today_records()
            return jsonify({"events": rows, "count": len(rows)})

        @app.get("/api/search")
        def search():
            require_token()
            return jsonify(self.search.query(request.args.get("q", "")))

        @app.get("/api/watchlist")
        def watchlist():
            require_token()
            config = self.store.get()
            names: dict[str, str] = {}
            # 设置页启动会自动请求这个接口；非交易时段不要因此偷偷访问行情源。
            if not active_updates_allowed(config.debug_mode):
                return jsonify({"names": names})
            try:
                for quote in providers.resolve(config.provider).fetch(list(config.symbols)):
                    if not quote.error and quote.name != quote.symbol:
                        names[quote.symbol] = quote.name
            except Exception:
                pass
            return jsonify({"names": names})

        return app

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}/?token={self.token}"

    def start(self) -> str:
        from werkzeug.serving import make_server

        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        server = make_server(self.host, self._port, self.app, threaded=True)
        self._port = server.server_port
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True, name="webui")
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if getattr(self, "_server", None) is not None:
            self._server.shutdown()
