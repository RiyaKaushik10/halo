# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Section 8 test plan on real filesystem images.

Creates a 200 MB ext4 image, loop-mounts it, writes a file full of a unique
marker (and one with a distinctive 40-character name), shreds both, unmounts
and searches the raw image. Needs root (mount -o loop) or udisksctl; skipped
otherwise. Never touches anything outside the temporary image.
"""

import os
import shutil
import subprocess

import pytest

from halo import safety
from halo.shred import VERIFIED, Shredder

MARKER = b"HALO-RAW-IMAGE-MARKER-5b1e0c"
NAME = "q9-distinctive-forty-char-name-halo-x7.txt"   # 40 characters


def _can_loop_mount():
    return os.geteuid() == 0 and shutil.which("mkfs.ext4") and shutil.which("mount")


def _mount(img, mnt):
    r = subprocess.run(["mount", "-o", "loop", img, mnt], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("loop mount not permitted here: %s" % r.stderr.strip())


def _make_image(tmp_path, fs="ext4"):
    img = str(tmp_path / ("test-%s.img" % fs))
    with open(img, "wb") as f:
        f.truncate(200 * 1024 * 1024)
    mkfs = {"ext4": ["mkfs.ext4", "-q", "-F", img], "btrfs": ["mkfs.btrfs", "-q", "-f", img]}[fs]
    if not shutil.which(mkfs[0]):
        pytest.skip("%s not installed" % mkfs[0])
    subprocess.run(mkfs, check=True, capture_output=True)
    mnt = tmp_path / ("mnt-" + fs)
    mnt.mkdir()
    return img, str(mnt)


def _age_journal(mnt, commits=2500):
    """Simulate files that have existed for a while: push enough small commits
    through the ext4 journal that the copies made when the test files were
    created are overwritten. (Those creation-time journal copies are outside
    what any file-level tool can reach; Halo's job is not to add new ones.)"""
    c = os.path.join(mnt, "churn")
    os.mkdir(c)
    for i in range(commits):
        fp = os.path.join(c, "c%d" % (i % 50))
        fd = os.open(fp, os.O_CREAT | os.O_WRONLY)
        os.write(fd, b"z")
        os.fsync(fd)
        os.close(fd)
        os.unlink(fp)
        dfd = os.open(c, os.O_RDONLY)
        os.fsync(dfd)
        os.close(dfd)


def _count(img, needle):
    with open(img, "rb") as f:
        data = f.read()
    return data.count(needle)


@pytest.mark.skipif(not _can_loop_mount(), reason="needs root and mkfs.ext4 for a loop-mounted image")
def test_data_and_name_really_gone_on_ext4(tmp_path):
    img, mnt = _make_image(tmp_path)
    _mount(img, mnt)
    try:
        secret = os.path.join(mnt, "secret.txt")
        with open(secret, "wb") as f:
            f.write((MARKER + b"\n") * 40000)          # ~1.2 MB of markers
        named = os.path.join(mnt, NAME)
        with open(named, "wb") as f:
            f.write(b"x" * 5000)
        subprocess.run(["sync"])
        _age_journal(mnt)
        r = Shredder("dod3", verify=True, dry_run=False).run([secret, named])
        assert [x.status for x in r] == [VERIFIED, VERIFIED]
    finally:
        subprocess.run(["umount", mnt])
    assert _count(img, MARKER) == 0
    assert _count(img, NAME.encode()) == 0


@pytest.mark.skipif(not _can_loop_mount() or not shutil.which("mkfs.btrfs"),
                    reason="needs root and mkfs.btrfs")
def test_btrfs_shows_amber_card(tmp_path):
    img, mnt = _make_image(tmp_path, "btrfs")
    _mount(img, mnt)
    try:
        p = os.path.join(mnt, "file.txt")
        with open(p, "w") as f:
            f.write("x")
        keys = {w.key for w in safety.warnings_for([p])}
        assert "fs" in keys
    finally:
        subprocess.run(["umount", mnt])
