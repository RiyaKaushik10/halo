# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Preferences: General, Shredding, Appearance, Privacy and data.
Every change saves to ~/.config/halo/settings.json immediately."""

from gi.repository import GLib, Gtk, Pango

from . import storage
from .util import _, format_size, pretty_path


class PreferencesWindow(Gtk.Window):
    def __init__(self, parent, settings):
        super().__init__(title=_("Preferences"), transient_for=parent, modal=False)
        self.parent_window = parent
        self.settings = settings
        self.add_css_class("halo-dialog")
        self.set_default_size(640, 720)
        hb = Gtk.HeaderBar()
        hb.add_css_class("halo-header")
        t = Gtk.Label(label=_("Preferences"))
        t.add_css_class("app-name")
        hb.set_title_widget(t)
        self.set_titlebar(hb)

        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.add_css_class("pref-page")
        sc.set_child(page)
        self.set_child(sc)
        self.page = page

        g = self._group(_("General"))
        dd = Gtk.DropDown.new_from_strings([_("Disk usage (like du)"), _("Apparent size")])
        dd.set_selected(0 if settings["size_mode"] == "disk" else 1)
        dd.connect("notify::selected", lambda d, p: self._set("size_mode", "disk" if d.get_selected() == 0
                                                              else "apparent"))
        self._row(g, _("Size measure"), _("Disk usage counts blocks really used; apparent size is file length"),
                  dd)
        self._switch(g, _("Show hidden files"), _("Files and folders whose names start with a dot"),
                     "show_hidden")
        self._switch(g, _("Reopen last scan on launch"), None, "reopen_last")
        self.excl_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        add = Gtk.Button(label=_("Add…"))
        add.connect("clicked", self._add_exclude)
        self._row(g, _("Folders to exclude from scans"), None, add)
        g.append(self.excl_box)
        self.excl_box.set_margin_start(16)
        self.excl_box.set_margin_end(16)
        self.excl_box.set_margin_bottom(8)
        self._refresh_excludes()

        g = self._group(_("Shredding"))
        dd = Gtk.DropDown.new_from_strings([_("DoD 5220.22-M 3-pass method"), _("Single random pass")])
        dd.set_selected(0 if settings["shred_method"] == "dod3" else 1)
        dd.connect("notify::selected", lambda d, p: self._set("shred_method", "dod3" if d.get_selected() == 0
                                                              else "random1"))
        self._row(g, _("Default method"), _("The single random pass is faster and recommended on SSDs"), dd)
        self._switch(g, _("Verify final pass"), _("Read every block back and compare"), "verify")
        self._switch(g, _("Warn about SSDs before shredding"),
                     _("Explains why overwriting can't guarantee removal on solid-state drives"), "warn_ssd")
        self._switch(g, _("Remove thumbnails"), None, "trace_thumbnails")
        self._switch(g, _("Remove recent-file entries"), None, "trace_recent")
        self._switch(g, _("Shred copies in the Trash"), None, "trace_trash")
        adj = Gtk.Adjustment(value=settings["hold_seconds"], lower=1.0, upper=3.0, step_increment=0.5,
                             page_increment=0.5)
        spin = Gtk.SpinButton(adjustment=adj, digits=1)
        spin.connect("value-changed", lambda s: self._set("hold_seconds", round(s.get_value(), 1)))
        self._row(g, _("Hold-to-confirm duration"), _("Seconds to press and hold the Shred button"), spin)
        self._switch(g, _("Save shred reports"), None, "reports")
        self.report_label = Gtk.Label(label=pretty_path(settings["report_dir"]), ellipsize=Pango.EllipsizeMode.START,
                                      max_width_chars=26)
        pick = Gtk.Button()
        pick.set_child(self.report_label)
        pick.connect("clicked", self._pick_report_dir)
        self._row(g, _("Report folder"), None, pick)
        self._switch(g, _("Include file names in reports"), _("Off: names are stored as hashes"), "report_names")

        g = self._group(_("Appearance"))
        self._switch(g, _("Reduce motion"), _("Also follows your desktop's animation setting"), "reduce_motion")
        adj = Gtk.Adjustment(value=settings["rings"], lower=3, upper=6, step_increment=1, page_increment=1)
        sc2 = Gtk.SpinButton(adjustment=adj, digits=0)
        sc2.connect("value-changed", lambda s: self._set("rings", int(s.get_value())))
        self._row(g, _("Chart rings"), _("How many levels the sunburst shows at once"), sc2)

        g = self._group(_("Privacy and data"))
        note = Gtk.Label(label=_("Halo stores only these files, only on this computer. Nothing is ever sent "
                                 "anywhere."), xalign=0, wrap=True)
        note.add_css_class("caption")
        note.set_margin_start(16)
        note.set_margin_end(16)
        note.set_margin_top(10)
        g.append(note)
        self.data_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        g.append(self.data_box)
        clear = Gtk.Button(label=_("Clear Halo's Own Data…"))
        clear.add_css_class("danger")
        clear.connect("clicked", self._clear_own)
        self._row(g, _("Clear Halo's own data"), _("Shreds caches, logs and the recent-scan list"), clear)
        self._refresh_data()

    def _group(self, title):
        l = Gtk.Label(label=title.upper(), xalign=0)
        l.add_css_class("section")
        l.set_margin_top(12)
        l.set_margin_start(4)
        self.page.append(l)
        g = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        g.add_css_class("pref-group")
        self.page.append(g)
        return g

    def _row(self, group, title, sub, widget):
        row = Gtk.Box(spacing=12)
        row.add_css_class("pref-row")
        t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t.set_hexpand(True)
        t.set_valign(Gtk.Align.CENTER)
        a = Gtk.Label(label=title, xalign=0, wrap=True)
        a.add_css_class("pref-title")
        t.append(a)
        if sub:
            b = Gtk.Label(label=sub, xalign=0, wrap=True)
            b.add_css_class("pref-sub")
            t.append(b)
        row.append(t)
        widget.set_valign(Gtk.Align.CENTER)
        row.append(widget)
        group.append(row)
        return row

    def _switch(self, group, title, sub, key):
        sw = Gtk.Switch(active=bool(self.settings[key]))
        sw.connect("notify::active", lambda s, p: self._set(key, s.get_active()))
        return self._row(group, title, sub, sw)

    def _set(self, key, value):
        self.settings[key] = value

    def _refresh_excludes(self):
        c = self.excl_box.get_first_child()
        while c:
            n = c.get_next_sibling()
            self.excl_box.remove(c)
            c = n
        for p in self.settings["exclude"]:
            r = Gtk.Box(spacing=8)
            l = Gtk.Label(label=pretty_path(p), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            l.set_hexpand(True)
            l.add_css_class("caption")
            r.append(l)
            b = Gtk.Button.new_from_icon_name("list-remove-symbolic")
            b.add_css_class("flat")
            b.connect("clicked", lambda w, q=p: (self._set("exclude", [x for x in self.settings["exclude"]
                                                                       if x != q]), self._refresh_excludes()))
            r.append(b)
            self.excl_box.append(r)

    def _add_exclude(self, *a):
        Gtk.FileDialog(title=_("Exclude a folder")).select_folder(self, None, self._exclude_done)

    def _exclude_done(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path() and f.get_path() not in self.settings["exclude"]:
            self._set("exclude", self.settings["exclude"] + [f.get_path()])
            self._refresh_excludes()

    def _pick_report_dir(self, *a):
        Gtk.FileDialog(title=_("Report folder")).select_folder(self, None, self._report_done)

    def _report_done(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f and f.get_path():
            self._set("report_dir", f.get_path())
            self.report_label.set_label(pretty_path(f.get_path()))
            self._refresh_data()

    def _refresh_data(self):
        c = self.data_box.get_first_child()
        while c:
            n = c.get_next_sibling()
            self.data_box.remove(c)
            c = n
        for label, path, size in storage.data_locations(self.settings):
            r = Gtk.Box(spacing=12)
            r.add_css_class("pref-row")
            t = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            t.set_hexpand(True)
            a = Gtk.Label(label=_(label), xalign=0)
            a.add_css_class("pref-title")
            b = Gtk.Label(label=pretty_path(path), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
            b.add_css_class("pref-sub")
            t.append(a)
            t.append(b)
            r.append(t)
            s = Gtk.Label(label=format_size(size))
            s.add_css_class("tnum")
            s.add_css_class("caption")
            r.append(s)
            self.data_box.append(r)

    def _clear_own(self, *a):
        self.close()
        if self.parent_window is not None:
            self.parent_window.tool_clear_own_data()
