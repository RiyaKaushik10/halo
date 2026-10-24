# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""The sunburst: a Gtk.DrawingArea drawn with Cairo.

Layout, merge rule, hover lift and info card, zoom animation, selection,
keyboard navigation, radar sweep while scanning and the right-click hook.
"""

import bisect
import math

import cairo
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, PangoCairo

from . import theme as T
from .scanner import FILES, LINK, OTHER_DRIVE, SMALLER
from .util import _, format_items, format_size, pretty_path

TAU = 2 * math.pi
MERGE_MIN = math.radians(T.MERGE_MIN_DEGREES)
SPECIAL_MIN = math.radians(T.MIN_SPECIAL_DEGREES)


class Arc:
    __slots__ = ("node", "depth", "a0", "a1", "color", "flags", "size", "count", "parent", "index",
                 "children_nodes", "top")

    def __init__(self, node, depth, a0, a1, color, flags=0, size=0, count=0, parent=None, top=None):
        self.node = node
        self.depth = depth
        self.a0 = a0
        self.a1 = a1
        self.color = color
        self.flags = flags
        self.size = size
        self.count = count
        self.parent = parent        # parent Arc or None (child of centre)
        self.index = 0              # position on its ring
        self.children_nodes = None  # merged nodes for "Smaller items"
        self.top = top

    @property
    def is_smaller(self):
        return bool(self.flags & SMALLER)

    @property
    def mid(self):
        return (self.a0 + self.a1) / 2


class Layout:
    def __init__(self, center, arcs, rings):
        self.center = center
        self.arcs = arcs
        self.rings = rings
        self.by_ring = {}
        for a in arcs:
            self.by_ring.setdefault(a.depth, []).append(a)
        self.starts = {}
        for d, lst in self.by_ring.items():
            lst.sort(key=lambda a: a.a0)
            for i, a in enumerate(lst):
                a.index = i
            self.starts[d] = [a.a0 for a in lst]
        self.by_node = {id(a.node): a for a in arcs if a.node is not None}

    def hit(self, ring, theta):
        lst = self.by_ring.get(ring)
        if not lst:
            return None
        i = bisect.bisect_right(self.starts[ring], theta) - 1
        if 0 <= i < len(lst) and lst[i].a0 <= theta < lst[i].a1:
            return lst[i]
        return None


def _color_for(node, depth, mode, top_index):
    if node.flags & OTHER_DRIVE:
        return T.hex_to_rgb(T.OTHER_DRIVE_COLOR)
    if mode == "folder":
        base = T.TOP_FOLDER_COLORS[top_index % len(T.TOP_FOLDER_COLORS)]
        return T.lighten(base, T.DEPTH_LIGHTEN * (depth - 1), T.DEPTH_LIGHTNESS_CAP)
    return T.kind_rgb(node.dominant_kind(), depth - 1)


def compute_layout(center, rings, show_hidden=True, color_mode="kind", fine=None):
    """Lay out up to `rings` rings below `center`.

    `fine` = (a0, a1, threshold): inside that angular window use the smaller
    merge threshold (keeps zoom animations from popping).
    """
    arcs = []
    total = center.size
    if total <= 0:
        return Layout(center, arcs, rings)
    stack = [(center, 1, 0.0, TAU, None, 0)]
    while stack:
        node, depth, start, span, parent_arc, top_idx = stack.pop()
        kids = node.sorted_children(show_hidden)
        if not kids or node.size <= 0:
            continue
        specials = [k for k in kids if k.flags & OTHER_DRIVE]
        normal = [k for k in kids if not (k.flags & OTHER_DRIVE)]
        reserve = 0.0
        if specials and len(specials) * SPECIAL_MIN < span * 0.25:
            reserve = len(specials) * SPECIAL_MIN
        avail = span - reserve
        a = start
        merged_size = merged_count = 0
        merged_nodes = []
        merged_span = 0.0
        thr = MERGE_MIN
        if fine is not None:
            f0, f1, fthr = fine
            if start < f1 and start + span > f0:
                thr = fthr
        for i, k in enumerate(normal):
            ang = avail * (k.size / node.size) if node.size else 0
            if ang < thr:
                merged_size += k.size
                merged_count += k.count if not (k.flags & LINK) else 1
                merged_nodes.append(k)
                merged_span += ang
                continue
            ti = i if depth == 1 else top_idx
            arc = Arc(k, depth, a, a + ang, _color_for(k, depth, color_mode, ti), k.flags, k.size,
                      k.count, parent_arc)
            arcs.append(arc)
            if depth < rings and k.children and not (k.flags & FILES):
                stack.append((k, depth + 1, a, ang, arc, ti))
            a += ang
        if merged_nodes and merged_span > math.radians(0.08):
            arc = Arc(None, depth, a, a + merged_span, T.hex_to_rgb(T.SMALLER_ITEMS_COLOR), SMALLER,
                      merged_size, merged_count, parent_arc)
            arc.children_nodes = merged_nodes
            arc.top = node
            arcs.append(arc)
            a += merged_span
        if reserve:
            # Other-drive wedges sit at the end of their parent's span
            a = start + span - reserve
            for k in specials:
                arc = Arc(k, depth, a, a + SPECIAL_MIN, _color_for(k, depth, color_mode, 0), k.flags, 0, 0,
                          parent_arc)
                arcs.append(arc)
                a += SPECIAL_MIN
    return Layout(center, arcs, rings)


def angular_range(ancestor, node):
    """(a0, a1) of `node` inside `ancestor`'s full-circle layout, by size alone."""
    path = []
    n = node
    while n is not None and n is not ancestor:
        path.append(n)
        n = n.parent
    if n is None:
        return 0.0, TAU
    a0, span = 0.0, TAU
    parent = ancestor
    for n in reversed(path):
        if parent.size <= 0:
            break
        off = 0.0
        for k in parent.sorted_children():
            if k is n:
                break
            if not (k.flags & OTHER_DRIVE):
                off += k.size
        a0 += span * off / parent.size
        span = span * n.size / parent.size
        parent = n
    return a0, a0 + max(span, 1e-6)


