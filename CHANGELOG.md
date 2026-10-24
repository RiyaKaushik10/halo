# Changelog

## 1.0.1 — 2026-10-24

- Fix: buttons, checkboxes and radio buttons inside dialogs didn't respond to clicks.
- Fix: press-and-hold to shred stopped filling after a moment.
- Fix: the Shred… row of the right-click menu could be cut off.
- Dialogs have a close button; Esc still closes them.
- The SSD warning is off by default; turn it on in Preferences › Shredding.

## 1.0.0 — 2026-10-24

First release.

- Interactive sunburst with live scanning, radar sweep, hover cards, zoom animation, keyboard navigation, and colour by file kind or top folder.
- Outline, Biggest and Oldest lists sharing one selection with the chart.
- Accurate scanner: disk usage or apparent size, hard links counted once, symlinks never followed, one filesystem at a time, permission-error banner with admin rescan.
- Secure shredder: DoD 5220.22-M 3-pass method or single random pass, verification, name scrubbing, thumbnail / recent-file / Trash-copy cleanup, shred reports with hashed names.
- Honest warnings for SSDs, copy-on-write filesystems, data=journal, snapshots, sync folders and hard links; protected paths; press-and-hold confirmation; dry-run mode.
- Move to Trash, Move to…, Copy to…, Show in File Manager, drop zone.
- Menu bar, keyboard shortcuts window, preferences, first-run welcome, offline help, About dialog.
- Nemo actions, pkexec helper with polkit policy, `.deb` packaging.
