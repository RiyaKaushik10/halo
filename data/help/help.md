# Halo Help

Halo shows what is filling your disk as a sunburst, and lets you securely shred what you find — including the thumbnails and recent-file entries your desktop leaves behind. Everything happens on this computer. Halo has no account, no tracking and no internet connection.

## Scanning

- Pick a drive under **Storage** and press its round play button, or click a **Favourite folder**.
- **File › Scan Folder…** (Ctrl+O) scans any folder you choose. **Rescan** (Ctrl+R or F5) refreshes the current one; **Stop Scan** (Ctrl+.) keeps everything found so far.
- The chart draws within seconds and keeps refining while a slow radar sweep passes over it. The counters underneath show files scanned, size found and elapsed time.
- Halo remembers each finished scan, so reopening a folder is instant. A banner says how old the saved result is, with a **Rescan** button.
- Halo counts *disk usage* (the blocks a file really occupies, like `du`) by default. Switch to *apparent size* under **View**. The measure in use is shown under the chart.
- Hard-linked files are counted once. Symbolic links are never followed and show as 0 bytes. Other drives mounted inside a folder show as a grey **Other drive** wedge you can click to scan separately.
- If some folders couldn't be read, a banner offers **Show list** and **Rescan with admin rights**, which asks for your password once.

## Reading the sunburst

- The centre is the folder you're looking at. Each ring outward is one level deeper, biggest items first, clockwise from 12 o'clock.
- Colours show the kind of file (documents, images, video, audio, archives, apps and code, system and other). Switch to **Colour by top folder** with the toggle above the chart. Every kind also has an icon in the legend and the list, so colour is never the only clue.
- Items too small to see are merged into one grey **Smaller items** wedge. Right-click it and choose **Show these items in the list**.
- Hover an arc to see its path, size, item count and share of its parent.

## Moving around

- **Double-click** a folder arc to zoom into it. **Click the centre** to go up one level.
- Use the breadcrumb above the chart to jump back to any level, or **Alt+Home** to return to the scan root.
- From the keyboard: arrow keys move between arcs, **Enter** zooms in, **Backspace** goes up and **Space** ticks the focused arc.

## Selecting

- **Click** an arc to select it. **Ctrl+click** adds or removes arcs, **Shift+click** selects a run of neighbours on the same ring, **Ctrl+A** selects everything inside the centre, **Esc** clears.
- The chart and the list share one selection. Ticking a row outlines its arc; selecting an arc ticks its row. The footer shows the running total.
- Selecting a folder includes everything inside it; its inner arcs get a faint outline.

## The list panel

- **Outline** mirrors the chart as an expandable tree. Expand *Files in this folder* to reach individual files.
- **Biggest** lists the 200 largest files under the current centre.
- **Oldest** lists files over 10 MB that haven't been used for the longest time. "Last used" is approximate, because Linux Mint updates access times at most once a day.

## Actions

Every action works on the current selection, from the buttons under the list, from the right-click menu on arcs and rows, or from the **Edit** menu.

- **Move to Trash** (Delete) — recoverable from the Trash in your file manager.
- **Move to… / Copy to…** — pick a folder; a small progress toast shows the copy.
- **Show in File Manager** — opens Nemo (or Caja, Thunar) with the item highlighted.
- **Shred…** (Shift+Delete) — overwrites and removes for good. See below.
- You can also drag items from the chart, the list or Nemo onto the **drop zone**.

## Shredding

Shredding takes three deliberate steps: select, review the sheet, then **press and hold** the Shred button until it fills. A stray click can never start it.

1. Halo checks where the items live and warns you, in plain language, if overwriting has limits there (SSDs, copy-on-write filesystems, snapshots, sync folders, hard links).
2. The sheet lists the items and lets you choose the **DoD 5220.22-M 3-pass method** (zeros, ones, random) or a **single random pass**, whether to verify the final pass, and which traces to remove.
3. Progress shows a ring per file with one segment per pass, then a tick when the read-back matched.

Traces removed with each file (each on by default):

- cached **thumbnails** in every size folder, including previews of renamed files inside shredded folders, and `.sh_thumbnails` on USB drives;
- matching entries in the **recent files** list (`recently-used.xbel`);
- **copies in the Trash** on every drive.

If you cancel during a pass, Halo finishes the current chunk, stops, and reports the file as *Partly overwritten, not removed*.

A **shred report** can be saved to `~/Documents/Halo Reports/`. File names are stored as hashes unless you tick *Include file names*.

## What Halo protects

Shred and Trash are disabled, with the reason shown, for your home folder itself, `~/.config` and `~/.local/share` as a whole, `~/.ssh`, and anything outside your home folder and external drives. System folders such as `/usr`, `/etc`, `/boot` and `/var` are view-only. Items on network or FUSE drives can be moved to the Trash but not shredded.

## Tools

- **Clean Thumbnail Cache…** shows the cache size, then shreds every cached preview.
- **Clear Recent Files List…** empties `recently-used.xbel` securely.
- **Shred Trash Contents…** shreds everything in the Trash on all drives.
- **Clear Halo's Own Data…** shreds Halo's saved scans, log and recent-scan list.

Each of these goes through the same confirmation sheet and press-and-hold button.

## What's out of scope

Halo works on files and folders. It can't reach copies in swap, hibernation images, RAM, backups, snapshots, or inside other apps' own databases. For whole drives, use a dedicated tool such as nwipe, or the drive's own secure-erase command.

## Halo's own files

Halo stores only these, readable only by you: preferences in `~/.config/halo/settings.json`, saved scans and the recent list in `~/.cache/halo/`, a small log in `~/.local/state/halo/halo.log` (never containing shredded file names), and shred reports if you turn them on. Uninstalling Halo leaves these per-user files, like every Linux app — use **Clear Halo's Own Data…** first if you want them gone.

## Updates

Halo never checks for updates. New versions appear on the project's GitHub Releases page; installing a newer `.deb` over the old one upgrades in place and keeps your settings.

## Command line

```
halo [PATH]          open Halo and scan PATH
halo --shred PATH…   open the shred sheet for these items
halo --version
halo --help
HALO_DRY_RUN=1 halo  run every shred step without changing anything
```
