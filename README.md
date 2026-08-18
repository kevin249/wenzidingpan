# 股票行情桌面组件 · Stock Ticker Widget

一个常驻桌面的悬浮行情小窗：无边框、半透明、可置顶、可拖拽，托盘常驻。
基于 Electron，**默认离线可用**——首次启动无需网络、无需 API key。

![组件窗口与设置窗口](docs/screenshot-widget.png)

## 特性

- **悬浮小窗**：无边框 + 毛玻璃背景，标题栏任意位置可拖动，不占任务栏，支持置顶到全屏应用之上
- **托盘常驻**：显示/隐藏、立即刷新、置顶开关、设置、退出
- **可插拔数据源**：默认模拟行情（离线），另内置 A 股与美股/港股两个免密钥公开接口
- **迷你走势图**：每行内嵌最近 40 个采样点的价格曲线，颜色跟随涨跌
- **涨跌配色可切**：红涨绿跌（A 股习惯）/ 绿涨红跌（欧美习惯）
- **紧凑模式**：一行一只股票，适合塞在屏幕角落
- **状态持久化**：自选列表、刷新间隔、透明度、窗口位置与尺寸，重启后自动恢复
- **逐行降级**：某个代码取不到数只在该行显示原因，不影响其他行情

## 快速开始

```bash
npm install
npm start
```

首次启动即有 4 只模拟股票在跳动。点组件右上角 ⚙ 打开设置，换数据源、填自己的自选代码。

## 数据源

| 数据源 | 说明 | 代码写法 |
| --- | --- | --- |
| `mock` | 离线随机游走行情，默认项。不联网、不需要密钥 | 任意字符串，如 `DEMO` |
| `sina` | A 股，新浪财经公开接口，免密钥 | `sh600000`、`sz000001`，也可只写 `600000` 自动补前缀 |
| `yahoo` | 美股 / 港股 / 加密货币，Yahoo Finance chart 接口，免密钥 | `AAPL`、`0700.HK`、`BTC-USD` |

两个联网数据源都是公开接口，无需注册，但**请求频率过高可能被限流**；刷新间隔建议不低于 3 秒。
在受限网络（公司代理、容器沙箱）中它们可能被拦截，此时组件会在对应行显示 `HTTP 403` 之类的原因，
其余功能不受影响——切回 `mock` 即可正常使用。

新增数据源只需在 `src/main/providers/` 下加一个模块，导出 `{ id, label, placeholder, fetchQuotes }`，
并在 `src/main/providers/index.js` 里注册；`fetchQuotes(symbols)` 返回统一结构：

```js
{
  symbol, name, price, prevClose,
  change, changePercent, currency,
  time,            // 毫秒时间戳
  error,           // 该代码取数失败的原因，成功时为 null
}
```

## 设置项

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| 数据源 | `mock` | 见上表 |
| 自选代码 | `DEMO TEST ACME NOVA` | 每行一个或逗号分隔，最多 50 个，自动去重 |
| 刷新间隔 | 5 秒 | 允许 1–3600 秒 |
| 涨跌配色 | 红涨绿跌 | 可切换为绿涨红跌 |
| 窗口透明度 | 95% | 20%–100% |
| 窗口置顶 | 开 | 关掉后组件会被其他窗口盖住 |
| 迷你走势图 | 开 | 紧凑模式下自动隐藏 |
| 紧凑模式 | 关 | 隐藏名称、走势图与页脚 |

![设置窗口](docs/screenshot-settings.png)

开关类设置改完立即生效；自选代码和刷新间隔在输入框失焦或点「保存并应用」后生效。
配置写在 Electron 的用户数据目录下：

- macOS `~/Library/Application Support/stock-ticker-widget/config.json`
- Windows `%APPDATA%\stock-ticker-widget\config.json`
- Linux `~/.config/stock-ticker-widget/config.json`

## 项目结构

```
src/main/            主进程
  main.js            窗口 / 托盘 / 轮询 / IPC
  store.js           配置读写与校验
  preload.js         暴露给渲染进程的 API 白名单
  providers/         行情数据源（mock / sina / yahoo）
src/renderer/        渲染进程
  index.html         组件窗口
  settings.html      设置窗口
scripts/
  make-icon.js       生成托盘图标（不依赖任何图形库）
  smoke.js           冒烟测试
```

## 安全设计

- 渲染进程禁用 Node（`contextIsolation: true` / `nodeIntegration: false`），只能调用 preload 白名单里的方法
- 两个页面都声明了 CSP，禁止外部资源与内联脚本
- 所有行情请求都在主进程发起，渲染进程不直接触网，也就不受 CORS 与凭据泄露影响
- 磁盘上的配置和渲染进程传来的配置一律经过 `sanitize()` 校验和区间夹取后才使用

## 测试

```bash
npm run smoke
```

先跑纯逻辑检查（配置校验、数据源回落、资源完整性），再真正启动一次 Electron，
确认窗口能加载且渲染进程没有未捕获异常或 CSP 违规。无图形环境时会自动套 `xvfb-run`；
两者都没有时会跳过启动检查并说明原因。

## 已知限制

- 托盘图标在部分 Linux 桌面环境需要 `libappindicator` / dbus 支持，缺失时托盘可能不显示，组件窗口本身不受影响
- 透明窗口需要桌面开启混成（compositing），未开启时背景会呈不透明纯色
- 尚未接入打包（`electron-builder` 等），目前以源码方式运行

## 许可

MIT
