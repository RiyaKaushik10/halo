# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Where Halo's bundled files live, installed or in a source checkout."""

import os

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_ROOT = os.path.dirname(PKG_DIR)

VERSION = "1.0.1"
APP_ID = "io.github.RiyaKaushik10.Halo"
REPO_URL = "https://github.com/RiyaKaushik10/halo"
ISSUES_URL = REPO_URL + "/issues"
DEV_NAME = "Riya K"
DEV_EMAIL = "riyak1@proton.me"
HELPER_INSTALLED = "/usr/libexec/halo/halo-helper"


def _first(*cands):
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[-1]


def style_css():
    return os.path.join(PKG_DIR, "style.css")


def data_dir():
    return _first(os.path.join(SRC_ROOT, "data"), "/usr/share/halo/data")


def icons_dir():
    return _first(os.path.join(SRC_ROOT, "data", "icons"), "/usr/share/halo/data/icons")


def help_dir():
    return _first(os.path.join(SRC_ROOT, "data", "help"), "/usr/share/halo/data/help")


def doc_file(name):
    """PRIVACY.md, TERMS.md, LICENSE: /usr/share/doc/halo when installed."""
    prefix = os.path.dirname(os.path.dirname(SRC_ROOT))
    return _first(os.path.join(SRC_ROOT, name), os.path.join(SRC_ROOT, "data", "docs", name),
                  os.path.join(prefix, "share", "doc", "halo", name), os.path.join("/usr/share/doc/halo", name),
                  os.path.join("/usr/share/doc/halo", name + ".gz"))


def helper():
    prefix = os.path.dirname(os.path.dirname(SRC_ROOT))        # /usr or /usr/local
    return _first(os.path.join(prefix, "libexec", "halo", "halo-helper"), HELPER_INSTALLED,
                  os.path.join(SRC_ROOT, "helper", "halo-helper"))


def read_version():
    """The version lives in debian/changelog; fall back to the constant."""
    p = os.path.join(SRC_ROOT, "debian", "changelog")
    try:
        with open(p) as f:
            first = f.readline()
        v = first.split("(", 1)[1].split(")", 1)[0]
        return v.split("-")[0]
    except (OSError, IndexError):
        pass
    try:
        from ._version import VERSION as built
        return built
    except ImportError:
        return VERSION
