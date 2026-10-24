# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Background disk scanner and the Node tree it builds.

Rules (section 4):
  * iterative os.scandir walk, stat(follow_symlinks=False), no recursion
  * disk usage (st_blocks * 512) by default, apparent size (st_size) optional
  * hard links counted once per (st_dev, st_ino)
  * symlinks never followed; listed with 0 bytes
  * one filesystem only; other filesystems become an "Other drive" wedge
  * /proc /sys /dev /run skipped when scanning /
  * permission errors collected; cancel keeps a usable partial result
"""

import gzip
import hashlib
import heapq
import itertools
import json
import mimetypes
import os
import stat as statmod
import threading
import time

from . import storage
from .theme import (KIND_ARCHIVES, KIND_AUDIO, KIND_CODE, KIND_DOCUMENTS, KIND_IMAGES,
                    KIND_OTHER, KIND_VIDEO, NUM_KINDS)
from .util import _

# Node flags
DIR = 1
FILES = 2          # "Files in this folder" pseudo-node
OTHER_DRIVE = 4
LINK = 8
HIDDEN = 16
UNREADABLE = 32
SMALLER = 64       # "Smaller items" merge wedge (layout only)

HEAP_CAP = 2000           # bigger than the 200 shown so "under the current centre" stays full
OLDEST_MIN_SIZE = 10 * 1000 * 1000
MAX_LINKS_PER_DIR = 50
BIG_FILES_PER_DIR = 12            # a folder's largest files get their own arcs…
BIG_FILE_MIN = 1_000_000          # …when they are at least 1 MB
SKIP_ON_ROOT = ("/proc", "/sys", "/dev", "/run")
CACHE_VERSION = 2

# ── File kinds ────────────────────────────────────────────────────────────
_EXT = {}


def _reg(kind, exts):
    for e in exts.split():
        _EXT[e] = kind


_reg(KIND_DOCUMENTS, "pdf doc docx odt ott rtf txt md markdown tex epub mobi djvu xls xlsx ods csv tsv "
                     "ppt pptx odp key pages numbers odg org rst log ics vcf eml msg")
_reg(KIND_IMAGES, "jpg jpeg png gif bmp tif tiff webp heic heif avif svg svgz ico xcf psd kra raw cr2 "
                  "cr3 nef arw dng orf rw2 jxl exr hdr")
_reg(KIND_VIDEO, "mp4 m4v mkv mov avi wmv flv webm mpg mpeg ts m2ts mts 3gp ogv vob")
_reg(KIND_AUDIO, "mp3 flac wav ogg oga opus m4a aac wma aiff aif alac mid midi ape")
_reg(KIND_ARCHIVES, "zip tar gz tgz bz2 tbz xz txz zst lz lzma 7z rar iso img dmg deb rpm cab "
                    "appimage snap flatpak qcow2 vdi vmdk vhd vhdx")
_reg(KIND_CODE, "py pyc js mjs ts tsx jsx c h cc cpp hpp rs go java class jar kt swift rb php pl sh "
                "bash zsh fish lua r sql json yaml yml toml ini cfg xml html htm css scss less vue "
                "ipynb so a o node wasm exe msi bin run dll whl gem")


def kind_for(name):
    dot = name.rfind(".")
    if dot > 0:
        ext = name[dot + 1:].lower()
        k = _EXT.get(ext)
        if k is not None:
            return k
        mt, _enc = mimetypes.guess_type(name, strict=False)
        if mt:
            major = mt.split("/", 1)[0]
            if major == "image":
                return KIND_IMAGES
            if major == "video":
                return KIND_VIDEO
            if major == "audio":
                return KIND_AUDIO
            if major == "text":
                return KIND_DOCUMENTS
            if "zip" in mt or "compressed" in mt or "archive" in mt:
                return KIND_ARCHIVES
    return KIND_OTHER


# ── Node ──────────────────────────────────────────────────────────────────
class Node:
    __slots__ = ("name", "parent", "size", "count", "mtime", "kinds", "children", "flags",
                 "root_path", "__weakref__")

    def __init__(self, name, parent=None, flags=DIR):
        self.name = name
        self.parent = parent
        self.size = 0
        self.count = 0
        self.mtime = 0.0
        self.kinds = [0] * NUM_KINDS
        self.children = []
        self.flags = flags
        self.root_path = None

    # identity
    @property
    def is_dir(self):
        return bool(self.flags & DIR)

    @property
    def is_files(self):
        return bool(self.flags & FILES)

    @property
    def is_other_drive(self):
        return bool(self.flags & OTHER_DRIVE)

    @property
    def is_link(self):
        return bool(self.flags & LINK)

    @property
    def is_hidden(self):
        return bool(self.flags & HIDDEN)

    def path(self):
        parts = []
        n = self
        if n.flags & FILES and n.parent is not None:
            n = n.parent
        while n.parent is not None:
            parts.append(n.name)
            n = n.parent
        if not parts:
            return n.root_path
        parts.append(n.root_path)
        return os.path.join(*reversed(parts))

    def label(self):
        if self.flags & FILES:
            return _("Files in this folder")
        if self.flags & OTHER_DRIVE:
            return _("%s (other drive)") % self.name
        return self.name

    def depth(self):
        d, n = 0, self
        while n.parent is not None:
            d += 1
            n = n.parent
        return d

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent

    def is_ancestor_of(self, other):
        n = other.parent
        while n is not None:
            if n is self:
                return True
            n = n.parent
        return False

    def dominant_kind(self):
        best, bk = -1, KIND_OTHER
        for k, v in enumerate(self.kinds):
            if v > best:
                best, bk = v, k
        return bk if best > 0 else KIND_OTHER

    def sorted_children(self, show_hidden=True):
        kids = list(self.children)
        if not show_hidden:
            kids = [c for c in kids if not (c.flags & HIDDEN)]
        kids.sort(key=lambda c: c.size, reverse=True)
        return kids

    def find(self, path):
        """Locate the node for an absolute path below this one."""
        base = self.path()
        if path == base:
            return self
        if not path.startswith(base.rstrip(os.sep) + os.sep):
            return None
        rel = path[len(base.rstrip(os.sep)) + 1:]
        node = self
        for part in rel.split(os.sep):
            nxt = None
            for c in node.children:
                if c.name == part and not (c.flags & FILES):
                    nxt = c
                    break
            if nxt is None:
                return None
            node = nxt
        return node

    def detach(self):
        """Remove from the tree and subtract totals from every ancestor."""
        p = self.parent
        if p is None:
            return
        try:
            p.children.remove(self)
        except ValueError:
            if self.flags & (DIR | FILES | OTHER_DRIVE | LINK):
                return
            # a plain file node (Biggest/Oldest/listed files): it lives inside
            # the parent's "Files in this folder" pseudo-node
            for fn in p.children:
                if fn.flags & FILES:
                    fn.size = max(0, fn.size - self.size)
                    fn.count = max(0, fn.count - self.count)
                    for k in range(NUM_KINDS):
                        fn.kinds[k] = max(0, fn.kinds[k] - self.kinds[k])
                    if fn.count == 0:
                        p.children.remove(fn)
                    break
        for a in self.ancestors():
            a.size -= self.size
            a.count -= self.count
            for k in range(NUM_KINDS):
                a.kinds[k] -= self.kinds[k]
            if a.size < 0:
                a.size = 0
        self.parent = None

    def sort_recursive(self):
        stack = [self]
        while stack:
            n = stack.pop()
            if n.children:
                n.children.sort(key=lambda c: c.size, reverse=True)
                stack.extend(n.children)


# ── Serialisation for the scan cache ─────────────────────────────────────
def node_to_list(root):
    def enc(n):
        return [n.name, n.flags, n.size, n.count, round(n.mtime, 1), n.kinds,
                [enc(c) for c in n.children]]
    # iterative to avoid deep recursion
    out = [root.name, root.flags, root.size, root.count, round(root.mtime, 1), root.kinds, []]
    stack = [(root, out)]
    while stack:
        n, lst = stack.pop()
        for c in n.children:
            cl = [c.name, c.flags, c.size, c.count, round(c.mtime, 1), list(c.kinds), []]
            lst[6].append(cl)
            if c.children:
                stack.append((c, cl))
    return out


def node_from_list(data, root_path):
    root = Node(data[0], None, data[1])
    root.size, root.count, root.mtime, root.kinds = data[2], data[3], data[4], list(data[5])
    root.root_path = root_path
    stack = [(root, data[6])]
    while stack:
        parent, kids = stack.pop()
        for k in kids:
            n = Node(k[0], parent, k[1])
            n.size, n.count, n.mtime, n.kinds = k[2], k[3], k[4], list(k[5])
            parent.children.append(n)
            if k[6]:
                stack.append((n, k[6]))
    return root


# ── Scan result ───────────────────────────────────────────────────────────
class ScanResult:
    def __init__(self, root_path, size_mode):
        self.root_path = root_path
        self.size_mode = size_mode
        self.root = Node(os.path.basename(root_path.rstrip(os.sep)) or root_path, None, DIR)
        self.root.root_path = root_path
        self.errors = []            # unreadable folder paths
        self.biggest = []           # min-heap (size, seq, path, kind, last_used)
        self.oldest = []            # max-heap (-last_used, seq, path, kind, size)
        self.files = 0
        self.dirs = 0
        self.started = time.time()
        self.finished = None
        self.cancelled = False
        self.from_cache = False
        self.scanned_at = time.time()
        self.lock = threading.Lock()

    def biggest_under(self, path, limit=200):
        pre = path.rstrip(os.sep) + os.sep
        rows = [r for r in self.biggest if r[2].startswith(pre) or path == "/"]
        rows.sort(key=lambda r: r[0], reverse=True)
        return [(r[2], r[0], r[3], r[4]) for r in rows[:limit]]

    def oldest_under(self, path, limit=200):
        pre = path.rstrip(os.sep) + os.sep
        rows = [r for r in self.oldest if r[2].startswith(pre) or path == "/"]
        rows.sort(key=lambda r: (-r[0], -r[4]))
        return [(r[2], r[4], r[3], -r[0]) for r in rows[:limit]]

    def forget_paths(self, paths):
        """Drop removed files (or anything under removed folders) from the heaps."""
        ps = set(paths)
        pres = tuple(p.rstrip(os.sep) + os.sep for p in ps)

        def keep(p):
            return p not in ps and not p.startswith(pres)
        self.biggest = [r for r in self.biggest if keep(r[2])]
        heapq.heapify(self.biggest)
        self.oldest = [r for r in self.oldest if keep(r[2])]
        heapq.heapify(self.oldest)

    # cache
    def cache_file(self):
        h = hashlib.sha256(("%s\0%s" % (self.root_path, self.size_mode)).encode()).hexdigest()[:32]
        return os.path.join(storage.scans_dir(), h + ".json.gz")

    def save(self):
        payload = {
            "v": CACHE_VERSION, "root": self.root_path, "mode": self.size_mode,
            "time": self.scanned_at, "files": self.files, "dirs": self.dirs,
            "errors": self.errors[:5000], "tree": node_to_list(self.root),
            "biggest": [list(r) for r in self.biggest], "oldest": [list(r) for r in self.oldest],
        }
        raw = gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), 5)
        storage.write_private(self.cache_file(), raw, binary=True)

    @classmethod
    def load(cls, root_path, size_mode):
        r = cls(root_path, size_mode)
        try:
            with open(r.cache_file(), "rb") as f:
                payload = json.loads(gzip.decompress(f.read()))
        except (OSError, ValueError, EOFError):
            return None
        if payload.get("v") != CACHE_VERSION or payload.get("root") != root_path:
            return None
        r.root = node_from_list(payload["tree"], root_path)
        r.files, r.dirs = payload.get("files", 0), payload.get("dirs", 0)
        r.errors = payload.get("errors", [])
        r.biggest = [tuple(x) for x in payload.get("biggest", [])]
        r.oldest = [tuple(x) for x in payload.get("oldest", [])]
        heapq.heapify(r.biggest)
        heapq.heapify(r.oldest)
        r.scanned_at = payload.get("time", time.time())
        r.finished = r.scanned_at
        r.from_cache = True
        return r


# ── Scanner ───────────────────────────────────────────────────────────────
class Scanner:
    """Walk a folder on a worker thread.

    `on_progress(result)` is called from the worker at most every 100 ms and
    `on_done(result)` once at the end; the GUI wraps both in GLib.idle_add.
    """

    def __init__(self, root_path, size_mode="disk", exclude=(), on_progress=None, on_done=None,
                 push_interval=0.1):
        self.root_path = os.path.abspath(root_path)
        self.size_mode = size_mode
        self.exclude = set(os.path.abspath(e) for e in exclude or ())
        self.on_progress = on_progress
        self.on_done = on_done
        self.push_interval = push_interval
        self.result = ScanResult(self.root_path, size_mode)
        self._cancel = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="halo-scan", daemon=True)
        self._thread.start()
        return self

    def cancel(self):
        self._cancel.set()

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def run_sync(self):
        self._run()
        return self.result

    def _run(self):
        res = self.result
        try:
            self._walk(res)
        finally:
            res.cancelled = self._cancel.is_set()
            res.root.sort_recursive()
            res.finished = time.time()
            res.scanned_at = res.finished
            if self.on_done:
                self.on_done(res)

    def _walk(self, res):
        apparent = self.size_mode == "apparent"
        root = res.root
        try:
            rst = os.stat(self.root_path, follow_symlinks=False)
        except OSError:
            res.errors.append(self.root_path)
            root.flags |= UNREADABLE
            return
        root_dev = rst.st_dev
        skip = set()
        if self.root_path == "/":
            skip = set(SKIP_ON_ROOT)
        skip |= self.exclude
        seen_inodes = set()
        seq = itertools.count()
        biggest, oldest = res.biggest, res.oldest
        cancel = self._cancel
        last_push = time.monotonic()
        push_interval = self.push_interval
        dir_own = (rst.st_blocks * 512) if not apparent else rst.st_size

        stack = [(root, self.root_path, dir_own)]
        heappush, heapreplace = heapq.heappush, heapq.heapreplace
        S_ISDIR, S_ISREG, S_ISLNK = statmod.S_ISDIR, statmod.S_ISREG, statmod.S_ISLNK

        while stack:
            if cancel.is_set():
                break
            node, path, own_size = stack.pop()
            own_count = 0
            own_kinds = [0] * NUM_KINDS
            newest = 0.0
            files_size = 0
            links = 0
            big = []          # min-heap of this folder's largest files
            try:
                it = os.scandir(path)
            except OSError:
                res.errors.append(path)
                node.flags |= UNREADABLE
                it = None
            if it is not None:
                with it:
                    while True:
                        try:
                            entry = next(it)
                        except StopIteration:
                            break
                        except OSError:
                            res.errors.append(path)
                            break
                        name = entry.name
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        mode = st.st_mode
                        hidden = HIDDEN if name.startswith(".") else 0
                        if S_ISDIR(mode):
                            full = entry.path
                            if full in skip:
                                continue
                            if st.st_dev != root_dev:
                                o = Node(name, node, OTHER_DRIVE | hidden)
                                node.children.append(o)
                                continue
                            child = Node(name, node, DIR | hidden)
                            child.mtime = st.st_mtime
                            node.children.append(child)
                            res.dirs += 1
                            stack.append((child, full, st.st_size if apparent else st.st_blocks * 512))
                            continue
                        if S_ISLNK(mode):
                            # the link itself is never followed; its own blocks
                            # (usually 0 on ext4) still count, as du counts them
                            lsize = st.st_size if apparent else st.st_blocks * 512
                            if lsize:
                                own_kinds[KIND_OTHER] += lsize
                                files_size += lsize
                            if links < MAX_LINKS_PER_DIR:
                                ln = Node(name, node, LINK | hidden)
                                ln.mtime = st.st_mtime
                                node.children.append(ln)
                            links += 1
                            continue
                        # regular files and anything else that occupies space
                        if st.st_nlink > 1 and S_ISREG(mode):
                            key = (st.st_dev, st.st_ino)
                            if key in seen_inodes:
                                continue
                            seen_inodes.add(key)
                        size = st.st_size if apparent else st.st_blocks * 512
                        k = kind_for(name)
                        own_kinds[k] += size
                        files_size += size
                        own_count += 1
                        mt = st.st_mtime
                        if mt > newest:
                            newest = mt
                        res.files += 1
                        if size >= BIG_FILE_MIN:
                            bitem = (size, name, k, mt, hidden)
                            if len(big) < BIG_FILES_PER_DIR:
                                heappush(big, bitem)
                            elif size > big[0][0]:
                                heapreplace(big, bitem)
                        if S_ISREG(mode):
                            full = entry.path
                            last_used = st.st_atime if st.st_atime > mt else mt
                            item = (size, next(seq), full, k, last_used)
                            if len(biggest) < HEAP_CAP:
                                heappush(biggest, item)
                            elif size > biggest[0][0]:
                                heapreplace(biggest, item)
                            if size >= OLDEST_MIN_SIZE:
                                o_item = (-last_used, item[1], full, k, size)
                                if len(oldest) < HEAP_CAP:
                                    heappush(oldest, o_item)
                                elif -last_used > oldest[0][0]:
                                    heapreplace(oldest, o_item)
            # a folder's biggest files become their own arcs; the rest are
            # grouped as one "Files in this folder" child
            rest_size, rest_count, rest_kinds = files_size, own_count, list(own_kinds)
            for bsize, bname, bk, bmt, bhid in big:
                bn = Node(bname, node, bhid)
                bn.size = bsize
                bn.count = 1
                bn.kinds[bk] = bsize
                bn.mtime = bmt
                node.children.append(bn)
                rest_size -= bsize
                rest_count -= 1
                rest_kinds[bk] -= bsize
            if rest_count > 0:
                fn = Node("", node, FILES)
                fn.size = rest_size
                fn.count = rest_count
                fn.mtime = newest
                fn.kinds = rest_kinds
                node.children.append(fn)
            # propagate own totals up the chain (once per folder, not per file)
            total_own = own_size + files_size
            with res.lock:
                n = node
                while n is not None:
                    n.size += total_own
                    n.count += own_count
                    if own_count:
                        nk = n.kinds
                        for i in range(NUM_KINDS):
                            if own_kinds[i]:
                                nk[i] += own_kinds[i]
                    if newest > n.mtime:
                        n.mtime = newest
                    n = n.parent
            if self.on_progress is not None:
                now = time.monotonic()
                if now - last_push >= push_interval:
                    last_push = now
                    self.on_progress(res)


class FileNodes:
    """Stable, detached Node objects for individual files (Biggest/Oldest tabs
    and expanded "Files in this folder" rows), so one file is one selectable
    object everywhere."""

    def __init__(self, root):
        self.root = root
        self._cache = {}

    def get(self, path, size, kind, mtime=0.0):
        n = self._cache.get(path)
        if n is not None and n.parent is not None:
            return n
        if self.root is not None:
            t = self.root.find(path)
            if t is not None and not (t.flags & (DIR | FILES | OTHER_DRIVE | LINK)):
                self._cache[path] = t
                return t
        parent = self.root.find(os.path.dirname(path)) if self.root is not None else None
        n = Node(os.path.basename(path), parent, HIDDEN if os.path.basename(path).startswith(".") else 0)
        if parent is None:
            n.root_path = path
        n.size = size
        n.count = 1
        n.kinds[kind] = size
        n.mtime = mtime
        self._cache[path] = n
        return n

    def list_folder(self, folder_node, size_mode="disk", show_hidden=True):
        """Individual files directly inside a folder node, biggest first."""
        out = []
        path = folder_node.path()
        own = {c.name for c in folder_node.children}
        apparent = size_mode == "apparent"
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            continue
                        st = e.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if not show_hidden and e.name.startswith("."):
                        continue
                    if e.name in own:
                        continue
                    if statmod.S_ISLNK(st.st_mode):
                        continue
                    size = st.st_size if apparent else st.st_blocks * 512
                    out.append(self.get(e.path, size, kind_for(e.name), st.st_mtime))
        except OSError:
            pass
        out.sort(key=lambda n: n.size, reverse=True)
        return out


def scan_path(path, size_mode="disk"):
    """Blocking scan, used by the CLI helper and tests."""
    return Scanner(path, size_mode).run_sync()
