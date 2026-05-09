"""
tests/conftest.py — Shared pytest configuration.

Adds the project root to sys.path so tests can import the project
modules without needing an installed package. Defines markers used to
separate fast unit tests from slow API-dependent integration tests.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root on sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires API keys + network (Charity Commission, "
        "Companies House, OFSI/OFAC downloads). Slower, costs free-tier quota.",
    )
    config.addinivalue_line(
        "markers",
        "slow: takes >5s. Use -m 'not slow' to skip during fast feedback loops.",
    )
