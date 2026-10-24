# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""The one selection shared by the sunburst and every list."""

from gi.repository import GObject

from .scanner import FILES


class Selection(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__()
        self._nodes = {}           # id(node) -> node, keeps insertion order
        self.anchor = None
        self._frozen = 0
        self._dirty = False
        self.version = 0

    # queries
    def __contains__(self, node):
        return id(node) in self._nodes

    def __len__(self):
        return len(self._nodes)

    def __iter__(self):
        return iter(list(self._nodes.values()))

    def nodes(self):
        return list(self._nodes.values())

    def top_level(self):
        """Selected nodes, minus any that sit inside another selected folder."""
        sel = self.nodes()
        out = []
        for n in sel:
            covered = False
            for a in n.ancestors():
                if id(a) in self._nodes:
                    covered = True
                    break
            if not covered:
                out.append(n)
        return out

    def implied(self, node):
        """True if `node` sits inside a selected folder."""
        for a in node.ancestors():
            if id(a) in self._nodes:
                return True
        if node.flags & FILES and node.parent is not None and id(node.parent) in self._nodes:
            return True
        return False

    def total_size(self):
        return sum(n.size for n in self.top_level())

    def total_count(self):
        return len(self.top_level())

    # changes
    def _emit(self):
        self.version += 1
        if self._frozen:
            self._dirty = True
        else:
            self.emit("changed")

    def freeze(self):
        self._frozen += 1

    def thaw(self):
        self._frozen -= 1
        if self._frozen == 0 and self._dirty:
            self._dirty = False
            self.emit("changed")

    def set(self, nodes):
        new = {id(n): n for n in nodes}
        if new.keys() == self._nodes.keys():
            return
        self._nodes = new
        self.anchor = nodes[-1] if nodes else None
        self._emit()

    def add(self, node):
        if id(node) not in self._nodes:
            self._nodes[id(node)] = node
            self.anchor = node
            self._emit()

    def remove(self, node):
        if self._nodes.pop(id(node), None) is not None:
            self._emit()

    def toggle(self, node):
        if id(node) in self._nodes:
            self.remove(node)
        else:
            self.add(node)

    def clear(self):
        if self._nodes:
            self._nodes = {}
            self.anchor = None
            self._emit()

    def prune_detached(self, root):
        """Drop nodes that are no longer in the tree under `root`."""
        keep = {}
        for k, n in self._nodes.items():
            m = n
            while m.parent is not None:
                m = m.parent
            if m is root:
                keep[k] = n
        if keep.keys() != self._nodes.keys():
            self._nodes = keep
            self._emit()
