'use strict';

const HISTORY_LEN = 40;
const MARQUEE_SPEED_PX_PER_SEC = 40;

const listEl = document.getElementById('quotes');
const marqueeEl = document.getElementById('marquee');
const trackEl = document.getElementById('marquee-track');
const statusEl = document.getElementById('status');
const updatedEl = document.getElementById('updated');
const refreshBtn = document.getElementById('btn-refresh');

/** symbol -> 多行模式下的行元素集合 */
const rows = new Map();
/** symbol -> number[]，只在内存里保留，用于画迷你走势图 */
const history = new Map();

let config = null;
let providerLabels = new Map();
/** 留一份最近的行情，配置或数据源标签晚到时可以直接重绘。 */
let lastPayload = null;
/** 单行模式的轨道内容，只在自选变化时重建，避免每次刷新都重启滚动动画 */
let marqueeSymbols = '';
let marqueeCells = new Map();

/* ------------------------------------------------------------ 工具函数 */

function fmtPrice(v) {
  if (!Number.isFinite(v)) return '—';
  const digits = Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 2 : 4;
  return v.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtChange(change, percent) {
  if (!Number.isFinite(change) || !Number.isFinite(percent)) return '';
  const sign = change > 0 ? '+' : '';
  return `${sign}${change.toFixed(2)}  ${sign}${percent.toFixed(2)}%`;
}

/** 暗盘资金接口给的是元，按东财口径折算成万/亿。 */
function fmtMoney(yuan) {
  if (!Number.isFinite(yuan)) return null;
  const sign = yuan > 0 ? '+' : yuan < 0 ? '-' : '';
  const abs = Math.abs(yuan);
  return abs >= 1e8 ? `${sign}${(abs / 1e8).toFixed(2)}亿` : `${sign}${(abs / 1e4).toFixed(2)}万`;
}

const fmtTime = (ts) => new Date(ts).toLocaleTimeString('zh-CN', { hour12: false });

function directionOf(quote) {
  if (quote.error || !Number.isFinite(quote.change)) return 'error';
  if (quote.change > 0) return 'up';
  if (quote.change < 0) return 'down';
  return 'flat';
}

const toneOf = (v) => (v > 0 ? 'positive' : v < 0 ? 'negative' : '');

/* ------------------------------------------------------------ 走势图 */

function sparkPath(points) {
  if (points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  return points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * 100;
      const y = 15 - ((p - min) / span) * 14;
      return `${i === 0 ? 'M' : 'L'}${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
}

function renderSpark(el, symbol, direction) {
  const d = config?.showSparkline && !config?.compact ? sparkPath(history.get(symbol) || []) : null;
  if (!d) {
    el.innerHTML = '';
    return;
  }
  const stroke =
    direction === 'up' ? 'var(--up)' : direction === 'down' ? 'var(--down)' : 'var(--flat)';
  el.innerHTML =
    `<svg viewBox="0 0 100 16" preserveAspectRatio="none" width="100%" height="16">` +
    `<path d="${d}" fill="none" stroke="${stroke}" stroke-width="1.4" ` +
    `stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
}

/* ------------------------------------------------------- 多行模式渲染 */

function createRow() {
  const row = document.createElement('li');
  row.className = 'quote';
  row.innerHTML =
    '<div class="id"><span class="symbol"></span><span class="name"></span></div>' +
    '<div class="price"></div><div class="spark"></div><div class="change"></div>' +
    '<div class="dark"></div>';
  return {
    row,
    symbolEl: row.querySelector('.symbol'),
    nameEl: row.querySelector('.name'),
    priceEl: row.querySelector('.price'),
    changeEl: row.querySelector('.change'),
    sparkEl: row.querySelector('.spark'),
    darkEl: row.querySelector('.dark'),
    lastPrice: null,
  };
}

function flash(el, up) {
  el.classList.remove('tick-up', 'tick-down');
  void el.offsetWidth; // 强制重排，让同名动画能重播
  el.classList.add(up ? 'tick-up' : 'tick-down');
}

function renderDark(el, quote) {
  const text = config?.showDarkTrade && !config?.compact ? fmtMoney(quote.darkFund) : null;
  if (!text) {
    el.innerHTML = '';
    return;
  }
  el.innerHTML =
    '<span class="dark-label">暗盘资金</span>' +
    `<span class="dark-value ${toneOf(quote.darkFund)}"></span>`;
  el.querySelector('.dark-value').textContent = text;
}

function renderList(quotes) {
  if (!quotes.length) {
    listEl.innerHTML = '<li class="empty">自选列表为空<br>点击 ⚙ 添加代码</li>';
    rows.clear();
    return;
  }
  if (listEl.querySelector('.empty')) listEl.innerHTML = '';

  const seen = new Set();

  quotes.forEach((quote, index) => {
    seen.add(quote.symbol);
    let entry = rows.get(quote.symbol);
    if (!entry) {
      entry = createRow();
      rows.set(quote.symbol, entry);
    }
    // 保持与配置一致的顺序
    if (listEl.children[index] !== entry.row) {
      listEl.insertBefore(entry.row, listEl.children[index] || null);
    }

    const direction = directionOf(quote);
    entry.row.className = `quote ${direction}`;
    entry.symbolEl.textContent = quote.name && quote.name !== quote.symbol ? quote.name : quote.symbol;
    entry.nameEl.textContent = quote.name && quote.name !== quote.symbol ? quote.symbol : '';

    if (quote.error) {
      entry.priceEl.textContent = quote.error;
      entry.changeEl.textContent = '';
      entry.sparkEl.innerHTML = '';
      entry.darkEl.innerHTML = '';
      entry.lastPrice = null;
      return;
    }

    entry.priceEl.textContent = fmtPrice(quote.price);
    entry.changeEl.textContent = fmtChange(quote.change, quote.changePercent);
    if (Number.isFinite(entry.lastPrice) && quote.price !== entry.lastPrice) {
      flash(entry.priceEl, quote.price > entry.lastPrice);
    }
    entry.lastPrice = quote.price;
    renderSpark(entry.sparkEl, quote.symbol, direction);
    renderDark(entry.darkEl, quote);
  });

  // 清掉已从自选中删除的行
  for (const [symbol, entry] of rows) {
    if (!seen.has(symbol)) {
      entry.row.remove();
      rows.delete(symbol);
    }
  }
}

/* ------------------------------------------------------- 单行模式渲染 */

function buildMarquee(quotes) {
  trackEl.innerHTML = '';
  marqueeCells = new Map();
  // 放两份相同内容，动画滚过一份的宽度后正好首尾相接。
  for (let copy = 0; copy < 2; copy++) {
    const group = document.createElement('div');
    group.className = 'marquee-copy';
    group.style.display = 'flex';
    for (const quote of quotes) {
      const cell = document.createElement('span');
      cell.className = 'tick';
      cell.innerHTML =
        '<span class="tick-name"></span><span class="tick-price"></span>' +
        '<span class="tick-change"></span><span class="tick-dark"></span>';
      group.append(cell);
      const list = marqueeCells.get(quote.symbol) || [];
      list.push(cell);
      marqueeCells.set(quote.symbol, list);
    }
    trackEl.append(group);
  }
}

function renderMarquee(quotes) {
  const signature = quotes.map((q) => q.symbol).join(',');
  if (signature !== marqueeSymbols) {
    marqueeSymbols = signature;
    buildMarquee(quotes);
  }

  for (const quote of quotes) {
    const direction = directionOf(quote);
    const dark = config?.showDarkTrade ? fmtMoney(quote.darkFund) : null;
    for (const cell of marqueeCells.get(quote.symbol) || []) {
      cell.className = `tick ${direction}`;
      cell.querySelector('.tick-name').textContent = quote.name || quote.symbol;
      cell.querySelector('.tick-price').textContent = quote.error ? quote.error : fmtPrice(quote.price);
      cell.querySelector('.tick-change').textContent = quote.error
        ? ''
        : fmtChange(quote.change, quote.changePercent);
      cell.querySelector('.tick-dark').textContent = dark ? `暗盘 ${dark}` : '';
    }
  }

  // 内容比窗口窄就没必要滚动。
  const copyWidth = trackEl.firstElementChild?.getBoundingClientRect().width || 0;
  if (copyWidth <= marqueeEl.clientWidth) {
    trackEl.style.animationName = 'none';
    trackEl.style.transform = 'translateX(0)';
  } else {
    trackEl.style.animationName = '';
    trackEl.style.transform = '';
    const duration = Math.max(8, Math.round(copyWidth / MARQUEE_SPEED_PX_PER_SEC));
    if (trackEl.dataset.duration !== String(duration)) {
      trackEl.dataset.duration = String(duration);
      trackEl.style.setProperty('--marquee-duration', `${duration}s`);
    }
  }
}

/* ---------------------------------------------------------- 高度自适应 */

/** 窗口高度跟着「行数 × 当前字号下的实际行高」走，由主进程实际调整窗口。 */
function autosize() {
  const chrome = document.querySelector('.titlebar').offsetHeight + 2; // 含边框
  if (config?.layout === 'single') {
    const line = trackEl.offsetHeight || 24;
    window.api.autosize(chrome + line + 10);
    return;
  }
  const first = listEl.querySelector('.quote');
  if (!first) return;
  const rowHeight = first.offsetHeight;
  const count = Math.max(1, Math.min(config?.visibleRows ?? 4, rows.size || 1));
  const footer = config?.compact ? 0 : document.querySelector('.footer').offsetHeight;
  window.api.autosize(chrome + rowHeight * count + footer + 6);
}

/* ------------------------------------------------------------ 总入口 */

function render(payload) {
  lastPayload = payload;
  const { quotes, time, darkTrade } = payload;

  if (config?.layout === 'single') renderMarquee(quotes);
  else renderList(quotes);

  for (const quote of quotes) {
    if (quote.error || !Number.isFinite(quote.price)) continue;
    const points = history.get(quote.symbol) || [];
    points.push(quote.price);
    if (points.length > HISTORY_LEN) points.shift();
    history.set(quote.symbol, points);
  }

  const failed = quotes.filter((q) => q.error).length;
  const label = providerLabels.get(payload.provider) || payload.provider;
  const parts = [label];
  if (failed) parts.push(`${failed} 个代码取数失败`);
  if (config?.showDarkTrade && darkTrade) {
    parts.push(darkTrade.error ? `暗盘取数失败：${darkTrade.error}` : `暗盘 ${darkTrade.date}`);
  }
  statusEl.textContent = parts.join(' · ');
  statusEl.classList.toggle('error', failed > 0);
  updatedEl.textContent = `更新于 ${fmtTime(time)} · 每 ${config?.refreshSeconds ?? '—'} 秒`;

  requestAnimationFrame(autosize);
}

/* ------------------------------------------------------------ 配置应用 */

function applyConfig(next) {
  const previousLayout = config?.layout;
  config = next;

  document.body.dataset.scheme = next.colorScheme;
  document.body.dataset.compact = String(next.compact);
  document.body.dataset.layout = next.layout;

  const root = document.documentElement;
  if (next.fontFamily) root.style.setProperty('--font-family', next.fontFamily);
  else root.style.removeProperty('--font-family');
  root.style.setProperty('--font-size', `${next.fontSize}px`);

  // 切换布局时另一套 DOM 需要重建。
  if (previousLayout && previousLayout !== next.layout) {
    rows.clear();
    listEl.innerHTML = '';
    marqueeSymbols = '';
  }
  if (lastPayload) render(lastPayload);
  else requestAnimationFrame(autosize);
}

/* ------------------------------------------------------------ 启动 */

refreshBtn.addEventListener('click', () => {
  refreshBtn.classList.remove('spinning');
  void refreshBtn.offsetWidth;
  refreshBtn.classList.add('spinning');
  window.api.refresh();
});
document.getElementById('btn-settings').addEventListener('click', () => window.api.openSettings());
document.getElementById('btn-quit').addEventListener('click', () => window.api.quit());

window.api.onQuotes(render);
window.api.onConfig(applyConfig);
window.addEventListener('resize', () => {
  if (config?.layout === 'single' && lastPayload) renderMarquee(lastPayload.quotes);
});

(async () => {
  const [initialConfig, providerList] = await Promise.all([
    window.api.getConfig(),
    window.api.listProviders(),
  ]);
  providerLabels = new Map(providerList.map((p) => [p.id, p.label]));
  applyConfig(initialConfig);
})();
