'use strict';

const token = document.body.dataset.token;
const el = (id) => document.getElementById(id);
const api = (path) => `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;

const CHECKBOXES = [
  'debug_mode',
  'always_on_top',
  'click_through',
  'show_title_buttons',
  'theme2_always_expanded',
  'show_dark_trade',
  'show_sparkline',
  'show_chart_depth',
  'show_sparkline_fill',
  'show_bs_points',
  'show_open_line',
  'show_high_low',
  'show_stock_name',
  'show_stock_price',
  'grayscale',
  'intraday_chart',
  'chart_fill_width',
  'tdw_recent_five',
  'theme3_show_tdw',
  'compact',
  'stock_name_bold',
  'stock_price_bold',
  'stock_percent_bold',
  'dark_trade_bold',
  'mcp_notifications_enabled',
  'mcp_bell_terminal',
  'mcp_bell_toast',
  'mcp_bell_tray_icon',
  'mcp_bell_window',
];
const FONT_SIZES = [
  'font_size',
  'stock_name_font_size',
  'stock_price_font_size',
  'stock_percent_font_size',
  'dark_trade_font_size',
  'chart_label_font_size',
  'theme2_popup_font_size',
  'tray_unread_font_size',
];
const NUMBERS = [
  'visible_rows',
  'chart_height',
  'theme2_depth_width',
  'chart_depth_width',
  'chart_line_width',
  'depth_line_width',
  ...FONT_SIZES,
  'refresh_seconds',
  'chart_refresh_seconds',
  'opacity',
  'background_alpha',
  'grayscale_level',
];
const TEXTS = [
  'provider',
  'kline_source',
  'display_theme',
  'theme2_side',
  'layout',
  'row_style',
  'color_scheme',
  'font_family',
  'tdw_label_color',
  'background_color',
  'mcp_url',
  'mcp_api_key',
];
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
const TEXT_REGIONS = ['name', 'code', 'price', 'percent', 'dark', 'high_low', 'chart', 'tdw'];
const COMMON_FIELDS = new Set(['provider', 'kline_source', 'symbols', 'stock_visibility', 'debug_mode',
  'refresh_seconds', 'chart_refresh_seconds', 'mcp_url', 'mcp_api_key', 'mcp_notifications_enabled',
  'mcp_bell_terminal', 'mcp_bell_toast', 'mcp_bell_tray_icon', 'mcp_bell_window', 'tray_unread_font_size']);
let settingsScope = 'theme';
const MAX_SYMBOLS = 50;
const STOCK_COMPONENTS = [
  ['name', '股票名称'], ['price', '股价'], ['percent', '涨跌幅'],
  ['dark', '暗盘'], ['chart', 'K线'],
];
const SIZE_REGIONS = ['row', 'name', 'price', 'percent', 'dark', 'chart', 'canvas'];
const sizeInput = (region, axis) => el(region === 'chart' && axis === 'height' ? 'chart_height' : `size-${region}-${axis}`);

let providers = [];
let symbols = [];
let stockVisibility = {};
let saveQueue = Promise.resolve();
let applySequence = 0;
/** 代码 -> 名称，来自搜索结果或 /api/watchlist，缺了就只显示代码 */
let names = {};
let savedTimer = null;
let searchTimer = null;
let searchSequence = 0;
let searchController = null;
let fontApplyTimer = null;
let activeTheme = el('display_theme')?.value || 'theme1';

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

    const heading = document.createElement('div');
    heading.className = 'watchlist-heading';
    heading.append(label, sub, actions);
    const toggles = document.createElement('div');
    toggles.className = 'watchlist-toggles';
    toggles.setAttribute('role', 'group');
    toggles.setAttribute('aria-label', `${names[code] || code} 显示项目`);
    const key = visibilityKey(code);
    for (const [part, text] of STOCK_COMPONENTS) {
      const enabled = stockVisibility[key]?.[part] !== false;
      const toggle = button(text, `${enabled ? '关闭' : '显示'}${text}`, () => {
        stockVisibility[key] = { ...stockVisibility[key], [part]: !enabled };
        renderWatchlist();
        apply('显示设置已应用');
      }, false, 'watchlist-toggle');
      toggle.setAttribute('aria-pressed', String(enabled));
      toggle.setAttribute('aria-label', `${names[code] || code} ${text}`);
      toggles.append(toggle);
    }
    item.append(heading, toggles);
    list.append(item);
  });
}

function visibilityKey(code) {
  const match = code.replace(/\s/g, '').match(/^(?:(?:sh|sz|bj))?(\d{6})(?:\.(?:sh|sz|bj))?$/i);
  return match ? match[1] : code;
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
  const [code] = symbols.splice(index, 1);
  if (!symbols.some((symbol) => visibilityKey(symbol) === visibilityKey(code))) {
    delete stockVisibility[visibilityKey(code)];
  }
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

function cancelSearch() {
  clearTimeout(searchTimer);
  searchController?.abort();
  searchController = null;
  ++searchSequence;
}

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
      cancelSearch();
      add(item.code, item.name);
      el('stock-search').value = '';
      hideSuggestions();
    });
    box.append(option);
  }
  box.hidden = false;
}

async function search(text) {
  cancelSearch();
  const sequence = searchSequence;
  if (!text.trim()) {
    hideSuggestions();
    el('search-hint').textContent = `最多 ${MAX_SYMBOLS} 只，用按钮调整顺序。`;
    return;
  }
  const controller = new AbortController();
  searchController = controller;
  el('search-hint').textContent = '搜索中…';
  try {
    const response = await fetch(api(`/api/search?q=${encodeURIComponent(text)}`), {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (sequence !== searchSequence) return;
    renderSuggestions(data.results || []);
    el('search-hint').textContent = data.error
      ? `搜索接口不可用（${data.error}），仍可直接输入 6 位代码添加`
      : data.results?.length
        ? `最多 ${MAX_SYMBOLS} 只，用按钮调整顺序。`
        : '未找到匹配的 A 股，可换关键词或输入 6 位代码。';
  } catch (error) {
    if (sequence !== searchSequence || error.name === 'AbortError') return;
    hideSuggestions();
    el('search-hint').textContent = `搜索失败：${error.message}`;
  } finally {
    if (sequence === searchSequence) searchController = null;
  }
}

/* ------------------------------------------------------------ 表单 */

function collect() {
  const patch = { symbols, stock_visibility: stockVisibility };
  patch.component_sizes = {};
  for (const region of SIZE_REGIONS) {
    patch.component_sizes[region] = { width: Number(sizeInput(region, 'width').value) };
    if (region !== 'chart') patch.component_sizes[region].height = Number(sizeInput(region, 'height').value);
  }
  for (const id of TEXTS) patch[id] = el(id).value;
  for (const id of NUMBERS) patch[id] = Number(el(id).value);
  for (const id of CHECKBOXES) patch[id] = el(id).checked;
  for (const id of FONT_COLORS) {
    patch[id] = el(`${id}_auto`).checked ? 'auto' : el(id).value;
  }
  patch.text_opacities = Object.fromEntries(TEXT_REGIONS.map(region => [region, Number(el(`text-opacity-${region}`).value)]));
  return Object.fromEntries(Object.entries(patch).filter(([key]) =>
    key === 'display_theme' || COMMON_FIELDS.has(key) === (settingsScope === 'common')));
}

function fill(config) {
  activeTheme = config.display_theme;
  symbols = [...config.symbols];
  stockVisibility = structuredClone(config.stock_visibility || {});
  for (const id of TEXTS) el(id).value = config[id];
  for (const id of NUMBERS) el(id).value = config[id];
  for (const id of CHECKBOXES) el(id).checked = config[id];
  for (const region of SIZE_REGIONS) {
    for (const axis of ['width', 'height']) {
      if (region === 'chart' && axis === 'height') continue;
      sizeInput(region, axis).value = config.component_sizes?.[region]?.[axis] || 0;
    }
  }
  for (const id of FONT_COLORS) {
    const automatic = config[id] === 'auto';
    el(`${id}_auto`).checked = automatic;
    el(id).value = automatic ? FONT_COLOR_FALLBACKS[id] : config[id];
    el(id).disabled = automatic;
  }
  for (const region of TEXT_REGIONS) {
    el(`text-opacity-${region}`).value = config.text_opacities?.[region] ?? 1;
    el(`text-opacity-${region}-value`).textContent = `${Math.round(Number(el(`text-opacity-${region}`).value) * 100)}%`;
  }
  el('opacity-value').textContent = `${Math.round(config.opacity * 100)}%`;
  el('background_alpha-value').textContent = `${Math.round(config.background_alpha * 100)}%`;
  el('grayscale_level-value').textContent = config.grayscale_level;
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

  const theme2 = el('display_theme').value === 'theme2';
  const theme3 = activeTheme === 'theme3';
  for (const choice of document.querySelectorAll('[name="theme-choice"]')) {
    choice.checked = settingsScope !== 'common' && choice.value === activeTheme;
  }
  for (const section of document.querySelectorAll('[data-settings-scope]')) {
    section.hidden = section.dataset.settingsScope !== settingsScope;
  }
  for (const field of document.querySelectorAll('[data-themes]')) {
    field.hidden = !field.dataset.themes.split(' ').includes(activeTheme);
  }
  el('common-settings').setAttribute('aria-pressed', String(settingsScope === 'common'));
  document.body.dataset.settingsScope = settingsScope;
  el('theme-settings-heading').textContent = `主题 ${activeTheme.slice(-1)} · 独立设置`;
  el('theme-font-heading').textContent = `主题 ${activeTheme.slice(-1)} 的字体颜色与字重`;
  el('tdw-label-field').hidden = !theme3;
  const mirrorLeft = el('theme2_side').value === 'left';
  const single = !theme2 && el('layout').value === 'single';
  el('show_chart_depth').disabled = theme2 || single;
  el('theme2_side').disabled = !theme2;
  el('theme2_depth_width').disabled = !theme2;
  el('theme2_popup_font_size').disabled = !theme2;
  el('theme2_always_expanded').disabled = !theme2;
  // 灰度值只在开启「灰度显示」时才参与绘制，没开就没什么可调的。
  el('grayscale_level').disabled = !el('grayscale').checked;
  el('layout').disabled = theme2 || theme3;
  for (const id of ['visible_rows', 'row_style', 'chart_height']) {
    el(id).disabled = single || theme2;
  }
  if (theme3) el('row_style').disabled = true;
  for (const region of SIZE_REGIONS) {
    for (const axis of ['width', 'height']) {
      sizeInput(region, axis).disabled = single || (region === 'chart' && theme2) || (region === 'canvas' && !theme2);
    }
  }
  el('layout-hint').textContent = theme2
    ? '主题 2 固定为一列纵向排列；显示模式、网格行数与行内样式不参与排版。'
    : single
      ? '单行滚动：所有股票在一行里横向滚动，鼠标悬停暂停；此模式下行数、行内样式与 K 线高度都不生效。'
      : '多行列表：自选按这个行数铺成网格——填 1 就全部横向排开，填 2 就铺两行，窗口宽度随之变宽。';

  el('display-theme-hint').textContent = theme2
    ? `股价轴靠${mirrorLeft ? '左' : '右'}，盘口条向内延伸。可以点击价格展开详情，或开启始终展开。字号、线宽、文字透明度和窗口尺寸只保存到主题 2。`
    : theme3 ? '上方分时图与下方 TDW 共用时间轴。TDW 副图可单独关闭，关闭后分时图自动扩展。挂单条逐价位绘制。'
             : '经典网格支持左中右、上中下、上下布局，也可选择单行滚动。所有显示参数只保存到主题 1。';
  el('chart-label-font-heading').textContent = theme2 ? '高低价字号（px）'
    : theme3 ? '走势图 / TDW 图注字号（px）' : '走势图图注字号（px）';

  el('row_style-hint').textContent = theme2
    ? '主题 2 不使用行内样式。'
    : theme3 ? '顶部依次显示名称、暗盘、现价和涨跌幅。'
    : el('row_style').value === 'top_bottom'
      ? '上下：顶部显示名称、现价、涨跌幅和暗盘，下方显示走势图。'
      : el('row_style').value === 'stacked'
        ? '上中下：上面一行是名称与现价，下面一行是暗盘与涨跌幅，中间紧贴 K 线，上下不留间距。'
        : '左中右：左边名称压暗盘、右边现价压涨跌幅，左右各两行，中间永远是 K 线；格子窄到排不下时会自动改用上中下。';
}

function note(text) {
  el('saved').textContent = `${text} · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (el('saved').textContent = ''), 2500);
}

