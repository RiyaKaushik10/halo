# Privacy Policy

*Halo 1.0 · effective 24 October 2026*

Halo is a disk space analyzer and secure file shredder that runs entirely on your computer. This policy explains, in plain language, what that means for your data.

## The short version

Halo collects nothing, sends nothing and needs no account. It contains no networking code at all.

## What Halo does not do

- It does not connect to the internet or to any other computer.
- It does not collect analytics, usage statistics, crash reports or telemetry.
- It does not check for updates or licences.
- It does not load remote fonts, images or help pages.
- It does not ask you to sign up, log in or register.

The only time anything leaves Halo is when **you** click a link — the GitHub page or the developer's email address. Halo hands that link to your own browser or mail app; it never opens a connection itself.

## What Halo stores on this computer

Halo keeps a few files in your own home folder, readable only by your user account (folders 0700, files 0600):

| Location | Contents | Removed by |
| --- | --- | --- |
| `~/.config/halo/settings.json` | Your preferences | Clear Halo's Own Data, or deleting the folder |
| `~/.cache/halo/scans/` | Folder names and sizes from past scans, and the recent-scan list | Clear Halo's Own Data (shredded) |
| `~/.local/state/halo/halo.log` | Errors and events, capped at 1 MB. Never contains the names of files you shred | Clear Halo's Own Data (shredded) |
| `~/Documents/Halo Reports/` (changeable) | Shred reports, only if reports are on. File names are stored as hashes unless you choose otherwise | You |

Uninstalling Halo removes the program but, like all Linux apps, leaves these per-user files. Use **Tools › Clear Halo's Own Data…** first if you want them gone.

## Administrator access

When a folder can't be read or a file belongs to another user, Halo can ask for your password through the standard Linux Mint prompt. Only a small helper program runs with administrator rights, only for that one job; the rest of Halo always runs as you.

## Children

Halo collects no personal information from anyone, including children.

## Changes

If this policy ever changes, the new version ships with the app and is listed in the release notes. Halo will never add data collection.

## Contact

Riya K · [riyak1@proton.me](mailto:riyak1@proton.me) · github.com/RiyaKaushik10/halo
