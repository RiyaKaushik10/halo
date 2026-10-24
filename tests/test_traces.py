# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
import hashlib
import os
import struct
import xml.etree.ElementTree as ET
import zlib

from halo import traces
from halo.shred import Shredder


def png_with_uri(path, uri):
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    raw = b"\x00\x00\x00\x00\x00"
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"Thumb::URI\0" + uri.encode())
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def shred_one(p):
    return all(r.status == "verified" for r in Shredder("random1", dry_run=False).run([p]))


def test_thumbnail_found_by_uri_hash_and_removed(home, tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"x" * 100)
    uri = traces.file_uri(str(f))
    md5 = hashlib.md5(uri.encode()).hexdigest()
    cache = home / ".cache" / "thumbnails"
    t1 = png_with_uri(str(cache / "normal" / (md5 + ".png")), uri)
    t2 = png_with_uri(str(cache / "x-large" / (md5 + ".png")), uri)
    t3 = png_with_uri(str(cache / "fail" / "gnome-thumbnail-factory" / (md5 + ".png")), uri)
    found = traces.find_thumbnails([str(f)])
    assert set(found) == {t1, t2, t3}
    for t in found:
        assert shred_one(t)
    assert not any(os.path.exists(t) for t in (t1, t2, t3))


def test_thumbnail_found_by_thumb_uri_for_folders(home, tmp_path):
    folder = tmp_path / "Holiday"
    folder.mkdir()
    # a preview of a file that was renamed earlier: its hash no longer matches anything
    uri = traces.file_uri(str(folder / "old-name.jpg"))
    t = png_with_uri(str(home / ".cache" / "thumbnails" / "large" / "deadbeef.png"), uri)
    other = png_with_uri(str(home / ".cache" / "thumbnails" / "large" / "cafe.png"),
                         traces.file_uri(str(tmp_path / "Elsewhere" / "x.jpg")))
    found = traces.find_thumbnails([str(folder)])
    assert t in found and other not in found


def test_shared_thumbnails_on_removable_media(tmp_path):
    f = tmp_path / "usb" / "clip.mp4"
    os.makedirs(str(f.parent))
    f.write_bytes(b"v")
    md5 = hashlib.md5(b"./clip.mp4").hexdigest()
    t = png_with_uri(str(f.parent / ".sh_thumbnails" / "normal" / (md5 + ".png")), "./clip.mp4")
    assert traces.shared_thumbnails(str(f)) == [t]


XBEL = """<?xml version="1.0" encoding="UTF-8"?>
<xbel version="1.0"
      xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"
      xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info"
>
  <bookmark href="{a}" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info><metadata owner="http://freedesktop.org"><mime:mime-type type="text/plain"/></metadata></info>
  </bookmark>
  <bookmark href="{b}" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info><metadata owner="http://freedesktop.org"><mime:mime-type type="image/png"/></metadata></info>
  </bookmark>
  <bookmark href="{c}" added="2026-01-01T00:00:00Z" modified="2026-01-01T00:00:00Z" visited="2026-01-01T00:00:00Z">
    <info><metadata owner="http://freedesktop.org"><mime:mime-type type="video/mp4"/></metadata></info>
  </bookmark>
</xbel>
"""


def test_recent_entry_removed(home, tmp_path):
    a, b, c = (str(tmp_path / n) for n in ("keep one.txt", "secret plan.png", "Folder"))
    os.makedirs(c)
    inside = os.path.join(c, "inner video.mp4")
    path = str(home / ".local" / "share" / "recently-used.xbel")
    os.makedirs(os.path.dirname(path))
    with open(path, "w") as f:
        f.write(XBEL.format(a=traces.file_uri(a), b=traces.file_uri(b), c=traces.file_uri(inside)))
    assert traces.count_recent_matches([b, c], path) == 2
    removed = traces.clean_recent([b, c], shred_one, path)
    assert removed == 2
    text = open(path).read()
    ET.fromstring(text.encode())                        # still valid XML
    assert traces.file_uri(a) in text
    assert "secret" not in text and "inner" not in text
    assert [n for n in os.listdir(os.path.dirname(path)) if n.startswith(".recently")] == []


def test_trash_copies_found(home, tmp_path):
    orig = str(tmp_path / "Docs" / "taxes 2025.pdf")
    trash = home / ".local" / "share" / "Trash"
    (trash / "files").mkdir(parents=True)
    (trash / "info").mkdir()
    (trash / "files" / "taxes 2025.pdf").write_bytes(b"pdf")
    from gi.repository import GLib
    (trash / "info" / "taxes 2025.pdf.trashinfo").write_text(
        "[Trash Info]\nPath=%s\nDeletionDate=2026-10-01T10:00:00\n" % GLib.uri_escape_string(orig, "/", False))
    found = traces.find_trash_copies([str(tmp_path / "Docs")], mounts=[])
    assert len(found) == 1 and found[0][0] == orig
    assert traces.find_trash_copies([str(tmp_path / "Other")], mounts=[]) == []
