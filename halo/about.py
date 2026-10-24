# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""About Halo."""

from gi.repository import Gtk

from . import paths
from .util import _


def show_about(parent):
    d = Gtk.AboutDialog(transient_for=parent, modal=True)
    d.add_css_class("halo-dialog")
    d.set_program_name("Halo")
    d.set_version(paths.read_version())
    d.set_logo_icon_name(paths.APP_ID)
    d.set_comments(_("Disk space analyzer and secure file shredder for Linux Mint. "
                     "Halo collects no data and never connects to the internet."))
    d.set_authors(["%s <%s>" % (paths.DEV_NAME, paths.DEV_EMAIL)])
    d.set_copyright("© 2026 %s" % paths.DEV_NAME)
    d.set_license_type(Gtk.License.MIT_X11)
    d.set_website(paths.REPO_URL)
    d.set_website_label(_("Source code on GitHub"))
    d.add_credit_section(_("Built with"), ["GTK", "PyGObject", "Cairo", "Python"])
    d.present()
    return d
