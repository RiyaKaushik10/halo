# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
import os
import subprocess
import time

import pytest

from halo import scanner
from halo.scanner import FILES, LINK, OTHER_DRIVE, Scanner, ScanResult, scan_path


def du_bytes(path):
    out = subprocess.run(["du", "-sx", "--block-size=1", str(path)], capture_output=True, text=True).stdout
    return int(out.split()[0])


def make_tree(base, mk):
    mk(base / "Videos" / "2025" / "a.mp4", size=3_000_000)
    mk(base / "Videos" / "2025" / "b.mp4", size=1_200_000)
    mk(base / "Videos" / "clip.webm", size=400_000)
    for i in range(40):
        mk(base / "Docs" / ("note%02d.txt" % i), size=3000 + i * 50)
    mk(base / "Docs" / "deep" / "deeper" / "deepest" / "x.pdf", size=250_000)
    mk(base / ".hidden" / "cache.bin", size=90_000)
    mk(base / "top.iso", size=2_000_000)


@pytest.mark.parametrize("sub", ["", "Videos", "Docs"])
def test_totals_match_du(tmp_path, mk, sub):
    make_tree(tmp_path, mk)
    p = tmp_path / sub if sub else tmp_path
    r = scan_path(str(p))
    d = du_bytes(p)
    assert abs(r.root.size - d) <= d * 0.01


def test_apparent_size(tmp_path, mk):
    mk(tmp_path / "a.bin", size=12345)
    mk(tmp_path / "b.bin", size=100)
    r = scan_path(str(tmp_path), "apparent")
    # folder's own st_size is counted too, like du --apparent-size
    assert r.root.size >= 12445
    assert r.files == 2


def test_hard_links_counted_once(tmp_path, mk):
    f = mk(tmp_path / "one.bin", size=1_000_000)
    os.link(f, str(tmp_path / "two.bin"))
    r = scan_path(str(tmp_path))
    assert r.files == 1
    assert abs(r.root.size - du_bytes(tmp_path)) <= 8192


def test_symlinks_not_followed(tmp_path, mk):
    target = tmp_path / "outside"
    mk(target / "big.bin", size=2_000_000)
    scan_root = tmp_path / "root"
    scan_root.mkdir()
    os.symlink(str(target), str(scan_root / "link-to-dir"))
    os.symlink(str(target / "big.bin"), str(scan_root / "link-to-file"))
    r = scan_path(str(scan_root))
    assert r.root.size < 100_000
    links = [c for c in r.root.children if c.flags & LINK]
    assert {c.name for c in links} == {"link-to-dir", "link-to-file"}
    assert all(c.size == 0 for c in links)


def test_skip_virtual_filesystems_on_root():
    assert scanner.SKIP_ON_ROOT == ("/proc", "/sys", "/dev", "/run")


def test_other_filesystem_becomes_wedge():
    # /proc is always another filesystem than /etc's parent; scanning "/" skips it,
    # but /dev/shm under /dev shows up as an Other drive wedge when scanning /dev
    if not os.path.isdir("/dev/shm"):
        pytest.skip("no /dev/shm")
    if os.stat("/dev").st_dev == os.stat("/dev/shm").st_dev:
        pytest.skip("/dev/shm is not a separate filesystem here")
    r = scan_path("/dev")
    others = [c for c in r.root.children if c.flags & OTHER_DRIVE]
    assert any(c.name == "shm" for c in others)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read every folder")
def test_permission_errors_collected(tmp_path, mk):
    locked = tmp_path / "locked"
    mk(locked / "secret.bin", size=1000)
    os.chmod(str(locked), 0)
    try:
        r = scan_path(str(tmp_path))
        assert str(locked) in r.errors
    finally:
        os.chmod(str(locked), 0o755)


def test_cancel_keeps_partial_result(tmp_path, mk):
    for i in range(300):
        mk(tmp_path / ("d%03d" % i) / "f.bin", size=4096)
    sc = Scanner(str(tmp_path))
    sc.cancel()
    res = sc.run_sync()
    assert res.cancelled
    assert res.root is not None


