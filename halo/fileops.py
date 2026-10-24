# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Everyday actions: Move to Trash, Move to…, Copy to…, Show in File Manager."""

import os
import shutil
import threading

from gi.repository import Gio, GLib

from .util import dry_run


def trash(paths, done):
    """Gio.File.trash() for each path on a worker thread. done(ok_paths, errors)."""
    def work():
        ok, errs = [], []
        for p in paths:
            if dry_run():
                ok.append(p)
                continue
            try:
                Gio.File.new_for_path(p).trash(None)
                ok.append(p)
            except GLib.Error as e:
                errs.append((p, e.message))
        GLib.idle_add(done, ok, errs)
    threading.Thread(target=work, daemon=True).start()


def _unique_target(dest_dir, name):
    t = os.path.join(dest_dir, name)
    if not os.path.lexists(t):
        return t
    base, ext = os.path.splitext(name)
    i = 2
    while True:
        t = os.path.join(dest_dir, "%s (%d)%s" % (base, i, ext))
        if not os.path.lexists(t):
            return t
        i += 1


def transfer(paths, dest_dir, move, progress, done, cancel=None):
    """Copy or move items into dest_dir. progress(i, n, name); done(ok, errors)."""
    cancel = cancel or threading.Event()

    def work():
        ok, errs = [], []
        n = len(paths)
        for i, p in enumerate(paths):
            if cancel.is_set():
                break
            GLib.idle_add(progress, i, n, os.path.basename(p))
            target = _unique_target(dest_dir, os.path.basename(p.rstrip("/")))
            if dry_run():
                ok.append((p, target))
                continue
            try:
                if move:
                    shutil.move(p, target)
                elif os.path.isdir(p) and not os.path.islink(p):
                    shutil.copytree(p, target, symlinks=True)
                else:
                    shutil.copy2(p, target, follow_symlinks=False)
                ok.append((p, target))
            except (OSError, shutil.Error) as e:
                errs.append((p, str(e)))
        GLib.idle_add(progress, n, n, "")
        GLib.idle_add(done, ok, errs)
    threading.Thread(target=work, daemon=True).start()
    return cancel


def show_in_file_manager(paths, parent_window=None):
    """org.freedesktop.FileManager1.ShowItems, so Nemo/Caja/Thunar highlight the file."""
    uris = [Gio.File.new_for_path(p).get_uri() for p in paths]
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                      "org.freedesktop.FileManager1", "ShowItems",
                      GLib.Variant("(ass)", (uris, "")), None, Gio.DBusCallFlags.NONE, 3000, None)
        return True
    except GLib.Error:
        pass
    try:
        from gi.repository import Gtk
        Gtk.FileLauncher.new(Gio.File.new_for_path(paths[0])).open_containing_folder(parent_window, None,
                                                                                    None, None)
        return True
    except Exception:
        return False


def open_folder(path, parent_window=None):
    try:
        from gi.repository import Gtk
        Gtk.FileLauncher.new(Gio.File.new_for_path(path)).launch(parent_window, None, None, None)
    except Exception:
        Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
