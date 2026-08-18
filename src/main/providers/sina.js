'use strict';

/**
 * 新浪财经行情（hq.sinajs.cn 公开接口，免密钥）。
 * 一次请求批量拉取，返回 GBK 编码的 JS 片段：
 *   var hq_str_sh600000="浦发银行,10.00,10.01,10.20,...";
 */

const { classify } = require('./symbols');
const { decodeGbk } = require('./gbk');

const ENDPOINT = 'https://hq.sinajs.cn/list=';
const REQUEST_TIMEOUT_MS = 8000;

// 新浪字段按位置排列，只取用得上的几个。
const F_NAME = 0;
const F_PREV_CLOSE = 2;
const F_PRICE = 3;

const keyOf = ({ market, code }) => `${market}${code}`;

function parse(text) {
  const quotes = new Map();
  const re = /var hq_str_(\w+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const [, key, payload] = m;
    const f = payload.split(',');
    if (f.length < 4) {
      quotes.set(key, null);
      continue;
    }
    const prevClose = Number(f[F_PREV_CLOSE]);
    const raw = Number(f[F_PRICE]);
    if (!Number.isFinite(prevClose) || !Number.isFinite(raw)) {
      quotes.set(key, null);
      continue;
    }
    // 停牌时现价为 0，退回昨收，避免显示成跌停 -100%。
    const price = raw > 0 ? raw : prevClose;
    const change = price - prevClose;
    quotes.set(key, {
      name: f[F_NAME] || key,
      price,
      prevClose,
      change,
      changePercent: prevClose ? (change / prevClose) * 100 : null,
      halted: raw === 0,
    });
  }
  return quotes;
}

async function fetchQuotes(symbols) {
  const parsed = symbols.map(classify);
  const valid = parsed.filter(Boolean);

  let quotes = new Map();
  let failure = valid.length ? null : '代码格式不正确';

  if (valid.length) {
    const url = ENDPOINT + encodeURIComponent(valid.map(keyOf).join(','));
    try {
      const res = await fetch(url, {
        // 新浪要求带 Referer，否则返回 403。
        headers: { Referer: 'https://finance.sina.com.cn/', 'User-Agent': 'Mozilla/5.0' },
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      quotes = parse(decodeGbk(await res.arrayBuffer()));
    } catch (err) {
      failure = err.name === 'TimeoutError' ? '请求超时' : err.message;
    }
  }

  return symbols.map((symbol, i) => {
    const parsedSymbol = parsed[i];
    const hit = parsedSymbol ? quotes.get(keyOf(parsedSymbol)) : null;
    const base = { symbol, currency: 'CNY', time: Date.now() };
    if (!hit) {
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
    return { ...base, ...hit, error: null };
  });
}

module.exports = {
  id: 'sina',
  label: 'A 股 · 新浪财经（免密钥）',
  placeholder: '600519, 000001, 300750, sh600000',
  fetchQuotes,
  parse, // 供测试使用
};
