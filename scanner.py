#!/usr/bin/env python
"""Compatibility shim: `python scanner.py <cmd>` == `nse-scan <cmd>`.

The CLI lives in nse/cli.py; this file exists so webui.py and any cron
scripts that call `scanner.py` keep working unchanged.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nse.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
