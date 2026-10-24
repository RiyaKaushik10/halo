# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
import json
import os

from halo import report


def results():
    return [{"path": "/home/u/Private/diary.txt", "status": "verified", "bytes": 1200, "is_dir": False},
            {"path": "/home/u/Private", "status": "removed", "bytes": 0, "is_dir": True},
            {"path": "/home/u/other.bin", "status": "failed", "bytes": 50, "is_dir": False}]


def test_names_hashed_by_default(tmp_path):
    rep = report.build(results(), "dod3", True)
    assert rep["names"] == "hashed"
    assert rep["item_count"] == 3 and rep["total_bytes"] == 1250
    assert rep["verified"] == 1 and rep["failed"] == 1
    path = report.save(rep, str(tmp_path / "Reports"))
    text = open(path).read()
    assert "diary" not in text and "Private" not in text
    assert "DoD 5220.22-M 3-pass method" in text
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    block = text.split("Machine-readable copy:\n", 1)[1]
    assert json.loads(block)["item_count"] == 3


def test_names_included_when_asked():
    rep = report.build(results(), "random1", False, include_names=True)
    assert rep["items"][0]["item"] == "/home/u/Private/diary.txt"
    assert rep["method"] == "Single random pass"
