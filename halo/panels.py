# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Left sidebar (drives, favourites, recent scans) and the right panel
(Outline / Biggest / Oldest lists, selection footer, drop zone, actions)."""

import os
import time
import weakref

from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from . import safety, storage
from . import theme as T
from .scanner import DIR, FILES, LINK, OTHER_DRIVE
from .util import _, display_name, format_items, format_size, pretty_path, time_ago


def section_header(text, extra=None):
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    box.add_css_class("section-head")
    lbl = Gtk.Label(label=text.upper(), xalign=0)
    lbl.add_css_class("section")
    box.append(lbl)
    rule = Gtk.Box()
    rule.add_css_class("section-rule")
    rule.set_hexpand(True)
    rule.set_valign(Gtk.Align.CENTER)
    box.append(rule)
    if extra is not None:
        extra.set_margin_start(8)
        box.append(extra)
    box._label = lbl
    box._rule = rule
    return box


def icon(name, size=16, css=None):
    im = Gtk.Image.new_from_icon_name(name)
    im.set_pixel_size(size)
    if css:
        for c in css.split():
            im.add_css_class(c)
    return im


def folder_icon_for(path):
    h = os.path.realpath(os.path.expanduser("~"))
    special = {}
    try:
        for ud, name in ((GLib.UserDirectory.DIRECTORY_DOWNLOAD, "folder-download-symbolic"),
                         (GLib.UserDirectory.DIRECTORY_PICTURES, "folder-pictures-symbolic"),
                         (GLib.UserDirectory.DIRECTORY_VIDEOS, "folder-videos-symbolic"),
                         (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "folder-documents-symbolic"),
                         (GLib.UserDirectory.DIRECTORY_MUSIC, "folder-music-symbolic")):
            d = GLib.get_user_special_dir(ud)
            if d:
                special[d] = name
    except Exception:
        pass
    if path == h:
        return "user-home-symbolic"
    return special.get(path, "folder-symbolic")


# ── Drives ────────────────────────────────────────────────────────────────
class Drive:
    def __init__(self, name, path, removable=False):
        self.name, self.path, self.removable = name, path, removable
        self.free = self.total = 0
        try:
            sv = os.statvfs(path)
            self.total = sv.f_blocks * sv.f_frsize
            self.free = sv.f_bavail * sv.f_frsize
        except OSError:
            pass
        info = safety.fs_info(path)
        self.badge = info.drive_badge
        self.network = info.network


def list_drives():
    drives = [Drive(_("File System"), "/")]
    seen = {"/"}
    h = os.path.realpath(os.path.expanduser("~"))
    # a separate /home partition gets its own row
    m = safety.mount_for(h)
    if m is not None and m.point not in seen and m.point != "/":
        drives.append(Drive(_("Home drive"), m.point))
        seen.add(m.point)
    try:
        mon = Gio.VolumeMonitor.get()
        for mount in mon.get_mounts():
            root = mount.get_root()
            p = root.get_path() if root else None
            if not p or p in seen:
                continue
            seen.add(p)
            drives.append(Drive(mount.get_name(), p, removable=mount.can_eject() or mount.can_unmount()))
    except Exception:
        pass
    return drives


class DriveRow(Gtk.Box):
    def __init__(self, drive, on_scan):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.drive = drive
        self.add_css_class("drive-row")
        self.set_tooltip_text(drive.path)
        ic = icon(T.REMOVABLE_ICON if drive.removable else T.DRIVE_ICON, 30, "drive-icon")
        ic.set_valign(Gtk.Align.CENTER)
        self.icon = ic
        self.append(ic)

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        mid.set_hexpand(True)
        top = Gtk.Box(spacing=6)
        name = Gtk.Label(label=drive.name, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("drive-name")
        top.append(name)
        if drive.badge:
            b = Gtk.Label(label=drive.badge)
            b.add_css_class("badge")
            b.add_css_class(drive.badge.lower())
            b.set_valign(Gtk.Align.CENTER)
            b.set_tooltip_text(_("Solid-state drive") if drive.badge == "SSD" else _("Spinning hard disk"))
            top.append(b)
        mid.append(top)
        free = Gtk.Label(label=_("%s free of %s") % (format_size(drive.free), format_size(drive.total)),
                         xalign=0)
        free.add_css_class("drive-free")
        free.add_css_class("tnum")
        mid.append(free)
        bar = Gtk.ProgressBar()
        bar.add_css_class("usage")
        used = 1 - (drive.free / drive.total) if drive.total else 0
        bar.set_fraction(used)
        if used > 0.9:
            bar.add_css_class("full")
        bar.set_margin_top(3)
        mid.append(bar)
        self.mid = mid
        self.append(mid)

        play = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        play.add_css_class("play")
        play.set_valign(Gtk.Align.CENTER)
        play.set_tooltip_text(_("Scan %s") % drive.name)
        play.update_property([Gtk.AccessibleProperty.LABEL], [_("Scan %s") % drive.name])
        play.connect("clicked", lambda *a: on_scan(drive.path))
        self.play = play
        self.append(play)

        click = Gtk.GestureClick()
        click.connect("released", lambda g, n, x, y: on_scan(drive.path))
        self.add_controller(click)

    def set_rail(self, rail):
        self.mid.set_visible(not rail)
        self.play.set_visible(not rail)


class NavRow(Gtk.Box):
    def __init__(self, path, meta=None, on_activate=None, icon_name=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.path = path
        self.add_css_class("nav-row")
        tile = Gtk.Box()
        tile.add_css_class("folder-tile")
        im = icon(icon_name or folder_icon_for(path), 16)
        im.set_hexpand(True)
        im.set_halign(Gtk.Align.CENTER)
        im.set_valign(Gtk.Align.CENTER)
        tile.append(im)
        tile.set_hexpand(False)
        tile.set_valign(Gtk.Align.CENTER)
        self.append(tile)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        name = Gtk.Label(label=display_name(path), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        name.add_css_class("nav-name")
        texts.append(name)
        if meta:
            m = Gtk.Label(label=meta, xalign=0, ellipsize=Pango.EllipsizeMode.END)
            m.add_css_class("nav-meta")
            m.add_css_class("tnum")
            texts.append(m)
        self.texts = texts
        self.append(texts)
        self.set_tooltip_text(pretty_path(path))
        if on_activate:
            click = Gtk.GestureClick()
            click.connect("released", lambda g, n, x, y: on_activate(path))
            self.add_controller(click)

    def set_rail(self, rail):
        self.texts.set_visible(not rail)


class Sidebar(Gtk.Box):
    __gsignals__ = {"scan-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,))}

    def __init__(self, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.settings = settings
        self.add_css_class("sidebar")
        self.set_size_request(T.SIDEBAR_WIDTH, -1)
        self.rail = False
        self.active_path = None
        self.remove_mode = False

        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_vexpand(True)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sc.set_child(self.body)
        self.append(sc)

        self.storage_head = section_header(_("Storage"))
        self.storage_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.fav_head = section_header(_("Favourite folders"))
        self.fav_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.recent_head = section_header(_("Recent scans"))
        self.recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for w in (self.storage_head, self.storage_box, self.fav_head, self.fav_box, self.recent_head,
                  self.recent_box):
            self.body.append(w)
        self.recent_box.set_margin_bottom(12)

        foot = Gtk.Box(spacing=4)
        foot.add_css_class("sidebar-foot")
        self.add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.add_btn.add_css_class("flat")
        self.add_btn.add_css_class("icon-button")
        self.add_btn.set_tooltip_text(_("Add a favourite folder"))
        self.add_btn.connect("clicked", self._on_add)
        self.rm_btn = Gtk.Button.new_from_icon_name("list-remove-symbolic")
        self.rm_btn.add_css_class("flat")
        self.rm_btn.add_css_class("icon-button")
        self.rm_btn.set_tooltip_text(_("Remove a favourite folder"))
        self.rm_btn.connect("clicked", self._on_remove_mode)
        self.rm_hint = Gtk.Label(label="", xalign=0)
        self.rm_hint.add_css_class("caption")
        self.rm_hint.set_hexpand(True)
        self.rm_hint.set_margin_start(6)
        foot.append(self.add_btn)
        foot.append(self.rm_btn)
        foot.append(self.rm_hint)
        self.foot = foot
        self.append(foot)

        self._mon = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "mount-changed"):
            self._mon.connect(sig, lambda *a: GLib.timeout_add(400, self._refresh_drives_once))
        self.refresh()
        GLib.timeout_add_seconds(60, self._tick)

    def _tick(self):
        self.refresh_recent()
        return True

    def _refresh_drives_once(self):
        self.refresh_drives()
        return False

    def refresh(self):
        self.refresh_drives()
        self.refresh_favourites()
        self.refresh_recent()

    def _clear(self, box):
        c = box.get_first_child()
        while c is not None:
            n = c.get_next_sibling()
            box.remove(c)
            c = n

    def refresh_drives(self):
        self._clear(self.storage_box)
        for d in list_drives():
            row = DriveRow(d, lambda p: self.emit("scan-requested", p))
            row.set_rail(self.rail)
            self.storage_box.append(row)
        self.set_active(self.active_path)

    def refresh_favourites(self):
        self._clear(self.fav_box)
        for p in self.settings["favourites"]:
            row = NavRow(p, None, self._on_fav)
            row.set_rail(self.rail)
            if self.remove_mode:
                row.add_css_class("removing")
            self.fav_box.append(row)
        self.set_active(self.active_path)

    def refresh_recent(self):
        self._clear(self.recent_box)
        items = storage.load_recent()
        if not items:
            lbl = Gtk.Label(label=_("Scans you run appear here"), xalign=0)
            lbl.add_css_class("caption")
            lbl.set_margin_start(24)
            lbl.set_visible(not self.rail)
            self.recent_box.append(lbl)
        for r in items:
            meta = "%s · %s" % (format_size(r.get("size", 0)), time_ago(r.get("time", time.time())))
            row = NavRow(r["path"], meta, lambda p: self.emit("scan-requested", p), "document-open-recent-symbolic")
            row.set_rail(self.rail)
            self.recent_box.append(row)

    def _on_fav(self, path):
        if self.remove_mode:
            favs = [f for f in self.settings["favourites"] if f != path]
            self.settings["favourites"] = favs
            self._on_remove_mode()
            return
        self.emit("scan-requested", path)

    def _on_add(self, *a):
        dlg = Gtk.FileDialog(title=_("Add a favourite folder"))
        dlg.select_folder(self.get_root(), None, self._on_add_done)

    def _on_add_done(self, dlg, res):
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        p = f.get_path() if f else None
        if p and p not in self.settings["favourites"]:
            self.settings["favourites"] = self.settings["favourites"] + [p]
            self.refresh_favourites()

    def _on_remove_mode(self, *a):
        self.remove_mode = not self.remove_mode
        self.rm_hint.set_label(_("Click a favourite to remove it") if self.remove_mode else "")
        self.rm_btn.add_css_class("suggested") if self.remove_mode else self.rm_btn.remove_css_class("suggested")
        self.refresh_favourites()

    def set_active(self, path):
        self.active_path = path
        for box in (self.storage_box, self.fav_box):
            c = box.get_first_child()
            while c is not None:
                p = getattr(c, "path", None) or (c.drive.path if hasattr(c, "drive") else None)
                if p is not None and p == path:
                    c.add_css_class("active")
                else:
                    c.remove_css_class("active")
                c = c.get_next_sibling()

    def set_scanning(self, path, scanning):
        c = self.storage_box.get_first_child()
        while c is not None:
            if hasattr(c, "play"):
                on = scanning and c.drive.path == path
                c.play.set_icon_name("media-playback-stop-symbolic" if on else "media-playback-start-symbolic")
                if on:
                    c.play.add_css_class("scanning")
                else:
                    c.play.remove_css_class("scanning")
            c = c.get_next_sibling()

    def set_rail(self, rail):
        if rail == self.rail:
            return
        self.rail = rail
        self.set_size_request(T.RAIL_WIDTH if rail else T.SIDEBAR_WIDTH, -1)
        if rail:
            self.add_css_class("rail")
        else:
            self.remove_css_class("rail")
        for head in (self.storage_head, self.fav_head, self.recent_head):
            head._label.set_visible(not rail)
        self.rm_hint.set_visible(not rail)
        self.foot.set_orientation(Gtk.Orientation.VERTICAL if rail else Gtk.Orientation.HORIZONTAL)
        self.refresh()


# ── Right panel rows ─────────────────────────────────────────────────────
class RowItem(GObject.Object):
    __gtype_name__ = "HaloRowItem"

    def __init__(self, node, mode, share_of=None, last_used=None):
        super().__init__()
        self.node = node
        self.mode = mode
        self.share_of = share_of
        self.last_used = last_used


def node_icon(node):
    if node.flags & OTHER_DRIVE:
        return T.DRIVE_ICON, "kind-other-drive"
    if node.flags & LINK:
        return T.LINK_ICON, "kind-link"
    if node.flags & DIR:
        return T.FOLDER_ICON, "kind-folder"
    k = node.dominant_kind()
    return T.KIND_ICONS[k], "kind-%d" % k


class ShareBar(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_content_height(3)
        self.fraction = 0.0
        self.color = T.hex_to_rgb(T.KIND_COLORS[0])
        self.set_draw_func(self._draw)

    def set(self, fraction, color):
        self.fraction = max(0.0, min(1.0, fraction))
        self.color = color
        self.queue_draw()

    def _draw(self, area, cr, w, h):
        cr.set_source_rgba(*T.rgb("line"), 0.6)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgb(*self.color)
        cr.rectangle(0, 0, max(2 if self.fraction > 0 else 0, w * self.fraction), h)
        cr.fill()


class FileRow(Gtk.Box):
    def __init__(self, panel, mode):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.panel = panel
        self.mode = mode
        self.item = None
        self.tree_row = None
        main = Gtk.Box(spacing=10)
        main.add_css_class("file-row")
        main.set_vexpand(True)
        self.main = main
        self.check = Gtk.CheckButton()
        self.check.set_valign(Gtk.Align.CENTER)
        self._check_handler = self.check.connect("toggled", self._on_toggled)
        main.append(self.check)

        content = Gtk.Box(spacing=10)
        content.set_hexpand(True)
        self.tile = Gtk.Box()
        self.tile.add_css_class("kind-tile")
        self.tile.set_valign(Gtk.Align.CENTER)
        self.image = Gtk.Image()
        self.image.set_pixel_size(16)
        self.image.set_hexpand(True)
        self.image.set_halign(Gtk.Align.CENTER)
        self.image.set_valign(Gtk.Align.CENTER)
        self.tile.append(self.image)
        self.tile.set_hexpand(False)
        content.append(self.tile)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        texts.set_valign(Gtk.Align.CENTER)
        texts.set_hexpand(True)
        self.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        self.name.add_css_class("row-name")
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START)
        self.sub.add_css_class("row-sub")
        texts.append(self.name)
        texts.append(self.sub)
        content.append(texts)

        if mode == "outline":
            self.expander = Gtk.TreeExpander()
            self.expander.set_child(content)
            self.expander.set_hexpand(True)
            main.append(self.expander)
        else:
            self.expander = None
            main.append(content)
        self.size = Gtk.Label(xalign=1)
        self.size.add_css_class("size")
        self.size.add_css_class("tnum")
        self.size.set_valign(Gtk.Align.CENTER)
        main.append(self.size)
        self.append(main)
        self.bar = ShareBar()
        self.append(self.bar)

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)
        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drag.connect("prepare", self._on_drag_prepare)
        self.add_controller(drag)

    def bind(self, item, tree_row=None):
        self.item = item
        self.tree_row = tree_row
        n = item.node
        if self.expander is not None:
            self.expander.set_list_row(tree_row)
        icon_name, css = node_icon(n)
        self.image.set_from_icon_name(icon_name)
        for c in list(self.image.get_css_classes()):
            self.image.remove_css_class(c)
        self.image.add_css_class(css)
        if n.flags & DIR:
            self.tile.add_css_class("folder")
        else:
            self.tile.remove_css_class("folder")
        self.name.set_label(n.label())
        if item.mode == "outline":
            if n.flags & FILES:
                self.sub.set_label(format_items(n.count))
                self.sub.set_visible(True)
            elif n.flags & OTHER_DRIVE:
                self.sub.set_label(_("Other drive · scan separately"))
                self.sub.set_visible(True)
            elif n.flags & LINK:
                self.sub.set_label(_("Link · not followed"))
                self.sub.set_visible(True)
            else:
                self.sub.set_visible(False)
            parent_size = n.parent.size if n.parent is not None else n.size
        elif item.mode == "biggest":
            self.sub.set_label(pretty_path(os.path.dirname(n.path())))
            self.sub.set_visible(True)
            parent_size = item.share_of or n.size
        else:
            self.sub.set_label(_("Last used %s") % time_ago(item.last_used or n.mtime))
            self.sub.set_visible(True)
            parent_size = item.share_of or n.size
        self.size.set_label(format_size(n.size) if not (n.flags & (LINK | OTHER_DRIVE)) else "—")
        frac = n.size / parent_size if parent_size else 0
        if n.flags & OTHER_DRIVE:
            col = T.rgb("line")
        elif self.panel.color_mode == "folder" and item.mode == "outline":
            col = T.hex_to_rgb(T.TOP_FOLDER_COLORS[self.panel.top_index(n) % len(T.TOP_FOLDER_COLORS)])
        else:
            col = T.hex_to_rgb(T.KIND_COLORS[n.dominant_kind()])
        self.bar.set(frac, col)
        reason = self.panel.reason_for(n)
        self.check.set_sensitive(not (n.flags & OTHER_DRIVE))
        if reason:
            self.main.add_css_class("protected")
            self.set_tooltip_text(reason)
        else:
            self.main.remove_css_class("protected")
            self.set_tooltip_text(None)
        self.sync()

    def sync(self):
        if self.item is None:
            return
        n = self.item.node
        sel = self.panel.selection
        on = n in sel
        implied = (not on) and sel.implied(n)
        self.check.handler_block(self._check_handler)
        self.check.set_active(on or implied)
        self.check.handler_unblock(self._check_handler)
        if implied:
            self.check.add_css_class("implied")
        else:
            self.check.remove_css_class("implied")
        if on:
            self.main.add_css_class("ticked")
        else:
            self.main.remove_css_class("ticked")

    def _on_toggled(self, btn):
        if self.item is None:
            return
        n = self.item.node
        sel = self.panel.selection
        self.panel.origin = "list"
        if sel.implied(n) and n not in sel:
            # unticking a child of a selected folder: replace folder by its other children
            self.panel.untick_implied(n)
        else:
            sel.toggle(n)
        self.panel.origin = None

    def _on_pressed(self, gesture, n_press, x, y):
        if self.item is None:
            return
        btn = gesture.get_current_button()
        n = self.item.node
        sel = self.panel.selection
        state = gesture.get_current_event_state()
        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if btn == 1 and picked is not None and (picked is self.check or picked.is_ancestor(self.check)):
            return   # the checkbox handles itself
        if btn == 3:
            if n not in sel and not (n.flags & OTHER_DRIVE):
                self.panel.origin = "list"
                sel.set([n])
                self.panel.origin = None
            self.panel.emit("context-menu", self, x, y, n)
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if btn != 1:
            return
        if n_press == 2:
            self.panel.emit("activate-node", n)
            return
        if n.flags & OTHER_DRIVE:
            self.panel.emit("activate-node", n)
            return
        self.panel.origin = "list"
        if state & Gdk.ModifierType.CONTROL_MASK:
            sel.toggle(n)
        else:
            sel.set([n])
        self.panel.origin = None

    def _on_drag_prepare(self, source, x, y):
        if self.item is None:
            return None
        n = self.item.node
        sel = self.panel.selection
        if n not in sel:
            sel.set([n])
        files = [Gio.File.new_for_path(m.path()) for m in sel.top_level() if not (m.flags & FILES)]
        if not files:
            return None
        return Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(files))


class RightPanel(Gtk.Box):
    __gsignals__ = {
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float, object)),
        "activate-node": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "dropped": (GObject.SignalFlags.RUN_FIRST, None, (object, float, float)),
        "tab-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, selection, settings):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.selection = selection
        self.settings = settings
        self.add_css_class("panel")
        self.set_size_request(T.PANEL_WIDTH, -1)
        self.center = None
        self.result = None
        self.file_nodes = None
        self.color_mode = settings["color_mode"]
        self.show_hidden = settings["show_hidden"]
        self.size_mode = settings["size_mode"]
        self.origin = None
        self._rows = weakref.WeakSet()
        self._reason_cache = {}
        self._mounts = safety.read_mounts()

        # header
        head = Gtk.Box(spacing=12)
        head.add_css_class("panel-head")
        self.head_icon = icon(T.DRIVE_ICON, 30, "panel-icon")
        head.append(self.head_icon)
        self.head_title = Gtk.Label(label=_("Nothing scanned"), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.head_title.add_css_class("panel-title")
        self.head_title.set_hexpand(True)
        head.append(self.head_title)
        self.head_size = Gtk.Label(label="")
        self.head_size.add_css_class("panel-size")
        head.append(self.head_size)
        self.append(head)

        # tabs
        tabs = Gtk.Box(homogeneous=True)
        tabs.add_css_class("segmented")
        self.tab_buttons = {}
        group = None
        for key, label in (("outline", _("Outline")), ("biggest", _("Biggest")), ("oldest", _("Oldest"))):
            b = Gtk.ToggleButton(label=label)
            if group is not None:
                b.set_group(group)
            else:
                group = b
            b.connect("toggled", self._on_tab, key)
            tabs.append(b)
            self.tab_buttons[key] = b
        tw = Gtk.Box()
        tw.add_css_class("panel-tabs")
        tabs.set_hexpand(True)
        tw.append(tabs)
        self.append(tw)

        self.caption = Gtk.Box()
        self.caption.add_css_class("list-caption")
        self.caption_left = Gtk.Label(label="", xalign=0)
        self.caption_left.set_hexpand(True)
        self.caption_right = Gtk.Label(label="", xalign=1)
        self.caption.append(self.caption_left)
        self.caption.append(self.caption_right)
        self.append(self.caption)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=T.VIEW_MS)
        self.stack.set_vexpand(True)
        self.outline_view = self._make_list("outline")
        self.biggest_view = self._make_list("biggest")
        self.oldest_view = self._make_list("oldest")
        for key, (sw, lv) in (("outline", self.outline_view), ("biggest", self.biggest_view),
                              ("oldest", self.oldest_view)):
            self.stack.add_named(sw, key)
        self.empty = Gtk.Label(label=_("Pick a drive or folder on the left to see where your space went."),
                               wrap=True, justify=Gtk.Justification.CENTER)
        self.empty.add_css_class("caption")
        self.empty.set_margin_start(30)
        self.empty.set_margin_end(30)
        self.stack.add_named(self.empty, "empty")
        self.append(self.stack)

        # footer
        foot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        foot.add_css_class("panel-foot")
        strip = Gtk.Box()
        strip.add_css_class("selected-strip")
        self.sel_label = Gtk.Label(label=_("Selected: 0 items"), xalign=0)
        self.sel_label.set_hexpand(True)
        strip.append(self.sel_label)
        self.clear_btn = Gtk.Button(label=_("Clear"))
        self.clear_btn.add_css_class("link-button")
        self.clear_btn.set_action_name("win.clear-selection")
        strip.append(self.clear_btn)
        foot.append(strip)

        drop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        drop.add_css_class("drop-zone")
        t = Gtk.Label(label=_("Drop files here"))
        t.add_css_class("drop-title")
        s = Gtk.Label(label=_("to shred, trash or move"))
        s.add_css_class("drop-sub")
        drop.append(t)
        drop.append(s)
        target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        target.connect("enter", lambda *a: (drop.add_css_class("hover"), Gdk.DragAction.COPY)[1])
        target.connect("leave", lambda *a: drop.remove_css_class("hover"))
        target.connect("drop", self._on_drop)
        drop.add_controller(target)
        self.drop = drop
        foot.append(drop)

        actions = Gtk.Box(spacing=10)
        actions.add_css_class("action-row")
        self.trash_btn = Gtk.Button(label=_("Move to Trash"))
        self.trash_btn.set_hexpand(True)
        self.trash_btn.set_action_name("win.trash")
        actions.append(self.trash_btn)
        split = Gtk.Box()
        split.add_css_class("split-danger")
        split.set_hexpand(True)
        self.shred_btn = Gtk.Button()
        sb = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        sb.append(icon("halo-shred-symbolic", 16))
        self.shred_label = Gtk.Label(label=_("Shred…"))
        sb.append(self.shred_label)
        self.shred_btn.set_child(sb)
        self.shred_btn.add_css_class("danger")
        self.shred_btn.set_hexpand(True)
        self.shred_btn.set_action_name("win.shred")
        split.append(self.shred_btn)
        more = Gio.Menu()
        more.append(_("Move to…"), "win.move-to")
        more.append(_("Copy to…"), "win.copy-to")
        mb = Gtk.MenuButton()
        mb.set_icon_name("pan-down-symbolic")
        mb.set_menu_model(more)
        mb.set_tooltip_text(_("More actions"))
        mb.get_first_child().add_css_class("danger")
        self.more_btn = mb
        split.append(mb)
        actions.append(split)
        foot.append(actions)
        self.append(foot)

        selection.connect("changed", self._on_selection)
        tab = settings["last_tab"] if settings["last_tab"] in self.tab_buttons else "outline"
        self.tab_buttons[tab].set_active(True)
        self.stack.set_visible_child_name("empty")
        self._on_selection()

    # lists
    def _make_list(self, mode):
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", lambda f, li: li.set_child(self._new_row(mode)))
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", lambda f, li: None)
        lv = Gtk.ListView(factory=factory)
        lv.add_css_class("halo-list")
        lv.set_single_click_activate(False)
        sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sw.set_child(lv)
        sw.set_vexpand(True)
        return sw, lv

    def _new_row(self, mode):
        r = FileRow(self, mode)
        self._rows.add(r)
        return r

    def _bind_row(self, factory, li):
        obj = li.get_item()
        row = li.get_child()
        if isinstance(obj, Gtk.TreeListRow):
            row.bind(obj.get_item(), obj)
        else:
            row.bind(obj, None)

    def reason_for(self, node):
        if node.flags & FILES:
            p = node.path()
            key = ("files", p)
        else:
            p = node.path()
            key = p
        r = self._reason_cache.get(key)
        if r is None:
            if node.flags & FILES:
                r = safety.protection_reason(os.path.join(p, "x"), self._mounts) or ""
            else:
                r = safety.trash_block_reason(p, self._mounts) or ""
            self._reason_cache[key] = r
        return r or None

    def top_index(self, node):
        n = node
        while n.parent is not None and n.parent is not self.center:
            n = n.parent
        if n.parent is None:
            return 0
        kids = self.center.sorted_children(self.show_hidden)
        try:
            return kids.index(n)
        except ValueError:
            return 0

    def _children_model(self, node):
        store = Gio.ListStore(item_type=RowItem)
        if node.flags & FILES:
            if self.file_nodes is not None and node.parent is not None:
                for f in self.file_nodes.list_folder(node.parent, self.size_mode, self.show_hidden):
                    store.append(RowItem(f, "outline"))
        else:
            for c in node.sorted_children(self.show_hidden):
                store.append(RowItem(c, "outline"))
        return store

    def _create_child_model(self, item):
        n = item.node
        if n.flags & FILES:
            return self._children_model(n) if n.count > 0 else None
        if n.flags & DIR and n.children:
            return self._children_model(n)
        return None

    def set_result(self, result, file_nodes):
        self.result = result
        self.file_nodes = file_nodes
        self._reason_cache = {}
        self._mounts = safety.read_mounts()

    def set_center(self, node):
        self.center = node
        if node is None:
            self.stack.set_visible_child_name("empty")
            self.head_title.set_label(_("Nothing scanned"))
            self.head_size.set_label("")
            return
        name = node.label() if node.parent is not None else display_name(node.path())
        self.head_title.set_label(name)
        self.head_size.set_label(format_size(node.size))
        if node.parent is None:
            self.head_icon.set_from_icon_name(T.DRIVE_ICON if os.path.ismount(node.path())
                                              else folder_icon_for(node.path()))
        else:
            self.head_icon.set_from_icon_name(folder_icon_for(node.path()))
        self.refresh_lists()

    def refresh_header(self):
        if self.center is not None:
            self.head_size.set_label(format_size(self.center.size))

    def refresh_lists(self):
        if self.center is None:
            return
        root_store = self._children_model(self.center)
        tree = Gtk.TreeListModel.new(root_store, False, False, self._create_child_model)
        self.outline_model = tree
        self.outline_view[1].set_model(Gtk.NoSelection(model=tree))
        self.refresh_files()
        cur = self.current_tab()
        self.stack.set_visible_child_name(cur)
        self._update_caption()

    def refresh_files(self):
        if self.center is None or self.result is None:
            return
        cpath = self.center.path()
        big = Gio.ListStore(item_type=RowItem)
        for path, size, kind, last in self.result.biggest_under(cpath, 200):
            if not self.show_hidden and "/." in path[len(cpath):]:
                continue
            fn = self.file_nodes.get(path, size, kind, last)
            big.append(RowItem(fn, "biggest", share_of=self.center.size))
        self.biggest_view[1].set_model(Gtk.NoSelection(model=big))
        old = Gio.ListStore(item_type=RowItem)
        for path, size, kind, last in self.result.oldest_under(cpath, 200):
            if not self.show_hidden and "/." in path[len(cpath):]:
                continue
            fn = self.file_nodes.get(path, size, kind, last)
            old.append(RowItem(fn, "oldest", share_of=self.center.size, last_used=last))
        self.oldest_view[1].set_model(Gtk.NoSelection(model=old))
        self._counts = {"biggest": big.get_n_items(), "oldest": old.get_n_items()}
        self._update_caption()

    def current_tab(self):
        for k, b in self.tab_buttons.items():
            if b.get_active():
                return k
        return "outline"

    def set_tab(self, key):
        if key in self.tab_buttons:
            self.tab_buttons[key].set_active(True)

    def _on_tab(self, btn, key):
        if not btn.get_active():
            return
        if self.center is not None:
            self.stack.set_visible_child_name(key)
        self.settings["last_tab"] = key
        self._update_caption()
        self.emit("tab-changed", key)

    def _update_caption(self):
        tab = self.current_tab()
        counts = getattr(self, "_counts", {})
        if self.center is None:
            self.caption.set_visible(False)
            return
        self.caption.set_visible(True)
        if tab == "outline":
            self.caption_left.set_label(_("NAME"))
            self.caption_right.set_label(_("SIZE"))
        elif tab == "biggest":
            self.caption_left.set_label(_("TOP %d FILES") % counts.get("biggest", 0))
            self.caption_right.set_label(_("SIZE"))
        else:
            self.caption_left.set_label(_("LAST USED (APPROX.) · OVER 10 MB"))
            self.caption_right.set_label(_("SIZE"))

    def sync_rows(self):
        for r in list(self._rows):
            r.sync()

    def rebind_rows(self):
        for r in list(self._rows):
            if r.item is not None:
                r.bind(r.item, r.tree_row)

    def _on_selection(self, *a):
        n = self.selection.total_count()
        if n == 0:
            self.sel_label.set_label(_("Selected: 0 items"))
        else:
            self.sel_label.set_label(_("Selected: %s · %s") % (format_items(n),
                                                               format_size(self.selection.total_size())))
        self.clear_btn.set_visible(n > 0)
        self.more_btn.set_sensitive(n > 0)
        self.sync_rows()
        if self.origin != "list" and n > 0 and self.current_tab() == "outline":
            GLib.idle_add(self.reveal, self.selection.nodes()[-1])

    def untick_implied(self, node):
        """Unticking something inside a selected folder selects its siblings instead."""
        sel = self.selection
        sel.freeze()
        anc = None
        for a in node.ancestors():
            if a in sel:
                anc = a
                break
        if anc is not None:
            sel.remove(anc)
            # walk down from anc to node, selecting every sibling on the way
            chain = []
            m = node
            while m is not anc:
                chain.append(m)
                m = m.parent
            for m in reversed(chain):
                for s in m.parent.sorted_children(self.show_hidden):
                    if s is not m and not (s.flags & OTHER_DRIVE):
                        sel.add(s)
        sel.thaw()

    def reveal(self, node):
        if self.center is None or not hasattr(self, "outline_model") or node is None:
            return False
        if node is self.center:
            return False
        chain = []
        m = node
        while m is not None and m is not self.center:
            chain.append(m)
            m = m.parent
        if m is None:
            return False
        model = self.outline_model
        pos = None
        for target in reversed(chain):
            found = None
            for i in range(model.get_n_items()):
                row = model.get_item(i)
                it = row.get_item()
                if it.node is target or (target.flags == 0 and it.node.flags & FILES and
                                         target.parent is it.node.parent):
                    found = (i, row)
                    if it.node is target:
                        break
            if found is None:
                break
            pos, row = found
            if target is not node and row.is_expandable():
                row.set_expanded(True)
        if pos is not None:
            try:
                self.outline_view[1].scroll_to(pos, Gtk.ListScrollFlags.NONE, None)
            except Exception:
                pass
        return False

    def _on_drop(self, target, value, x, y):
        self.drop.remove_css_class("hover")
        files = value.get_files() if value is not None else []
        paths = [f.get_path() for f in files if f.get_path()]
        if paths:
            self.emit("dropped", paths, x, y)
        return True

    def set_shred_label(self, text):
        self.shred_label.set_label(text)
