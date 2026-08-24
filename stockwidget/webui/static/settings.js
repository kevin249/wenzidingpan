'use strict';

const token = document.body.dataset.token;
const el = (id) => document.getElementById(id);
const api = (path) => `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;

const CHECKBOXES = [
  'always_on_top',
  'click_through',
  'show_dark_trade',
  'show_sparkline',
  'show_sparkline_fill',
  'show_bs_points',
  'show_open_line',
  'show_high_low',
  'show_stock_name',
  'show_stock_price',
  'grayscale',
  'intraday_chart',
  'compact',
  'stock_name_bold',
  'stock_price_bold',
  'stock_percent_bold',
  'dark_trade_bold',
];
const FONT_SIZES = [
  'font_size',
  'stock_name_font_size',
  'stock_price_font_size',
  'stock_percent_font_size',
  'dark_trade_font_size',
  'chart_label_font_size',
];
const NUMBERS = ['visible_rows', ...FONT_SIZES, 'refresh_seconds', 'opacity', 'background_alpha'];
const TEXTS = ['provider', 'layout', 'color_scheme', 'font_family', 'background_color'];
const FONT_COLORS = [
  'stock_name_color',
  'stock_price_color',
  'stock_percent_color',
  'dark_trade_color',
];
const FONT_COLOR_FALLBACKS = {
  stock_name_color: '#000000',
  stock_price_color: '#f04f5a',
  stock_percent_color: '#f04f5a',
  dark_trade_color: '#000000',
};
const MAX_SYMBOLS = 50;

let providers = [];
let symbols = [];
/** 代码 -> 名称，来自搜索结果或 /api/watchlist，缺了就只显示代码 */
let names = {};
let savedTimer = null;
let searchTimer = null;

/* ------------------------------------------------------------ 自选列表 */

function renderWatchlist() {
  const list = el('watchlist');
  list.innerHTML = '';
  if (!symbols.length) {
    const empty = document.createElement('li');
    empty.className = 'watchlist-empty';
    empty.textContent = '还没有自选，用上面的搜索框添加';
    list.append(empty);
    return;
  }

  symbols.forEach((code, index) => {
    const item = document.createElement('li');
    item.className = 'watchlist-item';

    const label = document.createElement('span');
    label.className = 'watchlist-name';
    label.textContent = names[code] || code;
    const sub = document.createElement('span');
    sub.className = 'watchlist-code';
    sub.textContent = names[code] ? code : '';

    const actions = document.createElement('span');
    actions.className = 'watchlist-actions';
    actions.append(
      button('↑', '上移', () => move(index, -1), index === 0),
      button('↓', '下移', () => move(index, 1), index === symbols.length - 1),
      button('✕', '移除', () => remove(index), false, 'danger')
    );

    item.append(label, sub, actions);
    list.append(item);
  });
}

function button(text, title, onClick, disabled = false, extra = '') {
  const element = document.createElement('button');
  element.type = 'button';
  element.className = `icon-btn ${extra}`.trim();
  element.textContent = text;
  element.title = title;
  element.disabled = disabled;
  element.addEventListener('click', onClick);
  return element;
}

function move(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= symbols.length) return;
  [symbols[index], symbols[target]] = [symbols[target], symbols[index]];
  renderWatchlist();
  apply('已应用');
}

function remove(index) {
  symbols.splice(index, 1);
  renderWatchlist();
  apply('已应用');
}

function add(code, name) {
  if (symbols.includes(code)) {
    note(`${name || code} 已在自选里`);
    return;
  }
  if (symbols.length >= MAX_SYMBOLS) {
    note(`最多 ${MAX_SYMBOLS} 只`);
    return;
  }
  symbols.push(code);
  if (name) names[code] = name;
  renderWatchlist();
  apply('已添加');
}

/* ------------------------------------------------------------ 搜索 */

function hideSuggestions() {
  el('suggestions').hidden = true;
  el('suggestions').innerHTML = '';
}

function renderSuggestions(results) {
  const box = el('suggestions');
  box.innerHTML = '';
  if (!results.length) {
    hideSuggestions();
    return;
  }
  for (const item of results) {
    const option = document.createElement('li');
    option.className = 'suggestion';
    option.innerHTML = '<span class="suggestion-name"></span><span class="suggestion-code"></span>';
    option.querySelector('.suggestion-name').textContent = item.name || item.code;
    option.querySelector('.suggestion-code').textContent = item.name ? item.code : '';
    option.addEventListener('click', () => {
      add(item.code, item.name);
      el('stock-search').value = '';
      hideSuggestions();
    });
    box.append(option);
  }
  box.hidden = false;
}

async function search(text) {
  if (!text.trim()) {
    hideSuggestions();
    el('search-hint').textContent = `最多 ${MAX_SYMBOLS} 只，用按钮调整顺序。`;
    return;
  }
  try {
    const response = await fetch(api(`/api/search?q=${encodeURIComponent(text)}`));
    const data = await response.json();
    renderSuggestions(data.results || []);
    el('search-hint').textContent = data.error
      ? `搜索接口不可用（${data.error}），仍可直接输入 6 位代码添加`
      : `最多 ${MAX_SYMBOLS} 只，用按钮调整顺序。`;
  } catch (error) {
    el('search-hint').textContent = `搜索失败：${error.message}`;
  }
}

/* ------------------------------------------------------------ 表单 */

function collect() {
  const patch = { symbols };
  for (const id of TEXTS) patch[id] = el(id).value;
  for (const id of NUMBERS) patch[id] = Number(el(id).value);
  for (const id of CHECKBOXES) patch[id] = el(id).checked;
  for (const id of FONT_COLORS) {
    patch[id] = el(`${id}_auto`).checked ? 'auto' : el(id).value;
  }
  return patch;
}

function fill(config) {
  symbols = [...config.symbols];
  for (const id of TEXTS) el(id).value = config[id];
  for (const id of NUMBERS) el(id).value = config[id];
  for (const id of CHECKBOXES) el(id).checked = config[id];
  for (const id of FONT_COLORS) {
    const automatic = config[id] === 'auto';
    el(`${id}_auto`).checked = automatic;
    el(id).value = automatic ? FONT_COLOR_FALLBACKS[id] : config[id];
    el(id).disabled = automatic;
  }
  el('opacity-value').textContent = `${Math.round(config.opacity * 100)}%`;
  el('background_alpha-value').textContent = `${Math.round(config.background_alpha * 100)}%`;
  refreshHints();
  renderWatchlist();
}

function refreshHints() {
  const provider = providers.find((p) => p.id === el('provider').value);
  const id = el('provider').value;
  el('provider-hint').textContent =
    id === 'mock'
      ? '离线随机行情，任何代码都能显示，适合先把界面调好。'
      : id === 'auto'
        ? '按东财 → 腾讯 → 新浪的顺序自动挑一个能用的，并记住它。'
        : '公开接口，无需 API key；请求频率过高可能被限流。';
  if (provider && el('symbols-hint')) el('symbols-hint').textContent = provider.placeholder;

  const single = el('layout').value === 'single';
  el('visible_rows').disabled = single;
  el('layout-hint').textContent = single
    ? '单行滚动：所有股票在一行里横向滚动，鼠标悬停暂停；此模式下行数设置不生效。'
    : '多行列表：自选按这个行数铺成网格——填 1 就全部横向排开，填 2 就铺两行，窗口宽度随之变宽。';
}

function note(text) {
  el('saved').textContent = `${text} · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (el('saved').textContent = ''), 2500);
}

