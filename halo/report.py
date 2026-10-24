# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Shred reports. File names are stored as hashes unless the user opts in,
because a report full of names would defeat the point."""

import hashlib
import json
import os
import time

from . import storage
from .paths import read_version
from .shred import METHOD_LABELS, STATUS_TEXT
from .util import format_size


def name_hash(path):
    return "sha256:" + hashlib.sha256(path.encode("utf-8", "surrogateescape")).hexdigest()[:16]


def build(results, method, verify, include_names=False, traces=None, dry_run=False, when=None):
    when = when or time.time()
    items = []
    for r in results:
        d = r if isinstance(r, dict) else r.to_dict()
        items.append({
            "item": d["path"] if include_names else name_hash(d["path"]),
            "kind": "folder" if d.get("is_dir") else "file",
            "bytes": d.get("bytes", 0),
            "result": d["status"],
        })
    return {
        "app": "Halo " + read_version(),
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(when)),
        "method": METHOD_LABELS.get(method, method),
        "verify_final_pass": bool(verify),
        "dry_run": bool(dry_run),
        "item_count": len(items),
        "total_bytes": sum(i["bytes"] for i in items),
        "verified": sum(1 for i in items if i["result"] == "verified"),
        "failed": sum(1 for i in items if i["result"] in ("failed", "partial")),
        "names": "included" if include_names else "hashed",
        "traces": traces or {},
        "items": items,
    }


def render_text(rep):
    lines = [
        "Halo shred report",
        "=================",
        "",
        "Date:          %s" % rep["date"],
        "Method:        %s" % rep["method"],
        "Verification:  %s" % ("on" if rep["verify_final_pass"] else "off"),
        "Items:         %d" % rep["item_count"],
        "Total size:    %s (%d bytes)" % (format_size(rep["total_bytes"]), rep["total_bytes"]),
        "Verified:      %d" % rep["verified"],
        "Failed:        %d" % rep["failed"],
        "File names:    %s" % rep["names"],
    ]
    if rep["dry_run"]:
        lines.append("Mode:          DRY RUN — nothing was written, renamed or deleted")
    if rep.get("traces"):
        lines += ["", "Traces removed"]
        for k, v in rep["traces"].items():
            lines.append("  %-22s %s" % (k, v))
    lines += ["", "Items", "-----"]
    for i in rep["items"]:
        lines.append("%-34s %-7s %12s  %s" % (STATUS_TEXT.get(i["result"], i["result"]), i["kind"],
                                             format_size(i["bytes"]), i["item"]))
    lines += ["", "Machine-readable copy:", json.dumps(rep, indent=2), ""]
    return "\n".join(lines)


def save(rep, folder):
    storage.ensure_dir(folder)
    stamp = time.strftime("%Y-%m-%d %H-%M-%S")
    path = os.path.join(folder, "Halo shred report %s.txt" % stamp)
    storage.write_private(path, render_text(rep))
    return path
