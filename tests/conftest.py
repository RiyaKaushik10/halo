# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Every test runs against a throwaway HOME. Shred code only ever touches
files inside pytest's tmp_path (or a loop-mounted test image)."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    for v in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.delenv("HALO_DRY_RUN", raising=False)
    return h


def write(path, data=b"", size=None):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    with open(path, "wb") as f:
        if size is not None:
            chunk = os.urandom(65536)
            left = size
            while left > 0:
                f.write(chunk[:min(left, len(chunk))])
                left -= len(chunk)
        else:
            f.write(data)
    return str(path)


@pytest.fixture
def mk():
    return write
