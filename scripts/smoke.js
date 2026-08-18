#!/usr/bin/env node
/**
 * 冒烟测试：先跑纯 Node 的逻辑检查，再真正启动一次 Electron，
 * 确认窗口能加载、渲染进程没有报错（含 CSP 违规）。
 *
 *   npm run smoke
 *
 * 无图形环境时会自动套一层 xvfb-run。
 */
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const RUN_SECONDS = Number(process.env.SMOKE_SECONDS || 8);

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failures++;
    console.error(`  ✗ ${name}\n    ${err.message}`);
  }
}
const assert = (cond, msg) => {
  if (!cond) throw new Error(msg);
};

/* ------------------------------------------------------------ 逻辑检查 */

console.log('逻辑检查');

const { sanitize, DEFAULTS } = require(path.join(root, 'src/main/store'));
const providers = require(path.join(root, 'src/main/providers'));

check('配置为空时回落到默认值', () => {
  assert(sanitize(null).provider === DEFAULTS.provider, 'provider 未回落');
  assert(sanitize(undefined).symbols.length > 0, '自选列表不应为空');
});

check('越界配置被夹到合法区间', () => {
  const c = sanitize({
    refreshSeconds: 1e9, opacity: 42, colorScheme: 'nope',
    visibleRows: 999, fontSize: 2, layout: 'diagonal',
  });
  assert(c.refreshSeconds === 3600, `refreshSeconds=${c.refreshSeconds}`);
  assert(c.opacity === 1, `opacity=${c.opacity}`);
  assert(c.colorScheme === 'cn', `colorScheme=${c.colorScheme}`);
  assert(c.visibleRows === 30, `visibleRows=${c.visibleRows}`);
  assert(c.fontSize === 9, `fontSize=${c.fontSize}`);
  assert(c.layout === 'multi', `layout=${c.layout}`);
});

check('字体名只接受安全字符', () => {
  assert(sanitize({ fontFamily: '微软雅黑, PingFang SC' }).fontFamily === '微软雅黑, PingFang SC');
  assert(sanitize({ fontFamily: 'x;} body{display:none}' }).fontFamily === '', '样式注入未被拒绝');
});

check('自选代码去重并去空白', () => {
  const c = sanitize({ symbols: ['AAPL', 'AAPL', '  MSFT ', '', '   '] });
  assert(JSON.stringify(c.symbols) === JSON.stringify(['AAPL', 'MSFT']), c.symbols.join(','));
});

check('未知数据源回落到默认的东财', () => {
  assert(providers.resolve('does-not-exist').id === 'eastmoney', '未回落到 eastmoney');
  assert(providers.DEFAULT_PROVIDER === 'eastmoney', '默认数据源不是东财');
  const ids = providers.list().map((p) => p.id);
  assert(!ids.includes('yahoo'), '美股数据源应已移除');
  for (const id of ['eastmoney', 'tencent', 'sina', 'mock']) {
    assert(ids.includes(id), `缺少数据源 ${id}`);
  }
});

check('A 股代码归一化覆盖沪深北与各种写法', () => {
  const { classify } = require(path.join(root, 'src/main/providers/symbols'));
  const cases = [
    ['600519', 'sh'],
    ['688981', 'sh'],
    ['000001', 'sz'],
    ['300750', 'sz'],
    ['830799', 'bj'],
    ['sh600000', 'sh'],
    ['SZ000001', 'sz'],
    ['600519.SH', 'sh'],
  ];
  for (const [input, market] of cases) {
    const got = classify(input);
    assert(got && got.market === market, `${input} -> ${JSON.stringify(got)}`);
  }
  assert(classify('不是代码') === null, '非法代码应返回 null');
});

check('新浪/腾讯解析停牌与缺字段', () => {
  const tencent = require(path.join(root, 'src/main/providers/tencent'));
  const sina = require(path.join(root, 'src/main/providers/sina'));
  const t = tencent.parse('v_sh600000="1~浦发银行~600000~0.00~10.00~9.9~1~2~3";');
  assert(t.get('sh600000').halted === true, '腾讯停牌未识别');
  assert(t.get('sh600000').price === 10, '腾讯停牌未回退昨收');
  const s = sina.parse('var hq_str_sh600000="浦发银行,9.9,10.00,10.20,";');
  assert(Math.abs(s.get('sh600000').changePercent - 2) < 1e-9, '新浪涨跌幅算错');
  assert(sina.parse('var hq_str_sh600001="";').get('sh600001') === null, '空数据未置空');
});

check('东财价格缩放自动识别', () => {
  const eastmoney = require(path.join(root, 'src/main/providers/eastmoney'));
  assert(eastmoney.detectScale([{ f2: 10.2, f18: 10, f3: 2 }]) === 1, '正常数据被误判为缩放');
  assert(eastmoney.detectScale([{ f2: 1020, f18: 1000, f3: 200 }]) === 100, '缩放数据未识别');
});

