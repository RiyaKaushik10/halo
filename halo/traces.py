# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Traces a desktop leaves behind: thumbnails, recent-file entries and copies
in the Trash (section 5, "Traces removed with each file")."""

import hashlib
import os
import re
import struct
import tempfile
import xml.etree.ElementTree as ET

from gi.repository import GLib

from .util import home

THUMB_SIZES = ("normal", "large", "x-large", "xx-large")


def file_uri(path):
    """Canonical file:// URI, as Gio.File.get_uri() produces it."""
    try:
        from gi.repository import Gio
        return Gio.File.new_for_path(os.path.abspath(path)).get_uri()
    except Exception:
        return GLib.filename_to_uri(os.path.abspath(path), None)


def uri_to_path(uri):
    try:
        p, _host = GLib.filename_from_uri(uri)
        return p
    except Exception:
        return None


def thumbnail_cache_dir():
    base = os.environ.get("XDG_CACHE_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(home(), ".cache")
    return os.path.join(base, "thumbnails")


def _thumb_dirs(cache=None):
    cache = cache or thumbnail_cache_dir()
    dirs = [os.path.join(cache, s) for s in THUMB_SIZES]
    fail = os.path.join(cache, "fail")
    if os.path.isdir(fail):
        try:
            dirs += [os.path.join(fail, d) for d in os.listdir(fail)]
        except OSError:
            pass
    return [d for d in dirs if os.path.isdir(d)]


def thumbnails_for_file(path, cache=None):
    md5 = hashlib.md5(file_uri(path).encode()).hexdigest()
    out = []
    for d in _thumb_dirs(cache):
        p = os.path.join(d, md5 + ".png")
        if os.path.isfile(p) and not os.path.islink(p):
            out.append(p)
    return out


def png_text_chunks(path, limit=65536):
    """tEXt / iTXt key-value pairs from a PNG, read without any image library."""
    out = {}
    try:
        with open(path, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return out
            read = 8
            while read < limit:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                n, typ = struct.unpack(">I4s", hdr)
                if typ == b"IDAT" or typ == b"IEND":
                    break
                data = f.read(n)
                f.read(4)
                read += 12 + n
                if typ == b"tEXt" and b"\0" in data:
                    k, v = data.split(b"\0", 1)
                    out[k.decode("latin-1")] = v.decode("latin-1")
                elif typ == b"iTXt" and b"\0" in data:
                    k, rest = data.split(b"\0", 1)
                    if len(rest) >= 2 and rest[0] == 0:
                        rest = rest[2:]
                        parts = rest.split(b"\0", 2)
                        if len(parts) == 3:
                            out[k.decode("latin-1")] = parts[2].decode("utf-8", "replace")
    except OSError:
        pass
    return out


def thumbnails_under_folder(folder, cache=None):
    """Thumbnails whose Thumb::URI lies inside `folder` (catches renamed/moved files)."""
    prefix = file_uri(folder).rstrip("/") + "/"
    out = []
    for d in _thumb_dirs(cache):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not n.endswith(".png"):
                continue
            p = os.path.join(d, n)
            uri = png_text_chunks(p).get("Thumb::URI", "")
            if uri.startswith(prefix):
                out.append(p)
    return out


def shared_thumbnails(path):
    """Removable media keep previews in <folder>/.sh_thumbnails/{normal,large}."""
    folder, name = os.path.split(os.path.abspath(path))
    out = []
    keys = {hashlib.md5(name.encode("utf-8", "surrogateescape")).hexdigest(),
            hashlib.md5(("./" + name).encode("utf-8", "surrogateescape")).hexdigest()}
    for size in THUMB_SIZES:
        d = os.path.join(folder, ".sh_thumbnails", size)
        for k in keys:
            p = os.path.join(d, k + ".png")
            if os.path.isfile(p) and not os.path.islink(p):
                out.append(p)
    return out


def find_thumbnails(paths, cache=None, removable=lambda p: False):
    found = []
    for p in paths:
        if os.path.isdir(p) and not os.path.islink(p):
            found += thumbnails_under_folder(p, cache)
            for root, dirs, files in os.walk(p):
                for f in files:
                    fp = os.path.join(root, f)
                    found += thumbnails_for_file(fp, cache)
                    if removable(fp):
                        found += shared_thumbnails(fp)
        else:
            found += thumbnails_for_file(p, cache)
            if removable(p):
                found += shared_thumbnails(p)
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def thumbnail_cache_files(cache=None):
    out = []
    for d in _thumb_dirs(cache):
        try:
            for n in os.listdir(d):
                p = os.path.join(d, n)
                if os.path.isfile(p) and not os.path.islink(p):
                    out.append(p)
        except OSError:
            pass
    return out


# ── recently-used.xbel ────────────────────────────────────────────────────
def recent_file():
    base = os.environ.get("XDG_DATA_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(home(), ".local", "share")
    return os.path.join(base, "recently-used.xbel")


_BOOKMARK_RE = re.compile(r"[ \t]*<bookmark\b[^>]*?\bhref=\"([^\"]*)\"[^>]*?(?:/>|>.*?</bookmark>)[ \t]*\n?",
                          re.S)


def _matches(href, targets):
    p = uri_to_path(_xml_unescape(href))
    if p is None:
        return False
    for t in targets:
        if p == t or p.startswith(t.rstrip("/") + "/"):
            return True
    return False


def _xml_unescape(s):
    return s.replace("&quot;", '"').replace("&apos;", "'").replace("&lt;", "<").replace("&gt;", ">") \
        .replace("&amp;", "&")


def clean_recent_text(text, targets):
    """Remove every <bookmark> whose href is a target or inside one. Returns (text, removed)."""
    targets = [os.path.abspath(t) for t in targets]
    removed = 0

    def repl(m):
        nonlocal removed
        if _matches(m.group(1), targets):
            removed += 1
            return ""
        return m.group(0)
    out = _BOOKMARK_RE.sub(repl, text)
    return out, removed


def count_recent_matches(targets, path=None):
    path = path or recent_file()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0
    return clean_recent_text(text, targets)[1]


def clean_recent(targets, shred_file, path=None, clear_all=False):
    """Write the cleaned XML to a temp file, shred the old file, rename the temp into place.

    `shred_file(path) -> bool` shreds one file with the 3-pass routine.
    Returns the number of entries removed.
    """
    path = path or recent_file()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0
    if clear_all:
        new = _BOOKMARK_RE.sub("", text)
        removed = len(_BOOKMARK_RE.findall(text))
    else:
        new, removed = clean_recent_text(text, targets)
    if removed == 0:
        return 0
    ET.fromstring(new.encode("utf-8"))   # must still be valid XML
    st = os.stat(path)
    fd, tmp = tempfile.mkstemp(prefix=".recently-used.", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, st.st_mode & 0o777)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        if not shred_file(path):
            os.unlink(tmp)
            return 0
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return removed


# ── Trash ─────────────────────────────────────────────────────────────────
def trash_dirs(mounts=None):
    base = os.environ.get("XDG_DATA_HOME")
    if not base or not os.path.isabs(base):
        base = os.path.join(home(), ".local", "share")
    dirs = [os.path.join(base, "Trash")]
    uid = os.getuid()
    try:
        from .safety import read_mounts
        for m in (mounts if mounts is not None else read_mounts()):
            for cand in (os.path.join(m.point, ".Trash-%d" % uid), os.path.join(m.point, ".Trash", str(uid))):
                if os.path.isdir(cand):
                    dirs.append(cand)
    except Exception:
        pass
    return [d for d in dirs if os.path.isdir(os.path.join(d, "info"))]


def _parse_trashinfo(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Path="):
                    raw = line[5:].strip()
                    return GLib.uri_unescape_string(raw, None) or raw
    except OSError:
        pass
    return None


def trash_entries(mounts=None):
    """[(original_path, payload, info_file)] for every item in every Trash."""
    out = []
    for d in trash_dirs(mounts):
        info = os.path.join(d, "info")
        if os.path.basename(d).startswith(".Trash-"):
            top = os.path.dirname(d)
        elif os.path.basename(os.path.dirname(d)) == ".Trash":
            top = os.path.dirname(os.path.dirname(d))
        else:
            top = None   # home trash: paths are absolute
        try:
            names = os.listdir(info)
        except OSError:
            continue
        for n in names:
            if not n.endswith(".trashinfo"):
                continue
            ip = os.path.join(info, n)
            orig = _parse_trashinfo(ip)
            if not orig:
                continue
            if not os.path.isabs(orig) and top:
                orig = os.path.join(top, orig)
            payload = os.path.join(d, "files", n[:-len(".trashinfo")])
            out.append((orig, payload, ip))
    return out


def find_trash_copies(paths, mounts=None):
    targets = [os.path.abspath(p) for p in paths]
    out = []
    for orig, payload, info in trash_entries(mounts):
        for t in targets:
            if orig == t or orig.startswith(t.rstrip("/") + "/"):
                if os.path.lexists(payload) or os.path.exists(info):
                    out.append((orig, payload, info))
                break
    return out
