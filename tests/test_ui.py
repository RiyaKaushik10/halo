# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Sunburst layout and a smoke test of the real window (skipped without a display)."""

import math
import os

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk  # noqa: E402,F401
except (ValueError, ImportError):
    pytest.skip("GTK 4 not available", allow_module_level=True)

from halo import sunburst  # noqa: E402
from halo.scanner import Node, SMALLER, DIR  # noqa: E402


def tree():
    root = Node("root")
    root.root_path = "/r"
    sizes = [500, 300, 150, 40, 5, 3, 1, 1]
    for i, s in enumerate(sizes):
        c = Node("c%d" % i, root, DIR)
        c.size = s * 1000
        c.kinds[i % 7] = c.size
        root.children.append(c)
        for j in range(3):
            g = Node("g%d" % j, c, DIR)
            g.size = c.size // 3
            g.kinds[0] = g.size
            c.children.append(g)
    root.size = sum(c.size for c in root.children)
    return root


def test_layout_largest_first_from_twelve():
    lay = sunburst.compute_layout(tree(), 5)
    ring1 = lay.by_ring[1]
    assert ring1[0].node.name == "c0" and ring1[0].a0 == 0.0
    assert all(a.a1 <= 2 * math.pi + 1e-9 for a in lay.arcs)


def test_small_arcs_merge():
    lay = sunburst.compute_layout(tree(), 5)
    merged = [a for a in lay.by_ring[1] if a.flags & SMALLER]
    assert len(merged) == 1
    assert {n.name for n in merged[0].children_nodes} <= {"c4", "c5", "c6", "c7"}
    for a in lay.arcs:
        if not (a.flags & SMALLER):
            assert math.degrees(a.a1 - a.a0) >= 0.6 - 1e-9


def test_ring_limit():
    lay = sunburst.compute_layout(tree(), 1)
    assert set(lay.by_ring) == {1}


def test_hit_testing():
    lay = sunburst.compute_layout(tree(), 5)
    a = lay.by_ring[1][1]
    assert lay.hit(1, (a.a0 + a.a1) / 2) is a


def test_angular_range():
    r = tree()
    a0, a1 = sunburst.angular_range(r, r.children[1])
    assert a0 == pytest.approx(2 * math.pi * 500 / 1000)
    assert a1 - a0 == pytest.approx(2 * math.pi * 300 / 1000)


def test_colour_tokens_match_css():
    from halo import theme
    css = open(os.path.join(os.path.dirname(theme.__file__), "style.css")).read()
    for k, v in theme.COLORS.items():
        assert "@define-color %s %s;" % (k, v) in css


@pytest.mark.skipif(not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"),
                    reason="needs a display")
def test_window_scans_and_selects(home, tmp_path, mk, monkeypatch):
    from gi.repository import GLib
    from halo.app import HaloApplication
    data = home / "data"
    mk(data / "Videos" / "a.mp4", size=3_000_000)
    mk(data / "Docs" / "b.pdf", size=200_000)
    monkeypatch.setenv("HALO_DRY_RUN", "1")
    app = HaloApplication()
    state = {}

    def run():
        w = app.window
        if w is None:
            return True
        w.scan(str(data), force=True)

        def check():
            if w.scanner is not None and w.scanner.running:
                return True
            state["size"] = w.result.root.size
            w.chart.select_all()
            state["selected"] = len(w.selection)
            state["shred_enabled"] = w.lookup_action("shred").get_enabled()
            app.quit()
            return False
        GLib.timeout_add(100, check)
        return False
    GLib.timeout_add(200, run)
    GLib.timeout_add_seconds(20, app.quit)
    app.run(["halo"])
    assert state.get("size", 0) > 3_000_000
    assert state["selected"] >= 2
    assert state["shred_enabled"]