async function postConfig(patch) {
  // 点击多个开关时按顺序保存快照，避免较早的响应把刚点的新状态覆盖。
  const body = JSON.stringify(patch);
  const pending = saveQueue.then(async () => {
    const response = await fetch(api('/api/config'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return (await response.json()).config;
  });
  saveQueue = pending.catch(() => {});
  return pending;
}

async function apply(label = '已保存') {
  const sequence = ++applySequence;
  try {
    // 只有 switchTheme() 可以改变主题。其它控件即使和下拉框事件撞在一起，
    // 也强制写回当前 activeTheme，避免旧主题整页值误覆盖目标 profile。
    const patch = collect();
    patch.display_theme = activeTheme;
    // 服务端会做最终校验，用返回值回填，页面始终反映真实生效的配置。
    const config = await postConfig(patch);
    if (sequence !== applySequence) return;
    fill(config);
    if (settingsScope !== 'common') await applyWindowSize();
    note(label);
  } catch (error) {
    note(`保存失败：${error.message}`);
  }
}

async function switchTheme(nextTheme) {
  const previousScope = settingsScope;
  settingsScope = 'theme';
  if (nextTheme === activeTheme) {
    refreshHints();
    return;
  }

  clearTimeout(fontApplyTimer);
  fontApplyTimer = null;
  ++applySequence;
  const main = document.querySelector('main');
  main.inert = true;
  try {
    // 先保存当前页，再只提交主题标识，防止旧主题的字号和颜色写入新主题。
    settingsScope = previousScope;
    const currentPatch = collect();
    settingsScope = 'theme';
    currentPatch.display_theme = activeTheme;
    await postConfig(currentPatch);

    fill(await postConfig({ display_theme: nextTheme }));
    note('主题已切换');
  } catch (error) {
    el('display_theme').value = activeTheme;
    refreshHints();
    note(`切换失败：${error.message}`);
  } finally {
    main.inert = false;
    document.querySelector('[name="theme-choice"]:checked')?.focus({preventScroll: true});
  }
}

/* ------------------------------------------------------------ 事件 */

async function applyWindowSize() {
  const axes = ['width', 'height'];
  if (!axes.some((axis) => el(`window_${axis}`).dataset.dirty)) return;
  const dimensions = Object.fromEntries(axes.map((axis) => [axis,
    el(`window_${axis}`).dataset.dirty ? Number(el(`window_${axis}`).value) : 0]));
  const response = await fetch(api('/api/window-size'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(dimensions),
  });
  if (!response.ok) throw new Error(`调整窗口失败（HTTP ${response.status}）`);
  for (const axis of axes) delete el(`window_${axis}`).dataset.dirty;
}

let readingMetrics = false;
el('reset-window-scale').addEventListener('click', async () => {
  try {
    const response = await fetch(api('/api/window-size'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reset_scale: true }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await refreshLayoutMetrics();
    note('已恢复 100% 缩放');
  } catch (error) {
    note(`恢复缩放失败：${error.message}`);
  }
});

async function refreshLayoutMetrics() {
  if (readingMetrics || document.hidden) return;
  readingMetrics = true;
  try {
    const response = await fetch(api('/api/layout-metrics'));
    if (!response.ok) return;
    const metrics = await response.json();
    if (metrics.theme !== activeTheme) return;
    for (const region of ['window', ...SIZE_REGIONS]) {
      const size = metrics.regions?.[region];
      el(`size-current-${region}`).textContent = size
        ? (size.visible ? `${size.width} × ${size.height}` : '隐藏') : '暂无股票数据';
    }
    const windowSize = metrics.regions?.window;
    if (windowSize) {
      for (const axis of ['width', 'height']) {
        const input = el(`window_${axis}`);
        if (document.activeElement !== input && !input.dataset.dirty) input.value = windowSize[axis];
      }
    }
    el('size-scale-hint').textContent = `当前缩放：${Number(metrics.scale || 1).toFixed(2)} 倍；右下角拖拽后实际尺寸会自动更新。`;
  } catch (_) { /* 桌面暂时离线时保留最后一次尺寸。 */ }
  finally { readingMetrics = false; }
}

for (const region of SIZE_REGIONS) {
  for (const axis of ['width', 'height']) {
    if (region === 'chart' && axis === 'height') continue;
    sizeInput(region, axis).addEventListener('blur', () => apply('尺寸已应用'));
  }
}
for (const axis of ['width', 'height']) {
  el(`window_${axis}`).addEventListener('input', (event) => { event.target.dataset.dirty = '1'; });
  el(`window_${axis}`).addEventListener('blur', () => apply('尺寸已应用'));
}

// 开关和下拉改完即时生效；字号短延迟自动保存，避免改完直接退出时丢失。
for (const id of [...CHECKBOXES, 'provider', 'kline_source', 'theme2_side', 'layout', 'row_style', 'color_scheme', 'background_color']) {
  el(id).addEventListener('change', () => apply('已应用'));
}
el('common-settings').addEventListener('click', () => { settingsScope = 'common'; refreshHints(); });
for (const choice of document.querySelectorAll('[name="theme-choice"]')) {
  choice.addEventListener('change', () => switchTheme(choice.value));
}
el('tdw_label_color').addEventListener('change', () => apply('已应用'));
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
// 灰度值是 0–255 的灰阶，直接显示原值，不走上面的百分比格式。
el('grayscale_level').addEventListener('input', () => {
  el('grayscale_level-value').textContent = el('grayscale_level').value;
});
el('grayscale_level').addEventListener('change', () => apply('已应用'));
for (const id of ['chart_depth_width', 'chart_line_width', 'depth_line_width']) {
  el(id).addEventListener('change', () => apply('已应用'));
}
for (const region of TEXT_REGIONS) {
  const id = `text-opacity-${region}`;
  el(id).addEventListener('input', () => { el(`${id}-value`).textContent = `${Math.round(Number(el(id).value) * 100)}%`; });
  el(id).addEventListener('change', () => apply('已应用'));
}
for (const id of FONT_SIZES) {
  el(id).addEventListener('input', () => {
    clearTimeout(fontApplyTimer);
    if (!el(id).value || !el(id).checkValidity()) return;
    fontApplyTimer = setTimeout(() => {
      fontApplyTimer = null;
      apply('已应用');
    }, 450);
  });
  el(id).addEventListener('blur', () => {
    clearTimeout(fontApplyTimer);
    fontApplyTimer = null;
    apply('已应用');
  });
}
for (const id of [
  'visible_rows',
  'chart_height',
  'theme2_depth_width',
  'font_family',
  'refresh_seconds',
  'chart_refresh_seconds',
  'mcp_url',
  'mcp_api_key',
]) {
  el(id).addEventListener('blur', () => apply('已应用'));
}
el('apply').addEventListener('click', () => apply());

function scheduleSearch(event) {
  cancelSearch();
  hideSuggestions();
  const text = event.target.value;
  if (event.isComposing) return;
  if (!text.trim()) {
    search(text);
    return;
  }
  searchTimer = setTimeout(() => search(text), 250);
}
el('stock-search').addEventListener('input', scheduleSearch);
el('stock-search').addEventListener('compositionend', scheduleSearch);
el('stock-search').addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    cancelSearch();
    hideSuggestions();
  }
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.search')) {
    cancelSearch();
    hideSuggestions();
  }
});

/* ------------------------------------------------------------ 启动 */

(async () => {
  const response = await fetch(api('/api/config'));
  const data = await response.json();
  providers = data.providers;
  fill(data.config);
  el('theme-picker').disabled = false;
  refreshLayoutMetrics();
  setInterval(refreshLayoutMetrics, 1000);

  // 名称是额外信息，拿不到也不影响列表可用
  try {
    const watchlist = await (await fetch(api('/api/watchlist'))).json();
    names = { ...names, ...(watchlist.names || {}) };
    renderWatchlist();
  } catch (error) {
    /* 离线就只显示代码 */
  }
})();
