'use strict';

const fs = require('fs');
const path = require('path');

const { DEFAULT_PROVIDER } = require('./providers');

const DEFAULTS = {
  provider: DEFAULT_PROVIDER,
  symbols: ['600519', '000001', '300750', '601318'],
  refreshSeconds: 5,
  colorScheme: 'cn', // cn = 红涨绿跌，us = 绿涨红跌
  opacity: 0.95,
  alwaysOnTop: true,
  showSparkline: true,
  showDarkTrade: true, // 显示东方财富暗盘资金
  compact: false,
  layout: 'multi', // multi = 多行列表，single = 单行滚动
  visibleRows: 4, // 多行模式下不用滚动就能看到的行数，窗口高度随它自适应
  fontFamily: '', // 留空表示跟随系统字体
  fontSize: 13,
  bounds: null,
};

const LAYOUTS = ['multi', 'single'];
// 字体名允许中英文、数字、空格、引号、逗号和连字符，挡掉任何可能破坏样式声明的字符。
const FONT_FAMILY_RE = /^[\w \-,'"\u4e00-\u9fff]{0,120}$/;

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
  if (typeof raw.showDarkTrade === 'boolean') out.showDarkTrade = raw.showDarkTrade;
  if (LAYOUTS.includes(raw.layout)) out.layout = raw.layout;
  if (Number.isFinite(raw.visibleRows)) out.visibleRows = clamp(Math.round(raw.visibleRows), 1, 30);
  if (Number.isFinite(raw.fontSize)) out.fontSize = clamp(Math.round(raw.fontSize), 9, 28);
  if (typeof raw.fontFamily === 'string') {
    const font = raw.fontFamily.trim();
    if (FONT_FAMILY_RE.test(font)) out.fontFamily = font;
  }
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
