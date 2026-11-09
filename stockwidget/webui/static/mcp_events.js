'use strict';

(() => {
  const token = document.body.dataset.token;
  const api = (path) => `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;

  const anchor = document.getElementById('common-theme-settings');
  if (!anchor) return;

  const card = document.createElement('section');
  card.className = 'card';
  card.dataset.settingsScope = 'common';
  card.innerHTML = `
    <h2>公共设置 · B/S 波动点来源</h2>
    <div class="grid">
      <div class="field">
        <label for="bs-source-select">B/S 计算来源（二选一）</label>
        <select id="bs-source-select">
          <option value="local">本地计算 · 逃顶王 TDW</option>
          <option value="mcp">MCP 订阅</option>
        </select>
        <p class="hint">实时价格按秒 K 显示，TDW 使用聚合后的 1 分钟 OHLC 计算。图上的箭头来自本地 TDW，仅标记上穿最高紫线、下穿最低绿线，并对齐到触发分钟的最后一笔成交。</p>
      </div>
    </div>
    <div class="reminder-actions">
      <button type="button" id="open-reminders" aria-haspopup="dialog">查看各类提醒</button>
      <p class="hint" id="mcp-event-summary" aria-live="polite">正在读取提醒…</p>
    </div>
    <p class="hint" id="bs-source-status" role="status"></p>
  `;
  anchor.parentNode.insertBefore(card, anchor);

  const dialog = document.createElement('dialog');
  dialog.className = 'reminder-dialog';
  dialog.setAttribute('aria-labelledby', 'reminder-heading');
  dialog.innerHTML = `
    <div class="dialog-header">
      <h2 id="reminder-heading">今日提醒列表</h2>
      <button type="button" id="close-reminders" autofocus>关闭</button>
    </div>
    <div class="grid">
      <div class="field">
        <label for="mcp-event-category">提醒类别</label>
        <select id="mcp-event-category"><option value="">全部类别</option></select>
      </div>
      <div class="field">
        <label for="mcp-event-sort">排序方式</label>
        <select id="mcp-event-sort">
          <option value="time">按时间</option>
          <option value="stock">按股票</option>
        </select>
      </div>
    </div>
    <p class="hint" id="mcp-event-count" role="status"></p>
    <div id="mcp-event-list"></div>
  `;
  document.body.append(dialog);

  const sourceSelect = document.getElementById('bs-source-select');
  const sortSelect = document.getElementById('mcp-event-sort');
  const count = document.getElementById('mcp-event-count');
  const list = document.getElementById('mcp-event-list');
  const summary = document.getElementById('mcp-event-summary');
  const sourceStatus = document.getElementById('bs-source-status');
  const categorySelect = document.getElementById('mcp-event-category');
  const openButton = document.getElementById('open-reminders');
  openButton.addEventListener('click', () => { dialog.showModal(); loadEvents(); });
  document.getElementById('close-reminders').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => openButton.focus());
  dialog.addEventListener('click', (event) => {
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right ||
      event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
  });
  categorySelect.addEventListener('change', render);
  let events = [];

  function updateCategories() {
    const selected = categorySelect.value;
    const categories = [...new Set(events.map(item => item.category || '其他'))].sort((a, b) => a.localeCompare(b, 'zh-CN'));
    categorySelect.replaceChildren(new Option('全部类别', ''));
    for (const category of categories) categorySelect.add(new Option(category, category));
    categorySelect.value = categories.includes(selected) ? selected : '';
  }

  function stockLabel(item) {
    const code = item.stock_code || '';
    const name = item.stock_name || '';
    return name || code || '-';
  }

  function sortedRows(rows) {
    const result = [...rows];
    if (sortSelect.value === 'stock') {
      result.sort((a, b) => {
        const stock = stockLabel(a).localeCompare(stockLabel(b), 'zh-CN');
        if (stock) return stock;
        return String(b.created_at_local || '').localeCompare(String(a.created_at_local || ''));
      });
    } else {
      result.sort((a, b) =>
        String(b.created_at_local || '').localeCompare(String(a.created_at_local || ''))
      );
    }
    return result;
  }

  function render() {
    list.innerHTML = '';
    const visibleEvents = events.filter(item => !categorySelect.value || (item.category || '其他') === categorySelect.value);
    summary.textContent = `今日 ${events.length} 条提醒 · 每 5 秒自动更新`;
    count.textContent = `今日共 ${events.length} 条，当前显示 ${visibleEvents.length} 条；按类别分组，${sortSelect.value === 'stock' ? '组内按股票' : '组内按时间'}排序。`;
    if (!visibleEvents.length) {
      const empty = document.createElement('p');
      empty.className = 'hint';
      empty.textContent = '今天尚未收到 MCP 订阅事件。';
      list.append(empty);
      return;
    }

    const groups = new Map();
    for (const item of visibleEvents) {
      const category = item.category || '其他';
      if (!groups.has(category)) groups.set(category, []);
      groups.get(category).push(item);
    }

    for (const category of [...groups.keys()].sort((a, b) => a.localeCompare(b, 'zh-CN'))) {
      const section = document.createElement('div');
      section.style.marginTop = '14px';

      const heading = document.createElement('div');
      heading.style.fontWeight = '700';
      heading.style.marginBottom = '6px';
      heading.textContent = `${category}（${groups.get(category).length}）`;
      section.append(heading);

      const tableWrap = document.createElement('div');
      tableWrap.style.overflowX = 'auto';
      const table = document.createElement('table');
      table.className = 'reminder-table';
      table.innerHTML = '<thead><tr><th>时间</th><th>股票</th><th>类型</th><th>内容</th></tr></thead>';
      const body = document.createElement('tbody');

      for (const item of sortedRows(groups.get(category))) {
        const row = document.createElement('tr');
        const values = [
          (item.created_at_local || '').slice(11, 19) || '-',
          stockLabel(item),
          item.display_type || item.event_type || '-',
          item.display_content || item.body || item.title || '-',
        ];
        for (const value of values) {
          const cell = document.createElement('td');
          cell.textContent = value;
          cell.style.padding = '6px 8px';
          cell.style.borderTop = '1px solid rgba(127,127,127,.22)';
          cell.style.verticalAlign = 'top';
          cell.style.whiteSpace = 'pre-line';
          row.append(cell);
        }
        body.append(row);
      }
      table.append(body);
      tableWrap.append(table);
      section.append(tableWrap);
      list.append(section);
    }
  }

  async function loadSource() {
    try {
      const response = await fetch(api('/api/mcp-bs-source'));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      sourceSelect.value = (await response.json()).source || 'local';
    } catch (error) {
      sourceStatus.textContent = `B/S 来源读取失败：${error.message}`;
    }
  }

  async function saveSource() {
    try {
      const response = await fetch(api('/api/mcp-bs-source'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({source: sourceSelect.value}),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      sourceSelect.value = (await response.json()).source;
      sourceStatus.textContent = 'B/S 来源已保存';
    } catch (error) {
      sourceStatus.textContent = `B/S 来源保存失败：${error.message}`;
    }
  }

  async function loadEvents() {
    try {
      const response = await fetch(api('/api/mcp-events'));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      events = (await response.json()).events || [];
      updateCategories();
      render();
    } catch (error) {
      summary.textContent = count.textContent = `MCP 列表读取失败：${error.message}`;
    }
  }

  sourceSelect.addEventListener('change', saveSource);
  sortSelect.addEventListener('change', render);
  loadSource();
  loadEvents();
  window.setInterval(loadEvents, 5000);
})();
