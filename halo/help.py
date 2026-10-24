# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Offline help, policies and licence in an in-app reader (no web view)."""

import gzip
import os
import re

from gi.repository import GLib, Gtk, Pango

from . import paths
from .util import _

PAGES = [
    ("help", _("Help"), lambda: os.path.join(paths.help_dir(), "help.md")),
    ("ssd", _("Shredding and SSDs"), lambda: os.path.join(paths.help_dir(), "shredding-and-ssds.md")),
    ("privacy", _("Privacy Policy"), lambda: paths.doc_file("PRIVACY.md")),
    ("terms", _("Terms of Service"), lambda: paths.doc_file("TERMS.md")),
    ("licence", _("Licence"), lambda: paths.doc_file("LICENSE")),
]


def read_text(path):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return f.read()
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return _("This page isn't installed.")


def _inline(text):
    t = GLib.markup_escape_text(text, -1)
    t = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*\s][^*]*)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def render_markdown(text, box, plain=False):
    """A small Markdown subset: headings, paragraphs, bullets, numbered lists, code blocks."""
    if plain:
        l = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
        l.add_css_class("p")
        l.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        c = Gtk.Box()
        c.add_css_class("code")
        c.append(l)
        box.append(c)
        return
    lines = text.splitlines()
    para = []
    code = None

    def flush():
        if para:
            l = Gtk.Label(xalign=0, wrap=True, selectable=True)
            l.set_markup(_inline(" ".join(para)))
            l.add_css_class("p")
            box.append(l)
            para.clear()
    table = []

    def flush_table():
        if not table:
            return
        grid = Gtk.Grid(column_spacing=18, row_spacing=8)
        grid.add_css_class("table")
        r = 0
        for row in table:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            for ci, c in enumerate(cells):
                l = Gtk.Label(xalign=0, wrap=True, max_width_chars=28)
                l.set_markup(("<b>%s</b>" if r == 0 else "%s") % _inline(c))
                grid.attach(l, ci, r, 1, 1)
            r += 1
        box.append(grid)
        table.clear()
    for ln in lines:
        if code is None and ln.strip().startswith("|"):
            flush()
            table.append(ln)
            continue
        flush_table()
        if code is not None:
            if ln.strip().startswith("```"):
                c = Gtk.Box()
                c.add_css_class("code")
                l = Gtk.Label(label="\n".join(code), xalign=0, selectable=True)
                c.append(l)
                box.append(c)
                code = None
            else:
                code.append(ln)
            continue
        s = ln.strip()
        if s.startswith("```"):
            flush()
            code = []
            continue
        m = re.match(r"^(#{1,3})\s+(.*)", s)
        if m:
            flush()
            l = Gtk.Label(xalign=0, wrap=True)
            l.set_markup(_inline(m.group(2)))
            l.add_css_class("h%d" % len(m.group(1)))
            box.append(l)
            continue
        m = re.match(r"^([-*]|\d+\.)\s+(.*)", s)
        if m:
            flush()
            row = Gtk.Box(spacing=8)
            bullet = Gtk.Label(label="•" if m.group(1) in "-*" else m.group(1), xalign=1)
            bullet.add_css_class("p")
            bullet.set_valign(Gtk.Align.START)
            bullet.set_size_request(18, -1)
            row.append(bullet)
            l = Gtk.Label(xalign=0, wrap=True, selectable=True)
            l.set_markup(_inline(m.group(2)))
            l.add_css_class("p")
            l.set_hexpand(True)
            row.append(l)
            box.append(row)
            continue
        if not s:
            flush()
            continue
        para.append(s)
    flush_table()
    flush()


class HelpWindow(Gtk.Window):
    def __init__(self, parent, page="help"):
        super().__init__(title=_("Halo Help"), transient_for=parent)
        self.add_css_class("halo-dialog")
        self.set_default_size(920, 700)
        hb = Gtk.HeaderBar()
        hb.add_css_class("halo-header")
        t = Gtk.Label(label=_("Help"))
        t.add_css_class("app-name")
        hb.set_title_widget(t)
        self.set_titlebar(hb)
        root = Gtk.Box()
        self.set_child(root)
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        nav.add_css_class("help-nav")
        nav.set_size_request(220, -1)
        root.append(nav)
        self.buttons = {}
        for key, label, _p in PAGES:
            b = Gtk.Button(label=label)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda w, k=key: self.show_page(k))
            nav.append(b)
            self.buttons[key] = b
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroller.set_hexpand(True)
        root.append(self.scroller)
        self.show_page(page)

    def show_page(self, key):
        for k, b in self.buttons.items():
            (b.add_css_class if k == key else b.remove_css_class)("active")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("help-page")
        box.set_size_request(-1, -1)
        for k, label, pathf in PAGES:
            if k == key:
                text = read_text(pathf())
                if k == "licence":
                    h = Gtk.Label(label=_("Licence"), xalign=0)
                    h.add_css_class("h1")
                    box.append(h)
                render_markdown(text, box, plain=(k == "licence"))
        foot = Gtk.Label(xalign=0, wrap=True)
        foot.set_markup(GLib.markup_escape_text(_("Halo is made by %s · ") % paths.DEV_NAME) +
                        '<a href="mailto:%s">%s</a>' % (paths.DEV_EMAIL, paths.DEV_EMAIL))
        foot.set_margin_top(24)
        fb = Gtk.Box()
        fb.add_css_class("help-foot")
        fb.append(foot)
        box.append(fb)
        clamp = Gtk.Box()
        box.set_hexpand(True)
        box.set_size_request(-1, -1)
        clamp.append(box)
        self.scroller.set_child(clamp)
        self.scroller.get_vadjustment().set_value(0)
