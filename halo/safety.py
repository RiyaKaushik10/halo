# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Protected paths, filesystem and drive detection, and the honest warnings
shown before any shred (section 6)."""

import os
import stat

from .util import _, home, pretty_path

NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "fuse.gvfsd-fuse",
              "fuse.rclone", "fuse.s3fs", "9p", "afs", "ceph", "glusterfs", "davfs", "fuse.davfs"}
COW_FS = {"btrfs": _("Btrfs is copy-on-write: new data goes to new blocks, so old blocks can survive."),
          "xfs": _("XFS may keep old data in its log and reflinked copies."),
          "zfs": _("ZFS is copy-on-write and keeps old blocks in snapshots."),
          "f2fs": _("F2FS is log-structured: overwrites land in new blocks."),
          "ntfs": _("NTFS can keep small files inside its metadata table and journal."),
          "ntfs3": _("NTFS can keep small files inside its metadata table and journal."),
          "fuseblk": _("This drive uses a FUSE filesystem (often NTFS), which may not overwrite in place.")}
SYNC_FOLDERS = ["Dropbox", "Nextcloud", "ownCloud", "OneDrive", "pCloudDrive", "MEGA", "Sync",
                "Google Drive", "Insync", "Seafile", "Syncthing"]
VIEW_ONLY_ROOTS = ("/usr", "/etc", "/boot", "/var", "/bin", "/sbin", "/lib", "/lib64", "/opt",
                   "/srv", "/root", "/proc", "/sys", "/dev", "/run", "/snap")
EXTERNAL_PREFIXES = ("/media/", "/run/media/", "/mnt/")


# ── Mount table ───────────────────────────────────────────────────────────
def _unescape(s):
    return s.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


class Mount:
    __slots__ = ("point", "fstype", "options", "devno", "source")

    def __init__(self, point, fstype, options, devno, source):
        self.point, self.fstype, self.options, self.devno, self.source = point, fstype, options, devno, source

    def __repr__(self):
        return "Mount(%r, %r)" % (self.point, self.fstype)


def read_mounts(path="/proc/self/mountinfo"):
    out = []
    try:
        with open(path) as f:
            for line in f:
                if " - " not in line:
                    continue
                left, right = line.rstrip("\n").split(" - ", 1)
                lp = left.split()
                rp = right.split()
                if len(lp) < 6 or len(rp) < 2:
                    continue
                opts = set(lp[5].split(","))
                if len(rp) >= 3:
                    opts |= set(rp[2].split(","))
                out.append(Mount(_unescape(lp[4]), rp[0], opts, lp[2], _unescape(rp[1])))
    except OSError:
        pass
    return out


def mount_for(path, mounts=None):
    mounts = read_mounts() if mounts is None else mounts
    path = os.path.realpath(path) if os.path.exists(path) else os.path.abspath(path)
    best = None
    for m in mounts:
        mp = m.point.rstrip("/") or "/"
        if path == mp or path.startswith(mp.rstrip("/") + "/") or mp == "/":
            if best is None or len(mp) >= len(best.point.rstrip("/") or "/"):
                best = m
    return best


def is_external_mount(m):
    return m is not None and m.point.startswith(EXTERNAL_PREFIXES)


# ── Drive type ────────────────────────────────────────────────────────────
def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _block_dir(devno):
    p = "/sys/dev/block/%s" % devno
    return os.path.realpath(p) if os.path.exists(p) else None


def _rotational_for_dir(d, depth=0):
    if d is None or depth > 6:
        return None
    v = _read(os.path.join(d, "queue", "rotational"))
    if v is None:
        v = _read(os.path.join(os.path.dirname(d), "queue", "rotational"))  # partition → disk
    if v is not None:
        # dm / md devices report their own value but inherit from slaves
        slaves = os.path.join(d, "slaves")
        if os.path.isdir(slaves) and os.listdir(slaves):
            vals = [_rotational_for_dir(os.path.realpath(os.path.join(slaves, s)), depth + 1)
                    for s in os.listdir(slaves)]
            vals = [x for x in vals if x is not None]
            if vals:
                return any(vals)
        return v == "1"
    return None


def drive_is_rotational(devno=None, path=None):
    """True for HDD, False for SSD/flash, None if unknown."""
    if devno is None and path is not None:
        try:
            st = os.stat(path)
            devno = "%d:%d" % (os.major(st.st_dev), os.minor(st.st_dev))
        except OSError:
            return None
    if devno is None:
        return None
    d = _block_dir(devno)
    if d is None:
        # btrfs and friends report anonymous device numbers; fall back to the source device
        return None
    return _rotational_for_dir(d)


def rotational_for_source(source):
    if not source or not source.startswith("/dev/"):
        return None
    try:
        st = os.stat(source)
    except OSError:
        return None
    if not stat.S_ISBLK(st.st_mode):
        return None
    return drive_is_rotational("%d:%d" % (os.major(st.st_rdev), os.minor(st.st_rdev)))


def is_encrypted(devno):
    d = _block_dir(devno)
    if not d:
        return False
    uuid = _read(os.path.join(d, "dm", "uuid")) or ""
    if uuid.startswith("CRYPT-"):
        return True
    slaves = os.path.join(d, "slaves")
    if os.path.isdir(slaves):
        for s in os.listdir(slaves):
            if is_encrypted_dir(os.path.realpath(os.path.join(slaves, s))):
                return True
    return False


def is_encrypted_dir(d):
    uuid = _read(os.path.join(d, "dm", "uuid")) or ""
    return uuid.startswith("CRYPT-")


class FsInfo:
    def __init__(self, mount):
        self.mount = mount
        self.fstype = mount.fstype if mount else "unknown"
        self.options = mount.options if mount else set()
        rot = drive_is_rotational(mount.devno) if mount else None
        if rot is None and mount is not None:
            rot = rotational_for_source(mount.source)
        self.rotational = rot
        self.network = bool(mount) and (self.fstype in NETWORK_FS or
                                        (self.fstype.startswith("fuse.") and self.fstype != "fuse.portal")
                                        or self.fstype == "fuse")
        self.journal = self.fstype in ("ext3", "ext4") and "data=journal" in self.options
        self.encrypted = bool(mount) and is_encrypted(mount.devno)

    @property
    def drive_badge(self):
        if self.rotational is True:
            return "HDD"
        if self.rotational is False:
            return "SSD"
        return None


def fs_info(path, mounts=None):
    return FsInfo(mount_for(path, mounts))


# ── Protected paths ───────────────────────────────────────────────────────
def _inside(path, base):
    return path == base or path.startswith(base.rstrip("/") + "/")


def protection_reason(path, mounts=None):
    """None if Halo may trash or shred `path`, else a short reason for the tooltip."""
    h = home()
    p = os.path.abspath(path)
    try:
        if os.path.islink(p):
            real_parent = os.path.realpath(os.path.dirname(p))
            p = os.path.join(real_parent, os.path.basename(p))
        else:
            p = os.path.realpath(p)
    except OSError:
        pass
    if p == h:
        return _("Your home folder itself is protected")
    for sub, why in ((".config", _("~/.config as a whole is protected")),
                     (".local", _("~/.local as a whole is protected")),
                     (os.path.join(".local", "share"), _("~/.local/share as a whole is protected"))):
        if p == os.path.join(h, sub):
            return why
    if _inside(p, os.path.join(h, ".ssh")):
        return _("~/.ssh holds your keys and is protected")
    if _inside(p, h):
        return None
    mounts = read_mounts() if mounts is None else mounts
    m = mount_for(p, mounts)
    if is_external_mount(m):
        if p == m.point:
            return _("The top of a drive is protected; select what's inside it")
        return None
    for r in VIEW_ONLY_ROOTS:
        if _inside(p, r):
            return _("System folders are view-only")
    return _("Only files in your home folder and on external drives can be removed")


def contains_protected(path):
    """True if `path` is an ancestor of a protected location (e.g. ~/.local)."""
    h = home()
    p = os.path.realpath(path)
    for sub in (".ssh", ".config", os.path.join(".local", "share")):
        t = os.path.join(h, sub)
        if t.startswith(p.rstrip("/") + "/"):
            return True
    return False


def shred_block_reason(path, mounts=None):
    """Reasons that disable Shred but still allow Move to Trash."""
    r = protection_reason(path, mounts)
    if r:
        return r
    if contains_protected(path):
        return _("Contains a protected folder")
    info = fs_info(path, mounts)
    if info.network:
        return _("Shredding isn't possible on network or FUSE drives (%s)") % info.fstype
    try:
        st = os.lstat(path)
        if stat.S_ISCHR(st.st_mode) or stat.S_ISBLK(st.st_mode) or stat.S_ISSOCK(st.st_mode) \
                or stat.S_ISFIFO(st.st_mode):
            return _("Device files, sockets and pipes can't be shredded")
    except OSError:
        pass
    return None


def trash_block_reason(path, mounts=None):
    r = protection_reason(path, mounts)
    if r:
        return r
    if contains_protected(path):
        return _("Contains a protected folder")
    return None


# ── Warnings before the sheet ─────────────────────────────────────────────
class Warning_:
    def __init__(self, key, title, body, items=None):
        self.key, self.title, self.body, self.items = key, title, body, items or []


def timeshift_dirs():
    out = []
    for d in ("/timeshift/snapshots", "/run/timeshift/backup/timeshift/snapshots"):
        if os.path.isdir(d):
            out.append(d)
    for m in read_mounts():
        cand = os.path.join(m.point, "timeshift", "snapshots")
        if m.point.startswith(EXTERNAL_PREFIXES) and os.path.isdir(cand):
            out.append(cand)
    return out


def sync_folder_for(path):
    h = home()
    for name in SYNC_FOLDERS:
        d = os.path.join(h, name)
        if _inside(os.path.abspath(path), d) and os.path.isdir(d):
            return d
    return None


def warnings_for(paths, mounts=None, include_ssd=False):
    """Amber cards for the confirmation sheet, de-duplicated across items.
    The SSD card is off unless the user turns it on in Preferences."""
    mounts = read_mounts() if mounts is None else mounts
    cards = []
    seen_fs = set()
    ssd_mounts, cow, journal, sync = [], {}, [], set()
    for p in paths:
        info = fs_info(p, mounts)
        mp = info.mount.point if info.mount else "/"
        if mp in seen_fs:
            pass
        else:
            seen_fs.add(mp)
            if include_ssd and info.rotational is False:
                ssd_mounts.append((mp, info.encrypted))
            if info.fstype in COW_FS:
                cow.setdefault(info.fstype, []).append(mp)
            if info.journal:
                journal.append(mp)
        s = sync_folder_for(p)
        if s:
            sync.add(s)
    if ssd_mounts:
        enc = all(e for _m, e in ssd_mounts)
        body = _("Solid-state drives move data around internally (wear-levelling), so overwriting a "
                 "file can't guarantee the old cells are erased. The single random pass is just as "
                 "effective here and much faster. Full-disk encryption (LUKS) is the real protection "
                 "on SSDs.")
        if enc:
            body += " " + _("This drive is encrypted with LUKS, which is the good case.")
        cards.append(Warning_("ssd", _("This is an SSD or flash drive"), body,
                              [_("Drive mounted at %s") % pretty_path(m) for m, _e in ssd_mounts]))
    for fst, mps in cow.items():
        cards.append(Warning_("fs", _("%s filesystem") % fst.upper(),
                              COW_FS[fst] + " " + _("Overwriting may not reach the original blocks."),
                              [_("Drive mounted at %s") % pretty_path(m) for m in mps]))
    if journal:
        cards.append(Warning_("journal", _("ext4 with full data journalling"),
                              _("This drive is mounted with data=journal, so copies of file contents "
                                "can remain in the journal."), [pretty_path(m) for m in journal]))
    ts = timeshift_dirs()
    if ts or sync:
        items = [pretty_path(t) + " " + _("(Timeshift snapshots)") for t in ts]
        items += [pretty_path(s) + " " + _("(sync folder)") for s in sorted(sync)]
        cards.append(Warning_("copies", _("Copies may remain elsewhere"),
                              _("Snapshots and synced folders keep their own copies, which "
                                "shredding here can't reach."), items))
    return cards


def hard_link_names(path, search_root, time_budget=3.0):
    """Other names for the same inode under `search_root` (bounded search)."""
    import time
    try:
        st = os.lstat(path)
    except OSError:
        return []
    if st.st_nlink <= 1 or not stat.S_ISREG(st.st_mode):
        return []
    found = []
    t0 = time.monotonic()
    stack = [search_root]
    while stack and time.monotonic() - t0 < time_budget:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            s = e.stat(follow_symlinks=False)
                            if s.st_ino == st.st_ino and s.st_dev == st.st_dev and e.path != path:
                                found.append(e.path)
                    except OSError:
                        pass
        except OSError:
            pass
    return found