check('暗盘资金字段映射与价格还原', () => {
  const darktrade = require(path.join(root, 'src/main/darktrade'));
  const row = darktrade.normalizeRow({
    3: 1, 4: '600519', 16: '贵州茅台', 6: 123456789, 8: -5000, 13: 1680000, 14: 2.35, 21: 7,
  });
  assert(row.code === '600519' && row.market === 'sh', `代码/市场解析错误: ${JSON.stringify(row)}`);
  assert(row.darkFund === 123456789, '暗盘资金未取到');
  assert(row.price === 1680, `价格未按 1000 还原: ${row.price}`);
  assert(darktrade.normalizeRow({ 4: '' }) === null, '无代码的行未被剔除');
  assert(darktrade.formatDate('20260817') === '2026-08-17', '日期格式化错误');
});

check('mock 数据源返回完整行情结构', () => {
  const q = require('child_process').execSync(
    `node -e "require('${path.join(root, 'src/main/providers')}').resolve('mock')` +
      `.fetchQuotes(['DEMO']).then(r=>console.log(JSON.stringify(r[0])))"`,
    { encoding: 'utf8' }
  );
  const quote = JSON.parse(q);
  for (const key of ['symbol', 'price', 'prevClose', 'change', 'changePercent']) {
    assert(quote[key] !== undefined, `缺少字段 ${key}`);
  }
  assert(Number.isFinite(quote.price) && quote.price > 0, `价格异常: ${quote.price}`);
});

check('渲染层资源齐全', () => {
  for (const f of [
    'src/renderer/index.html',
    'src/renderer/renderer.js',
    'src/renderer/styles.css',
    'src/renderer/settings.html',
    'src/renderer/settings.js',
    'src/renderer/settings.css',
    'assets/tray.png',
  ]) {
    assert(fs.existsSync(path.join(root, f)), `缺少 ${f}`);
  }
});

/* ---------------------------------------------------------- 启动检查 */

function electronBinary() {
  try {
    return require(path.join(root, 'node_modules', 'electron'));
  } catch {
    return null;
  }
}

const bin = electronBinary();
if (!bin || !fs.existsSync(bin)) {
  console.log('\n跳过启动检查：未安装 electron（先执行 npm install）');
  process.exit(failures ? 1 : 0);
}

const hasDisplay = Boolean(process.env.DISPLAY);
const hasXvfb = spawnSync('which', ['xvfb-run']).status === 0;
if (!hasDisplay && !hasXvfb) {
  console.log('\n跳过启动检查：没有图形环境，也没有 xvfb-run');
  process.exit(failures ? 1 : 0);
}

console.log(`\n启动检查（运行 ${RUN_SECONDS}s${hasDisplay ? '' : '，经由 xvfb-run'}）`);

// 容器里常以 root 运行，Chromium 的 setuid sandbox 会拒绝启动，这里显式关掉。
const args = ['.', '--no-sandbox', '--enable-logging'];
const [cmd, argv] = hasDisplay ? [bin, args] : ['xvfb-run', ['-a', bin, ...args]];

// detached：xvfb-run 是层 shell 包装，信号不会转发给 Electron，
// 所以整组进程一起起、一起收，避免留下占住单实例锁的孤儿进程。
const child = spawn(cmd, argv, {
  cwd: root,
  detached: true,
  env: { ...process.env, ELECTRON_ENABLE_LOGGING: '1' },
});
let output = '';
child.stdout.on('data', (d) => (output += d));
child.stderr.on('data', (d) => (output += d));

let exitedEarly = null;
child.on('exit', (code, signal) => {
  if (!signal) exitedEarly = code;
});

setTimeout(() => {
  if (exitedEarly === null) {
    try {
      process.kill(-child.pid, 'SIGTERM');
    } catch {
      child.kill('SIGTERM');
    }
  }

  setTimeout(() => {
    check('进程保持运行未提前退出', () => {
      assert(exitedEarly === null, `进程提前退出，code=${exitedEarly}`);
    });
    check('无未捕获异常 / CSP 违规 / 资源加载失败', () => {
      const bad = output
        .split('\n')
        .filter((l) =>
          /Uncaught|Content Security Policy|Failed to load|ERR_FILE_NOT_FOUND|Unhandled/i.test(l)
        );
      assert(bad.length === 0, bad.slice(0, 5).join('\n    '));
    });

    if (failures) {
      console.error(`\n${failures} 项检查失败`);
      if (process.env.SMOKE_VERBOSE) console.error(output);
      process.exit(1);
    }
    console.log('\n全部检查通过');
    process.exit(0);
  }, 800);
}, RUN_SECONDS * 1000);
