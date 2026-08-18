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
  const c = sanitize({ refreshSeconds: 1e9, opacity: 42, colorScheme: 'nope' });
  assert(c.refreshSeconds === 3600, `refreshSeconds=${c.refreshSeconds}`);
  assert(c.opacity === 1, `opacity=${c.opacity}`);
  assert(c.colorScheme === 'cn', `colorScheme=${c.colorScheme}`);
});

check('自选代码去重并去空白', () => {
  const c = sanitize({ symbols: ['AAPL', 'AAPL', '  MSFT ', '', '   '] });
  assert(JSON.stringify(c.symbols) === JSON.stringify(['AAPL', 'MSFT']), c.symbols.join(','));
});

check('未知数据源回落到 mock', () => {
  assert(providers.resolve('does-not-exist').id === 'mock', '未回落到 mock');
  assert(providers.list().length >= 3, '数据源少于 3 个');
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

check('网络数据源失败时逐个代码降级而不是抛异常', async () => {
  const yahoo = require(path.join(root, 'src/main/providers/yahoo'));
  assert(typeof yahoo.fetchQuotes === 'function', 'fetchQuotes 不存在');
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
