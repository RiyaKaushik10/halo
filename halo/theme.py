# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Riya K
"""Design tokens for Halo.

Every colour, size and timing used by the Cairo drawing code lives here.
The same colour values are declared in style.css with @define-color.
"""

import colorsys

# ── Colour tokens (section 3) ──────────────────────────────────────────────
COLORS = {
    "bg_window": "#0E1424",
    "bg_sidebar": "#141B2E",
    "bg_panel": "#18203A",
    "bg_raised": "#1F2945",
    "line": "#2A3557",
    "text_primary": "#E8ECF6",
    "text_secondary": "#9AA6C4",
    "text_muted": "#6B7799",
    "accent": "#4F8CFF",
    "danger": "#FF5470",
    "success": "#3DDC97",
    "warning": "#FFB547",
}

# ── File kinds and the sunburst palette ────────────────────────────────────
KIND_DOCUMENTS = 0
KIND_IMAGES = 1
KIND_VIDEO = 2
KIND_AUDIO = 3
KIND_ARCHIVES = 4
KIND_CODE = 5
KIND_OTHER = 6
NUM_KINDS = 7

KIND_NAMES = ["Documents", "Images", "Video", "Audio", "Archives", "Apps and code", "System and other"]
KIND_COLORS = ["#4F8CFF", "#2ED3B7", "#9B6BFF", "#FF8A3D", "#FFC93D", "#FF5FA2", "#7A88B0"]
KIND_ICONS = [
    "x-office-document-symbolic",
    "image-x-generic-symbolic",
    "video-x-generic-symbolic",
    "audio-x-generic-symbolic",
    "package-x-generic-symbolic",
    "halo-code-symbolic",
    "application-x-executable-symbolic",
]
FOLDER_ICON = "folder-symbolic"
LINK_ICON = "halo-link-symbolic"
DRIVE_ICON = "drive-harddisk-symbolic"
REMOVABLE_ICON = "drive-removable-media-symbolic"

# Rotation used when colouring by top folder.
TOP_FOLDER_COLORS = ["#4F8CFF", "#2ED3B7", "#9B6BFF", "#FF8A3D", "#FFC93D", "#FF5FA2",
                     "#5CC8FF", "#8BE26B", "#C38BFF", "#FF7A7A"]

# ── Sunburst geometry (fractions of the chart radius) ──────────────────────
CENTER_RADIUS = 0.22
RING_WIDTH = 0.14
DEFAULT_RINGS = 5
ARC_GAP_PX = 1.5
MERGE_MIN_DEGREES = 0.6
MIN_SPECIAL_DEGREES = 1.6          # "Other drive" wedges stay visible
CENTER_RING_PX = 2.0
SELECTED_OUTLINE_PX = 2.0
SCOPE_OUTLINE_PX = 1.0
SCOPE_OUTLINE_ALPHA = 0.35
FOCUS_OUTLINE_PX = 2.0
UNSELECTED_DIM = 0.80
DEPTH_LIGHTEN = 0.08
DEPTH_LIGHTNESS_CAP = 0.84      # outer rings stay coloured, never washed-out white
HOVER_LIGHTEN = 0.12
HOVER_LIFT_PX = 4.0
CHART_MARGIN_PX = 18.0
GLOW_ALPHA = 0.22
RADAR_PERIOD_S = 2.4
RADAR_ALPHA = 0.25
RADAR_TRAIL_RAD = 1.15
SMALLER_ITEMS_COLOR = "#46527A"
OTHER_DRIVE_COLOR = COLORS["line"]

# ── Motion ────────────────────────────────────────────────────────────────
HOVER_MS = 150
VIEW_MS = 250
ZOOM_MS = 350
REMOVE_MS = 250
HOLD_TO_CONFIRM_S = 1.5
SCAN_PUSH_MS = 100

# ── Typography (px) ───────────────────────────────────────────────────────
FONT_CENTER = 28
FONT_HEADER = 20
FONT_ROW = 15
FONT_CAPTION = 13
FONT_SECTION = 12

# ── Layout (px) ───────────────────────────────────────────────────────────
WINDOW_DEFAULT = (1280, 800)
WINDOW_MIN = (960, 600)
SIDEBAR_WIDTH = 280
RAIL_WIDTH = 72
PANEL_WIDTH = 380
PANEL_NARROW_WIDTH = 320
COLLAPSE_BELOW = 1100
ROW_HEIGHT = 52
CARD_RADIUS = 16
HOVER_CARD_RADIUS = 12
HOVER_CARD_PAD = 12


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def rgb(token):
    """RGB tuple for a colour token name."""
    return hex_to_rgb(COLORS[token])


def lighten(color, amount, cap=0.95):
    """Raise HSL lightness by `amount` (0..1). Accepts hex or an rgb tuple."""
    r, g, b = hex_to_rgb(color) if isinstance(color, str) else color
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.0, min(max(cap, l), l + amount))
    return colorsys.hls_to_rgb(h, l, s)


def kind_rgb(kind, depth=0):
    return lighten(KIND_COLORS[kind], DEPTH_LIGHTEN * depth, DEPTH_LIGHTNESS_CAP)


def css_hex(rgbt):
    return "#%02X%02X%02X" % tuple(int(round(c * 255)) for c in rgbt)


def ease_out_cubic(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3
