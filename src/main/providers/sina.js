'use strict';

/**
 * A 股行情（新浪财经公开接口，免密钥）。
 * 一次请求即可批量拉取，返回 GBK 编码的 JS 片段：
 *   var hq_str_sh600000="浦发银行,10.00,10.01,10.20,...";
 */

const ENDPOINT = 'https://hq.sinajs.cn/list=';
const REQUEST_TIMEOUT_MS = 8000;

/** 用户可以只写 600000，这里补全交易所前缀。 */
function normalize(symbol) {
  const s = symbol.trim().toLowerCase().replace(/\s+/g, '');
  if (/^(sh|sz|bj|hk|gb_)/.test(s)) return s;
  if (/^(6|9)\d{5}$/.test(s)) return `sh${s}`;
  if (/^(0|2|3)\d{5}$/.test(s)) return `sz${s}`;
  if (/^(4|8)\d{5}$/.test(s)) return `bj${s}`;
  return s;
}

function decode(buffer) {
  for (const enc of ['gbk', 'gb18030']) {
    try {
      return new TextDecoder(enc).decode(buffer);
    } catch {
      /* ICU 不支持该编码时继续尝试 */
    }
  }
  // 兜底：名称可能乱码，但价格字段是 ASCII，不影响行情显示。
  return Buffer.from(buffer).toString('latin1');
}

function parse(text) {
  const quotes = new Map();
  const re = /var hq_str_(\w+)="([^"]*)"/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const [, code, payload] = m;
    if (!payload) {
      quotes.set(code, { error: '无数据' });
      continue;
    }
    const f = payload.split(',');
    const price = Number(f[3]);
    const prevClose = Number(f[2]);
    // 停牌时当前价为 0，退回昨收，避免显示 -100%。
    const effective = price > 0 ? price : prevClose;
    const change = effective - prevClose;
    quotes.set(code, {
      name: f[0] || code,
      price: effective,
      prevClose,
      change,
      changePercent: prevClose ? (change / prevClose) * 100 : 0,
      currency: 'CNY',
      halted: price === 0,
    });
  }
  return quotes;
}

async function fetchQuotes(symbols) {
  const codes = symbols.map(normalize);
  const url = ENDPOINT + encodeURIComponent(codes.join(','));
  let parsed = new Map();
  let failure = null;

  try {
    const res = await fetch(url, {
      headers: {
        // 新浪要求带 Referer，否则返回 403。
        Referer: 'https://finance.sina.com.cn/',
        'User-Agent': 'Mozilla/5.0',
      },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    parsed = parse(decode(await res.arrayBuffer()));
  } catch (err) {
    failure = err.name === 'TimeoutError' ? '请求超时' : err.message;
  }

  return symbols.map((symbol, i) => {
    const hit = parsed.get(codes[i]);
    if (!hit || hit.error) {
      return {
        symbol,
        name: symbol,
        price: null,
        prevClose: null,
        change: null,
        changePercent: null,
        currency: 'CNY',
        time: Date.now(),
        error: failure || (hit && hit.error) || '无数据',
      };
    }
    return { symbol, ...hit, time: Date.now(), error: null };
  });
}

module.exports = {
  id: 'sina',
  label: 'A 股 · 新浪财经（免密钥）',
  placeholder: 'sh600000, sz000001, 300750',
  fetchQuotes,
};
