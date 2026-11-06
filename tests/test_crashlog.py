"""Crash capture is installed before Qt imports and survives closed consoles."""

import os
from pathlib import Path
import subprocess
import sys


def test_native_dump_and_uncaught_exceptions_are_persisted(tmp_path):
    target = tmp_path / 'crash.log'
    script = '''
import faulthandler, sys, threading
from pathlib import Path
from stockwidget.crashlog import install_crash_log
path = Path(sys.argv[1])
assert install_crash_log(path) == path.resolve()
hook = sys.excepthook
assert install_crash_log(path) == path.resolve()
assert sys.excepthook is hook
assert faulthandler.is_enabled()
# The same file is used by the fatal-signal handler; avoid crashing pytest.
from stockwidget import crashlog
faulthandler.dump_traceback(file=crashlog._stream, all_threads=True)
def worker():
    raise RuntimeError('worker failure sentinel')
thread = threading.Thread(target=worker)
thread.start()
thread.join()
raise RuntimeError('startup failure sentinel')
'''
    result = subprocess.run([sys.executable, '-c', script, str(target)],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    log = target.read_text(encoding='utf-8')
    assert 'Python ' in log
    assert 'most recent call first' in log
    assert 'worker failure sentinel' in log
    assert 'startup failure sentinel' in log
    assert 'startup failure sentinel' in result.stderr


def test_module_entrypoint_captures_import_failures_before_creating_window(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    script = '''
import runpy, sys
class BlockAppImport:
    def find_spec(self, fullname, *args):
        if fullname == 'stockwidget.app':
            raise ImportError('app import failure sentinel')
sys.meta_path.insert(0, BlockAppImport())
runpy.run_module('stockwidget', run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(repo)},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert 'app import failure sentinel' in (tmp_path/'logs/crash.log').read_text()
