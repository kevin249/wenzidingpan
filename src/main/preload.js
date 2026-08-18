'use strict';

const { contextBridge, ipcRenderer } = require('electron');

/** 渲染进程只能看到这张白名单，拿不到 Node 或完整的 ipcRenderer。 */
contextBridge.exposeInMainWorld('api', {
  getConfig: () => ipcRenderer.invoke('config:get'),
  setConfig: (patch) => ipcRenderer.invoke('config:set', patch),
  listProviders: () => ipcRenderer.invoke('providers:list'),
  refresh: () => ipcRenderer.invoke('quotes:refresh'),
  openSettings: () => ipcRenderer.invoke('settings:open'),
  quit: () => ipcRenderer.invoke('app:quit'),
  onQuotes: (cb) => {
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on('quotes:update', handler);
    return () => ipcRenderer.off('quotes:update', handler);
  },
  onConfig: (cb) => {
    const handler = (_e, config) => cb(config);
    ipcRenderer.on('config:changed', handler);
    return () => ipcRenderer.off('config:changed', handler);
  },
});
