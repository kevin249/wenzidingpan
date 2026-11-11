from dataclasses import replace
import sqlite3
import pytest

from stockwidget.config import Config
from stockwidget.mcp_depth import McpDepthPoller, McpBsPoller
from stockwidget.mcp_endpoint import realtime_url
from stockwidget.mcp_realtime import ClientJournal

BASE = 'https://gupiao.yanglaogu.cn/mcp'
REALTIME = 'https://gupiao.yanglaogu.cn/api/notifications/mcp/realtime'


def test_public_service_automatically_uses_isolated_realtime_entry():
    config = Config(mcp_url=BASE, mcp_realtime_url='')
    assert REALTIME == realtime_url(config)
    assert REALTIME == realtime_url(replace(config, mcp_realtime_url=BASE))
    assert not McpDepthPoller._enabled(config)
    assert not McpBsPoller._enabled(config)


def test_explicit_addresses_and_custom_legacy_servers_are_preserved():
    config = Config(mcp_url=BASE, mcp_realtime_url='http://localhost:8802/mcp')
    assert 'http://localhost:8802/mcp' == realtime_url(config)
    assert '' == realtime_url(Config(mcp_url='https://example.test/mcp'))


def test_journal_commits_rolls_back_and_releases_database_connections(tmp_path):
    journal = ClientJournal(tmp_path / 'journal.sqlite3', 'test')
    with journal.connect() as db:
        db.execute('INSERT INTO cursors VALUES (?,?)', ('test', 'committed'))
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')
    with pytest.raises(RuntimeError):
        with journal.connect() as db:
            db.execute('UPDATE cursors SET cursor=?', ('not-committed',))
            raise RuntimeError('interrupted transaction')
    assert 'committed' == journal.cursor()
    with pytest.raises(sqlite3.ProgrammingError):
        db.execute('SELECT 1')
