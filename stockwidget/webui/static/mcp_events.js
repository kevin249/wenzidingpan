'use strict';

(() => {
  const token = document.body.dataset.token;
  const api = (path) => `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;

  const footer = document.querySelector('footer.bar');
  if (!footer) return;

  const card = document.createElement('section');
  card.className = 'card';
  card.innerHTML = `
    <h2>B/S 波动点来源</h2>
    <div class="grid">
      <div class="field">
        <label for="bs-source-select">B/S 计算来源（二选一）</label>
        <select id="bs-source-select">
          <option value="local">本地计算</option>
          <option value="mcp">MCP 订阅</option>
        </select>
        <p class="hint">选择 MCP 时，只显示 MCP 通道收到的当日 B/S 信号；本地反转算法不再参与。</p>
      </div>
      <div class="field">
        <label for="mcp-event-sort">当日 MCP 信息排序</label>
        <select id="mcp-event-sort">
          <option value="time">按时间</option>
          <option value="stock">按股票</option>
        </select>
        <p class="hint" id="mcp-event-count">正在读取...</p>
      </div>
    </div>
    <div id="mcp-event-list"></div>
  `;
  footer.parentNode.insertBefore(card, footer);

  const sourceSelect = document.getElementById('bs-source-select');
  const sortSelect = document.getElementById('mcp-event-sort');
  const count = document.getElementById('mcp-event-count');
  const list = document.getElementById('mcp-event-list');
  let events = [];

  function stockLabel(item) {
    const code = item.stock_code || '';
    const name = item.stock_name || '';
    if (name && code) return `${name} ${code}`;
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
    count.textContent = `今日共 ${events.length} 条；按类别分组，${sortSelect.value === 'stock' ? '组内按股票' : '组内按时间'}排序。`;
    if (!events.length) {
      const empty = document.createElement('p');
      empty.className = 'hint';
      empty.textContent = '今天尚未收到 MCP 订阅事件。';
      list.append(empty);
      return;
    }

    const groups = new Map();
    for (const item of events) {
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
      table.style.width = '100%';
      table.style.borderCollapse = 'collapse';
      table.style.fontSize = '13px';
      table.innerHTML = '<thead><tr><th>时间</th><th>股票</th><th>类型</th><th>内容</th></tr></thead>';
      const body = document.createElement('tbody');

      for (const item of sortedRows(groups.get(category))) {
        const row = document.createElement('tr');
        const values = [
          (item.created_at_local || '').slice(11, 19) || '-',
          stockLabel(item),
          item.side ? `${item.side} · ${item.event_type || ''}` : (item.event_type || '-'),
          item.title || item.body || '-',
        ];
        for (const value of values) {
          const cell = document.createElement('td');
          cell.textContent = value;
          cell.style.padding = '6px 8px';
          cell.style.borderTop = '1px solid rgba(127,127,127,.22)';
          cell.style.verticalAlign = 'top';
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
      count.textContent = `B/S 来源读取失败：${error.message}`;
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
    } catch (error) {
      count.textContent = `B/S 来源保存失败：${error.message}`;
    }
  }

  async function loadEvents() {
    try {
      const response = await fetch(api('/api/mcp-events'));
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      events = (await response.json()).events || [];
      render();
    } catch (error) {
      count.textContent = `MCP 列表读取失败：${error.message}`;
    }
  }

  sourceSelect.addEventListener('change', saveSource);
  sortSelect.addEventListener('change', render);
  loadSource();
  loadEvents();
  window.setInterval(loadEvents, 5000);
})();
