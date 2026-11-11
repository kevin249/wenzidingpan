"""实时订阅消费与 SQLite 游标事务；与 AI 全量查询路径独立。"""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.client.subscriptions import listen

from .config import config_dir


def payload(result):
    structured = getattr(result, 'structured_content', None)
    if structured is not None:
        return structured
    return json.loads(next(item.text for item in result.content if hasattr(item, 'text')))


class ClientJournal:
    def __init__(self, path, scope):
        self.path, self.scope = Path(path), scope
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cursors (scope TEXT PRIMARY KEY, cursor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (scope TEXT, event_id TEXT, value TEXT NOT NULL,
                    PRIMARY KEY (scope, event_id));
                CREATE TABLE IF NOT EXISTS signal_states (scope TEXT, signal_id TEXT, revision INTEGER,
                    value TEXT NOT NULL, PRIMARY KEY(scope, signal_id));
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def cursor(self):
        with self.connect() as db:
            row = db.execute('SELECT cursor FROM cursors WHERE scope=?', (self.scope,)).fetchone()
        return row[0] if row else ''

    def apply(self, page):
        fresh = []
        with self.connect() as db:
            for event in page['events']:
                changed = db.execute('INSERT OR IGNORE INTO events VALUES (?,?,?)',
                    (self.scope, event['event_id'], json.dumps(event, ensure_ascii=False))).rowcount
                if changed:
                    fresh.append(event)
                if event.get('signal_id'):
                    db.execute('''INSERT INTO signal_states VALUES (?,?,?,?)
                        ON CONFLICT(scope,signal_id) DO UPDATE SET revision=excluded.revision,value=excluded.value
                        WHERE excluded.revision>signal_states.revision''',
                        (self.scope, event['signal_id'], event['revision'], json.dumps(event)))
            db.execute('INSERT INTO cursors VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET cursor=excluded.cursor',
                       (self.scope, page['next_cursor']))
        return fresh

    def all_events(self):
        with self.connect() as db:
            rows = db.execute('SELECT value FROM events WHERE scope=? ORDER BY rowid', (self.scope,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def seed_states(self, events):
        with self.connect() as db:
            for event in events:
                if event.get('signal_id'):
                    db.execute('''INSERT INTO signal_states VALUES (?,?,?,?)
                        ON CONFLICT(scope,signal_id) DO UPDATE SET revision=excluded.revision,value=excluded.value
                        WHERE excluded.revision>signal_states.revision''',
                        (self.scope, event['signal_id'], event['revision'], json.dumps(event)))

    def reset(self):
        with self.connect() as db:
            db.execute('DELETE FROM cursors WHERE scope=?', (self.scope,))
            db.execute('DELETE FROM events WHERE scope=?', (self.scope,))
            db.execute('DELETE FROM signal_states WHERE scope=?', (self.scope,))

    def alertable(self, event):
        if event.get('status') not in (None, 'active', 'confirmed'):
            return False
        if not event.get('signal_id'):
            return True
        with self.connect() as db:
            row = db.execute('SELECT revision,value FROM signal_states WHERE scope=? AND signal_id=?',
                             (self.scope, event['signal_id'])).fetchone()
        return bool(row and row[0] == event['revision'] and json.loads(row[1]).get('status') in (None, 'active', 'confirmed'))


async def _call(session, name, arguments=None):
    result = await asyncio.wait_for(session.call_tool(name, arguments=arguments or {}), timeout=15)
    if result.is_error:
        raise RuntimeError(next((item.text for item in result.content if hasattr(item, 'text')), name))
    return payload(result)


async def run_cancellable(coro, cancel_event):
    """配置变更或退出时也能取消握手、恢复分页和快照读取。"""
    task = asyncio.create_task(coro)
    stop = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait([task, stop], return_when=asyncio.FIRST_COMPLETED)
        if stop not in done:
            return await task
    finally:
        for pending in (task, stop):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, stop, return_exceptions=True)


async def _drain(session, journal, cursor, *, recovery_token='', symbols=()):
    fresh = []
    while True:
        page = await _call(session, 'get_event_delta', dict(cursor=cursor, recovery_token=recovery_token,
                                                         symbols=list(symbols), limit=100))
        fresh.extend(journal.apply(page))
        cursor = page['next_cursor']
        if not page['has_more']:
            return cursor, fresh


async def read_depth(session, symbol):
    first = await _call(session, 'get_depth_snapshot', dict(symbol=symbol))
    parts = [first]
    for index in range(1, first['part_count']):
        parts.append(await _call(session, 'get_depth_snapshot',
                     dict(symbol=symbol, snapshot_id=first['snapshot_id'], part_index=index)))
    sides = {side: [row for part in parts for row in part[side]] for side in ('bids', 'asks')}
    encoded = json.dumps(sides, ensure_ascii=False, separators=(',', ':')).encode()
    if hashlib.sha256(encoded).hexdigest() != first['checksum']:
        raise ValueError('DEPTH_CHECKSUM_MISMATCH')
    levels = [dict(side=side, price=row[0], volume=row[1])
              for side, key in [('bid', 'bids'), ('ask', 'asks')] for row in sides[key]]
    return dict(first, levels=levels, verified=first['available'],
                service_ready=first['available'], method='realtime_depth_snapshot')


async def run_listener(listener, config, cancel_event):
    from .mcp_notifications import SSE_TIMEOUT, _api_key
    from .mcp_bs import reset_realtime_cache
    url, key = config.mcp_realtime_url, _api_key(config)
    from .symbols import classify
    symbols = sorted({item.code for symbol in config.symbols if (item := classify(symbol)) is not None})
    # 事件范围是当前用户全部授权通知；自选股只约束盘口，避免丢失无股票代码的系统/选股事件。
    event_symbols = ()
    scope = hashlib.sha256(json.dumps([url, key, 'all-events-v1']).encode()).hexdigest()
    journal = ClientJournal(config_dir() / 'mcp_realtime.sqlite3', scope)
    headers = {'Authorization': 'Bearer ' + key}
    async with httpx2.AsyncClient(headers=headers, timeout=SSE_TIMEOUT) as client:
        async with streamable_http_client(url, http_client=client, terminate_on_close=False) as (rd, wr):
            async with ClientSession(rd, wr) as session:
                await asyncio.wait_for(session.discover(), 15)
                stream = await _call(session, 'get_event_stream')
                uris = [stream['resource_uri'], stream['control_uri']]
                uris += [stream['depth_uri_template'].replace('{code}', symbol) for symbol in symbols]
                async with listen(session, resource_subscriptions=uris) as subscription:
                    honored = set(getattr(subscription.honored, 'resource_subscriptions', None) or ())
                    if not set(uris).issubset(honored):
                        raise RuntimeError('实时服务未确认全部资源订阅')
                    cursor = journal.cursor()
                    recovery = await recover(session, journal, cursor, symbols=event_symbols, listener=listener)
                    reset_realtime_cache()
                    journal.seed_states(recovery['signals'])
                    cursor, new = await _drain(session, journal, recovery['cursor'],
                        recovery_token=recovery['recovery_token'], symbols=event_symbols)
                    for event in journal.all_events() + recovery['signals']:
                        emit_event(listener, event, alert=False)
                    for event in new:
                        emit_event(listener, event, alert=not recovery['initial'] and journal.alertable(event))
                    listener._emit_status('已连接 · 实时增量 / 游标已恢复')
                    from .mcp_realtime_depth import DepthWorker
                    depth_worker = DepthWorker(listener, config)
                    depth_updates = depth_worker.updates
                    for symbol in symbols:
                        depth_updates.put_nowait(symbol)
                    depth_worker.start()
                    try:
                        await consume(listener, session, subscription, journal=journal, cursor=cursor,
                                      symbols=symbols, cancel_event=cancel_event, depth_updates=depth_updates)
                    finally:
                        depth_worker.close()


async def recover(session, journal, cursor, *, symbols, listener):
    try:
        return await _call(session, 'begin_event_recovery', dict(cursor=cursor, symbols=list(symbols)))
    except RuntimeError as exc:
        if not any(code in str(exc) for code in ('CURSOR_EXPIRED', 'STREAM_RESET', 'FILTER_CHANGED')):
            raise
        listener._emit_status('恢复游标已失效，历史存在缺口；重新取得当前状态', level='WARN')
        journal.reset()
        return await _call(session, 'begin_event_recovery', dict(symbols=list(symbols)))


async def emit_depth(listener, session, symbol):
    from .mcp_depth import parse_depth_payload
    try:
        value = await read_depth(session, symbol)
    except RuntimeError:
        return False
    listener.depth_ready.emit(parse_depth_payload(symbol, value))
    return True


async def consume(listener, session, subscription, *, journal, cursor, symbols, cancel_event, depth_updates):
    updates = asyncio.Queue(maxsize=1)
    versions = {}
    async def watch():
        async for event in subscription:
            if updates.empty():
                updates.put_nowait(str(getattr(event, 'uri', '')))
    watcher = asyncio.create_task(watch())
    try:
        while not cancel_event.is_set():
            changed = asyncio.create_task(updates.get())
            stop = asyncio.create_task(cancel_event.wait())
            done, _ = await asyncio.wait([changed, stop, watcher], timeout=15, return_when=asyncio.FIRST_COMPLETED)
            for task in (changed, stop):
                if task not in done:
                    task.cancel()
            await asyncio.gather(changed, stop, return_exceptions=True)
            if stop in done:
                return
            if watcher in done:
                await watcher
                raise RuntimeError('实时订阅已结束')
            cursor, fresh = await _drain(session, journal, cursor)
            for event in fresh:
                emit_event(listener, event, alert=journal.alertable(event))
            state = await _call(session, 'get_stream_state')
            for symbol, version in state['depth_versions'].items():
                if symbol in symbols and versions.get(symbol) != version:
                    if not depth_updates.full():
                        depth_updates.put_nowait(symbol)
                        versions[symbol] = version
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)


def emit_event(listener, event, *, alert):
    from .mcp_notifications import McpNotification
    from .mcp_bs import record_notification
    title = f"{event['occurred_at'][11:19]} {event['event']} {event.get('price') or '-'} {event.get('name') or event['symbol']}"
    notification = McpNotification(event_id=event['event_id'], title=title, body='',
        event_type=event['event_type'], created_at=event['generated_at'], sequence=event['sequence'],
        payload=dict(code=event['symbol'][:6], name=event['name'], signal=event['event'], price=event['price'],
                     signal_at=event['occurred_at'], l2_confirmed=event['l2_confirmation'] == 'confirmed',
                     signal_id=event.get('signal_id', ''), revision=event.get('revision', 1),
                     status=event.get('status', 'active')))
    record_notification(notification)
    if event['event_type'] == 'trading.holding_t_signal':
        listener.bs_updated.emit(event['symbol'][:6])
    expires = datetime.fromisoformat(event['alert_until'])
    if alert and listener._config.mcp_notifications_enabled and expires > datetime.now(timezone.utc) and listener._remember(event['event_id']):
        listener.notification_ready.emit(notification)