class Camera:
    __slots__ = ("w0", "w1", "depth")

    def __init__(self, w0=0.0, w1=TAU, depth=0.0):
        self.w0, self.w1, self.depth = w0, w1, depth

    def lerp(self, other, t):
        return Camera(self.w0 + (other.w0 - self.w0) * t, self.w1 + (other.w1 - self.w1) * t,
                      self.depth + (other.depth - self.depth) * t)

    def map(self, a):
        return (a - self.w0) * TAU / max(1e-9, self.w1 - self.w0)

    @property
    def identity(self):
        return abs(self.w0) < 1e-9 and abs(self.w1 - TAU) < 1e-9 and abs(self.depth) < 1e-9


class Sunburst(Gtk.DrawingArea):
    __gtype_name__ = "HaloSunburst"
    __gsignals__ = {
        "center-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "context-menu": (GObject.SignalFlags.RUN_FIRST, None, (float, float, object)),
        "special-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, selection):
        super().__init__()
        self.selection = selection
        self.root = None
        self.center = None
        self.layout = None
        self.rings = T.DEFAULT_RINGS
        self.show_hidden = True
        self.color_mode = "kind"
        self.reduce_motion = False
        self.scanning = False
        self.size_label = ""

        self.hover = None             # Arc
        self.hover_center = False
        self.pointer = None           # (x, y)
        self.hover_anim = {}          # id(arc) -> [value, target]
        self.focus_arc = None
        self.keyboard_focus_visible = False

        self._anim = None             # dict for zoom animation
        self._morph = None            # dict for removal animation
        self._tick_id = 0
        self._radar_t0 = None
        self._cache = None
        self._cache_key = None
        self._layout_dirty = True
        self._fine = None
        self._font = self._system_font()

        self.set_focusable(True)
        self.set_can_focus(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)
        self.add_css_class("sunburst")
        self.update_property([Gtk.AccessibleProperty.LABEL], [_("Disk usage chart")])

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_click)
        self.add_controller(click)

        rclick = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        rclick.connect("pressed", self._on_right_click)
        self.add_controller(rclick)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

        drag = Gtk.DragSource(actions=Gdk.DragAction.COPY | Gdk.DragAction.MOVE)
        drag.connect("prepare", self._on_drag_prepare)
        self.add_controller(drag)

        selection.connect("changed", lambda *a: self.invalidate(False))
        self.connect("notify::scale-factor", lambda *a: self.invalidate(False))

    # ── configuration ────────────────────────────────────────────────────
    def _system_font(self):
        s = Gtk.Settings.get_default()
        name = s.props.gtk_font_name if s else "Sans 10"
        fd = Pango.FontDescription.from_string(name or "Sans 10")
        return fd

    def animations_enabled(self):
        s = Gtk.Settings.get_default()
        sys_on = s.props.gtk_enable_animations if s else True
        return sys_on and not self.reduce_motion

    def set_root(self, root, center=None):
        self.root = root
        self.center = center or root
        self.hover = None
        self.focus_arc = None
        self._anim = None
        self._morph = None
        self.invalidate()
        self.emit("center-changed", self.center)

    def set_options(self, rings=None, show_hidden=None, color_mode=None, reduce_motion=None):
        if rings is not None:
            self.rings = int(rings)
        if show_hidden is not None:
            self.show_hidden = bool(show_hidden)
        if color_mode is not None:
            self.color_mode = color_mode
        if reduce_motion is not None:
            self.reduce_motion = bool(reduce_motion)
        self.invalidate()

    def set_scanning(self, scanning):
        self.scanning = scanning
        if scanning:
            self._radar_t0 = None
            self._ensure_tick()
        self.queue_draw()

    def invalidate(self, relayout=True):
        if relayout:
            self._layout_dirty = True
        self._cache_key = None
        self.queue_draw()

    def _ensure_layout(self):
        if self.center is None:
            self.layout = None
            return
        if self._layout_dirty or self.layout is None or self.layout.center is not self.center:
            focus = None
            self.layout = compute_layout(self.center, self.rings, self.show_hidden, self.color_mode)
            if self.focus_arc is not None:
                n = self.focus_arc.node
                focus = self.layout.by_node.get(id(n)) if n is not None else None
            self.focus_arc = focus
            if self.hover is not None and self.hover.node is not None:
                self.hover = self.layout.by_node.get(id(self.hover.node))
            else:
                self.hover = None
            self._layout_dirty = False

    # ── geometry ─────────────────────────────────────────────────────────
    def _geom(self, w=None, h=None):
        w = self.get_width() if w is None else w
        h = self.get_height() if h is None else h
        R = max(10.0, min(w, h) / 2 - T.CHART_MARGIN_PX)
        return w / 2, h / 2, R

    def _ring_radii(self, R, depth, cam_depth=0.0):
        rc = R * T.CENTER_RADIUS
        rw = R * T.RING_WIDTH
        eff = depth - cam_depth
        return rc + (eff - 1) * rw, rc + eff * rw

    def _hit(self, x, y):
        """('center', None) | ('arc', Arc) | (None, None)."""
        if self.layout is None or self._anim is not None:
            return None, None
        cx, cy, R = self._geom()
        dx, dy = x - cx, y - cy
        r = math.hypot(dx, dy)
        rc = R * T.CENTER_RADIUS
        if r <= rc:
            return "center", None
        rw = R * T.RING_WIDTH
        ring = int((r - rc) // rw) + 1
        if ring > self.rings:
            return None, None
        theta = (math.atan2(dy, dx) + math.pi / 2) % TAU
        a = self.layout.hit(ring, theta)
        return ("arc", a) if a is not None else (None, None)

    # ── animation plumbing ───────────────────────────────────────────────
    def _ensure_tick(self):
        if not self._tick_id:
            self._tick_id = self.add_tick_callback(self._on_tick)

    def _on_tick(self, widget, clock):
        now = clock.get_frame_time() / 1e6
        busy = False
        if self._anim is not None:
            a = self._anim
            if a["t0"] is None:
                a["t0"] = now
            t = (now - a["t0"]) / a["dur"] if a["dur"] > 0 else 1.0
            a["t"] = min(1.0, t)
            if t >= 1.0:
                self._finish_anim()
            else:
                busy = True
        if self._morph is not None:
            m = self._morph
            if m["t0"] is None:
                m["t0"] = now
            t = (now - m["t0"]) / m["dur"] if m["dur"] > 0 else 1.0
            m["t"] = min(1.0, t)
            if t >= 1.0:
                self._morph = None
                self._cache_key = None
            else:
                busy = True
        # hover transitions
        if self.hover_anim:
            step = (1.0 / 60) / (T.HOVER_MS / 1000.0) if self.animations_enabled() else 1.0
            dead = []
            for k, v in self.hover_anim.items():
                if v[0] < v[1]:
                    v[0] = min(v[1], v[0] + step)
                elif v[0] > v[1]:
                    v[0] = max(v[1], v[0] - step)
                if v[0] != v[1]:
                    busy = True
                elif v[1] == 0:
                    dead.append(k)
            for k in dead:
                del self.hover_anim[k]
        if self.scanning:
            if self._radar_t0 is None:
                self._radar_t0 = now
            self._radar_angle = ((now - self._radar_t0) / T.RADAR_PERIOD_S) * TAU % TAU
            busy = True
        self.queue_draw()
        if not busy:
            self._tick_id = 0
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _finish_anim(self):
        a = self._anim
        self._anim = None
        self.center = a["target"]
        self._layout_dirty = True
        self._cache_key = None
        self._ensure_layout()
        self.emit("center-changed", self.center)

    # ── navigation ───────────────────────────────────────────────────────
    def zoom_to(self, node, animate=True):
        if node is None or self.center is None or node is self.center:
            return
        if node.flags & (FILES | LINK | OTHER_DRIVE) or not node.children:
            return
        if self._anim is not None:
            self._finish_anim()
        dur = T.ZOOM_MS / 1000.0 if (animate and self.animations_enabled()) else 0
        old = self.center
        self.hover = None
        self.hover_anim.clear()
        if dur == 0:
            self.center = node
            self.focus_arc = None
            self.invalidate()
            self.emit("center-changed", node)
            return
        if old.is_ancestor_of(node):
            base = old
            a0, a1 = angular_range(old, node)
            k = node.depth() - old.depth()
            cam0, cam1 = Camera(), Camera(a0, a1, k)
        elif node.is_ancestor_of(old):
            base = node
            a0, a1 = angular_range(node, old)
            k = old.depth() - node.depth()
            cam0, cam1 = Camera(a0, a1, k), Camera()
        else:
            self.center = node
            self.invalidate()
            self.emit("center-changed", node)
            return
        span = max(1e-6, a1 - a0)
        fine = (a0, a1, MERGE_MIN * span / TAU)
        lay = compute_layout(base, self.rings + k, self.show_hidden, self.color_mode, fine)
        # keep each node's colour depth stable relative to the final view
        self._anim = {"base": base, "layout": lay, "cam0": cam0, "cam1": cam1, "t0": None, "t": 0.0,
                      "dur": dur, "target": node, "k": k, "zoom_in": cam1.depth > cam0.depth}
        self.focus_arc = None
        self._ensure_tick()

    def go_up(self):
        if self.center is not None and self.center.parent is not None:
            self.zoom_to(self.center.parent)

    def go_root(self):
        if self.root is not None:
            self.zoom_to(self.root)

    def animate_removal(self, old_arcs):
        """Close removed arcs over 250 ms after the tree changed in place."""
        self._layout_dirty = True
        self._ensure_layout()
        if not self.animations_enabled() or not old_arcs:
            self.invalidate()
            return
        self._morph = {"old": old_arcs, "t0": None, "t": 0.0, "dur": T.REMOVE_MS / 1000.0}
        self._cache_key = None
        self._ensure_tick()

    def snapshot_arcs(self):
        if self.layout is None:
            return {}
        return {id(a.node) if a.node is not None else ("s", a.depth, round(a.a0, 4)):
                (a.a0, a.a1, a.depth, a.color, a.flags) for a in self.layout.arcs}

    # ── drawing ──────────────────────────────────────────────────────────
    def _arc_path(self, cr, cx, cy, r0, r1, a0, a1, lift=0.0):
        s, e = a0 - math.pi / 2, a1 - math.pi / 2
        if lift:
            m = (s + e) / 2
            cx += math.cos(m) * lift
            cy += math.sin(m) * lift
        cr.new_path()
        if a1 - a0 >= TAU - 1e-6:
            cr.arc(cx, cy, r1, 0, TAU)
            cr.close_path()
            cr.new_sub_path()
            cr.arc_negative(cx, cy, r0, TAU, 0)
            cr.close_path()
            return
        cr.arc(cx, cy, r1, s, e)
        cr.arc_negative(cx, cy, r0, e, s)
        cr.close_path()

    def _fill_arc(self, cr, cx, cy, r0, r1, a0, a1, color, alpha=1.0, lift=0.0):
        self._arc_path(cr, cx, cy, r0, r1, a0, a1, lift)
        if a1 - a0 > math.radians(1.5) and r1 - r0 > 4:
            g = cairo.RadialGradient(cx, cy, r0, cx, cy, r1)
            dark = T.lighten(color, -0.05)
            light = T.lighten(color, 0.035)
            g.add_color_stop_rgba(0, dark[0], dark[1], dark[2], alpha)
            g.add_color_stop_rgba(1, light[0], light[1], light[2], alpha)
            cr.set_source(g)
        else:
            cr.set_source_rgba(color[0], color[1], color[2], alpha)
        cr.fill_preserve()
        bg = T.rgb("bg_window")
        cr.set_source_rgb(*bg)
        cr.set_line_width(T.ARC_GAP_PX)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()

    def _text_layout(self, cr, text, px, weight=Pango.Weight.NORMAL, width=None,
                     ellipsize=Pango.EllipsizeMode.END, align=Pango.Alignment.LEFT):
        lay = PangoCairo.create_layout(cr)
        fd = self._font.copy()
        fd.set_absolute_size(px * Pango.SCALE)
        fd.set_weight(weight)
        lay.set_font_description(fd)
        if width is not None:
            lay.set_width(int(max(1, width) * Pango.SCALE))
            lay.set_ellipsize(ellipsize)
        lay.set_alignment(align)
        attrs = Pango.AttrList()
        try:
            attrs.insert(Pango.attr_font_features_new("tnum=1"))
        except Exception:
            pass
        lay.set_attributes(attrs)
        lay.set_text(text, -1)
        return lay

    def _draw(self, area, cr, w, h):
        cr.set_source_rgb(*T.rgb("bg_window"))
        cr.paint()
        cx, cy, R = self._geom(w, h)
        self._draw_glow(cr, cx, cy, R)
        if self.center is None:
            self._draw_empty(cr, cx, cy, R)
            return
        self._ensure_layout()

        if self._anim is not None:
            self._draw_anim(cr, cx, cy, R)
        elif self._morph is not None:
            self._draw_tracks(cr, cx, cy, R)
            self._draw_morph(cr, cx, cy, R)
        else:
            self._draw_static(cr, cx, cy, R, w, h)
            self._draw_hover(cr, cx, cy, R)
            self._draw_focus(cr, cx, cy, R)
        if self.scanning:
            self._draw_radar(cr, cx, cy, R)
        self._draw_center(cr, cx, cy, R)
        if self._anim is None and self.hover is not None and self.pointer is not None:
            self._draw_card(cr, w, h)
        elif self._anim is None and self.hover_center and self.pointer is not None and \
                self.center.parent is not None:
            self._draw_hint(cr, w, h, _("Click to go up to %s") % self.center.parent.label())

    def _draw_glow(self, cr, cx, cy, R):
        acc = T.rgb("accent")
        g = cairo.RadialGradient(cx, cy, R * 0.25, cx, cy, R * 1.25)
        g.add_color_stop_rgba(0, acc[0], acc[1], acc[2], T.GLOW_ALPHA)
        g.add_color_stop_rgba(0.55, acc[0], acc[1], acc[2], T.GLOW_ALPHA * 0.35)
        g.add_color_stop_rgba(1, acc[0], acc[1], acc[2], 0)
        cr.set_source(g)
        cr.arc(cx, cy, R * 1.3, 0, TAU)
        cr.fill()

    def _draw_tracks(self, cr, cx, cy, R):
        line = T.rgb("line")
        for d in range(1, self.rings + 1):
            r0, r1 = self._ring_radii(R, d)
            cr.new_path()
            cr.set_source_rgba(line[0], line[1], line[2], 0.42 if d == 1 else 0.16)
            cr.set_line_width(r1 - r0 - T.ARC_GAP_PX)
            cr.arc(cx, cy, (r0 + r1) / 2, 0, TAU)
            cr.stroke()

    def _draw_empty(self, cr, cx, cy, R):
        self._draw_tracks(cr, cx, cy, R)
        rc = R * T.CENTER_RADIUS
        cr.set_source_rgb(*T.rgb("bg_raised"))
        cr.arc(cx, cy, rc, 0, TAU)
        cr.fill()
        cr.set_source_rgb(*T.rgb("line"))
        cr.set_line_width(T.CENTER_RING_PX)
        cr.arc(cx, cy, rc, 0, TAU)
        cr.stroke()
        if self.scanning:
            self._draw_radar(cr, cx, cy, R)

    def _static_key(self, w, h):
        return (w, h, id(self.layout), self.selection.version, self.color_mode,
                self.scale_factor if hasattr(self, "scale_factor") else 1)

    def _draw_static(self, cr, cx, cy, R, w, h):
        key = self._static_key(w, h)
        if self._cache is not None and self._cache_key == key and not self.scanning:
            cr.set_source_surface(self._cache, 0, 0)
            cr.paint()
            return
        target = cr.get_target()
        try:
            surf = target.create_similar(cairo.CONTENT_COLOR_ALPHA, int(w), int(h))
        except Exception:
            surf = None
        if surf is None:
            self._paint_arcs(cr, cx, cy, R)
            return
        c2 = cairo.Context(surf)
        self._paint_arcs(c2, cx, cy, R)
        surf.flush()
        self._cache, self._cache_key = surf, key
        cr.set_source_surface(surf, 0, 0)
        cr.paint()

    def _arc_state(self, arc):
        sel = self.selection
        if arc.node is None:
            return False, False
        if arc.node in sel:
            return True, False
        return False, sel.implied(arc.node)

    def _paint_arcs(self, cr, cx, cy, R):
        self._draw_tracks(cr, cx, cy, R)
        has_sel = len(self.selection) > 0
        outlines = []
        for a in self.layout.arcs:
            r0, r1 = self._ring_radii(R, a.depth)
            selected, implied = self._arc_state(a)
            alpha = 1.0 if (not has_sel or selected or implied) else T.UNSELECTED_DIM
            self._fill_arc(cr, cx, cy, r0, r1, a.a0, a.a1, a.color, alpha)
            if selected:
                outlines.append((a, r0, r1, True))
            elif implied:
                outlines.append((a, r0, r1, False))
        tp = T.rgb("text_primary")
        for a, r0, r1, strong in sorted(outlines, key=lambda o: o[3]):
            inset = T.ARC_GAP_PX / 2 + (T.SELECTED_OUTLINE_PX / 2 if strong else T.SCOPE_OUTLINE_PX / 2)
            self._arc_outline(cr, cx, cy, r0 + inset, r1 - inset, a.a0, a.a1, inset)
            cr.set_source_rgba(tp[0], tp[1], tp[2], 1.0 if strong else T.SCOPE_OUTLINE_ALPHA)
            cr.set_line_width(T.SELECTED_OUTLINE_PX if strong else T.SCOPE_OUTLINE_PX)
            cr.stroke()

    def _arc_outline(self, cr, cx, cy, r0, r1, a0, a1, inset_px):
        # shrink angularly by inset at each radius so the stroke sits inside the gap
        if a1 - a0 >= TAU - 1e-6:
            cr.new_path()
            cr.arc(cx, cy, r1, 0, TAU)
            cr.new_sub_path()
            cr.arc(cx, cy, max(1, r0), 0, TAU)
            return
        d1 = inset_px / max(1.0, r1)
        d0 = inset_px / max(1.0, r0)
        if a1 - a0 <= 2 * d0:
            d0 = d1 = (a1 - a0) / 4
        cr.new_path()
        cr.arc(cx, cy, r1, a0 + d1 - math.pi / 2, a1 - d1 - math.pi / 2)
        cr.arc_negative(cx, cy, r0, a1 - d0 - math.pi / 2, a0 + d0 - math.pi / 2)
        cr.close_path()

    def _hover_value(self, arc):
        v = self.hover_anim.get(id(arc.node) if arc.node is not None else id(arc))
        return v[0] if v else 0.0

    def _draw_hover(self, cr, cx, cy, R):
        if not self.hover_anim or self.layout is None:
            return
        has_sel = len(self.selection) > 0
        for a in self.layout.arcs:
            key = id(a.node) if a.node is not None else id(a)
            v = self.hover_anim.get(key)
            if not v or v[0] <= 0.001:
                continue
            t = T.ease_out_cubic(v[0])
            r0, r1 = self._ring_radii(R, a.depth)
            # clear the original slot, then draw the lifted arc with a soft shadow
            self._arc_path(cr, cx, cy, r0, r1, a.a0, a.a1)
            cr.set_source_rgb(*T.rgb("bg_window"))
            cr.fill()
            lift = T.HOVER_LIFT_PX * t
            for i, sa in enumerate((0.10, 0.06, 0.03)):
                self._arc_path(cr, cx, cy, r0, r1 + 1 + i * 1.5, a.a0, a.a1, lift + 1.5 + i)
                cr.set_source_rgba(0, 0, 0, sa * t)
                cr.fill()
            col = T.lighten(a.color, T.HOVER_LIGHTEN * t)
            selected, implied = self._arc_state(a)
            alpha = 1.0 if (not has_sel or selected or implied) else T.UNSELECTED_DIM + (1 - T.UNSELECTED_DIM) * t
            self._fill_arc(cr, cx, cy, r0, r1, a.a0, a.a1, col, alpha, lift)
            if selected:
                inset = T.ARC_GAP_PX / 2 + T.SELECTED_OUTLINE_PX / 2
                m = (a.a0 + a.a1) / 2 - math.pi / 2
                cr.save()
                cr.translate(math.cos(m) * lift, math.sin(m) * lift)
                self._arc_outline(cr, cx, cy, r0 + inset, r1 - inset, a.a0, a.a1, inset)
                cr.set_source_rgb(*T.rgb("text_primary"))
                cr.set_line_width(T.SELECTED_OUTLINE_PX)
                cr.stroke()
                cr.restore()

    def _draw_focus(self, cr, cx, cy, R):
        a = self.focus_arc
        if a is None or not self.keyboard_focus_visible or not self.has_focus():
            return
        r0, r1 = self._ring_radii(R, a.depth)
        inset = T.ARC_GAP_PX / 2 + T.FOCUS_OUTLINE_PX / 2
        self._arc_outline(cr, cx, cy, r0 + inset, r1 - inset, a.a0, a.a1, inset)
        cr.set_source_rgb(*T.rgb("accent"))
        cr.set_line_width(T.FOCUS_OUTLINE_PX)
        cr.set_dash([5, 3])
        cr.stroke()
        cr.set_dash([])

    def _draw_anim(self, cr, cx, cy, R):
        an = self._anim
        e = T.ease_out_cubic(an["t"])
        cam = an["cam0"].lerp(an["cam1"], e)
        lay = an["layout"]
        rc = R * T.CENTER_RADIUS
        rmax = rc + self.rings * R * T.RING_WIDTH
        self._draw_tracks(cr, cx, cy, R)
        for a in lay.arcs:
            a0, a1 = cam.map(a.a0), cam.map(a.a1)
            if a1 <= 0 or a0 >= TAU:
                continue
            a0, a1 = max(0.0, a0), min(TAU, a1)
            if a1 - a0 < 1e-4:
                continue
            r0, r1 = self._ring_radii(R, a.depth, cam.depth)
            if r1 <= rc + 0.5 or r0 >= rmax:
                continue
            fade = 1.0
            if r1 > rmax:
                fade = max(0.0, (rmax - r0) / max(1.0, r1 - r0))
                r1 = rmax
            r0 = max(rc, r0)
            # colour depth follows the visible ring so the end frame matches the final view
            node = a.node
            eff_depth = max(1, int(round(a.depth - cam.depth)))
            col = a.color if node is None else _color_for(node, eff_depth, self.color_mode,
                                                         self._top_index(node, an))
            self._fill_arc(cr, cx, cy, r0, r1, a0, a1, col, fade)

    def _top_index(self, node, an):
        if self.color_mode != "folder":
            return 0
        target = an["target"]
        n = node
        while n.parent is not None and n.parent is not target and n.parent is not an["base"]:
            n = n.parent
        parent = n.parent
        if parent is None:
            return 0
        kids = parent.sorted_children(self.show_hidden)
        try:
            return kids.index(n)
        except ValueError:
            return 0

    def _draw_morph(self, cr, cx, cy, R):
        m = self._morph
        e = T.ease_out_cubic(m["t"])
        old = m["old"]
        seen = set()
        for a in self.layout.arcs:
            key = id(a.node) if a.node is not None else ("s", a.depth, round(a.a0, 4))
            r0, r1 = self._ring_radii(R, a.depth)
            o = old.get(key) if a.node is not None else None
            if o is not None:
                seen.add(key)
                a0 = o[0] + (a.a0 - o[0]) * e
                a1 = o[1] + (a.a1 - o[1]) * e
            else:
                a0, a1 = a.a0, a.a0 + (a.a1 - a.a0) * e
            self._fill_arc(cr, cx, cy, r0, r1, a0, a1, a.color)
        for key, o in old.items():
            if key in seen or not isinstance(key, int):
                continue
            a0, a1, depth, color, flags = o
            if depth > self.rings:
                continue
            r0, r1 = self._ring_radii(R, depth)
            mid = (a0 + a1) / 2
            span = (a1 - a0) * (1 - e)
            if span > 1e-4:
                self._fill_arc(cr, cx, cy, r0, r1, mid - span / 2, mid + span / 2, color, 1 - e)

    def _draw_radar(self, cr, cx, cy, R):
        ang = getattr(self, "_radar_angle", 0.0)
        acc = T.rgb("accent")
        rc = R * T.CENTER_RADIUS
        rmax = rc + self.rings * R * T.RING_WIDTH
        n = 28
        step = T.RADAR_TRAIL_RAD / n
        for i in range(n):
            a1 = ang - i * step
            a0 = a1 - step
            alpha = T.RADAR_ALPHA * (1 - i / n) ** 1.6
            cr.new_path()
            cr.move_to(cx + math.cos(a0 - math.pi / 2) * rc, cy + math.sin(a0 - math.pi / 2) * rc)
            cr.arc(cx, cy, rmax, a0 - math.pi / 2, a1 - math.pi / 2 + 0.002)
            cr.arc_negative(cx, cy, rc, a1 - math.pi / 2 + 0.002, a0 - math.pi / 2)
            cr.close_path()
            cr.set_source_rgba(acc[0], acc[1], acc[2], alpha)
            cr.fill()
        cr.set_source_rgba(acc[0], acc[1], acc[2], min(1.0, T.RADAR_ALPHA * 2.6))
        cr.set_line_width(1.5)
        cr.move_to(cx + math.cos(ang - math.pi / 2) * rc, cy + math.sin(ang - math.pi / 2) * rc)
        cr.line_to(cx + math.cos(ang - math.pi / 2) * rmax, cy + math.sin(ang - math.pi / 2) * rmax)
        cr.stroke()

    def _draw_center(self, cr, cx, cy, R):
        rc = R * T.CENTER_RADIUS
        node = self._anim["target"] if self._anim is not None else self.center
        if node is None:
            return
        raised = T.rgb("bg_raised")
        if self.hover_center and node.parent is not None and self._anim is None:
            raised = T.lighten(raised, 0.04)
        # soft inner shadow ring
        g = cairo.RadialGradient(cx, cy, rc * 0.6, cx, cy, rc)
        g.add_color_stop_rgb(0, *T.lighten(raised, 0.02))
        g.add_color_stop_rgb(1, *raised)
        cr.set_source(g)
        cr.arc(cx, cy, rc - 1, 0, TAU)
        cr.fill()
        cr.set_source_rgb(*T.rgb("accent"))
        cr.set_line_width(T.CENTER_RING_PX)
        cr.arc(cx, cy, rc - T.CENTER_RING_PX / 2, 0, TAU)
        cr.stroke()
        if node is not None and node in self.selection:
            pass
        width = rc * 1.78
        name = node.label() if node.parent is not None else _display_root(node)
        px = T.FONT_CENTER
        while True:
            big = self._text_layout(cr, format_size(node.size), px, Pango.Weight.SEMIBOLD)
            if big.get_pixel_extents()[1].width <= width or px <= 16:
                break
            px -= 1
        big.set_width(int(width * Pango.SCALE))
        big.set_alignment(Pango.Alignment.CENTER)
        small = self._text_layout(cr, name, T.FONT_ROW, Pango.Weight.MEDIUM, width,
                                  align=Pango.Alignment.CENTER)
        cap = self._text_layout(cr, format_items(node.count), T.FONT_CAPTION, Pango.Weight.NORMAL, width,
                                align=Pango.Alignment.CENTER)
        _i, bl = big.get_pixel_extents()
        _i, sl = small.get_pixel_extents()
        _i, cl = cap.get_pixel_extents()
        show_cap = rc > 70
        total_h = sl.height + bl.height + (cl.height + 2 if show_cap else 0)
        y = cy - total_h / 2
        cr.set_source_rgb(*T.rgb("text_secondary"))
        cr.move_to(cx - width / 2, y)
        PangoCairo.show_layout(cr, small)
        y += sl.height
        cr.set_source_rgb(*T.rgb("text_primary"))
        cr.move_to(cx - width / 2, y - 2)
        PangoCairo.show_layout(cr, big)
        y += bl.height
        if show_cap:
            cr.set_source_rgb(*T.rgb("text_muted"))
            cr.move_to(cx - width / 2, y)
            PangoCairo.show_layout(cr, cap)

    def _rounded(self, cr, x, y, w, h, r):
        cr.new_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def _card_box(self, cr, w, h, cw, ch):
        px, py = self.pointer
        cx, cy, _R = self._geom(w, h)
        # open away from the centre so the centre label stays readable
        x = px + 18 if px >= cx else px - cw - 18
        y = py + 14 if py >= cy else py - ch - 14
        if x + cw > w - 8:
            x = px - cw - 18
        if x < 8:
            x = px + 18
        if y + ch > h - 8:
            y = py - ch - 14
        if y < 8:
            y = py + 14
        x = max(8, min(x, w - cw - 8))
        y = max(8, min(y, h - ch - 8))
        # shadow
        for i, a in enumerate((0.20, 0.10, 0.05)):
            self._rounded(cr, x - i, y + 3 + i, cw + 2 * i, ch + 2 * i, T.HOVER_CARD_RADIUS + i)
            cr.set_source_rgba(0, 0, 0, a)
            cr.fill()
        self._rounded(cr, x, y, cw, ch, T.HOVER_CARD_RADIUS)
        cr.set_source_rgba(*T.rgb("bg_panel"), 0.98)
        cr.fill_preserve()
        cr.set_source_rgb(*T.rgb("line"))
        cr.set_line_width(1)
        cr.stroke()
        return x, y

    def _draw_hint(self, cr, w, h, text):
        lay = self._text_layout(cr, text, T.FONT_CAPTION)
        _i, ext = lay.get_pixel_extents()
        pad = 9
        x, y = self._card_box(cr, w, h, ext.width + pad * 2, ext.height + pad * 2 - 2)
        cr.set_source_rgb(*T.rgb("text_secondary"))
        cr.move_to(x + pad, y + pad - 1)
        PangoCairo.show_layout(cr, lay)

    def _draw_card(self, cr, w, h):
        a = self.hover
        pad = T.HOVER_CARD_PAD
        cw = 268
        inner = cw - pad * 2
        if a.is_smaller:
            title = _("Smaller items")
            path = pretty_path(a.top.path()) if a.top is not None else ""
            parent_size = a.top.size if a.top is not None else 0
            detail = _("%s merged so the chart stays readable") % format_items(len(a.children_nodes or []))
        else:
            n = a.node
            title = n.label()
            path = pretty_path(n.path())
            parent_size = n.parent.size if n.parent is not None else 0
            if n.flags & OTHER_DRIVE:
                detail = _("Another filesystem · click to scan it")
            elif n.flags & LINK:
                detail = _("Symbolic link · not followed")
            else:
                detail = None
        share = (a.size / parent_size * 100) if parent_size else 0
        parent_name = (a.top.label() if a.is_smaller and a.top is not None
                       else (a.node.parent.label() if a.node is not None and a.node.parent is not None else ""))
        lines = []
        lp = self._text_layout(cr, path, T.FONT_SECTION, width=inner, ellipsize=Pango.EllipsizeMode.START)
        lt = self._text_layout(cr, title, T.FONT_ROW, Pango.Weight.SEMIBOLD, width=inner - 18)
        lines.append((lp, "text_muted", 4))
        lines.append((lt, "text_primary", 6))
        stats = "%s  ·  %s" % (format_size(a.size), format_items(a.count))
        ls = self._text_layout(cr, stats, T.FONT_CAPTION, Pango.Weight.MEDIUM, width=inner)
        lines.append((ls, "text_secondary", 8))
        lshare = self._text_layout(cr, _("%.1f%% of %s") % (share, parent_name) if parent_name else "",
                                   T.FONT_CAPTION, width=inner)
        if detail:
            ld = self._text_layout(cr, detail, T.FONT_CAPTION, width=inner)
        hs = [l.get_pixel_extents()[1].height for l, _c, _g in lines]
        bar_h = 4
        ch = pad + sum(hs) + sum(g for _l, _c, g in lines) + bar_h + 6 + lshare.get_pixel_extents()[1].height + pad
        if detail:
            ch += ld.get_pixel_extents()[1].height + 4
        x, y = self._card_box(cr, w, h, cw, ch)
        yy = y + pad
        for (l, col, gap), hh in zip(lines, hs):
            xx = x + pad
            if l is lt:
                cr.set_source_rgb(*a.color)
                cr.arc(xx + 5, yy + hh / 2, 4.5, 0, TAU)
                cr.fill()
                xx += 16
            cr.set_source_rgb(*T.rgb(col))
            cr.move_to(xx, yy)
            PangoCairo.show_layout(cr, l)
            yy += hh + gap
        # share bar
        cr.set_source_rgb(*T.rgb("line"))
        self._rounded(cr, x + pad, yy, inner, bar_h, bar_h / 2)
        cr.fill()
        cr.set_source_rgb(*a.color)
        self._rounded(cr, x + pad, yy, max(bar_h, inner * min(1.0, share / 100)), bar_h, bar_h / 2)
        cr.fill()
        yy += bar_h + 6
        cr.set_source_rgb(*T.rgb("text_secondary"))
        cr.move_to(x + pad, yy)
        PangoCairo.show_layout(cr, lshare)
        yy += lshare.get_pixel_extents()[1].height + 4
        if detail:
            cr.set_source_rgb(*T.rgb("accent") if a.node is not None and a.node.flags & OTHER_DRIVE
                              else T.rgb("text_muted"))
            cr.move_to(x + pad, yy)
            PangoCairo.show_layout(cr, ld)

    # ── input ────────────────────────────────────────────────────────────
    def _set_hover(self, arc, on_center):
        if arc is not self.hover:
            if self.hover is not None:
                k = id(self.hover.node) if self.hover.node is not None else id(self.hover)
                if k in self.hover_anim:
                    self.hover_anim[k][1] = 0.0
            if arc is not None:
                k = id(arc.node) if arc.node is not None else id(arc)
                v = self.hover_anim.setdefault(k, [0.0, 1.0])
                v[1] = 1.0
            self.hover = arc
            self._ensure_tick()
        if on_center != self.hover_center:
            self.hover_center = on_center
        self.set_cursor_from_name("pointer" if (arc is not None or (on_center and self.center is not None
                                                                     and self.center.parent is not None))
                                  else None)
        self.queue_draw()

    def _on_motion(self, ctl, x, y):
        self.pointer = (x, y)
        kind, arc = self._hit(x, y)
        self._set_hover(arc, kind == "center")

    def _on_leave(self, ctl):
        self.pointer = None
        self._set_hover(None, False)

    def _on_click(self, gesture, n_press, x, y):
        self.grab_focus()
        self.keyboard_focus_visible = False
        kind, arc = self._hit(x, y)
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if kind == "center":
            if n_press == 1:
                self.go_up()
            return
        if arc is None:
            if not ctrl and not shift:
                self.selection.clear()
            return
        self.focus_arc = arc
        if arc.is_smaller:
            if n_press == 1:
                self.emit("special-activated", arc)
            return
        node = arc.node
        if node.flags & OTHER_DRIVE:
            if n_press == 1:
                self.emit("special-activated", arc)
            return
        if n_press >= 2:
            if node.children and not (node.flags & FILES):
                self.selection.set([])
                self.zoom_to(node)
            return
        if ctrl:
            self.selection.toggle(node)
        elif shift:
            self._select_run(arc)
        else:
            self.selection.set([node])

    def _select_run(self, arc):
        anchor = self.selection.anchor
        ring = self.layout.by_ring.get(arc.depth, [])
        a_arc = self.layout.by_node.get(id(anchor)) if anchor is not None else None
        if a_arc is None or a_arc.depth != arc.depth:
            self.selection.set([arc.node])
            return
        i0, i1 = sorted((a_arc.index, arc.index))
        nodes = [a.node for a in ring[i0:i1 + 1] if a.node is not None]
        # keep the anchor where it was
        nodes.sort(key=lambda n: n is anchor)
        self.selection.set(nodes)
        self.selection.anchor = anchor

    def _on_right_click(self, gesture, n_press, x, y):
        self.grab_focus()
        kind, arc = self._hit(x, y)
        if arc is not None and arc.node is not None and not arc.is_smaller and \
                not (arc.node.flags & OTHER_DRIVE):
            if arc.node not in self.selection:
                self.selection.set([arc.node])
        elif kind == "center" and self.center is not None:
            pass
        elif arc is None:
            return
        self.emit("context-menu", x, y, arc)

    def select_all(self):
        if self.center is None:
            return
        kids = [k for k in self.center.sorted_children(self.show_hidden) if not (k.flags & OTHER_DRIVE)]
        self.selection.set(kids)

    def invert_selection(self):
        if self.center is None:
            return
        kids = [k for k in self.center.sorted_children(self.show_hidden) if not (k.flags & OTHER_DRIVE)]
        self.selection.set([k for k in kids if k not in self.selection])

    def _ring_list(self, depth):
        return self.layout.by_ring.get(depth, []) if self.layout else []

    def focus_point(self):
        """Pointer-like position of the focused arc (for Menu / Shift+F10)."""
        a = self.focus_arc
        cx, cy, R = self._geom()
        if a is None:
            return cx, cy
        r0, r1 = self._ring_radii(R, a.depth)
        m = a.mid - math.pi / 2
        r = (r0 + r1) / 2
        return cx + math.cos(m) * r, cy + math.sin(m) * r

    def _on_key(self, ctl, keyval, keycode, state):
        if self.layout is None:
            return False
        name = Gdk.keyval_name(keyval) or ""
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        nav = {"Left", "Right", "Up", "Down", "KP_Left", "KP_Right", "KP_Up", "KP_Down"}
        if name in nav:
            self.keyboard_focus_visible = True
            f = self.focus_arc
            if f is None:
                ring = self._ring_list(1)
                self.focus_arc = ring[0] if ring else None
            else:
                ring = self._ring_list(f.depth)
                if name.endswith("Right"):
                    self.focus_arc = ring[(f.index + 1) % len(ring)]
                elif name.endswith("Left"):
                    self.focus_arc = ring[(f.index - 1) % len(ring)]
                elif name.endswith("Up"):
                    kids = [a for a in self._ring_list(f.depth + 1) if a.parent is f]
                    if kids:
                        self.focus_arc = kids[0]
                elif name.endswith("Down"):
                    if f.parent is not None:
                        self.focus_arc = f.parent
            self._announce_focus()
            self.queue_draw()
            return True
        if name in ("Return", "KP_Enter"):
            f = self.focus_arc
            if f is not None and f.node is not None:
                if f.node.flags & OTHER_DRIVE:
                    self.emit("special-activated", f)
                else:
                    self.zoom_to(f.node)
            return True
        if name == "BackSpace":
            self.go_up()
            return True
        if name == "space":
            f = self.focus_arc
            if f is not None and f.node is not None and not f.is_smaller:
                self.selection.toggle(f.node)
            return True
        if name == "Menu" or (name == "F10" and shift):
            f = self.focus_arc
            if f is not None and f.node is not None and f.node not in self.selection and not f.is_smaller:
                self.selection.set([f.node])
            x, y = self.focus_point()
            self.emit("context-menu", x, y, f)
            return True
        return False

    def _announce_focus(self):
        f = self.focus_arc
        if f is None:
            return
        label = _("Smaller items") if f.is_smaller else f.node.label()
        self.update_property([Gtk.AccessibleProperty.DESCRIPTION],
                             ["%s, %s" % (label, format_size(f.size))])

    def _on_drag_prepare(self, source, x, y):
        kind, arc = self._hit(x, y)
        if arc is None or arc.node is None or arc.is_smaller or arc.node.flags & OTHER_DRIVE:
            return None
        if arc.node not in self.selection:
            self.selection.set([arc.node])
        files = []
        for n in self.selection.top_level():
            if n.flags & FILES:
                continue
            files.append(Gio.File.new_for_path(n.path()))
        if not files:
            return None
        return Gdk.ContentProvider.new_for_value(Gdk.FileList.new_from_list(files))


def _display_root(node):
    from .util import display_name
    return display_name(node.path())
