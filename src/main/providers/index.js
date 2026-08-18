'use strict';

const mock = require('./mock');
const sina = require('./sina');
const yahoo = require('./yahoo');

const providers = [mock, sina, yahoo];
const byId = new Map(providers.map((p) => [p.id, p]));

/** 未知 id 一律回落到 mock，保证界面始终有数据。 */
function resolve(id) {
  return byId.get(id) || mock;
}

function list() {
  return providers.map(({ id, label, placeholder }) => ({ id, label, placeholder }));
}

module.exports = { resolve, list, providers };
