# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Small helpers shared by every module: formatting and gettext."""

import gettext
import os
import time

gettext.textdomain("halo")
_ = gettext.gettext
ngettext = gettext.ngettext

_UNITS = ["KB", "MB", "GB", "TB", "PB"]


def format_size(n):
    """Human size with SI units, like Mint's file manager: 4.2 GB, 214 GB."""
    n = max(0, int(n))
    if n < 1000:
        return ngettext("%d byte", "%d bytes", n) % n
    v = float(n)
    unit = "bytes"
    for unit in _UNITS:
        v /= 1000.0
        if v < 1000:
            break
    if v < 100:
        s = ("%.1f" % v)
        if s.endswith(".0") and v >= 10:
            s = s[:-2]
    else:
        s = "%.0f" % v
    return "%s %s" % (s, unit)


def format_count(n):
    return "{:,}".format(int(n)).replace(",", " ")


def format_items(n):
    return ngettext("%s item", "%s items", n) % format_count(n)


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "0:%02d" % seconds
    m, s = divmod(seconds, 60)
    if m < 60:
        return "%d:%02d" % (m, s)
    h, m = divmod(m, 60)
    return "%d:%02d:%02d" % (h, m, s)


def time_ago(ts, now=None):
    now = time.time() if now is None else now
    d = max(0, now - ts)
    if d < 60:
        return _("just now")
    if d < 3600:
        return _("%d min ago") % (d // 60)
    if d < 86400:
        return _("%d h ago") % (d // 3600)
    if d < 86400 * 30:
        return _("%d d ago") % (d // 86400)
    if d < 86400 * 365:
        return _("%d mo ago") % (d // (86400 * 30))
    return _("%d y ago") % (d // (86400 * 365))


def home():
    return os.path.realpath(os.path.expanduser("~"))


def pretty_path(path):
    h = home()
    if path == h:
        return "~"
    if path.startswith(h + os.sep):
        return "~" + path[len(h):]
    return path


def display_name(path):
    if path == home():
        return _("Home")
    if path == "/":
        return _("File System")
    return os.path.basename(path.rstrip(os.sep)) or path


def dry_run():
    return os.environ.get("HALO_DRY_RUN") == "1"
