'use strict';

const fs = require('fs');
const path = require('path');

const DEFAULTS = {
  provider: 'mock',
  symbols: ['DEMO', 'TEST', 'ACME', 'NOVA'],
  refreshSeconds: 5,
  colorScheme: 'cn', // cn = 红涨绿跌，us = 绿涨红跌
  opacity: 0.95,
  alwaysOnTop: true,
  showSparkline: true,
  compact: false,
  bounds: null,
};

const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

/** 配置来自磁盘和渲染进程，一律按 schema 校验后再用。 */
function sanitize(input) {
  const raw = input && typeof input === 'object' ? input : {};
  const out = { ...DEFAULTS };

  if (typeof raw.provider === 'string') out.provider = raw.provider;

  if (Array.isArray(raw.symbols)) {
    const symbols = raw.symbols
      .filter((s) => typeof s === 'string')
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 50);
    if (symbols.length) out.symbols = [...new Set(symbols)];
  }

  if (Number.isFinite(raw.refreshSeconds)) out.refreshSeconds = clamp(raw.refreshSeconds, 1, 3600);
  if (raw.colorScheme === 'cn' || raw.colorScheme === 'us') out.colorScheme = raw.colorScheme;
  if (Number.isFinite(raw.opacity)) out.opacity = clamp(raw.opacity, 0.2, 1);
  if (typeof raw.alwaysOnTop === 'boolean') out.alwaysOnTop = raw.alwaysOnTop;
  if (typeof raw.showSparkline === 'boolean') out.showSparkline = raw.showSparkline;
  if (typeof raw.compact === 'boolean') out.compact = raw.compact;

  const b = raw.bounds;
  if (b && [b.x, b.y, b.width, b.height].every(Number.isFinite)) {
    out.bounds = {
      x: Math.round(b.x),
      y: Math.round(b.y),
      width: clamp(Math.round(b.width), 200, 4000),
      height: clamp(Math.round(b.height), 120, 4000),
    };
  }

  return out;
}

class Store {
  constructor(file) {
    this.file = file;
    this.data = sanitize(this.#read());
  }

  #read() {
    try {
      return JSON.parse(fs.readFileSync(this.file, 'utf8'));
    } catch {
      // 首次启动或文件损坏，都退回默认配置。
      return {};
    }
  }

  get() {
    return { ...this.data };
  }

  /** 合并补丁并落盘，返回生效后的完整配置。 */
  set(patch) {
    this.data = sanitize({ ...this.data, ...patch });
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      fs.writeFileSync(this.file, JSON.stringify(this.data, null, 2));
    } catch (err) {
      console.error('[store] 配置写入失败:', err.message);
    }
    return this.get();
  }
}

module.exports = { Store, DEFAULTS, sanitize };
