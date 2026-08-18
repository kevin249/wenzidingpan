'use strict';

/**
 * 东方财富暗盘资金。
 *
 * 口径与字段映射对齐 gupiao_ztfx 的 `src/dark_trade/eastmoney.py`：
 * 接口返回的是「某个交易日的暗盘排行榜」，按暗盘资金降序分页，
 * 所以这里翻页收集，直到自选股都命中或翻到页数上限为止。
 *
 * 返回体形如：
 *   { errid: 0, "1": "20260817", "2": 总条数, data: [ { "4": 代码, "6": 暗盘资金, ... } ] }
 */

const ENDPOINT = 'https://quotederivates.eastmoney.com/datacenter/darktrade';
const PAGE_SIZE = 100;
const MAX_PAGES = 50; // 5000 条，足够覆盖一天有暗盘成交的个股
const PRICE_DIVISOR = 1000; // 接口里的价格放大了 1000 倍
const CACHE_TTL_MS = 10 * 60 * 1000; // 日频数据，不必跟行情同频刷新
const REQUEST_TIMEOUT_MS = 10000;

// 接口字段是数字键，含义按 gupiao_ztfx 的映射表。
const F = {
  MARKET: '3',
  CODE: '4',
  DARK_FUND: '6',
  OPEN_FUND: '7',
  MAIN_NET_INFLOW: '8',
  ACTIVITY: '11',
  PRICE: '13',
  CHANGE_PERCENT: '14',
  NAME: '16',
  RANK: '21',
};

const num = (v) => {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** 接口要 YYYYMMDD；本地日期即可，返回体里会给出实际交易日。 */
function todayStamp(now = new Date()) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
}

function normalizeRow(row) {
  if (!row || typeof row !== 'object') return null;
  const digits = String(row[F.CODE] ?? '').replace(/\D/g, '');
  if (!digits) return null;
  const price = num(row[F.PRICE]);
  return {
    code: digits.padStart(6, '0'),
    name: String(row[F.NAME] ?? '').trim(),
    market: num(row[F.MARKET]) === 1 ? 'sh' : 'sz',
    darkFund: num(row[F.DARK_FUND]) ?? 0, // 单位：元
    openFund: num(row[F.OPEN_FUND]) ?? 0,
    mainNetInflow: num(row[F.MAIN_NET_INFLOW]) ?? 0,
    activity: num(row[F.ACTIVITY]) ?? 0,
    price: price === null ? null : price / PRICE_DIVISOR,
    changePercent: num(row[F.CHANGE_PERCENT]),
    rank: num(row[F.RANK]),
  };
}

async function requestPage(dateStamp, page) {
  const params = new URLSearchParams({
    version: '101',
    cver: '100',
    date: dateStamp,
    StartPage: String(page),
    NumPerPage: String(PAGE_SIZE),
    sortflag: '6',
    desc: '1',
    market: '',
    datetype: '',
  });
  const res = await fetch(`${ENDPOINT}?${params}`, {
    headers: {
      'User-Agent': 'Mozilla/5.0',
      Accept: 'application/json,text/plain,*/*',
      Referer: 'https://emrnweb.eastmoney.com/graymarket/rankList',
      rnProjectId: 'emrn.GrayMarketRank',
    },
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const payload = await res.json();
  if (!payload || typeof payload !== 'object') throw new Error('返回格式异常');
  if (num(payload.errid) !== 0) {
    throw new Error(String(payload.errmsg || '接口返回错误').slice(0, 60));
  }
  return payload;
}

/** 把 "20260817" 变成 "2026-08-17"，拿不到就返回空串。 */
function formatDate(value) {
  const text = String(value ?? '').replace(/-/g, '');
  return /^\d{8}$/.test(text) ? `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6)}` : '';
}

let cache = null; // { at, date, byCode, error, codes }

/**
 * 取指定代码的暗盘资金。
 * @param {string[]} codes 6 位股票代码
 * @returns {Promise<{ date: string, byCode: Map<string, object>, error: string|null }>}
 */
async function getDarkTrade(codes, { now = Date.now() } = {}) {
  const wanted = new Set(codes);
  const cacheUsable =
    cache &&
    now - cache.at < CACHE_TTL_MS &&
    // 自选变了且新代码上次没覆盖到时，重新拉一次
    [...wanted].every((code) => cache.codes.has(code));
  if (cacheUsable) return { date: cache.date, byCode: cache.byCode, error: cache.error };

  const byCode = new Map();
  let date = '';
  let error = null;

  try {
    const dateStamp = todayStamp();
    const first = await requestPage(dateStamp, 1);
    date = formatDate(first['1']);
    const total = num(first['2']) ?? 0;
    const pages = Math.min(MAX_PAGES, Math.max(1, Math.ceil(total / PAGE_SIZE)));

    const absorb = (payload) => {
      for (const raw of payload?.data || []) {
        const row = normalizeRow(raw);
        if (row) byCode.set(row.code, row);
      }
    };
    absorb(first);

    for (let page = 2; page <= pages; page++) {
      // 自选股全部命中就不必继续翻页了。
      if ([...wanted].every((code) => byCode.has(code))) break;
      absorb(await requestPage(dateStamp, page));
    }
  } catch (err) {
    error = err.name === 'TimeoutError' ? '请求超时' : err.message;
  }

  cache = { at: now, date, byCode, error, codes: wanted };
  return { date, byCode, error };
}

/** 仅供测试：清掉进程内缓存。 */
function resetCache() {
  cache = null;
}

module.exports = { getDarkTrade, normalizeRow, formatDate, todayStamp, resetCache, PRICE_DIVISOR };
