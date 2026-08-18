'use strict';

/**
 * 美股/港股/外汇等（Yahoo Finance chart 接口，免密钥）。
 * 用 chart 端点而不是 v7/quote —— 后者现在需要 cookie + crumb。
 */

const ENDPOINT = 'https://query1.finance.yahoo.com/v8/finance/chart/';
const REQUEST_TIMEOUT_MS = 8000;

async function fetchOne(symbol) {
  const url = `${ENDPOINT}${encodeURIComponent(symbol.trim())}?interval=1d&range=1d`;
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'Mozilla/5.0', Accept: 'application/json' },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    const meta = json?.chart?.result?.[0]?.meta;
    if (!meta || typeof meta.regularMarketPrice !== 'number') {
      throw new Error(json?.chart?.error?.description || '无数据');
    }
    const price = meta.regularMarketPrice;
    const prevClose =
      typeof meta.previousClose === 'number' ? meta.previousClose : meta.chartPreviousClose;
    const change = typeof prevClose === 'number' ? price - prevClose : null;
    return {
      symbol,
      name: meta.shortName || meta.longName || meta.symbol || symbol,
      price,
      prevClose: prevClose ?? null,
      change,
      changePercent: change !== null && prevClose ? (change / prevClose) * 100 : null,
      currency: meta.currency || '',
      time: Date.now(),
      error: null,
    };
  } catch (err) {
    return {
      symbol,
      name: symbol,
      price: null,
      prevClose: null,
      change: null,
      changePercent: null,
      currency: '',
      time: Date.now(),
      error: err.name === 'TimeoutError' ? '请求超时' : err.message,
    };
  }
}

async function fetchQuotes(symbols) {
  return Promise.all(symbols.map(fetchOne));
}

module.exports = {
  id: 'yahoo',
  label: '美股/港股 · Yahoo Finance（免密钥）',
  placeholder: 'AAPL, MSFT, 0700.HK, BTC-USD',
  fetchQuotes,
};
