'use strict';

/**
 * A 股代码归一化。用户可以写 600519、sh600519、SH600519、600519.SH，
 * 统一解析成 { code, market }，各数据源再拼成自己需要的格式。
 */

/** 只按代码段判断交易所，规则来自沪深北三所的号段划分。 */
function marketOf(code) {
  if (/^(60|68|90|50|51|52|56|58)/.test(code)) return 'sh'; // 主板/科创板/B股/沪市 ETF
  if (/^(00|30|20|15|16|18|39)/.test(code)) return 'sz'; // 主板/创业板/B股/深市 ETF
  if (/^(43|83|87|88|92)/.test(code)) return 'bj'; // 北交所
  return 'sh';
}

/**
 * @param {string} input 用户填写的代码
 * @returns {{ raw: string, code: string, market: 'sh'|'sz'|'bj' } | null}
 */
function classify(input) {
  const raw = String(input || '').trim();
  if (!raw) return null;

  const s = raw.toLowerCase().replace(/\s+/g, '');
  let market = null;
  let code = s;

  const prefixed = /^(sh|sz|bj)(\d{6})$/.exec(s); // sh600519
  const suffixed = /^(\d{6})\.(sh|sz|bj)$/.exec(s); // 600519.SH
  if (prefixed) [, market, code] = prefixed;
  else if (suffixed) [, code, market] = suffixed;

  if (!/^\d{6}$/.test(code)) return null;
  return { raw, code, market: market || marketOf(code) };
}

module.exports = { classify, marketOf };
