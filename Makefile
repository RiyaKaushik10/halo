# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
#
#   make test                         run the test suite
#   make run                          run from the checkout (dry-run mode)
#   make install PREFIX=/usr/local    install from the source tarball
#   make uninstall PREFIX=/usr/local
#   make deb                          build dist/halo_<version>_all.deb with dpkg-deb
#   dpkg-buildpackage -us -uc -b      the official Debian build (uses debian/)

PREFIX  ?= /usr
DESTDIR ?=
PYTHON  ?= python3

VERSION := $(shell sed -n '1s/.*(\([^)-]*\).*/\1/p' debian/changelog)
APPDIR   = $(PREFIX)/share/halo
LIBEXEC  = $(PREFIX)/libexec/halo
DOCDIR   = $(PREFIX)/share/doc/halo
ICONS    = $(PREFIX)/share/icons/hicolor
POLKIT   = /usr/share/polkit-1/actions
APP_ID   = io.github.RiyaKaushik10.Halo

PYFILES  = $(filter-out halo/_version.py,$(wildcard halo/*.py))
SUBST    = sed -e 's|@APPDIR@|$(APPDIR)|g' -e 's|/usr/libexec/halo/halo-helper|$(LIBEXEC)/halo-helper|g'

.PHONY: all version test run install uninstall deb clean

all: version

version:
	@printf '# SPDX-License-Identifier: MIT\n# Copyright (c) 2026 Riya K\n# Generated from debian/changelog at build time.\nVERSION = "%s"\n' "$(VERSION)" > halo/_version.py

test:
	$(PYTHON) -m pytest -q tests

run:
	HALO_DRY_RUN=1 $(PYTHON) -m halo

install: version
	install -d $(DESTDIR)$(APPDIR)/halo $(DESTDIR)$(APPDIR)/data/help
	install -m 0644 $(PYFILES) halo/_version.py halo/style.css $(DESTDIR)$(APPDIR)/halo/
	install -m 0644 data/help/*.md $(DESTDIR)$(APPDIR)/data/help/
	install -d $(DESTDIR)$(APPDIR)/data/icons/hicolor/scalable/apps \
	           $(DESTDIR)$(APPDIR)/data/icons/hicolor/scalable/actions \
	           $(DESTDIR)$(APPDIR)/data/icons/hicolor/symbolic/apps
	install -m 0644 data/icons/hicolor/scalable/apps/*.svg $(DESTDIR)$(APPDIR)/data/icons/hicolor/scalable/apps/
	install -m 0644 data/icons/hicolor/scalable/actions/*.svg $(DESTDIR)$(APPDIR)/data/icons/hicolor/scalable/actions/
	install -m 0644 data/icons/hicolor/symbolic/apps/*.svg $(DESTDIR)$(APPDIR)/data/icons/hicolor/symbolic/apps/
	install -d $(DESTDIR)$(PREFIX)/bin $(DESTDIR)$(LIBEXEC)
	$(SUBST) bin/halo > $(DESTDIR)$(PREFIX)/bin/halo && chmod 0755 $(DESTDIR)$(PREFIX)/bin/halo
	$(SUBST) helper/halo-helper > $(DESTDIR)$(LIBEXEC)/halo-helper && chmod 0755 $(DESTDIR)$(LIBEXEC)/halo-helper
	install -d $(DESTDIR)$(POLKIT)
	$(SUBST) data/io.github.riyakaushik10.halo.policy > $(DESTDIR)$(POLKIT)/io.github.riyakaushik10.halo.policy
	chmod 0644 $(DESTDIR)$(POLKIT)/io.github.riyakaushik10.halo.policy
	install -Dm 0644 data/$(APP_ID).desktop $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	install -Dm 0644 data/$(APP_ID).metainfo.xml $(DESTDIR)$(PREFIX)/share/metainfo/$(APP_ID).metainfo.xml
	install -Dm 0644 data/icons/hicolor/scalable/apps/$(APP_ID).svg $(DESTDIR)$(ICONS)/scalable/apps/$(APP_ID).svg
	install -Dm 0644 data/icons/hicolor/symbolic/apps/$(APP_ID)-symbolic.svg $(DESTDIR)$(ICONS)/symbolic/apps/$(APP_ID)-symbolic.svg
	install -d $(DESTDIR)$(ICONS)/scalable/actions
	install -m 0644 data/icons/hicolor/scalable/actions/halo-*-symbolic.svg $(DESTDIR)$(ICONS)/scalable/actions/
	install -d $(DESTDIR)$(PREFIX)/share/nemo/actions
	install -m 0644 data/nemo/*.nemo_action $(DESTDIR)$(PREFIX)/share/nemo/actions/
	install -d $(DESTDIR)$(DOCDIR) $(DESTDIR)$(APPDIR)/data/docs
	install -m 0644 PRIVACY.md TERMS.md LICENSE README.md $(DESTDIR)$(DOCDIR)/
	# private copies for the in-app reader (some systems exclude /usr/share/doc)
	install -m 0644 PRIVACY.md TERMS.md LICENSE $(DESTDIR)$(APPDIR)/data/docs/

uninstall:
	rm -rf $(DESTDIR)$(APPDIR) $(DESTDIR)$(LIBEXEC) $(DESTDIR)$(DOCDIR)
	rm -f $(DESTDIR)$(PREFIX)/bin/halo
	rm -f $(DESTDIR)$(POLKIT)/io.github.riyakaushik10.halo.policy
	rm -f $(DESTDIR)$(PREFIX)/share/applications/$(APP_ID).desktop
	rm -f $(DESTDIR)$(PREFIX)/share/metainfo/$(APP_ID).metainfo.xml
	rm -f $(DESTDIR)$(ICONS)/scalable/apps/$(APP_ID).svg $(DESTDIR)$(ICONS)/symbolic/apps/$(APP_ID)-symbolic.svg
	rm -f $(DESTDIR)$(ICONS)/scalable/actions/halo-shred-symbolic.svg \
	      $(DESTDIR)$(ICONS)/scalable/actions/halo-code-symbolic.svg \
	      $(DESTDIR)$(ICONS)/scalable/actions/halo-link-symbolic.svg
	rm -f $(DESTDIR)$(PREFIX)/share/nemo/actions/halo-analyze.nemo_action \
	      $(DESTDIR)$(PREFIX)/share/nemo/actions/halo-shred.nemo_action

# A .deb without debhelper, for machines that only have dpkg-deb.
deb:
	rm -rf build/pkg && mkdir -p build/pkg/DEBIAN dist
	$(MAKE) install DESTDIR=$(CURDIR)/build/pkg PREFIX=/usr
	install -Dm 0644 debian/copyright build/pkg/usr/share/doc/halo/copyright
	gzip -9nc debian/changelog > build/pkg/usr/share/doc/halo/changelog.gz
	chmod 0644 build/pkg/usr/share/doc/halo/changelog.gz
	sed -n '/^Package:/,$$p' debian/control \
	  | sed -e 's/\$${misc:Depends}, //' -e '/^Architecture:/a Version: $(VERSION)' \
	        -e '/^Package:/a Maintainer: Riya K <riyak1@proton.me>' \
	        -e '/^Architecture:/a Section: utils' -e '/^Architecture:/a Priority: optional' \
	        -e '/^Architecture:/a Homepage: https://github.com/RiyaKaushik10/halo' > build/pkg/DEBIAN/control
	echo "Installed-Size: $$(du -sk build/pkg/usr | cut -f1)" >> build/pkg/DEBIAN/control
	cd build/pkg && find usr -type f -exec md5sum {} + > DEBIAN/md5sums
	dpkg-deb --build --root-owner-group build/pkg dist/halo_$(VERSION)_all.deb
	cd dist && sha256sum halo_$(VERSION)_all.deb > SHA256SUMS

clean:
	rm -rf build dist halo/_version.py
	find . -name __pycache__ -prune -exec rm -rf {} +
