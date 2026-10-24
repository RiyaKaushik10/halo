# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Gtk.Application, the three-pane window and every window action."""

import csv
import io
import json
import logging
import os
import signal
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import fileops, menus, paths, report, safety, scanner, sheets, storage, traces  # noqa: E402
from . import shred as shredmod  # noqa: E402
from . import theme as T  # noqa: E402
from .panels import RightPanel, Sidebar, icon  # noqa: E402
from .selection import Selection  # noqa: E402
from .sunburst import Sunburst  # noqa: E402
from .util import (_, display_name, dry_run, format_count, format_duration, format_items,  # noqa: E402
                   format_size, home, ngettext, pretty_path, time_ago)

log = logging.getLogger("halo")


class Item:
    """One thing an action works on."""
    __slots__ = ("path", "size", "is_dir", "node")

    def __init__(self, path, size, is_dir, node=None):
        self.path, self.size, self.is_dir, self.node = path, size, is_dir, node


def _quick_size(path):
    try:
        st = os.lstat(path)
    except OSError:
        return 0, False
    if os.path.isdir(path) and not os.path.islink(path):
        total = 0
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_blocks * 512
                except OSError:
                    pass
        return total, True
    return st.st_blocks * 512, False


class HaloWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Halo")
        self.app = app
        self.settings = app.settings
        self.add_css_class("halo")
        self.set_icon_name(paths.APP_ID)
        self.set_default_size(max(T.WINDOW_MIN[0], self.settings["window_width"]),
                              max(T.WINDOW_MIN[1], self.settings["window_height"]))
        self.set_size_request(*T.WINDOW_MIN)
        if self.settings["window_maximized"]:
            self.maximize()

        self.selection = Selection()
        self.result = None
        self.scanner = None
        self.file_nodes = None
        self._scan_started = 0
        self._last_list_refresh = 0
        self._elapsed_id = 0
        self._shred_cancel = None
        self._helper_proc = None
        self._prefer_single = False
        self._collapsed = None

        # header bar
        hb = Gtk.HeaderBar()
        hb.add_css_class("halo-header")
        hb.set_title_widget(Gtk.Box())
        brand = Gtk.Box(spacing=10)
        logo = Gtk.Image.new_from_icon_name(paths.APP_ID)
        logo.set_pixel_size(24)
        brand.append(logo)
        name = Gtk.Label(label="Halo")
        name.add_css_class("app-name")
        brand.append(name)
        hb.pack_start(brand)
        if dry_run():
            b = Gtk.Label(label=_("DRY RUN"))
            b.add_css_class("badge")
            b.add_css_class("dry")
            b.set_valign(Gtk.Align.CENTER)
            b.set_tooltip_text(_("HALO_DRY_RUN=1: nothing is written, renamed or deleted"))
            hb.pack_start(b)
        self.set_titlebar(hb)

        self.overlay = Gtk.Overlay()
        self.set_child(self.overlay)
        self.host = sheets.SheetHost(self.overlay)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.overlay.set_child(outer)

        self.drive_menu = Gio.Menu()
        self.menubar = Gtk.PopoverMenuBar.new_from_model(menus.build_menubar(self.drive_menu))
        self.menubar.add_css_class("halo-menubar")
        outer.append(self.menubar)

        panes = Gtk.Box()
        panes.set_vexpand(True)
        outer.append(panes)

        self.sidebar = Sidebar(self.settings)
        self.sidebar.connect("scan-requested", lambda s, p: self.scan(p))
        panes.append(self.sidebar)

        panes.append(self._build_centre())

        self.panel = RightPanel(self.selection, self.settings)
        self.panel.connect("context-menu", self._on_list_context)
        self.panel.connect("activate-node", self._on_list_activate)
        self.panel.connect("dropped", self._on_dropped)
        panes.append(self.panel)

        self.toast = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                  transition_duration=T.VIEW_MS)
        self.toast.set_halign(Gtk.Align.CENTER)
        self.toast.set_valign(Gtk.Align.END)
        self.toast.set_can_target(True)
        tb = Gtk.Box(spacing=12)
        tb.add_css_class("toast")
        self.toast_label = Gtk.Label(label="")
        tb.append(self.toast_label)
        self.toast_bar = Gtk.ProgressBar()
        self.toast_bar.add_css_class("thin")
        self.toast_bar.set_size_request(120, -1)
        self.toast_bar.set_valign(Gtk.Align.CENTER)
        tb.append(self.toast_bar)
        self.toast.set_child(tb)
        self.overlay.add_overlay(self.toast)
        self._toast_id = 0

        self._setup_actions()
        self.selection.connect("changed", self._on_selection_changed)
        self.settings.connect(self._on_setting)
        self._apply_visibility()
        self._refresh_drive_menu()
        self.sidebar._mon.connect("mount-added", lambda *a: self._refresh_drive_menu())
        self.sidebar._mon.connect("mount-removed", lambda *a: self._refresh_drive_menu())

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        keys.connect("key-pressed", self._on_window_key)
        self.add_controller(keys)
        self.connect("close-request", self._on_close)
        self._update_actions()
        self._show_empty(True)

    # ── layout ────────────────────────────────────────────────────────────
    def _build_centre(self):
        centre = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        centre.add_css_class("centre")
        centre.set_hexpand(True)

        top = Gtk.Box(spacing=12)
        top.add_css_class("centre-top")
        self.crumbs = Gtk.Box(spacing=0)
        self.crumbs.add_css_class("breadcrumb")
        self.crumbs.set_hexpand(True)
        top.append(self.crumbs)
        seg = Gtk.Box()
        seg.add_css_class("segmented")
        self.color_buttons = {}
        first = None
        for key, label in (("kind", _("File kind")), ("folder", _("Top folder"))):
            b = Gtk.ToggleButton(label=label)
            if first is None:
                first = b
            else:
                b.set_group(first)
            b.set_action_name("win.color-mode")
            b.set_action_target_value(GLib.Variant.new_string(key))
            seg.append(b)
            self.color_buttons[key] = b
        cl = Gtk.Label(label=_("Colour by"))
        cl.add_css_class("caption")
        self.colour_by_label = cl
        top.append(cl)
        top.append(seg)
        centre.append(top)

        self.banners = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        centre.append(self.banners)

        chart_overlay = Gtk.Overlay()
        chart_overlay.set_vexpand(True)
        self.chart = Sunburst(self.selection)
        self.chart.set_options(rings=self.settings["rings"], show_hidden=self.settings["show_hidden"],
                               color_mode=self.settings["color_mode"],
                               reduce_motion=self.settings["reduce_motion"])
        self.chart.connect("center-changed", self._on_center_changed)
        self.chart.connect("context-menu", self._on_chart_context)
        self.chart.connect("special-activated", self._on_special)
        chart_overlay.set_child(self.chart)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.add_css_class("empty-state")
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_valign(Gtk.Align.END)
        empty.set_margin_bottom(40)
        et = Gtk.Label(label=_("See where your space went"))
        et.add_css_class("empty-title")
        eb = Gtk.Label(label=_("Choose a drive or folder on the left, or scan your Home folder now."),
                       wrap=True, justify=Gtk.Justification.CENTER)
        eb.add_css_class("empty-body")
        ebtns = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        b1 = Gtk.Button(label=_("Scan Home"))
        b1.add_css_class("suggested")
        b1.connect("clicked", lambda *a: self.scan(home()))
        b2 = Gtk.Button(label=_("Scan Folder…"))
        b2.set_action_name("win.scan-folder")
        ebtns.append(b2)
        ebtns.append(b1)
        empty.append(et)
        empty.append(eb)
        empty.append(ebtns)
        empty.set_can_target(True)
        self.empty_state = empty
        chart_overlay.add_overlay(empty)
        centre.append(chart_overlay)

        # legend
        self.legend = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=7,
                                  min_children_per_line=2, column_spacing=2, row_spacing=2)
        self.legend.add_css_class("legend")
        self.legend.set_halign(Gtk.Align.CENTER)
        self._build_legend()
        centre.append(self.legend)

        foot = Gtk.Box(spacing=12)
        foot.add_css_class("centre-foot")
        self.foot_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                                    transition_duration=T.VIEW_MS)
        self.foot_stack.set_hexpand(True)
        hint = Gtk.Box(spacing=8)
        self.hint_label = Gtk.Label(label=_("Click selects · double-click zooms · right-click shreds"), xalign=0,
                                    ellipsize=Pango.EllipsizeMode.END)
        self.hint_label.set_hexpand(True)
        hint.append(self.hint_label)
        self.mode_label = Gtk.Label(label="", xalign=1, ellipsize=Pango.EllipsizeMode.START)
        hint.append(self.mode_label)
        self.foot_stack.add_named(hint, "hint")

        counters = Gtk.Box(spacing=18)
        counters.add_css_class("counters")
        dot = Gtk.Box()
        dot.add_css_class("pulse-dot")
        dot.set_valign(Gtk.Align.CENTER)
        counters.append(dot)
        self.c_files = self._counter(counters, _("files scanned"))
        self.c_size = self._counter(counters, _("found"))
        self.c_time = self._counter(counters, _("elapsed"))
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        counters.append(spacer)
        stop = Gtk.Button(label=_("Stop"))
        stop.set_action_name("win.stop-scan")
        counters.append(stop)
        self.foot_stack.add_named(counters, "scan")
        foot.append(self.foot_stack)
        centre.append(foot)
        self._update_mode_label()
        return centre

    def _counter(self, box, caption):
        b = Gtk.Box(spacing=6)
        v = Gtk.Label(label="0")
        v.add_css_class("counter-value")
        c = Gtk.Label(label=caption, ellipsize=Pango.EllipsizeMode.END)
        c.add_css_class("counter")
        b.append(v)
        b.append(c)
        box.append(b)
        return v

    def _build_legend(self):
        c = self.legend.get_first_child()
        while c is not None:
            n = c.get_next_sibling()
            self.legend.remove(c)
            c = n
        if self.settings["color_mode"] == "kind":
            for k, name in enumerate(T.KIND_NAMES):
                chip = Gtk.Box(spacing=6)
                chip.add_css_class("legend-chip")
                chip.append(icon(T.KIND_ICONS[k], 13, "kind-%d" % k))
                chip.append(Gtk.Label(label=_(name)))
                self.legend.append(chip)
        else:
            center = self.chart.center
            if center is not None:
                for i, k in enumerate(center.sorted_children(self.settings["show_hidden"])[:7]):
                    chip = Gtk.Box(spacing=6)
                    chip.add_css_class("legend-chip")
                    dot = Gtk.DrawingArea()
                    dot.set_content_width(10)
                    dot.set_content_height(10)
                    col = T.hex_to_rgb(T.TOP_FOLDER_COLORS[i % len(T.TOP_FOLDER_COLORS)])
                    dot.set_draw_func(lambda a, cr, w, h, c=col: (cr.set_source_rgb(*c),
                                                                  cr.arc(w / 2, h / 2, 4.5, 0, 6.3), cr.fill()))
                    dot.set_valign(Gtk.Align.CENTER)
                    chip.append(dot)
                    chip.append(Gtk.Label(label=k.label(), ellipsize=Pango.EllipsizeMode.END,
                                          max_width_chars=14))
                    self.legend.append(chip)

    def _update_mode_label(self):
        m = self.settings["size_mode"]
        self.mode_label.set_label(_("Measuring disk usage") if m == "disk" else _("Measuring apparent size"))
        self.mode_label.set_tooltip_text(_("Disk usage counts the blocks a file really occupies (like du). "
                                           "Apparent size is the length of each file."))

    def _show_empty(self, on):
        self.empty_state.set_visible(on)
        self.legend.set_visible(not on)
        self.foot_stack.set_visible(not on)

    # ── responsive collapse ───────────────────────────────────────────────
    def do_size_allocate(self, w, h, baseline):
        Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        narrow = w < T.COLLAPSE_BELOW
        if narrow != self._collapsed:
            self._collapsed = narrow
            GLib.idle_add(self._apply_collapse, narrow)

    def _apply_collapse(self, narrow):
        self.sidebar.set_rail(narrow)
        self.colour_by_label.set_visible(not narrow)
        self.panel.set_size_request(T.PANEL_NARROW_WIDTH if narrow else T.PANEL_WIDTH, -1)
        return False

    def _apply_visibility(self):
        self.sidebar.set_visible(self.settings["show_sidebar"])
        self.panel.set_visible(self.settings["show_panel"])

    # ── actions ───────────────────────────────────────────────────────────
    def _add(self, name, cb, param=None, state=None):
        if state is not None:
            a = Gio.SimpleAction.new_stateful(name, param, state)
            a.connect("change-state", cb)
        else:
            a = Gio.SimpleAction.new(name, param)
            a.connect("activate", cb)
        self.add_action(a)
        return a

    def _setup_actions(self):
        s = GLib.VariantType.new("s")
        self._add("scan-folder", lambda *a: self.pick_folder_and_scan())
        self._add("scan-path", lambda a, p: self.scan(p.get_string()), s)
        self._add("rescan", lambda *a: self.rescan())
        self._add("stop-scan", lambda *a: self.stop_scan())
        self._add("export", lambda *a: self.export_summary())
        self._add("select-all", lambda *a: self.chart.select_all())
        self._add("invert-selection", lambda *a: self.chart.invert_selection())
        self._add("clear-selection", lambda *a: self.selection.clear())
        self._add("copy-path", lambda *a: self.copy_paths())
        self._add("trash", lambda *a: self.trash_selection())
        self._add("shred", lambda *a: self.shred_selection())
        self._add("move-to", lambda *a: self.transfer_selection(True))
        self._add("copy-to", lambda *a: self.transfer_selection(False))
        self._add("show-in-files", lambda *a: self.show_in_files())
        self._add("zoom-selected", lambda *a: self.zoom_selected())
        self._add("show-in-list", lambda *a: self.show_smaller_in_list())
        self._add("scan-other", lambda *a: self.scan_other())
        self._add("color-mode", self._on_color_mode, s, GLib.Variant.new_string(self.settings["color_mode"]))
        self._add("size-mode", self._on_size_mode, s, GLib.Variant.new_string(self.settings["size_mode"]))
        self._add("show-hidden", self._on_toggle_setting("show_hidden"), None,
                  GLib.Variant.new_boolean(self.settings["show_hidden"]))
        self._add("zoom-in", lambda *a: self.zoom_selected())
        self._add("go-up", lambda *a: self.chart.go_up())
        self._add("go-root", lambda *a: self.chart.go_root())
        self._add("show-sidebar", self._on_toggle_setting("show_sidebar"), None,
                  GLib.Variant.new_boolean(self.settings["show_sidebar"]))
        self._add("show-panel", self._on_toggle_setting("show_panel"), None,
                  GLib.Variant.new_boolean(self.settings["show_panel"]))
        self._add("fullscreen", self._on_fullscreen, None, GLib.Variant.new_boolean(False))
        self._add("clean-thumbnails", lambda *a: self.tool_clean_thumbnails())
        self._add("clear-recent", lambda *a: self.tool_clear_recent())
        self._add("shred-trash", lambda *a: self.tool_shred_trash())
        self._add("open-reports", lambda *a: self.open_reports())
        self._add("clear-own-data", lambda *a: self.tool_clear_own_data())
        self._add("admin-rescan", lambda *a: self.admin_rescan())
        self._add("show-errors", lambda *a: self.show_error_list())
        self._add("disabled-reason", lambda *a: None).set_enabled(False)

    def _on_toggle_setting(self, key):
        def cb(action, value):
            action.set_state(value)
            self.settings[key] = value.get_boolean()
        return cb

    def _on_color_mode(self, action, value):
        action.set_state(value)
        self.settings["color_mode"] = value.get_string()

    def _on_size_mode(self, action, value):
        action.set_state(value)
        if self.settings["size_mode"] != value.get_string():
            self.settings["size_mode"] = value.get_string()
            if self.result is not None:
                self.scan(self.result.root_path)

    def _on_fullscreen(self, action, value):
        action.set_state(value)
        self.fullscreen() if value.get_boolean() else self.unfullscreen()

    def _on_setting(self, key, value):
        if key == "color_mode":
            self.chart.set_options(color_mode=value)
            self.panel.color_mode = value
            self.panel.rebind_rows()
            self._build_legend()
            a = self.lookup_action("color-mode")
            if a.get_state().get_string() != value:
                a.set_state(GLib.Variant.new_string(value))
        elif key == "show_hidden":
            self.chart.set_options(show_hidden=value)
            self.panel.show_hidden = value
            self.panel.refresh_lists()
            a = self.lookup_action("show-hidden")
            if a.get_state().get_boolean() != value:
                a.set_state(GLib.Variant.new_boolean(value))
        elif key == "rings":
            self.chart.set_options(rings=value)
        elif key == "reduce_motion":
            self.chart.set_options(reduce_motion=value)
        elif key == "size_mode":
            self._update_mode_label()
            self.panel.size_mode = value
            a = self.lookup_action("size-mode")
            if a.get_state().get_string() != value:
                a.set_state(GLib.Variant.new_string(value))
        elif key in ("show_sidebar", "show_panel"):
            self._apply_visibility()
        elif key == "favourites":
            self.sidebar.refresh_favourites()

    def _update_actions(self):
        sel = self.selection.top_level()
        has = bool(sel)
        trash_ok = has and all(self._trash_reason(n) is None for n in sel)
        shred_ok = has and all(self._shred_reason(n) is None for n in sel)
        real = has and all(not (n.flags & scanner.OTHER_DRIVE) for n in sel)
        for name, ok in (("trash", trash_ok), ("shred", shred_ok), ("copy-path", real),
                         ("show-in-files", real), ("move-to", trash_ok), ("copy-to", real)):
            self.lookup_action(name).set_enabled(ok)
        zoomable = len(sel) == 1 and sel[0].flags & scanner.DIR and bool(sel[0].children)
        self.lookup_action("zoom-selected").set_enabled(bool(zoomable))
        self.lookup_action("zoom-in").set_enabled(bool(zoomable))
        scanning = self.scanner is not None and self.scanner.running
        self.lookup_action("stop-scan").set_enabled(scanning)
        self.lookup_action("rescan").set_enabled(self.result is not None)
        self.lookup_action("export").set_enabled(self.result is not None)
        self.lookup_action("select-all").set_enabled(self.chart.center is not None)
        self.lookup_action("invert-selection").set_enabled(self.chart.center is not None)
        at_root = self.chart.center is None or self.chart.center.parent is None
        self.lookup_action("go-up").set_enabled(not at_root)
        self.lookup_action("go-root").set_enabled(not at_root)

    def _node_path_for_check(self, n):
        if n.flags & scanner.FILES:
            return os.path.join(n.path(), "x")
        return n.path()

    def _trash_reason(self, n):
        if n.flags & scanner.OTHER_DRIVE:
            return _("This is another drive")
        return safety.trash_block_reason(self._node_path_for_check(n), self._mounts())

    def _shred_reason(self, n):
        if n.flags & scanner.OTHER_DRIVE:
            return _("This is another drive")
        if n.flags & scanner.FILES:
            return safety.protection_reason(os.path.join(n.path(), "x"), self._mounts()) or \
                (safety.shred_block_reason(n.path(), self._mounts())
                 if safety.fs_info(n.path(), self._mounts()).network else None)
        return safety.shred_block_reason(n.path(), self._mounts())

    def _mounts(self):
        t = time.monotonic()
        if getattr(self, "_mounts_t", 0) < t - 5:
            self._mounts_cache = safety.read_mounts()
            self._mounts_t = t
        return self._mounts_cache

    def _on_selection_changed(self, *a):
        self._update_actions()

    def _on_window_key(self, ctl, keyval, code, state):
        name = Gdk.keyval_name(keyval) or ""
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK)
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Text, Gtk.Entry)):
            return False
        if self.host.busy:
            if name == "Escape" and self.host.current.closable:
                self.host.current.close()
                return True
            return False
        if name == "Delete" and not mods:
            self.activate_action("win.trash", None)
            return True
        if name == "Escape" and not mods:
            self.selection.clear()
            return True
        if name == "BackSpace" and not mods:
            self.chart.go_up()
            return True
        if name in ("Return", "KP_Enter") and not mods and not isinstance(focus, Gtk.Button):
            self.zoom_selected()
            return True
        return False

    # ── scanning ──────────────────────────────────────────────────────────
    def pick_folder_and_scan(self):
        dlg = Gtk.FileDialog(title=_("Scan a folder"))
        try:
            dlg.set_initial_folder(Gio.File.new_for_path(home()))
        except Exception:
            pass
        dlg.select_folder(self, None, self._on_folder_picked)

    def _on_folder_picked(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            self.scan(f.get_path())

    def rescan(self):
        if self.result is not None:
            self.scan(self.result.root_path, force=True)

    def stop_scan(self):
        if self.scanner is not None and self.scanner.running:
            self.scanner.cancel()

    def scan(self, path, force=False):
        path = os.path.realpath(path)
        if not os.path.isdir(path):
            self.show_toast(_("That folder doesn't exist any more"))
            return
        if self.scanner is not None and self.scanner.running:
            self.scanner.cancel()
        self.settings["last_root"] = path
        self.selection.clear()
        self._clear_banners()
        self.sidebar.set_active(path)
        if not force:
            cached = scanner.ScanResult.load(path, self.settings["size_mode"])
            if cached is not None:
                self.scanner = None
                self._set_result(cached)
                self._banner("view-refresh-symbolic", _("Scanned %s") % time_ago(cached.scanned_at),
                             _("Showing the saved result for %s.") % pretty_path(path),
                             [(_("Rescan"), "win.rescan", True)])
                self._after_scan_ui(cached)
                return
        sc = scanner.Scanner(path, self.settings["size_mode"], self.settings["exclude"],
                             on_progress=lambda r: GLib.idle_add(self._on_scan_progress, sc, r),
                             on_done=lambda r: GLib.idle_add(self._on_scan_done, sc, r),
                             push_interval=T.SCAN_PUSH_MS / 1000)
        self.scanner = sc
        self._scan_started = time.monotonic()
        self._set_result(sc.result)
        self.chart.set_scanning(True)
        self.sidebar.set_scanning(path, True)
        self.foot_stack.set_visible_child_name("scan")
        self._update_counters(sc.result)
        if not self._elapsed_id:
            self._elapsed_id = GLib.timeout_add(250, self._tick_elapsed)
        sc.start()
        self._update_actions()

    def _set_result(self, result):
        self.result = result
        self.file_nodes = scanner.FileNodes(result.root)
        self.panel.set_result(result, self.file_nodes)
        self._show_empty(False)
        self.chart.set_root(result.root)

    def _tick_elapsed(self):
        if self.scanner is None or not self.scanner.running:
            self._elapsed_id = 0
            return False
        self.c_time.set_label(format_duration(time.monotonic() - self._scan_started))
        return True

    def _update_counters(self, res):
        self.c_files.set_label(format_count(res.files))
        self.c_size.set_label(format_size(res.root.size))
        self.c_time.set_label(format_duration(time.monotonic() - self._scan_started))

    def _on_scan_progress(self, sc, res):
        if sc is not self.scanner:
            return False
        self._update_counters(res)
        self.chart.invalidate()
        self.panel.refresh_header()
        now = time.monotonic()
        if now - self._last_list_refresh > 1.5:
            self._last_list_refresh = now
            self.panel.refresh_lists()
        return False

    def _on_scan_done(self, sc, res):
        if sc is not self.scanner:
            return False
        self.chart.set_scanning(False)
        self.sidebar.set_scanning(res.root_path, False)
        self.chart.invalidate()
        self._update_counters(res)
        elapsed = time.monotonic() - self._scan_started
        self.foot_stack.set_visible_child_name("hint")
        self._after_scan_ui(res)
        threading.Thread(target=self._save_scan, args=(res,), daemon=True).start()
        if res.cancelled:
            self._banner("dialog-information-symbolic", _("Scan stopped"),
                         _("Showing what was found in %s. The partial result stays usable.")
                         % format_duration(elapsed), [(_("Rescan"), "win.rescan", True)])
        if res.errors:
            n = len(set(res.errors))
            self._banner("dialog-warning-symbolic",
                         ngettext("%d folder couldn't be read", "%d folders couldn't be read", n) % n,
                         _("Some folders couldn't be read, so their size isn't counted."),
                         [(_("Show list"), "win.show-errors", False),
                          (_("Rescan with admin rights"), "win.admin-rescan", True)], warning=True)
        self.notify_if_background(_("Scan finished"), _("%s · %s in %s") % (
            display_name(res.root_path), format_size(res.root.size), format_duration(elapsed)), elapsed)
        self._update_actions()
        return False

    def _after_scan_ui(self, res):
        self.panel.refresh_lists()
        self.panel.refresh_header()
        self.sidebar.refresh_recent()
        self._update_actions()
        self._build_legend()

    def _save_scan(self, res):
        try:
            res.save()
            storage.add_recent(res.root_path, res.root.size, res.scanned_at)
        except Exception:
            log.exception("could not save scan cache")
        GLib.idle_add(lambda: (self.sidebar.refresh_recent(), False)[1])

    def notify_if_background(self, title, body, elapsed):
        if elapsed < 10 or self.is_active():
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_icon(Gio.ThemedIcon.new(paths.APP_ID))
        self.app.send_notification(None, n)

    def show_error_list(self):
        if self.result is None:
            return
        errs = sorted(set(self.result.errors))
        tv = Gtk.Label(label="\n".join(pretty_path(e) for e in errs[:300]) +
                       ("\n…" if len(errs) > 300 else ""), xalign=0, selectable=True, wrap=True)
        tv.add_css_class("caption")
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True)
        sw.set_max_content_height(300)
        sw.set_child(tv)
        admin = Gtk.Button(label=_("Rescan with admin rights"))
        admin.connect("clicked", lambda *a: (self.host.current.close(), self.admin_rescan()))
        ok = Gtk.Button(label=_("Close"))
        ok.add_css_class("suggested")
        ok.connect("clicked", lambda *a: self.host.current.close())
        self.host.show(sheets.MessageSheet(_("Folders Halo couldn't read"),
                                           _("Your user account can't open these folders."),
                                           "dialog-warning-symbolic", sw, [admin, ok]))

    def admin_rescan(self):
        if self.result is None:
            return
        path = self.result.root_path
        busy = sheets.BusySheet(_("Waiting for your password, then scanning %s with administrator rights…")
                                % pretty_path(path))
        self.host.show(busy)

        def work():
            try:
                proc = subprocess.Popen(["pkexec", paths.helper(), "scan", path], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
            except OSError:
                GLib.idle_add(self._admin_failed, _("pkexec isn't available on this system."))
                return
            self._helper_proc = proc
            result = None
            for line in proc.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "result":
                    result = ev
                elif ev.get("type") == "error":
                    GLib.idle_add(self._admin_failed, ev.get("message", ""))
            proc.wait()
            self._helper_proc = None
            if result is None:
                GLib.idle_add(self._admin_failed, _("Administrator permission wasn't given."))
                return
            GLib.idle_add(self._admin_done, path, result)
        threading.Thread(target=work, daemon=True).start()

    def _admin_failed(self, msg):
        if self.host.current is not None:
            self.host.current.close()
        self.show_toast(msg)
        return False

    def _admin_done(self, path, data):
        if self.host.current is not None:
            self.host.current.close()
        res = scanner.ScanResult(path, "disk")
        res.root = scanner.node_from_list(data["tree"], path)
        res.errors = data.get("errors", [])
        res.files, res.dirs = data.get("files", 0), data.get("dirs", 0)
        import heapq
        res.biggest = [tuple(x) for x in data.get("biggest", [])]
        res.oldest = [tuple(x) for x in data.get("oldest", [])]
        heapq.heapify(res.biggest)
        heapq.heapify(res.oldest)
        res.finished = time.time()
        self._clear_banners()
        self._set_result(res)
        self._after_scan_ui(res)
        self.show_toast(_("Scanned with administrator rights"))
        return False

    # ── banners & toasts ─────────────────────────────────────────────────
    def _clear_banners(self):
        c = self.banners.get_first_child()
        while c is not None:
            n = c.get_next_sibling()
            self.banners.remove(c)
            c = n

    def _banner(self, icon_name, title, body, buttons, warning=False):
        b = Gtk.Box(spacing=12)
        b.add_css_class("banner")
        if warning:
            b.add_css_class("warning")
        im = icon(icon_name, 18, "banner-icon")
        im.set_valign(Gtk.Align.CENTER)
        b.append(im)
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        t.set_hexpand(True)
        tl = Gtk.Label(label=title, xalign=0)
        tl.add_css_class("banner-title")
        bl = Gtk.Label(label=body, xalign=0, wrap=True)
        bl.add_css_class("banner-body")
        t.append(tl)
        t.append(bl)
        b.append(t)
        for label, action, primary in buttons:
            btn = Gtk.Button(label=label)
            btn.set_valign(Gtk.Align.CENTER)
            btn.set_action_name(action)
            if primary:
                btn.add_css_class("suggested")
            else:
                btn.add_css_class("flat")
            b.append(btn)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.add_css_class("flat")
        close.add_css_class("icon-button")
        close.set_valign(Gtk.Align.CENTER)
        close.connect("clicked", lambda *a: self.banners.remove(b))
        b.append(close)
        self.banners.append(b)

    def show_toast(self, text, fraction=None, timeout=4):
        self.toast_label.set_label(text)
        self.toast_bar.set_visible(fraction is not None)
        if fraction is not None:
            self.toast_bar.set_fraction(fraction)
        self.toast.set_reveal_child(True)
        if self._toast_id:
            GLib.source_remove(self._toast_id)
            self._toast_id = 0
        if timeout:
            self._toast_id = GLib.timeout_add_seconds(timeout, self._hide_toast)

    def _hide_toast(self):
        self._toast_id = 0
        self.toast.set_reveal_child(False)
        return False

    # ── navigation ───────────────────────────────────────────────────────
    def _on_center_changed(self, chart, node):
        self._rebuild_crumbs(node)
        self.panel.set_center(node)
        if self.settings["color_mode"] == "folder":
            self._build_legend()
        self._update_actions()

    def _rebuild_crumbs(self, node):
        c = self.crumbs.get_first_child()
        while c is not None:
            n = c.get_next_sibling()
            self.crumbs.remove(c)
            c = n
        if node is None:
            return
        chain = [node] + list(node.ancestors())
        chain.reverse()
        if len(chain) > 6:
            chain = chain[:1] + [None] + chain[-4:]
        for i, n in enumerate(chain):
            if i:
                sep = Gtk.Label(label="›")
                sep.add_css_class("crumb-sep")
                self.crumbs.append(sep)
            if n is None:
                l = Gtk.Label(label="…")
                l.add_css_class("crumb-sep")
                self.crumbs.append(l)
                continue
            label = display_name(n.path()) if n.parent is None else n.label()
            b = Gtk.Button(label=label)
            b.get_child().set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            b.get_child().set_max_width_chars(22)
            if n is node:
                b.add_css_class("current")
            b.connect("clicked", lambda w, m=n: self.chart.zoom_to(m))
            self.crumbs.append(b)

    def zoom_selected(self):
        sel = self.selection.top_level()
        if len(sel) == 1 and sel[0].flags & scanner.DIR and sel[0].children:
            node = sel[0]
            self.selection.clear()
            self.chart.zoom_to(node)

    def _on_list_activate(self, panel, node):
        if node.flags & scanner.OTHER_DRIVE:
            self.scan(node.path())
        elif node.flags & scanner.DIR and node.children:
            self.selection.clear()
            self.chart.zoom_to(node)

    def _on_special(self, chart, arc):
        if arc.is_smaller:
            self._smaller_arc = arc
            self.show_smaller_in_list()
        elif arc.node is not None and arc.node.flags & scanner.OTHER_DRIVE:
            self.scan(arc.node.path())

    def show_smaller_in_list(self):
        arc = getattr(self, "_smaller_arc", None)
        if arc is None or not arc.children_nodes:
            return
        self.settings["show_panel"] = True
        self.panel.set_tab("outline")
        first = arc.children_nodes[0]
        self.panel.origin = "chart"
        GLib.idle_add(self.panel.reveal, first)
        self.show_toast(_("%s smaller items are listed under %s") % (format_count(len(arc.children_nodes)),
                                                                      arc.top.label() if arc.top.parent else
                                                                      display_name(arc.top.path())))

    def scan_other(self):
        arc = getattr(self, "_other_arc", None)
        if arc is not None and arc.node is not None:
            self.scan(arc.node.path())

    # ── context menus ────────────────────────────────────────────────────
    def _shred_label(self):
        n = self.selection.total_count()
        return _("Shred %s (%s)…") % (format_items(n), format_size(self.selection.total_size()))

    def _reason_for_selection(self):
        for n in self.selection.top_level():
            r = self._shred_reason(n)
            if r:
                return r
        return None

    def _on_chart_context(self, chart, x, y, arc):
        special = None
        if arc is not None and arc.is_smaller:
            special = "smaller"
            self._smaller_arc = arc
        elif arc is not None and arc.node is not None and arc.node.flags & scanner.OTHER_DRIVE:
            special = "other-drive"
            self._other_arc = arc
        if special is None and len(self.selection) == 0:
            return
        self._update_actions()
        reason = self._reason_for_selection() if special is None else None
        sel = self.selection.top_level()
        is_folder = len(sel) == 1 and bool(sel[0].flags & scanner.DIR)
        pop = menus.make_context_popover(chart, x, y, is_folder, self._shred_label(),
                                         self.lookup_action("shred").get_enabled(), reason, special)
        menus.popup_later(pop)

    def _on_list_context(self, panel, row, x, y, node):
        if node.flags & scanner.OTHER_DRIVE:
            self._other_arc = type("A", (), {"node": node})()
            pop = menus.make_context_popover(row, x, y, False, "", False, None, "other-drive")
            menus.popup_later(pop)
            return
        self._update_actions()
        reason = self._reason_for_selection()
        sel = self.selection.top_level()
        is_folder = len(sel) == 1 and bool(sel[0].flags & scanner.DIR)
        pop = menus.make_context_popover(row, x, y, is_folder, self._shred_label(),
                                         self.lookup_action("shred").get_enabled(), reason)
        menus.popup_later(pop)

    # ── items for actions ────────────────────────────────────────────────
    def _items_from_selection(self):
        out = []
        for n in self.selection.top_level():
            if n.flags & scanner.OTHER_DRIVE:
                continue
            if n.flags & scanner.FILES:
                for f in self.file_nodes.list_folder(n.parent, self.settings["size_mode"], True):
                    out.append(Item(f.path(), f.size, False, f))
                continue
            out.append(Item(n.path(), n.size, bool(n.flags & scanner.DIR), n))
        return out

    def copy_paths(self):
        items = self._items_from_selection()
        if not items:
            return
        text = "\n".join(i.path for i in items)
        self.get_clipboard().set(text)
        self.show_toast(ngettext("Copied %d path", "Copied %d paths", len(items)) % len(items))

    def show_in_files(self):
        items = self._items_from_selection()
        if items:
            fileops.show_in_file_manager([i.path for i in items[:20]], self)

    # ── removal bookkeeping ──────────────────────────────────────────────
    def _apply_removed(self, items):
        """Subtract removed items from the tree and close their arcs (250 ms)."""
        old = self.chart.snapshot_arcs()
        removed_paths = []
        center = self.chart.center
        new_center = center
        for it in items:
            n = it.node
            if n is None and self.result is not None:
                n = self.result.root.find(it.path)
                if n is None and self.file_nodes is not None:
                    parent = self.result.root.find(os.path.dirname(it.path))
                    if parent is not None:
                        n = self.file_nodes.get(it.path, it.size, scanner.kind_for(it.path))
            if n is not None:
                if n.parent is None:
                    continue
                if center is not None and (n is center or n.is_ancestor_of(center)):
                    new_center = n.parent           # the view was inside what got removed
                n.detach()
            removed_paths.append(it.path)
        if self.result is not None:
            self.result.forget_paths(removed_paths)
        self.selection.clear()
        if new_center is not center:
            self.chart.set_root(self.result.root, new_center)
        else:
            self.chart.animate_removal(old)
            self.panel.refresh_lists()
            self.panel.refresh_header()
            self._rebuild_crumbs(self.chart.center)
        self.sidebar.refresh_drives()
        if self.result is not None:
            threading.Thread(target=self._save_scan, args=(self.result,), daemon=True).start()
        self._update_actions()

    # ── trash / move / copy ──────────────────────────────────────────────
    def trash_selection(self):
        if not self.lookup_action("trash").get_enabled():
            return
        items = self._items_from_selection()
        self._trash_items(items)

    def _trash_items(self, items):
        if not items:
            return
        self.show_toast(_("Moving %s to the Trash…") % format_items(len(items)), 0.0, timeout=0)

        def done(ok, errs):
            okset = set(ok)
            self._apply_removed([i for i in items if i.path in okset])
            if errs:
                self.show_toast(_("%d item(s) couldn't be moved to the Trash: %s") % (len(errs), errs[0][1]))
            else:
                self.show_toast(_("Moved %s to the Trash · restore from the Trash in your file manager")
                                % format_items(len(ok)))
            return False
        fileops.trash([i.path for i in items], done)

    def transfer_selection(self, move):
        items = self._items_from_selection()
        if not items:
            return
        self._transfer_items(items, move)

    def _transfer_items(self, items, move):
        dlg = Gtk.FileDialog(title=_("Move to…") if move else _("Copy to…"))
        dlg.set_accept_label(_("Move Here") if move else _("Copy Here"))

        def picked(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            dest = f.get_path() if f else None
            if not dest:
                return

            def progress(i, n, name):
                self.show_toast((_("Moving %s") if move else _("Copying %s")) % (name or "…"),
                                i / max(1, n), timeout=0)
                return False

            def done(ok, errs):
                if move:
                    inside = self.result is not None and (dest + "/").startswith(self.result.root_path.rstrip("/")
                                                                                + "/")
                    moved = set(p for p, t in ok)
                    self._apply_removed([i for i in items if i.path in moved])
                    if inside:
                        self._banner("view-refresh-symbolic", _("Items moved inside this scan"),
                                     _("Rescan to see them in their new place."),
                                     [(_("Rescan"), "win.rescan", True)])
                if errs:
                    self.show_toast(_("%d item(s) failed: %s") % (len(errs), errs[0][1]))
                else:
                    self.show_toast((_("Moved %s to %s") if move else _("Copied %s to %s"))
                                    % (format_items(len(ok)), pretty_path(dest)))
                return False
            fileops.transfer([i.path for i in items], dest, move, progress, done)
        dlg.select_folder(self, None, picked)

    def _on_dropped(self, panel, paths_, x, y):
        items = []
        for p in paths_:
            node = self.result.root.find(p) if self.result is not None else None
            if node is not None:
                items.append(Item(p, node.size, bool(node.flags & scanner.DIR), node))
            else:
                size, is_dir = _quick_size(p)
                items.append(Item(p, size, is_dir, None))
        if not items:
            return
        self._dropped = items
        label = _("Shred %s (%s)…") % (format_items(len(items)), format_size(sum(i.size for i in items)))
        blocked = [i for i in items if safety.shred_block_reason(i.path, self._mounts())]
        m = Gio.Menu()
        if blocked:
            m.append(safety.shred_block_reason(blocked[0].path, self._mounts()), "win.disabled-reason")
        m.append(label, "win.drop-shred")
        m.append(_("Move to Trash"), "win.drop-trash")
        m.append(_("Move to…"), "win.drop-move")
        if not self.lookup_action("drop-trash"):
            self._add("drop-shred", lambda *a: self.start_shred(list(self._dropped)))
            self._add("drop-trash", lambda *a: self._trash_items(self._checked_drop("trash")))
            self._add("drop-move", lambda *a: self._transfer_items(self._checked_drop("trash"), True))
        trash_blocked = any(safety.trash_block_reason(i.path, self._mounts()) for i in items)
        self.lookup_action("drop-shred").set_enabled(not blocked)
        self.lookup_action("drop-trash").set_enabled(not trash_blocked)
        self.lookup_action("drop-move").set_enabled(not trash_blocked)
        pop = menus.make_menu_popover(panel.drop, m, x, y, label, [_("Move to Trash")])
        menus.popup_later(pop)

    def _checked_drop(self, kind):
        return [i for i in getattr(self, "_dropped", []) if not safety.trash_block_reason(i.path, self._mounts())]

    # ── shredding ────────────────────────────────────────────────────────
    def shred_selection(self):
        if not self.lookup_action("shred").get_enabled():
            return
        self.start_shred(self._items_from_selection())

    def shred_paths_from_cli(self, paths_):
        items = []
        for p in paths_:
            p = os.path.abspath(p)
            reason = safety.shred_block_reason(p)
            if reason:
                self.show_toast(_("Can't shred %s: %s") % (os.path.basename(p), reason), timeout=8)
                continue
            size, is_dir = _quick_size(p)
            items.append(Item(p, size, is_dir))
        if items:
            self.start_shred(items)

    def start_shred(self, items, mode="files", title=None, subtitle=None):
        if not items:
            return
        self.host.show(sheets.BusySheet(_("Checking what can be removed and where copies may remain…")))
        search_root = self.result.root_path if self.result is not None else home()
        cache = traces.thumbnail_cache_dir()

        def work():
            pre = sheets.Preflight()
            pre.items = [(i.path, i.size, i.is_dir, i.node) for i in items]
            ps = [i.path for i in items]
            try:
                mounts = safety.read_mounts()
                pre.warnings = safety.warnings_for(ps, mounts, self.settings["warn_ssd"])
                if mode == "files":
                    for p in ps:
                        if os.path.islink(p):
                            pre.symlinks.append(p)
                            continue
                        if not os.path.isdir(p):
                            others = safety.hard_link_names(p, search_root, 2.0)
                            if others:
                                pre.hard_links[p] = others
                    removable = lambda fp: safety.is_external_mount(safety.mount_for(fp, mounts))  # noqa: E731
                    pre.thumbnails = traces.find_thumbnails([p for p in ps if not os.path.islink(p)], cache,
                                                            removable)
                    pre.recent = traces.count_recent_matches(ps)
                    pre.trash = traces.find_trash_copies(ps, mounts)
                pre.privileged = [p for p in ps if shredmod.needs_privilege(p)]
            except Exception:
                log.exception("preflight failed")
            GLib.idle_add(self._show_shred_sheets, pre, mode, title, subtitle)
        threading.Thread(target=work, daemon=True).start()

    def _show_shred_sheets(self, pre, mode, title, subtitle):
        self._prefer_single = False

        def confirm():
            if self._prefer_single:
                self.settings["shred_method"] = "random1"
            sheet = sheets.ShredConfirmSheet(pre, self.settings, lambda opts: self._run_shred(pre, opts, mode),
                                             title, subtitle, show_traces=(mode == "files"))
            self.host.show(sheet)
        if pre.warnings:
            self.host.show(sheets.WarningsSheet(pre, confirm, lambda: setattr(self, "_prefer_single", True)))
        else:
            confirm()
        return False

    def _run_shred(self, pre, opts, mode):
        method = opts["method"]
        passes = len(shredmod.METHODS[method])
        user_paths = [p for p, _s, _d, _n in pre.items if p not in pre.privileged]
        priv_paths = [p for p in pre.privileged]
        tmp = shredmod.Shredder(method, opts["verify"])
        plan = tmp.plan(user_paths) + [(p, s, d) for p, s, d, _n in pre.items if p in priv_paths]
        sheet = sheets.ShredProgressSheet(plan, passes, shredmod.METHOD_LABELS[method],
                                          lambda: self._cancel_shred())
        self.host.show(sheet)
        cancel = threading.Event()
        self._shred_cancel = cancel
        t0 = time.monotonic()

        def emit(ev, offset=0):
            GLib.idle_add(lambda: (sheet.handle(ev, offset), False)[1])

        def shred_one(path):
            r = shredmod.Shredder(method, opts["verify"], cancel_event=threading.Event()).run([path])
            return all(x.status in (shredmod.VERIFIED, shredmod.REMOVED) for x in r)

        def work():
            results = []
            sh = shredmod.Shredder(method, opts["verify"], progress=lambda ev: emit(ev) if ev["type"] not in
                                   ("start", "done") else None, cancel_event=cancel,
                                   remove_links=opts["remove_links"])
            if user_paths:
                results += [r.to_dict() for r in sh.run(user_paths)]
            base = len(plan) - len(priv_paths)
            for j, p in enumerate(priv_paths):
                if cancel.is_set():
                    results.append({"path": p, "status": "skipped", "message": shredmod.STATUS_TEXT["skipped"],
                                    "bytes": 0, "is_dir": os.path.isdir(p)})
                    emit({"type": "file_done", "index": j, "path": p, "status": "skipped"}, base)
                    continue
                results.append(self._shred_privileged(p, method, opts["verify"], base + j, emit, cancel))
            # traces, as one step after the files
            removed = [r["path"] for r in results if r["status"] in ("verified", "removed", "link_removed")]
            summary = {}
            if mode == "files" and removed and not cancel.is_set():
                top_ok = [p for p, _s, _d, _n in pre.items
                          if not any(r["path"] == p or r["path"].startswith(p.rstrip("/") + "/")
                                     for r in results if r["status"] not in ("verified", "removed",
                                                                             "link_removed"))]
                if opts["thumbnails"] and pre.thumbnails:
                    n = 0
                    for t in pre.thumbnails:
                        if os.path.exists(t) and shred_one(t):
                            n += 1
                    summary["thumbnails"] = n
                if opts["recent"] and pre.recent:
                    try:
                        summary["recent entries"] = traces.clean_recent(top_ok, shred_one) if not dry_run() \
                            else pre.recent
                    except Exception:
                        log.exception("recent cleanup failed")
                if opts["trash"] and pre.trash:
                    n = 0
                    for orig, payload, info in pre.trash:
                        targets = [x for x in (payload, info) if os.path.lexists(x)]
                        if targets:
                            r = shredmod.Shredder(method, opts["verify"]).run(targets)
                            if all(x.status in (shredmod.VERIFIED, shredmod.REMOVED, shredmod.LINK_REMOVED)
                                   for x in r):
                                n += 1
                    summary["trash copies"] = n
            report_path = None
            if opts["report"]:
                try:
                    rep = report.build(results, method, opts["verify"], opts["names"], summary, dry_run())
                    report_path = report.save(rep, self.settings["report_dir"])
                except Exception:
                    log.exception("could not save report")
            GLib.idle_add(self._shred_finished, pre, results, summary, report_path, sheet, mode,
                          time.monotonic() - t0)
        threading.Thread(target=work, daemon=True).start()

    def _shred_privileged(self, path, method, verify, row, emit, cancel):
        args = ["pkexec", paths.helper(), "shred", "--method", method] + ([] if verify else ["--no-verify"]) \
            + [path]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            emit({"type": "file_done", "index": 0, "path": path, "status": "failed",
                  "message": _("pkexec isn't available")}, row)
            return {"path": path, "status": "failed", "message": "pkexec missing", "bytes": 0, "is_dir": False}
        self._helper_proc = proc
        final = None
        emit({"type": "file_start", "index": 0, "path": path, "size": 0}, row)
        for line in proc.stdout:
            if cancel.is_set() and proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGTERM)
                except OSError:
                    pass
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "pass":
                emit({"type": "pass", "index": 0, "pass": ev["pass"], "passes": ev["passes"],
                      "fraction": ev["fraction"]}, row)
            elif t == "verify":
                emit({"type": "verify", "index": 0, "fraction": ev["fraction"]}, row)
            elif t == "done":
                final = ev["results"]
            elif t == "error":
                final = [{"path": path, "status": "failed", "message": ev.get("message", ""), "bytes": 0}]
        proc.wait()
        self._helper_proc = None
        if final is None:
            final = [{"path": path, "status": "failed", "message": _("Administrator permission wasn't given"),
                      "bytes": 0}]
        bad = [r for r in final if r["status"] not in ("verified", "removed", "link_removed")]
        st = bad[0]["status"] if bad else ("verified" if verify else "removed")
        msg = bad[0].get("message") if bad else shredmod.STATUS_TEXT[st]
        emit({"type": "file_done", "index": 0, "path": path, "status": st, "message": msg}, row)
        size = sum(r.get("bytes", 0) for r in final)
        return {"path": path, "status": st, "message": msg, "bytes": size, "is_dir": os.path.isdir(path)}

    def _cancel_shred(self):
        if self._shred_cancel is not None:
            self._shred_cancel.set()
        if self._helper_proc is not None:
            try:
                self._helper_proc.send_signal(signal.SIGTERM)
            except OSError:
                pass

    def _shred_finished(self, pre, results, summary, report_path, sheet, mode, elapsed):
        bits = []
        if summary.get("thumbnails"):
            bits.append(ngettext("%d thumbnail", "%d thumbnails", summary["thumbnails"]) % summary["thumbnails"])
        if summary.get("recent entries"):
            bits.append(ngettext("%d recent-file entry", "%d recent-file entries", summary["recent entries"])
                        % summary["recent entries"])
        if summary.get("trash copies"):
            bits.append(ngettext("%d copy in the Trash", "%d copies in the Trash", summary["trash copies"])
                        % summary["trash copies"])
        trace_text = (_("Also removed: ") + ", ".join(bits)) if bits else ""
        sheet.finish(results, trace_text, report_path, self.open_reports)
        if mode == "files":
            bad = {r["path"] for r in results if r["status"] not in ("verified", "removed", "link_removed")}
            done_items = []
            for p, s, d, n in pre.items:
                if p in bad or any(b.startswith(p.rstrip("/") + "/") for b in bad):
                    continue
                done_items.append(Item(p, s, d, n))
            if done_items:
                self._apply_removed(done_items)
        elif mode == "own-data":
            storage.remove_empty_dirs()
            self.sidebar.refresh_recent()
        self.notify_if_background(_("Shred finished"), _("%s processed") % format_items(len(results)), elapsed)
        self._shred_cancel = None
        return False

    # ── Tools menu ───────────────────────────────────────────────────────
    def tool_clean_thumbnails(self):
        files = traces.thumbnail_cache_files()
        if not files:
            self.host.show(sheets.MessageSheet(_("No cached thumbnails"),
                                               _("Your thumbnail cache is already empty.")))
            return
        total = sum(os.lstat(f).st_size for f in files if os.path.exists(f))
        self.start_shred([Item(f, os.lstat(f).st_size, False) for f in files], mode="tool",
                         title=_("Shred %s of cached thumbnails?") % format_size(total),
                         subtitle=_("Your desktop keeps small previews of images, videos and documents you "
                                    "have looked at, even after the originals are gone. They'll be "
                                    "re-created for files you still have."))

    def tool_clear_recent(self):
        p = traces.recent_file()
        if not os.path.exists(p):
            self.host.show(sheets.MessageSheet(_("No recent-files list"), _("There's nothing to clear.")))
            return

        def run(pre, opts, mode="recent"):
            sheet = sheets.ShredProgressSheet([(p, os.path.getsize(p), False)], len(shredmod.METHODS[opts["method"]]),
                                              shredmod.METHOD_LABELS[opts["method"]], lambda: None)
            self.host.show(sheet)

            def work():
                ok = [0]

                def shred_one(path):
                    r = shredmod.Shredder(opts["method"], opts["verify"],
                                          progress=lambda ev: GLib.idle_add(sheet.handle, ev)).run([path])
                    ok[0] = all(x.status in ("verified", "removed") for x in r)
                    return ok[0]
                try:
                    n = traces.clean_recent([], shred_one, clear_all=True) if not dry_run() else 0
                except Exception:
                    log.exception("clear recent failed")
                    n = 0
                res = [{"path": p, "status": "verified" if ok[0] or dry_run() else "failed", "bytes": 0}]
                GLib.idle_add(sheet.finish, res, ngettext("%d entry removed", "%d entries removed", n) % n,
                              None, self.open_reports)
            threading.Thread(target=work, daemon=True).start()
        pre = sheets.Preflight()
        pre.items = [(p, os.path.getsize(p), False, None)]
        pre.warnings = safety.warnings_for([p], None, self.settings["warn_ssd"])
        self._show_tool_sheet(pre, run, _("Clear the recent files list?"),
                              _("Every app's “Recent” list is stored in recently-used.xbel. Halo writes an "
                                "empty list and shreds the old file."))

    def _show_tool_sheet(self, pre, run, title, subtitle):
        def confirm():
            self.host.show(sheets.ShredConfirmSheet(pre, self.settings, lambda opts: run(pre, opts), title,
                                                    subtitle, show_traces=False))
        if pre.warnings:
            self.host.show(sheets.WarningsSheet(pre, confirm, lambda: None))
        else:
            confirm()

    def tool_shred_trash(self):
        items = []
        for d in traces.trash_dirs():
            for sub in ("files", "info"):
                base = os.path.join(d, sub)
                try:
                    for n in os.listdir(base):
                        p = os.path.join(base, n)
                        size, is_dir = _quick_size(p)
                        items.append(Item(p, size, is_dir))
                except OSError:
                    pass
        if not items:
            self.host.show(sheets.MessageSheet(_("The Trash is empty"), _("There's nothing to shred.")))
            return
        self.start_shred(items, mode="tool", title=_("Shred everything in the Trash?"),
                         subtitle=_("Items in the Trash on every drive are overwritten and removed. "
                                    "You won't be able to restore them."))

    def tool_clear_own_data(self):
        files = storage.own_data_files()
        if not files:
            self.host.show(sheets.MessageSheet(_("Nothing to clear"), _("Halo has no cached data right now.")))
            return
        self.start_shred([Item(f, os.path.getsize(f), False) for f in files], mode="own-data",
                         title=_("Clear Halo's own data?"),
                         subtitle=_("Shreds Halo's saved scans, its log and the recent-scan list. "
                                    "Preferences and shred reports are kept."))

    def open_reports(self):
        d = self.settings["report_dir"]
        storage.ensure_dir(d)
        fileops.open_folder(d, self)

    # ── export ───────────────────────────────────────────────────────────
    def export_summary(self):
        if self.chart.center is None:
            return
        dlg = Gtk.FileDialog(title=_("Export summary"))
        dlg.set_initial_name("Halo summary - %s.csv" % display_name(self.chart.center.path()))
        filters = Gio.ListStore(item_type=Gtk.FileFilter)
        for name, pat in ((_("CSV spreadsheet"), "*.csv"), (_("HTML page"), "*.html")):
            f = Gtk.FileFilter()
            f.set_name(name)
            f.add_pattern(pat)
            filters.append(f)
        dlg.set_filters(filters)
        dlg.save(self, None, self._on_export)

    def _on_export(self, dlg, res):
        try:
            f = dlg.save_finish(res)
        except GLib.Error:
            return
        path = f.get_path()
        c = self.chart.center
        rows = []
        for k in c.sorted_children(self.settings["show_hidden"]):
            share = k.size / c.size * 100 if c.size else 0
            rows.append((k.label(), k.path(), k.size, format_size(k.size), k.count, "%.1f" % share,
                         _(T.KIND_NAMES[k.dominant_kind()])))
        head = ("Name", "Path", "Bytes", "Size", "Items", "Share %", "Main kind")
        if path.lower().endswith(".html"):
            from html import escape
            body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % escape(str(v)) for v in r) for r in rows)
            html = ("<!doctype html><meta charset=utf-8><title>Halo summary</title>"
                    "<style>body{font:14px system-ui;background:#0E1424;color:#E8ECF6;padding:24px}"
                    "table{border-collapse:collapse}td,th{padding:6px 12px;border-bottom:1px solid #2A3557;"
                    "text-align:left}th{color:#9AA6C4}</style><h1>%s · %s</h1><table><tr>%s</tr>%s</table>"
                    % (escape(display_name(c.path())), escape(format_size(c.size)),
                       "".join("<th>%s</th>" % h for h in head), body))
            data = html
        else:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(head)
            w.writerows(rows)
            data = buf.getvalue()
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(data)
            self.show_toast(_("Exported to %s") % pretty_path(path))
        except OSError as e:
            self.show_toast(_("Couldn't export: %s") % e.strerror)

    # ── drives submenu ───────────────────────────────────────────────────
    def _refresh_drive_menu(self):
        from .panels import list_drives
        self.drive_menu.remove_all()
        for d in list_drives():
            it = Gio.MenuItem.new(d.name, None)
            it.set_action_and_target_value("win.scan-path", GLib.Variant.new_string(d.path))
            self.drive_menu.append_item(it)

    # ── close ────────────────────────────────────────────────────────────
    def _on_close(self, *a):
        if self.scanner is not None:
            self.scanner.cancel()
        if self._shred_cancel is not None:
            self._cancel_shred()
        if not self.is_maximized() and not self.is_fullscreen():
            w, h = self.get_default_size()
            if w > 0 and h > 0:
                self.settings["window_width"] = w
                self.settings["window_height"] = h
        self.settings["window_maximized"] = self.is_maximized()
        return False


class HaloApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=paths.APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.settings = None
        self.window = None
        GLib.set_application_name("Halo")

    def do_startup(self):
        Gtk.Application.do_startup(self)
        storage.setup_logging()
        log.info("Halo %s starting", paths.VERSION)
        self.settings = storage.Settings()
        gs = Gtk.Settings.get_default()
        if gs is not None:
            gs.props.gtk_application_prefer_dark_theme = True
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(paths.icons_dir())
        Gtk.IconTheme.get_for_display(display).add_search_path(os.path.join(paths.icons_dir(), "hicolor"))
        prov = Gtk.CssProvider()
        prov.load_from_path(paths.style_css())
        Gtk.StyleContext.add_provider_for_display(display, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        def add(name, cb):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda *x: cb())
            self.add_action(a)
        add("quit", self._quit)
        add("preferences", self.show_preferences)
        add("about", self.show_about)
        add("help", lambda: self.show_help("help"))
        add("ssd-help", lambda: self.show_help("ssd"))
        add("privacy", lambda: self.show_help("privacy"))
        add("terms", lambda: self.show_help("terms"))
        add("licence", lambda: self.show_help("licence"))
        add("shortcuts", self.show_shortcuts)
        add("report-issue", self.report_issue)
        for action, accels in menus.ACCELS.items():
            if action in menus.WIDGET_KEYS:
                continue
            self.set_accels_for_action(action, accels)

    def _quit(self):
        if self.window is not None:
            self.window._on_close()
        self.quit()

    def _ensure_window(self):
        if self.window is None:
            self.window = HaloWindow(self)
        return self.window

    def do_activate(self):
        w = self._ensure_window()
        w.present()

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        cwd = cmdline.get_cwd() or os.getcwd()
        shred_paths, scan_path = [], None
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--shred":
                shred_paths = [os.path.join(cwd, p) for p in args[i + 1:]]
                break
            if not a.startswith("-"):
                scan_path = os.path.join(cwd, a)
            i += 1
        first = self.window is None
        w = self._ensure_window()
        w.present()
        if shred_paths:
            GLib.idle_add(lambda: (w.shred_paths_from_cli(shred_paths), False)[1])
        elif scan_path:
            GLib.idle_add(lambda: (w.scan(scan_path), False)[1])
        elif first:
            GLib.idle_add(self._first_view)
        return 0

    def _first_view(self):
        w = self.window
        if not self.settings["welcomed"]:
            self.settings["welcomed"] = True
            ssd = self.settings["warn_ssd"] and safety.fs_info("/").rotational is False
            w.host.show(sheets.WelcomeSheet(ssd, w.pick_folder_and_scan, lambda: w.scan(home()), self.show_help))
        elif self.settings["reopen_last"] and self.settings["last_root"] and \
                os.path.isdir(self.settings["last_root"]):
            w.scan(self.settings["last_root"])
        return False

    def show_preferences(self):
        from .preferences import PreferencesWindow
        PreferencesWindow(self.window, self.settings).present()

    def show_about(self):
        from .about import show_about
        show_about(self.window)

    def show_help(self, page="help"):
        from .help import HelpWindow
        HelpWindow(self.window, page).present()

    def show_shortcuts(self):
        menus.shortcuts_window(self.window).present()

    def report_issue(self):
        w = self.window
        b1 = Gtk.Button(label=_("Open GitHub issues"))
        b1.add_css_class("suggested")
        b2 = Gtk.Button(label=_("Email %s") % paths.DEV_EMAIL)

        def open_uri(uri):
            Gtk.UriLauncher.new(uri).launch(w, None, None, None)
            w.host.current.close()
        b1.connect("clicked", lambda *a: open_uri(paths.ISSUES_URL))
        b2.connect("clicked", lambda *a: open_uri("mailto:%s?subject=Halo%%20issue" % paths.DEV_EMAIL))
        w.host.show(sheets.MessageSheet(
            _("Report an issue"),
            _("This opens your own browser or mail app — Halo itself never connects to the internet. "
              "Please don't paste private file names or paths."), "dialog-information-symbolic",
            None, [b2, b1]))
