'use strict';
(() => {
  const button = document.getElementById('mcp-test-start');
  const summary = document.getElementById('mcp-test-summary');
  const results = document.getElementById('mcp-test-results');
  const labels = { running: '测试中', pass: '通过', fail: '失败', warn: '待检查', pending: '等待' };
  let polling;
  function row(item) {
    const li = document.createElement('li');
    li.dataset.status = item.status;
    li.textContent = `${item.label}：${labels[item.status] || item.status}${item.milliseconds == null ? '' : ` · ${item.milliseconds} 毫秒`}${item.detail ? ` · ${item.detail}` : ''}`;
    return li;
  }
  async function refresh(remaining = 10) {
    try {
      const response = await fetch(api('/api/mcp-test'));
      if (!response.ok) throw new Error(`读取测试结果失败（${response.status}）`);
      const value = await response.json();
      results.replaceChildren(...value.steps.map(row));
      if (value.depth) results.append(row({ label: '窗口显示', ...value.display }));
      summary.textContent = value.error ? `测试未完成：${value.error}` : value.running ? `正在测试 ${value.symbol || ''}…` : '测试完成，请查看各项结果。';
      button.disabled = value.running;
      if (value.running || (value.depth && value.display.status === 'pending' && remaining > 0)) {
        polling = setTimeout(() => refresh(value.running ? 10 : remaining - 1), 500);
      }
    } catch (error) { summary.textContent = error.message; button.disabled = false; }
  }
  button.addEventListener('click', async () => {
    clearTimeout(polling);
    button.disabled = true;
    summary.textContent = '正在保存连接设置并启动测试…';
    results.replaceChildren();
    try {
      const patch = collect();
      patch.display_theme = activeTheme;
      fill(await postConfig(patch));
      const response = await fetch(api('/api/mcp-test'), { method: 'POST' });
      if (!response.ok) throw new Error(`启动测试失败（${response.status}）`);
      const updated = await fetch(api('/api/config'));
      if (!updated.ok) throw new Error('读取显示设置失败');
      fill((await updated.json()).config);
      await refresh();
    } catch (error) { summary.textContent = error.message; button.disabled = false; }
  });
})();
