'use strict';

/**
 * Offline provider: a seeded random walk per symbol. Needs no network access
 * and no API key, so the widget is fully usable out of the box.
 */

const walks = new Map();

function seedFrom(symbol) {
  let h = 2166136261;
  for (let i = 0; i < symbol.length; i++) {
    h ^= symbol.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 0xffffffff;
}

function walkFor(symbol) {
  let walk = walks.get(symbol);
  if (!walk) {
    const seed = seedFrom(symbol);
    const base = 10 + seed * 290; // 10 .. 300
    walk = {
      prevClose: Math.round(base * 100) / 100,
      price: Math.round(base * 100) / 100,
      volatility: 0.002 + seed * 0.006,
    };
    walks.set(symbol, walk);
  }
  return walk;
}

async function fetchQuotes(symbols) {
  return symbols.map((symbol) => {
    const walk = walkFor(symbol);
    const drift = (Math.random() - 0.5) * 2 * walk.volatility;
    walk.price = Math.max(0.01, Math.round(walk.price * (1 + drift) * 100) / 100);
    const change = walk.price - walk.prevClose;
    return {
      symbol,
      name: symbol,
      price: walk.price,
      prevClose: walk.prevClose,
      change,
      changePercent: walk.prevClose ? (change / walk.prevClose) * 100 : 0,
      currency: '',
      time: Date.now(),
      error: null,
    };
  });
}

module.exports = {
  id: 'mock',
  label: '模拟数据（离线，无需网络）',
  placeholder: 'DEMO, TEST, ANY-SYMBOL',
  fetchQuotes,
};
