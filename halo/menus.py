# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Menu bar model, keyboard shortcuts and the shared right-click menu.

Every item is a win. or app. action, so the menu bar, shortcuts and context
menus share one implementation. Items that can't apply are greyed, not hidden.
"""

from gi.repository import Gio, GLib, Gtk

from .util import _

ACCELS = {
    "win.scan-folder": ["<Control>o"],
    "win.rescan": ["<Control>r", "F5"],
    "win.stop-scan": ["<Control>period"],
    "win.export": ["<Control>e"],
    "app.quit": ["<Control>q"],
    "win.select-all": ["<Control>a"],
    "win.invert-selection": ["<Control>i"],
    "win.clear-selection": ["Escape"],
    "win.copy-path": ["<Control><Shift>c"],
    "win.trash": ["Delete"],
    "win.shred": ["<Shift>Delete"],
    "app.preferences": ["<Control>comma"],
    "win.show-hidden": ["<Control>h"],
    "win.zoom-in": ["Return"],
    "win.go-up": ["BackSpace"],
    "win.go-root": ["<Alt>Home"],
    "win.show-sidebar": ["F9"],
    "win.show-panel": ["F10"],
    "win.fullscreen": ["F11"],
    "app.help": ["F1"],
    "app.shortcuts": ["<Control>question"],
}

# accels that must not steal keys from focused widgets (lists, entries, the chart)
WIDGET_KEYS = {"win.zoom-in", "win.go-up", "win.clear-selection", "win.trash"}


class _Menu(Gio.Menu):
    def append(self, label, action):
        it = Gio.MenuItem.new(label, action)
        if action in WIDGET_KEYS:
            it.set_attribute_value("accel", GLib.Variant.new_string(ACCELS[action][0]))
        self.append_item(it)


def build_menubar(drive_menu):
    bar = Gio.Menu()

    f = _Menu()
    s = _Menu()
    s.append(_("Scan Folder…"), "win.scan-folder")
    s.append_submenu(_("Scan Drive"), drive_menu)
    s.append(_("Rescan"), "win.rescan")
    s.append(_("Stop Scan"), "win.stop-scan")
    f.append_section(None, s)
    s = _Menu()
    s.append(_("Export Summary…"), "win.export")
    f.append_section(None, s)
    s = _Menu()
    s.append(_("Quit"), "app.quit")
    f.append_section(None, s)
    bar.append_submenu(_("_File"), f)

    e = _Menu()
    s = _Menu()
    s.append(_("Select All"), "win.select-all")
    s.append(_("Invert Selection"), "win.invert-selection")
    s.append(_("Clear Selection"), "win.clear-selection")
    e.append_section(None, s)
    s = _Menu()
    s.append(_("Copy Path"), "win.copy-path")
    s.append(_("Move to Trash"), "win.trash")
    s.append(_("Shred…"), "win.shred")
    e.append_section(None, s)
    s = _Menu()
    s.append(_("Preferences"), "app.preferences")
    e.append_section(None, s)
    bar.append_submenu(_("_Edit"), e)

    v = _Menu()
    s = _Menu()
    s.append(_("Colour by File Kind"), "win.color-mode::kind")
    s.append(_("Colour by Top Folder"), "win.color-mode::folder")
    v.append_section(None, s)
    s = _Menu()
    s.append(_("Disk Usage"), "win.size-mode::disk")
    s.append(_("Apparent Size"), "win.size-mode::apparent")
    v.append_section(None, s)
    s = _Menu()
    s.append(_("Show Hidden Files"), "win.show-hidden")
    v.append_section(None, s)
    s = _Menu()
    s.append(_("Zoom In"), "win.zoom-in")
    s.append(_("Up One Level"), "win.go-up")
    s.append(_("Back to Scan Root"), "win.go-root")
    v.append_section(None, s)
    s = _Menu()
    s.append(_("Show Sidebar"), "win.show-sidebar")
    s.append(_("Show List Panel"), "win.show-panel")
    s.append(_("Full Screen"), "win.fullscreen")
    v.append_section(None, s)
    bar.append_submenu(_("_View"), v)

    t = _Menu()
    s = _Menu()
    s.append(_("Clean Thumbnail Cache…"), "win.clean-thumbnails")
    s.append(_("Clear Recent Files List…"), "win.clear-recent")
    s.append(_("Shred Trash Contents…"), "win.shred-trash")
    t.append_section(None, s)
    s = _Menu()
    s.append(_("Open Shred Reports Folder"), "win.open-reports")
    s.append(_("Clear Halo's Own Data…"), "win.clear-own-data")
    t.append_section(None, s)
    bar.append_submenu(_("_Tools"), t)

    h = _Menu()
    s = _Menu()
    s.append(_("Help"), "app.help")
    s.append(_("Keyboard Shortcuts"), "app.shortcuts")
    s.append(_("Shredding and SSDs Explained"), "app.ssd-help")
    h.append_section(None, s)
    s = _Menu()
    s.append(_("Privacy Policy"), "app.privacy")
    s.append(_("Terms of Service"), "app.terms")
    s.append(_("Licence"), "app.licence")
    h.append_section(None, s)
    s = _Menu()
    s.append(_("Report an Issue…"), "app.report-issue")
    s.append(_("About Halo"), "app.about")
    h.append_section(None, s)
    bar.append_submenu(_("_Help"), h)
    return bar


def context_menu_model(is_folder, shred_label, special=None, reason=None):
    """Zoom In (folders) · Show in File Manager · Copy Path · | · Move to… · Copy to… ·
    Move to Trash · | · Shred… (styled danger).

    One flat section: the dividers are drawn with CSS on the first item of each
    group (see make_menu_popover), because GtkPopoverMenu measures itself before
    its own section separators appear and then clips the last row."""
    m = Gio.Menu()
    if special == "smaller":
        m.append(_("Show these items in the list"), "win.show-in-list")
        return m, []
    if special == "other-drive":
        m.append(_("Scan this drive"), "win.scan-other")
        return m, []
    m.append(_("Zoom In"), "win.zoom-selected")
    m.append(_("Show in File Manager"), "win.show-in-files")
    m.append(_("Copy Path"), "win.copy-path")
    m.append(_("Move to…"), "win.move-to")
    m.append(_("Copy to…"), "win.copy-to")
    m.append(_("Move to Trash"), "win.trash")
    starts = [_("Move to…")]
    if reason:
        # greyed, never actionable: says why Shred is unavailable
        m.append(reason, "win.disabled-reason")
        starts.append(reason)
    else:
        starts.append(shred_label)
    m.append(shred_label, "win.shred")
    return m, starts


def _tag(widget, label, css):
    """GtkPopoverMenu has no per-item styling, so find a row by its text and tag it."""
    if type(widget).__name__ == "GtkModelButton":
        if widget.get_property("text") == label:
            widget.add_css_class(css)
            return True
    ch = widget.get_first_child()
    while ch is not None:
        if _tag(ch, label, css):
            return True
        ch = ch.get_next_sibling()
    return False


def make_menu_popover(parent, model, x, y, danger_label=None, group_starts=()):
    pop = Gtk.PopoverMenu.new_from_model_full(model, Gtk.PopoverMenuFlags.NESTED)
    pop.set_has_arrow(False)
    pop.set_parent(parent)
    if danger_label:
        _tag(pop, danger_label, "danger-item")
    for label in group_starts:
        _tag(pop, label, "group-start")
    from gi.repository import Gdk
    rect = Gdk.Rectangle()
    rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
    pop.set_pointing_to(rect)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
    return pop


def make_context_popover(parent, x, y, is_folder, shred_label, shred_enabled, reason=None, special=None):
    model, starts = context_menu_model(is_folder, shred_label, special, reason)
    return make_menu_popover(parent, model, x, y, shred_label if special is None else None, starts)


def _fit_height(pop):
    """Make the menu's scroller exactly as tall as its contents. GtkPopoverMenu
    can measure itself before its section separators are shown, which clips the
    last row (Shred…) off the bottom."""
    def find(w):
        if isinstance(w, Gtk.ScrolledWindow):
            return w
        ch = w.get_first_child()
        while ch is not None:
            r = find(ch)
            if r is not None:
                return r
            ch = ch.get_next_sibling()
        return None
    sw = find(pop)
    if sw is None or sw.get_child() is None:
        return
    child = sw.get_child()
    _mn, nat, _a, _b = child.measure(Gtk.Orientation.VERTICAL, -1)
    if nat > 0:
        sw.set_min_content_height(nat)
        sw.set_max_content_height(max(nat, sw.get_max_content_height()))


def popup_later(pop):
    def go():
        _fit_height(pop)
        pop.popup()
        GLib.timeout_add(40, lambda: (_fit_height(pop), False)[1])
        return False
    GLib.timeout_add(30, go)
