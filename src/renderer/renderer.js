'use strict';

const HISTORY_LEN = 40;

const listEl = document.getElementById('quotes');
const statusEl = document.getElementById('status');
const updatedEl = document.getElementById('updated');
const refreshBtn = document.getElementById('btn-refresh');

/** symbol -> { row, symbolEl, nameEl, priceEl, changeEl, sparkEl, lastPrice } */
const rows = new Map();
/** symbol -> number[]，只在内存里保留，用于画迷你走势图 */
const history = new Map();
let config = null;
let providerLabels = new Map();
/** 留一份最近的行情，配置或数据源标签晚到时可以直接重绘。 */
let lastPayload = null;

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

function fmtTime(ts) {
  return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false });
}

function directionOf(quote) {
  if (quote.error || !Number.isFinite(quote.change)) return 'error';
  if (quote.change > 0) return 'up';
  if (quote.change < 0) return 'down';
  return 'flat';
}

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
  if (!config?.showSparkline || config?.compact) {
    el.innerHTML = '';
    return;
  }
  const d = sparkPath(history.get(symbol) || []);
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

/* ------------------------------------------------------------ 列表渲染 */

function createRow() {
  const row = document.createElement('li');
  row.className = 'quote';
  row.innerHTML =
    '<div class="id"><span class="symbol"></span><span class="name"></span></div>' +
    '<div class="price"></div><div class="spark"></div><div class="change"></div>';
  return {
    row,
    symbolEl: row.querySelector('.symbol'),
    nameEl: row.querySelector('.name'),
    priceEl: row.querySelector('.price'),
    changeEl: row.querySelector('.change'),
    sparkEl: row.querySelector('.spark'),
    lastPrice: null,
  };
}

function flash(el, up) {
  el.classList.remove('tick-up', 'tick-down');
  void el.offsetWidth; // 强制重排，让同名动画能重播
  el.classList.add(up ? 'tick-up' : 'tick-down');
}

function renderQuotes(payload) {
  lastPayload = payload;
  const { quotes, time } = payload;

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
    entry.symbolEl.textContent = quote.symbol;
    entry.nameEl.textContent = quote.name && quote.name !== quote.symbol ? quote.name : '';

    if (quote.error) {
      entry.priceEl.textContent = quote.error;
      entry.changeEl.textContent = '';
      entry.sparkEl.innerHTML = '';
      entry.lastPrice = null;
      return;
    }

    const points = history.get(quote.symbol) || [];
    points.push(quote.price);
    if (points.length > HISTORY_LEN) points.shift();
    history.set(quote.symbol, points);

    entry.priceEl.textContent = fmtPrice(quote.price);
    entry.changeEl.textContent = fmtChange(quote.change, quote.changePercent);
    if (Number.isFinite(entry.lastPrice) && quote.price !== entry.lastPrice) {
      flash(entry.priceEl, quote.price > entry.lastPrice);
    }
    entry.lastPrice = quote.price;
    renderSpark(entry.sparkEl, quote.symbol, direction);
  });

  // 清掉已从自选中删除的行
  for (const [symbol, entry] of rows) {
    if (!seen.has(symbol)) {
      entry.row.remove();
      rows.delete(symbol);
      history.delete(symbol);
    }
  }

  const failed = quotes.filter((q) => q.error).length;
  const label = providerLabels.get(payload.provider) || payload.provider;
  statusEl.textContent = failed ? `${label} · ${failed} 个代码取数失败` : label;
  statusEl.classList.toggle('error', failed > 0);
  updatedEl.textContent = `更新于 ${fmtTime(time)} · 每 ${config?.refreshSeconds ?? '—'} 秒`;
}

/* ------------------------------------------------------------ 配置应用 */

function applyConfig(next) {
  config = next;
  document.body.dataset.scheme = next.colorScheme;
  document.body.dataset.compact = String(next.compact);
  // 走势图和页脚的显示依赖配置，配置变了要立刻反映，不必等下一轮行情。
  if (lastPayload) renderQuotes(lastPayload);
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

window.api.onQuotes(renderQuotes);
window.api.onConfig(applyConfig);

(async () => {
  const [config0, providerList] = await Promise.all([
    window.api.getConfig(),
    window.api.listProviders(),
  ]);
  providerLabels = new Map(providerList.map((p) => [p.id, p.label]));
  applyConfig(config0);
})();
