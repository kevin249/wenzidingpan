'use strict';

const el = (id) => document.getElementById(id);
const fields = {
  provider: el('provider'),
  symbols: el('symbols'),
  layout: el('layout'),
  rows: el('rows'),
  fontFamily: el('font-family'),
  fontSize: el('font-size'),
  refresh: el('refresh'),
  scheme: el('scheme'),
  opacity: el('opacity'),
  ontop: el('ontop'),
  darktrade: el('darktrade'),
  spark: el('spark'),
  compact: el('compact'),
};

let providers = [];
let savedTimer = null;

function parseSymbols(text) {
  return [...new Set(text.split(/[\n,，;；\s]+/).map((s) => s.trim()).filter(Boolean))];
}

function refreshHints() {
  const provider = providers.find((p) => p.id === fields.provider.value);
  el('provider-hint').textContent =
    fields.provider.value === 'mock'
      ? '离线随机行情，任何代码都能显示，适合先把界面调好。'
      : '公开接口，无需 API key；请求频率过高可能被限流。';
  el('symbols-hint').textContent = provider ? provider.placeholder : '';

  const single = fields.layout.value === 'single';
  fields.rows.disabled = single;
  el('layout-hint').textContent = single
    ? '单行滚动：所有股票在一行里横向滚动，鼠标悬停暂停；此模式下行数设置不生效。'
    : '多行列表：窗口高度按行数自适应，自选超过行数时列表内可滚动。';
}

function fill(config) {
  fields.provider.value = config.provider;
  fields.symbols.value = config.symbols.join('\n');
  fields.layout.value = config.layout;
  fields.rows.value = config.visibleRows;
  fields.fontFamily.value = config.fontFamily;
  fields.fontSize.value = config.fontSize;
  fields.refresh.value = config.refreshSeconds;
  fields.scheme.value = config.colorScheme;
  fields.opacity.value = config.opacity;
  fields.ontop.checked = config.alwaysOnTop;
  fields.darktrade.checked = config.showDarkTrade;
  fields.spark.checked = config.showSparkline;
  fields.compact.checked = config.compact;
  el('opacity-value').textContent = `${Math.round(config.opacity * 100)}%`;
  refreshHints();
}

function collect() {
  return {
    provider: fields.provider.value,
    symbols: parseSymbols(fields.symbols.value),
    layout: fields.layout.value,
    visibleRows: Number(fields.rows.value),
    fontFamily: fields.fontFamily.value,
    fontSize: Number(fields.fontSize.value),
    refreshSeconds: Number(fields.refresh.value),
    colorScheme: fields.scheme.value,
    opacity: Number(fields.opacity.value),
    alwaysOnTop: fields.ontop.checked,
    showDarkTrade: fields.darktrade.checked,
    showSparkline: fields.spark.checked,
    compact: fields.compact.checked,
  };
}

async function apply(note = '已保存') {
  // 主进程会做最终校验，这里用返回值回填，界面始终反映真实生效的配置。
  const applied = await window.api.setConfig(collect());
  fill(applied);
  el('saved').textContent = `${note} · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => (el('saved').textContent = ''), 2500);
}

// 开关/下拉改完即时生效，文本与数字框失焦后再提交，避免每敲一个字就写盘。
for (const id of ['provider', 'layout', 'scheme', 'ontop', 'darktrade', 'spark', 'compact']) {
  fields[id].addEventListener('change', () => apply('已应用'));
}
fields.opacity.addEventListener('input', () => {
  el('opacity-value').textContent = `${Math.round(Number(fields.opacity.value) * 100)}%`;
});
fields.opacity.addEventListener('change', () => apply('已应用'));
for (const id of ['symbols', 'rows', 'fontFamily', 'fontSize', 'refresh']) {
  fields[id].addEventListener('blur', () => apply('已应用'));
}
el('apply').addEventListener('click', () => apply());

window.api.onConfig(fill);

(async () => {
  providers = await window.api.listProviders();
  fields.provider.innerHTML = '';
  for (const p of providers) {
    const option = document.createElement('option');
    option.value = p.id;
    option.textContent = p.label;
    fields.provider.append(option);
  }
  fill(await window.api.getConfig());
})();
