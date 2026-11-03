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
  addEventListener(name, callback) { this.listeners[name] = callback; }
  dispatch(name, extra = {}) { this.listeners[name]({ target: this, ...extra }); }
}

function setup() {
  const elements = new Map();
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const context = vm.createContext({
    document: {
      body: { dataset: { token: 'test-token' } },
      getElementById: el,
      createElement: () => new Element(),
      addEventListener() {},
    },
    AbortController,
    setTimeout: (callback) => { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout: (id) => timers.delete(id),
    setInterval() {},
    fetch: (url, options) => new Promise((resolve, reject) => {
      // Ignore cancellation deliberately: even a response already in flight
      // must not overwrite a newer query or reopen dismissed suggestions.
      if (url.startsWith('/api/search')) requests.push({ url, options, resolve, reject });
    }),
  });
  const source = fs.readFileSync(path.join(__dirname, '../stockwidget/webui/static/settings.js'), 'utf8');
  vm.runInContext(source, context);
  return { el, context, requests, timers };
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
