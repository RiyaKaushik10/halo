# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Shred engine tests. Everything here works inside tmp_path only."""

import os
import subprocess
import sys
import threading

from halo import shred
from halo.shred import (FAILED, LINK_REMOVED, PARTIAL, REFUSED, REMOVED, VERIFIED, Shredder)

MARKER = b"HALO-TEST-MARKER-7f3a9c"


def marker_file(path, kb=300):
    with open(path, "wb") as f:
        for _ in range(kb):
            f.write(MARKER * (1024 // len(MARKER)) + b"\n" * (1024 % len(MARKER)))
    return str(path)


def test_file_is_overwritten_verified_and_removed(tmp_path):
    p = marker_file(tmp_path / "secret.txt")
    r = Shredder("dod3", verify=True, dry_run=False).run([p])
    assert [x.status for x in r] == [VERIFIED]
    assert not os.path.exists(p)
    assert os.listdir(tmp_path) == []


def test_passes_write_zeros_ones_random(tmp_path):
    """Read the file back after every pass to check what was written."""
    p = marker_file(tmp_path / "f.bin", kb=64)
    sh = Shredder("dod3", verify=True, dry_run=False)
    passes = []
    orig = sh._write_pass

    def wrapped(fd, length, kind, pass_no, keep_digests):
        out = orig(fd, length, kind, pass_no, keep_digests)
        with open(p, "rb") as f:
            passes.append(f.read())
        return out
    sh._write_pass = wrapped
    r = sh.run([p])
    assert r[0].status == VERIFIED
    assert len(passes) == 3
    blk = os.stat(str(tmp_path)).st_blksize
    assert len(passes[0]) % blk == 0                      # rounded up to the block size
    assert set(passes[0]) == {0}
    assert set(passes[1]) == {0xFF}
    assert MARKER not in passes[2] and len(set(passes[2])) > 200


def test_sibling_names_scrubbed_together(tmp_path, monkeypatch):
    names = ["first-private-name.txt", "second-private-name.txt"]
    paths = [marker_file(tmp_path / n, kb=2) for n in names]
    calls = []
    real_rename, real_fsync = os.rename, os.fsync

    def spy_rename(src, dst, *a, **k):
        calls.append(("rename", os.path.basename(src)))
        return real_rename(src, dst, *a, **k)

    def spy_fsync(fd):
        calls.append(("fsync", None))
        return real_fsync(fd)
    monkeypatch.setattr(os, "rename", spy_rename)
    monkeypatch.setattr(os, "fsync", spy_fsync)
    Shredder("random1", dry_run=False).run(paths)
    first_rename = next(i for i, c in enumerate(calls) if c[0] == "rename")
    # both original names are renamed away before the folder is synced again
    assert calls[first_rename][1] in names and calls[first_rename + 1][1] in names
    assert os.listdir(tmp_path) == []


def test_single_random_pass(tmp_path):
    p = marker_file(tmp_path / "f.bin", kb=20)
    events = []
    r = Shredder("random1", verify=True, dry_run=False, progress=events.append).run([p])
    assert r[0].status == VERIFIED
    assert {e["pass"] for e in events if e["type"] == "pass"} == {1}


def test_verification_catches_faults(tmp_path):
    p = marker_file(tmp_path / "f.bin", kb=40)
    sh = Shredder("dod3", verify=True, dry_run=False)

    def flip(data, off):
        if off == 0:
            b = bytearray(data)
            b[10] ^= 0xFF
            return bytes(b)
        return data
    sh.read_hook = flip
    r = sh.run([p])
    assert r[0].status == FAILED
    assert os.path.exists(p)                              # failed files are never deleted


def test_name_is_scrubbed(tmp_path, monkeypatch):
    name = "a-distinctive-forty-character-file-name.txt"
    p = marker_file(tmp_path / name, kb=4)
    renames = []
    real = os.rename

    def spy(src, dst, *a, **k):
        renames.append(os.path.basename(dst))
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "rename", spy)
    Shredder("random1", dry_run=False).run([p])
    assert len(renames) == len(name)
    assert [len(n) for n in renames] == list(range(len(name), 0, -1))
    assert name not in renames
    assert os.listdir(tmp_path) == []


def test_cancel_mid_pass_reports_partial(tmp_path):
    p = str(tmp_path / "big.bin")
    with open(p, "wb") as f:
        f.write(os.urandom(4 << 20))
    ev = threading.Event()

    def progress(e):
        if e["type"] == "pass" and e["pass"] == 2:
            ev.set()
    sh = Shredder("dod3", dry_run=False, progress=progress, cancel_event=ev, chunk=64 * 1024)
    r = sh.run([p])
    assert r[0].status == PARTIAL
    assert shred.STATUS_TEXT[PARTIAL] == "Partly overwritten, not removed"
    assert os.path.exists(p)


def test_dry_run_changes_nothing(tmp_path):
    p = marker_file(tmp_path / "keep.txt", kb=8)
    before = open(p, "rb").read()
    st = os.stat(p)
    r = Shredder("dod3", dry_run=True).run([p])
    assert r[0].status == VERIFIED
    assert open(p, "rb").read() == before
    assert os.stat(p).st_mtime == st.st_mtime


def test_dry_run_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HALO_DRY_RUN", "1")
    p = marker_file(tmp_path / "keep.txt", kb=1)
    Shredder("dod3").run([p])
    assert os.path.exists(p)


def test_symlink_refused_target_untouched(tmp_path):
    target = marker_file(tmp_path / "target.txt", kb=2)
    link = str(tmp_path / "link")
    os.symlink(target, link)
    r = Shredder(dry_run=False).run([link])
    assert r[0].status == REFUSED
    assert os.path.lexists(link) and open(target, "rb").read().startswith(MARKER)
    r = Shredder(dry_run=False, remove_links=True).run([link])
    assert r[0].status == LINK_REMOVED
    assert not os.path.lexists(link) and os.path.exists(target)


def test_fifo_refused(tmp_path):
    f = str(tmp_path / "pipe")
    os.mkfifo(f)
    r = Shredder(dry_run=False).run([f])
    assert r[0].status == REFUSED


def test_folder_depth_first(tmp_path):
    d = tmp_path / "folder"
    marker_file(os.path.join(str(d), "a.txt") if d.mkdir() is None else None, kb=2)
    (d / "sub" / "subsub").mkdir(parents=True)
    marker_file(d / "sub" / "b.txt", kb=2)
    marker_file(d / "sub" / "subsub" / "c.txt", kb=2)
    outside = marker_file(tmp_path / "outside.txt", kb=1)
    os.symlink(outside, str(d / "sub" / "link"))
    r = Shredder("random1", dry_run=False).run([str(d)])
    assert all(x.status in (VERIFIED, REMOVED, LINK_REMOVED) for x in r), r
    assert not d.exists()
    assert os.path.exists(outside)                         # links inside a folder are removed, not followed
    dirs = [x.path for x in r if x.is_dir]
    assert dirs.index(str(d / "sub" / "subsub")) < dirs.index(str(d / "sub")) < dirs.index(str(d))


def test_hard_link_other_name_keeps_overwritten_data(tmp_path):
    a = marker_file(tmp_path / "a.txt", kb=4)
    b = str(tmp_path / "b.txt")
    os.link(a, b)
    from halo.safety import hard_link_names
    assert hard_link_names(a, str(tmp_path)) == [b]
    Shredder("random1", dry_run=False).run([a])
    assert not os.path.exists(a)
    with open(b, "rb") as f:
        assert MARKER not in f.read()


def test_cli(tmp_path):
    p = marker_file(tmp_path / "cli.txt", kb=4)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-m", "halo.shred", p, "--method", "dod3", "--verify", "--yes"],
                         cwd=root, capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=root))
    assert out.returncode == 0, out.stderr
    assert "Verified and removed" in out.stdout
    assert not os.path.exists(p)


def test_logs_never_contain_file_names(tmp_path, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="halo.shred")
    p = marker_file(tmp_path / "very-private-name.txt", kb=1)
    Shredder("random1", dry_run=False).run([p])
    assert "very-private-name" not in caplog.text


def test_wording_rule():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = ("certified", "guaranteed unrecoverable", "military-grade", "military grade")
    for folder in ("halo", "data"):
        for dp, dn, fn in os.walk(os.path.join(root, folder)):
            for f in fn:
                if f.endswith((".py", ".css", ".desktop", ".xml", ".nemo_action")):
                    text = open(os.path.join(dp, f), encoding="utf-8").read().lower()
                    for word in bad:
                        assert word not in text, (f, word)
