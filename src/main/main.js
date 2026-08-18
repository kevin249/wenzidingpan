'use strict';

const path = require('path');
const { app, BrowserWindow, Tray, Menu, ipcMain, screen, shell, nativeImage } = require('electron');

const { Store } = require('./store');
const providers = require('./providers');

const TRAY_ICON = path.join(__dirname, '..', '..', 'assets', 'tray.png');
const RENDERER = path.join(__dirname, '..', 'renderer');
const PRELOAD = path.join(__dirname, 'preload.js');

let store;
let widget = null;
let settingsWindow = null;
let tray = null;
let pollTimer = null;
let polling = false;
let saveBoundsTimer = null;
let quitting = false;
/** 最近一次行情，用于给刚加载完的窗口补发，避免首轮推送打在还没订阅的渲染进程上。 */
let lastPayload = null;

/* ------------------------------------------------------------------ 窗口 */

/** 恢复上次位置前先确认它仍落在某块屏幕上（外接显示器可能已拔掉）。 */
function resolveBounds(saved) {
  const fallback = { width: 300, height: 260 };
  if (!saved) return fallback;
  const visible = screen.getAllDisplays().some((d) => {
    const w = d.workArea;
    return (
      saved.x + saved.width > w.x &&
      saved.y + saved.height > w.y &&
      saved.x < w.x + w.width &&
      saved.y < w.y + w.height
    );
  });
  return visible ? saved : { ...fallback, width: saved.width, height: saved.height };
}

function persistBounds() {
  if (!widget || widget.isDestroyed()) return;
  clearTimeout(saveBoundsTimer);
  saveBoundsTimer = setTimeout(() => {
    if (widget && !widget.isDestroyed()) store.set({ bounds: widget.getBounds() });
  }, 400);
}

function createWidget() {
  const config = store.get();
  const bounds = resolveBounds(config.bounds);

  widget = new BrowserWindow({
    ...bounds,
    minWidth: 220,
    minHeight: 120,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    resizable: true,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: config.alwaysOnTop,
    show: false,
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  if (config.alwaysOnTop) {
    // 'screen-saver' 层级才能压住全屏应用；visibleOnAllWorkspaces 让它跟随桌面切换。
    widget.setAlwaysOnTop(true, 'screen-saver');
    widget.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  }
  widget.setOpacity(config.opacity);

  widget.loadFile(path.join(RENDERER, 'index.html'));
  widget.once('ready-to-show', () => widget.show());
  widget.webContents.on('did-finish-load', () => {
    if (lastPayload) widget.webContents.send('quotes:update', lastPayload);
    else poll();
  });
  widget.on('move', persistBounds);
  widget.on('resize', persistBounds);
  widget.on('closed', () => {
    widget = null;
  });

  // 组件内的外链一律交给系统浏览器。
  widget.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function openSettings() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.show();
    settingsWindow.focus();
    return;
  }
  settingsWindow = new BrowserWindow({
    width: 460,
    height: 620,
    title: '行情组件设置',
    resizable: true,
    minimizable: false,
    maximizable: false,
    alwaysOnTop: true,
    show: false,
    webPreferences: { preload: PRELOAD, contextIsolation: true, nodeIntegration: false },
  });
  settingsWindow.setMenuBarVisibility(false);
  settingsWindow.loadFile(path.join(RENDERER, 'settings.html'));
  settingsWindow.once('ready-to-show', () => settingsWindow.show());
  settingsWindow.on('closed', () => {
    settingsWindow = null;
  });
}

function toggleWidget() {
  if (!widget || widget.isDestroyed()) return createWidget();
  if (widget.isVisible()) widget.hide();
  else widget.show();
}

/* -------------------------------------------------------------- 行情轮询 */

function broadcast(channel, payload) {
  for (const win of [widget, settingsWindow]) {
    if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
  }
}

function publish(payload) {
  lastPayload = payload;
  broadcast('quotes:update', payload);
}

async function poll() {
  if (polling) return; // 上一轮还没回来就跳过，避免请求叠加
  polling = true;
  const { provider: id, symbols } = store.get();
  const provider = providers.resolve(id);
  try {
    const quotes = await provider.fetchQuotes(symbols);
    publish({ provider: provider.id, quotes, time: Date.now() });
  } catch (err) {
    // 单个数据源整体挂掉时也要出一屏，让用户看到原因而不是空白。
    publish({
      provider: provider.id,
      quotes: symbols.map((symbol) => ({ symbol, name: symbol, error: err.message })),
      time: Date.now(),
    });
  } finally {
    polling = false;
  }
}

function schedulePolling() {
  clearInterval(pollTimer);
  poll();
  pollTimer = setInterval(poll, store.get().refreshSeconds * 1000);
}

/* -------------------------------------------------------------------- 托盘 */

function buildTray() {
  const icon = nativeImage.createFromPath(TRAY_ICON);
  tray = new Tray(icon.isEmpty() ? nativeImage.createEmpty() : icon);
  tray.setToolTip('股票行情组件');
  refreshTrayMenu();
  tray.on('click', toggleWidget);
}

function refreshTrayMenu() {
  if (!tray) return;
  const config = store.get();
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '显示 / 隐藏', click: toggleWidget },
      { label: '立即刷新', click: poll },
      { type: 'separator' },
      {
        label: '窗口置顶',
        type: 'checkbox',
        checked: config.alwaysOnTop,
        click: (item) => applyConfig(store.set({ alwaysOnTop: item.checked })),
      },
      { label: '设置…', click: openSettings },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitting = true;
          app.quit();
        },
      },
    ])
  );
}

/* -------------------------------------------------------------- 配置生效 */

function applyConfig(config) {
  if (widget && !widget.isDestroyed()) {
    widget.setAlwaysOnTop(config.alwaysOnTop, config.alwaysOnTop ? 'screen-saver' : 'normal');
    widget.setVisibleOnAllWorkspaces(config.alwaysOnTop, { visibleOnFullScreen: true });
    widget.setOpacity(config.opacity);
  }
  refreshTrayMenu();
  broadcast('config:changed', config);
  schedulePolling();
  return config;
}

/* ---------------------------------------------------------------- 生命周期 */

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (widget && !widget.isDestroyed()) {
      widget.show();
      widget.focus();
    }
  });

  app.whenReady().then(() => {
    store = new Store(path.join(app.getPath('userData'), 'config.json'));

    ipcMain.handle('config:get', () => store.get());
    ipcMain.handle('config:set', (_e, patch) => applyConfig(store.set(patch)));
    ipcMain.handle('providers:list', () => providers.list());
    ipcMain.handle('quotes:refresh', () => poll());
    ipcMain.handle('settings:open', () => openSettings());
    ipcMain.handle('app:quit', () => {
      quitting = true;
      app.quit();
    });

    createWidget();
    buildTray();
    schedulePolling();

    // 桌面组件不需要占用 Dock 图标，托盘就是它的入口。
    if (process.platform === 'darwin' && app.dock) app.dock.hide();
  });

  app.on('activate', () => {
    if (!widget || widget.isDestroyed()) createWidget();
    else widget.show();
  });

  // 组件常驻托盘：关掉窗口不等于退出。
  app.on('window-all-closed', () => {
    if (quitting) app.quit();
  });

  app.on('before-quit', () => {
    quitting = true;
    clearInterval(pollTimer);
  });
}