def test_progress_is_streamed(tmp_path, mk):
    for i in range(200):
        mk(tmp_path / ("d%03d" % i) / "f.bin", size=4096)
    seen = []
    sc = Scanner(str(tmp_path), on_progress=lambda r: seen.append(r.files), push_interval=0)
    sc.run_sync()
    assert len(seen) > 10


def test_biggest_and_oldest(tmp_path, mk):
    now = time.time()
    for i in range(5):
        p = mk(tmp_path / ("f%d.bin" % i), size=(i + 1) * 3_000_000)
        os.utime(p, (now - (i + 1) * 86400 * 30, now - (i + 1) * 86400 * 30))
    r = scan_path(str(tmp_path))
    big = r.biggest_under(str(tmp_path))
    assert [os.path.basename(b[0]) for b in big[:3]] == ["f4.bin", "f3.bin", "f2.bin"]
    old = r.oldest_under(str(tmp_path))
    assert os.path.basename(old[0][0]) == "f4.bin"       # least recently used first
    assert all(o[1] >= scanner.OLDEST_MIN_SIZE for o in old)


def test_big_files_become_arcs_and_rest_grouped(tmp_path, mk):
    mk(tmp_path / "huge.mkv", size=5_000_000)
    for i in range(20):
        mk(tmp_path / ("small%02d.txt" % i), size=2000)
    r = scan_path(str(tmp_path))
    names = {c.name for c in r.root.children if not (c.flags & FILES)}
    assert "huge.mkv" in names
    files = [c for c in r.root.children if c.flags & FILES]
    assert len(files) == 1 and files[0].count == 20


def test_detach_subtracts_sizes(tmp_path, mk):
    make_tree(tmp_path, mk)
    r = scan_path(str(tmp_path))
    before = r.root.size
    videos = r.root.find(str(tmp_path / "Videos"))
    vsize = videos.size
    videos.detach()
    assert r.root.size == before - vsize
    assert r.root.find(str(tmp_path / "Videos")) is None


def test_file_node_detach_updates_files_group(tmp_path, mk):
    for i in range(5):
        mk(tmp_path / ("s%d.txt" % i), size=5000)
    r = scan_path(str(tmp_path))
    fn = scanner.FileNodes(r.root)
    files = fn.list_folder(r.root)
    assert len(files) == 5
    total = r.root.size
    files[0].detach()
    assert r.root.size == total - files[0].size


def test_cache_roundtrip(home, tmp_path, mk):
    make_tree(tmp_path / "data", mk)
    r = scan_path(str(tmp_path / "data"))
    r.save()
    path = r.cache_file()
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(os.path.dirname(path)).st_mode & 0o777) == "0o700"
    again = ScanResult.load(str(tmp_path / "data"), "disk")
    assert again is not None and again.from_cache
    assert again.root.size == r.root.size
    assert again.root.find(str(tmp_path / "data" / "Videos" / "2025")) is not None


def test_kind_for():
    from halo.theme import KIND_ARCHIVES, KIND_AUDIO, KIND_DOCUMENTS, KIND_IMAGES, KIND_OTHER, KIND_VIDEO
    assert scanner.kind_for("a.PDF") == KIND_DOCUMENTS
    assert scanner.kind_for("b.jpeg") == KIND_IMAGES
    assert scanner.kind_for("c.mkv") == KIND_VIDEO
    assert scanner.kind_for("d.flac") == KIND_AUDIO
    assert scanner.kind_for("e.tar.gz") == KIND_ARCHIVES
    assert scanner.kind_for("noext") == KIND_OTHER


def test_deep_tree_does_not_recurse(tmp_path, mk):
    import sys
    p = str(tmp_path)
    try:
        for i in range(400):
            p = os.path.join(p, "d")
            os.mkdir(p)
        with open(os.path.join(p, "f.bin"), "wb") as f:
            f.write(b"x" * 10)
    except OSError:
        pytest.skip("path too long for this filesystem")
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(150)          # a recursive walk would blow up here
    try:
        r = scan_path(str(tmp_path))
        deepest = r.root
        while deepest.children and any(c.is_dir for c in deepest.children):
            deepest = [c for c in deepest.children if c.is_dir][0]
        path = deepest.path()
    finally:
        sys.setrecursionlimit(limit)
    assert r.files == 1
    assert path.count(os.sep + "d") == 400
