"""设置用的本地 WebUI。

所有参数都在浏览器里改，改完立刻下发到桌面窗口，同时写回 JSON 配置文件。
服务只监听回环地址，并带一个随机 token——同一台机器上的其他用户
拿不到这个 token 就改不了配置。
"""

from __future__ import annotations

import logging
import secrets
import threading
from typing import Callable

from flask import Flask, abort, jsonify, render_template, request

from .. import providers
from ..config import MIN_OPACITY, Config, Store
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
    ) -> None:
        self.store = store
        self.on_change = on_change
        self.host = host
        self.token = secrets.token_urlsafe(16)
        self.search = StockSearch()
        self._port = port
        self._thread: threading.Thread | None = None
        self.app = self._build_app()

    # ------------------------------------------------------------ 路由

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
            return render_template(
                "settings.html",
                token=self.token,
                providers=providers.listing(),
                config=self.store.get().to_dict(),
                # 滑块下限跟着 sanitize 的夹取区间走，免得页面比后端更严。
                min_opacity=MIN_OPACITY,
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
            # Store 内部会做完整校验与区间夹取，这里直接把表单原样交进去。
            config = self.store.update(patch)
            self.on_change(config)
            return jsonify({"config": config.to_dict()})

        @app.get("/api/search")
        def search():
            """按代码/名称/拼音/首字母找股票，供自选列表的搜索框用。"""
            require_token()
            return jsonify(self.search.query(request.args.get("q", "")))

        @app.get("/api/watchlist")
        def watchlist():
            """给自选里的代码补上名称，一次请求解决，拉不到就只显示代码。"""
            require_token()
            config = self.store.get()
            names: dict[str, str] = {}
            try:
                for quote in providers.resolve(config.provider).fetch(list(config.symbols)):
                    if not quote.error and quote.name != quote.symbol:
                        names[quote.symbol] = quote.name
            except Exception:
                pass
            return jsonify({"names": names})

        return app

    # ------------------------------------------------------------ 生命周期

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self._port}/?token={self.token}"

    def start(self) -> str:
        """启动服务并返回带 token 的设置页地址。"""
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
