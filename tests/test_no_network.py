# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Halo contains no networking code at all. This test fails the build if any
source file imports a networking library (section 10)."""

import ast
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORBIDDEN = {"socket", "ssl", "http", "urllib", "urllib2", "urllib3", "ftplib", "smtplib", "poplib",
             "imaplib", "nntplib", "telnetlib", "xmlrpc", "requests", "httpx", "aiohttp", "websocket",
             "websockets", "socketserver", "asyncio", "selectors", "email.mime", "webbrowser", "paramiko",
             "pycurl", "grpc", "zmq"}


def sources():
    for folder in ("halo", "helper", "tests", "bin"):
        for dp, dn, fn in os.walk(os.path.join(ROOT, folder)):
            for f in fn:
                p = os.path.join(dp, f)
                if f.endswith(".py") or f == "halo-helper":
                    yield p


def imported(path):
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def test_no_network_imports():
    bad = []
    for p in sources():
        if p.endswith("test_no_network.py"):
            continue
        for mod in imported(p):
            top = mod.split(".")[0]
            if top in FORBIDDEN or mod in FORBIDDEN:
                bad.append((os.path.relpath(p, ROOT), mod))
    assert bad == [], "networking imports are not allowed: %r" % bad


def test_spdx_headers():
    for p in sources():
        head = open(p, encoding="utf-8").read(400)
        assert "SPDX-License-Identifier: MIT" in head, p
        assert "Copyright (c) 2026 Riya K" in head, p


def test_modules_import_without_socket():
    """Import the engine modules with the socket module blocked. (PyGObject's own
    GLib overrides import socket, so modules that need GLib are covered by the
    unshare -rn / strace runs in CI instead.)"""
    code = ("import sys; sys.modules['socket'] = None; sys.modules['ssl'] = None; "
            "import halo.scanner, halo.shred, halo.safety, halo.report, halo.storage; print('ok')")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env=dict(os.environ, PYTHONPATH=ROOT))
    assert out.stdout.strip() == "ok", out.stderr
