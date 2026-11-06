// Run with: node --test tests/test_settings_search.cjs
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class Element {
  constructor() {
    this.value = '';
    this.dataset = {};
    this.children = [];
    this.selectors = new Map();
    this.listeners = {};
    this.hidden = true;
  }
  set innerHTML(value) { this.children = []; }
  append(...children) { this.children.push(...children); }
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }
  setAttribute(name, value) { this[name] = value; }
  focus() {}
  addEventListener(name, callback) { this.listeners[name] = callback; }
  dispatch(name, extra = {}) { this.listeners[name]({ target: this, ...extra }); }
}

function setup() {
  const elements = new Map();
  const requests = [];
  const configRequests = [];
  const timers = new Map();
  let nextTimer = 0;
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const choices = ['theme1', 'theme2', 'theme3'].map(value => Object.assign(new Element(), {value}));
  const commonSection = Object.assign(new Element(), {dataset: {settingsScope: 'common'}});
  const themeSection = Object.assign(new Element(), {dataset: {settingsScope: 'theme'}});
  const secondOnly = Object.assign(new Element(), {dataset: {themes: 'theme2'}});
  const thirdOnly = Object.assign(new Element(), {dataset: {themes: 'theme3'}});
  const body = Object.assign(new Element(), {hidden: false, dataset: {token: 'test-token'}});
  const context = vm.createContext({
    document: {
      body,
      getElementById: el,
      querySelectorAll: selector => selector === '[name="theme-choice"]' ? choices
        : selector === '[data-settings-scope]' ? [
            ...('settingsScope' in body.dataset ? [body] : []), commonSection, themeSection]
        : selector === 'section[data-settings-scope]' ? [commonSection, themeSection]
        : selector === '[data-themes]' ? [secondOnly, thirdOnly] : [],
      querySelector: selector => selector === 'main' ? el('main') : choices.find(choice => choice.checked),
      createElement: () => new Element(),
      addEventListener() {},
    },
    AbortController,
    structuredClone,
    setTimeout: (callback) => { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout: (id) => timers.delete(id),
    setInterval() {},
    fetch: (url, options) => new Promise((resolve, reject) => {
      // Ignore cancellation deliberately: even a response already in flight
      // must not overwrite a newer query or reopen dismissed suggestions.
      if (url.startsWith('/api/search')) requests.push({ url, options, resolve, reject });
      if (url.startsWith('/api/config') && options?.method === 'POST') configRequests.push({url, options, resolve, reject});
    }),
  });
  const source = fs.readFileSync(path.join(__dirname, '../stockwidget/webui/static/settings.js'), 'utf8');
  vm.runInContext(source, context);
  return { el, context, requests, timers, configRequests, commonSection, themeSection, secondOnly, thirdOnly, choices, body };
}

function answer(request, code) {
  request.resolve({ ok: true, json: async () => ({ results: [{ code }], error: null }) });
}

test('late success and late failure cannot overwrite the latest search', async () => {
  for (const fail of [false, true]) {
    const { context, requests, el } = setup();
    const old = context.search('旧关键词');
    const latest = context.search('zycx');
    assert.equal(requests[0].options.signal.aborted, true);
    answer(requests[1], '603986');
    await latest;
    const hint = el('search-hint').textContent;
    if (fail) requests[0].reject(new Error('old request failed'));
    else answer(requests[0], '600519');
    await old;
    assert.equal(el('suggestions').children.length, 1);
    assert.equal(el('suggestions').children[0].querySelector('.suggestion-name').textContent, '603986');
    assert.equal(el('search-hint').textContent, hint);
  }
});

test('clearing input or pressing Escape keeps late results dismissed', async () => {
  for (const event of ['input', 'keydown']) {
    const { context, requests, el } = setup();
    const pending = context.search('gzmt');
    el('stock-search').value = '';
    el('stock-search').dispatch(event, { key: 'Escape' });
    answer(requests[0], '600519');
    await pending;
    assert.equal(el('suggestions').hidden, true);
    assert.equal(el('suggestions').children.length, 0);
  }
});

test('changing input invalidates results during the debounce interval', async () => {
  const { context, requests, el, timers } = setup();
  const pending = context.search('gzmt');
  el('stock-search').value = 'zycx';
  el('stock-search').dispatch('input');
  assert.equal(timers.size, 1);
  answer(requests[0], '600519');
  await pending;
  assert.equal(el('suggestions').hidden, true);
  const runTimer = [...timers.values()][0];
  runTimer();
  assert.match(requests[1].url, /q=zycx&token=test-token/);
});

test('Chinese IME composition waits until the text is committed', () => {
  const { requests, el, timers } = setup();
  el('stock-search').value = 'zhao';
  el('stock-search').dispatch('input', { isComposing: true });
  assert.equal(timers.size, 0);
  assert.equal(requests.length, 0);
  el('stock-search').value = '兆易';
  el('stock-search').dispatch('compositionend');
  assert.equal(timers.size, 1);
});

test('HTTP failures clear old suggestions and show a useful error', async () => {
  const { context, requests, el } = setup();
  context.renderSuggestions([{ code: '600519' }]);
  const pending = context.search('zycx');
  requests[0].resolve({ ok: false, status: 503 });
  await pending;
  assert.equal(el('suggestions').hidden, true);
  assert.match(el('search-hint').textContent, /HTTP 503/);
});


test('common and per-theme forms expose and submit only their own settings', () => {
  const {context, el, commonSection, themeSection, secondOnly, thirdOnly, body} = setup();
  context.fill({display_theme: 'theme3', symbols: [], opacity: 1, background_alpha: .5,
    text_opacities: {name: .4}, theme3_show_tdw: false, chart_line_width: 2.3, depth_line_width: .6});
  assert.equal(commonSection.hidden, true);
  assert.equal(themeSection.hidden, false);
  assert.equal(secondOnly.hidden, true);
  assert.equal(thirdOnly.hidden, false);
  let patch = context.collect();
  assert.equal(patch.theme3_show_tdw, false);
  assert.equal(patch.chart_line_width, 2.3);
  assert.equal(patch.text_opacities.name, .4);
  assert.equal('mcp_url' in patch, false);
  assert.equal('symbols' in patch, false);
  el('common-settings').dispatch('click');
  assert.equal(body.hidden, false, 'opening MCP settings must never hide the entire page');
  assert.equal(commonSection.hidden, false);
  assert.equal(themeSection.hidden, true);
  el('mcp_url').value = 'http://localhost:8802/mcp';
  patch = context.collect();
  assert.equal(patch.mcp_url, 'http://localhost:8802/mcp');
  assert.equal('chart_line_width' in patch, false);
  assert.equal('text_opacities' in patch, false);
  context.fill({display_theme: 'theme3', symbols: [], mcp_url: patch.mcp_url});
  assert.equal(body.hidden, false, 'saving MCP settings must leave the page visible');
  context.switchTheme('theme3');
  assert.equal(body.hidden, false, 'returning to the current theme must leave navigation visible');
  el('common-settings').dispatch('click');
  assert.equal(body.hidden, false);
});

test('switching from common settings saves common fields before loading a theme profile', async () => {
  const {context, el, configRequests, commonSection, thirdOnly} = setup();
  const first = {display_theme: 'theme1', symbols: [], text_opacities: {price: .2}};
  context.fill(first);
  el('common-settings').dispatch('click');
  el('mcp_url').value = 'http://localhost:8802/mcp';
  const pending = context.switchTheme('theme3');
  await new Promise(resolve => setImmediate(resolve));
  const oldPatch = JSON.parse(configRequests[0].options.body);
  assert.equal(oldPatch.mcp_url, 'http://localhost:8802/mcp');
  assert.equal('text_opacities' in oldPatch, false);
  configRequests[0].resolve({ok: true, json: async () => ({config: first})});
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(JSON.parse(configRequests[1].options.body), {display_theme: 'theme3'});
  configRequests[1].resolve({ok: true, json: async () => ({config: {...first, display_theme: 'theme3',
    text_opacities: {price: .8}, chart_line_width: 3, theme3_show_tdw: false}})});
  await pending;
  assert.equal(commonSection.hidden, true);
  assert.equal(thirdOnly.hidden, false);
  assert.equal(context.collect().text_opacities.price, .8);
  assert.equal(context.collect().theme3_show_tdw, false);
  assert.equal(el('main').inert, false);
});
