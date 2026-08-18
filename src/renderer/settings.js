'use strict';

const el = (id) => document.getElementById(id);
const fields = {
  provider: el('provider'),
  symbols: el('symbols'),
  refresh: el('refresh'),
  scheme: el('scheme'),
  opacity: el('opacity'),
  ontop: el('ontop'),
  spark: el('spark'),
  compact: el('compact'),
};

let providers = [];
let savedTimer = null;

function parseSymbols(text) {
  return [...new Set(text.split(/[\n,，;；\s]+/).map((s) => s.trim()).filter(Boolean))];
}

function showProviderHint() {
  const p = providers.find((x) => x.id === fields.provider.value);
  el('provider-hint').textContent =
    fields.provider.value === 'mock'
      ? '离线随机行情，任何代码都能显示，适合先调好界面。'
      : '公开接口，无需 API key；请求频率过高可能被限流。';
  el('symbols-hint').textContent = p ? p.placeholder : '';
}

function fill(config) {
  fields.provider.value = config.provider;
  fields.symbols.value = config.symbols.join('\n');
  fields.refresh.value = config.refreshSeconds;
  fields.scheme.value = config.colorScheme;
  fields.opacity.value = config.opacity;
  fields.ontop.checked = config.alwaysOnTop;
  fields.spark.checked = config.showSparkline;
  fields.compact.checked = config.compact;
  el('opacity-value').textContent = `${Math.round(config.opacity * 100)}%`;
  showProviderHint();
}

function collect() {
  return {
    provider: fields.provider.value,
    symbols: parseSymbols(fields.symbols.value),
    refreshSeconds: Number(fields.refresh.value),
    colorScheme: fields.scheme.value,
    opacity: Number(fields.opacity.value),
    alwaysOnTop: fields.ontop.checked,
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

// 开关类改动即时生效，文本框等失焦后再提交，避免每敲一个字就写盘。
for (const id of ['provider', 'scheme', 'ontop', 'spark', 'compact']) {
  fields[id].addEventListener('change', () => apply('已应用'));
}
fields.opacity.addEventListener('input', () => {
  el('opacity-value').textContent = `${Math.round(Number(fields.opacity.value) * 100)}%`;
});
fields.opacity.addEventListener('change', () => apply('已应用'));
for (const id of ['symbols', 'refresh']) {
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
