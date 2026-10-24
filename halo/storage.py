# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Everything Halo stores on this computer, and nothing more.

~/.config/halo/settings.json      preferences
~/.cache/halo/scans/              past scan results (+ recent.json)
~/.local/state/halo/halo.log      errors and events, never shred-job file names
~/Documents/Halo Reports/         shred reports, only when reports are on

Folders are created 0700 and files 0600.
"""

import copy
import json
import logging
import os
import tempfile
import time

from .util import home


def _xdg(var, default):
    v = os.environ.get(var)
    return v if v and os.path.isabs(v) else os.path.join(home(), default)


def config_dir():
    return os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "halo")


def cache_dir():
    return os.path.join(_xdg("XDG_CACHE_HOME", ".cache"), "halo")


def scans_dir():
    return os.path.join(cache_dir(), "scans")


def state_dir():
    return os.path.join(_xdg("XDG_STATE_HOME", os.path.join(".local", "state")), "halo")


def log_path():
    return os.path.join(state_dir(), "halo.log")


def settings_path():
    return os.path.join(config_dir(), "settings.json")


def recent_path():
    return os.path.join(cache_dir(), "recent.json")


def default_report_dir():
    docs = os.path.join(home(), "Documents")
    try:
        from gi.repository import GLib
        d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
        if d:
            docs = d
    except Exception:
        pass
    return os.path.join(docs, "Halo Reports")


def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def write_private(path, data, binary=False):
    """Atomically write a 0600 file."""
    ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb" if binary else "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Settings ──────────────────────────────────────────────────────────────

def _default_favourites():
    h = home()
    out = [h]
    for name in ("Downloads", "Pictures", "Videos", "Documents"):
        out.append(os.path.join(h, name))
    try:
        from gi.repository import GLib
        specials = [GLib.UserDirectory.DIRECTORY_DOWNLOAD, GLib.UserDirectory.DIRECTORY_PICTURES,
                    GLib.UserDirectory.DIRECTORY_VIDEOS, GLib.UserDirectory.DIRECTORY_DOCUMENTS]
        out = [h] + [GLib.get_user_special_dir(s) or out[i + 1] for i, s in enumerate(specials)]
    except Exception:
        pass
    return out


DEFAULTS = {
    # General
    "size_mode": "disk",            # "disk" (st_blocks*512) or "apparent" (st_size)
    "show_hidden": True,
    "exclude": [],
    "reopen_last": True,
    "last_root": None,
    # Shredding
    "shred_method": "dod3",         # "dod3" or "random1"
    "verify": True,
    "trace_thumbnails": True,
    "trace_recent": True,
    "trace_trash": True,
    "hold_seconds": 1.5,
    "reports": True,
    "report_dir": None,
    "report_names": False,
    "warn_ssd": False,              # SSD caveat card before shredding (opt-in)
    # Appearance
    "reduce_motion": False,
    "rings": 5,
    "color_mode": "kind",           # "kind" or "folder"
    # Sidebar
    "favourites": None,
    # Window state
    "window_width": 1280,
    "window_height": 800,
    "window_maximized": False,
    "last_tab": "outline",
    "show_sidebar": True,
    "show_panel": True,
    # First run
    "welcomed": False,
    "understood_shred": False,
}


class Settings:
    def __init__(self, path=None):
        self.path = path or settings_path()
        self._data = copy.deepcopy(DEFAULTS)
        self._listeners = []
        try:
            with open(self.path) as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k in DEFAULTS:
                        self._data[k] = v
        except (OSError, ValueError):
            pass
        if not self._data.get("favourites"):
            self._data["favourites"] = _default_favourites()
        if not self._data.get("report_dir"):
            self._data["report_dir"] = default_report_dir()
        self._data["rings"] = max(3, min(6, int(self._data.get("rings") or 5)))
        self._data["hold_seconds"] = max(1.0, min(3.0, float(self._data.get("hold_seconds") or 1.5)))

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def __setitem__(self, key, value):
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.save()
        for cb in list(self._listeners):
            try:
                cb(key, value)
            except Exception:
                logging.getLogger("halo").exception("settings listener failed")

    def connect(self, cb):
        self._listeners.append(cb)

    def save(self):
        try:
            write_private(self.path, json.dumps(self._data, indent=2, sort_keys=True))
        except OSError:
            logging.getLogger("halo").warning("could not save settings")


# ── Recent scans ──────────────────────────────────────────────────────────

def load_recent():
    try:
        with open(recent_path()) as f:
            data = json.load(f)
        return [r for r in data if isinstance(r, dict) and "path" in r][:5]
    except (OSError, ValueError):
        return []


def add_recent(path, size, when=None):
    items = [r for r in load_recent() if r.get("path") != path]
    items.insert(0, {"path": path, "size": int(size), "time": when or time.time()})
    try:
        write_private(recent_path(), json.dumps(items[:5]))
    except OSError:
        pass
    return items[:5]


# ── Logging ───────────────────────────────────────────────────────────────

class _RotatingLog(logging.FileHandler):
    """Rotates at max_bytes, keeping one old file. (logging.handlers would
    pull in the socket module, and Halo has no networking code at all.)"""

    def __init__(self, path, max_bytes):
        super().__init__(path, encoding="utf-8", delay=True)
        self.max_bytes = max_bytes

    def emit(self, record):
        try:
            if os.path.exists(self.baseFilename) and os.path.getsize(self.baseFilename) >= self.max_bytes:
                if self.stream is not None:
                    self.stream.close()
                    self.stream = None
                os.replace(self.baseFilename, self.baseFilename + ".1")
        except OSError:
            pass
        super().emit(record)
        try:
            os.chmod(self.baseFilename, 0o600)
        except OSError:
            pass


def setup_logging():
    log = logging.getLogger("halo")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    try:
        ensure_dir(state_dir())
        h = _RotatingLog(log_path(), 1_000_000)
        try:
            if not os.path.exists(log_path()):
                open(log_path(), "a").close()
            os.chmod(log_path(), 0o600)
        except OSError:
            pass
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(h)
    except OSError:
        log.addHandler(logging.NullHandler())
    return log


# ── Privacy and data ──────────────────────────────────────────────────────

def _tree_size(path):
    total = 0
    if os.path.isfile(path):
        try:
            return os.lstat(path).st_size
        except OSError:
            return 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def data_locations(settings=None):
    """(label, path, size) for every place Halo stores data."""
    rows = [
        ("Preferences", settings_path()),
        ("Past scans", cache_dir()),
        ("Log", state_dir()),
    ]
    if settings is not None:
        rows.append(("Shred reports", settings["report_dir"]))
    return [(label, p, _tree_size(p) if os.path.exists(p) else 0) for label, p in rows]


def own_data_files():
    """Files 'Clear Halo's Own Data' shreds: caches, logs and the recent list."""
    out = []
    for base in (cache_dir(), state_dir()):
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            for f in files:
                p = os.path.join(root, f)
                if not os.path.islink(p):
                    out.append(p)
    return out


def remove_empty_dirs():
    for base in (scans_dir(), cache_dir(), state_dir()):
        for root, dirs, files in sorted(os.walk(base, topdown=False), key=lambda t: -len(t[0])):
            try:
                os.rmdir(root)
            except OSError:
                pass
