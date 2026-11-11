# 桌面客户端实时 MCP 接入验证

日期：2026-11-11。项目：`D:/gupiao/cc/wenzidingpan`。

## 接入行为

- 设置页新增 `mcp_realtime_url`，保存后立即重连；留空使用旧通道。
- 新通道接收当前用户全部授权事件，自选股只限制盘口，不遗漏无股票代码的通知。
- 事件和游标在本地 SQLite 中事务保存；重启、断线、自选股变更后继续使用游标。
- 历史恢复不重复提醒；过期、撤销和旧修订不触发新提醒。
- B/S 修订替换图层，撤销移除标记；事件列表保留历史。
- 千档使用独立线程、事件循环和 MCP 连接，固定版本分片并校验 SHA256。
- 每只股票最多一个待取盘口任务；临时失败自动重试，不依赖下一次推送。
- 切换新通道后停止旧全量轮询，拦截旧请求迟到的缓存和盘口写入。
- 关闭提醒时仍同步事件列表、B/S 和盘口；退出或改配置可取消初始握手与恢复。

## 真实数据通道验证

证据：[validation.json](validation.json)。使用当天 PostgreSQL 记录盘后回放，来源可用事件 66 条、千档快照 20 份；本次选取 3 条投递和 1 份盘口测试客户端接入。

执行实际 `run_listener` 与实际 `DepthWorker`，通过独立本机 MCP HTTP 测试实例接入，使用独立服务端 namespace 和客户端日志库。

| 项目 | 结果 |
| --- | --- |
| B/S 事件 | 收到 |
| 无股票代码的选股事件 | 收到 |
| 关闭提醒后的千档同步 | 正常，1000 买档 / 624 卖档，FULL_DEPTH |
| 关闭提醒后的弹窗 | 0 次 |
| 客户端断开期间产生事件，重连后补发 | 成功 |
| 改自选股后复用原事件游标 | 成功 |
| 最终唯一事件数量 | 3 条，无重复 |

验证进程使用服务端项目 `.venv`，加载客户端 `.venv` 的 Qt 包；实际网络协议、订阅消费、事务日志、盘口线程和恢复均运行。Qt 窗口绘制未测试。本次验证不用于更新延迟统计；前一轮服务端性能回放结果另见服务端报告。

生产进程和用户配置未切换。测试网关及其子进程已清理。

## 回归验证

使用客户端项目 `.venv`：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_mcp_realtime.py tests/test_mcp_realtime_integration.py tests/test_mcp_notifications.py tests/test_mcp_depth.py tests/test_mcp_bs.py tests/test_mcp_request_deadlines.py tests/test_config.py tests/test_webui.py -q --tb=short -k 'not test_serial_scheduler_does_not_double_wait_after_long_batch'
```

结果：179 passed，1 deselected。覆盖本地日志事务、重开与去重、初次状态恢复、撤销与修订、独立盘口线程、盘口重试和合并、全用户事件范围、自选股变更后的游标复用、初始恢复取消、迟到旧请求隔离、设置 API 保存和清空、旧通道兼容等。

单独运行原有 `test_serial_scheduler_does_not_double_wait_after_long_batch` 仍失败：期望 1.0 秒，实际 0.1 秒。该用例及对应 `_next_wake_seconds` 方法与 HEAD 相同，未在本次修改中调整。

`git diff --check`、设置页 JavaScript 语法检查、新增 Python 文件 AST/行数检查通过。

## 启用

先启动服务端独立实时网关，再在客户端「实时订阅地址」填写可访问的 `/mcp` 地址和现有 API Key；同机示例 `http://127.0.0.1:8802/mcp`。显示服务端 B/S 需选择 MCP B/S 来源。