async function apply(label = '已保存') {
  try {
    const response = await fetch(api('/api/config'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(collect()),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    // 服务端会做最终校验，用返回值回填，页面始终反映真实生效的配置。
    fill((await response.json()).config);
    note(label);
  } catch (error) {
    note(`保存失败：${error.message}`);
  }
}

/* ------------------------------------------------------------ 事件 */

// 开关和下拉改完即时生效，文本与数字框失焦后再提交，避免每敲一个字就写盘。
for (const id of [...CHECKBOXES, 'provider', 'layout', 'color_scheme', 'background_color']) {
  el(id).addEventListener('change', () => apply('已应用'));
}
for (const id of FONT_COLORS) {
  el(id).addEventListener('change', () => apply('已应用'));
  el(`${id}_auto`).addEventListener('change', () => {
    el(id).disabled = el(`${id}_auto`).checked;
    apply('已应用');
  });
}
for (const [id, valueId] of [['opacity', 'opacity-value'], ['background_alpha', 'background_alpha-value']]) {
  el(id).addEventListener('input', () => {
    el(valueId).textContent = `${Math.round(Number(el(id).value) * 100)}%`;
  });
  el(id).addEventListener('change', () => apply('已应用'));
}
for (const id of ['visible_rows', 'font_family', ...FONT_SIZES, 'refresh_seconds']) {
  el(id).addEventListener('blur', () => apply('已应用'));
}
el('apply').addEventListener('click', () => apply());

el('stock-search').addEventListener('input', (event) => {
  clearTimeout(searchTimer);
  const text = event.target.value;
  searchTimer = setTimeout(() => search(text), 250);
});
el('stock-search').addEventListener('keydown', (event) => {
  if (event.key === 'Escape') hideSuggestions();
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.search')) hideSuggestions();
});

/* ------------------------------------------------------------ 启动 */

(async () => {
  const response = await fetch(api('/api/config'));
  const data = await response.json();
  providers = data.providers;
  fill(data.config);

  // 名称是额外信息，拿不到也不影响列表可用
  try {
    const watchlist = await (await fetch(api('/api/watchlist'))).json();
    names = { ...names, ...(watchlist.names || {}) };
    renderWatchlist();
  } catch (error) {
    /* 离线就只显示代码 */
  }
})();
