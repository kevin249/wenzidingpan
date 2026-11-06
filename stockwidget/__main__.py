"""``python -m stockwidget`` 入口。"""

import sys

from .crashlog import install_crash_log

# Catch import/startup errors as well as native failures in the Qt event loop.
install_crash_log()

from .app import main

if __name__ == "__main__":
    sys.exit(main())
