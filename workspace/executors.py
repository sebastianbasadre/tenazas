#!/usr/bin/env python3
"""
Legacy compatibility shim for the old flat `workspace/executors.py` module.

The canonical executors implementation lives in the `runtime/` package.
"""

from __future__ import annotations

import sys
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_DIR))

from runtime.executors import *  # noqa: F401,F403
