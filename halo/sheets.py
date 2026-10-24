# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""In-window sheets: cards on bg_panel with a 16 px radius, a soft shadow and
a dimmed backdrop. Shred warnings, the confirmation sheet with its
press-and-hold button, per-file progress rings, the first-run welcome."""

import math
import os

from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from . import shred as shredmod
from . import theme as T
from .panels import icon
from .util import _, dry_run, format_items, format_size, ngettext, pretty_path

TAU = 2 * math.pi


class Sheet(Gtk.Revealer):
    """Backdrop + centred card. Add with SheetHost.show()."""

    def __init__(self, width=560, closable=True):
        super().__init__(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                         transition_duration=T.VIEW_MS)
        self.closable = closable
        self.host = None
        back = Gtk.Box()
        back.add_css_class("backdrop")
        back.set_hexpand(True)
        back.set_vexpand(True)
        self.set_child(back)
        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.card.add_css_class("sheet-card")
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.set_valign(Gtk.Align.CENTER)
        self.card.set_size_request(width, -1)
        self.card.set_hexpand(True)
        self.card.set_vexpand(True)
        self.card.set_margin_top(24)
        self.card.set_margin_bottom(24)
        back.append(self.card)
        # The backdrop covers the whole window, so clicks never reach the window
        # behind it. (No gesture here: claiming presses on the backdrop would
        # cancel them for every button inside the card too.)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def _on_key(self, ctl, keyval, code, state):
        if keyval == Gdk.KEY_Escape and self.closable:
            self.close()
            return True
        return False

    def header(self, title, body=None, icon_name=None, icon_css=None):
        box = Gtk.Box(spacing=14)
        if icon_name:
            im = icon(icon_name, 28, icon_css)
            im.set_valign(Gtk.Align.START)
            box.append(im)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        texts.set_hexpand(True)
        t = Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=50)
        t.add_css_class("sheet-title")
        texts.append(t)
        self.title_label = t
        if body:
            b = Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=60)
            b.add_css_class("sheet-body")
            texts.append(b)
            self.body_label = b
        box.append(texts)
        if self.closable:
            x = Gtk.Button.new_from_icon_name("window-close-symbolic")
            x.add_css_class("flat")
            x.add_css_class("icon-button")
            x.set_valign(Gtk.Align.START)
            x.set_tooltip_text(_("Close (Esc)"))
            x.connect("clicked", lambda *a: self.close())
            box.append(x)
        self.card.append(box)
        return box

    def buttons(self, *widgets, left=None):
        row = Gtk.Box(spacing=10)
        row.set_margin_top(4)
        if left is not None:
            row.append(left)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        for w in widgets:
            row.append(w)
        self.card.append(row)
        return row

    def close(self):
        if self.host is not None:
            self.host.dismiss(self)

    def on_shown(self):
        pass


class SheetHost:
    def __init__(self, overlay):
        self.overlay = overlay
        self.current = None

    def show(self, sheet):
        if self.current is not None:
            self._remove(self.current)
        sheet.host = self
        self.current = sheet
        self.overlay.add_overlay(sheet)
        sheet.set_reveal_child(False)
        GLib.idle_add(lambda: (sheet.set_reveal_child(True), sheet.on_shown(), False)[2])

    def dismiss(self, sheet):
        if sheet is not self.current:
            return
        self.current = None
        sheet.set_reveal_child(False)
        GLib.timeout_add(T.VIEW_MS + 20, lambda: (self._remove(sheet), False)[1])

    def _remove(self, sheet):
        if sheet.get_parent() is not None:
            self.overlay.remove_overlay(sheet)

    @property
    def busy(self):
        return self.current is not None


# ── Press-and-hold button ─────────────────────────────────────────────────
class HoldButton(Gtk.Overlay):
    """Fills left→right in `danger` over `seconds`; fires only on completion."""

    __gsignals__ = {"confirmed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, label, seconds=T.HOLD_TO_CONFIRM_S):
        super().__init__()
        self.seconds = seconds
        self.progress = 0.0
        self._holding = False
        self._t0 = None
        self._tick = 0
        self._done = False
        self.add_css_class("hold-button")
        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_cursor_from_name("pointer")
        self.area = Gtk.DrawingArea()
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        self.label = Gtk.Label(label=label)
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_valign(Gtk.Align.CENTER)
        self.add_overlay(self.label)
        self.set_size_request(260, 42)
        self.update_property([Gtk.AccessibleProperty.LABEL], [label])
        self.set_tooltip_text(_("Press and hold for %.1f seconds") % seconds)

        # Only a real release (or a cancelled sequence) ends the hold. GestureClick's
        # "stopped" fires once a press outlasts the double-click time, which is
        # exactly what a press-and-hold does, so it must not stop the fill.
        g = Gtk.GestureClick()
        g.connect("pressed", lambda *a: self._start())
        g.connect("released", lambda *a: self._stop())
        g.connect("cancel", lambda *a: self._stop())
        g.connect("unpaired-release", lambda *a: self._stop())
        self.add_controller(g)
        k = Gtk.EventControllerKey()
        k.connect("key-pressed", self._key_down)
        k.connect("key-released", self._key_up)
        self.add_controller(k)

    def set_label(self, text):
        self.label.set_label(text)
        self.update_property([Gtk.AccessibleProperty.LABEL], [text])

    def _key_down(self, ctl, keyval, code, state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if not self._holding:
                self._start()
            return True
        return False

    def _key_up(self, ctl, keyval, code, state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._stop()

    def _start(self):
        if not self.get_sensitive() or self._done:
            return
        self.grab_focus()
        self._holding = True
        self._t0 = None
        self.add_css_class("filling")
        if not self._tick:
            self._tick = self.add_tick_callback(self._on_tick)

    def _stop(self):
        if self._done:
            return
        self._holding = False

    def _on_tick(self, w, clock):
        now = clock.get_frame_time() / 1e6
        if self._holding:
            if self._t0 is None:
                self._t0 = now - self.progress * self.seconds
            self.progress = min(1.0, (now - self._t0) / self.seconds)
            if self.progress >= 1.0:
                self._done = True
                self._holding = False
                self.area.queue_draw()
                self._tick = 0
                GLib.idle_add(lambda: (self.emit("confirmed"), False)[1])
                return GLib.SOURCE_REMOVE
        else:
            # spring back quickly
            self.progress = max(0.0, self.progress - (1 / 60) / 0.25)
            if self.progress <= 0:
                self.remove_css_class("filling")
                self.area.queue_draw()
                self._tick = 0
                return GLib.SOURCE_REMOVE
        self.area.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, area, cr, w, h):
        if self.progress <= 0:
            return
        r = 9
        fw = w * self.progress
        cr.save()
        cr.new_path()
        cr.arc(r, r, r, math.pi, 1.5 * math.pi)
        cr.arc(w - r, r, r, 1.5 * math.pi, 2 * math.pi)
        cr.arc(w - r, h - r, r, 0, 0.5 * math.pi)
        cr.arc(r, h - r, r, 0.5 * math.pi, math.pi)
        cr.close_path()
        cr.clip()
        cr.rectangle(0, 0, fw, h)
        cr.set_source_rgb(*T.rgb("danger"))
        cr.fill()
        cr.restore()


# ── Pass ring ─────────────────────────────────────────────────────────────
class PassRing(Gtk.DrawingArea):
    def __init__(self, passes=3, size=30):
        super().__init__()
        self.passes = passes
        self.fractions = [0.0] * passes
        self.verify = 0.0
        self.state = "pending"     # pending | running | verifying | verified | removed | failed | partial | skipped
        self.set_content_width(size)
        self.set_content_height(size)
        self.set_draw_func(self._draw)

    def update(self, pass_no=None, fraction=None, verify=None, state=None):
        if pass_no is not None and fraction is not None and 1 <= pass_no <= self.passes:
            for i in range(pass_no - 1):
                self.fractions[i] = 1.0
            self.fractions[pass_no - 1] = fraction
        if verify is not None:
            self.verify = verify
            self.fractions = [1.0] * self.passes
        if state is not None:
            self.state = state
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        cx, cy = w / 2, h / 2
        r = min(w, h) / 2 - 3
        lw = 3.2
        gap = 0.32 if self.passes > 1 else 0
        seg = TAU / self.passes
        st = self.state
        if st in ("verified", "removed"):
            col = T.rgb("success")
        elif st == "failed":
            col = T.rgb("danger")
        elif st == "partial":
            col = T.rgb("warning")
        else:
            col = T.rgb("accent")
        cr.set_line_width(lw)
        cr.set_line_cap(1)  # round
        for i in range(self.passes):
            a0 = -math.pi / 2 + i * seg + gap / 2
            a1 = a0 + seg - gap
            cr.set_source_rgb(*T.rgb("line"))
            cr.arc(cx, cy, r, a0, a1)
            cr.stroke()
            f = 1.0 if st in ("verified", "removed", "failed") else self.fractions[i]
            if f > 0:
                cr.set_source_rgb(*col)
                cr.arc(cx, cy, r, a0, a0 + (a1 - a0) * f)
                cr.stroke()
        if st == "verifying" and self.verify > 0:
            cr.set_source_rgba(*T.rgb("success"), 0.9)
            cr.set_line_width(1.6)
            cr.arc(cx, cy, r - 4.5, -math.pi / 2, -math.pi / 2 + TAU * self.verify)
            cr.stroke()
        cr.set_line_width(2.0)
        if st == "verified":
            cr.set_source_rgb(*T.rgb("success"))
            cr.move_to(cx - 4.5, cy + 0.2)
            cr.line_to(cx - 1.2, cy + 3.5)
            cr.line_to(cx + 5, cy - 3.6)
            cr.stroke()
        elif st == "removed":
            cr.set_source_rgb(*T.rgb("success"))
            cr.arc(cx, cy, 2.2, 0, TAU)
            cr.fill()
        elif st == "failed":
            cr.set_source_rgb(*T.rgb("danger"))
            cr.move_to(cx - 3.5, cy - 3.5)
            cr.line_to(cx + 3.5, cy + 3.5)
            cr.move_to(cx + 3.5, cy - 3.5)
            cr.line_to(cx - 3.5, cy + 3.5)
            cr.stroke()
        elif st == "partial":
            cr.set_source_rgb(*T.rgb("warning"))
            cr.move_to(cx, cy - 4)
            cr.line_to(cx, cy + 1)
            cr.stroke()
            cr.arc(cx, cy + 3.8, 1.2, 0, TAU)
            cr.fill()


# ── Building blocks ───────────────────────────────────────────────────────
def amber_card(title, body, items=None, action=None):
    card = Gtk.Box(spacing=12)
    card.add_css_class("amber-card")
    im = icon("dialog-warning-symbolic", 18)
    im.set_valign(Gtk.Align.START)
    card.append(im)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    texts.set_hexpand(True)
    t = Gtk.Label(label=title, xalign=0, wrap=True, max_width_chars=56)
    t.add_css_class("amber-title")
    texts.append(t)
    b = Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=64)
    b.add_css_class("amber-body")
    texts.append(b)
    for it in (items or [])[:6]:
        l = Gtk.Label(label=it, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        l.add_css_class("amber-item")
        texts.append(l)
    if items and len(items) > 6:
        l = Gtk.Label(label=_("…and %d more") % (len(items) - 6), xalign=0)
        l.add_css_class("amber-item")
        texts.append(l)
    if action is not None:
        action.set_halign(Gtk.Align.START)
        action.set_margin_top(4)
        texts.append(action)
    card.append(texts)
    return card


def group(title=None):
    g = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    g.add_css_class("group")
    if title:
        t = Gtk.Label(label=title.upper(), xalign=0)
        t.add_css_class("group-title")
        g.append(t)
    return g


class Preflight:
    """Everything the sheets need to know, computed off the UI thread."""

    def __init__(self):
        self.items = []            # [(path, size, is_dir, node)]
        self.warnings = []         # safety.Warning_
        self.hard_links = {}       # path -> [other names]
        self.symlinks = []         # top-level symlink paths
        self.thumbnails = []
        self.recent = 0
        self.trash = []            # [(orig, payload, info)]
        self.privileged = []       # paths needing the helper
        self.blocked = {}          # path -> reason

    @property
    def total(self):
        return sum(s for _p, s, _d, _n in self.items)


# ── Warnings first ────────────────────────────────────────────────────────
class WarningsSheet(Sheet):
    def __init__(self, pre, on_continue, on_single_pass):
        super().__init__(width=560)
        self.header(_("Before you shred"),
                    _("Halo checked where these items live. Overwriting has limits on this setup — "
                      "here is what that means, in plain language."),
                    "dialog-warning-symbolic", "warning-text")
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        sc.set_max_content_height(380)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for w in pre.warnings:
            action = None
            if w.key == "ssd":
                action = Gtk.Button(label=_("Use the single random pass"))
                action.add_css_class("link-button")
                action.connect("clicked", lambda *a: (on_single_pass(), self.close(), on_continue()))
            box.append(amber_card(w.title, w.body, w.items, action))
        sc.set_child(box)
        self.card.append(sc)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *a: self.close())
        cont = Gtk.Button(label=_("Continue"))
        cont.add_css_class("suggested")
        cont.connect("clicked", lambda *a: (self.close(), on_continue()))
        self.buttons(cancel, cont)
        self._focus = cont

    def on_shown(self):
        self._focus.grab_focus()


# ── Confirmation sheet ────────────────────────────────────────────────────
class ShredConfirmSheet(Sheet):
    def __init__(self, pre, settings, on_confirm, title=None, subtitle=None, show_traces=True):
        super().__init__(width=600)
        self.pre = pre
        self.settings = settings
        self.on_confirm = on_confirm
        n = len(pre.items)
        self.header(title or (ngettext("Shred %d item?", "Shred %d items?", n) % n),
                    subtitle or _("Halo overwrites each file, checks the result, then removes it along "
                                  "with the traces your desktop keeps. This can't be undone."),
                    "halo-shred-symbolic", "danger-text")
        if dry_run():
            b = Gtk.Label(label=_("DRY RUN · HALO_DRY_RUN=1 — nothing will be written, renamed or deleted"),
                          xalign=0)
            b.add_css_class("badge")
            b.add_css_class("dry")
            b.set_halign(Gtk.Align.START)
            self.card.append(b)

        outer = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        outer.set_max_content_height(470)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_child(body)
        self.card.append(outer)

        # items
        g = group(_("Items"))
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for path, size, is_dir, node in pre.items[:40]:
            row = Gtk.Box(spacing=10)
            row.add_css_class("item-line")
            row.append(icon("folder-symbolic" if is_dir else "text-x-generic-symbolic", 16, "dim-label"))
            name = Gtk.Label(label=os.path.basename(path) or path, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            name.set_hexpand(True)
            name.set_tooltip_text(pretty_path(path))
            row.append(name)
            sz = Gtk.Label(label=format_size(size))
            sz.add_css_class("size")
            sz.add_css_class("tnum")
            row.append(sz)
            lst.append(row)
        if len(pre.items) > 40:
            more = Gtk.Label(label=_("…and %d more") % (len(pre.items) - 40), xalign=0)
            more.add_css_class("caption")
            lst.append(more)
        if len(pre.items) > 6:
            isc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
            isc.set_min_content_height(150)
            isc.set_child(lst)
            g.append(isc)
        else:
            g.append(lst)
        g.append(Gtk.Separator())
        tot = Gtk.Box()
        tot.add_css_class("total-line")
        tl = Gtk.Label(label=_("Total"), xalign=0)
        tl.set_hexpand(True)
        tot.append(tl)
        tv = Gtk.Label(label="%s · %s" % (format_items(n), format_size(pre.total)))
        tv.add_css_class("tnum")
        tot.append(tv)
        g.append(tot)
        body.append(g)

        # per-item notes
        if pre.hard_links:
            items = []
            for p, others in pre.hard_links.items():
                items.append(pretty_path(p))
                items += ["  = " + pretty_path(o) for o in others[:3]]
            body.append(amber_card(_("Other names for the same data exist"),
                                   _("These files are hard-linked. Overwriting changes the data under every "
                                     "name; the other names below will remain as empty-looking files only "
                                     "if removed separately."), items))
        if pre.symlinks:
            self.remove_links = Gtk.CheckButton(label=_("Remove just the links (targets are never touched)"))
            body.append(amber_card(_("Symbolic links are never followed"),
                                   _("Halo won't overwrite the file a link points to."),
                                   [pretty_path(p) for p in pre.symlinks], self.remove_links))
        else:
            self.remove_links = None
        if pre.privileged:
            body.append(amber_card(_("Administrator password needed"),
                                   _("Some items belong to another user. Halo will ask for your password "
                                     "once, just for those."), [pretty_path(p) for p in pre.privileged]))

        # method
        g = group(_("Method"))
        self.r_dod = Gtk.CheckButton(label=_("DoD 5220.22-M 3-pass method"))
        self.r_rand = Gtk.CheckButton(label=_("Single random pass"))
        self.r_rand.set_group(self.r_dod)
        (self.r_dod if settings["shred_method"] == "dod3" else self.r_rand).set_active(True)
        g.append(self._with_sub(self.r_dod, _("Zeros, then ones, then random data. The classic choice.")))
        g.append(self._with_sub(self.r_rand, _("Faster, and the recommended choice on SSDs.")))
        self.verify = Gtk.Switch(active=settings["verify"])
        g.append(self._switch_row(_("Verify final pass"), _("Read every block back and compare"), self.verify))
        body.append(g)

        # traces
        self.c_thumbs = self.c_recent = self.c_trash = None
        if show_traces:
            g = group(_("Also remove"))
            self.c_thumbs = Gtk.CheckButton(label=_("Cached thumbnails") + self._count(len(pre.thumbnails)))
            self.c_thumbs.set_active(settings["trace_thumbnails"])
            self.c_recent = Gtk.CheckButton(label=_("Recent-file entries") + self._count(pre.recent))
            self.c_recent.set_active(settings["trace_recent"])
            self.c_trash = Gtk.CheckButton(label=_("Copies in the Trash") + self._count(len(pre.trash)))
            self.c_trash.set_active(settings["trace_trash"])
            for c in (self.c_thumbs, self.c_recent, self.c_trash):
                g.append(c)
            body.append(g)

        # report
        g = group(_("Report"))
        self.c_report = Gtk.CheckButton(label=_("Save a shred report"))
        self.c_report.set_active(settings["reports"])
        self.c_names = Gtk.CheckButton(label=_("Include file names (otherwise stored as hashes)"))
        self.c_names.set_active(settings["report_names"])
        self.c_names.set_margin_start(26)
        self.c_report.connect("toggled", lambda b: self.c_names.set_sensitive(b.get_active()))
        self.c_names.set_sensitive(self.c_report.get_active())
        g.append(self.c_report)
        g.append(self.c_names)
        body.append(g)

        # first-time acknowledgement
        self.ack = None
        if not settings["understood_shred"]:
            self.ack = Gtk.CheckButton(label=_("I understand shredded files cannot be recovered"))
            self.ack.add_css_class("danger-check")
            self.ack.connect("toggled", lambda *a: self._update())
            self.ack.set_margin_top(2)
            self.card.append(self.ack)

        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda *a: self.close())
        self.hold = HoldButton(_("Hold to shred %s (%s)") % (format_items(n), format_size(pre.total)),
                               settings["hold_seconds"])
        self.hold.connect("confirmed", self._confirmed)
        self.buttons(cancel, self.hold)
        self._cancel = cancel
        self._update()

    def on_shown(self):
        self._cancel.grab_focus()

    def _count(self, n):
        return "  (%s)" % (_("none found") if n == 0 else ngettext("%d found", "%d found", n) % n)

    def _with_sub(self, w, sub):
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        b.append(w)
        s = Gtk.Label(label=sub, xalign=0, wrap=True, max_width_chars=60)
        s.add_css_class("caption")
        s.set_margin_start(26)
        b.append(s)
        return b

    def _switch_row(self, title, sub, sw):
        row = Gtk.Box(spacing=12)
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        t.set_hexpand(True)
        a = Gtk.Label(label=title, xalign=0)
        b = Gtk.Label(label=sub, xalign=0)
        b.add_css_class("caption")
        t.append(a)
        t.append(b)
        row.append(t)
        sw.set_valign(Gtk.Align.CENTER)
        row.append(sw)
        return row

    def _update(self):
        ok = self.ack is None or self.ack.get_active()
        self.hold.set_sensitive(ok)

    def options(self):
        return {
            "method": "dod3" if self.r_dod.get_active() else "random1",
            "verify": self.verify.get_active(),
            "thumbnails": bool(self.c_thumbs and self.c_thumbs.get_active()),
            "recent": bool(self.c_recent and self.c_recent.get_active()),
            "trash": bool(self.c_trash and self.c_trash.get_active()),
            "report": self.c_report.get_active(),
            "names": self.c_names.get_active(),
            "remove_links": bool(self.remove_links and self.remove_links.get_active()),
        }

    def _confirmed(self, *a):
        if self.ack is not None:
            self.settings["understood_shred"] = True
        opts = self.options()
        self.closable = False
        self.on_confirm(opts)


# ── Progress sheet ────────────────────────────────────────────────────────
class ProgressRow(Gtk.Box):
    def __init__(self, path, size, passes, is_dir):
        super().__init__(spacing=12)
        self.add_css_class("progress-row")
        self.ring = PassRing(passes if not is_dir else 1)
        self.ring.set_valign(Gtk.Align.CENTER)
        self.append(self.ring)
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        t.set_hexpand(True)
        self.name = Gtk.Label(label=os.path.basename(path) or path, xalign=0,
                              ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.name.add_css_class("row-name")
        self.sub = Gtk.Label(label=format_size(size) if not is_dir else _("Folder"), xalign=0)
        self.sub.add_css_class("row-sub")
        t.append(self.name)
        t.append(self.sub)
        self.append(t)
        self.status = Gtk.Label(label=_("Waiting"), xalign=1)
        self.status.add_css_class("row-sub")
        self.append(self.status)
        self.size = size

    def set_status(self, text, css=None):
        self.status.set_label(text)
        for c in ("status-ok", "status-bad", "status-warn", "row-sub"):
            self.status.remove_css_class(c)
        self.status.add_css_class(css or "row-sub")


class ShredProgressSheet(Sheet):
    def __init__(self, plan, passes, method_label, on_cancel):
        super().__init__(width=600, closable=False)
        self.on_cancel = on_cancel
        self.header(_("Shredding…"), method_label + (" · " + _("dry run") if dry_run() else ""),
                    "halo-shred-symbolic", "danger-text")
        self.overall = Gtk.ProgressBar()
        self.overall.add_css_class("thin")
        self.overall.add_css_class("danger")
        self.card.append(self.overall)
        self.counter = Gtk.Label(label="", xalign=0)
        self.counter.add_css_class("caption")
        self.counter.add_css_class("tnum")
        self.card.append(self.counter)
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_min_content_height(min(320, 52 * max(1, len(plan))))
        sc.set_max_content_height(320)
        sc.set_propagate_natural_height(True)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        sc.set_child(self.list)
        self.scroller = sc
        self.card.append(sc)
        self.rows = []
        self.total_bytes = max(1, sum(s for _p, s, d in plan if not d))
        self.done_bytes = 0
        for p, s, d in plan:
            r = ProgressRow(p, s, passes, d)
            self.rows.append(r)
            self.list.append(r)
        self.summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.card.append(self.summary)
        self.cancel_btn = Gtk.Button(label=_("Cancel"))
        self.cancel_btn.connect("clicked", self._cancel)
        self.close_btn = Gtk.Button(label=_("Close"))
        self.close_btn.add_css_class("suggested")
        self.close_btn.connect("clicked", lambda *a: self.close())
        self.close_btn.set_visible(False)
        self.open_btn = Gtk.Button(label=_("Open report folder"))
        self.open_btn.set_visible(False)
        self.buttons(self.cancel_btn, self.close_btn, left=self.open_btn)
        self.passes = passes
        self.current = None

    def _cancel(self, *a):
        self.cancel_btn.set_sensitive(False)
        self.cancel_btn.set_label(_("Stopping after this chunk…"))
        self.on_cancel()

    def handle(self, ev, offset=0):
        t = ev.get("type")
        i = ev.get("index", 0) + offset
        if i >= len(self.rows) and t != "done":
            return
        if t == "file_start":
            r = self.rows[i]
            self.current = r
            r.ring.update(state="running")
            r.set_status(_("Pass 1 of %d") % self.passes if not ev.get("is_dir") else _("Removing"))
            self._scroll_to(r)
        elif t == "pass":
            r = self.rows[i]
            r.ring.update(ev["pass"], ev["fraction"], state="running")
            r.set_status(_("Pass %d of %d") % (ev["pass"], ev["passes"]))
            done = self.done_bytes + r.size * ((ev["pass"] - 1 + ev["fraction"]) / (ev["passes"] + 1))
            self.overall.set_fraction(min(1.0, done / self.total_bytes))
        elif t == "verify":
            r = self.rows[i]
            r.ring.update(verify=ev["fraction"], state="verifying")
            r.set_status(_("Verifying"))
        elif t == "file_done":
            r = self.rows[i]
            st = ev["status"]
            ring_state = {"verified": "verified", "removed": "removed", "link_removed": "removed",
                          "failed": "failed", "partial": "partial", "refused": "failed",
                          "skipped": "skipped"}.get(st, "failed")
            r.ring.update(state=ring_state)
            css = {"verified": "status-ok", "removed": "status-ok", "link_removed": "status-ok",
                   "partial": "status-warn", "skipped": "row-sub"}.get(st, "status-bad")
            r.set_status(_(ev.get("message") or shredmod.STATUS_TEXT.get(st, st)), css)
            self.done_bytes += r.size
            self.overall.set_fraction(min(1.0, self.done_bytes / self.total_bytes))
            ndone = sum(1 for x in self.rows if x.ring.state not in ("pending", "running", "verifying"))
            self.counter.set_label(_("%d of %d done") % (ndone, len(self.rows)))

    def _scroll_to(self, row):
        adj = self.scroller.get_vadjustment()
        ok, rect = row.compute_bounds(self.list)
        if ok:
            target = rect.get_y() - adj.get_page_size() / 2
            adj.set_value(max(0, min(target, adj.get_upper() - adj.get_page_size())))

    def finish(self, results, trace_summary, report_path, on_open_reports):
        good = sum(1 for r in results if r["status"] in ("verified", "removed", "link_removed"))
        verified = sum(1 for r in results if r["status"] == "verified")
        bad = [r for r in results if r["status"] in ("failed", "refused")]
        partial = [r for r in results if r["status"] == "partial"]
        skipped = [r for r in results if r["status"] == "skipped"]
        if bad or partial:
            title = _("Finished with problems")
        elif skipped:
            title = _("Stopped")
        else:
            title = _("Shredded") if not dry_run() else _("Dry run finished")
        self.title_label.set_label(title)
        self.overall.set_fraction(1.0 if not (partial or skipped) else self.overall.get_fraction())
        self.overall.remove_css_class("danger")
        self.overall.add_css_class("success" if not (bad or partial) else "danger")

        def line(text, css):
            l = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=64)
            l.add_css_class(css)
            self.summary.append(l)
        line(ngettext("%d item removed", "%d items removed", good) % good
             + (" · " + ngettext("%d verified", "%d verified", verified) % verified if verified else ""),
             "success-text" if good else "caption")
        if partial:
            line(ngettext("%d file partly overwritten, not removed", "%d files partly overwritten, not removed",
                          len(partial)) % len(partial), "warning-text")
        if bad:
            line(ngettext("%d item failed and was kept", "%d items failed and were kept", len(bad)) % len(bad),
                 "danger-text")
        if skipped:
            line(ngettext("%d item not started", "%d items not started", len(skipped)) % len(skipped), "caption")
        if trace_summary:
            line(trace_summary, "caption")
        if report_path:
            line(_("Report saved to %s") % pretty_path(report_path), "caption")
            self.open_btn.set_visible(True)
            self.open_btn.connect("clicked", lambda *a: on_open_reports())
        self.cancel_btn.set_visible(False)
        self.close_btn.set_visible(True)
        self.close_btn.grab_focus()
        self.closable = True


# ── Busy sheet ────────────────────────────────────────────────────────────
class BusySheet(Sheet):
    def __init__(self, text):
        super().__init__(width=380, closable=False)
        row = Gtk.Box(spacing=14)
        sp = Gtk.Spinner()
        sp.start()
        sp.set_size_request(22, 22)
        row.append(sp)
        l = Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=40)
        l.add_css_class("sheet-body")
        row.append(l)
        self.card.append(row)


class MessageSheet(Sheet):
    def __init__(self, title, body, icon_name="dialog-information-symbolic", extra=None, buttons=None):
        super().__init__(width=480)
        self.header(title, body, icon_name)
        if extra is not None:
            self.card.append(extra)
        btns = buttons or []
        if not btns:
            ok = Gtk.Button(label=_("OK"))
            ok.add_css_class("suggested")
            ok.connect("clicked", lambda *a: self.close())
            btns = [ok]
        self.buttons(*btns)
        self._focus = btns[-1]

    def on_shown(self):
        self._focus.grab_focus()


# ── First run ─────────────────────────────────────────────────────────────
class WelcomeSheet(Sheet):
    def __init__(self, ssd, on_pick, on_home, on_doc):
        super().__init__(width=720)
        top = Gtk.Box(spacing=16)
        logo = Gtk.Image.new_from_icon_name("io.github.RiyaKaushik10.Halo")
        logo.set_pixel_size(64)
        top.append(logo)
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        t.set_valign(Gtk.Align.CENTER)
        a = Gtk.Label(label=_("Welcome to Halo"), xalign=0)
        a.add_css_class("sheet-title")
        b = Gtk.Label(label=_("See where your space went in seconds, then make sensitive files truly gone."),
                      xalign=0, wrap=True, max_width_chars=70)
        b.add_css_class("sheet-body")
        t.append(a)
        t.append(b)
        top.append(t)
        self.card.append(top)

        cards = Gtk.Box(spacing=12, homogeneous=True)
        for ic, title, body in (
            ("drive-harddisk-symbolic", _("See your space"),
             _("Pick a drive or folder to scan. A sunburst shows what's filling it, biggest first.")),
            ("halo-shred-symbolic", _("Shred for real"),
             _("Shredding overwrites a file, checks the result and removes its thumbnails and recent "
               "entries too. It can't be undone.")),
            ("security-high-symbolic", _("Private by design"),
             _("Nothing leaves this computer. No account, no tracking, no internet.")),
        ):
            c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            c.add_css_class("info-card")
            c.append(icon(ic, 24, "info-icon"))
            l1 = Gtk.Label(label=title, xalign=0)
            l1.add_css_class("info-title")
            l2 = Gtk.Label(label=body, xalign=0, wrap=True, max_width_chars=24)
            l2.add_css_class("info-body")
            l2.set_vexpand(True)
            l2.set_valign(Gtk.Align.START)
            c.append(l1)
            c.append(l2)
            cards.append(c)
        self.card.append(cards)
        if ssd:
            self.card.append(amber_card(
                _("Your system drive is an SSD"),
                _("On solid-state drives, overwriting can't guarantee old data is gone, because the drive "
                  "moves data around internally. Halo will remind you and suggest the faster single pass. "
                  "Full-disk encryption is the real protection.")))

        links = Gtk.Box(spacing=6)
        for label, key in ((_("Privacy Policy"), "privacy"), (_("Terms of Service"), "terms")):
            b = Gtk.Button(label=label)
            b.add_css_class("link-button")
            b.connect("clicked", lambda w, k=key: on_doc(k))
            links.append(b)
        home = Gtk.Button(label=_("Scan my Home folder"))
        home.connect("clicked", lambda *a: (self.close(), on_home()))
        pick = Gtk.Button(label=_("Pick a drive or folder"))
        pick.add_css_class("suggested")
        pick.connect("clicked", lambda *a: (self.close(), on_pick()))
        self.buttons(home, pick, left=links)
        self._focus = pick

    def on_shown(self):
        self._focus.grab_focus()
