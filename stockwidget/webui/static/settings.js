'use strict';

const token = document.body.dataset.token;
const el = (id) => document.getElementById(id);

const CHECKBOXES = ['always_on_top', 'show_dark_trade', 'show_sparkline', 'compact'];
const NUMBERS = ['visible_rows', 'font_size', 'refresh_seconds', 'opacity'];
const TEXTS = ['provider', 'layout', 'color_scheme', 'font_family'];

let providers = [];
let savedTimer = null;

function collect() {
  const patch = { symbols: el('symbols').value };
  for (const id of TEXTS) patch[id] = el(id).value;
  for (const id of NUMBERS) patch[id] = Number(el(id).value);
  for (const id of CHECKBOXES) patch[id] = el(id).checked;
  return patch;
}

function fill(config) {
  el('symbols').value = config.symbols.join('\n');
  for (const id of TEXTS) el(id).value = config[id];
  for (const id of NUMBERS) el(id).value = config[id];
  for (const id of CHECKBOXES) el(id).checked = config[id];
  el('opacity-value').textContent = `${Math.round(config.opacity * 100)}%`;
  refreshHints();
}

function refreshHints() {
  const provider = providers.find((p) => p.id === el('provider').value);
  el('provider-hint').textContent =
    el('provider').value === 'mock'
      ? '离线随机行情，任何代码都能显示，适合先把界面调好。'
      : '公开接口，无需 API key；请求频率过高可能被限流。';
  el('symbols-hint').textContent = provider ? provider.placeholder : '';

  const single = el('layout').value === 'single';
  el('visible_rows').disabled = single;
  el('layout-hint').textContent = single
    ? '单行滚动：所有股票在一行里横向滚动，鼠标悬停暂停；此模式下行数设置不生效。'
    : '多行列表：窗口高度按行数自适应，自选超过行数时列表内可滚动。';
}

function note(text) {
  el('saved').textContent = `${text} · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (el('saved').textContent = ''), 2500);
}

async function apply(label = '已保存') {
  try {
    const response = await fetch(`/api/config?token=${encodeURIComponent(token)}`, {
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

// 开关和下拉改完即时生效，文本与数字框失焦后再提交，避免每敲一个字就写盘。
for (const id of [...CHECKBOXES, 'provider', 'layout', 'color_scheme']) {
  el(id).addEventListener('change', () => apply('已应用'));
}
el('opacity').addEventListener('input', () => {
  el('opacity-value').textContent = `${Math.round(Number(el('opacity').value) * 100)}%`;
});
el('opacity').addEventListener('change', () => apply('已应用'));
for (const id of ['symbols', 'visible_rows', 'font_family', 'font_size', 'refresh_seconds']) {
  el(id).addEventListener('blur', () => apply('已应用'));
}
el('apply').addEventListener('click', () => apply());

(async () => {
  const response = await fetch(`/api/config?token=${encodeURIComponent(token)}`);
  const data = await response.json();
  providers = data.providers;
  fill(data.config);
})();
