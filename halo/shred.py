# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Halo's own overwrite engine (section 5).

DoD 5220.22-M 3-pass method: 0x00, 0xFF, random (os.urandom), each followed by
fsync, then the last pass is read back and compared chunk by chunk using
BLAKE2b digests. Then: truncate, scrub the name, unlink, fsync the parent.

Run it on its own:
    python3 -m halo.shred <path>... --method dod3 --verify
    HALO_DRY_RUN=1 python3 -m halo.shred <path>...   # changes nothing
"""

import argparse
import hashlib
import json
import logging
import os
import secrets
import stat
import string
import sys
import threading
import time

CHUNK = 1024 * 1024
NAME_ALPHABET = string.ascii_letters + string.digits

METHOD_DOD3 = "dod3"
METHOD_RANDOM1 = "random1"
METHODS = {
    METHOD_DOD3: ["zeros", "ones", "random"],
    METHOD_RANDOM1: ["random"],
}
METHOD_LABELS = {
    METHOD_DOD3: "DoD 5220.22-M 3-pass method",
    METHOD_RANDOM1: "Single random pass",
}

# Per-file outcomes
VERIFIED = "verified"          # overwritten, verified, removed
REMOVED = "removed"            # overwritten and removed, verification off
FAILED = "failed"              # verification mismatch or I/O error; file kept
PARTIAL = "partial"            # cancelled mid-pass: partly overwritten, not removed
SKIPPED = "skipped"            # cancelled before this file started
REFUSED = "refused"            # symlink / device / socket / FIFO
LINK_REMOVED = "link_removed"  # user chose to remove just the symlink

STATUS_TEXT = {
    VERIFIED: "Verified and removed",
    REMOVED: "Removed (not verified)",
    FAILED: "Failed, file kept",
    PARTIAL: "Partly overwritten, not removed",
    SKIPPED: "Not started",
    REFUSED: "Refused",
    LINK_REMOVED: "Link removed, target untouched",
}

log = logging.getLogger("halo.shred")


def is_dry_run():
    return os.environ.get("HALO_DRY_RUN") == "1"


class Cancelled(Exception):
    pass


class VerifyMismatch(Exception):
    pass


class FileResult:
    __slots__ = ("path", "status", "message", "bytes", "is_dir")

    def __init__(self, path, status, message="", nbytes=0, is_dir=False):
        self.path, self.status, self.message, self.bytes, self.is_dir = path, status, message, nbytes, is_dir

    def to_dict(self):
        return {"path": self.path, "status": self.status, "message": self.message,
                "bytes": self.bytes, "is_dir": self.is_dir}

    def __repr__(self):
        return "FileResult(%r, %r)" % (self.path, self.status)


def needs_privilege(path):
    """True if the current user can't shred `path` alone (not owner or can't write)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if os.geteuid() == 0:
        return False
    if st.st_uid != os.geteuid():
        return True
    parent = os.path.dirname(os.path.abspath(path))
    if not os.access(parent, os.W_OK | os.X_OK):
        return True
    if stat.S_ISDIR(st.st_mode):
        for root, dirs, files in os.walk(path):
            for n in dirs + files:
                try:
                    if os.lstat(os.path.join(root, n)).st_uid != os.geteuid():
                        return True
                except OSError:
                    pass
    return False


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _random_name(length, parent, avoid):
    for _ in range(64):
        n = "".join(secrets.choice(NAME_ALPHABET) for _ in range(length))
        if n not in avoid and not os.path.lexists(os.path.join(parent, n)):
            return n
    return None


