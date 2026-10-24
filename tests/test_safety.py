# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
import os

from halo import safety

MOUNTINFO = """\
22 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw,errors=remount-ro
40 22 8:17 / /media/user/USB\\040STICK rw,nosuid,nodev,relatime shared:2 - vfat /dev/sdb1 rw
41 22 0:45 / /home rw,relatime shared:3 - btrfs /dev/sda3 rw,space_cache=v2
42 22 8:5 / /data rw,relatime shared:4 - ext4 /dev/sda5 rw,data=journal
43 22 0:50 / /mnt/nas rw,relatime - cifs //nas/share rw
44 22 0:51 / /run/user/1000/gvfs rw - fuse.gvfsd-fuse gvfsd-fuse rw
"""


def mounts(tmp_path):
    p = tmp_path / "mountinfo"
    p.write_text(MOUNTINFO)
    return safety.read_mounts(str(p))


def test_mountinfo_parsing(tmp_path):
    ms = mounts(tmp_path)
    by = {m.point: m for m in ms}
    assert by["/media/user/USB STICK"].fstype == "vfat"
    assert "data=journal" in by["/data"].options
    assert safety.mount_for("/home/user/x", ms).fstype == "btrfs"
    assert safety.mount_for("/usr/bin", ms).point == "/"


def test_protected_paths(home):
    h = str(home)
    os.makedirs(os.path.join(h, ".ssh"))
    os.makedirs(os.path.join(h, ".config", "app"))
    os.makedirs(os.path.join(h, ".local", "share", "app"))
    os.makedirs(os.path.join(h, "Documents"))
    assert safety.protection_reason(h)
    assert safety.protection_reason(os.path.join(h, ".ssh"))
    assert safety.protection_reason(os.path.join(h, ".ssh", "id_ed25519"))
    assert safety.protection_reason(os.path.join(h, ".config"))
    assert safety.protection_reason(os.path.join(h, ".local", "share"))
    assert safety.protection_reason("/etc/hosts")
    assert safety.protection_reason("/usr/bin/ls")
    assert safety.protection_reason(os.path.join(h, "Documents")) is None
    assert safety.protection_reason(os.path.join(h, ".config", "app")) is None
    assert safety.protection_reason(os.path.join(h, ".local", "share", "app")) is None


def test_trash_and_shred_disabled_for_protected(home):
    assert safety.trash_block_reason("/etc/hosts")
    assert safety.shred_block_reason("/etc/hosts")
    assert safety.shred_block_reason(os.path.join(str(home), ".ssh"))


def test_symlink_to_protected_is_judged_by_its_own_location(home):
    link = os.path.join(str(home), "hosts-link")
    os.symlink("/etc/hosts", link)
    assert safety.protection_reason(link) is None      # the link itself lives in home


def test_external_drive_allowed_but_not_its_root(tmp_path):
    ms = mounts(tmp_path)
    assert safety.protection_reason("/media/user/USB STICK/photo.jpg", ms) is None
    assert safety.protection_reason("/media/user/USB STICK", ms)


def test_warnings_for_cow_journal(tmp_path, monkeypatch):
    ms = mounts(tmp_path)
    monkeypatch.setattr(safety, "drive_is_rotational", lambda *a, **k: True)
    monkeypatch.setattr(safety, "rotational_for_source", lambda *a, **k: True)
    monkeypatch.setattr(safety, "timeshift_dirs", lambda: [])
    cards = safety.warnings_for(["/home/user/a.txt", "/data/b.txt"], ms)
    keys = {c.key for c in cards}
    assert keys == {"fs", "journal"}
    assert not safety.warnings_for(["/usr/x"], ms)        # ext4 default on HDD: no warning


def test_ssd_warning(tmp_path, monkeypatch):
    ms = mounts(tmp_path)
    monkeypatch.setattr(safety, "drive_is_rotational", lambda *a, **k: False)
    monkeypatch.setattr(safety, "timeshift_dirs", lambda: [])
    assert safety.warnings_for(["/usr/x"], ms) == []          # off by default
    cards = safety.warnings_for(["/usr/x"], ms, include_ssd=True)
    assert [c.key for c in cards] == ["ssd"]
    assert "LUKS" in cards[0].body


def test_network_mount_blocks_shred_only(tmp_path, monkeypatch):
    ms = mounts(tmp_path)
    assert safety.fs_info("/mnt/nas/file", ms).network
    assert safety.fs_info("/run/user/1000/gvfs/x", ms).network
    assert not safety.fs_info("/home/user/x", ms).network


def test_sync_and_timeshift_warning(home, monkeypatch):
    d = os.path.join(str(home), "Dropbox")
    os.makedirs(d)
    monkeypatch.setattr(safety, "timeshift_dirs", lambda: ["/timeshift/snapshots"])
    monkeypatch.setattr(safety, "drive_is_rotational", lambda *a, **k: True)
    cards = safety.warnings_for([os.path.join(d, "a.txt")], [])
    copies = [c for c in cards if c.key == "copies"]
    assert copies and any("Dropbox" in i for i in copies[0].items)
