'use strict';

/**
 * 东方财富行情（push2 公开接口，免密钥，默认数据源）。
 * 一次请求批量拉取，返回 JSON：
 *   { rc: 0, data: { total: n, diff: [ { f2, f3, f4, f12, f13, f14, f18 }, ... ] } }
 */

const { classify } = require('./symbols');

const ENDPOINT = 'https://push2.eastmoney.com/api/qt/ulist.np/get';
// 东财网页端公开使用的固定 ut 值，不是密钥，缺省时接口会拒绝请求。
const UT = 'fa5fd1943c7b386f172d6893dbfba10b';
const FIELDS = 'f1,f2,f3,f4,f12,f13,f14,f18';
const REQUEST_TIMEOUT_MS = 8000;

/** 东财用 1 表示沪市，0 表示深市与北交所。 */
const secidOf = ({ market, code }) => `${market === 'sh' ? 1 : 0}.${code}`;

const numeric = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/**
 * push2 在不同参数组合下会把价格按 100 倍返回。涨跌幅是比值，与缩放无关，
 * 拿它和「按价格算出来的涨跌幅」对一次，就能判断这批数据到底缩没缩放。
 */
function detectScale(rows) {
  let scaled = 0;
  let plain = 0;
  for (const row of rows) {
    const price = numeric(row.f2);
    const prevClose = numeric(row.f18);
    const pct = numeric(row.f3);
    if (!price || !prevClose || pct === null) continue;
    const computed = ((price - prevClose) / prevClose) * 100;
    if (Math.abs(computed - pct) <= Math.abs(computed - pct / 100)) plain++;
    else scaled++;
  }
  return scaled > plain ? 100 : 1;
}

async function fetchQuotes(symbols) {
  const parsed = symbols.map(classify);
  const valid = parsed.filter(Boolean);

  let bySecid = new Map();
  let scale = 1;
  let failure = valid.length ? null : '代码格式不正确';

  if (valid.length) {
    const url =
      `${ENDPOINT}?ut=${UT}&fltt=2&invt=2&fields=${FIELDS}` +
      `&secids=${encodeURIComponent(valid.map(secidOf).join(','))}&_=${Date.now()}`;
    try {
      const res = await fetch(url, {
        headers: { 'User-Agent': 'Mozilla/5.0', Referer: 'https://quote.eastmoney.com/' },
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      const rows = json?.data?.diff;
      if (!Array.isArray(rows)) throw new Error('返回格式异常');
      scale = detectScale(rows);
      bySecid = new Map(rows.map((row) => [`${row.f13}.${row.f12}`, row]));
    } catch (err) {
      failure = err.name === 'TimeoutError' ? '请求超时' : err.message;
    }
  }

  return symbols.map((symbol, i) => {
    const parsedSymbol = parsed[i];
    const row = parsedSymbol ? bySecid.get(secidOf(parsedSymbol)) : null;
    const base = { symbol, currency: 'CNY', time: Date.now() };

    if (!row) {
      return {
        ...base,
        name: symbol,
        price: null,
        prevClose: null,
        change: null,
        changePercent: null,
        error: failure || (parsedSymbol ? '无数据' : '代码格式不正确'),
      };
    }

    const prevClose = numeric(row.f18) !== null ? row.f18 / scale : null;
    const raw = numeric(row.f2) !== null ? row.f2 / scale : null;
    // 停牌时最新价为 0，退回昨收，避免显示成跌停 -100%。
    const price = raw && raw > 0 ? raw : prevClose;
    const change = price !== null && prevClose !== null ? price - prevClose : null;

    return {
      ...base,
      name: typeof row.f14 === 'string' && row.f14 !== '-' ? row.f14 : symbol,
      price,
      prevClose,
      change,
      changePercent: change !== null && prevClose ? (change / prevClose) * 100 : null,
      halted: raw === 0,
      error: price === null ? '无数据' : null,
    };
  });
}

module.exports = {
  id: 'eastmoney',
  label: 'A 股 · 东方财富（默认，免密钥）',
  placeholder: '600519, 000001, 300750, sh601318',
  fetchQuotes,
  detectScale, // 供测试使用
};