class Shredder:
    """Shred a list of files and folders.

    progress(event: dict) is called from the calling thread with events:
      start         {total_bytes, total_files}
      file_start    {index, path, size, passes}
      pass          {index, pass, passes, fraction}
      verify        {index, fraction}
      file_done     {index, path, status, message}
      done          {results}
    """

    def __init__(self, method=METHOD_DOD3, verify=True, dry_run=None, progress=None,
                 cancel_event=None, chunk=CHUNK, remove_links=False):
        if method not in METHODS:
            raise ValueError("unknown method %r" % method)
        self.method = method
        self.passes = METHODS[method]
        self.verify = verify
        self.dry_run = is_dry_run() if dry_run is None else dry_run
        self.progress = progress or (lambda ev: None)
        self.cancel_event = cancel_event or threading.Event()
        self.chunk = chunk
        self.remove_links = remove_links
        self.read_hook = None        # tests: callable(bytes, offset) -> bytes
        self._file_index = 0
        self._bytes_done = 0

    # ── public ────────────────────────────────────────────────────────────
    def cancel(self):
        self.cancel_event.set()

    def plan(self, paths):
        """Expand folders depth-first: [(path, size, is_dir)] files first, folders after."""
        out = []
        for p in paths:
            try:
                st = os.lstat(p)
            except OSError:
                out.append((p, 0, False))
                continue
            if stat.S_ISDIR(st.st_mode):
                for root, dirs, files in os.walk(p, topdown=False, followlinks=False):
                    for f in sorted(files):
                        fp = os.path.join(root, f)
                        try:
                            s = os.lstat(fp)
                            out.append((fp, s.st_size if stat.S_ISREG(s.st_mode) else 0, False))
                        except OSError:
                            out.append((fp, 0, False))
                    for d in sorted(dirs):
                        dp = os.path.join(root, d)
                        if os.path.islink(dp):
                            out.append((dp, 0, False))
                    for d in sorted(dirs):
                        dp = os.path.join(root, d)
                        if not os.path.islink(dp):
                            out.append((dp, 0, True))
                out.append((p, 0, True))
            else:
                out.append((p, st.st_size if stat.S_ISREG(st.st_mode) else 0, False))
        # nested dirs: os.walk(topdown=False) already yields children before parents,
        # but the per-root listing above appends subfolders after their files; sort
        # directories so deeper ones come first.
        files = [x for x in out if not x[2]]
        dirs = [x for x in out if x[2]]
        dirs.sort(key=lambda x: x[0].count(os.sep), reverse=True)
        seen = set()
        uniq = []
        for x in files + dirs:
            if x[0] not in seen:
                seen.add(x[0])
                uniq.append(x)
        return uniq

    def run(self, paths):
        plan = self.plan(paths)
        total = sum(s for _p, s, d in plan if not d)
        nfiles = sum(1 for _p, _s, d in plan if not d)
        self.progress({"type": "start", "total_bytes": total, "total_files": nfiles,
                       "method": self.method, "dry_run": self.dry_run})
        log.info("shred job: %d files, %d bytes, method=%s, verify=%s, dry_run=%s",
                 nfiles, total, self.method, self.verify, self.dry_run)
        top = set(os.path.abspath(p) for p in paths)
        results = [None] * len(plan)
        failed_dirs = set()

        def done(k, r):
            results[k] = r
            self.progress({"type": "file_done", "index": k, "path": r.path, "status": r.status,
                           "message": r.message})
            if not r.is_dir and r.status not in (VERIFIED, REMOVED, LINK_REMOVED):
                d = os.path.dirname(r.path)
                while d and d not in failed_dirs:
                    failed_dirs.add(d)
                    nd = os.path.dirname(d)
                    if nd == d:
                        break
                    d = nd

        # Items are handled in groups that share a parent folder. Every file in a
        # group is overwritten and verified first; then all their names are
        # scrubbed together, so no directory block reaches the journal while it
        # still holds one of the original names.
        i = 0
        while i < len(plan):
            parent, is_dir = os.path.dirname(plan[i][0]), plan[i][2]
            j = i
            while j < len(plan) and plan[j][2] == is_dir and os.path.dirname(plan[j][0]) == parent:
                j += 1
            ready = []
            for k in range(i, j):
                p, size, _d = plan[k]
                self._file_index = k
                if self.cancel_event.is_set():
                    done(k, FileResult(p, SKIPPED, STATUS_TEXT[SKIPPED], size, is_dir))
                    continue
                self.progress({"type": "file_start", "index": k, "path": p, "size": size,
                               "passes": len(self.passes), "is_dir": is_dir})
                if is_dir:
                    r = self._check_dir(p, failed_dirs)
                else:
                    r = self._overwrite(p, size, os.path.abspath(p) not in top)
                if r is None:
                    ready.append((k, p, size))
                else:
                    done(k, r)
            if ready:
                for k, r in self._finish(ready, is_dir):
                    done(k, r)
            i = j
        self.progress({"type": "done", "results": [r.to_dict() for r in results]})
        ok = sum(1 for r in results if r.status in (VERIFIED, REMOVED, LINK_REMOVED))
        log.info("shred job finished: %d of %d items done", ok, len(results))
        return results

    # ── internals ─────────────────────────────────────────────────────────
    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def _overwrite(self, path, size, inside_folder):
        """Steps 1-5: pre-flight, open, size, write every pass, verify.
        Returns None when the file is ready to be removed, else a FileResult."""
        idx = self._file_index
        try:
            lst = os.lstat(path)
        except OSError as e:
            return FileResult(path, FAILED, "Can't read: %s" % e.strerror, size)
        mode = lst.st_mode
        if stat.S_ISLNK(mode):
            # Never follow. Inside a chosen folder the link is just removed;
            # at top level only if the user explicitly asked for it.
            if inside_folder or self.remove_links:
                if not self.dry_run:
                    os.unlink(path)
                    _fsync_dir(os.path.dirname(path) or ".")
                log.info("item %d: symlink removed, target untouched (dry_run=%s)", idx, self.dry_run)
                return FileResult(path, LINK_REMOVED, STATUS_TEXT[LINK_REMOVED], 0)
            return FileResult(path, REFUSED, "This is a symbolic link; Halo never follows links", 0)
        if stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            return FileResult(path, REFUSED, "Device files can't be shredded", 0)
        if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode):
            if inside_folder and not self.dry_run:
                os.unlink(path)
                return FileResult(path, REMOVED, "Socket or pipe removed", 0)
            return FileResult(path, REFUSED, "Sockets and pipes can't be shredded", 0)
        if not stat.S_ISREG(mode):
            return FileResult(path, REFUSED, "Not a regular file", 0)

        started = False
        try:
            flags = (os.O_RDONLY if self.dry_run else os.O_WRONLY) | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open(path, flags)
            try:
                fst = os.fstat(fd)
                if not stat.S_ISREG(fst.st_mode) or fst.st_ino != lst.st_ino or fst.st_dev != lst.st_dev:
                    return FileResult(path, REFUSED, "File changed while opening", size)
                blk = fst.st_blksize or 4096
                length = fst.st_size
                if length % blk:
                    length += blk - (length % blk)
                digests = []
                for pi, kind in enumerate(self.passes, 1):
                    started = True
                    last = pi == len(self.passes)
                    digests = self._write_pass(fd, length, kind, pi, keep_digests=last)
                    if not self.dry_run:
                        os.fsync(fd)
                log.info("item %d: %d pass(es) written (dry_run=%s)", idx, len(self.passes), self.dry_run)
            finally:
                os.close(fd)
            if self.verify:
                self._verify(path, length, digests)
                log.info("item %d: verified", idx)
            return None
        except Cancelled:
            if started:
                log.info("item %d: cancelled mid-pass", idx)
                return FileResult(path, PARTIAL, STATUS_TEXT[PARTIAL], size)
            return FileResult(path, SKIPPED, STATUS_TEXT[SKIPPED], size)
        except VerifyMismatch as e:
            log.warning("item %d: verification failed", idx)
            return FileResult(path, FAILED, "Verification failed: %s" % e, size)
        except PermissionError:
            return FileResult(path, FAILED, "Permission denied", size)
        except OSError as e:
            log.warning("item %d: I/O error %s", idx, e.errno)
            return FileResult(path, FAILED, e.strerror or str(e), size)

    def _check_dir(self, path, failed_dirs):
        if path in failed_dirs:
            return FileResult(path, FAILED, "Folder kept because something inside it wasn't removed", 0, True)
        if not self.dry_run:
            try:
                if os.listdir(path):
                    return FileResult(path, FAILED, "Folder not empty", 0, True)
            except OSError as e:
                return FileResult(path, FAILED, e.strerror or str(e), 0, True)
        return None

    def _finish(self, ready, is_dir):
        """Steps 6-9 for a group of siblings: truncate, scrub names, delete."""
        out = []
        cur = {}
        if not is_dir:
            for k, p, size in ready:
                try:
                    if not self.dry_run:
                        fd = os.open(p, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
                        try:
                            os.ftruncate(fd, 0)
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                    cur[k] = p
                except OSError as e:
                    out.append((k, FileResult(p, FAILED, "Overwritten, but couldn't be removed: %s"
                                              % (e.strerror or e), size)))
        else:
            cur = {k: p for k, p, _s in ready}
        sizes = {k: s for k, _p, s in ready}
        errors = {}
        if cur:
            parent = os.path.dirname(os.path.abspath(next(iter(cur.values()))))
            lengths = {k: max(1, len(os.path.basename(p).encode("utf-8", "surrogateescape")))
                       for k, p in cur.items()}
            avoid = set()
            # round 0 renames every name to a random one of the same length, then
            # each round shortens them by one character, down to a single one
            for rnd in range(max(lengths.values())):
                changed = False
                for k in list(cur):
                    if k in errors:
                        continue
                    n = lengths[k] - rnd
                    if n < 1:
                        continue
                    new = _random_name(n, parent, avoid)
                    if new is None:
                        continue
                    avoid.add(new)
                    target = os.path.join(parent, new)
                    try:
                        if not self.dry_run:
                            os.rename(cur[k], target)
                            cur[k] = target
                        changed = True
                    except OSError as e:
                        errors[k] = e
                if changed and not self.dry_run:
                    _fsync_dir(parent)
            for k in list(cur):
                if k in errors:
                    continue
                try:
                    if not self.dry_run:
                        (os.rmdir if is_dir else os.unlink)(cur[k])
                except OSError as e:
                    errors[k] = e
            if not self.dry_run:
                _fsync_dir(parent)
        orig = {k: p for k, p, _s in ready}
        for k in cur:
            if k in errors:
                e = errors[k]
                out.append((k, FileResult(orig[k], FAILED, "Overwritten, but couldn't be removed: %s"
                                          % (e.strerror or e), sizes[k], is_dir)))
            elif is_dir:
                out.append((k, FileResult(orig[k], REMOVED, "Folder removed", 0, True)))
            else:
                st = VERIFIED if self.verify else REMOVED
                out.append((k, FileResult(orig[k], st, STATUS_TEXT[st], sizes[k])))
        log.info("%d item(s) truncated, names scrubbed and removed (dry_run=%s)", len(cur), self.dry_run)
        out.sort(key=lambda t: t[0])
        return out

    def _pattern(self, kind, n):
        if kind == "zeros":
            return bytes(n)
        if kind == "ones":
            return b"\xff" * n
        return os.urandom(n)

    def _write_pass(self, fd, length, kind, pass_no, keep_digests):
        digests = []
        chunk = self.chunk
        fixed = None
        if kind in ("zeros", "ones"):
            fixed = self._pattern(kind, chunk)
        off = 0
        npasses = len(self.passes)
        last_emit = 0.0
        while off < length:
            n = min(chunk, length - off)
            if not self.dry_run:
                data = (fixed if n == chunk else fixed[:n]) if fixed is not None else self._pattern(kind, n)
                if keep_digests:
                    digests.append(hashlib.blake2b(data, digest_size=32).digest())
                written = 0
                while written < n:
                    written += os.pwrite(fd, data[written:], off + written)
            else:
                # dry run: no I/O, just enough delay to watch the progress UI
                # (about 3 GB/s, and never more than ~1.5 s per pass)
                time.sleep(min(n / 3e9, 1.5 * n / max(n, length)))
            off += n
            now = time.monotonic()
            if now - last_emit > 0.05 or off >= length:
                last_emit = now
                self.progress({"type": "pass", "index": self._file_index, "pass": pass_no,
                               "passes": npasses, "fraction": off / length if length else 1.0})
            # finish the current chunk, then honour cancel
            self._check_cancel()
        if length == 0:
            self.progress({"type": "pass", "index": self._file_index, "pass": pass_no,
                           "passes": npasses, "fraction": 1.0})
            self._check_cancel()
        return digests

    def _verify(self, path, length, digests):
        if self.dry_run:
            self.progress({"type": "verify", "index": self._file_index, "fraction": 1.0})
            return
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
            except (OSError, AttributeError):
                pass
        finally:
            os.close(fd)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            off = 0
            i = 0
            last_emit = 0.0
            while off < length:
                n = min(self.chunk, length - off)
                data = b""
                while len(data) < n:
                    part = os.pread(fd, n - len(data), off + len(data))
                    if not part:
                        break
                    data += part
                if len(data) < n:
                    # the file may have been shorter than the block-rounded length on a
                    # filesystem that doesn't extend on pwrite; compare what exists
                    raise VerifyMismatch("short read at block %d" % i)
                if self.read_hook is not None:
                    data = self.read_hook(data, off)
                if i >= len(digests) or hashlib.blake2b(data, digest_size=32).digest() != digests[i]:
                    raise VerifyMismatch("block %d differs" % i)
                off += n
                i += 1
                now = time.monotonic()
                if now - last_emit > 0.05 or off >= length:
                    last_emit = now
                    self.progress({"type": "verify", "index": self._file_index,
                                   "fraction": off / length if length else 1.0})
            if length == 0:
                self.progress({"type": "verify", "index": self._file_index, "fraction": 1.0})
        finally:
            os.close(fd)


def shred_paths(paths, method=METHOD_DOD3, verify=True, dry_run=None, progress=None):
    return Shredder(method, verify, dry_run, progress).run(paths)


# ── CLI ───────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m halo.shred",
                                 description="Overwrite, verify and remove files (Halo shred engine).")
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--method", choices=sorted(METHODS), default=METHOD_DOD3)
    ap.add_argument("--verify", dest="verify", action="store_true", default=True)
    ap.add_argument("--no-verify", dest="verify", action="store_false")
    ap.add_argument("--dry-run", action="store_true", default=is_dry_run(),
                    help="run the full procedure but write, rename and delete nothing")
    ap.add_argument("--remove-links", action="store_true",
                    help="remove top-level symbolic links themselves (targets are never touched)")
    ap.add_argument("--json", action="store_true", help="print progress as JSON lines")
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    a = ap.parse_args(argv)
    paths = [os.path.abspath(p) for p in a.paths]

    if not a.yes and not a.json and not a.dry_run:
        sys.stderr.write("About to shred %d item(s) with the %s. This can't be undone.\n"
                         "Type YES to continue: " % (len(paths), METHOD_LABELS[a.method]))
        sys.stderr.flush()
        if sys.stdin.readline().strip() != "YES":
            sys.stderr.write("Cancelled.\n")
            return 1

    def progress(ev):
        if a.json:
            sys.stdout.write(json.dumps(ev) + "\n")
            sys.stdout.flush()
        elif ev["type"] == "file_done":
            sys.stdout.write("%-34s %s\n" % (ev.get("message") or STATUS_TEXT.get(ev["status"]), ev["path"]))
            sys.stdout.flush()

    sh = Shredder(a.method, a.verify, a.dry_run, progress, remove_links=a.remove_links)
    try:
        import signal
        signal.signal(signal.SIGTERM, lambda *x: sh.cancel())
    except (ValueError, OSError):
        pass
    try:
        results = sh.run(paths)
    except KeyboardInterrupt:
        sh.cancel()
        return 130
    bad = [r for r in results if r.status not in (VERIFIED, REMOVED, LINK_REMOVED)]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
