'use strict';

const eastmoney = require('./eastmoney');
const tencent = require('./tencent');
const sina = require('./sina');
const mock = require('./mock');

// 顺序即设置里的下拉顺序，东财排第一并作为默认数据源。
const providers = [eastmoney, tencent, sina, mock];
const byId = new Map(providers.map((p) => [p.id, p]));
const DEFAULT_PROVIDER = eastmoney.id;

/** 未知 id 一律回落到默认数据源，保证界面始终能出数。 */
function resolve(id) {
  return byId.get(id) || byId.get(DEFAULT_PROVIDER);
}

function list() {
  return providers.map(({ id, label, placeholder }) => ({ id, label, placeholder }));
}

module.exports = { resolve, list, providers, DEFAULT_PROVIDER };
