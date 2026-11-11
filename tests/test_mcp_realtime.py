import asyncio
import hashlib
import json
from types import SimpleNamespace

from stockwidget.mcp_realtime import ClientJournal, read_depth


def test_journal_transaction_reopens_and_dedupes(tmp_path):
    path = tmp_path / 'events.db'
    page = dict(events=[dict(event_id='e1', price='12.34')], next_cursor='signed-cursor')
    first = ClientJournal(path, 'alice')
    assert 1 == len(first.apply(page))
    reopened = ClientJournal(path, 'alice')
    assert 'signed-cursor' == reopened.cursor()
    assert [] == reopened.apply(page)
    assert '' == ClientJournal(path, 'bob').cursor()
    reopened.reset()
    assert [] == reopened.all_events()
    assert '' == reopened.cursor()


def test_bad_page_rolls_back_events_and_cursor(tmp_path):
    journal = ClientJournal(tmp_path / 'events.db', 'alice')
    try:
        journal.apply(dict(events=[dict(event_id='e1'), {}], next_cursor='bad'))
    except KeyError:
        pass
    assert '' == journal.cursor()
    assert [] == journal.all_events()


def test_recovered_invalidations_suppress_obsolete_popups(tmp_path):
    journal = ClientJournal(tmp_path / 'events.db', 'alice')
    created = dict(event_id='created', signal_id='signal', revision=1, status='active')
    invalidated = dict(event_id='invalid', signal_id='signal', revision=2, status='invalidated')
    journal.apply(dict(events=[created, invalidated], next_cursor='last'))
    assert journal.alertable(created) is False
    assert journal.alertable(invalidated) is False


def test_initial_state_does_not_mark_unread_events_as_consumed(tmp_path):
    journal = ClientJournal(tmp_path / 'events.db', 'alice')
    created = dict(event_id='created', signal_id='signal', revision=1, status='active')
    journal.seed_states([created])
    fresh = journal.apply(dict(events=[created], next_cursor='last'))
    assert [created] == fresh
    assert journal.alertable(created)


def test_depth_reassembles_pinned_version_and_checksum():
    sides = dict(bids=[['12.34', 100]], asks=[['12.35', 200]])
    checksum = hashlib.sha256(json.dumps(sides, separators=(',', ':')).encode()).hexdigest()
    calls = []
    class Session:
        async def call_tool(self, name, arguments):
            calls.append(arguments)
            index = arguments.get('part_index', 0)
            value = dict(snapshot_id='one', checksum=checksum, part_count=2, part_index=index,
                available=True, bids=sides['bids'] if index == 0 else [], asks=sides['asks'] if index == 1 else [])
            return SimpleNamespace(is_error=False, structured_content=value)
    result = asyncio.run(read_depth(Session(), '603986'))
    assert 2 == len(result['levels'])
    assert 'one' == calls[1]['snapshot_id']


def test_depth_worker_has_its_own_event_loop_and_stops(monkeypatch):
    import threading
    from stockwidget.mcp_realtime_depth import DepthWorker
    started = threading.Event()
    thread_ids = []
    async def serve(self):
        self.loop, self.task = asyncio.get_running_loop(), asyncio.current_task()
        thread_ids.append(threading.get_ident())
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(DepthWorker, '_serve', serve)
    worker = DepthWorker(SimpleNamespace(), SimpleNamespace())
    worker.start()
    assert started.wait(2)
    assert threading.get_ident() != thread_ids[0]
    worker.close()
    worker.thread.join(2)
    assert worker.thread.is_alive() is False
