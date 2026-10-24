# Shredding and SSDs, explained

Deleting a file normally only removes its name; the data stays on the disk until something else happens to be written over it. Shredding writes over the file's data first, checks that the new data really landed, and only then removes it.

Halo uses the **DoD 5220.22-M 3-pass method**: it writes zeros over every byte, then ones, then random data from the kernel's random generator, flushing to the disk after each pass. It then reads the whole file back and compares it against the last pass. Only when every block matches does Halo scrub the file's name and delete it. You can also choose a **single random pass**, which is faster.

Overwriting only destroys data when the filesystem and the drive put the new data on top of the old. On many modern setups they don't. Halo detects these cases and tells you before you shred, and it never claims more than it can deliver.

## SSDs and flash drives

Solid-state drives spread writes across their cells (wear-levelling) and keep spare cells the computer can't see. When Halo overwrites a file, the drive may write the new data to fresh cells and leave the old ones untouched until it cleans them up later. So on an SSD, USB stick or SD card, overwriting **can't guarantee** that the old data is gone.

What that means in practice:

- The **single random pass** is just as effective as three passes on an SSD, and much faster — Halo suggests it when it sees an SSD.
- The real protection on SSDs is **full-disk encryption (LUKS)**, which Linux Mint offers during installation. With encryption, leftover cells hold only scrambled data. If your drive is encrypted, Halo says so.

## Filesystems that don't overwrite in place

- **Btrfs, ZFS** and **F2FS** write changes to new blocks (copy-on-write or log-structured), so the old blocks can survive.
- **XFS** may keep data in its log or in reflinked copies.
- **NTFS** can keep small files inside its master file table and journal.
- **ext3/ext4** work as expected in the default `data=ordered` and `data=writeback` modes, but are less effective with `data=journal`. Linux Mint installs ext4 in the default mode, which is the good case.

On a spinning hard disk with ext4 in its default mode, Halo shows no warning: this is the case the method is designed for.

## Copies elsewhere

Shredding can't reach copies held somewhere else:

- **Timeshift snapshots** (`/timeshift/snapshots`) keep their own copies of files.
- **Sync folders** such as Dropbox, Nextcloud or OneDrive may keep copies in the cloud and in their own caches.
- **Backups** on other drives, email attachments, and copies inside other apps' databases.
- **Swap, hibernation images and RAM** can briefly hold file contents.

Halo lists the snapshot and sync locations it finds before you shred.

## Hard links

A hard link is a second name for the same data. Overwriting changes the data under every name; Halo warns you and lists the other names it found, so nothing surprises you.

## About the standard

DoD 5220.22-M is no longer a current US federal standard. The current guidance is **NIST SP 800-88 Rev. 2** (September 2025), which favours a single overwrite or cryptographic erase. Halo keeps the DoD 3-pass method as the default because people ask for it by name, and offers the single random pass as the modern alternative.

Halo never calls its result "certified", "guaranteed unrecoverable" or "military-grade": honest limits matter more than reassuring words.
