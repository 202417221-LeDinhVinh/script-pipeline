"""Keep `pytest tests -q` offline: live tests run only under `-m live`."""
from __future__ import annotations
import pytest


def pytest_collection_modifyitems(items, config):
    if "live" in (getattr(config.option, "markexpr", "") or ""):
        return
    for item in items:
        try:
            parts = item.path.parts
        except AttributeError:
            parts = str(getattr(item, "fspath", "")).replace("\\", "/").split("/")
        if "live" in parts:
            item.add_marker(pytest.mark.skip(reason="live only; run with -m live"))
