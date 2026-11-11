"""本地 token 保护的一键测试路由和 GUI 绘制回执。"""
from flask import jsonify
from ..mcp_selftest import McpSelfTest
from ..symbols import classify


def register(app, server, require_token):
    @app.post('/api/mcp-test')
    def start():
        require_token()
        if server.mcp_test.snapshot()['running']:
            return jsonify(server.mcp_test.snapshot()), 202
        previous = server.store.get()
        visibility = {code: dict(value) for code, value in previous.stock_visibility.items()}
        if previous.symbols and (symbol := classify(previous.symbols[0])):
            visibility.setdefault(symbol.code, {})['chart'] = True
        config = server.store.update(dict(show_chart_depth=True, show_sparkline=True, stock_visibility=visibility))
        server.on_change(config)
        server.mcp_test.start(config)
        return jsonify(server.mcp_test.snapshot()), 202

    @app.get('/api/mcp-test')
    def status():
        require_token()
        value = server.mcp_test.snapshot()
        depth = value.get('depth') or {}
        received = server._depth_received.get(depth.get('symbol'), {})
        painted = server._depth_painted.get(depth.get('symbol'), {})
        matches = bool(depth and painted.get('source_at') == depth.get('source_at')
                       and painted.get('at', 0) >= value.get('delivered_at', 0))
        value['display'] = dict(received=received, painted=painted,
            status=('pass' if painted.get('shown') else 'warn') if matches else 'pending',
            detail=('窗口已绘制千档分布' if painted.get('shown') else '窗口未画出分布，请检查窗口是否可见、价格范围与显示设置')
                if matches else '等待窗口绘制回执，请保持行情窗口可见')
        return jsonify(value)
