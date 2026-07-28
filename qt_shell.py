"""qt_shell.py - the live focus-aid + capture window (sections 5 and 6 wired
together: exposure panel, capture-enforces-lock, burst/HDR walkthroughs with
a real worker thread, the ruler, and calibration integration all built).

Session/profile management (Session, load_profile/save_profile, new_session_dir,
record_capture/record_burst/record_hdr, ...) lives in provenance.py (BUILD_LIST
Tier 3 item 1, phase 1) -- generic workflow code (session folders, metadata,
profile persistence), not sensor-specific, so it no longer needs to sit inside
this GUI file just because this was its only caller at the time it got baked
in from the old capture.py. Reached here as provenance.X throughout, never a
`from provenance import X` (see provenance.py's own comment on OUT_ROOT/
PROFILE_PATH for why). camera_backend.py stays the place for anything that IS
sensor-specific (IMX477 resolutions, lores format, ON-RIG lines).

The tick is the whole loop: pull the most recent lores frame from the seam, run
the meter on it, render the box and bar into an RGBA overlay, hand that overlay
to set_overlay. The overlay is a separate display layer and never touches a
capturable pixel, the same rule the score obeys.

Two ways to run:
  python3 qt_shell.py --render-check   headless: exercises the pure overlay art,
                                       the letterbox mouse math, the shutter stop
                                       table, and record_capture, no PyQt5, no
                                       camera. Same self-check spirit as the rest.
  python3 qt_shell.py                  the GUI on the FakeCamera: a real window,
                                       real overlay, real box drag and resize,
                                       a working exposure panel, with no hardware.
  python3 qt_shell.py --camera         the GUI on the Pi camera. This is the run
                                       that finally exercises the ON-RIG lines in
                                       camera_backend.py.

--no-onboarding suppresses the one-time "calibrate now?" prompt even on a
launch that does have a real display -- for a scripted/automated launch that
shouldn't be interrupted. The prompt already self-suppresses automatically on
a headless/offscreen/no-display launch (see should_show_onboarding_gate /
_onboarding_session_is_interactive); this flag is for the separate case of a
real display that should still skip it.

render_overlay, the geometry helpers, the shutter stop table, and record_capture
are pure and Qt-free, so they are tested by --render-check without a display.
The window and the fake preview are the only Qt-bound parts.

RECONSTRUCTION NOTE (2026-07-11): rebuilt from verified fragments pulled out of
a prior conversation's tool-call history after the on-disk project copy was
found to be stale (missing everything past the section-6 focus aid). Every
piece of exposure/capture logic below (the shutter stop table, the debounce,
_enforce_exposure_lock, record_capture, the QSplitter layout, capture_status's
fixed-height fix) was matched against a direct quote from that history. A
handful of small mechanical slots (_on_gain, _on_red, _on_blue, _on_ae_toggled,
_on_awb_toggled, _apply_panel_values, the Reprobe worker thread, gui_prefs
persistence) were not quoted verbatim and are written here to match the
patterns that WERE quoted; those are marked inline. Burst wiring (the actual
ask for this session) is new code, not reconstruction, added after this base.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

# Force XWayland (xcb) over Qt's native "wayland" QPA platform, before PyQt5
# resolves one at QApplication construction time -- setdefault so an explicit
# QT_QPA_PLATFORM in the environment still wins. self.preview (the real
# QGlPicamera2 on-rig, --camera) is a WA_NativeWindow child widget doing its
# own direct EGL rendering, not a top-level window. Nested native child
# windows are a documented, real limitation of Qt5's native Wayland platform
# plugin: their underlying surface does not reliably follow the widget's own
# Qt-side resize/reposition once the TOP-LEVEL window's own geometry changes
# out from under them (confirmed on-rig: full screen entry correctly resizes
# the top-level window and even fires the preview's own resizeEvent/glViewport
# call, but the actual visible native surface stays stuck at its old small
# size/position regardless). X11 child subwindows (available here via the
# already-running XWayland, confirmed with QApplication().platformName()) do
# not have this limitation -- decades-old, fully dynamic subwindow support.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import numpy as np

try:
    from .camera_backend import FakeCamera, LORES_RES, FULL_RES, PREVIEW_RES
    from .focus import FocusMeter, FocusBox, FocusState, BarState, score_capture_sharpness
except ImportError:                 # run directly as a script, not as a package module
    from camera_backend import FakeCamera, LORES_RES, FULL_RES, PREVIEW_RES
    from focus import FocusMeter, FocusBox, FocusState, BarState, score_capture_sharpness

# provenance.py: session creation, per-capture sidecar writing, and the
# profile store (BUILD_LIST Tier 3 item 1, phase 1) -- pulled out of this
# file into its own module. Not optional like the guarded imports below:
# this file's own capture flow cannot function without it, so no
# try/except-to-None fallback the way stacks/calibrate/measure/gallery get;
# only the package-vs-script import shape varies. Reference OUT_ROOT/
# PROFILE_PATH ONLY as provenance.OUT_ROOT/provenance.PROFILE_PATH -- see
# provenance.py's own comment on those two names for why a `from` import
# would silently break render_check()'s test-isolation mutation of them.
try:
    from . import provenance
except ImportError:
    import provenance

# stacks.py's tagging (apply_tag/output_name): the pure, camera-free half of
# z-stack support (section 1's own seam rule -- this is exactly the kind of
# logic that belongs off the camera side). None (the Tag action just stays
# disabled) if stacks.py is not alongside this file.
try:
    from . import stacks as _stacks
except ImportError:
    try:
        import stacks as _stacks
    except ImportError:
        _stacks = None

# calibrate.py's own append-only calibration store, reused so the ruler reads
# EXACTLY the same current-calibration logic calibrate.py itself uses, never a
# second copy of that lookup. None (ruler quietly unavailable, not a crash) if
# calibrate.py is not alongside this file.
try:
    from . import calibrate as _calibrate
except ImportError:
    try:
        import calibrate as _calibrate
    except ImportError:
        _calibrate = None

# --- MEASURE MENU (separable): measure.py's own analysis GUI, opened from a
# menu action, the same pattern as the Calibrate menu below. Unlike
# ca_measure.py's CAWizard, measure.py never constructs its own camera (it
# only opens already-captured files), so there is no hardware-sharing risk
# to resolve first -- this is a safe integration on its own, no
# camera-conflict caveat.
try:
    from . import measure as _measure
except ImportError:
    try:
        import measure as _measure
    except ImportError:
        _measure = None

# gallery.py's shared capture browser (BUILD_LIST Tier 3 item 4), reused here
# for the standalone "Browse captures" action. None (the menu action reports
# unavailable rather than crashing) if gallery.py is not alongside this file.
try:
    from . import gallery as _gallery
except ImportError:
    try:
        import gallery as _gallery
    except ImportError:
        _gallery = None

# process_wizard.py's choose-your-operations processing wizard (BUILD_LIST
# Tier 3 item 5), a separate path from "Process session..." below -- see its
# own module docstring for why the two coexist. None (menu action reports
# unavailable rather than crashing) if process_wizard.py is not alongside
# this file.
try:
    from . import process_wizard as _process_wizard
except ImportError:
    try:
        import process_wizard as _process_wizard
    except ImportError:
        _process_wizard = None

# plane_cache.py's green-plane cache (Preferences-dialog plan set, Part 04):
# the substrate a committed live measurement (Part 05) points at. None (the
# Advanced tab's clean-now/auto-clean controls report unavailable rather
# than crashing) if plane_cache.py is not alongside this file.
try:
    from . import plane_cache as _plane_cache
except ImportError:
    try:
        import plane_cache as _plane_cache
    except ImportError:
        _plane_cache = None

# annotations.py's mark store (Preferences-dialog plan set, Part 05): the
# live measure panel builds marks with the same build_*_mark calls
# measure.py's own commit path uses, and writes them with the same
# save_mark, once committed. None (the Live measure... action reports
# unavailable rather than crashing) if annotations.py is not alongside
# this file.
try:
    from . import annotations as _annotations
except ImportError:
    try:
        import annotations as _annotations
    except ImportError:
        _annotations = None

# pixel_hash.py (Preferences-dialog plan set, Part 05): the live measure
# panel hashes a frozen plane itself (the same key plane_cache.store_plane
# and annotations.save_mark both key on), rather than trusting store_plane
# to compute it silently -- so the hash used to inject the first click's
# point and to look up the record afterward is known to be the exact same
# value the cache and the store settled on.
try:
    from . import pixel_hash as _pixel_hash
except ImportError:
    try:
        import pixel_hash as _pixel_hash
    except ImportError:
        _pixel_hash = None

# export.py / publish.py (MeasureWindow extraction, step 3): dedicated
# File-menu actions for the store-wide Export and the per-image Publish,
# relocated out of MeasureWindow (which is not deleted this step -- see
# HANDOFF.md's step-3 section). None (the menu action reports unavailable
# rather than crashing) if either module is not alongside this file.
try:
    from . import export as _export
except ImportError:
    try:
        import export as _export
    except ImportError:
        _export = None

try:
    from . import publish as _publish
except ImportError:
    try:
        import publish as _publish
    except ImportError:
        _publish = None

# The green plane calibrate.py measures on: half the sensor's resolution each
# axis (see debayer.py's extract_green / the build checklist's own invariant).
# The ruler's field-of-view-in-microns is derived from THIS width/height, not
# the lores preview's own pixel count, since um_per_px in calibration.json is
# a green-plane number.
GREEN_PLANE_RES = (FULL_RES[0] // 2, FULL_RES[1] // 2)

# --- Session and profile management -----------------------------------
# Generic capture workflow: session folders, profile persistence, metadata
# recording. Not IMX477-specific; reusable with any camera sensor. Moved to
# provenance.py (BUILD_LIST Tier 3 item 1, phase 1) -- OUT_ROOT/PROFILE_PATH/
# Session/load_profile/save_profile/record_capture/record_burst/record_hdr
# all live there now; reference them as provenance.X, never a `from` import
# (see provenance.py's own comment on OUT_ROOT/PROFILE_PATH for why).

DEFAULT_BURST = 8
MAX_BURST = 10
DEFAULT_STOPS = [-2.0, -1.0, 0.0, 1.0, 2.0]
PROCESSOR = Path(__file__).resolve().parent / "hdr_from_session.py"
# GREEN-PLANE EXTRACTION UTILITY (BUILD_LIST Tier 1 item 4): debayer.py
# itself does the real work (--green, zero interpolation); this is just a
# menu action wrapping that existing tool, invoked the same by-absolute-
# path subprocess pattern PROCESSOR already uses above.
DEBAYER_TOOL = Path(__file__).resolve().parent / "debayer.py"

# CASUAL MODE (BUILD_LIST Tier 3 item 2) is superseded in full (Preferences-
# dialog plan set, PLAN_00_context_and_supersession.md): there is no toggle,
# no second window class, no launch branch, no separate layout, and (Part
# 03) no casual_mode.py file any more -- its capture-and-save logic, format
# handling, and JPG-first delivery are all lifted into this file's own main
# capture path (see _auto_process/_run_process_cmd and PreferencesDialog's
# "Additional export formats" row).

# --- THEMES (BUILD_LIST Tier 1 item 3) --------------------------------------
# Deliberately open-ended, not a fixed Dark/Light pair: the user plans to
# design a dozen-plus side-panel aesthetics over time, so the Theme menu is
# built by SCANNING this folder, never a hardcoded list -- dropping in a new
# themes/<name>/style.qss is the entire integration step, no code change
# ever needed. See discover_themes/load_theme_stylesheet below for the exact
# contract; the side panel itself carries objectName "side_panel" so a
# theme's QSS has something precise to target.
THEMES_ROOT = Path(__file__).resolve().parent / "themes"


def discover_themes(themes_root=None):
    """Every theme found under themes_root: one entry per immediate
    subdirectory that contains a style.qss, sorted by name. Returns
    [(name, qss_path), ...] -- [] if the folder doesn't exist or holds
    nothing yet, which is a normal, expected state (no themes designed
    yet), not an error. Qt-free, so the menu-building logic this feeds is
    testable without PyQt5."""
    themes_root = Path(themes_root) if themes_root is not None else THEMES_ROOT
    if not themes_root.is_dir():
        return []
    found = []
    for d in sorted(themes_root.iterdir()):
        qss = d / "style.qss"
        if d.is_dir() and qss.is_file():
            found.append((d.name, qss))
    return found


def resolve_theme_qss_path(theme_name, themes_root=None):
    """The style.qss path for a persisted theme preference, or None if no
    preference is set, or the named theme is no longer found (its folder
    was deleted/renamed since it was chosen) -- degrades to the stock Qt
    look rather than crashing main() on a stale preference."""
    if theme_name is None:
        return None
    for name, qss_path in discover_themes(themes_root):
        if name == theme_name:
            return qss_path
    return None


def load_theme_stylesheet(qss_path):
    """A theme's style.qss with {{ASSETS}} substituted for that theme's own
    assets/ folder (its ABSOLUTE path), so a theme package stays portable
    and self-contained: plain QSS url() paths resolve against the running
    app's working directory, not the stylesheet's own location, which would
    silently break image references the moment the app is launched from
    anywhere else. A theme author writes url({{ASSETS}}/panel_bg.png) once
    and it resolves correctly regardless of where qt_shell.py is run from."""
    qss_path = Path(qss_path)
    text = qss_path.read_text()
    assets_dir = qss_path.parent / "assets"
    return text.replace("{{ASSETS}}", str(assets_dir))


def default_green_output_path(raw_path):
    """Where a green-plane extraction would land with no explicit output
    given -- <raw's own dir>/<raw's own stem>_green.tif. Matches
    debayer.py's own CLI default naming EXACTLY (main()'s own
    `stem.parent / (stem.name + "_green.tif")` when no -o is given), so a
    file this menu action writes has the identical name someone would get
    running debayer.py --green on the same input by hand -- one naming
    rule, not two."""
    raw_path = Path(raw_path)
    return raw_path.with_suffix("").parent / (raw_path.stem + "_green.tif")


def build_display_flags(args):
    """Display-processing flags for hdr_from_session.py, from this file's own
    launch flags (byte-identical to the shape the original capture.py CLI
    built, so a processing run kicked off here matches one kicked off there).
    --wl/--lw are always present (main() gives them defaults). --ca is
    absolutised because the processor runs inside the session dir, where a
    relative calibration path would no longer resolve. --sharpen checks
    `is not None`, not truthiness, so an explicit --sharpen 0 still reaches
    the processor rather than being silently dropped."""
    flags = ["--wl", str(args.wl), "--lw", str(args.lw)]
    if args.gains:
        flags += ["--gains", str(args.gains[0]), str(args.gains[1])]
    if args.ca:
        flags += ["--ca", str(Path(args.ca).resolve())]
    if args.sharpen is not None:
        flags += ["--sharpen", str(args.sharpen)]
    if args.shadow_deepen:
        flags += ["--shadow-deepen"]
    if args.archive_raws:
        flags += ["--archive-raws"]
    return flags

# --- RECORD BUTTON (separable): video's own output folder, deliberately NOT
# a Session -- no sidecar, no pixel hash, no session record, documentation/
# review only. To remove the whole Record feature: delete this constant, the
# record_btn widget and its row in the panel layout, _toggle_recording, the
# recording checks in _start_capture/_walkthrough_burst/_walkthrough_hdr/
# _walkthrough_batch, and the four new CameraBackend verbs in
# camera_backend.py (start_recording/stop_recording/is_recording, plus their
# FakeCamera/Picamera2Camera implementations). Nothing else depends on any
# of it existing.
VIDEO_OUT_ROOT = provenance.OUT_ROOT / "video"

try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QWidget,
                                 QVBoxLayout, QPushButton, QSlider, QCheckBox,
                                 QHBoxLayout, QSplitter, QMessageBox, QInputDialog,
                                 QDialog, QComboBox, QActionGroup, QFileDialog,
                                 QFormLayout, QGroupBox, QSpinBox, QLineEdit,
                                 QDialogButtonBox, QStackedLayout, QMenu,
                                 QGraphicsView, QGraphicsScene, QButtonGroup, QFrame)
    from PyQt5.QtCore import QTimer, Qt, QRect, QEvent, pyqtSignal, QObject, QPointF
    from PyQt5.QtGui import (QImage, QPainter, QKeyEvent, QCloseEvent, QPen,
                             QColor, QPolygonF, QMouseEvent)
    _HAVE_QT = True
except ImportError:                 # PyQt5 absent: --render-check still runs
    _HAVE_QT = False

MIN_FRAC = 0.03                      # smallest box a resize will commit (fractional)
HANDLE_FRAC = 0.05                   # how near a corner counts as grabbing it

# Appended to the window title only while _is_fullscreen (see
# _toggle_fullscreen), matched by a labwc rc.xml <windowRule> to trigger
# labwc's own ToggleAlwaysOnTop action -- the one thing that actually gets
# this "fake fullscreen" window (see the comment on self._is_fullscreen in
# FocusPreviewWindow.__init__ for why it's not a real showFullScreen())
# to raise above the desktop taskbar's wlr-layer-shell surface. No square
# brackets or other glob(7) metacharacters -- rc.xml title matching is a
# shell wildcard pattern, not a literal string.
FULLSCREEN_TITLE_MARKER = " ZYNERGY-FULLSCREEN-MARKER"

SHUTTER_STEPS = 200                  # (legacy name kept for the slider's int range
                                      # semantics; the table below is what's authoritative)
GAIN_STEPS = 1000
AWB_GAIN_RANGE = (0.5, 4.0)   # ColourGains span the sliders drive; sensor takes wider
LONG_EXPOSURE_MAX_US = 3_000_000   # 3.0s cap, per the earlier explicit decision
# Day-to-day shutter ceiling when Long Exposure is unchecked. Deliberately NOT
# derived from camera_controls' reported ExposureTime max (see the FIX comment
# where this is used): that value is the sensor's raw capability, not a sane
# default operating range. Matches the disable-path fallback already used in
# Picamera2Camera.set_long_exposure.
NORMAL_SHUTTER_MAX_US = 50_000

PREFS_PATH = Path.home() / ".zynergy" / "gui_prefs.json"

# --- VIDEO/CAPTURE RESOLUTION (BUILD_LIST Tier 1 item 5; Preferences-dialog
# plan set, Part 01/02) --------------------------------------------------
# camera_backend.py's set_video_resolution() validates input but, as its own
# docstring says plainly, currently has NO live effect: a recording always
# encodes the preview config's fixed "main" stream, built once when the
# camera is constructed via start_encoder() against "main" -- never through
# self._video_res. Capture resolution genuinely works this way (feeding
# Picamera2Camera's full_res constructor parameter, applied on next launch);
# video resolution does not, because nothing consumes self._video_res yet.
#
# ROADMAP item 1 correction: this file used to also feed the persisted
# "video_resolution" pref into preview_res at camera construction (a
# video_resolution_kwargs() analogous to capture_resolution_kwargs() below).
# That was wrong on two counts. First, main's size IS preview_res, and lores
# (LORES_RES, hardcoded 4:3) is paired against it at create_preview_
# configuration() time -- so a non-4:3 video-resolution preference silently
# broke the lores stream pairing, killing focus aid for the life of the
# process (root cause of the "focus aid dies on non-4:3 resolution" bug).
# Second, that preview_res detour was never what the preference claimed to
# do: it happened to change the recorded file's size only as a side effect
# of start_recording() encoding whatever "main" is, not because anything
# reads self._video_res. Decoupled: preview_res construction no longer
# depends on this preference at all (removed from main()'s camera-
# construction kwargs, below), and the "Video resolution" control in
# Preferences is now display-only with a tooltip explaining why, same
# pattern as the capture/video FORMAT controls just below it, which persist
# a preference nothing reads yet rather than pretending to apply it.
# Wiring this for real means the Record-button rework building its own video
# config from self._video_res -- explicitly out of scope here, since
# encoding at a size other than main means either a mode switch on record
# start or a third stream, and both are exactly the pairing/mode-switch
# fragility that produced this bug in the first place.


def capture_resolution_kwargs(pref=None):
    """Camera-construction kwargs for the persisted "capture_resolution"
    pref: {} if none set (the camera's own FULL_RES default applies), else
    {"full_res": (w, h)}. Qt-free and camera-free, so main()'s wiring is
    testable without constructing a real window or camera."""
    if pref is None:
        return {}
    w, h = pref
    return {"full_res": (int(w), int(h))}


def format_lores_config_summary(cfg):
    """Renders camera_backend.py's Picamera2Camera.lores_config_at_failure
    (a plain dict from _summarize_camera_configuration, or None if no
    genuine decode failure has happened yet) into the focus-aid readout's
    diagnostic text. Whether "lores" is in the active config is stated
    explicitly rather than left for the reader to infer from the stream
    list, since that's the single fact candidate 1 (create_preview_
    configuration() silently dropping lores during its own validation) or
    candidate 2 (some other still-mode/config-drift bug) turns on."""
    if not cfg:
        return "active config not yet captured"
    if "error" in cfg:
        return "camera_configuration() itself failed: {}".format(cfg["error"])
    present = cfg.get("streams_present", [])
    parts = []
    for name in present:
        info = cfg.get(name) or {}
        size = info.get("size")
        size_str = "{}x{}".format(*size) if size else "?"
        parts.append("{}={}@{}".format(name, size_str, info.get("format")))
    streams_str = ", ".join(parts) if parts else "none"
    lores_note = "lores PRESENT" if "lores" in present else "lores MISSING"
    return "streams: {} ({})".format(streams_str, lores_note)


# ---------------------------------------------------------------------------
# Pure geometry (Qt-free, so --render-check covers it)
# ---------------------------------------------------------------------------
def displayed_rect(widget_w, widget_h, img_aspect):
    """Rect (x, y, w, h) of a letterboxed image inside a widget, preserving the
    image aspect (width / height). The preview may not fill the widget, so mouse
    points and the overlay must both be mapped through this rect, not the raw
    widget size. ON-RIG: this assumes the GL preview fits-with-letterbox; if it
    stretches to fill instead, pass the full widget rect."""
    if widget_h <= 0 or widget_w <= 0:
        return 0, 0, max(widget_w, 1), max(widget_h, 1)
    widget_aspect = widget_w / widget_h
    if widget_aspect > img_aspect:                 # widget wider: pillarbox
        h = widget_h
        w = int(round(h * img_aspect))
        return (widget_w - w) // 2, 0, w, h
    h = int(round(widget_w / img_aspect))          # widget taller: letterbox
    return 0, (widget_h - h) // 2, widget_w, h


def frac_from_point(px, py, disp_rect):
    """Map a widget point to fractional field coordinates given the displayed
    image rect. Clamps to [0, 1]."""
    x, y, w, h = disp_rect
    fx = (px - x) / w if w > 0 else 0.0
    fy = (py - y) / h if h > 0 else 0.0
    clamp = lambda v: min(max(v, 0.0), 1.0)
    return clamp(fx), clamp(fy)


def move_box(box, dfx, dfy):
    """Translate a box by a fractional delta, preserving size (so it stays the
    same size and does NOT reset the bar). Position is clamped to the field."""
    w, h = box.width, box.height
    x0 = min(max(box.x0 + dfx, 0.0), 1.0 - w)
    y0 = min(max(box.y0 + dfy, 0.0), 1.0 - h)
    return FocusBox(x0, y0, x0 + w, y0 + h)


def opposite_corner(box, fx, fy, handle=HANDLE_FRAC):
    """If (fx, fy) is within `handle` of a box corner, return the fixed opposite
    corner (for a resize); else None."""
    pairs = (((box.x0, box.y0), (box.x1, box.y1)),
             ((box.x1, box.y0), (box.x0, box.y1)),
             ((box.x0, box.y1), (box.x1, box.y0)),
             ((box.x1, box.y1), (box.x0, box.y0)))
    for (cx, cy), (ox, oy) in pairs:
        if abs(fx - cx) <= handle and abs(fy - cy) <= handle:
            return ox, oy
    return None


# ---------------------------------------------------------------------------
# Live measure panel (Preferences-dialog plan set, Part 05): pure, Qt-free
# helpers. The freeze-triggering click's own preview-widget coordinates
# reuse frac_from_point/displayed_rect above -- this is that same
# preview-to-sensor mapping, scaled into the green plane's own native
# pixel size instead of a fractional focus box.
# ---------------------------------------------------------------------------

LIVE_MEASURE_HIT_RADIUS_PX = 14   # right-click hit-test grab radius, in VIEW-space
                                  # pixels (not scene-space), so the grab stays the
                                  # same regardless of the canvas's own zoom level


def native_point_from_preview_click(px, py, disp_rect, green_plane_res):
    """A preview-widget click (px, py) converted to the frozen green plane's
    own native pixel coordinates -- the exact "preview-to-sensor" mapping
    PLAN_05 calls for. Reuses frac_from_point's existing letterboxing-aware
    fraction (never a naive width/height ratio, which would be wrong the
    moment the widget's aspect differs from the sensor's), then scales that
    fraction into green_plane_res. This is what makes the freeze-triggering
    click usable as the shape tool's own first point, not a throwaway
    trigger click -- see PLAN_05's own reasoning for why an exact
    conversion of the RIGHT input (the click that happened) is fine, even
    though the live preview itself is display-referred."""
    fx, fy = frac_from_point(px, py, disp_rect)
    return fx * green_plane_res[0], fy * green_plane_res[1]


def _live_measure_tool_hint(name):
    return {
        "distance": "distance: click the feed to freeze, then a second point",
        "angle": "angle: click the feed to freeze (vertex), then two arm points",
        "polygon": "polygon: click the feed to freeze, then each vertex, "
                  "double-click to finish (3+ points)",
        "ellipse": "ellipse: click the feed to freeze, then 5+ boundary "
                  "points, double-click to finish",
    }.get(name, "Pick a shape, then click on the live feed.")


def _live_measure_point_status(tool, n):
    if tool == "distance":
        return "distance: {} of 2 points".format(n)
    if tool == "angle":
        return "angle: {} of 3 points (vertex first)".format(n)
    if tool == "polygon":
        return "polygon: {} point(s), double-click to finish (3+ needed)".format(n)
    if tool == "ellipse":
        return "ellipse: {} point(s), double-click to finish (5+ needed)".format(n)
    return ""


def dist_point_to_segment_px(p, a, b):
    """Point-to-segment distance, plain (x, y) tuples in and out -- pure,
    Qt-free. Used by the live measure panel's right-click hit test against
    each mark's own geometry, already converted to VIEW-space coordinates
    by the caller (mapFromScene), so LIVE_MEASURE_HIT_RADIUS_PX means the
    same thing regardless of canvas zoom."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def live_measure_mark_segments(mark):
    """The mark's own geometry, decomposed into (a, b) point-pairs (plain
    (x, y) tuples) for hit-testing -- a distance mark is one segment; angle
    is its two arms (vertex to each); polygon and ellipse (its boundary
    click points, the same shape a polygon's hit test already handles) are
    their closed edge loops. None for an unrecognized mark type. Pure,
    Qt-free: the caller converts each pair to view coordinates."""
    t = mark.get("type")
    if t == "distance":
        p = mark["input"]["points"]
        return [(tuple(p[0]), tuple(p[1]))]
    if t == "angle":
        v = tuple(mark["input"]["vertex"])
        a = tuple(mark["input"]["arm_a"])
        b = tuple(mark["input"]["arm_b"])
        return [(v, a), (v, b)]
    if t == "polygon":
        pts = [tuple(pt) for pt in mark["input"]["points"]]
        n = len(pts)
        return [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    if t == "ellipse":
        pts = [tuple(pt) for pt in mark["input"]["boundary_points"]]
        n = len(pts)
        return [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    return None


# ---------------------------------------------------------------------------
# Live Measuring (PLAN_quick_ruler.md): a pixel-only overlay on the LIVE,
# moving feed -- no freeze, no calibration, nothing committed. Distinct from
# Measure/Part 05 above: this reuses that feature's INTERACTION SHAPE (shape
# picker, click-to-place, right-click menu), never its substrate. Every
# function below is deliberately self-contained pure pixel math -- this
# module boundary must NEVER import calibrate.py/annotations.py/
# provenance.py or call native_point_from_preview_click, the same way
# camera_backend.py must be the only file importing picamera2 (see
# assert_only_camera_backend_imports_picamera2's own structural check) --
# assert_live_measuring_has_no_calibration_dependency() below is this
# feature's own version of that same rule, checked the same way (source
# inspection, not just code review).
# ---------------------------------------------------------------------------

def lores_point_from_preview_click(px, py, disp_rect):
    """A preview-widget click (px, py) converted to LORES_RES-space pixel
    coordinates -- the SAME overlay-buffer space render_overlay_into already
    draws the focus box/bar/ruler into (see FocusPreviewWindow._ov_bufs'
    own shape). Reuses frac_from_point's existing letterboxing-aware
    fraction, same math native_point_from_preview_click (Part 05) uses for
    the green plane -- but deliberately NOT that function: Live Measuring
    reports raw preview pixels, never a calibrated sensor-space value, and
    PLAN_quick_ruler.md is explicit that reusing a function named for that
    other purpose would blur the line this feature exists to keep sharp."""
    fx, fy = frac_from_point(px, py, disp_rect)
    return fx * LORES_RES[0], fy * LORES_RES[1]


def _live_measuring_tool_hint(name):
    return {
        "distance": "distance: click the live feed for two points (px)",
        "angle": "angle: click the vertex, then two arm points",
        "polygon": "polygon: click each vertex, double-click to finish (3+ points)",
        "ellipse": "ellipse: click 5+ boundary points, double-click to finish",
    }.get(name, "Pick a shape, then click on the live feed.")


def _live_measuring_point_status(tool, n):
    if tool == "distance":
        return "distance: {} of 2 points".format(n)
    if tool == "angle":
        return "angle: {} of 3 points (vertex first)".format(n)
    if tool == "polygon":
        return "polygon: {} point(s), double-click to finish (3+ needed)".format(n)
    if tool == "ellipse":
        return "ellipse: {} point(s), double-click to finish (5+ needed)".format(n)
    return ""


def live_measuring_mark_segments(mark):
    """Segment decomposition for hit-testing/drawing a Live Measuring mark
    -- same shape as live_measure_mark_segments above (Part 05's committed-
    mark version), but against Live Measuring's own minimal in-memory dict
    ({"type":, "points": [...]}) rather than an annotations.py mark record,
    since nothing here is ever built via annotations.build_*_mark."""
    t = mark.get("type")
    pts = [tuple(p) for p in mark.get("points", [])]
    if t == "distance" and len(pts) == 2:
        return [(pts[0], pts[1])]
    if t == "angle" and len(pts) == 3:
        v, a, b = pts
        return [(v, a), (v, b)]
    if t in ("polygon", "ellipse") and len(pts) >= 3:
        n = len(pts)
        return [(pts[i], pts[(i + 1) % n]) for i in range(n)]
    return []


def live_measuring_distance_px(p0, p1):
    return math.hypot(p1[0] - p0[0], p1[1] - p0[1])


def live_measuring_angle_deg(vertex, a, b):
    """Interior angle at vertex between rays to a and b, in degrees --
    dimensionless, so (unlike distance/polygon/ellipse) there is no px unit
    to attach here; degrees alone already reads as unambiguous. None if
    either arm collapses onto the vertex (undefined direction)."""
    v = np.asarray(vertex, dtype=float)
    va = np.asarray(a, dtype=float) - v
    vb = np.asarray(b, dtype=float) - v
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return None
    cos_t = np.clip(np.dot(va, vb) / (na * nb), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos_t)))


def live_measuring_polygon_stats(points):
    """Perimeter and shoelace area, plain pixels -- reimplemented here
    rather than reusing annotations.py's own polygon math, since this
    feature must import NEITHER annotations.py NOR calibrate.py at all (see
    the module-boundary check above). Used for both Polygon and Ellipse:
    Live Measuring's "ellipse" is the clicked boundary loop itself, same as
    a polygon, never a true least-squares fit -- there is no calibrated
    number at stake here to justify the fit machinery measure.fit_ellipse
    exists for."""
    n = len(points)
    if n < 3:
        return 0.0, 0.0
    perimeter = sum(
        math.hypot(points[(i + 1) % n][0] - points[i][0],
                  points[(i + 1) % n][1] - points[i][1])
        for i in range(n))
    area = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        area += x0 * y1 - x1 * y0
    return perimeter, abs(area) / 2.0


def live_measuring_result_text(mark):
    """Every result is labeled explicitly in px (or degrees, itself already
    unambiguous) -- PLAN_quick_ruler.md's own rule, so a screenshot never
    reads as a calibrated figure out of context."""
    t = mark.get("type")
    pts = mark.get("points", [])
    if t == "distance":
        return "{:.1f} px".format(live_measuring_distance_px(pts[0], pts[1]))
    if t == "angle":
        deg = live_measuring_angle_deg(pts[0], pts[1], pts[2])
        return "{:.1f}°".format(deg) if deg is not None else "undefined (degenerate arm)"
    if t in ("polygon", "ellipse"):
        perimeter, area = live_measuring_polygon_stats(pts)
        return "perimeter {:.1f} px, area {:.1f} px²".format(perimeter, area)
    return ""


def _source_without_docs_and_comments(obj):
    """A function/method/class's own source with every comment and string
    literal (docstrings included) stripped, via the tokenizer rather than a
    regex -- so a docstring/comment that merely EXPLAINS why some other
    module or function is deliberately NOT used (naming it to say so, the
    way this file's own docstrings do throughout) doesn't trip a scan meant
    to catch actual CODE reaching for that name. A real reference always
    survives this strip (it's an attribute access or a call, not a string),
    so this only removes the false positives, never a real violation."""
    import inspect
    import io
    import tokenize
    src = inspect.getsource(obj)
    kept = []
    for tok_type, tok_string, *_rest in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok_type in (tokenize.COMMENT, tokenize.STRING):
            continue
        kept.append(tok_string)
    return " ".join(kept)


def assert_live_measuring_has_no_calibration_dependency():
    """Structural self-check (PLAN_quick_ruler.md's own instruction: "assert
    this the same way Part 02 asserted the camera-import boundary"): every
    Live Measuring function/method's own SOURCE CODE (comments and
    docstrings excluded -- see _source_without_docs_and_comments) must never
    reference calibrate.py/annotations.py/provenance.py or the sensor-space
    conversion Measure/Part 05 uses. Source inspection, not just code
    review, so a future edit that quietly reaches into calibration is
    caught the moment --render-check runs, not just at review time. Called
    from render_check() itself -- an assertion nobody ever runs is not a
    guard, it's a comment that lies about being one."""
    forbidden = ("_calibrate", "_annotations", "_provenance",
                "native_point_from_preview_click", "um_per_px",
                "calibration_ref", "pixel_sha256")
    targets = [lores_point_from_preview_click, _live_measuring_tool_hint,
              _live_measuring_point_status, live_measuring_mark_segments,
              live_measuring_distance_px, live_measuring_angle_deg,
              live_measuring_polygon_stats, live_measuring_result_text]
    if _HAVE_QT:
        targets += [LiveMeasuringPanel,
                   FocusPreviewWindow._launch_live_measuring,
                   FocusPreviewWindow._live_measuring_set_tool,
                   FocusPreviewWindow._live_measuring_preview_event,
                   FocusPreviewWindow._live_measuring_add_point,
                   FocusPreviewWindow._live_measuring_finish_pending,
                   FocusPreviewWindow._live_measuring_cancel_pending,
                   FocusPreviewWindow._live_measuring_context_menu,
                   FocusPreviewWindow._live_measuring_delete_point,
                   FocusPreviewWindow._live_measuring_delete_all,
                   FocusPreviewWindow._live_measuring_hit_test,
                   FocusPreviewWindow._live_measuring_view_point,
                   FocusPreviewWindow._live_measuring_notify_changed,
                   FocusPreviewWindow._live_measuring_signature,
                   FocusPreviewWindow._live_measuring_close]
    for target in targets:
        src = _source_without_docs_and_comments(target)
        for word in forbidden:
            assert word not in src, (
                "{} references {!r} -- Live Measuring must never touch "
                "calibration/annotation/provenance machinery".format(
                    getattr(target, "__qualname__", target), word))


# ---------------------------------------------------------------------------
# Pure overlay art (Qt-free)
# ---------------------------------------------------------------------------
def _paint(ov, rs, re, cs, ce, col, alpha=255):
    h, w = ov.shape[:2]
    rs, cs = max(rs, 0), max(cs, 0)
    re, ce = min(re, h), min(ce, w)
    if re > rs and ce > cs:
        ov[rs:re, cs:ce, 0] = col[0]
        ov[rs:re, cs:ce, 1] = col[1]
        ov[rs:re, cs:ce, 2] = col[2]
        ov[rs:re, cs:ce, 3] = alpha


def _rect_outline(ov, r0, r1, c0, c1, col, t):
    _paint(ov, r0, r0 + t, c0, c1, col)            # top
    _paint(ov, r1 - t, r1, c0, c1, col)            # bottom
    _paint(ov, r0, r1, c0, c0 + t, col)            # left
    _paint(ov, r0, r1, c1 - t, c1, col)            # right


def _draw_segment_into(ov, p0, p1, col, thickness=2):
    """A straight line segment from p0 to p1, (x, y) native pixel
    coordinates -- NOT axis-aligned like _paint/_rect_outline above.
    Sampled at every integer step along its own length and stamped as a
    thickness x thickness square via _paint (which already clips safely to
    the buffer's bounds). Deliberately simple -- no true Bresenham, no
    anti-aliasing -- matching this overlay system's existing blocky
    aesthetic (the focus box/bar/ruler ticks are all axis-aligned
    rectangles already); this is the one shape family here that genuinely
    cannot be, since Live Measuring's marks are placed at arbitrary
    angles. Used by Live Measuring (PLAN_quick_ruler.md) only."""
    x0, y0 = p0
    x1, y1 = p1
    length = math.hypot(x1 - x0, y1 - y0)
    n = max(int(length), 1)
    half = thickness // 2
    for i in range(n + 1):
        t = i / n
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        _paint(ov, y - half, y - half + thickness, x - half, x - half + thickness, col)


# Live Measuring's two visual states (PLAN_quick_ruler.md): unlike Measure/
# Part 05's three-way pen (in-progress/uncommitted/committed), there is no
# "committed" state here at all -- nothing here is ever committed. Amber for
# "still clicking" reuses state_color()'s own "searching" colour (same
# semantic: work in progress); white for "finished, on the overlay until
# Deleted" is deliberately unlike EITHER of Part 05's own colours (orange/
# cyan), so a glance never confuses which of the two live tools is on screen.
LIVE_MEASURING_PENDING_COL = (245, 205, 70)
LIVE_MEASURING_FINISHED_COL = (255, 255, 255)


def _draw_live_measuring_into(ov, marks, pending_points, thickness=2):
    """Draws every finished Live Measuring mark (white) plus the in-progress
    shape's own polyline (amber, consecutive pending points joined pairwise
    -- same simplification Part 05's own _LiveMeasureCanvas._draw_pending_
    point already makes: correct for distance/polygon/ellipse, a harmless
    cosmetic approximation for angle's own middle segment until the shape
    finishes and switches to the type-correct segment rendering). Composites
    into an EXISTING buffer without clearing it first, same convention
    _draw_ruler_ticks_into already follows, so this layers on top of
    whatever the focus box/bar/ruler already drew."""
    for mark in marks:
        for a, b in live_measuring_mark_segments(mark):
            _draw_segment_into(ov, a, b, LIVE_MEASURING_FINISHED_COL, thickness)
    for i in range(len(pending_points) - 1):
        _draw_segment_into(ov, pending_points[i], pending_points[i + 1],
                          LIVE_MEASURING_PENDING_COL, thickness)
    return ov


def _draw_bar(ov, r0, r1, c_edge, fill, col, width=10):
    h, w = ov.shape[:2]
    bc1 = min(max(c_edge, 1), w)
    bc0 = max(bc1 - width, 0)
    _paint(ov, r0, r1, bc0, bc1, (40, 40, 40), alpha=110)          # faint track
    filled = int(round(min(max(fill, 0.0), 1.0) * (r1 - r0)))
    _paint(ov, max(r1 - filled, r0), r1, bc0, bc1, col)            # fill from bottom


def state_color(state):
    """The overlay colour for a state: red when the box is too small to score,
    green when re-pinned at the peak, amber while searching."""
    if not state.valid:
        return (200, 70, 70)
    if state.bar is not None and state.bar.at_peak and state.bar.settled:
        return (70, 220, 100)
    return (245, 205, 70)


# ---------------------------------------------------------------------------
# XY ruler: plain tick marks (no text), an aiming aid like the focus box, not
# a measurement -- it reads calibrate.py's stored um_per_px for whichever
# objective is selected, never the raw camera feed, so it is exactly as
# trustworthy (and no more) as that calibration.
# ---------------------------------------------------------------------------
_NICE_TICK_STEPS_UM = (1, 2, 5, 10, 20, 50, 100, 200, 500,
                       1000, 2000, 5000, 10000, 20000, 50000)


def nice_tick_step_um(fov_um, target_ticks=10):
    """A 'round' micron tick spacing (1/2/5 x10^n) giving roughly
    target_ticks minor ticks across a field fov_um wide -- the same kind of
    axis autoscaler a plotting library uses, so the ruler never lands on an
    oddball spacing like 37.4um nobody could read off a live frame at a
    glance. None for a degenerate (non-positive) field of view."""
    if fov_um is None or fov_um <= 0 or target_ticks <= 0:
        return None
    raw = fov_um / target_ticks
    for step in _NICE_TICK_STEPS_UM:
        if step >= raw:
            return step
    return _NICE_TICK_STEPS_UM[-1]


def ruler_ticks(fov_width_um, fov_height_um, target_ticks=10, major_every=5):
    """Fractional [0, 1) tick positions along X and Y at one SHARED 'round'
    micron step (picked from the width, then applied to both axes, so the
    two rulers read at the same scale rather than each auto-picking its own
    and disagreeing), each tagged major/minor. Every `major_every`th tick is
    flagged major (drawn longer), like a physical ruler's cm/inch marks, so
    structure reads at a glance with no text at all. Returns
    (x_ticks, y_ticks), each a tuple of (frac, is_major); empty on an axis
    whose field of view is degenerate."""
    step = nice_tick_step_um(fov_width_um, target_ticks)
    if step is None:
        return (), ()

    def _ticks_for(fov_um):
        if fov_um is None or fov_um <= 0:
            return ()
        n = int(fov_um // step)
        out = []
        for i in range(1, n + 1):
            frac = (i * step) / fov_um
            if frac >= 1.0:
                break
            out.append((frac, i % major_every == 0))
        return tuple(out)

    return _ticks_for(fov_width_um), _ticks_for(fov_height_um)


def _draw_ruler_ticks_into(ov, x_ticks, y_ticks, col=(230, 230, 230),
                           minor_len_frac=0.02, major_len_frac=0.05, thickness=2):
    """X ticks hang down from the top edge, Y ticks extend right from the
    left edge, into an EXISTING buffer without clearing it first, so this
    composites alongside whatever else (the focus box) is already drawn."""
    h, w = ov.shape[:2]
    for frac, major in x_ticks:
        c = int(round(frac * w))
        tick_len = int(round((major_len_frac if major else minor_len_frac) * h))
        _paint(ov, 0, tick_len, c, c + thickness, col)
    for frac, major in y_ticks:
        r = int(round(frac * h))
        tick_len = int(round((major_len_frac if major else minor_len_frac) * w))
        _paint(ov, r, r + thickness, 0, tick_len, col)
    return ov


# ============================================================================
# CALIBRATION INTEGRATION (separable): calibrate.py's own GUI, opened from a
# menu action here, plus a one-time onboarding nudge (build checklist
# section 4). Everything under this banner and the two other banners marked
# "CALIBRATION INTEGRATION" below is additive and self-contained; nothing
# outside these blocks reaches into calibrate.py or depends on this existing.
# To pull it back out entirely: delete this function and its render_check
# block, the "Calibrate" menu block in __init__, the _launch_calibrate and
# _maybe_show_onboarding_gate methods, and the one singleShot() call that
# triggers the gate. Also delete _onboarding_session_is_interactive
# (introduced solely to keep the gate from hanging a non-interactive
# launch), its render_check coverage, the --no-onboarding argparse entry
# and its mention in the module docstring's usage block, and the
# no_onboarding constructor parameter/self._no_onboarding attribute on
# FocusPreviewWindow. calibrate.py itself needs no changes either way; it
# already runs standalone, unmodified, exactly as before.
# ============================================================================

def should_show_onboarding_gate(already_shown, any_calibration_exists, interactive=True):
    """The onboarding gate's decision (checklist section 4), pure and
    testable apart from any Qt or filesystem state: show the "calibrate now
    or skip" prompt at most ONCE EVER. already_shown gates it out regardless
    of calibration state afterward -- skip is a real, respected choice, not
    a "not yet" that gets asked again next launch -- and it never shows at
    all once ANYTHING has been calibrated for any objective, shown or not.
    The "Calibrate" menu action is the whenever-you're-ready path either
    way, so a one-time miss here costs nothing.

    interactive (default True, so every pre-existing call site keeps its
    old behavior): whether anything is actually able to dismiss a modal
    dialog right now. False for a headless/offscreen/CI/no-display launch,
    where the real QMessageBox this gates would otherwise block the event
    loop forever with no one able to click it -- see
    _onboarding_session_is_interactive, which computes this. Suppression
    for non-interactivity must read as "not now, nobody's here," never as
    "asked and answered": the caller (_maybe_show_onboarding_gate) only
    ever records the prompt as shown on the branch this function's own
    early-return skips, so a non-interactive launch cannot burn the user's
    real one-time prompt."""
    return (not already_shown) and (not any_calibration_exists) and interactive
# ============================================================================


def _onboarding_session_is_interactive(no_onboarding_flag=False):
    """Conservative, mostly Qt-free detector for whether this process can
    present and dismiss a real modal dialog right now (used only to decide
    whether the onboarding gate above may fire). Errs toward True: a missed
    one-time prompt costs nothing (the Calibrate menu action always covers
    "whenever"), while wrongly suppressing a real one means a user silently
    never learns they need to calibrate. Only returns False for the
    specific conditions this project has confirmed are non-interactive --
    an unusual platform plugin or an SSH session with real display
    forwarding is left alone, not guessed at.

    All three checks the plan calls for, in one place:
    - no_onboarding_flag: the explicit --no-onboarding opt-out (main()'s own
      argparse), for a scripted launch that has a real display but should
      not be interrupted.
    - QT_QPA_PLATFORM is offscreen/minimal -- these platforms cannot render
      or receive input at all, so a modal dialog can structurally never be
      dismissed. Read live via os.environ (never cached: --render-check and
      a real launch can differ within the same process), and compared on
      the platform name alone -- QT_QPA_PLATFORM may carry backend options
      after a colon (e.g. "offscreen:some=option").
    - no live QApplication instance: defensive only (QMessageBox.question
      itself requires one to exist), and Qt-free when PyQt5 isn't even
      importable here."""
    if no_onboarding_flag:
        return False
    platform_name = os.environ.get("QT_QPA_PLATFORM", "").split(":", 1)[0].strip().lower()
    if platform_name in ("offscreen", "minimal"):
        return False
    if _HAVE_QT and QApplication.instance() is None:
        return False
    return True


def render_overlay_into(ov, box, state, line=3, ruler_ticks=None,
                        live_measuring_marks=None, live_measuring_pending=None):
    """Draw the overlay into an existing (H, W, 4) buffer, clearing it first. The
    GUI reuses one buffer per tick instead of allocating ~1.2 MB every frame.
    ruler_ticks (x_ticks, y_ticks), if given, draws first so the box+bar (the
    thing actively being dragged) stays visually on top. Live Measuring's
    marks/pending shape (PLAN_quick_ruler.md), if given, draw LAST -- on top
    of everything else, since they're the thing a user is actively placing."""
    ov[:] = 0
    if ruler_ticks is not None:
        _draw_ruler_ticks_into(ov, *ruler_ticks)
    h, w = ov.shape[:2]
    r0, r1, c0, c1 = box.pixel_rect((h, w))
    col = state_color(state)
    _rect_outline(ov, r0, r1, c0, c1, col, line)
    if state.bar is not None:
        _draw_bar(ov, r0, r1, c1, state.bar.fill, col)
    if live_measuring_marks or live_measuring_pending:
        _draw_live_measuring_into(ov, live_measuring_marks or [],
                                  live_measuring_pending or [])
    return ov


def render_overlay(size, box, state, line=3, ruler_ticks=None):
    """RGBA overlay (H, W, 4 uint8): the focus box outline plus a session-relative
    bar filled from the bottom, colour-coded by state, plus an optional ruler.
    Pure; the GUI hands the result to set_overlay. `size` is (width, height)."""
    w, h = size
    return render_overlay_into(np.zeros((h, w, 4), dtype=np.uint8), box, state,
                               line, ruler_ticks=ruler_ticks)


def overlay_signature(box, state, overlay_shape, ruler_key=None, live_measuring_key=None):
    """A cheap fingerprint of what the overlay would draw: the box pixel rect, the
    colour, the bar fill in whole pixels, the ruler's config, and (Live
    Measuring, PLAN_quick_ruler.md) its own marks/pending-shape key. When it
    is unchanged, the overlay is identical and the GPU upload can be
    skipped."""
    h, w = overlay_shape[:2]
    r0, r1, c0, c1 = box.pixel_rect((h, w))
    filled = -1
    if state.bar is not None:
        filled = int(round(min(max(state.bar.fill, 0.0), 1.0) * (r1 - r0)))
    return (r0, r1, c0, c1, state_color(state), filled, state.valid, ruler_key,
            live_measuring_key)


# ---------------------------------------------------------------------------
# Exposure slider maths (Qt-free): a discrete standard-photographic shutter stop
# table (exact powers of two of a second) instead of a smooth log scale, so every
# slider position is one exact, nameable value; linear gain for AnalogueGain and
# the two ColourGains.
# ---------------------------------------------------------------------------
def build_shutter_stops(lo_us, hi_us, tol=0.03):
    """Standard photographic full stops (exact powers of two of a second, e.g.
    1/500, 1/1000, 1/2000, and above 1s: 1, 2, 4 ...) that fall within the given
    range, so the shutter slider moves in named, discrete steps instead of a
    smooth log scale. Walks BOTH directions from 1s (down for sub-second stops,
    up for multi-second ones), so a long-exposure ceiling gets 1s, 2s, 3s
    properly instead of jumping straight from 1s to a single top anchor. The
    sensor's true lo/hi are included as anchors unless within `tol` of a computed
    stop (avoids a near-duplicate step bunched at one end)."""
    lo_us = max(float(lo_us), 1.0)     # a reported 0 min must not reach math.log
    hi_us = float(hi_us)
    stops = []
    # walk down from 1s (1_000_000 us) while still >= lo, keeping only stops that
    # also fall at or below hi (the walk must still continue past an over-hi value
    # to reach the in-range stops below it, so the bound check is inside the loop).
    us = 1_000_000.0
    down = []
    while us >= lo_us:
        if us <= hi_us:
            down.append(us)
        us /= 2.0
    down.reverse()
    # walk up from 1s while still <= hi (whole-second doubling: 1, 2, 3...
    # note: "3s" specifically is an explicit anchor added below, since doubling
    # from 1s gives 1, 2, 4, 8 ... and the agreed long-exposure ceiling is 3.0s,
    # not a power of two.
    up = []
    n = 2.0
    us = 1_000_000.0 * n
    while us <= hi_us:
        up.append(us)
        n += 1.0
        us = 1_000_000.0 * n
    stops = down + up
    if not stops:
        stops = [lo_us]
    # anchor the true endpoints unless within tol of an existing stop
    if abs(stops[0] - lo_us) / max(lo_us, 1.0) > tol:
        stops.insert(0, lo_us)
    if abs(stops[-1] - hi_us) / max(hi_us, 1.0) > tol:
        stops.append(hi_us)
    return sorted(set(stops))


def shutter_stop_pos(us, stops):
    """The slider position (index) of the stop nearest `us`."""
    arr = np.asarray(stops, dtype=np.float64)
    return int(np.argmin(np.abs(arr - float(us))))


def pos_to_shutter_stop(pos, stops):
    """The exact shutter value (us) named by slider position `pos`."""
    pos = min(max(int(pos), 0), len(stops) - 1)
    return float(stops[pos])


def fmt_shutter_fraction(us):
    """Fraction-of-a-second display for a shutter value in microseconds: below
    1s, "1/Ns" (N rounded to the nearest whole reciprocal); at or above 1s,
    seconds with one decimal. No space before the unit (kept tight on purpose,
    a lone digit-vs-unit gap read worse on a small panel than the pure number).
    Used everywhere shutter appears (slider label, lock status, profile-load
    message), so the displayed number always matches what is actually sent to
    the sensor (no rounded photography-dial numbers)."""
    s = us / 1_000_000.0
    if s >= 1.0:
        return "{:.1f}s".format(s)
    return "1/{}s".format(int(round(1.0 / s))) if s > 0 else "0s"


def linear_to_pos(value, lo, hi, steps=GAIN_STEPS):
    frac = (value - lo) / (hi - lo) if hi > lo else 0.0
    return int(round(min(max(frac, 0.0), 1.0) * steps))


def pos_to_linear(pos, lo, hi, steps=GAIN_STEPS):
    frac = min(max(pos, 0), steps) / float(steps)
    return lo + frac * (hi - lo)


def fmt_shutter_ms(us):
    """Millisecond display (three decimals, exact to the microsecond). Used in
    older status lines that predate fmt_shutter_fraction; kept for anything
    still calling it directly."""
    return "{:.3f} ms".format(us / 1000.0)


# ---------------------------------------------------------------------------
# gui_prefs.json persistence (Qt-free): atomic write, tolerant read.
# ---------------------------------------------------------------------------
def load_prefs():
    try:
        return json.loads(PREFS_PATH.read_text())
    except Exception:
        return {}


def load_pref(key, default=None):
    return load_prefs().get(key, default)


def save_pref(key, value):
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        prefs = load_prefs()
        prefs[key] = value
        tmp = PREFS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, indent=2))
        os.replace(tmp, PREFS_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Processing wizard support (Qt-free): lets the wizard dialog browse ANY
# session under OUT_ROOT, not just the current one, and preview what running
# hdr_from_session.py against a given capture would actually find on disk
# right now, before committing to it. Built specifically because the
# automatic "process now?" offer fires right after science/HDR, often before
# the standalone dark burst exists yet (dark is shot last on purpose, once
# the illuminator is already off from HDR); processing then runs without dark
# correction even though dark shows up moments later. Mirrors
# hdr_from_session.py's own frames_for()/pick_capture()/process() logic
# exactly (same prefix conventions, same "last flat wins" rule), so what this
# reports matches what actually running it would do.
# ---------------------------------------------------------------------------
PROCESSABLE_KINDS = {"hdr", "science", "snap"}


def _provenance_dir_for(capture_dir):
    """The provenance directory mirroring capture_dir's own position under
    the GLOBAL provenance.OUT_ROOT -- session.json and every .meta.json
    sidecar live there now, never beside the raw frames (Part 03, provenance
    relocation). Sessions and their provenance directories are always minted
    in lockstep with matching relative paths under their respective global
    roots (see provenance.py's new_session_dirs/new_zstack_root_dirs and
    Session's own session_dir=/provenance_dir= pairing), so this mapping is
    exact -- never a guess -- PROVIDED the relative path is always taken
    from the true global OUT_ROOT, never from some narrower capture_root a
    caller happens to be browsing (e.g. list_sessions() called against a
    single z-stack's own root folder, several levels under OUT_ROOT: the
    mirrored provenance path still needs the "focal/zstack_<ts>/" prefix
    that a root relative to just the z-stack folder would lose)."""
    rel = Path(capture_dir).relative_to(provenance.OUT_ROOT)
    return provenance.PROVENANCE_ROOT / rel


def list_sessions(capture_root):
    """Every session's CAPTURE directory (raw + processed image bytes, what
    every caller here actually wants) directly under capture_root whose
    MATCHING provenance directory has a session.json, most recent first
    (session directories are timestamp-named, so name order is chronological
    order). capture_root need not be the global provenance.OUT_ROOT itself
    (the z-stack aid passes its own stack root, several levels under
    OUT_ROOT, to browse just that stack's planes) -- _provenance_dir_for
    always maps each candidate back to OUT_ROOT regardless. Returns a list
    of capture-dir Paths -- unchanged shape from before Part 03, just
    discovered via the mirrored provenance side now that session.json no
    longer lives beside the raw frames."""
    capture_root = Path(capture_root)
    if not capture_root.exists():
        return []
    found = [d for d in capture_root.iterdir()
             if d.is_dir() and (_provenance_dir_for(d) / "session.json").exists()]
    return sorted(found, key=lambda d: d.name, reverse=True)


def load_session_json(session_dir):
    try:
        prov_dir = _provenance_dir_for(session_dir)
        return json.loads((prov_dir / "session.json").read_text())
    except Exception:
        return {"captures": []}


def processable_captures(session_json):
    """Captures in a session (already-loaded session.json dict) that
    hdr_from_session.py can actually process: hdr, science, snap. Flat and
    dark are calibration-only and are never offered, matching pick_capture's
    own processable set exactly."""
    return [c for c in session_json.get("captures", []) if c.get("kind") in PROCESSABLE_KINDS]


def capture_correction_status(session_dir, session_json, cap):
    """What flat/dark correction frames actually exist on disk for `cap`
    RIGHT NOW, mirroring hdr_from_session.py's process() exactly:
      - flat: provenance.FLAT_ROOT, the one standing library shared across
        every session (Part 03, provenance relocation) -- never scanned out
        of this session's own captures list. A session's own "flat" kind
        entry, if it has one, only documents that a flat was (re)shot during
        it; it does not mean the frames still live here, since each new Flat
        capture replaces FLAT_ROOT outright.
      - dark: for an hdr capture, its own per-level dark_levels prefixes; for
        science/snap, the standalone "dark_" prefix (pairs with science).
        Either way, read from session_dir/"dark" -- session-scoped imagery
        nested under its own session, never flat alongside science/hdr
        frames (mirrors hdr_from_session.py's own frames_for/process split).
    Also detects the raw file extension in use (dng on-rig, tif off-rig)
    from whatever is actually on disk, so the wizard's eventual subprocess
    call passes the right --raw-ext without guessing. Returns a dict with
    flat_frames/dark_frames/own_frames counts and the detected ext."""
    session_dir = Path(session_dir)
    dark_dir = session_dir / "dark"

    def _frames_in(base, prefix):
        # Restricted to the actual raw extensions (not a bare "*.*" wildcard,
        # which also matches each frame's own ".meta.json" sidecar and both
        # double-counts frames and can misdetect the extension).
        if not prefix:
            return []
        matches = []
        for ext in ("dng", "tif"):
            matches += base.glob("{}frame_*.{}".format(prefix, ext))
        return sorted(matches)

    flat_frames = _frames_in(provenance.FLAT_ROOT, "flat_")

    kind = cap.get("kind")
    if kind == "hdr":
        own_prefix = cap["levels"][0]["file_prefix"] if cap.get("levels") else None
        dark_frames = []
        for lvl in cap.get("dark_levels", []):
            dark_frames += _frames_in(dark_dir, lvl.get("file_prefix"))
    else:
        own_prefix = cap.get("file_prefix")
        dark_frames = _frames_in(dark_dir, "dark_")

    own_frames = _frames_in(session_dir, own_prefix)
    ext = own_frames[0].suffix.lstrip(".") if own_frames else "dng"
    return {"flat_frames": len(flat_frames), "dark_frames": len(dark_frames),
           "own_frames": len(own_frames), "ext": ext}


def archive_session_raws(session_dir):
    """Bundle every raw frame in a session directory (flat/science/hdr/dark/
    snap, whatever is present) into one .tar file and remove the loose
    originals, mirroring hdr_from_session.py's own archive_raws() exactly:
    same filename convention ("<session>_raws.tar"), same safety order (tar
    written, then reopened and verified to contain every file, ONLY THEN are
    the loose originals removed). A standalone action rather than going
    through hdr_from_session.py itself: that script's main() always runs the
    full process() step before ever reaching archive_raws, so reusing it here
    would mean reprocessing a session just to tidy up its raws. Checks both
    known raw extensions (dng on-rig, tif off-rig) rather than requiring the
    caller to already know which one this session used.

    Note: this is a bundle, not a size reduction -- the tar is uncompressed,
    same total bytes as the loose files, just one file instead of many.

    Returns {"archived": count, "tar_path": Path or None, "mb": float}.
    Raises RuntimeError if the tar does not verify (loose files are left in
    place in that case, same as the original's failure mode).
    """
    session_dir = Path(session_dir)
    dark_dir = session_dir / "dark"   # Part 03: dark nests one level down
    raws = []
    for ext in ("dng", "tif"):
        raws += sorted(session_dir.glob("*.{}".format(ext)))
        if dark_dir.is_dir():
            raws += sorted(dark_dir.glob("*.{}".format(ext)))
    if not raws:
        return {"archived": 0, "tar_path": None, "mb": 0.0}
    tarpath = session_dir / "{}_raws.tar".format(session_dir.name)
    with tarfile.open(str(tarpath), "w") as tf:
        for r in raws:
            tf.add(str(r), arcname=r.name)
    # only remove after the tar is confirmed to have everything, same order
    # hdr_from_session.py's own archive_raws uses
    with tarfile.open(str(tarpath)) as tf:
        n = len(tf.getnames())
    if n != len(raws):
        raise RuntimeError(
            "tar verification failed ({} in tar vs {} on disk); raws left in place."
            .format(n, len(raws)))
    for r in raws:
        r.unlink()
    mb = tarpath.stat().st_size / 1e6
    return {"archived": len(raws), "tar_path": tarpath, "mb": mb}



# ---------------------------------------------------------------------------
# Qt-bound parts
# ---------------------------------------------------------------------------
if _HAVE_QT:

    class _FakePreview(QWidget):
        """A minimal stand-in preview widget for the FakeCamera off-rig: paints
        whatever focus_frame() last returned so the window is visually alive
        with no hardware and no GL preview."""

        def __init__(self, camera):
            super().__init__()
            self._cam = camera
            self._frame = None
            self.setMinimumSize(480, 360)

            # The aid only adds the overlay, so with the aid off this window
            # still shows a moving preview.
            self._refresh = QTimer(self)
            self._refresh.timeout.connect(self._paint_frame)
            self._refresh.start(100)

        def set_frame(self, data):
            self._frame = data

        def _paint_frame(self):
            self._frame = np.asarray(self._cam.focus_frame().data)
            self.update()

        def paintEvent(self, ev):
            painter = QPainter(self)
            if self._frame is not None:
                arr = np.clip(self._frame * 255, 0, 255).astype(np.uint8)
                h, w = arr.shape
                img = QImage(arr.tobytes(), w, h, w, QImage.Format_Grayscale8)
                painter.drawImage(self.rect(), img)
            painter.end()


    class BatchSelectDialog(QDialog):
        """Checkbox picker for 'run several capture kinds automatically': Flat,
        Science, HDR, Dark. Whatever is checked always runs in the FIXED capture
        order (flat, science, hdr, dark), regardless of check order -- that
        order is a real-world lighting/thermal decision (dark shot last, once
        the sensor has settled and the illuminator is already off from HDR's own
        dark phase), not a preference this dialog should let someone reorder."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Run capture sequence")
            self.flat_box = QCheckBox("Flat")
            self.science_box = QCheckBox("Science")
            self.hdr_box = QCheckBox("HDR")
            self.dark_box = QCheckBox("Dark")
            run_btn = QPushButton("Run")
            cancel_btn = QPushButton("Cancel")
            run_btn.clicked.connect(self.accept)
            cancel_btn.clicked.connect(self.reject)

            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Select which captures to run:"))
            for box in (self.flat_box, self.science_box, self.hdr_box, self.dark_box):
                lay.addWidget(box)
            note = QLabel(
                "Runs in the fixed order flat, science, HDR, dark, skipping "
                "anything left unchecked. Each step's own setup (reshoot check, "
                "frame count) still runs, but fires immediately once set up, no "
                "separate Capture press between steps. HDR's own science-to-dark "
                "pause for the illuminator note is unchanged. Esc aborts the "
                "rest of the sequence once it is running.")
            note.setWordWrap(True)
            lay.addWidget(note)
            btn_row = QHBoxLayout()
            btn_row.addWidget(run_btn)
            btn_row.addWidget(cancel_btn)
            lay.addLayout(btn_row)

        def selected_kinds(self):
            # Fixed order on purpose; see the class docstring.
            order = []
            if self.flat_box.isChecked():
                order.append("flat")
            if self.science_box.isChecked():
                order.append("science")
            if self.hdr_box.isChecked():
                order.append("hdr")
            if self.dark_box.isChecked():
                order.append("dark")
            return order


    class ProcessSessionDialog(QDialog):
        """Processing wizard: pick ANY session under OUT_ROOT (not just the
        current one), pick any processable capture in it (hdr/science/snap;
        flat/dark are calibration-only and are not listed), see whether
        flat/dark correction frames actually exist for it right now, then
        process on demand. Independent of the automatic "process now?" offer
        at capture time, which can fire before a standalone dark burst
        exists (dark is shot last on purpose, once the sensor has settled
        and the illuminator is already off from HDR's own dark phase); this
        lets processing wait until everything the correction needs is
        actually on disk, rather than running without it.
        """

        def __init__(self, out_root, display_flags, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Process a session")
            self._out_root = Path(out_root)
            self.display_flags = list(display_flags)
            self._session_dirs = []
            self._session_dir = None
            self._session_json = None
            self._captures = []

            self.session_combo = QComboBox()
            self.capture_combo = QComboBox()
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            self.process_btn = QPushButton("Process")
            self.process_btn.setEnabled(False)
            close_btn = QPushButton("Close")

            self.session_combo.currentIndexChanged.connect(self._on_session_chosen)
            self.capture_combo.currentIndexChanged.connect(self._on_capture_chosen)
            self.process_btn.clicked.connect(self.accept)
            close_btn.clicked.connect(self.reject)

            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Session (most recent first):"))
            lay.addWidget(self.session_combo)
            lay.addWidget(QLabel("Capture:"))
            lay.addWidget(self.capture_combo)
            lay.addWidget(self.status_label)
            btn_row = QHBoxLayout()
            btn_row.addWidget(self.process_btn)
            btn_row.addWidget(close_btn)
            lay.addLayout(btn_row)
            self.resize(520, 320)

            self._populate_sessions()

        def _populate_sessions(self):
            self._session_dirs = list_sessions(self._out_root)
            if not self._session_dirs:
                self.session_combo.addItem("(no sessions found)")
                self.capture_combo.addItem("(none)")
                return
            for d in self._session_dirs:
                self.session_combo.addItem(d.name)
            self._on_session_chosen(0)

        def _on_session_chosen(self, index):
            if not self._session_dirs or not (0 <= index < len(self._session_dirs)):
                return
            self._session_dir = self._session_dirs[index]
            self._session_json = load_session_json(self._session_dir)
            self.capture_combo.clear()
            self._captures = processable_captures(self._session_json)
            if not self._captures:
                self.capture_combo.addItem("(no processable captures)")
                self.process_btn.setEnabled(False)
                self.status_label.setText("")
                return
            for c in self._captures:
                ts = (c.get("timestamp") or "")[:19].replace("T", " ")
                note = "  ({})".format(c["note"]) if c.get("note") else ""
                self.capture_combo.addItem(
                    "[{}] {}  {}{}".format(c.get("index"), c.get("kind"), ts, note))
            self._on_capture_chosen(0)

        def _on_capture_chosen(self, index):
            if not self._captures or not (0 <= index < len(self._captures)):
                self.process_btn.setEnabled(False)
                return
            cap = self._captures[index]
            status = capture_correction_status(self._session_dir, self._session_json, cap)
            self.status_label.setText(
                "Flat: {} frame(s) {}\nDark: {} frame(s) {}\nOwn frames: {} ({})".format(
                    status["flat_frames"],
                    "found" if status["flat_frames"] else "(none yet)",
                    status["dark_frames"],
                    "found" if status["dark_frames"] else "(none yet)",
                    status["own_frames"], status["ext"]))
            self.process_btn.setEnabled(status["own_frames"] > 0)

        def selected(self):
            """(session_dir, capture_index, raw_ext) for the chosen capture,
            or None if nothing valid is currently selected."""
            idx = self.capture_combo.currentIndex()
            if not self._captures or not (0 <= idx < len(self._captures)):
                return None
            cap = self._captures[idx]
            status = capture_correction_status(self._session_dir, self._session_json, cap)
            return self._session_dir, cap.get("index"), status["ext"]


    class ArchiveSessionDialog(QDialog):
        """Pick any session under OUT_ROOT and bundle its raw frames into one
        .tar file (tidiness only, does not reduce disk usage; the tar is
        uncompressed). Standalone, independent of processing:
        hdr_from_session.py's own archive_raws is only ever reachable after
        its main() runs process() again, so this lets a session that was
        already processed (or one you never plan to reprocess) get tidied up
        without rerunning the whole pipeline just to reach it.
        """

        def __init__(self, out_root, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Archive session raws")
            self._session_dirs = list_sessions(out_root)

            self.session_combo = QComboBox()
            if not self._session_dirs:
                self.session_combo.addItem("(no sessions found)")
            else:
                for d in self._session_dirs:
                    self.session_combo.addItem(d.name)

            archive_btn = QPushButton("Archive")
            archive_btn.setEnabled(bool(self._session_dirs))
            archive_btn.clicked.connect(self.accept)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.reject)

            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Session (most recent first):"))
            lay.addWidget(self.session_combo)
            note = QLabel(
                "Bundles every raw frame in the session into one .tar and "
                "removes the loose files. Tidiness only, does not reduce "
                "disk usage.")
            note.setWordWrap(True)
            lay.addWidget(note)
            btn_row = QHBoxLayout()
            btn_row.addWidget(archive_btn)
            btn_row.addWidget(close_btn)
            lay.addLayout(btn_row)
            self.resize(480, 260)

        def selected_session_dir(self):
            if not self._session_dirs:
                return None
            idx = self.session_combo.currentIndex()
            if not (0 <= idx < len(self._session_dirs)):
                return None
            return self._session_dirs[idx]


    class PreferencesDialog(QDialog):
        """Options > Preferences... (Preferences-dialog plan set, Part 01).
        One sectioned dialog, replacing the old standalone Video
        resolution/Theme submenus and the Casual Mode action.

        Capture and Video Options is populated ENTIRELY from
        camera.get_capabilities() (PLAN_02_camera_capability_query.md) --
        no hardcoded resolution/format list, no Picamera2-specific value
        anywhere in this class. A capability the driver omits from its
        returned dict produces no control at all here, never an empty or
        disabled one (absent vs. empty -- see get_capabilities()'s own
        docstring).

        Live versus next-launch (PLAN_01's own rule): Capture/Video/
        Appearance settings are camera-construction-time or startup-time
        facts, so they persist only when OK is pressed, exactly like the
        menus they replace. Advanced (retention/cache) settings touch
        nothing about the camera, so they persist immediately on change,
        independent of OK/Cancel -- Cancel closes the dialog but does not
        revert an Advanced change already written to disk.
        """

        def __init__(self, camera, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Preferences")
            caps = camera.get_capabilities()
            layout = QVBoxLayout(self)

            # --- Capture and Video Options ----------------------------------
            cap_group = QGroupBox("Capture and Video Options")
            cap_form = QFormLayout(cap_group)

            self._capture_res_combo = self._resolution_combo(
                caps.get("capture_resolutions", []), load_pref("capture_resolution", None))
            cap_form.addRow("Capture resolution (next launch):", self._capture_res_combo)

            self._capture_fmt_combo = self._choice_combo(
                caps.get("capture_formats", []), load_pref("capture_format", None))
            self._capture_fmt_combo.setToolTip(
                "Persisted, not yet applied to captures -- camera_backend.py "
                "has no format-selection hook for a still capture yet.")
            cap_form.addRow("Capture file format (next launch):", self._capture_fmt_combo)

            self._video_res_combo = self._resolution_combo(
                caps.get("video_resolutions", []), load_pref("video_resolution", None))
            # Disabled, not just disclosed (Decouple video resolution from
            # preview, HANDOFF.md): this preference used to actually change
            # recorded video size (via preview_res -> the "main" stream it
            # shares with the encoder), but that coupling caused a real
            # crash (non-4:3 preview_res broke lores' pairing with it,
            # killing focus aid) and has been removed. An enabled combo
            # that still changes, persists, and shows the user's choice
            # back to them would be a false affordance -- they'd believe
            # it worked. Stays visible and its own persisted value is kept
            # (gui_prefs.json), against a future Record-button rework that
            # gives recording its own resolution independent of preview.
            self._video_res_combo.setEnabled(False)
            self._video_res_combo.setToolTip(
                "Disabled, pending a Record-button rework -- currently has "
                "no effect on recorded video. See HANDOFF.md's \"Decouple "
                "video resolution from preview\" entry.")
            cap_form.addRow("Video resolution (next launch):", self._video_res_combo)

            self._video_fmt_combo = self._choice_combo(
                caps.get("video_formats", []), load_pref("video_format", None))
            self._video_fmt_combo.setToolTip(
                "Persisted, not yet applied to recordings -- start_recording() "
                "always uses H264Encoder today.")
            cap_form.addRow("Video file format (next launch):", self._video_fmt_combo)

            # Stream format/resolution: present only if the driver actually
            # reports them (Picamera2Camera does not yet -- no stream server
            # exists in this backend). Absent means no row at all, not an
            # empty or disabled one.
            self._stream_fmt_combo = None
            self._stream_res_combo = None
            if "stream_formats" in caps:
                self._stream_fmt_combo = self._choice_combo(
                    caps["stream_formats"], load_pref("stream_format", None))
                cap_form.addRow("Stream format (next launch):", self._stream_fmt_combo)
            if "stream_resolutions" in caps:
                self._stream_res_combo = self._resolution_combo(
                    caps["stream_resolutions"], load_pref("stream_resolution", None))
                cap_form.addRow("Stream resolution (next launch):", self._stream_res_combo)

            layout.addWidget(cap_group)

            # --- Appearance ---------------------------------------------------
            # Theme is capture-independent (not camera configuration, doesn't
            # fit Advanced) -- its own small section, same next-launch shape
            # as the section above, per PLAN_01's own judgment call.
            appearance_group = QGroupBox("Appearance")
            appearance_form = QFormLayout(appearance_group)
            self._theme_combo = QComboBox()
            current_theme = load_pref("theme", None)
            theme_choices = [("Default", None)] + [
                (name, name) for name, _qss in discover_themes()]
            for label, theme_name in theme_choices:
                self._theme_combo.addItem(label, theme_name)
            idx = self._index_for_data(self._theme_combo, current_theme)
            self._theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
            appearance_form.addRow("Theme (next launch):", self._theme_combo)
            layout.addWidget(appearance_group)

            # --- Advanced -------------------------------------------------------
            adv_group = QGroupBox("Advanced")
            adv_form = QFormLayout(adv_group)

            # Keep RAW Images: the only setting in this dialog that changes
            # what gets retained (PLAN_00/PLAN_03 semantics land later; this
            # part only builds the control and persists it). Defaults ON --
            # this project's usual provenance-by-default stance, and raws
            # are what green-plane measurement is taken from, so silently
            # discarding them by default would break that capability.
            self._keep_raw_check = QCheckBox(
                "Keep RAW Images (applies to captures from now on, not retroactively)")
            self._keep_raw_check.setChecked(bool(load_pref("keep_raw_images", True)))
            self._keep_raw_check.toggled.connect(
                lambda on: save_pref("keep_raw_images", bool(on)))
            adv_form.addRow(self._keep_raw_check)

            # Additional export formats (Part 03: lifted from casual_mode.py,
            # which this supersedes). Persist immediately, live-apply, same
            # shape as Keep RAW Images just above -- read fresh at
            # PROCESSING time by _run_process_cmd, not baked into an open
            # session. All four are now genuinely independent: debayer.py's
            # tonemap step (Preferences-dialog plan set follow-up) computes
            # its display-referred result once in memory and writes exactly
            # the formats asked for via --tonemap-tiff/--tonemap-8bit/
            # --tonemap-jpg, none reading another format's file back off
            # disk -- TIFF is no longer a locked structural byproduct (it
            # used to be, before debayer.py could skip writing it; that
            # workaround is gone). Unchecking all of TIFF/PNG/JPG is a
            # legitimate choice now, not a special case: it simply means no
            # display-referred image gets produced this run (final.tif, the
            # linear RGB measurement master, is unaffected either way -- it
            # has no checkbox, never did). DNG defaults off (a raw-domain
            # COPY, not a second copy of what Keep RAW Images already
            # governs -- see its own tooltip); "Process DNG" (merge) only
            # matters together with it.
            fmt_row = QHBoxLayout()
            self._fmt_tiff_check = QCheckBox("TIFF")
            self._fmt_tiff_check.setChecked(bool(load_pref("export_format_tiff", True)))
            self._fmt_tiff_check.toggled.connect(
                lambda on: save_pref("export_format_tiff", bool(on)))
            self._fmt_png_check = QCheckBox("PNG")
            self._fmt_png_check.setChecked(bool(load_pref("export_format_png", True)))
            self._fmt_png_check.toggled.connect(
                lambda on: save_pref("export_format_png", bool(on)))
            self._fmt_jpg_check = QCheckBox("JPG")
            self._fmt_jpg_check.setChecked(bool(load_pref("export_format_jpg", True)))
            self._fmt_jpg_check.toggled.connect(
                lambda on: save_pref("export_format_jpg", bool(on)))
            self._fmt_dng_check = QCheckBox("DNG (raw copy)")
            self._fmt_dng_check.setToolTip(
                "Copy a raw-domain deliverable alongside the processed result. "
                "Independent of Keep RAW Images, which governs the session's "
                "own working raw frames, not this extra copy.")
            self._fmt_dng_check.setChecked(bool(load_pref("export_format_dng", False)))
            self._fmt_dng_check.toggled.connect(
                lambda on: save_pref("export_format_dng", bool(on)))
            self._fmt_dng_merge_check = QCheckBox("Process DNG (merge Burst/HDR frames)")
            self._fmt_dng_merge_check.setToolTip(
                "With DNG checked, on a Burst/HDR capture: deliver the merged "
                "raw-domain master instead of the first untouched raw frame "
                "(named <prefix>raw.tif, never .dng -- a merge is a derivative).")
            self._fmt_dng_merge_check.setChecked(bool(load_pref("export_format_dng_merge", False)))
            self._fmt_dng_merge_check.setEnabled(self._fmt_dng_check.isChecked())
            self._fmt_dng_check.toggled.connect(self._fmt_dng_merge_check.setEnabled)
            self._fmt_dng_merge_check.toggled.connect(
                lambda on: save_pref("export_format_dng_merge", bool(on)))
            fmt_row.addWidget(self._fmt_tiff_check)
            fmt_row.addWidget(self._fmt_png_check)
            fmt_row.addWidget(self._fmt_jpg_check)
            fmt_row.addWidget(self._fmt_dng_check)
            adv_form.addRow("Additional export formats:", fmt_row)
            adv_form.addRow("", self._fmt_dng_merge_check)

            prov_row = QHBoxLayout()
            self._provenance_edit = QLineEdit(
                str(load_pref("provenance_folder", str(Path.home() / "provenance"))))
            self._provenance_edit.editingFinished.connect(
                lambda: save_pref("provenance_folder", self._provenance_edit.text()))
            prov_browse = QPushButton("Browse...")
            prov_browse.clicked.connect(
                lambda: self._on_browse_folder_pref(self._provenance_edit, "provenance_folder",
                                                    "Choose provenance folder"))
            prov_row.addWidget(self._provenance_edit)
            prov_row.addWidget(prov_browse)
            adv_form.addRow("Provenance folder location:", prov_row)

            # capture_folder / flat_library_folder (Part 03: provenance
            # relocation) -- same next-launch-independent, persist-immediately
            # shape as provenance_folder just above; applied to
            # provenance.OUT_ROOT/provenance.FLAT_ROOT at startup in main(),
            # not live here, since every open Session already has its roots
            # baked in (see HANDOFF.md's Part 03 folder-layout note).
            cap_row = QHBoxLayout()
            self._capture_edit = QLineEdit(
                str(load_pref("capture_folder", str(Path.home() / "captures"))))
            self._capture_edit.editingFinished.connect(
                lambda: save_pref("capture_folder", self._capture_edit.text()))
            cap_browse = QPushButton("Browse...")
            cap_browse.clicked.connect(
                lambda: self._on_browse_folder_pref(self._capture_edit, "capture_folder",
                                                    "Choose capture folder"))
            cap_row.addWidget(self._capture_edit)
            cap_row.addWidget(cap_browse)
            adv_form.addRow("Capture folder location (next launch):", cap_row)

            flat_row = QHBoxLayout()
            self._flat_library_edit = QLineEdit(
                str(load_pref("flat_library_folder", str(Path.home() / "flat"))))
            self._flat_library_edit.editingFinished.connect(
                lambda: save_pref("flat_library_folder", self._flat_library_edit.text()))
            flat_browse = QPushButton("Browse...")
            flat_browse.clicked.connect(
                lambda: self._on_browse_folder_pref(self._flat_library_edit, "flat_library_folder",
                                                    "Choose flat-field library folder"))
            flat_row.addWidget(self._flat_library_edit)
            flat_row.addWidget(flat_browse)
            adv_form.addRow("Flat-field library location (next launch):", flat_row)

            clean_now_btn = QPushButton("Clean cache now")
            clean_now_btn.clicked.connect(self._on_clean_cache_now)
            adv_form.addRow(clean_now_btn)
            self._clean_cache_status = QLabel("")
            adv_form.addRow(self._clean_cache_status)

            auto_clean_row = QHBoxLayout()
            self._auto_clean_check = QCheckBox("Automatically clean cache after")
            self._auto_clean_check.setChecked(bool(load_pref("cache_auto_clean_enabled", False)))
            self._auto_clean_check.toggled.connect(
                lambda on: save_pref("cache_auto_clean_enabled", bool(on)))
            self._auto_clean_days = QSpinBox()
            self._auto_clean_days.setRange(1, 365)
            self._auto_clean_days.setValue(int(load_pref("cache_auto_clean_days", 30)))
            self._auto_clean_days.valueChanged.connect(
                lambda v: save_pref("cache_auto_clean_days", int(v)))
            auto_clean_row.addWidget(self._auto_clean_check)
            auto_clean_row.addWidget(self._auto_clean_days)
            auto_clean_row.addWidget(QLabel("days"))
            adv_form.addRow(auto_clean_row)

            layout.addWidget(adv_group)

            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(self._on_accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        @staticmethod
        def _index_for_data(combo, value):
            # NOT combo.findData(value): PyQt5's findData does not reliably
            # match tuple item data built at runtime against an equal-but-
            # distinct tuple passed to findData (confirmed empirically --
            # itemData(i) == value is True while findData(value) still
            # returns -1 for a runtime-built tuple, though it happens to
            # work for tuple literals the interpreter constant-folds to the
            # same object). A plain Python == scan sidesteps whatever
            # QVariant-level identity check findData is actually doing.
            for i in range(combo.count()):
                if combo.itemData(i) == value:
                    return i
            return -1

        @staticmethod
        def _resolution_combo(resolutions, current):
            combo = QComboBox()
            current = tuple(current) if current is not None else None
            combo.addItem("Default (current preview)", None)
            resolutions = list(resolutions)
            if current is not None and current not in resolutions:
                # The persisted value doesn't match anything get_capabilities()
                # currently reports (e.g. set by an older build before this
                # combo validated against the driver's own list, or hardware
                # whose reported modes changed since). Show it anyway, as its
                # own entry, rather than silently falling back to "Default"
                # -- a disabled/next-launch control displaying something
                # other than what's actually stored is exactly the class of
                # defect the video-resolution decoupling fix (HANDOFF.md)
                # already had to correct once.
                resolutions = [current] + resolutions
            for w, h in resolutions:
                combo.addItem("{}x{}".format(w, h), (w, h))
            idx = PreferencesDialog._index_for_data(combo, current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            return combo

        @staticmethod
        def _choice_combo(choices, current):
            combo = QComboBox()
            for c in choices:
                combo.addItem(str(c), c)
            idx = PreferencesDialog._index_for_data(combo, current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            return combo

        def _on_browse_folder_pref(self, edit, pref_key, dialog_title):
            chosen = QFileDialog.getExistingDirectory(self, dialog_title, edit.text())
            if chosen:
                edit.setText(chosen)
                save_pref(pref_key, chosen)

        def _on_clean_cache_now(self):
            # Part 04: real green-plane cache. "Clean cache now" is always
            # older_than_days=None -- remove every unreferenced plane right
            # now, regardless of age; the day threshold only governs
            # auto-clean (see main()). Report what actually happened --
            # silent housekeeping on a directory the user can't see is
            # exactly the kind of thing that becomes untrustworthy the
            # moment it surprises anyone (PLAN_04's own words).
            if _plane_cache is None:
                self._clean_cache_status.setText(
                    "cache unavailable (plane_cache.py not importable)")
                return
            result = _plane_cache.clean_cache(older_than_days=None)
            self._clean_cache_status.setText(
                "removed {removed}, kept {retained_referenced} "
                "(referenced by a saved measurement)".format(**result))

        def _on_accept(self):
            # Capture/Video/Appearance are next-launch: persist here, on OK,
            # rather than live on every combo change. Advanced settings
            # already persisted live, above, as each control changed.
            self._save_next_launch_prefs()
            self.accept()

        def _save_next_launch_prefs(self):
            def _res(combo):
                data = combo.currentData()
                return list(data) if data is not None else None
            save_pref("capture_resolution", _res(self._capture_res_combo))
            save_pref("capture_format", self._capture_fmt_combo.currentData())
            save_pref("video_resolution", _res(self._video_res_combo))
            save_pref("video_format", self._video_fmt_combo.currentData())
            if self._stream_fmt_combo is not None:
                save_pref("stream_format", self._stream_fmt_combo.currentData())
            if self._stream_res_combo is not None:
                save_pref("stream_resolution", _res(self._stream_res_combo))
            save_pref("theme", self._theme_combo.currentData())


    class _LiveMeasureCanvas(QGraphicsView):
        """The frozen-plane canvas for the live measure panel (Preferences-
        dialog plan set, Part 05). Modeled on measure.py's own MeasureView
        (same click-count-per-shape interaction: 2 points for distance, 3
        for angle, double-click to finish a 3+-point polygon or a
        5+-point ellipse) but a SEPARATE class, not that one reused --
        measure.py stays untouched, per the plan, and this canvas needs two
        things MeasureView doesn't: per-mark QGraphicsItem tracking (so a
        specific mark can be recolored on commit or removed on delete --
        MeasureView's own draw_* methods discard their item references
        immediately) and a three-way visual state instead of MeasureView's
        two. window_ (a FocusPreviewWindow) owns all tool/mark-lifecycle
        DECISIONS (what tool is active, whether a finished shape becomes a
        real mark, commit/delete); this class owns click handling and
        drawing only, the same division of labor MeasureView/MeasureWindow
        already use."""

        # IN_PROGRESS: dashed, while a shape's points are still being
        # clicked -- same color/style MeasureView's own PENDING_PEN uses,
        # since it is semantically the same "still clicking" state.
        IN_PROGRESS_PEN = QPen(QColor(255, 210, 80), 2)
        # UNCOMMITTED: a finished shape, not yet committed -- solid, but a
        # different color from both other states, so "done but not saved"
        # reads as its own thing, not a variant of either.
        UNCOMMITTED_PEN = QPen(QColor(255, 140, 0), 2)
        # COMMITTED: identical color to measure.py's own MARK_PEN (80, 220,
        # 255) -- a mark looks the same here as it will when measure.py
        # later opens this same plane by hash. Not imported from measure.py
        # (that would reach into a module this feature is told not to
        # touch, just to read a constant); the color is duplicated here on
        # purpose, matched by eye against measure.py's own MARK_PEN.
        COMMITTED_PEN = QPen(QColor(80, 220, 255), 2)
        POINT_RADIUS = 4

        def __init__(self, window):
            self.scene_ = QGraphicsScene()
            super().__init__(self.scene_)
            self.window_ = window
            self.setRenderHint(QPainter.Antialiasing)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            # Match self.preview's own appearance (black letterbox, no
            # border, no scrollbars) so the freeze reads as the same frame
            # freezing in place, not a different, patchier widget appearing
            # underneath it (PLAN_live_measure_canvas_fit).
            self.setBackgroundBrush(QColor("black"))
            self.setFrameShape(QFrame.NoFrame)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._pixmap_item = None
            self._pending_points = []   # native green-plane (x, y) floats
            self._pending_items = []    # scene items for the in-progress shape
            # First-freeze mis-fit fix (PLAN_live_measure_canvas_fit): set_image
            # is called from _on_live_measure_freeze_done BEFORE the stack
            # layout swap makes this canvas the current widget, so on the
            # very first freeze fitInView computes against stale/no geometry
            # and lands on a much-too-small transform. resizeEvent/showEvent
            # below refit once real geometry actually arrives; _user_zoomed
            # stops that refit from fighting a manual wheelEvent zoom.
            self._user_zoomed = False

        def _fit_to_view(self):
            if self._pixmap_item is None or self._user_zoomed:
                return
            self.resetTransform()
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

        def set_image(self, pixmap):
            self.scene_.clear()
            self._pixmap_item = self.scene_.addPixmap(pixmap)
            self.scene_.setSceneRect(self._pixmap_item.boundingRect())
            self._pending_points = []
            self._pending_items = []
            self._user_zoomed = False   # a new frozen plane is a fresh view
            self._fit_to_view()

        def resizeEvent(self, ev):
            super().resizeEvent(ev)
            self._fit_to_view()

        def showEvent(self, ev):
            super().showEvent(ev)
            self._fit_to_view()

        def wheelEvent(self, ev):
            self._user_zoomed = True
            factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)

        def mousePressEvent(self, ev):
            if self._pixmap_item is None:
                super().mousePressEvent(ev)
                return
            if ev.button() == Qt.RightButton:
                self._show_context_menu(ev.pos())
                return
            if self.window_._live_measure_tool is None:
                super().mousePressEvent(ev)
                return
            self._add_point(self.mapToScene(ev.pos()))

        def mouseDoubleClickEvent(self, ev):
            min_points = {"polygon": 3, "ellipse": 5}.get(self.window_._live_measure_tool)
            if min_points is not None and len(self._pending_points) >= min_points:
                self._finish_pending()
            else:
                super().mouseDoubleClickEvent(ev)

        def keyPressEvent(self, ev):
            if ev.key() == Qt.Key_Escape:
                self._clear_pending()
                self.window_._live_measure_on_point_added([])
            else:
                super().keyPressEvent(ev)

        def add_point_programmatic(self, x, y):
            """Injects the freeze-triggering click's own (already-converted,
            native green-plane) coordinates as the shape tool's first point,
            exactly as if the user had clicked there on the frozen image --
            see native_point_from_preview_click. Called once, right after
            set_image, by the window's own freeze-completion handler."""
            self._add_point(QPointF(x, y))

        def _add_point(self, pt):
            self._pending_points.append((pt.x(), pt.y()))
            self._draw_pending_point(pt)
            self.window_._live_measure_on_point_added(list(self._pending_points))
            needed = {"distance": 2, "angle": 3}.get(self.window_._live_measure_tool)
            if needed is not None and len(self._pending_points) >= needed:
                self._finish_pending()

        def _finish_pending(self):
            points = list(self._pending_points)
            self._clear_pending()
            self.window_._live_measure_finish_points(points)

        def _draw_pending_point(self, pt):
            r = self.POINT_RADIUS
            item = self.scene_.addEllipse(pt.x() - r, pt.y() - r, 2 * r, 2 * r,
                                          self.IN_PROGRESS_PEN)
            self._pending_items.append(item)
            if len(self._pending_points) >= 2:
                a = self._pending_points[-2]
                self._pending_items.append(
                    self.scene_.addLine(a[0], a[1], pt.x(), pt.y(), self.IN_PROGRESS_PEN))

        def _clear_pending(self):
            for it in self._pending_items:
                self.scene_.removeItem(it)
            self._pending_items = []
            self._pending_points = []

        # --- finished marks: drawn with an explicit pen, items returned so
        # the caller (window_) can track/recolor/remove them individually --
        # the one real difference from measure.py's own draw_distance/
        # draw_angle/draw_polygon/draw_ellipse, which discard their items.
        def draw_mark(self, mark, pen):
            t = mark.get("type")
            if t == "distance":
                p = mark["input"]["points"]
                return [self.scene_.addLine(p[0][0], p[0][1], p[1][0], p[1][1], pen)]
            if t == "angle":
                v = mark["input"]["vertex"]
                a = mark["input"]["arm_a"]
                b = mark["input"]["arm_b"]
                return [self.scene_.addLine(v[0], v[1], a[0], a[1], pen),
                        self.scene_.addLine(v[0], v[1], b[0], b[1], pen)]
            if t == "polygon":
                pts = mark["input"]["points"]
                return [self.scene_.addPolygon(
                    QPolygonF([QPointF(x, y) for x, y in pts]), pen)]
            if t == "ellipse":
                cx, cy = mark["derived"]["center"]
                major_px, minor_px = mark["derived"]["axes_px"]
                item = self.scene_.addEllipse(-major_px, -minor_px,
                                              2 * major_px, 2 * minor_px, pen)
                item.setPos(cx, cy)
                item.setRotation(mark["derived"]["angle_deg"])
                return [item]
            return []

        # --- right-click menu: Commit > Point/All, Delete > Point/All, per
        # PLAN_05 exactly -- no extra entries. Escape (keyPressEvent above)
        # is what cancels an in-progress click sequence, so this menu never
        # needs a third meaning layered onto it.
        def _show_context_menu(self, pos):
            entry = self.window_._live_measure_hit_test((pos.x(), pos.y()), self)
            marks = self.window_._live_measure_marks
            menu = QMenu(self)
            commit_menu = menu.addMenu("Commit")
            commit_point = commit_menu.addAction("Point")
            commit_all = commit_menu.addAction("All")
            delete_menu = menu.addMenu("Delete")
            delete_point = delete_menu.addAction("Point")
            delete_all = delete_menu.addAction("All")
            # Point acts on the mark under the cursor; greyed out on empty
            # space (PLAN_05) rather than falling back to a nearest mark.
            # Also greyed for a HIT on an already-committed mark: Commit has
            # nothing left to do, and Delete structurally can't (the store
            # never deletes) -- both are about the specific mark under the
            # cursor, not a generic enable/disable on "anything exists".
            commit_point.setEnabled(entry is not None and not entry["committed"])
            delete_point.setEnabled(entry is not None and not entry["committed"])
            commit_all.setEnabled(any(not e["committed"] for e in marks))
            delete_all.setEnabled(any(not e["committed"] for e in marks))
            chosen = menu.exec_(self.mapToGlobal(pos))
            if chosen is commit_point and entry is not None:
                self.window_._live_measure_commit_entry(entry)
            elif chosen is commit_all:
                self.window_._live_measure_commit_all()
            elif chosen is delete_point and entry is not None:
                self.window_._live_measure_delete_entry(entry)
            elif chosen is delete_all:
                self.window_._live_measure_delete_all()


    class LiveMeasurePanel(QWidget):
        """The floating shape-picker for the live measure feature
        (Preferences-dialog plan set, Part 05) -- the ONE genuinely new
        user-facing capability in this plan set; everything else was
        relocation, configuration, or housekeeping.

        Qt.Tool, no FramelessWindowHint: a small utility window the window
        manager decorates with its own native title bar (dragging, a close
        button) for free -- unlike full-screen mode's own floating panel
        (Qt.Tool | Qt.FramelessWindowHint), which is composited invisibly on
        purpose. This one is meant to be dragged out of the way and closed
        by the user, so it keeps normal window chrome. Non-modal, so the
        live feed underneath stays interactive while this is open.

        Closing it (native close button, or any window-manager equivalent)
        is what exits live measure mode -- closeEvent hands off to the main
        window's own cleanup (_live_measure_close) rather than doing any of
        it here, since the main window owns the frozen plane, the marks,
        and the preview/canvas swap."""

        def __init__(self, window):
            super().__init__(window, Qt.Tool)
            self.window_ = window
            self.setWindowTitle("Live measure")

            self.distance_btn = QPushButton("Distance")
            self.angle_btn = QPushButton("Angle")
            self.polygon_btn = QPushButton("Polygon")
            self.ellipse_btn = QPushButton("Ellipse")
            for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                btn.setCheckable(True)

            self.tool_group = QButtonGroup(self)
            self.tool_group.setExclusive(True)
            for name, btn in (("distance", self.distance_btn),
                             ("angle", self.angle_btn),
                             ("polygon", self.polygon_btn),
                             ("ellipse", self.ellipse_btn)):
                self.tool_group.addButton(btn)
                btn.toggled.connect(lambda checked, n=name: self._on_tool_toggled(n, checked))

            self.status_label = QLabel(_live_measure_tool_hint(None))
            self.status_label.setWordWrap(True)

            row = QHBoxLayout()
            row.addWidget(self.distance_btn)
            row.addWidget(self.angle_btn)
            row.addWidget(self.polygon_btn)
            row.addWidget(self.ellipse_btn)

            lay = QVBoxLayout(self)
            lay.addLayout(row)
            lay.addWidget(self.status_label)

        def _on_tool_toggled(self, name, checked):
            self.window_._live_measure_set_tool(name if checked else None)

        def set_status(self, text):
            self.status_label.setText(text)

        def closeEvent(self, ev):
            self.window_._live_measure_close()
            super().closeEvent(ev)


    class LiveMeasuringPanel(QWidget):
        """The floating shape-picker for Live Measuring (PLAN_quick_ruler.md)
        -- a pixel-only overlay on the LIVE, moving feed. Distinct from
        Measure's own LiveMeasurePanel above (Part 05): no freeze, no
        calibration, no commit -- see assert_live_measuring_has_no_
        calibration_dependency's own module-boundary check. Same Qt.Tool,
        native-title-bar, non-modal window shape as LiveMeasurePanel, for the
        same reasons: a small utility window meant to be dragged aside and
        closed, while the live feed underneath stays interactive."""

        def __init__(self, window):
            super().__init__(window, Qt.Tool)
            self.window_ = window
            self.setWindowTitle("Live Measuring")

            self.distance_btn = QPushButton("Distance")
            self.angle_btn = QPushButton("Angle")
            self.polygon_btn = QPushButton("Polygon")
            self.ellipse_btn = QPushButton("Ellipse")
            for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                btn.setCheckable(True)

            self.tool_group = QButtonGroup(self)
            self.tool_group.setExclusive(True)
            for name, btn in (("distance", self.distance_btn),
                             ("angle", self.angle_btn),
                             ("polygon", self.polygon_btn),
                             ("ellipse", self.ellipse_btn)):
                self.tool_group.addButton(btn)
                btn.toggled.connect(lambda checked, n=name: self._on_tool_toggled(n, checked))

            self.status_label = QLabel(_live_measuring_tool_hint(None))
            self.status_label.setWordWrap(True)

            row = QHBoxLayout()
            row.addWidget(self.distance_btn)
            row.addWidget(self.angle_btn)
            row.addWidget(self.polygon_btn)
            row.addWidget(self.ellipse_btn)

            lay = QVBoxLayout(self)
            lay.addLayout(row)
            lay.addWidget(self.status_label)

        def _on_tool_toggled(self, name, checked):
            self.window_._live_measuring_set_tool(name if checked else None)

        def set_status(self, text):
            self.status_label.setText(text)

        def closeEvent(self, ev):
            self.window_._live_measuring_close()
            super().closeEvent(ev)


    class FocusPreviewWindow(QMainWindow):
        """The live focus-aid + capture window. Embeds either the on-rig GL
        preview (camera.widget) or the off-rig _FakePreview. A QTimer tick pulls
        the latest lores frame, runs the focus meter, and (when the aid is on)
        renders the box+bar overlay via set_overlay. Drag the box interior to
        move it, drag a corner to resize it, press R to reset the per-field
        high-water mark, F to toggle the aid. On the fake, Up/Down rack focus.

        The exposure panel (right-hand column, via QSplitter) holds Auto
        exposure / Long exposure / shutter+gain sliders, Auto white balance /
        red+blue sliders, a Reprobe button, and status lines. The Capture button
        fires a non-blocking still through capture_still_async; capture always
        enforces a locked exposure first via _enforce_exposure_lock.
        """

        capture_done_signal = pyqtSignal(object)

        # probe() blocks while AE settles, so Reprobe runs it on a worker thread and
        # this signal hops the metered lock (or an exception) back to the GUI thread.
        probe_done_signal = pyqtSignal(object)

        # A burst/HDR sequence runs on a worker thread (see _fire_armed_burst); this
        # signal hops the result dict (or an exception) back to the GUI thread, the
        # same pattern as capture_done_signal and probe_done_signal.
        burst_done_signal = pyqtSignal(object)

        # hdr_from_session.py runs as a subprocess on a worker thread (frame
        # averaging + debayering at full res is not instant); this signal hops
        # (ok, stdout, stderr) back to the GUI thread.
        process_done_signal = pyqtSignal(object)

        # archive_session_raws runs on a worker thread too (tarring real DNGs
        # is not necessarily instant); hops the result dict, or an exception,
        # back to the GUI thread.
        archive_done_signal = pyqtSignal(object)

        # --- RECORD BUTTON (separable): start_recording/stop_recording run on a
        # worker thread too, same reasoning as every signal above -- on-rig
        # report: calling stop_recording directly on the Qt thread froze the
        # whole window (Picamera2/ffmpeg finalizing the encoder/output is not
        # guaranteed instant), the same class of bug capture_still_async hit
        # before it was moved off the Qt thread. Two signals, not one: start
        # and stop have different UI consequences on completion.
        record_start_done_signal = pyqtSignal(object)
        record_stop_done_signal = pyqtSignal(object)
        # --- end record button (signals) ------------------------------------

        # Z-STACK AID (BUILD_LIST Tier 3 item 6): one plane's capture+tag runs
        # on a worker thread same as every other burst above; kept as its own
        # signal rather than reusing burst_done_signal, which is hardwired to
        # self._session/self._batch_active -- both wrong for a z-stack plane,
        # which has its own per-plane Session and no batch queue.
        zstack_plane_done_signal = pyqtSignal(object)

        # GREEN-PLANE EXTRACTION UTILITY (BUILD_LIST Tier 1 item 4): debayer.py
        # runs as a subprocess on a worker thread, same shape as
        # process_done_signal above; kept separate since _on_process_finished
        # is specific to the session-based processing flow (it offers to
        # archive the session's raws on success, which makes no sense for a
        # standalone extraction that has no session involved at all).
        green_extract_done_signal = pyqtSignal(object)

        # LIVE MEASURE PANEL (Preferences-dialog plan set, Part 05): the
        # freeze-triggering capture_still_async runs on a worker thread,
        # same reasoning as every signal above -- this hops a CaptureResult
        # (or an Exception) back to the GUI thread, where extraction/
        # hashing/caching and the preview<->canvas swap actually happen.
        live_measure_freeze_done_signal = pyqtSignal(object)

        # EXPORT / PUBLISH (MeasureWindow extraction, step 3): both run on a
        # worker thread, same shape as green_extract_done_signal above --
        # export is store-wide (no open image dependency), publish resolves
        # its own image via GalleryPickDialog rather than any open
        # MeasureWindow's self._plane, since neither has one once triggered
        # from this menu.
        export_results_done_signal = pyqtSignal(object)
        publish_package_done_signal = pyqtSignal(object)

        def __init__(self, camera, meter, tick_ms=33, display_flags=None, no_onboarding=False):
            super().__init__()
            self.camera = camera
            self.meter = meter
            # CALIBRATION INTEGRATION (separable, see the banner comment near
            # should_show_onboarding_gate): main()'s own --no-onboarding
            # opt-out, read at gate-check time by _maybe_show_onboarding_gate.
            self._no_onboarding = bool(no_onboarding)
            self._drag = None
            self._aspect = LORES_RES[0] / LORES_RES[1]
            self._tick_ms = tick_ms
            # Passed straight to hdr_from_session.py on a process offer, e.g.
            # ["--wl", "65520", "--lw", "2.2", ...], built by build_display_flags
            # from this file's own launch flags (see main() below). Empty by
            # default: every display stage is then skipped except the base
            # average + debayer, same as passing none of those flags to the CLI.
            self._display_flags = list(display_flags) if display_flags else []
            self._last_sig = None
            self._zero_lores_ticks = 0   # see _readout: diagnoses a stuck lores stream
            self._ov_bufs = [np.zeros((LORES_RES[1], LORES_RES[0], 4), dtype=np.uint8)
                            for _ in range(2)]
            self._ov_idx = 0
            self._aid_on = False
            self._capturing = False
            self._session = None
            self._last_process_session_dir = None   # set by _run_process_cmd; used to
                                                     # offer archiving after a successful run
            self._last_process_index = None   # set by _run_process_cmd; paired with
                                               # _last_process_session_dir to record
                                               # flat/dark correction status (Part 03)
            self._snap_counter = 0   # unique stem per snap; see _start_capture / _ensure_session
            self._last_readout = None
            # Arm-then-fire for burst kinds: a walkthrough (menu-triggered) fills
            # this in and relabels the Capture button; the NEXT press of that same
            # button fires the sequence instead of a single snap. None means the
            # button behaves as a plain single-shot Capture.
            self._armed = None
            # Checkbox-selected batch (flat/science/hdr/dark run automatically in
            # that fixed order, whichever are checked): _batch_queue holds the
            # kinds still to run, _batch_active is True for the whole run so
            # completions know to auto-advance instead of going idle or offering
            # to process mid-sequence.
            self._batch_queue = []
            self._batch_active = False
            # RECORD BUTTON (separable): set fresh per recording session by
            # _toggle_recording. See that method's docstring for why this
            # exists -- the thread that forks ffmpeg must survive until
            # stop_encoder() has run, so Stop cannot spawn its own thread; it
            # only signals this event.
            self._record_stop_event = None
            # The worker itself, held so closeEvent can join it on quit.
            self._record_thread = None

            # Z-STACK AID (BUILD_LIST Tier 3 item 6): None means no stack is
            # active and the Capture button/action behaves normally. While
            # active: {"root": Path, "stack_id": str, "next_plane": int} --
            # see _toggle_zstack/_start_zstack/_capture_zstack_plane/_end_zstack.
            self._zstack = None

            # FULL SCREEN MODE (BUILD_LIST Tier 2): the floating panel window,
            # created lazily on first entry into full screen and reused
            # (never destroyed/recreated) across every toggle after that, so
            # no control's state -- a slider position, a combo selection --
            # is ever lost by moving self._panel in and out of it. See
            # _toggle_fullscreen/_toggle_floating_panel.
            self._floating_panel = None
            # _is_fullscreen/_pre_fullscreen_geometry back this app's OWN
            # notion of full screen rather than Qt/the window manager's: real
            # showFullScreen() puts the top-level window into the compositor's
            # xdg_toplevel fullscreen state, and on this rig (labwc/wlroots,
            # with the panel's own 2x output scale) that state takes a direct
            # scanout fast path that skips the compositor's normal scale-up
            # compositing pass -- the client's own buffer, sized in
            # unscaled/logical pixels, lands on the physical (2x) panel
            # covering only a quarter of it. A borderless window manually
            # resized to the screen's geometry looks identical to the user
            # but is never flagged as fullscreen to the compositor, so it
            # stays on the same normal composited (scaled) path ordinary
            # windowed mode already uses correctly. Confirmed via a photo of
            # the actual tablet: real fullscreen left the live preview
            # pinned to a small quarter-screen rectangle regardless of the
            # xcb-vs-wayland QPA platform (an earlier, wrong theory, see
            # CHANGELOG.md).
            self._is_fullscreen = False
            self._pre_fullscreen_geometry = None
            # See FULLSCREEN_TITLE_MARKER below (and the entry/exit branches
            # of _toggle_fullscreen) for what this backs.
            self._pre_fullscreen_title = None

            # LIVE MEASURE PANEL (Preferences-dialog plan set, Part 05): see
            # _launch_live_measure/_live_measure_freeze/_on_live_measure_
            # freeze_done/_live_measure_close below for the state machine
            # these back. _live_measure_canvas/_preview_stack are built
            # once, right after self.preview exists (below); the panel
            # itself is created lazily, on first launch, like _measure_
            # window elsewhere in this file.
            self._live_measure_panel = None
            self._live_measure_active = False       # the panel is open
            self._live_measure_frozen = False        # a plane has been pulled
            self._live_measure_freezing = False       # a freeze capture is in flight
            self._live_measure_tool = None            # distance/angle/polygon/ellipse/None
            self._live_measure_plane = None
            self._live_measure_pixel_sha256 = None
            self._live_measure_pending_first_point = None
            self._live_measure_tmp_dir = None
            # {"mark":, "committed": bool, "items": [QGraphicsItem, ...],
            #  "objective": str, "um_per_px": float|None} -- um_per_px is
            #  None only for an angle mark, which needs no calibration.
            self._live_measure_marks = []
            self.live_measure_freeze_done_signal.connect(self._on_live_measure_freeze_done)

            # LIVE MEASURING (PLAN_quick_ruler.md): pixel-only overlay on the
            # LIVE, moving feed -- no freeze, so unlike Part 05 above there is
            # no async signal, no plane, no hash, no temp dir. Marks live
            # entirely in these two lists and draw straight into the SAME
            # overlay buffer _tick()/_static_overlay_buf() already manage for
            # the focus box/ruler -- no separate canvas widget, no
            # self.preview/_preview_stack swap. Mutually exclusive with
            # Measure's own live panel (Part 05): both repurpose self.preview's
            # clicks for their own tool, so opening either one closes the
            # other first -- see _launch_live_measuring/_launch_live_measure.
            self._live_measuring_panel = None
            self._live_measuring_active = False        # the panel is open
            self._live_measuring_tool = None            # distance/angle/polygon/ellipse/None
            self._live_measuring_pending_points = []    # LORES_RES-space (x, y), in-progress shape
            self._live_measuring_marks = []             # [{"type":, "points": [...]}, ...]

            self.preview = camera.widget if hasattr(camera, "widget") \
                else _FakePreview(camera)
            self._live_measure_canvas = _LiveMeasureCanvas(self)

            self.readout = QLabel("focus aid off, press F")
            self.readout.setWordWrap(True)
            self.capture_status = QLabel("")            # capture state lives here, kept
                                                          # apart from the focus readout
            self.capture_btn = QPushButton("Capture")
            self.capture_btn.clicked.connect(self._start_capture)

            # --- RECORD BUTTON (separable; see the banner comment near
            # VIDEO_OUT_ROOT below for the full removal list): documentation/
            # review video only, not the measurement path -- see
            # camera_backend.py's own "video recording" section for why this
            # is deliberately separate from every capture kind above.
            self.record_btn = QPushButton("Record")
            self.record_btn.clicked.connect(self._toggle_recording)
            # --- end record button (widget) -------------------------------

            # Z-STACK AID (BUILD_LIST Tier 3 item 6): a two-state toggle,
            # mirroring record_btn's own shape exactly -- press to start
            # (captures plane 0 immediately), press again to end. While
            # active, capture_btn/_capture_action are REPURPOSED (see
            # _start_capture) to capture each subsequent plane instead of a
            # second new button.
            self.zstack_btn = QPushButton("Start Z-Stack")
            self.zstack_btn.clicked.connect(self._toggle_zstack)

            # Capture-kind picker, sitting directly beneath the Capture button
            # (see the panel layout below): choosing an entry runs that kind's
            # walkthrough immediately, then the combo resets to the placeholder
            # so it never looks like a "current mode" indicator sitting stale.
            # This replaces the per-kind menu items, which required going all
            # the way up to the menu bar and back down to the button; picker
            # and button are now next to each other.
            self.capture_kind_combo = QComboBox()
            self.capture_kind_combo.addItem("Choose capture...")
            self.capture_kind_combo.addItem("Flat...")
            self.capture_kind_combo.addItem("Science...")
            self.capture_kind_combo.addItem("HDR...")
            self.capture_kind_combo.addItem("Dark...")
            self.capture_kind_combo.addItem("Run sequence...")
            self.capture_kind_combo.currentIndexChanged.connect(self._on_capture_kind_chosen)

            # --- exposure panel: probe/lock, sliders, AE/AWB toggles ----------
            self._exp_updating = False               # guard so programmatic slider
            lim = self.camera.exposure_limits()      # moves do not echo to the camera
            # FIX (on-rig report): camera_controls' reported ExposureTime max is
            # the sensor's raw silicon capability, which on the IMX477 can be
            # hundreds of seconds, not a sane day-to-day ceiling -- it is NOT
            # gated by the currently active FrameDurationLimits the way an
            # earlier assumption here expected. Trusting it directly meant the
            # "normal" (Long unchecked) shutter table already reached into
            # multi-hundred-second territory at construction, which is exactly
            # how a slider read 925s with Long off. NORMAL_SHUTTER_MAX_US caps
            # the day-to-day ceiling explicitly; only checking Long raises it
            # (to LONG_EXPOSURE_MAX_US), same as before, just from a sane base.
            self._shutter_range = (lim["shutter_us"][0],
                                   min(lim["shutter_us"][1], NORMAL_SHUTTER_MAX_US))
            self._gain_range = lim["gain"]
            # Discrete standard-photographic stops (1/500, 1/1000, ...) instead of a
            # smooth log scale: each slider position is one exact, nameable value.
            self._shutter_stops = build_shutter_stops(*self._shutter_range)
            # A frame already committed to a multi-second ExposureTime cannot be
            # aborted mid-flight. Dragging through several stops while in long-
            # exposure mode, before any of those long frames finish, queues each
            # one up behind the last; the camera works through all of them before
            # the drag's final choice ever reaches the sensor. This is what
            # debounces the shutter while long exposure is on: only the position
            # the drag settles on gets sent, not every intermediate tick.
            self._shutter_apply_timer = QTimer(self)
            self._shutter_apply_timer.setSingleShot(True)
            self._shutter_apply_timer.timeout.connect(self._apply_pending_shutter)
            self._pending_shutter_us = None

            self.ae_box = QCheckBox("Auto")
            self.ae_box.toggled.connect(self._on_ae_toggled)
            self.long_exp_box = QCheckBox("Long")
            self.long_exp_box.toggled.connect(self._on_long_exposure_toggled)
            self.shutter_slider = QSlider(Qt.Horizontal)
            self.shutter_slider.setRange(0, len(self._shutter_stops) - 1)
            self.shutter_slider.valueChanged.connect(self._on_shutter)
            self.shutter_label = QLabel("shutter")
            self.gain_slider = QSlider(Qt.Horizontal)
            self.gain_slider.setRange(0, GAIN_STEPS)
            self.gain_slider.valueChanged.connect(self._on_gain)
            self.gain_label = QLabel("gain")

            self.awb_box = QCheckBox("Auto")
            self.awb_box.toggled.connect(self._on_awb_toggled)
            self.red_slider = QSlider(Qt.Horizontal)
            self.red_slider.setRange(0, GAIN_STEPS)
            self.red_slider.valueChanged.connect(self._on_red)
            self.red_label = QLabel("red")
            self.blue_slider = QSlider(Qt.Horizontal)
            self.blue_slider.setRange(0, GAIN_STEPS)
            self.blue_slider.valueChanged.connect(self._on_blue)
            self.blue_label = QLabel("blue")

            self.reprobe_btn = QPushButton("Reprobe")
            self.reprobe_btn.clicked.connect(self._on_reprobe)
            self.exp_status = QLabel("")

            # XY ruler: an aiming aid like the focus box, not a measurement tool --
            # it just reads calibrate.py's stored um_per_px for whichever objective
            # is picked here. Off by default; qt_shell.py has never tracked an
            # "objective" before this, so this combo is the first place it does.
            _ruler_objectives = list(getattr(_calibrate, "DEFAULT_OBJECTIVES", None)
                                     or ["4x", "10x", "40x", "100x"])
            self.ruler_check = QCheckBox("On")
            self.ruler_check.setChecked(bool(load_pref("ruler_on", False)))
            self.ruler_objective_combo = QComboBox()
            self.ruler_objective_combo.setEditable(True)
            for obj in _ruler_objectives:
                self.ruler_objective_combo.addItem(obj)
            _saved_ruler_obj = load_pref("ruler_objective",
                                         _ruler_objectives[0] if _ruler_objectives else "")
            _idx = self.ruler_objective_combo.findText(_saved_ruler_obj)
            if _idx >= 0:
                self.ruler_objective_combo.setCurrentIndex(_idx)
            else:
                self.ruler_objective_combo.setCurrentText(_saved_ruler_obj)
            self.ruler_status = QLabel("")
            self.ruler_status.setWordWrap(True)
            # Connected here, after _last_sig/_ov_bufs (set above) and _aid_on
            # (set above) all already exist, since _on_ruler_changed reaches for
            # them; setChecked() above ran before any handler existed, so no
            # spurious first signal reaches into unbuilt state.
            self.ruler_check.toggled.connect(self._on_ruler_changed)
            self.ruler_objective_combo.currentTextChanged.connect(self._on_ruler_changed)

            # Bring up locked at launch: reuse the CLI's profile.json if present,
            # else meter once. Either way the panel starts consistent with the lock.
            startup_locked = None
            if provenance.load_profile is not None:
                startup_locked = provenance.load_profile()
            if startup_locked is None:
                startup_locked = self.camera.probe()
                if provenance.save_profile is not None:
                    provenance.save_profile(startup_locked)
                self.exp_status.setText("Probed at startup:\nShutter {} - Gain {:.2f}".format(
                    fmt_shutter_fraction(startup_locked["shutter_us"]),
                    startup_locked["analogue_gain"]))
            else:
                self.exp_status.setText("Profile loaded:\nShutter {} - Gain {:.2f}".format(
                    fmt_shutter_fraction(startup_locked["shutter_us"]),
                    startup_locked["analogue_gain"]))
            self.camera.apply_exposure_lock(startup_locked)
            self._apply_panel_values(startup_locked["shutter_us"], startup_locked["analogue_gain"],
                                     startup_locked["awb_red_gain"], startup_locked["awb_blue_gain"],
                                     False, False)

            def _slider_block(name, slider, value_label):
                # Label and value share one row (value pushed to the far right
                # via stretch); the slider gets its own row directly below,
                # full width. Two lines per control, not one crowded row.
                row = QHBoxLayout()
                nl = QLabel(name)
                row.addWidget(nl)
                row.addStretch(1)
                row.addWidget(value_label)
                block = QVBoxLayout()
                block.addLayout(row)
                block.addWidget(slider)
                return block

            panel = QWidget()
            # Stored on self (not just a local) so full-screen mode's
            # floating-panel toggle (_toggle_fullscreen/_toggle_floating_
            # panel, defined later in this class) can reparent this exact
            # widget between the splitter and a floating window -- the same
            # instance either way, so no control ever loses its state.
            self._panel = panel
            # Themes (BUILD_LIST Tier 1 item 3) target this panel specifically
            # via #side_panel in their own style.qss -- see THEMES_ROOT's own
            # comment. Set once here, not per-theme: the object name is part
            # of this widget's identity, not something a theme should need to
            # know to set itself.
            panel.setObjectName("side_panel")
            # A floor, not a hard lock: the splitter below is what holds this
            # column's width steady against content changes; this minimum just
            # keeps a drag from squeezing it down to something unusable.
            panel.setMinimumWidth(250)
            col = QVBoxLayout(panel)
            col.addWidget(self.capture_status)
            capture_row = QHBoxLayout()
            capture_row.addWidget(self.capture_btn)
            capture_row.addWidget(self.record_btn)
            capture_row.addWidget(self.zstack_btn)
            col.addLayout(capture_row)
            col.addWidget(self.capture_kind_combo)
            col.addSpacing(8)
            col.addWidget(QLabel("Exposure"))
            exp_row = QHBoxLayout()
            exp_row.addWidget(self.long_exp_box)
            exp_row.addStretch(1)
            exp_row.addWidget(self.ae_box)
            col.addLayout(exp_row)
            col.addSpacing(4)
            col.addLayout(_slider_block("Shutter", self.shutter_slider, self.shutter_label))
            col.addSpacing(8)
            col.addLayout(_slider_block("Gain", self.gain_slider, self.gain_label))
            col.addSpacing(10)
            wb_row = QHBoxLayout()
            wb_row.addWidget(QLabel("White Balance"))
            wb_row.addStretch(1)
            wb_row.addWidget(self.awb_box)
            col.addLayout(wb_row)
            col.addLayout(_slider_block("Red", self.red_slider, self.red_label))
            col.addSpacing(4)
            col.addLayout(_slider_block("Blue", self.blue_slider, self.blue_label))
            col.addSpacing(8)
            col.addWidget(self.reprobe_btn)
            col.addWidget(self.exp_status)
            col.addSpacing(10)
            ruler_row = QHBoxLayout()
            ruler_row.addWidget(QLabel("Ruler"))
            ruler_row.addWidget(self.ruler_check)
            ruler_row.addStretch(1)
            ruler_row.addWidget(QLabel("Objective:"))
            ruler_row.addWidget(self.ruler_objective_combo)
            col.addLayout(ruler_row)
            col.addWidget(self.ruler_status)
            col.addStretch(1)
            # Focus readout pinned to the bottom, below the stretch. Its height still
            # varies with content, but everything above it is now fixed, so a longer
            # or shorter readout no longer nudges the Capture button around.
            col.addWidget(self.readout)

            # A splitter, not a plain layout: dragging the handle resizes the panel,
            # and it then holds that width regardless of what any label's content
            # does later (the earlier bug: a long wrapped-label string permanently
            # growing the window, since a minimum-size request is met but never
            # shrunk back). A splitter's child sizes come from the user's drag, not
            # renegotiated every time a child's size hint changes.
            # LIVE MEASURE PANEL (Part 05): self.preview no longer goes into
            # the splitter directly -- it and _live_measure_canvas share one
            # wrapper via QStackedLayout (which sizes EVERY child to the
            # container's rect, not just the current one, so self.preview's
            # own width()/height() -- what _disp_rect() below reads -- keeps
            # tracking the real displayed size regardless of which child is
            # on top). Not frozen: the wrapper shows self.preview, live as
            # always. Frozen: it shows the canvas instead. See
            # _on_live_measure_freeze_done / _live_measure_close for the swap.
            self._preview_stack = QWidget()
            self._preview_stack_layout = QStackedLayout(self._preview_stack)
            self._preview_stack_layout.addWidget(self.preview)
            self._preview_stack_layout.addWidget(self._live_measure_canvas)
            self._preview_stack_layout.setCurrentWidget(self.preview)

            splitter = QSplitter(Qt.Horizontal)
            splitter.addWidget(self._preview_stack)
            splitter.addWidget(panel)
            splitter.setStretchFactor(0, 1)   # preview absorbs window resizes
            splitter.setStretchFactor(1, 0)   # panel only changes when dragged
            # Restore the last dragged panel width if one was saved; 250 (the
            # floor set above) otherwise. Preview's initial share (800) is left
            # as a starting hint only, since stretch factor 1 means it absorbs
            # whatever the panel doesn't take anyway.
            splitter.setSizes([800, load_pref("panel_width", 250)])
            splitter.setCollapsible(0, False)  # neither pane can vanish under a drag
            splitter.setCollapsible(1, False)
            self._splitter = splitter   # closeEvent reads .sizes() from this on exit
            self.setCentralWidget(splitter)
            self.setFocusPolicy(Qt.StrongFocus)

            filemenu = self.menuBar().addMenu("File")
            self._capture_action = filemenu.addAction("Capture", self._start_capture)
            filemenu.addAction("Process session...", self._open_processing_wizard)
            filemenu.addAction("Process files...", self._open_process_wizard)
            filemenu.addAction("Archive session raws...", self._open_archive_wizard)
            filemenu.addAction("Browse captures...", self._open_gallery_browser)
            filemenu.addAction("Extract green plane...", self._open_green_extraction)
            filemenu.addAction("Export measurement results...", self._open_export_results)
            filemenu.addAction("Publish package...", self._open_publish_package)
            filemenu.addAction("Quit", self.close)
            view = self.menuBar().addMenu("View")
            view.addAction("Reset field (R)").triggered.connect(self.meter.reset_field)
            view.addAction("Full screen (F11)").triggered.connect(self._toggle_fullscreen)
            opts = self.menuBar().addMenu("Options")
            self._aid_action = opts.addAction("Focus aid (F)")
            self._aid_action.setCheckable(True)
            self._aid_action.triggered.connect(self._set_aid)
            self._startup_action = opts.addAction("Enable focus aid at startup")
            self._startup_action.setCheckable(True)
            self._startup_action.triggered.connect(
                lambda on: save_pref("focus_aid_at_startup", bool(on)))
            self._reset_on_aid_action = opts.addAction("Reset field when enabling aid")
            self._reset_on_aid_action.setCheckable(True)
            self._reset_on_aid_action.triggered.connect(
                lambda on: save_pref("reset_field_on_aid_enable", bool(on)))
            # Verified default is ON; _set_aid now actually reads this back
            # (see its FIX comment) instead of always resetting unconditionally.
            self._reset_on_aid_action.setChecked(bool(load_pref("reset_field_on_aid_enable", True)))
            self._startup_action.setChecked(bool(load_pref("focus_aid_at_startup", False)))

            # PREFERENCES DIALOG (Preferences-dialog plan set, Part 01):
            # replaces the standalone Video resolution and Theme submenus
            # and the Casual Mode action that used to live directly on this
            # menu -- one sectioned dialog instead of three separate menu
            # entries. Capture/Video Options is populated from
            # camera.get_capabilities() (PLAN_02), never a hardcoded list.
            opts.addAction("Preferences...", self._open_preferences)

            # Capture submenu: each item runs a walkthrough (reshoot guard, frame
            # count, instructional message) that ARMS the Capture button rather
            # than firing immediately. The next press of Capture (button or File
            # menu action) runs the sequence on a worker thread.
            # Per-kind actions (Flat/Science/HDR/Dark/Run sequence) live in
            # capture_kind_combo, right next to the Capture button. Snap used to
            # have its own entries in both the combo and this menu; removed from
            # both, since a plain Capture press with nothing armed already falls
            # through to a single-frame snap by default (see _start_capture).
            # Cancel armed capture is the one thing left here: a safety-valve,
            # independent of the combo, for backing out of an armed burst
            # (Escape does the same thing from the keyboard).
            # "Fire armed capture" is gone too: the Capture button already fires,
            # a duplicate menu entry for the same action was dead weight.
            capmenu = self.menuBar().addMenu("Capture")
            capmenu.addAction("Cancel armed capture", self._cancel_armed)
            self._tag_action = capmenu.addAction(
                "Tag as stack plane...", self._on_tag_stack)
            self._tag_action.setEnabled(_stacks is not None)
            if _stacks is None:
                self._tag_action.setToolTip("stacks.py not alongside this file")

            # --- CALIBRATION INTEGRATION (separable, see the banner comment
            # near should_show_onboarding_gate above): one menu, one action.
            calibmenu = self.menuBar().addMenu("Calibrate")
            self._calibrate_action = calibmenu.addAction(
                "Calibrate spatial (\u00b5m/px)...", self._launch_calibrate)
            self._calibrate_action.setEnabled(_calibrate is not None)
            if _calibrate is None:
                self._calibrate_action.setToolTip(
                    "calibrate.py not found alongside this file")
            # --- end calibration integration (menu) -----------------------------

            # --- MEASURE MENU (separable): one menu, one action, same shape
            # as Calibrate above. To remove: delete this block, _launch_measure,
            # and the _measure import near GREEN_PLANE_RES at module level.
            # measure.py itself needs no changes; it already runs standalone.
            measuremenu = self.menuBar().addMenu("Measure")
            self._measure_action = measuremenu.addAction(
                "Measure...", self._launch_measure)
            self._measure_action.setEnabled(_measure is not None)
            if _measure is None:
                self._measure_action.setToolTip(
                    "measure.py not found alongside this file")

            # LIVE MEASURE PANEL (Preferences-dialog plan set, Part 05): a
            # second action on this SAME menu, not a literal "Options >
            # Measure" -- PLAN_05's own prose predates this app growing its
            # own top-level Measure menu; this is the real, consistent
            # placement (see HANDOFF.md's Part 05 section for the full
            # reasoning), not a deviation worth relitigating.
            self._live_measure_action = measuremenu.addAction(
                "Live measure...", self._launch_live_measure)
            self._live_measure_action.setEnabled(
                _measure is not None and _annotations is not None)
            if _measure is None or _annotations is None:
                self._live_measure_action.setToolTip(
                    "measure.py and annotations.py must both be alongside this file")

            # LIVE MEASURING (PLAN_quick_ruler.md): its own distinct entry on
            # this SAME menu, not nested inside or toggled from either of the
            # two actions above -- a third, independent tool. Always enabled:
            # unlike the other two, this one has no dependency on measure.py/
            # annotations.py at all (see the module-boundary check), so there
            # is nothing here that could be missing.
            self._live_measuring_action = measuremenu.addAction(
                "Live Measuring...", self._launch_live_measuring)
            # --- end measure menu (menu) -----------------------------------------

            self.preview.setMouseTracking(True)
            self.preview.installEventFilter(self)

            self.timer = QTimer(self)                    # created idle; the toggle starts it
            self.timer.timeout.connect(self._tick)
            self.capture_done_signal.connect(self._on_capture_finished)
            self.probe_done_signal.connect(self._on_probe_finished)
            self.burst_done_signal.connect(self._on_burst_finished)
            self.process_done_signal.connect(self._on_process_finished)
            self.archive_done_signal.connect(self._on_archive_finished)
            self.record_start_done_signal.connect(self._on_record_start_finished)
            self.record_stop_done_signal.connect(self._on_record_stop_finished)
            self.zstack_plane_done_signal.connect(self._on_zstack_plane_finished)
            self.green_extract_done_signal.connect(self._on_green_extract_finished)
            self.export_results_done_signal.connect(self._on_export_results_finished)
            self.publish_package_done_signal.connect(self._on_publish_package_finished)

            self.camera.start()
            startup_on = bool(load_pref("focus_aid_at_startup", False))
            self._set_aid(startup_on)                    # off by default; on only if the pref says so

            # --- CALIBRATION INTEGRATION (separable): fires once, after this
            # window is up and the event loop has started (singleShot(0) rather
            # than calling it directly from __init__, so a modal dialog never
            # pops before the main window itself is visible).
            QTimer.singleShot(0, self._maybe_show_onboarding_gate)
            # --- end calibration integration (startup trigger) ------------------

        # --- the loop -------------------------------------------------------
        def _tick(self):
            frame = self.camera.focus_frame()
            state = self.meter.update(frame)
            ruler = self._current_ruler_ticks()
            # Upload the overlay only when what it draws has changed. Parked on a
            # plane, this skips the GPU texture upload entirely; while racking, it
            # redraws into a reused buffer and alternates buffers so the uploaded
            # one is never overwritten mid-read.
            sig = overlay_signature(self.meter.box, state, self._ov_bufs[0].shape,
                                    ruler_key=self._ruler_key(),
                                    live_measuring_key=self._live_measuring_signature())
            if sig != self._last_sig:
                buf = self._ov_bufs[self._ov_idx]
                render_overlay_into(buf, self.meter.box, state, ruler_ticks=ruler,
                                    live_measuring_marks=self._live_measuring_marks,
                                    live_measuring_pending=self._live_measuring_pending_points)
                self.camera.set_overlay(buf)
                self._ov_idx ^= 1
                self._last_sig = sig
            self._readout(state)

        def _readout(self, state):
            # Diagnostic (on-rig report: score stuck at 0.0000, fill stuck at
            # 100%): that combination is exactly what focus_frame() returning
            # its all-zero placeholder every tick produces (variance of a
            # constant array is 0; a single-value bar range fills to 100% by
            # definition). If the backend is counting zero successful lores
            # decodes after a couple of seconds, the lores stream itself is
            # not reaching _stash_lores at all, this is not the score math
            # misbehaving, so say that plainly instead of showing a number
            # that looks like a real reading.
            received = getattr(self.camera, "lores_frames_received", None)
            if received is not None:
                if received == 0:
                    self._zero_lores_ticks += 1
                else:
                    self._zero_lores_ticks = 0
                if self._zero_lores_ticks > 30:   # ~2s at the default 66ms tick
                    # camera_backend.py's _stash_lores now distinguishes WHY
                    # it's stuck at zero: lores_decode_errors climbing means
                    # the stream is configured but make_array("lores") is
                    # failing on every real frame (a real backend defect --
                    # report the actual exception text), versus both staying
                    # 0 meaning post_callback isn't reaching this backend at
                    # all (the generic message, unchanged from before).
                    errors = getattr(self.camera, "lores_decode_errors", 0)
                    if errors:
                        cfg_summary = format_lores_config_summary(
                            getattr(self.camera, "lores_config_at_failure", None))
                        txt = ("lores stream configured but failing to decode "
                              "({} time(s)): {} -- active config: {}".format(
                                  errors, getattr(self.camera, "last_lores_error", ""),
                                  cfg_summary))
                    else:
                        txt = ("no real lores frames received -- lores stream is not "
                              "reaching the camera backend, not a scoring bug")
                    if txt != self._last_readout:
                        self.readout.setText(txt)
                        self._last_readout = txt
                    return
            if not state.valid:
                txt = "box too small to score"
            else:
                b = state.bar
                txt = "score {:.4f}   fill {:.0%}".format(
                    state.smoothed, b.fill if b else 0.0)
            if txt != self._last_readout:                 # setText forces a repaint
                self.readout.setText(txt)
                self._last_readout = txt

        # --- focus aid on/off ----------------------------------------------
        def _open_preferences(self):
            # PLAN_01_preferences_dialog.md: one dialog, sectioned, replacing
            # the old standalone Video resolution/Theme/Casual Mode menu
            # entries. Modal: reads self.camera.get_capabilities() once at
            # open time, same as those old menus reading their own source
            # once at window-construction time.
            dlg = PreferencesDialog(self.camera, parent=self)
            dlg.exec_()

        # --- FULL SCREEN MODE (BUILD_LIST Tier 2) ----------------------------
        def _toggle_fullscreen(self):
            # One method for both directions (F11 in, Ctrl+Escape or the
            # View menu action either way) rather than two separate methods
            # that could quietly drift apart over time -- enter does
            # A/B/C, exit does C/B/A-but-slightly-different, six months
            # later they no longer match.
            #
            # Deliberately NOT real showFullScreen()/showNormal() -- see the
            # comment on self._is_fullscreen in __init__ for why (the direct
            # scanout / output scale bug). self._is_fullscreen is this app's
            # own notion of the state; isFullScreen() would stay False the
            # whole time now since the window is never put into the
            # compositor's actual fullscreen state.
            if self._is_fullscreen:
                self._is_fullscreen = False
                if self._pre_fullscreen_title is not None:
                    self.setWindowTitle(self._pre_fullscreen_title)
                    self._pre_fullscreen_title = None
                self.setWindowFlags(self.windowFlags() & ~Qt.FramelessWindowHint)
                if self._pre_fullscreen_geometry is not None:
                    self.setGeometry(self._pre_fullscreen_geometry)
                self.show()   # changing windowFlags unmaps the window; re-show it
                # Always restore the normal-mode layout exactly, regardless
                # of whether the floating panel happened to be shown or
                # hidden at the moment of exit.
                if self._splitter.indexOf(self._panel) == -1:
                    self._splitter.insertWidget(1, self._panel)
                if self._floating_panel is not None:
                    self._floating_panel.hide()
                self.menuBar().setVisible(True)
            else:
                self._is_fullscreen = True
                self._pre_fullscreen_geometry = self.geometry()
                self.menuBar().setVisible(False)
                if self._floating_panel is None:
                    # Qt.Tool: a borderless utility window associated with
                    # this one, no separate taskbar entry, doesn't steal
                    # keyboard focus from the preview underneath it.
                    self._floating_panel = QWidget(
                        self, Qt.Tool | Qt.FramelessWindowHint)
                    lay = QVBoxLayout(self._floating_panel)
                    lay.setContentsMargins(0, 0, 0, 0)
                # Reparent the panel into the floating window EVERY entry,
                # not just the first: exiting puts it back in the splitter
                # (see the self._is_fullscreen branch above), so a second or
                # third entry needs this to actually move it again, not
                # just on the floating window's own one-time construction.
                # addWidget() on a new layout reparents automatically --
                # QSplitter notices the child leaving and adjusts itself.
                self._floating_panel.layout().addWidget(self._panel)
                # Explicit-toggle by design (BUILD_LIST Tier 2's own picked
                # interaction model): hidden by default on entry, maximizing
                # the preview -- the whole point of going full screen --
                # rather than auto-showing and making the user dismiss it.
                self._floating_panel.hide()
                # The desktop taskbar (wf-panel-pi, a wlr-layer-shell surface
                # living in its own compositor layer ABOVE ordinary windows
                # by design) otherwise stays visible over this window's
                # bottom edge -- confirmed via a photo of the actual tablet.
                # Real showFullScreen() gets special compositor handling
                # that raises above that layer automatically, but this
                # deliberately avoids real fullscreen (see the comment on
                # self._is_fullscreen in __init__). Two direct ways to ask
                # for that same stacking were tried and reverted, both dead
                # ends on this rig's labwc: Qt.WindowStaysOnTopHint via
                # setWindowFlags crashes the preview (forces Qt to recreate
                # this window's native handle out from under self.preview's
                # already-created EGL surface -- real XCB BadDrawable/
                # BadWindow errors); a raw EWMH _NET_WM_STATE_ABOVE
                # ClientMessage was delivered fine but silently ignored by
                # labwc. What DOES work: labwc's own ToggleAlwaysOnTop
                # action, wired up via an rc.xml <windowRule> matched on
                # this distinctive title suffix (see FULLSCREEN_TITLE_MARKER
                # and ~/.config/labwc/rc.xml) -- setWindowTitle() is a plain
                # X11 property change, not a window recreation, so it
                # carries none of setWindowFlags's crash risk. Set the
                # title BEFORE the frameless setWindowFlags call below so
                # the marker is already in place when labwc sees this
                # window's next map (setWindowFlags's own show() causes
                # that map -- see the exit branch above for the symmetric
                # restore).
                self._pre_fullscreen_title = self.windowTitle()
                self.setWindowTitle(self._pre_fullscreen_title + FULLSCREEN_TITLE_MARKER)
                self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
                # The window starts real-maximized (main()'s showMaximized()),
                # and Qt.WindowMaximized can still be set here on a later
                # entry too (the user maximizing normally in windowed mode
                # before pressing F11). Clear it before setGeometry below --
                # left set, the WM can clamp the resize back to the old
                # maximized bounds instead of the full screen geometry we're
                # asking for (same symptom class as the two prior on-rig
                # stuck-at-old-size bugs, see CHANGELOG.md). Needs on-rig
                # confirmation that this also clears the underlying X11
                # _NET_WM_STATE_MAXIMIZED_VERT|HORZ property and not just
                # Qt's own state bit -- labwc has ignored WM-state requests
                # from this app before (see the _NET_WM_STATE_ABOVE dead end
                # in HANDOFF.md); if `xprop` still shows the maximized atoms
                # after this, an explicit wmctrl/xdotool unmaximize call
                # will be needed here too.
                self.setWindowState(self.windowState() & ~Qt.WindowMaximized)
                self.setGeometry(QApplication.primaryScreen().geometry())
                self.show()   # changing windowFlags unmaps the window; re-show it

        def _toggle_floating_panel(self):
            # No-op outside full screen (P does nothing in normal windowed
            # mode -- see keyPressEvent's own guard, which only calls this
            # while self._is_fullscreen).
            if self._floating_panel is None:
                return
            if self._floating_panel.isVisible():
                self._floating_panel.hide()
            else:
                self._floating_panel.adjustSize()
                # Top-right corner of the screen, matching where the panel
                # already sits relative to the preview in normal mode.
                # QApplication.primaryScreen() rather than the newer (Qt
                # 5.14+) QWidget.screen(), for broader PyQt5 compatibility.
                screen = QApplication.primaryScreen().availableGeometry()
                w = self._floating_panel.width()
                self._floating_panel.move(screen.right() - w, screen.top())
                self._floating_panel.show()
                self._floating_panel.raise_()

        def _set_aid(self, on):
            self._aid_on = bool(on)
            self._aid_action.setChecked(self._aid_on)     # keep the menu in sync
            if self._aid_on:
                # FIX: "Reset field when enabling aid" was write-only (saved,
                # never read back), so F always reset the high-water mark
                # regardless of this checkbox. Verified default is ON (a
                # fresh field each time the aid is enabled); turning it off
                # makes F a pause/resume that keeps the high-water mark for
                # one continuous sweep, per the documented on-rig design.
                if load_pref("reset_field_on_aid_enable", True):
                    self.meter.reset_field()      # the score you left belongs to an earlier field
                self._last_sig = None         # force the first tick to redraw the box
                self._zero_lores_ticks = 0    # fresh diagnostic window on each enable
                self.timer.start(self._tick_ms)
            else:
                self.timer.stop()             # idle: no decode, no score, no upload
                # The aid drives the timer, but the ruler (and Live Measuring's
                # own marks, PLAN_quick_ruler.md) are each their own toggle;
                # turning the aid off should not also erase either.
                self.camera.set_overlay(self._static_overlay_buf())
                ruler = self._current_ruler_ticks()
                self._last_sig = None
                txt = "focus aid off, press F"
                if ruler is not None:
                    txt += "  (ruler on)"
                self.readout.setText(txt)
                self._last_readout = txt

        def _toggle_aid(self):
            self._set_aid(not self._aid_on)

        def _static_overlay_buf(self):
            """Composes whatever should show on the overlay while the tick
            timer is NOT running (aid off): ruler ticks (if the ruler toggle
            is on) plus any Live Measuring marks (if that panel is open),
            into one buffer -- same double-buffer reuse _tick() itself uses.
            Returns None if there is truly nothing to show (the caller should
            clear the overlay outright), the buffer otherwise. Two call sites
            needed this identical composition before Live Measuring existed
            (_set_aid's and _on_ruler_changed's own aid-off branches, each
            doing "ruler-only, or clear"); Live Measuring made a third, hence
            one shared helper instead of a third copy."""
            ruler = self._current_ruler_ticks()
            has_live_measuring = bool(self._live_measuring_marks or
                                      self._live_measuring_pending_points)
            if ruler is None and not has_live_measuring:
                return None
            buf = self._ov_bufs[self._ov_idx]
            buf[:] = 0
            if ruler is not None:
                _draw_ruler_ticks_into(buf, *ruler)
            if has_live_measuring:
                _draw_live_measuring_into(buf, self._live_measuring_marks,
                                         self._live_measuring_pending_points)
            self._ov_idx ^= 1
            return buf

        # --- XY ruler ---------------------------------------------------------
        def _current_ruler_ticks(self):
            """(x_ticks, y_ticks) for the ruler's current objective, or None if
            the ruler is off or that objective has no calibration on record.
            Also updates ruler_status with a short, honest note in every case
            where nothing gets drawn, so a checked box with no lines on screen
            doesn't look like a silent bug."""
            if not self.ruler_check.isChecked():
                self.ruler_status.setText("")
                return None
            if _calibrate is None:
                self.ruler_status.setText("ruler: calibrate.py not found alongside this file")
                return None
            obj = self.ruler_objective_combo.currentText().strip()
            entry = _calibrate.current_calibration(obj)
            if entry is None:
                self.ruler_status.setText(
                    "ruler: no calibration on record for {}".format(obj or "(no objective set)"))
                return None
            um_per_px = entry["um_per_px"]
            fov_w_um = GREEN_PLANE_RES[0] * um_per_px
            fov_h_um = GREEN_PLANE_RES[1] * um_per_px
            self.ruler_status.setText("")
            return ruler_ticks(fov_w_um, fov_h_um)

        def _ruler_key(self):
            """A hashable fingerprint of the ruler's config, folded into
            overlay_signature so a ruler-only change (on/off, objective) forces
            a redraw even when the focus box/state have not changed at all."""
            if not self.ruler_check.isChecked():
                return None
            return (self.ruler_objective_combo.currentText().strip(),)

        def _on_ruler_changed(self, _value=None):
            save_pref("ruler_on", self.ruler_check.isChecked())
            save_pref("ruler_objective", self.ruler_objective_combo.currentText().strip())
            if self._aid_on:
                # The timer is already running and will redraw within one tick
                # (~66ms); invalidating the signature is enough, no need to
                # duplicate _tick's render call here.
                self._last_sig = None
                return
            # Aid is off, so the timer is not running: push the overlay directly
            # rather than waiting on a tick loop that isn't ticking.
            self.camera.set_overlay(self._static_overlay_buf())
            ruler = self._current_ruler_ticks()
            txt = "focus aid off, press F"
            if ruler is not None:
                txt += "  (ruler on)"
            self.readout.setText(txt)
            self._last_readout = txt

        # --- CALIBRATION INTEGRATION (separable; see the banner comment near
        # should_show_onboarding_gate at module level for the full removal list)
        def _launch_calibrate(self):
            """Opens calibrate.py's own CalibrationWindow as a SEPARATE window,
            reusing its class as-is rather than embedding any of its widgets
            here -- calibrate.py never touches the live camera (it only reads
            files already on disk), so this and the live preview coexist with
            no resource conflict. Pre-fills the objective from the ruler's own
            combo as a convenience only; calibrate.py's window works
            identically if launched with no objective at all."""
            if _calibrate is None:
                return
            existing = getattr(self, "_calibrate_window", None)
            if existing is not None and existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
            obj = self.ruler_objective_combo.currentText().strip() or None
            # Held on self, not a local: PyQt5 garbage-collects a window with no
            # surviving Python reference, closing it out from under itself the
            # moment this method returns.
            self._calibrate_window = _calibrate.CalibrationWindow(objective=obj)
            self._calibrate_window.show()

        # --- MEASURE MENU (separable; see the banner comment near the Measure
        # menu setup for the full removal list)
        def _launch_measure(self):
            """Opens measure.py's own MeasureWindow as a SEPARATE window, same
            treatment as _launch_calibrate above. measure.py only ever opens
            already-captured files from disk -- it never constructs its own
            camera -- so unlike ca_measure.py's CAWizard, there is no
            hardware-sharing risk with the live preview to account for here.
            Pre-fills the objective from the ruler's own combo as a
            convenience only; measure.py's window works identically if
            launched with no objective at all."""
            if _measure is None:
                return
            existing = getattr(self, "_measure_window", None)
            if existing is not None and existing.isVisible():
                existing.raise_()
                existing.activateWindow()
                return
            obj = self.ruler_objective_combo.currentText().strip() or None
            # Held on self, not a local: see _launch_calibrate's own note --
            # PyQt5 garbage-collects a window with no surviving reference.
            self._measure_window = _measure.MeasureWindow(objective=obj)
            self._measure_window.show()

        # --- LIVE MEASURE PANEL (Preferences-dialog plan set, Part 05) ------
        def _launch_live_measure(self):
            """Opens the floating LiveMeasurePanel -- unlike _launch_measure/
            _launch_calibrate above, this DOES touch the live camera (the
            freeze-on-first-click capture), so unlike those two there is a
            real hardware-sharing consideration: the panel and a normal
            Capture share self._capturing as their busy guard (see
            _live_measure_freeze), so the two can never collide."""
            if _measure is None or _annotations is None:
                return
            if self._live_measure_panel is not None and self._live_measure_panel.isVisible():
                self._live_measure_panel.raise_()
                self._live_measure_panel.activateWindow()
                return
            # Mutual exclusion with Live Measuring (PLAN_quick_ruler.md): both
            # repurpose self.preview's clicks for their own tool, so only one
            # may claim them at a time -- opening this one closes that one
            # first, if it's open (see _launch_live_measuring's matching guard).
            if self._live_measuring_active:
                self._live_measuring_panel.close()
            self._live_measure_active = True
            # Held on self, not a local: PyQt5 garbage-collects a Qt.Tool
            # window with no surviving Python reference, same reason
            # _measure_window/_calibrate_window are held above.
            self._live_measure_panel = LiveMeasurePanel(self)
            self._live_measure_panel.show()

        def _live_measure_set_tool(self, name):
            self._live_measure_tool = name
            self._live_measure_canvas._clear_pending()
            if self._live_measure_panel is not None:
                self._live_measure_panel.set_status(_live_measure_tool_hint(name))

        def _live_measure_on_point_added(self, points):
            if self._live_measure_panel is not None:
                self._live_measure_panel.set_status(
                    _live_measure_point_status(self._live_measure_tool, len(points)))

        def _live_measure_preview_event(self, ev):
            """Routes clicks on self.preview while the live measure panel is
            open. Consumes every event unconditionally (returns True) so
            this app's ordinary box-drag interaction (_press/_move, further
            down in eventFilter) never fires while measure mode has
            repurposed the same widget's clicks -- the two would otherwise
            fight over the same drag. Once frozen, self.preview is no
            longer the visible top of _preview_stack_layout, so it stops
            receiving mouse events at all (Qt only delivers them to the
            widget actually under the cursor); the `_live_measure_frozen`
            check below is a defensive no-op for that state, not the real
            guard."""
            if self._live_measure_frozen or self._live_measure_freezing:
                return True
            if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                if self._live_measure_tool is None:
                    # No tool armed yet -- a freeze click with nothing to do
                    # with the resulting point would either strand it (the
                    # old bug) or require a second click on the frozen plane.
                    # Consume the click, prompt for a tool, and never start
                    # the capture at all.
                    if self._live_measure_panel is not None:
                        self._live_measure_panel.set_status(_live_measure_tool_hint(None))
                    return True
                native = native_point_from_preview_click(
                    ev.x(), ev.y(), self._disp_rect(), GREEN_PLANE_RES)
                self._live_measure_freeze(native)
            return True

        def _live_measure_freeze(self, first_point):
            """The plan's own load-bearing decision: the first click on the
            feed pulls a green plane and freezes it under the overlay. Not a
            provenance event -- no Session, no record_capture, no sidecar;
            only the cached plane (plane_cache.store_plane, below) has to
            survive, per Part 04's own framing. Shares self._capturing with
            the normal capture path as its busy guard, so a freeze can never
            collide with a real session capture (or vice versa) on the one
            physical camera."""
            if self._live_measure_freezing or self._live_measure_frozen:
                return
            if self._capturing or self.camera.is_recording():
                self._set_capture_status(
                    "Live measure: camera busy, click again",
                    "a capture or recording is already in progress")
                return
            self._live_measure_freezing = True
            self._capturing = True   # reuse the same busy-guard the capture path uses
            self._live_measure_pending_first_point = first_point
            self._set_capture_status(
                "Freezing...", "Live measure: pulling a green plane to measure on")
            tmp_dir = Path(tempfile.mkdtemp(prefix="zynergy_live_measure_"))
            self._live_measure_tmp_dir = tmp_dir

            def _on_done(result):
                self.live_measure_freeze_done_signal.emit(result)

            try:
                self.camera.capture_still_async(tmp_dir, "freeze", _on_done)
            except Exception as exc:
                self._capturing = False
                self.live_measure_freeze_done_signal.emit(exc)

        def _on_live_measure_freeze_done(self, result):
            tmp_dir = self._live_measure_tmp_dir
            self._live_measure_tmp_dir = None
            self._live_measure_freezing = False
            self._capturing = False   # matches the guard set in _live_measure_freeze
            pending_pt = self._live_measure_pending_first_point
            self._live_measure_pending_first_point = None
            try:
                if isinstance(result, Exception):
                    self._set_capture_status("Live measure freeze failed", str(result))
                    return
                if _measure is None:
                    self._set_capture_status(
                        "Live measure unavailable", "measure.py not importable")
                    return
                if _calibrate is None:
                    self._set_capture_status(
                        "Live measure unavailable", "calibrate.py not importable")
                    return
                try:
                    plane = _measure.load_measurement_plane(str(result.raw))
                except Exception as exc:
                    self._set_capture_status("Live measure freeze failed", str(exc))
                    return
                pixel_sha256 = (_pixel_hash.pixel_sha256(plane)
                                if _pixel_hash is not None else None)
                if _plane_cache is not None and pixel_sha256 is not None:
                    _plane_cache.store_plane(plane, pixel_sha256=pixel_sha256)
                # _live_measure_frozen is set only after the pixmap/set_image/
                # swap below all succeed -- never before. A failure here must
                # leave the feature retryable, not stuck reporting a frozen
                # state that was never actually reached (the original bug:
                # the flag was set FIRST, so an exception in this block left
                # it True forever, and _live_measure_preview_event swallowed
                # every click after that unconditionally).
                try:
                    pixmap = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(plane))
                    self._live_measure_canvas.set_image(pixmap)
                    self._preview_stack_layout.setCurrentWidget(self._live_measure_canvas)
                except Exception as exc:
                    self._preview_stack_layout.setCurrentWidget(self.preview)
                    self._set_capture_status("Live measure freeze failed", str(exc))
                    return
                self._live_measure_plane = plane
                self._live_measure_pixel_sha256 = pixel_sha256
                self._live_measure_frozen = True
                # pending_pt is the freeze-triggering click's own coordinate --
                # it must always become point 1 of the mark, never be dropped,
                # per the required-behavior change: a user clicking a spore
                # edge expects that edge to be point 1, not a second click on
                # the frozen plane. _live_measure_preview_event now requires a
                # tool before it will start a freeze at all, so
                # _live_measure_tool is guaranteed non-None here in normal
                # use; the None check is defensive belt-and-braces only (the
                # panel closing mid-capture), not a reachable silent-drop path.
                if pending_pt is not None and self._live_measure_tool is not None:
                    self._live_measure_canvas.add_point_programmatic(
                        pending_pt[0], pending_pt[1])
                self._set_capture_status(
                    "Live measure: frozen", "click to continue measuring")
            finally:
                if tmp_dir is not None:
                    shutil.rmtree(tmp_dir, ignore_errors=True)

        def _live_measure_finish_points(self, points):
            """A shape's points are complete (canvas-driven); build the mark
            (identical calls to measure.py's own commit_mark -- see
            annotations.build_*_mark/measure.fit_ellipse) but do NOT call
            annotations.save_mark yet. Held in memory, drawn with the
            uncommitted pen, until an explicit right-click Commit."""
            if _annotations is None or _measure is None:
                return
            tool = self._live_measure_tool
            obj = self.ruler_objective_combo.currentText().strip()
            um_per_px = _measure.current_um_per_px(obj) if obj else None
            if tool != "angle" and um_per_px is None:
                QMessageBox.warning(
                    self, "No calibration",
                    "No calibration on record for {}.".format(obj or "(no objective set)"))
                return
            try:
                if tool == "distance":
                    mark = _annotations.build_distance_mark(points[0], points[1], um_per_px)
                elif tool == "angle":
                    mark = _annotations.build_angle_mark(points[0], points[1], points[2])
                elif tool == "polygon":
                    mark = _annotations.build_polygon_mark(points, um_per_px)
                elif tool == "ellipse":
                    center, axes_px, angle_deg = _measure.fit_ellipse(points)
                    mark = _annotations.build_ellipse_mark(
                        points, center, axes_px, angle_deg, um_per_px)
                else:
                    return
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot measure", str(exc))
                return
            items = self._live_measure_canvas.draw_mark(
                mark, _LiveMeasureCanvas.UNCOMMITTED_PEN)
            entry = {"mark": mark, "committed": False, "items": items,
                     "objective": obj, "um_per_px": um_per_px}
            self._live_measure_marks.append(entry)
            if self._live_measure_panel is not None:
                self._live_measure_panel.set_status(
                    "{} (uncommitted -- right-click to Commit)".format(
                        _measure.format_mark_result(mark)))

        def _live_measure_hit_test(self, view_pos, canvas):
            """The nearest finished mark within LIVE_MEASURE_HIT_RADIUS_PX of
            view_pos (a plain (x, y) tuple, VIEW-space pixels), or None. Each
            mark's own geometry (live_measure_mark_segments, pure) is
            converted to view coordinates via mapFromScene so the radius
            means the same thing regardless of the canvas's own zoom."""
            best_entry = None
            best_dist = LIVE_MEASURE_HIT_RADIUS_PX
            for entry in self._live_measure_marks:
                segs = live_measure_mark_segments(entry["mark"])
                if not segs:
                    continue
                for a, b in segs:
                    va = canvas.mapFromScene(QPointF(a[0], a[1]))
                    vb = canvas.mapFromScene(QPointF(b[0], b[1]))
                    d = dist_point_to_segment_px(
                        view_pos, (va.x(), va.y()), (vb.x(), vb.y()))
                    if d <= best_dist:
                        best_entry = entry
                        best_dist = d
            return best_entry

        def _live_measure_commit_entry(self, entry):
            if entry["committed"] or _annotations is None or _measure is None:
                return
            if self._live_measure_plane is None or self._live_measure_pixel_sha256 is None:
                return
            defaults = _measure.build_record_defaults(
                self._live_measure_plane, entry["objective"])
            _annotations.save_mark(
                self._live_measure_pixel_sha256, entry["mark"], record_defaults=defaults)
            entry["committed"] = True
            for item in entry["items"]:
                item.setPen(_LiveMeasureCanvas.COMMITTED_PEN)

        def _live_measure_commit_all(self):
            for entry in self._live_measure_marks:
                self._live_measure_commit_entry(entry)

        def _live_measure_delete_entry(self, entry):
            # Delete acts only on uncommitted marks (PLAN_05): committed
            # marks are in the append-only store, which never deletes.
            if entry["committed"]:
                return
            for item in entry["items"]:
                self._live_measure_canvas.scene_.removeItem(item)
            self._live_measure_marks.remove(entry)

        def _live_measure_delete_all(self):
            for entry in list(self._live_measure_marks):
                self._live_measure_delete_entry(entry)

        def _live_measure_close(self):
            """Closing discards: every uncommitted mark is gone (a committed
            one needs no special handling here -- it is already durably in
            annotations.json, save_mark's own atomic write already saw to
            that). Reopening the panel is a genuine blank slate, by
            construction (a fresh freeze pulls a fresh frame with a fresh
            pixel_sha256) -- not a recall feature; see PLAN_05's own
            "Recall is not a live-mode feature" section."""
            self._live_measure_canvas._clear_pending()
            self._live_measure_canvas.scene_.clear()
            self._live_measure_canvas._pixmap_item = None
            self._live_measure_marks = []
            self._preview_stack_layout.setCurrentWidget(self.preview)
            self._live_measure_frozen = False
            self._live_measure_freezing = False
            self._live_measure_plane = None
            self._live_measure_pixel_sha256 = None
            self._live_measure_pending_first_point = None
            self._live_measure_tool = None
            self._live_measure_active = False
            self._set_capture_status("", "")
        # --- end live measure panel (methods) --------------------------------

        # --- LIVE MEASURING (PLAN_quick_ruler.md) ----------------------------
        def _launch_live_measuring(self):
            """Opens the floating LiveMeasuringPanel. Unlike Measure/Part 05's
            own launcher, this never touches the camera at all -- no freeze,
            no capture, nothing async -- so there is no hardware-sharing
            guard needed here."""
            if self._live_measuring_panel is not None and self._live_measuring_panel.isVisible():
                self._live_measuring_panel.raise_()
                self._live_measuring_panel.activateWindow()
                return
            # Mutual exclusion with Measure's own live panel (Part 05): see
            # _launch_live_measure's matching guard.
            if self._live_measure_active:
                self._live_measure_panel.close()
            self._live_measuring_active = True
            # Held on self, not a local: PyQt5 garbage-collects a Qt.Tool
            # window with no surviving Python reference, same reason every
            # other floating panel in this file is held.
            self._live_measuring_panel = LiveMeasuringPanel(self)
            self._live_measuring_panel.show()

        def _live_measuring_set_tool(self, name):
            self._live_measuring_tool = name
            self._live_measuring_pending_points = []
            if self._live_measuring_panel is not None:
                self._live_measuring_panel.set_status(_live_measuring_tool_hint(name))
            self._live_measuring_notify_changed()

        def _live_measuring_preview_event(self, ev):
            """Routes clicks on self.preview while the Live Measuring panel is
            open -- same unconditional-consume shape as _live_measure_preview_
            event (Part 05), so ordinary box-drag never fires while this has
            repurposed the widget's clicks. No freeze: every click places a
            point directly against the CURRENT live frame, in LORES_RES-space
            pixel coordinates (lores_point_from_preview_click), never sensor
            space -- PLAN_quick_ruler.md's whole point."""
            if ev.type() == QEvent.MouseButtonPress:
                if ev.button() == Qt.RightButton:
                    self._live_measuring_context_menu(ev.pos())
                elif ev.button() == Qt.LeftButton and self._live_measuring_tool is not None:
                    pt = lores_point_from_preview_click(ev.x(), ev.y(), self._disp_rect())
                    self._live_measuring_add_point(pt)
                return True
            if ev.type() == QEvent.MouseButtonDblClick:
                min_points = {"polygon": 3, "ellipse": 5}.get(self._live_measuring_tool)
                if (min_points is not None
                        and len(self._live_measuring_pending_points) >= min_points):
                    self._live_measuring_finish_pending()
                return True
            return True

        def _live_measuring_add_point(self, pt):
            self._live_measuring_pending_points.append(pt)
            if self._live_measuring_panel is not None:
                self._live_measuring_panel.set_status(
                    _live_measuring_point_status(self._live_measuring_tool,
                                                 len(self._live_measuring_pending_points)))
            needed = {"distance": 2, "angle": 3}.get(self._live_measuring_tool)
            if needed is not None and len(self._live_measuring_pending_points) >= needed:
                self._live_measuring_finish_pending()
            else:
                self._live_measuring_notify_changed()

        def _live_measuring_finish_pending(self):
            """A shape's points are complete: held as a plain in-memory dict
            (no annotations.build_*_mark call, no calibration, nothing
            written anywhere -- see the module-boundary check), drawn white
            until an explicit right-click Delete or the panel closes."""
            points = self._live_measuring_pending_points
            self._live_measuring_pending_points = []
            mark = {"type": self._live_measuring_tool, "points": points}
            self._live_measuring_marks.append(mark)
            if self._live_measuring_panel is not None:
                self._live_measuring_panel.set_status(
                    "{} -- right-click to Delete".format(live_measuring_result_text(mark)))
            self._live_measuring_notify_changed()

        def _live_measuring_cancel_pending(self):
            """Escape cancels an in-progress, not-yet-finished click sequence
            -- mirrors this app's existing Escape conventions elsewhere (an
            armed burst, a batch sequence), and Part 05's own identical rule
            for its own in-progress shape."""
            self._live_measuring_pending_points = []
            if self._live_measuring_panel is not None:
                self._live_measuring_panel.set_status(
                    _live_measuring_tool_hint(self._live_measuring_tool))
            self._live_measuring_notify_changed()

        def _live_measuring_view_point(self, lores_pt):
            """LORES_RES-space (x, y) back to CURRENT on-screen preview-widget
            pixel coordinates -- the inverse of lores_point_from_preview_click,
            needed for the right-click hit test: LIVE_MEASURE_HIT_RADIUS_PX
            means view-space pixels (same reasoning as Part 05's own hit
            test), so the grab radius stays constant regardless of window
            size, not scaled by it."""
            x, y, w, h = self._disp_rect()
            fx = lores_pt[0] / LORES_RES[0]
            fy = lores_pt[1] / LORES_RES[1]
            return (x + fx * w, y + fy * h)

        def _live_measuring_hit_test(self, view_pos):
            best, best_dist = None, LIVE_MEASURE_HIT_RADIUS_PX
            for mark in self._live_measuring_marks:
                for a, b in live_measuring_mark_segments(mark):
                    va = self._live_measuring_view_point(a)
                    vb = self._live_measuring_view_point(b)
                    d = dist_point_to_segment_px(view_pos, va, vb)
                    if d <= best_dist:
                        best, best_dist = mark, d
            return best

        def _live_measuring_context_menu(self, pos):
            """Delete (Point / All) only -- no Commit submenu at all, per
            PLAN_quick_ruler.md: nothing here is ever committed, so there is
            nothing a Commit action could do."""
            entry = self._live_measuring_hit_test((pos.x(), pos.y()))
            marks = self._live_measuring_marks
            menu = QMenu(self.preview)
            delete_menu = menu.addMenu("Delete")
            delete_point = delete_menu.addAction("Point")
            delete_all = delete_menu.addAction("All")
            delete_point.setEnabled(entry is not None)
            delete_all.setEnabled(bool(marks))
            chosen = menu.exec_(self.preview.mapToGlobal(pos))
            if chosen is delete_point and entry is not None:
                self._live_measuring_delete_point(entry)
            elif chosen is delete_all:
                self._live_measuring_delete_all()

        def _live_measuring_delete_point(self, entry):
            """Split out of _live_measuring_context_menu so render_check can
            drive real deletion without going through QMenu.exec_ (a blocking
            modal call) -- same reason Part 05's own commit/delete are their
            own methods rather than living inline in ITS context-menu
            handler."""
            self._live_measuring_marks.remove(entry)
            self._live_measuring_notify_changed()

        def _live_measuring_delete_all(self):
            self._live_measuring_marks = []
            self._live_measuring_notify_changed()

        def _live_measuring_notify_changed(self):
            """Call after any Live Measuring mutation (point added, mark
            finished, mark deleted, panel opened/closed). While the aid's
            timer is running, _tick() picks up the change on its own within
            one tick (~33ms, imperceptible) via _live_measuring_signature's
            own key folded into overlay_signature -- nothing to do here.
            While it is NOT running, nothing else will ever redraw the
            overlay on its own, so this pushes the change immediately, the
            same "timer not ticking, push directly" rule _on_ruler_changed
            already follows for the ruler."""
            if self._aid_on:
                return
            self.camera.set_overlay(self._static_overlay_buf())

        def _live_measuring_signature(self):
            """Folded into overlay_signature (mirrors _ruler_key's own
            reasoning) so a Live Measuring change forces a redraw on the very
            next tick even when the focus box/bar/ruler are completely
            unchanged -- without this, _tick()'s own unchanged-signature skip
            would leave a just-added mark invisible until something else
            happened to also change that tick."""
            if not self._live_measuring_active:
                return None
            pending = tuple((round(x, 1), round(y, 1))
                            for x, y in self._live_measuring_pending_points)
            marks = tuple((m["type"], tuple((round(x, 1), round(y, 1)) for x, y in m["points"]))
                         for m in self._live_measuring_marks)
            return (self._live_measuring_tool, pending, marks)

        def _live_measuring_close(self):
            """Closing discards everything -- nothing here was ever committed
            or written anywhere, so unlike Measure/Part 05's own close (which
            only discards uncommitted marks, since a committed one is already
            durable) there is nothing to preserve. Reopening the panel is
            always a genuine blank slate."""
            self._live_measuring_marks = []
            self._live_measuring_pending_points = []
            self._live_measuring_tool = None
            self._live_measuring_active = False
            self._live_measuring_notify_changed()
        # --- end Live Measuring (methods) ------------------------------------
        # --- end measure menu (method) ---------------------------------------

        def _maybe_show_onboarding_gate(self):
            """The first-launch prompt itself (checklist section 4): ask once
            whether to calibrate now, using should_show_onboarding_gate's pure
            decision. Skipping (or closing the dialog without choosing) just
            continues into the GUI exactly as it would otherwise; the
            Calibrate menu action covers "whenever" either way.

            interactive is computed fresh on every call, never cached on
            self -- QT_QPA_PLATFORM (the dominant signal) can only be
            fixed for the life of a process anyway, but reading it live
            here (rather than once at construction) keeps this method's
            own behavior obvious from its body alone, matching
            _onboarding_session_is_interactive's own "never cached"
            contract. self._no_onboarding is the one per-launch override
            (main()'s --no-onboarding), not a live environment signal, so
            it's threaded through as an explicit argument rather than
            folded into the environment check itself."""
            if _calibrate is None:
                return
            already_shown = bool(load_pref("onboarding_calibration_prompt_shown", False))
            any_calibration_exists = bool(_calibrate.load_calibrations())
            interactive = _onboarding_session_is_interactive(self._no_onboarding)
            if not should_show_onboarding_gate(already_shown, any_calibration_exists, interactive):
                return
            save_pref("onboarding_calibration_prompt_shown", True)
            resp = QMessageBox.question(
                self, "Calibrate now?",
                "No spatial calibration is on record yet for any objective. "
                "Measurements won't convert to real units until one exists.\n\n"
                "Calibrate now, or skip? (Calibrate stays in the menu for "
                "later either way.)",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if resp == QMessageBox.Yes:
                self._launch_calibrate()
        # --- end calibration integration (methods) --------------------------

        # --- exposure panel ---------------------------------------------------
        def _apply_panel_values(self, shutter_us, gain, red, blue, ae_on, awb_on):
            # Programmatic slider moves (startup, a lock, a reprobe) must not echo
            # back into set_exposure calls; _exp_updating suppresses that.
            self._exp_updating = True
            try:
                pos = shutter_stop_pos(shutter_us, self._shutter_stops)
                self.shutter_slider.setValue(pos)
                self.shutter_label.setText(fmt_shutter_fraction(shutter_us))
                self.gain_slider.setValue(linear_to_pos(gain, *self._gain_range))
                self.gain_label.setText("{:.2f}".format(gain))
                self.red_slider.setValue(linear_to_pos(red, *AWB_GAIN_RANGE))
                self.red_label.setText("{:.2f}".format(red))
                self.blue_slider.setValue(linear_to_pos(blue, *AWB_GAIN_RANGE))
                self.blue_label.setText("{:.2f}".format(blue))
                self.ae_box.setChecked(bool(ae_on))
                self.awb_box.setChecked(bool(awb_on))
                self.shutter_slider.setEnabled(not ae_on)
                self.gain_slider.setEnabled(not ae_on)
                self.red_slider.setEnabled(not awb_on)
                self.blue_slider.setEnabled(not awb_on)
            finally:
                self._exp_updating = False

        def _on_shutter(self, pos):
            if self._exp_updating:
                return
            us = pos_to_shutter_stop(pos, self._shutter_stops)
            self.shutter_label.setText(fmt_shutter_fraction(us))   # instant, no wait
            if self.long_exp_box.isChecked():
                # See the debounce timer's construction comment: a fast drag through
                # several long-exposure stops must not queue up multiple multi-second
                # frames behind each other. Only the position the drag settles on
                # (300ms of no further movement) actually reaches the camera.
                self._pending_shutter_us = us
                self._shutter_apply_timer.start(300)
            else:
                self.camera.set_exposure(shutter_us=us)

        def _apply_pending_shutter(self):
            if self._pending_shutter_us is not None:
                self.camera.set_exposure(shutter_us=self._pending_shutter_us)
                self._pending_shutter_us = None

        def _on_gain(self, pos):
            if self._exp_updating:
                return
            gain = pos_to_linear(pos, *self._gain_range)
            self.gain_label.setText("{:.2f}".format(gain))
            self.camera.set_exposure(gain=gain)

        def _on_red(self, pos):
            if self._exp_updating:
                return
            red = pos_to_linear(pos, *AWB_GAIN_RANGE)
            self.red_label.setText("{:.2f}".format(red))
            self.camera.set_exposure(red_gain=red)

        def _on_blue(self, pos):
            if self._exp_updating:
                return
            blue = pos_to_linear(pos, *AWB_GAIN_RANGE)
            self.blue_label.setText("{:.2f}".format(blue))
            self.camera.set_exposure(blue_gain=blue)

        def _on_ae_toggled(self, on):
            if self._exp_updating:
                return
            self.camera.set_exposure(auto_exposure=bool(on))
            self.shutter_slider.setEnabled(not on)
            self.gain_slider.setEnabled(not on)
            if on:
                self.reprobe_btn.setEnabled(True)

        def _on_awb_toggled(self, on):
            if self._exp_updating:
                return
            self.camera.set_exposure(auto_white_balance=bool(on))
            self.red_slider.setEnabled(not on)
            self.blue_slider.setEnabled(not on)

        def _on_long_exposure_toggled(self, on):
            # Checking it raises the sensor's real FrameDurationLimits ceiling (the
            # part that actually matters; a display change alone would just get
            # silently clamped by libcamera), swaps the shutter table's ceiling from
            # the normal fast-range max up to LONG_EXPOSURE_MAX_US, disables Auto and
            # Reprobe (a multi-second AE loop is not meaningful), and carries the
            # current slider position across into the new table instead of resetting.
            # Unchecking snaps the value back into the fast range BEFORE shrinking the
            # ceiling back down, so ExposureTime is never left above what the sensor
            # will accept.
            current_us = pos_to_shutter_stop(self.shutter_slider.value(), self._shutter_stops)
            if on:
                self.ae_box.setChecked(False)
                self.ae_box.setEnabled(False)
                self.reprobe_btn.setEnabled(False)
                self.camera.set_long_exposure(True)
                self._shutter_stops = build_shutter_stops(self._shutter_range[0],
                                                          LONG_EXPOSURE_MAX_US)
            else:
                # Snap back into the fast range's ceiling BEFORE the ceiling itself
                # shrinks, so ExposureTime is never left above what the sensor allows.
                fast_hi = self._shutter_range[1]
                if current_us > fast_hi:
                    current_us = fast_hi
                    self.camera.set_exposure(shutter_us=int(current_us))
                self.camera.set_long_exposure(False, normal_max_us=fast_hi)
                self._shutter_stops = build_shutter_stops(*self._shutter_range)
                self.ae_box.setEnabled(True)
                self.reprobe_btn.setEnabled(True)
                # A drag in flight when Long Exposure is toggled off must not still
                # fire a stale pending value into the now-shrunk range.
                self._shutter_apply_timer.stop()
                self._pending_shutter_us = None
            self.shutter_slider.setRange(0, len(self._shutter_stops) - 1)
            self.shutter_slider.setValue(shutter_stop_pos(current_us, self._shutter_stops))
            self.shutter_label.setText(fmt_shutter_fraction(current_us))

        def _on_reprobe(self):
            # probe() blocks while AE settles; run it on a worker thread so the Qt
            # thread (which also services the camera) never stalls.
            self.reprobe_btn.setEnabled(False)
            self.exp_status.setText("reprobing ...")

            def _worker():
                try:
                    result = self.camera.probe()
                except Exception as exc:
                    result = exc
                self.probe_done_signal.emit(result)

            threading.Thread(target=_worker, daemon=True).start()

        def _on_probe_finished(self, result):
            self.reprobe_btn.setEnabled(True)
            if isinstance(result, Exception):
                self.exp_status.setText("reprobe failed: {}".format(result))
                return
            self.camera.apply_exposure_lock(result)
            if provenance.save_profile is not None:
                provenance.save_profile(result)
            self._apply_panel_values(result["shutter_us"], result["analogue_gain"],
                                     result["awb_red_gain"], result["awb_blue_gain"],
                                     False, False)
            self.exp_status.setText("Reprobed:\nShutter {} - Gain {:.2f}".format(
                fmt_shutter_fraction(result["shutter_us"]), result["analogue_gain"]))

        def _enforce_exposure_lock(self):
            # A capture must never be taken mid-hunt. If either channel is on auto,
            # freeze it at its current metered value via apply_exposure_lock,
            # rather than just flipping AeEnable off and trusting wherever the
            # driver happened to settle. A no-op when already locked or manual,
            # so a deliberate manual exposure is left alone. Reused as-is by
            # burst kinds later: a burst needs one stable exposure across the
            # whole set, not a per-frame one.
            e = self.camera.read_exposure()
            if not (e["auto_exposure"] or e["auto_white_balance"]):
                return
            locked = {"shutter_us": e["shutter_us"], "analogue_gain": e["analogue_gain"],
                      "awb_red_gain": e["awb_red_gain"], "awb_blue_gain": e["awb_blue_gain"]}
            self.camera.apply_exposure_lock(locked)
            self._apply_panel_values(locked["shutter_us"], locked["analogue_gain"],
                                     locked["awb_red_gain"], locked["awb_blue_gain"],
                                     False, False)
            # exp_status, not capture_status: this is an exposure event, and
            # capture_status is about to be overwritten with "capturing still ...".
            # FIX: this used fmt_shutter_ms (milliseconds) while every other
            # exposure display uses fmt_shutter_fraction -- the one inconsistency
            # a code review turned up matching the report of mismatched units
            # between exposure displays.
            self.exp_status.setText(
                "Auto-locked for capture:\nShutter {} - Gain {:.2f}".format(
                    fmt_shutter_fraction(locked["shutter_us"]), locked["analogue_gain"]))

        # --- capture (section 5, non-blocking) ------------------------------
        def _set_capture_status(self, text, tooltip=None):
            # Kept short by construction, not by disabling word wrap: this label
            # sits directly above the Capture button, and a long message here
            # previously wrapped to a variable number of lines and pushed the
            # button (and everything below it) up and down each time. The full
            # detail (exact filename, session, capture index, or error text)
            # goes in the tooltip instead of the visible line.
            self.capture_status.setText(text)
            self.capture_status.setToolTip(tooltip if tooltip is not None else text)

        def _start_capture(self):
            # Shoot a still without blocking the Qt thread. Guard re-entry so a
            # second trigger while one is in flight is ignored. A running focus tick
            # keeps going: it writes only self.readout, while capture state writes
            # only self.capture_status, so the two labels never fight.
            if self._zstack is not None:
                # Z-STACK AID: while a stack is active, Capture (button, menu
                # action, or any keyboard shortcut routed here) means "capture
                # the next plane," full stop -- not the plain untagged-snap
                # path below. _capture_zstack_plane does its own self._capturing
                # guard, same contract as every other kind here.
                self._capture_zstack_plane()
                return
            if self._capturing:
                return
            # --- RECORD BUTTON (separable): defensive re-check -- the button is
            # already disabled while recording, but a keyboard shortcut or the
            # File menu's Capture action could still reach this directly.
            if self.camera.is_recording():
                return
            if self._armed is not None:
                # Arm-then-fire: a walkthrough already collected parameters
                # and relabeled this same button/action; this press is the
                # deliberate second press that actually changes the
                # physical setup (dark slide, ambient blocking) and runs
                # the sequence.
                self._fire_armed_burst()
                return
            if self._session is None:
                # Open a session on the first shot: a timestamped folder under
                # provenance.OUT_ROOT via provenance.Session (see provenance.py).
                # locked_settings snapshots profile.json (or {} if none); the actual
                # per-shot exposure enforced below is recorded per-capture instead,
                # via record_capture's metadata, not here.
                self._ensure_session()
            self._capturing = True
            self._enforce_exposure_lock()
            # Just the kind name on the button ("Snap"), not "Capturing ...":
            # the disabled/relabeled button IS the busy indicator. The status
            # line above it is left alone here (a single frame has no count
            # worth calling out); it will show the saved-file message once
            # this finishes.
            self._set_capture_controls(enabled=False, label="Snap")

            def _on_done(result):
                self.capture_done_signal.emit(result)

            # FIX (unique prefix per snap): every snap previously wrote to the
            # same "snap_frame_0000" stem, so a second snap in the same
            # session silently overwrote the first. _snap_counter increments
            # per snap (reset when a new session opens, see _ensure_session),
            # giving "snap_frame_0000", "snap_frame_0001", ... matching the
            # same "<prefix>frame_<idx>" naming every other kind already uses.
            stem = "snap_frame_{:04d}".format(self._snap_counter)
            self._snap_counter += 1
            try:
                self.camera.capture_still_async(self._session.dir, stem, _on_done)
            except Exception as exc:
                self.capture_done_signal.emit(exc)

        def _on_capture_kind_chosen(self, index):
            # The combo is an action list, not a persistent mode selector: any
            # real choice (index > 0, skipping the placeholder) fires the
            # matching walkthrough immediately, then resets back to the
            # placeholder so it never reads as "currently selected kind"
            # sitting stale after the walkthrough finishes or gets cancelled.
            if index <= 0:
                return
            text = self.capture_kind_combo.itemText(index)
            self.capture_kind_combo.blockSignals(True)
            self.capture_kind_combo.setCurrentIndex(0)
            self.capture_kind_combo.blockSignals(False)
            if text.startswith("Flat"):
                self._walkthrough_flat()
            elif text.startswith("Science"):
                self._walkthrough_science()
            elif text.startswith("HDR"):
                self._walkthrough_hdr()
            elif text.startswith("Dark"):
                self._walkthrough_dark()
            elif text.startswith("Run sequence"):
                self._walkthrough_batch()

        def _set_capture_controls(self, enabled, label):
            self.capture_btn.setEnabled(enabled)
            self.capture_btn.setText(label)
            self._capture_action.setEnabled(enabled)
            # Z-STACK AID: busy is busy, same reasoning as record_btn just
            # below -- the Start/End TEXT is managed separately by
            # _start_zstack/_on_zstack_plane_finished/_end_zstack, this only
            # ever touches whether the button can be clicked at all.
            self.zstack_btn.setEnabled(enabled)
            # --- RECORD BUTTON (separable): a capture/burst busy disables Record
            # too, since the two have not been verified safe to run concurrently
            # on real hardware. Re-enabling here only when nothing is currently
            # recording is a defensive check; the entry points on both sides
            # already keep the two from overlapping in the first place.
            self.record_btn.setEnabled(enabled and not self.camera.is_recording())

        def _on_capture_finished(self, result):
            # On the GUI thread (via capture_done_signal), so touching widgets is
            # safe. result is a CaptureResult on success or an Exception on failure;
            # either way the control comes back, so the window never hangs.
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            if isinstance(result, Exception):
                self._set_capture_status("capture failed",
                                         "capture failed: {}".format(result))
                return
            try:
                idx = provenance.record_capture(self._session, result)
                self._set_capture_status(
                    "saved {}".format(result.raw.name),
                    "saved {}  (session {}, capture #{})".format(
                        result.raw.name, self._session.ts, idx))
                # Auto-processing (Part 03): Snap is a genuinely new call
                # site here -- before this part only science/hdr ever
                # reached _run_process_cmd (frame-averaging a single frame
                # was considered pointless), but hdr_from_session.py's
                # process() already has a working kind in ("science",
                # "snap") branch, so this is wiring, not new processing
                # logic. Matches Casual Mode's always-functional design:
                # no Yes/No gate, every capture kind processes automatically.
                self._auto_process("snap", idx)
            except Exception as exc:
                self._set_capture_status(
                    "saved but recording failed",
                    "saved {} but recording failed: {}".format(result.raw.name, exc))

        # --- RECORD BUTTON (separable; see the banner comment near
        # VIDEO_OUT_ROOT at module level for the full removal list)
        def _toggle_recording(self):
            """Documentation/review video only -- own folder (VIDEO_OUT_ROOT),
            own filenames, no Session, no sidecar, no pixel hash. Capture and
            Record are kept mutually exclusive in both directions: this
            refuses to start while a still/burst is in progress or armed, and
            _set_capture_controls disables Record whenever a capture starts,
            since the two have not been verified safe to run concurrently.

            FIX (on-rig report, round 1): the first version of this called
            start_recording/stop_recording directly on the Qt thread,
            assuming both were fast control calls. On real hardware,
            stop_recording froze the whole window -- Picamera2/ffmpeg
            finalizing the encoder and output is not guaranteed instant,
            the exact class of bug capture_still_async hit before it was
            moved to a worker thread. Both verbs were moved to worker
            threads, one spawned on Record and a separate one spawned on
            Stop, each exiting as soon as its call returned.

            FIX (on-rig report, round 2 -- the "no file written, no error"
            bug): that two-short-lived-threads shape is exactly what broke
            it. picamera2's FfmpegOutput forks ffmpeg with
            preexec_fn=prctl.set_pdeathsig(SIGKILL), which ties ffmpeg's
            life to the SPECIFIC OS THREAD that forked it (start_encoder's
            caller), not to this process. The old Record-thread called
            start_recording() and returned immediately -- so within
            milliseconds ffmpeg got SIGKILLed by the kernel, long before
            Stop was ever pressed. Confirmed with a minimal repro outside
            this GUI: identical config, calling start_encoder from a
            short-lived thread while the Qt loop is running reproduces "no
            file, no exception" every time; keeping that thread alive
            through the encoder's lifetime fixes it. The failure is silent
            because the broken pipe this causes is caught and swallowed
            inside picamera2's own outputframe(), and stop_encoder() is a
            clean no-op once self.ffmpeg is already None -- nothing here
            could have raised.

            So: one persistent worker thread per recording session, not two
            short-lived ones. It calls start_recording(), signals the GUI,
            then blocks on _record_stop_event -- staying alive, and keeping
            ffmpeg alive -- until Stop sets that event, at which point this
            same thread (never a new one) calls stop_recording() and only
            then exits. Stop no longer spawns a thread at all; it just sets
            the event the running one is already waiting on."""
            if self.camera.is_recording():
                self.record_btn.setEnabled(False)
                self.record_btn.setText("Stopping...")
                self._record_stop_event.set()
                return
            if self._capturing or self._armed is not None:
                return   # a still/burst is in progress or armed; button is disabled anyway
            VIDEO_OUT_ROOT.mkdir(parents=True, exist_ok=True)
            stem = "clip_{}".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
            self.record_btn.setEnabled(False)
            self.record_btn.setText("Starting...")
            self._record_stop_event = threading.Event()

            def _worker():
                try:
                    result = self.camera.start_recording(VIDEO_OUT_ROOT, stem)
                except Exception as exc:
                    self.record_start_done_signal.emit(exc)
                    return
                self.record_start_done_signal.emit(result)
                # Park here, on the same thread that forked ffmpeg, for the
                # whole recording -- see docstring above.
                self._record_stop_event.wait()
                try:
                    stop_result = self.camera.stop_recording()
                except Exception as exc:
                    stop_result = exc
                self.record_stop_done_signal.emit(stop_result)

            # Held on self, not fire-and-forget: closeEvent needs to JOIN this
            # thread on quit. It is the thread ffmpeg's life is tied to, so
            # letting the process exit while it is still parked kills ffmpeg
            # the same way the original bug did, just triggered by quitting
            # instead of by the thread returning early.
            self._record_thread = threading.Thread(target=_worker, daemon=True)
            self._record_thread.start()

        def _on_record_start_finished(self, result):
            # On the GUI thread (via record_start_done_signal), so touching
            # widgets is safe. result is a Path on success or an Exception.
            if isinstance(result, Exception):
                self.record_btn.setText("Record")
                self.record_btn.setEnabled(not self._capturing and self._armed is None)
                self._set_capture_status("start recording failed",
                                         "start recording failed: {}".format(result))
                return
            self.record_btn.setText("Stop Recording")
            self.record_btn.setEnabled(True)
            self.capture_btn.setEnabled(False)
            self._capture_action.setEnabled(False)
            self.capture_kind_combo.setEnabled(False)
            self._set_capture_status("recording...", "recording to {}".format(result))

        def _on_record_stop_finished(self, result):
            # On the GUI thread (via record_stop_done_signal). Capture controls
            # come back regardless of success or failure, same as
            # _on_capture_finished: a failed stop should not leave the window
            # stuck with everything disabled and no way to recover.
            self.record_btn.setEnabled(True)
            self.record_btn.setText("Record")
            self.capture_btn.setEnabled(True)
            self._capture_action.setEnabled(True)
            self.capture_kind_combo.setEnabled(True)
            if isinstance(result, Exception):
                self._set_capture_status("stop recording failed",
                                         "stop recording failed: {}".format(result))
                return
            self._set_capture_status("saved {}".format(result.name),
                                     "saved {}".format(result))
        # --- end record button (methods) -------------------------------------

        # --- burst / HDR walkthroughs (arm-then-fire on the same Capture control)
        def _ensure_session(self):
            if self._session is None:
                self._session = provenance.Session(
                    provenance.OUT_ROOT, provenance.load_profile() or {}, self._display_flags)
                self._snap_counter = 0   # fresh per session; see _start_capture

        # --- shared dialog shape (consistent, flat command dialogs) ---
        # QMessageBox's own convenience constructors (question/information) and
        # QInputDialog.getInt auto-size to whatever their content computes,
        # which is where the inconsistent shapes came from: a short Yes/No
        # came out narrow and tall, a longer message came out stretched nearly
        # edge to edge.
        #
        # On-rig findings from two different dialogs corrected the original
        # approach here:
        #   - QMessageBox: the stylesheet min-width trick DOES control the box
        #     size (confirmed good on-rig), but the text was left sitting in
        #     one corner rather than using the space -- added centered
        #     alignment to fix that, box size unchanged.
        #   - QInputDialog: a plain .resize() call turned out NOT to reliably
        #     hold against a long unbroken label (on-rig, the dialog grew far
        #     wider than the requested size anyway). Dropped the resize call;
        #     shape now comes from choosing sensible \n breaks in each
        #     dialog's own text instead, the same mechanism already confirmed
        #     working for the two-line exp_status messages.
        _DIALOG_MIN_WIDTH = 440

        def _flat_question(self, title, text, default=None):
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(text)
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(default if default is not None else QMessageBox.No)
            box.setStyleSheet(
                "QLabel{{min-width: {}px; qproperty-alignment: AlignCenter;}}"
                .format(self._DIALOG_MIN_WIDTH))
            return box.exec_()

        def _flat_information(self, title, text):
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(text)
            box.setStandardButtons(QMessageBox.Ok)
            box.setStyleSheet(
                "QLabel{{min-width: {}px; qproperty-alignment: AlignCenter;}}"
                .format(self._DIALOG_MIN_WIDTH))
            box.exec_()

        def _flat_ask_int(self, title, label, value, minv, maxv, step=1):
            dlg = QInputDialog(self)
            dlg.setWindowTitle(title)
            dlg.setLabelText(label)
            dlg.setIntRange(minv, maxv)
            dlg.setIntValue(value)
            dlg.setIntStep(step)
            ok = dlg.exec_() == QDialog.Accepted
            return dlg.intValue(), ok

        def _flat_ask_text(self, title, label, value=""):
            dlg = QInputDialog(self)
            dlg.setWindowTitle(title)
            dlg.setLabelText(label)
            dlg.setTextValue(value)
            ok = dlg.exec_() == QDialog.Accepted
            return dlg.textValue().strip(), ok

        def _reshoot_guard(self, prefixes, kinds_set, label):
            # If frames already exist for these prefixes, confirm before
            # clearing them (default No). Declining cancels the walkthrough
            # outright.
            hits = self._session.existing(prefixes)
            if not hits:
                return True
            resp = self._flat_question(
                "Re-shoot {}?".format(label),
                "Clear {} existing {} frame(s) and re-shoot?".format(len(hits), label))
            if resp != QMessageBox.Yes:
                self._set_capture_status("{} cancelled".format(label),
                                         "kept - {} walkthrough cancelled.".format(label))
                return False
            self._session.clear(prefixes, kinds_set)
            return True

        def _ask_frames(self, prompt_label, cap=MAX_BURST):
            # Capped at MAX_BURST. A dialog Cancel (ok=False) aborts the whole
            # walkthrough, same as declining the guard.
            default = DEFAULT_BURST if DEFAULT_BURST <= cap else cap
            n, ok = self._flat_ask_int(
                "Frame count", "{} (1-{}):".format(prompt_label, cap), default, 1, cap, 1)
            return n if ok else None

        def _arm(self, kind, label, status=None, **params):
            self._armed = {"kind": kind, **params}
            self._set_capture_controls(enabled=True, label=label)
            self._set_capture_status(
                status if status is not None else "armed: {}".format(kind),
                "{}  (press Capture again to fire, Esc to cancel)".format(label))

        def _cancel_armed(self):
            if self._armed is None:
                return
            armed = self._armed
            self._armed = None
            if armed["kind"] == "hdr" and armed.get("phase") == "dark":
                # Still mode was already exited right after the science phase
                # (see _run_burst_kind); this just needs to record the
                # science-only result rather than silently losing those
                # frames. Reuses the same path _continue_hdr_to_dark's own
                # Cancel button uses.
                self._abort_hdr_mid_sequence(
                    armed["sci_levels"], armed["base_us"],
                    "cancelled before firing, science frames kept")
                return
            kind = armed["kind"]
            self._set_capture_controls(enabled=True, label="Capture")
            self._set_capture_status("armed capture cancelled", "cancelled: {}".format(kind))

        # --- z-stack tagging (section 8's own capture-side half; see stacks.py) ---
        def _on_tag_stack(self):
            # Mirrors capture.py's original do_tag exactly: tags THIS session's
            # most recent science capture (one science capture per session is
            # the project's own one-session-one-plane convention -- flat/dark
            # are calibration frames, never stack planes, and an untagged snap
            # is a throwaway single frame, not a measurement plane). Cross-
            # session duplicate/gap checking is stacks.validate_all's job, not
            # this dialog's -- it can only see the current session.
            if self._session is None or not self._session.captures:
                self._flat_information(
                    "Nothing to tag", "No capture in this session yet -- "
                    "shoot a science frame first, then tag it.")
                return
            sci_positions = [i for i, c in enumerate(self._session.captures)
                             if c.get("kind") == "science"]
            if not sci_positions:
                self._flat_information(
                    "Nothing to tag", "No science capture in this session yet -- "
                    "shoot one first, then tag it.")
                return
            position = sci_positions[-1]

            stack_id, ok = self._flat_ask_text(
                "Tag as stack plane", "Stack ID (e.g. T4):",
                getattr(self, "_last_stack_id", ""))
            if not ok:
                return
            if not stack_id:
                self._flat_information("Tag cancelled", "Stack ID cannot be blank.")
                return

            plane, ok = self._flat_ask_int(
                "Tag as stack plane", "Plane (depth position, integer):",
                getattr(self, "_last_stack_plane", 1) + 1, 0, 999, 1)
            if not ok:
                return

            try:
                _stacks.apply_tag(self._session.captures, position, stack_id, plane)
            except ValueError as exc:
                self._flat_information("Slot already taken", str(exc))
                return
            self._session.write()
            self.meter.reset_field()      # new plane locked in: last plane's peak/settle
                                           # is stale history, not a real reading for this one
            self._last_stack_id = stack_id
            self._last_stack_plane = plane
            output = _stacks.output_name(stack_id, plane)
            self._set_capture_status(
                "tagged: {} plane {}".format(stack_id, plane),
                "science capture -> stack {!r}, plane {} (output will be {})"
                .format(stack_id, plane, output))
            self._flat_information(
                "Tagged", "Science capture tagged as stack {!r}, plane {}.\n"
                "Output will be named {}.".format(stack_id, plane, output))

        # --- Z-STACK AID (BUILD_LIST Tier 3 item 6): a one-click alternative
        # to the manual _on_tag_stack above -- same stacks.apply_tag call,
        # just automatic, per plane, with its own nested session per plane
        # instead of the shared self._session flat/science/hdr/dark all
        # capture into. See the module docstring's own z-stack notes and
        # HANDOFF.md for the full interaction-model reasoning.
        def _toggle_zstack(self):
            if self._zstack is not None:
                self._end_zstack()
            else:
                self._start_zstack()

        def _start_zstack(self):
            # Same guard shape every other kind-starting action here uses
            # (_walkthrough_burst, _walkthrough_hdr, _toggle_recording): a
            # capture already in flight, an armed-but-not-fired burst, or an
            # active recording all refuse a new stack rather than racing it.
            if self._capturing or self._armed is not None:
                return
            if self.camera.is_recording():
                return
            stack_id, root, prov_root = provenance.new_zstack_root_dirs()
            self._zstack = {"root": root, "prov_root": prov_root,
                           "stack_id": stack_id, "next_plane": 0}
            # Mutual exclusion, mirroring how Record disables capture_kind_
            # combo while recording (_on_record_start_finished): nothing else
            # should start a flat/science/hdr/dark burst or a recording while
            # a stack is running. capture_btn/_capture_action stay enabled --
            # see _start_capture's own repurposing branch.
            self.capture_kind_combo.setEnabled(False)
            self.record_btn.setEnabled(False)
            self.zstack_btn.setText("End Z-Stack")
            self._set_capture_status(
                "z-stack {} started".format(stack_id),
                "z-stack {!r} started at {}".format(stack_id, root))
            self._capture_zstack_plane()   # "first press captures plane 0 immediately"

        def _capture_zstack_plane(self):
            if self._capturing:
                return
            plane = self._zstack["next_plane"]
            plane_dir = self._zstack["root"] / "plane_{}".format(plane)
            plane_prov_dir = self._zstack["prov_root"] / "plane_{}".format(plane)
            plane_session = provenance.Session(
                self._zstack["root"], provenance.load_profile() or {},
                self._display_flags, session_dir=plane_dir, provenance_dir=plane_prov_dir)
            self._capturing = True
            self._enforce_exposure_lock()
            self._set_capture_controls(
                enabled=False, label="Plane {}".format(plane))
            self._set_capture_status(
                "z-stack {}: capturing plane {} ...".format(
                    self._zstack["stack_id"], plane))

            def _worker():
                try:
                    result = self.camera.capture_burst(plane_session.dir, "science_", 1)
                    idx = provenance.record_burst(plane_session, "science", "science_", result)
                    _stacks.apply_tag(plane_session.captures, idx,
                                      self._zstack["stack_id"], plane)
                    plane_session.write()
                    self._score_capture_sharpness(plane_session, idx, result)
                    payload = {"plane": plane, "session": plane_session}
                except Exception as exc:
                    payload = exc
                self.zstack_plane_done_signal.emit(payload)

            threading.Thread(target=_worker, daemon=True).start()

        def _on_zstack_plane_finished(self, result):
            # On the GUI thread (via zstack_plane_done_signal). A failure is
            # reported, never silently dropped, and does NOT advance
            # next_plane -- the next Capture press retries the same plane
            # number rather than leaving a silent gap in the stack.
            self._capturing = False
            if isinstance(result, Exception):
                self._set_capture_controls(enabled=True, label="Capture")
                self._set_capture_status(
                    "z-stack plane capture failed",
                    "z-stack {} plane {} failed: {}".format(
                        self._zstack["stack_id"] if self._zstack else "?",
                        self._zstack["next_plane"] if self._zstack else "?", result))
                return
            plane = result["plane"]
            self._zstack["next_plane"] = plane + 1
            # SPEC_focus_aid_fps_and_stack_reset.md part 2, carried over per
            # its own forward-looking note: "whatever action ends up being
            # 'this plane is locked in' -- whether that's today's
            # _on_tag_stack or its future replacement." This IS that
            # replacement's own successful-tag path (apply_tag already
            # succeeded inside the worker above; a failed capture/tag took
            # the isinstance(result, Exception) branch above and returned
            # before reaching here) -- last plane's peak/settle is stale
            # history, not a real reading for this one.
            self.meter.reset_field()
            self._set_capture_controls(enabled=True, label="Capture")
            self.zstack_btn.setText(
                "End Z-Stack ({} plane{})".format(
                    plane + 1, "" if plane == 0 else "s"))
            output = _stacks.output_name(self._zstack["stack_id"], plane)
            self._set_capture_status(
                "z-stack {}: plane {} captured".format(self._zstack["stack_id"], plane),
                "plane {} captured and tagged (output will be {}); Capture "
                "again for the next plane, or End Z-Stack to finish"
                .format(plane, output))

        def _end_zstack(self):
            # Same "ignore a press mid-operation" guard every other toggle
            # here uses -- ending while a plane capture is still in flight
            # would race _on_zstack_plane_finished's own state updates.
            if self._capturing:
                return
            zstack = self._zstack
            self._zstack = None
            self.capture_kind_combo.setEnabled(True)
            self.record_btn.setEnabled(not self.camera.is_recording())
            self.zstack_btn.setText("Start Z-Stack")

            plane_dirs = sorted(zstack["root"].glob("plane_*"))
            # validate_all reads session.json straight from whatever dirs
            # it's given -- session.json lives in the mirrored provenance
            # dir now, not beside each plane's raw frames (Part 03), so map
            # through _provenance_dir_for rather than passing plane_dirs
            # (capture dirs) directly, which would silently find nothing.
            prov_plane_dirs = [_provenance_dir_for(d) for d in plane_dirs]
            issues = _stacks.validate_all(prov_plane_dirs)
            if issues:
                detail = "\n".join(str(i) for i in issues)
            else:
                detail = "No issues found."
            self._set_capture_status(
                "z-stack {} ended ({} planes)".format(zstack["stack_id"], len(plane_dirs)),
                "z-stack {!r} ended: {} plane folder(s) at {}\n{}".format(
                    zstack["stack_id"], len(plane_dirs), zstack["root"], detail))

            if not plane_dirs:
                return
            resp = self._flat_question(
                "Process this stack?",
                "Z-stack {!r} ended with {} plane(s).\n{}\n\n"
                "Process it now?".format(zstack["stack_id"], len(plane_dirs), detail))
            if resp != QMessageBox.Yes:
                return
            if _process_wizard is None:
                self._set_capture_status(
                    "processing wizard unavailable",
                    "process_wizard.py not found beside this file, skipped")
                return
            wiz = _process_wizard.ProcessWizard(zstack["root"], self)
            wiz.file_page.gallery.list_widget.selectAll()
            wiz.exec_()
        # --- end Z-STACK AID --------------------------------------------

        def _walkthrough_burst(self, kind, prefix, kinds_set, instruction, auto_fire=False):
            # Shared by flat/science/dark: reshoot guard, an optional instructional
            # message (flat/dark have one, science does not), then a single frame-
            # count ask. HDR is enough of a special case (two asks, a message in
            # between, two prefix sets) that it gets its own method below instead.
            # auto_fire=True is how a batch sequence (_advance_batch) drives this:
            # the setup dialogs still run (frame counts can differ per kind), but
            # firing happens immediately once setup completes, no separate manual
            # Capture press between each selected kind. Declining a step here
            # skips just that kind and lets the batch continue, rather than
            # treating a "No" on one item as cancelling everything else selected.
            if self._capturing or self._armed is not None:
                return
            # --- RECORD BUTTON (separable): defensive re-check, see _start_capture
            if self.camera.is_recording():
                return
            self._ensure_session()
            # Flat is a replaced-outright standing library (provenance.
            # FLAT_ROOT, Part 03), never a per-session capture -- there is
            # nothing to "clear existing frames and reshoot" about it, each
            # new Flat capture simply replaces whatever was there. Every
            # other kind still guards against silently clobbering frames
            # already shot into THIS session.
            if kind != "flat" and not self._reshoot_guard([prefix], kinds_set, kind):
                if auto_fire and self._batch_active:
                    self._advance_batch()
                return
            if instruction:
                self._flat_information(kind.capitalize(), instruction)
            n = self._ask_frames("{} frames".format(kind.capitalize()))
            if n is None:
                if auto_fire and self._batch_active:
                    self._advance_batch()
                return
            if auto_fire:
                self._armed = {"kind": kind, "n": n, "prefix": prefix}
                self._fire_armed_burst()
            else:
                self._arm(kind, "Fire: {}".format(kind.capitalize()),
                         status="{} {} frames".format(n, kind.capitalize()),
                         n=n, prefix=prefix)

        def _walkthrough_flat(self, auto_fire=False):
            self._walkthrough_burst(
                "flat", "flat_", {"flat"},
                "Empty field, illuminator ON,\n~60-70% and unclipped.",
                auto_fire=auto_fire)

        def _walkthrough_science(self, auto_fire=False):
            self._walkthrough_burst("science", "science_", {"science"}, None,
                                    auto_fire=auto_fire)

        def _walkthrough_dark(self, auto_fire=False):
            self._walkthrough_burst(
                "dark", "dark_", {"dark"},
                "Illuminator OFF, no ambient leak\n(verify the raw floor).",
                auto_fire=auto_fire)

        def _walkthrough_hdr(self, auto_fire=False):
            # Mirrors do_hdr's ORDER exactly, which matters: the CLI does not ask
            # for the dark frame count until AFTER the science frames are already
            # shot (it asks while still mode is held open, right when the
            # switch-off-the-illuminator note appears). Asking both counts up
            # front, as an earlier version of this did, ran both phases back to
            # back with no real pause, so there was never a moment to actually
            # turn off the light before darks fired. This only collects n;
            # _continue_hdr_to_dark collects nd once science is actually done,
            # and that transition ALWAYS fires immediately on OK regardless of
            # auto_fire (that pause and the timing around it is deliberate, not
            # something batching should skip).
            if self._capturing or self._armed is not None:
                return
            # --- RECORD BUTTON (separable): defensive re-check, see _start_capture
            if self.camera.is_recording():
                return
            self._ensure_session()
            ordered = sorted(DEFAULT_STOPS)
            sci_pre = ["{}_".format(i) for i in range(1, len(ordered) + 1)]
            dark_pre = ["dark_{}_".format(i) for i in range(1, len(ordered) + 1)]
            if not self._reshoot_guard(sci_pre + dark_pre, {"hdr"}, "HDR"):
                if auto_fire and self._batch_active:
                    self._advance_batch()
                return
            n = self._ask_frames("HDR science frames per level ({} levels)"
                                 .format(len(ordered)))
            if n is None:
                if auto_fire and self._batch_active:
                    self._advance_batch()
                return
            if auto_fire:
                self._armed = {"kind": "hdr", "phase": "science", "n": n}
                self._fire_armed_burst()
            else:
                # "Fire: HDR > Dark", not "Fire: HDR science (n x5 levels)":
                # the button just needs to convey the two-phase flow this one
                # press leads through; the frame count lives in capture_status
                # instead, both now (armed) and once it is actually firing
                # (see _fire_armed_burst).
                self._arm("hdr", "Fire: HDR > Dark", status="{} HDR frames".format(n),
                         phase="science", n=n)

        def _walkthrough_batch(self):
            # Checkbox picker for running several capture kinds automatically,
            # in the fixed flat/science/hdr/dark order, with no separate manual
            # Capture press between them. Each kind's own setup dialogs
            # (reshoot guard, frame count, HDR's illuminator pause) still run.
            if self._capturing or self._armed is not None or self._batch_active:
                return
            # --- RECORD BUTTON (separable): defensive re-check, see _start_capture
            if self.camera.is_recording():
                return
            dlg = BatchSelectDialog(self)
            if dlg.exec_() != QDialog.Accepted:
                return
            kinds = dlg.selected_kinds()
            if not kinds:
                return
            self._ensure_session()
            self._batch_queue = kinds
            self._batch_active = True
            self._set_capture_status(
                "sequence: {}".format(" -> ".join(kinds)),
                "running {} in order".format(", ".join(kinds)))
            self._advance_batch()

        def _advance_batch(self):
            # Pops and starts the next queued kind, auto-firing once its setup
            # completes; called both to kick off the sequence and, from
            # _on_burst_finished, after each kind completes to move to the next
            # one. Declining a step's own reshoot guard or frame-count ask
            # skips just that kind (see _walkthrough_burst/_walkthrough_hdr's
            # auto_fire branches) rather than aborting everything selected.
            if not self._batch_queue:
                self._batch_active = False
                self._set_capture_status("sequence done", "batch capture sequence complete")
                return
            kind = self._batch_queue.pop(0)
            if kind == "flat":
                self._walkthrough_flat(auto_fire=True)
            elif kind == "science":
                self._walkthrough_science(auto_fire=True)
            elif kind == "dark":
                self._walkthrough_dark(auto_fire=True)
            elif kind == "hdr":
                self._walkthrough_hdr(auto_fire=True)

        def _abort_batch(self):
            # Esc with nothing currently armed aborts the REST of a running
            # sequence (whatever already fired and got recorded is kept; this
            # only stops what has not started yet). See keyPressEvent.
            if not self._batch_active:
                return
            remaining = list(self._batch_queue)
            self._batch_queue = []
            self._batch_active = False
            self._set_capture_status(
                "sequence aborted", "sequence aborted; {} not run".format(
                    ", ".join(remaining) if remaining else "nothing further was queued"))

        def _fire_armed_burst(self):
            # All four burst kinds require a locked exposure, no exceptions: one
            # _enforce_exposure_lock() call, before the worker thread starts,
            # covers all of them (not per frame, not per phase). For HDR's dark
            # phase specifically, exposure was already locked back at the
            # science phase and nothing re-enables auto in between (still mode
            # being exited and re-entered around the pause, see
            # _run_burst_kind, does not touch AE/AWB state); re-locking here
            # would be meaningless, and base_us must stay the SAME value both
            # phases bracket from, not be re-read.
            armed = self._armed
            self._armed = None
            self._capturing = True
            kind = armed["kind"]
            continuing_hdr = (kind == "hdr" and armed.get("phase") == "dark")
            if continuing_hdr:
                base_us = armed["base_us"]
            else:
                self._enforce_exposure_lock()
                base_us = self.camera.read_exposure()["shutter_us"]
            # Button just names the kind: "Flat", "Science", "Dark", or "HDR"
            # for EITHER of HDR's own phases (not "HDR science" / "HDR dark"),
            # no "Capturing" prefix and no frame count. The disabled, relabeled
            # button already is the busy indicator; the kind is all it needs
            # to say, everything else lives in the status line above it.
            self._set_capture_controls(enabled=False,
                                       label="HDR" if kind == "hdr" else kind.capitalize())
            if kind == "hdr":
                frame_text = ("{} HDR / {} dark frames".format(armed["n"], armed["nd"])
                              if continuing_hdr else "{} HDR frames".format(armed["n"]))
            else:
                frame_text = "{} {} frames".format(armed["n"], kind.capitalize())
            self._set_capture_status(frame_text)

            def _worker():
                try:
                    result = self._run_burst_kind(armed, base_us)
                except Exception as exc:
                    result = exc
                self.burst_done_signal.emit(result)

            threading.Thread(target=_worker, daemon=True).start()

        def _run_burst_kind(self, armed, base_us):
            # Runs OFF the Qt thread (HDR especially can run long: multiple levels
            # x frames x settle waits). Only touches self.camera (whose blocking
            # burst/bracket verbs are explicitly designed to be called this way)
            # and self._session (plain file I/O); no widget access here.
            kind = armed["kind"]
            session = self._session
            if kind == "hdr":
                ordered = sorted(DEFAULT_STOPS)
                if armed.get("phase") == "science":
                    # Still mode is entered and exited around JUST the science
                    # shots here, not held through the pause that follows. On-
                    # rig report: holding it through the pause left the preview
                    # frozen on the old bright still-mode frame even after the
                    # illuminator was switched off, since a held still mode
                    # never resumes the lores callback, which looks exactly
                    # like a hang even though the capture itself is fine. The
                    # dark phase below re-enters still mode on its own when it
                    # actually fires, so the pause in between now runs with a
                    # live, responsive preview.
                    self.camera.enter_still_mode()
                    try:
                        sci_levels = self.camera.capture_bracket_phase(
                            session.dir, "", armed["n"], base_us, ordered)
                    finally:
                        self.camera.exit_still_mode(base_us)
                    sci_n = sum(lv["frame_count"] for lv in sci_levels)
                    return {"kind": "hdr", "phase": "science", "sci_levels": sci_levels,
                           "base_us": base_us, "n": armed["n"],
                           "summary": "{} science frames across {} levels"
                           .format(sci_n, len(ordered))}
                else:
                    # phase == "dark": re-enters still mode here (exited above,
                    # right after science) rather than continuing a session
                    # held open across the pause. Dark frames nest one level
                    # down, in dir/"dark" (Part 03: session-scoped imagery,
                    # never flat alongside the science frames above).
                    dark_dir = session.dir / "dark"
                    dark_dir.mkdir(parents=True, exist_ok=True)
                    self.camera.enter_still_mode()
                    try:
                        dark_levels = self.camera.capture_bracket_phase(
                            dark_dir, "dark_", armed["nd"], base_us, ordered)
                    finally:
                        self.camera.exit_still_mode(base_us)
                    idx = provenance.record_hdr(session, armed["sci_levels"], dark_levels)
                    sci_n = sum(lv["frame_count"] for lv in armed["sci_levels"])
                    dark_n = sum(lv["frame_count"] for lv in dark_levels)
                    return {"kind": "hdr", "phase": "dark", "index": idx,
                           "summary": "{} science + {} dark frames across {} levels"
                           .format(sci_n, dark_n, len(ordered))}
            else:
                prefix = armed["prefix"]
                # Part 03 folder split: flat replaces provenance.FLAT_ROOT
                # outright (one standing library, never a per-session
                # capture -- see _walkthrough_burst's own reshoot-guard
                # skip for "flat"); standalone dark nests under dir/"dark",
                # same as HDR's own dark phase above; science/snap still
                # write directly into the session dir.
                if kind == "flat":
                    target_dir = provenance.FLAT_ROOT
                    if target_dir.exists():
                        shutil.rmtree(target_dir)
                    target_dir.mkdir(parents=True, exist_ok=True)
                elif kind == "dark":
                    target_dir = session.dir / "dark"
                    target_dir.mkdir(parents=True, exist_ok=True)
                else:
                    target_dir = session.dir
                result = self.camera.capture_burst(target_dir, prefix, armed["n"])
                idx = provenance.record_burst(session, kind, prefix, result)
                if kind == "science":
                    # Post-capture QC (section 13): flat/dark are calibration
                    # frames, never stack planes (see _on_tag_stack's own
                    # comment), so only science gets scored. Runs here, off
                    # the Qt thread already (see this method's own docstring).
                    self._score_capture_sharpness(session, idx, result)
                return {"kind": kind, "index": idx, "summary": "{} frames".format(len(result["frames"]))}

        def _score_capture_sharpness(self, session, idx, result):
            """Post-capture QC (section 13): variance-of-Laplacian on the
            green plane extracted from frame 0 of this science burst -- the
            same "frame 0 stands for the burst" convention measure.py's own
            resolve_capture_raw uses, since every frame of a burst shares the
            same subject and exposure. A recorded number, distinct from the
            live focus aid: this runs once, after the shutter, on the
            capture actually written to disk, not on the ISP preview.
            Never raises into the capture flow -- a scoring failure (green
            extraction needs calibrate.py + debayer.py alongside this file;
            the frame could also just fail to read) is recorded honestly as
            sharpness_score=None rather than losing an otherwise-good
            capture over it."""
            score = None
            if _calibrate is not None:
                try:
                    green = _calibrate.load_green_plane(result["frames"][0].raw)
                    score = score_capture_sharpness(green)
                except Exception:
                    score = None
            session.captures[idx]["sharpness_score"] = score
            session.write()

        def _continue_hdr_to_dark(self, sci_result):
            # Called the instant the science phase's worker thread reports back
            # (see _on_burst_finished): go straight into the dark setup, note and
            # frame-count ask COMBINED in one dialog. The dialog itself is the
            # checkpoint: you read it, physically switch off the illuminator,
            # then act, so OK fires the dark phase immediately rather than
            # arming for yet another separate Capture press. Cancel disarms
            # entirely and captures nothing for dark ("back to normal"). The
            # preview is live through this whole pause (see _run_burst_kind):
            # still mode was exited right after science finished.
            ordered = sorted(DEFAULT_STOPS)
            default_n = DEFAULT_BURST if DEFAULT_BURST <= MAX_BURST else MAX_BURST
            nd, ok = self._flat_ask_int(
                "HDR: dark frames",
                "Science frames done.\n"
                "Switch off the illuminator and block ambient light.\n"
                "Dark frame count ({} levels):".format(len(ordered)),
                default_n, 1, MAX_BURST, 1)
            if not ok:
                self._abort_hdr_mid_sequence(
                    sci_result["sci_levels"], sci_result["base_us"],
                    "dark frames cancelled, science frames kept")
                return
            self._armed = {"kind": "hdr", "phase": "dark", "nd": nd, "n": sci_result["n"],
                          "sci_levels": sci_result["sci_levels"],
                          "base_us": sci_result["base_us"]}
            self._fire_armed_burst()

        def _abort_hdr_mid_sequence(self, sci_levels, base_us, reason):
            # Shared by "cancelled the dark-count ask" and "Escape while armed
            # for the dark phase" (see _cancel_armed). Still mode was already
            # exited right after the science phase completed (see
            # _run_burst_kind), so there is no camera-side state left to
            # unwind here, this only needs to record the science-only result
            # rather than silently losing those frames.
            self._capturing = True
            self._set_capture_controls(enabled=False, label="HDR")
            self._set_capture_status(reason)

            def _worker():
                try:
                    idx = provenance.record_hdr(self._session, sci_levels, [])
                    result = {"kind": "hdr", "phase": "dark", "index": idx,
                             "summary": "science-only (dark phase skipped)"}
                except Exception as exc:
                    result = exc
                self.burst_done_signal.emit(result)

            threading.Thread(target=_worker, daemon=True).start()

        def _auto_process(self, kind, index):
            # Auto-processing (Part 03) replaces the old Yes/No QMessageBox:
            # Snap, Science, and HDR all process automatically now, matching
            # Casual Mode's always-functional design -- no gate, no blocking
            # prompt. Invokes hdr_from_session.py (--index/display flags);
            # the actual run is shared with the manual processing wizard
            # (see _run_process_cmd). `kind` is accepted for symmetry with
            # the burst-finished call site and future logging, though
            # --index alone already fully selects the capture.
            #
            # --raw-ext must be detected, not left to hdr_from_session.py's
            # own "dng" default: the real Picamera2 backend always writes
            # .dng, so that default was never wrong on-rig, but the default
            # (no --camera) FakeCamera backend writes .tif, and the manual
            # processing wizard already detects this per capture via
            # capture_correction_status's own on-disk glob rather than
            # assuming a camera class -- reused here instead of a second,
            # camera-duck-typed way to answer the same question.
            cap = next((c for c in self._session.captures if c.get("index") == index), None)
            ext = capture_correction_status(
                self._session.dir, {"captures": self._session.captures}, cap)["ext"] \
                if cap is not None else "dng"
            self._run_process_cmd(self._session.dir, index, extra_args=["--raw-ext", ext])

        def _run_process_cmd(self, session_dir, index, extra_args=None):
            # Shared by the automatic offer (_auto_process) and the manual
            # processing wizard (_open_processing_wizard): same
            # hdr_from_session.py invocation shape, same worker thread (frame
            # averaging plus debayering at full res is not instant and must
            # not block the Qt thread), same busy-guard and completion
            # handling either way. --index alone fully selects the capture;
            # pick_capture ignores --kind whenever --index is given, so there
            # is no need to pass both.
            if PROCESSOR is None or not PROCESSOR.exists():
                self._set_capture_status(
                    "processing unavailable",
                    "hdr_from_session.py not found beside this file, skipped")
                return
            # FIX (on-rig report): all the real work (frame averaging, HDR
            # merge, debayer) completed successfully -- final.tif and
            # final_display.* existed -- but the GUI stayed stuck on
            # "Processing ...". Root cause: hdr_from_session.py's own
            # archive_raws() runs AFTER all of that and, with neither
            # --archive-raws nor --keep-raws given, defaults to a y/n prompt
            # via input(), only skipped if stdin is not a tty. subprocess.run
            # with no stdin= inherits the GUI's own stdin; if the GUI itself
            # was launched from a real terminal, the child sees a real tty
            # and blocks forever on a prompt nobody is there to answer, so
            # hdr_from_session.py never exits and subprocess.run() (and thus
            # this worker) never returns, no matter how fast the actual
            # processing was. Two independent fixes, so this cannot recur
            # even if one of them stops applying: force stdin closed
            # (guarantees isatty() is False regardless of how the GUI itself
            # was launched), and pass --keep-raws explicitly so the prompt
            # branch is never reached at all. Raw archiving is not something
            # this app does automatically; if that becomes wanted later it
            # should be its own explicit choice, not a side effect of a
            # prompt that happened to get suppressed.
            # hdr_from_session.py's positional arg is the PROVENANCE dir now
            # (Part 03: session.json no longer sits beside the raw frames);
            # session_dir here is the CAPTURE dir every caller of this method
            # actually has on hand (self._session.dir, or a dir list_sessions
            # returned), so map it through _provenance_dir_for rather than
            # asking every caller to track two directories.
            prov_dir = _provenance_dir_for(Path(session_dir))
            cmd = ([sys.executable, str(PROCESSOR), str(prov_dir),
                   "--index", str(index), "--keep-raws",
                   "--flat-root", str(provenance.FLAT_ROOT)]
                  + list(self._display_flags) + list(extra_args or []))
            # Keep RAW Images (Preferences > Advanced, applies to captures
            # from now on): the live preference is read at PROCESSING time,
            # not capture time, for both the automatic and manual-wizard
            # paths -- this is the only setting that changes what survives
            # once processing succeeds (Part 03: provenance is always
            # written regardless).
            if not load_pref("keep_raw_images", True):
                cmd.append("--delete-raw-on-success")
            # Additional export formats (Preferences > Advanced, Part 03) --
            # same "read live at processing time" reasoning as Keep RAW
            # Images just above. TIFF is now genuinely optional too (the
            # debayer.py tonemap/write split removed the old structural-
            # byproduct lock -- see _fmt_tiff_check's own comment).
            if not load_pref("export_format_tiff", True):
                cmd.append("--no-export-tiff")
            if not load_pref("export_format_png", True):
                cmd.append("--no-export-png")
            if load_pref("export_format_jpg", True):
                cmd.append("--export-jpg")
            if load_pref("export_format_dng", False):
                cmd.append("--export-dng")
                if load_pref("export_format_dng_merge", False):
                    cmd.append("--export-dng-merge")
            self._last_process_session_dir = Path(session_dir)
            self._last_process_index = index
            self._capturing = True   # reuse the same busy-guard the capture path uses
            self._set_capture_controls(enabled=False, label="Processing ...")
            self._set_capture_status("processing ...",
                                     "running: {}".format(" ".join(cmd)))

            def _worker():
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL)
                    payload = (r.returncode == 0, r.stdout, r.stderr)
                except Exception as exc:
                    payload = (False, "", str(exc))
                self.process_done_signal.emit(payload)

            threading.Thread(target=_worker, daemon=True).start()

        def _open_processing_wizard(self):
            # Manual counterpart to the automatic offer: browse ANY session
            # (not just the current one), pick any processable capture, see
            # what flat/dark correction is actually available for it right
            # now, then process on demand. See ProcessSessionDialog's
            # docstring for why this exists (dark is shot last on purpose,
            # often after the auto-offer for science/HDR has already come
            # and gone).
            if self._capturing:
                return
            dlg = ProcessSessionDialog(provenance.OUT_ROOT, self._display_flags, self)
            if dlg.exec_() != QDialog.Accepted:
                return
            picked = dlg.selected()
            if picked is None:
                return
            session_dir, cap_index, ext = picked
            self._run_process_cmd(session_dir, cap_index, extra_args=["--raw-ext", ext])

        def _open_archive_wizard(self):
            # Standalone: archive any session's raws without needing to
            # reprocess it first (hdr_from_session.py's own archive_raws is
            # only ever reachable after main() runs process(), which would
            # mean reprocessing just to tidy up an already-processed session).
            if self._capturing:
                return
            dlg = ArchiveSessionDialog(provenance.OUT_ROOT, self)
            if dlg.exec_() != QDialog.Accepted:
                return
            session_dir = dlg.selected_session_dir()
            if session_dir is None:
                return
            self._offer_archive_raws(session_dir)

        def _open_process_wizard(self):
            # The new choose-your-operations wizard (process_wizard.py),
            # separate from _open_processing_wizard's session/kind-based
            # ProcessSessionDialog above -- both stay, see process_wizard.py's
            # own module docstring for why. Independent of self._capturing
            # for the same reason _open_gallery_browser is: modal (exec_),
            # so it cannot race a capture in progress.
            if _process_wizard is None:
                self._set_capture_status(
                    "processing wizard unavailable",
                    "process_wizard.py not found beside this file, skipped")
                return
            wiz = _process_wizard.ProcessWizard(provenance.OUT_ROOT, self)
            wiz.exec_()

        def _open_green_extraction(self):
            # GREEN-PLANE EXTRACTION UTILITY (BUILD_LIST Tier 1 item 4): the
            # real work is entirely debayer.py's own --green (zero
            # interpolation) -- this menu action is just pick a source, pick
            # a destination, run it as a subprocess, same pattern as
            # _run_process_cmd above. No new image logic here.
            if self._capturing:
                return
            if _gallery is None:
                self._set_capture_status(
                    "green extraction unavailable",
                    "gallery.py not found beside this file, skipped")
                return
            dlg = _gallery.GalleryPickDialog(provenance.OUT_ROOT, self)
            if dlg.exec_() != QDialog.Accepted:
                return
            paths = dlg.selected_paths()
            if not paths:
                return
            raw_path = paths[0]
            default_out = default_green_output_path(raw_path)
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Save green plane", str(default_out),
                "TIFF (*.tif *.tiff)")
            if not out_path:
                return
            self._run_green_extract_cmd(raw_path, Path(out_path))

        def _run_green_extract_cmd(self, raw_path, out_path):
            if DEBAYER_TOOL is None or not DEBAYER_TOOL.exists():
                self._set_capture_status(
                    "green extraction unavailable",
                    "debayer.py not found beside this file, skipped")
                return
            cmd = [sys.executable, str(DEBAYER_TOOL), str(raw_path),
                  "--green", "-o", str(out_path)]
            self._capturing = True   # reuse the same busy-guard the capture path uses
            self._set_capture_controls(enabled=False, label="Extracting ...")
            self._set_capture_status("extracting green plane ...",
                                     "running: {}".format(" ".join(cmd)))

            def _worker():
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True,
                                       stdin=subprocess.DEVNULL)
                    payload = (r.returncode == 0, str(out_path), r.stdout, r.stderr)
                except Exception as exc:
                    payload = (False, str(out_path), "", str(exc))
                self.green_extract_done_signal.emit(payload)

            threading.Thread(target=_worker, daemon=True).start()

        def _on_green_extract_finished(self, payload):
            # Own handler, not a reuse of _on_process_finished: that one
            # offers to archive a session's raws on success, which makes no
            # sense here -- this action has no session involved at all.
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            ok, out_path, stdout, stderr = payload
            if ok:
                self._set_capture_status(
                    "green plane saved",
                    "wrote {}\n\n{}".format(out_path, stdout[-4000:]))
            else:
                detail = (stderr or stdout)[-4000:]
                self._set_capture_status(
                    "green extraction failed",
                    "green extraction failed:\n\n{}".format(detail))

        def _open_export_results(self):
            # EXPORT MEASUREMENT RESULTS (MeasureWindow extraction, step 3):
            # store-wide, no dependency on any open image -- a File-menu
            # action rather than something that needs a MeasureWindow open.
            # MeasureWindow._on_export_results itself is untouched; this is
            # a second, independent call site for the same underlying
            # export.export_measurements.
            if self._capturing:
                return
            if _export is None or _annotations is None:
                self._set_capture_status(
                    "export unavailable",
                    "export.py or annotations.py not found beside this file, skipped")
                return
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Export measurement results", "measurements.json",
                "JSON (*.json);;All files (*)")
            if not out_path:
                return
            self._run_export_results_cmd(Path(out_path))

        def _run_export_results_cmd(self, out_path):
            self._capturing = True   # reuse the same busy-guard the capture path uses
            self._set_capture_controls(enabled=False, label="Exporting ...")
            self._set_capture_status("exporting measurement results ...",
                                     "writing {}".format(out_path))

            def _worker():
                try:
                    store = _annotations.load_annotations()
                    result = _export.export_measurements(
                        store=store, out_path=str(out_path))
                except Exception as exc:
                    self.export_results_done_signal.emit(
                        (False, str(out_path), 0, None, str(exc)))
                    return
                total_measurements = result["total_measurements"]

                # Orphan evidence, never a gate: the write above already
                # landed regardless of what happens below (see
                # HANDOFF.md's step-3 section). known_hashes is the union
                # of every real on-disk capture's green-plane hash
                # (gallery.py's known_green_hashes) and every cache-only
                # plane a Live Measuring commit may point at
                # (plane_cache.list_cached_hashes) -- either source
                # missing or failing means the known set is PARTIAL, which
                # would report real, committed marks as false-positive
                # orphans just as confidently as an empty set would, so
                # that case is treated as "coverage unavailable," not
                # silently computed anyway.
                capture_scan_ok = False
                cache_scan_ok = False
                capture_hashes = set()
                cache_hashes = set()
                if _gallery is not None:
                    try:
                        capture_hashes = _gallery.known_green_hashes(provenance.OUT_ROOT)
                        capture_scan_ok = True
                    except Exception:
                        capture_scan_ok = False
                if _plane_cache is not None:
                    try:
                        cache_hashes = set(_plane_cache.list_cached_hashes())
                        cache_scan_ok = True
                    except Exception:
                        cache_scan_ok = False

                if capture_scan_ok and cache_scan_ok:
                    known_hashes = capture_hashes | cache_hashes
                    orphan_status = {"orphans": _annotations.find_orphans(store, known_hashes)}
                else:
                    missing = []
                    if not capture_scan_ok:
                        missing.append("capture-root scan (gallery.py)")
                    if not cache_scan_ok:
                        missing.append("plane-cache scan (plane_cache.py)")
                    orphan_status = {
                        "unavailable": "orphan scan unavailable: {}".format(
                            ", ".join(missing))}

                self.export_results_done_signal.emit(
                    (True, str(out_path), total_measurements, orphan_status, None))

            threading.Thread(target=_worker, daemon=True).start()

        def _on_export_results_finished(self, payload):
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            ok, out_path, total_measurements, orphan_status, error = payload
            if not ok:
                self._set_capture_status(
                    "export failed", "export failed:\n\n{}".format(error))
                return
            detail = "wrote {} ({} measurement(s))".format(out_path, total_measurements)
            if "orphans" in orphan_status:
                orphans = orphan_status["orphans"]
                # Evidence, never a gate (same temperament as
                # poly2_flag/sharpness_relative_flag/calibration_staleness):
                # the write already succeeded either way. A clean scan with
                # zero orphans stays silent about it here, same as those
                # other detectors report nothing when there's nothing to
                # flag -- only "unavailable" gets its own explicit note,
                # since that (not a clean scan) is the case that must never
                # be mistaken for "0 orphans."
                if orphans:
                    detail += "\n\n{} orphaned record(s) (no matching capture " \
                              "or cache plane found):\n{}".format(
                                  len(orphans), "\n".join(orphans))
            else:
                detail += "\n\n{}".format(orphan_status["unavailable"])
            self._set_capture_status("measurements exported", detail)

        def _open_publish_package(self):
            # PUBLISH PACKAGE (MeasureWindow extraction, step 3): image-
            # specific, but this menu action has no open MeasureWindow (or
            # its self._plane) to work from -- picks its own image via
            # GalleryPickDialog, the same input-picking step
            # _open_green_extraction already uses. calibration_ref is
            # Option B+ (see HANDOFF.md's step-3 section): the record's
            # OWN stored calibration_ref, not whatever is currently active
            # for some objective -- there is no objective picker here at
            # all, deliberately.
            if self._capturing:
                return
            if _gallery is None:
                self._set_capture_status(
                    "publish unavailable",
                    "gallery.py not found beside this file, skipped")
                return
            if _publish is None or _measure is None or _pixel_hash is None:
                self._set_capture_status(
                    "publish unavailable",
                    "publish.py, measure.py, or pixel_hash.py not found "
                    "beside this file, skipped")
                return
            dlg = _gallery.GalleryPickDialog(provenance.OUT_ROOT, self)
            if dlg.exec_() != QDialog.Accepted:
                return
            paths = dlg.selected_paths()
            if not paths:
                return
            raw_path = paths[0]
            out_dir = QFileDialog.getExistingDirectory(
                self, "Create publication package in directory")
            if not out_dir:
                return
            self._run_publish_package_cmd(raw_path, Path(out_dir))

        def _run_publish_package_cmd(self, raw_path, out_dir):
            self._capturing = True
            self._set_capture_controls(enabled=False, label="Publishing ...")
            self._set_capture_status("publishing package ...",
                                     "publishing {} to {}".format(raw_path, out_dir))

            def _worker():
                try:
                    plane = _measure.load_measurement_plane(raw_path)
                    pixel_sha256 = _pixel_hash.pixel_sha256(plane)
                    calib_ref = (_annotations.stored_calibration_ref(pixel_sha256)
                                if _annotations is not None else None)
                    import tifffile
                    Path(out_dir).mkdir(parents=True, exist_ok=True)
                    green_path = Path(out_dir) / "green_plane.tif"
                    tifffile.imwrite(str(green_path), plane, compression="deflate")
                    manifest = _publish.publish_measurements(
                        green_path, calibration_ref=calib_ref, out_dir=str(out_dir))
                    self.publish_package_done_signal.emit(
                        (True, str(out_dir), manifest, None))
                except Exception as exc:
                    self.publish_package_done_signal.emit(
                        (False, str(out_dir), None, str(exc)))

            threading.Thread(target=_worker, daemon=True).start()

        def _on_publish_package_finished(self, payload):
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            ok, out_dir, manifest, error = payload
            if not ok:
                self._set_capture_status(
                    "publish failed", "publish failed:\n\n{}".format(error))
                return
            no_calib = "objective" not in manifest.get("calibration", {})
            detail = ("wrote package to {}:\n"
                      "  green_plane.tif (pixel_sha256 {}...)\n"
                      "  results.json ({} measurement(s) for this image)\n"
                      "  manifest.json (provenance chain{})").format(
                out_dir,
                manifest["green_plane"]["pixel_sha256"][:16],
                manifest["results"]["total_measurements"],
                "; NO calibration on record -- results are pixel-only"
                if no_calib else "")
            self._set_capture_status("package published", detail)

        def _open_gallery_browser(self):
            # Standalone browse mode (gallery.py): just looking, no commit.
            # Independent of self._capturing -- it only reads the filesystem,
            # and it is modal (exec_) like Process/Archive above, so it
            # cannot race a capture in progress either way.
            if _gallery is None:
                self._set_capture_status(
                    "gallery unavailable",
                    "gallery.py not found beside this file, skipped")
                return
            dlg = _gallery.GalleryBrowseWindow(provenance.OUT_ROOT, self)
            dlg.exec_()

        def _offer_archive_raws(self, session_dir):
            # Bundle-only, not a size reduction (the tar is uncompressed,
            # same total bytes, just one file instead of many); offered
            # separately from processing itself, both because archiving
            # removes the loose originals (worth a deliberate second
            # confirmation, not a side effect of "process now?") and because
            # this can be reached standalone via _open_archive_wizard too,
            # for a session that was already processed before this existed.
            resp = self._flat_question(
                "Archive raw files?",
                "Bundle this session's raw frames into one .tar and remove "
                "the loose originals?\n(tidiness only, does not reduce disk "
                "usage)")
            if resp != QMessageBox.Yes:
                return
            self._capturing = True
            self._set_capture_controls(enabled=False, label="Archiving ...")
            self._set_capture_status("archiving ...")

            def _worker():
                try:
                    result = archive_session_raws(session_dir)
                except Exception as exc:
                    result = exc
                self.archive_done_signal.emit(result)

            threading.Thread(target=_worker, daemon=True).start()

        def _on_archive_finished(self, result):
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            if isinstance(result, Exception):
                self._set_capture_status("archive failed", "archive failed: {}".format(result))
                return
            if result["archived"] == 0:
                self._set_capture_status("nothing to archive",
                                         "no raw files found in this session")
                return
            self._set_capture_status(
                "archived {} raws".format(result["archived"]),
                "archived {} raw file(s) into {} ({:.1f} MB); loose files removed."
                .format(result["archived"], result["tar_path"].name, result["mb"]))

        def _record_correction_status(self, capture_dir, index, correction_status):
            """Write flat_correction/dark_correction onto capture #index's
            OWN entry in session.json -- the named technique that ran or
            was skipped, never folded into a generic "processing complete"
            (CORRECTION_flat_dark_framing.md). Reads session.json fresh and
            writes it straight back rather than going through a live
            Session object: this runs for ANY processed session, including
            one opened through the manual processing wizard, not just
            self._session. Best-effort -- the image itself already
            processed successfully by the time this runs, so a bookkeeping
            failure here is surfaced in the status detail, not raised into
            the completion handler."""
            prov_dir = _provenance_dir_for(Path(capture_dir))
            sj_path = prov_dir / "session.json"
            try:
                data = json.loads(sj_path.read_text())
                cap = next((c for c in data.get("captures", [])
                           if c.get("index") == index), None)
                if cap is None:
                    return "capture #{} not found in {}".format(index, sj_path)
                cap.update(correction_status)
                sj_path.write_text(json.dumps(data, indent=2))
            except Exception as exc:
                return "could not record correction status: {}".format(exc)
            return None

        def _on_process_finished(self, payload):
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            ok, stdout, stderr = payload
            if ok:
                detail = "processing complete\n\n" + stdout[-4000:]
                # hdr_from_session.py's process() prints one line naming the
                # flat/dark correction techniques that ran or were skipped
                # (CORRECTION_flat_dark_framing.md); parse it back out of
                # stdout (the only channel a subprocess result carries) and
                # persist it onto session.json.
                for line in stdout.splitlines():
                    if line.startswith("CORRECTION_STATUS_JSON: "):
                        try:
                            status = json.loads(line[len("CORRECTION_STATUS_JSON: "):])
                        except Exception:
                            break
                        if (self._last_process_session_dir is not None
                                and self._last_process_index is not None):
                            err = self._record_correction_status(
                                self._last_process_session_dir,
                                self._last_process_index, status)
                            if err:
                                detail += "\n\n(correction status not recorded: {})".format(err)
                        break
                self._set_capture_status("processed", detail)
                if self._last_process_session_dir is not None:
                    self._offer_archive_raws(self._last_process_session_dir)
            else:
                detail = (stderr or stdout)[-4000:]
                self._set_capture_status("processing failed",
                                         "processing failed:\n\n" + detail)

        def _on_burst_finished(self, result):
            # On the GUI thread (via burst_done_signal). result is the dict
            # _run_burst_kind returns, or an Exception on failure; either way
            # control comes back and the button re-enables, EXCEPT when the
            # science phase of an HDR sequence just finished: that is not done,
            # it goes straight into the dark setup instead of an idle button.
            self._capturing = False
            if isinstance(result, Exception):
                self._set_capture_controls(enabled=True, label="Capture")
                self._set_capture_status("burst failed", "burst failed: {}".format(result))
                if self._batch_active:
                    # A real failure, not a declined dialog: stop the rest of
                    # the sequence rather than pressing on into more captures
                    # after something already went wrong.
                    self._batch_queue = []
                    self._batch_active = False
                return
            if result.get("kind") == "hdr" and result.get("phase") == "science":
                self._continue_hdr_to_dark(result)
                return
            self._set_capture_controls(enabled=True, label="Capture")
            self._set_capture_status(
                "{} done".format(result["kind"]),
                "{} complete: {}  (session {}, capture #{})".format(
                    result["kind"].capitalize(), result["summary"],
                    self._session.ts, result["index"]))
            if self._batch_active:
                # Mid-sequence: move straight to the next selected kind rather
                # than pausing to offer processing (dark, if selected, may not
                # have run yet, and offering per-step here is exactly the extra
                # manual step this sequence exists to remove).
                self._advance_batch()
                return
            # Auto-process the two burst-produced kinds that can be (flat
            # and dark are calibration-only, never processed). This also
            # fires when HDR's dark phase was cancelled mid-sequence
            # (science-only, dark_levels empty): hdr_from_session.py's own
            # process() already handles an empty dark_levels dict gracefully,
            # just skipping that correction stage.
            if result["kind"] in ("science", "hdr"):
                self._auto_process(result["kind"], result["index"])

        # --- box interaction ------------------------------------------------
        def _disp_rect(self):
            return displayed_rect(self.preview.width(), self.preview.height(),
                                  self._aspect)

        def eventFilter(self, obj, ev):
            if obj is self.preview:
                # LIVE MEASURE PANEL (Part 05): while the panel is open, every
                # preview click is repurposed as a freeze trigger instead of
                # box-drag -- _live_measure_preview_event consumes the event
                # unconditionally (returns True) so _press/_move below never
                # see it. Checked first, before any box-drag branch, so the
                # two features can never both react to the same click.
                if self._live_measure_active:
                    if self._live_measure_preview_event(ev):
                        return True
                # LIVE MEASURING (PLAN_quick_ruler.md): same shape as Part 05's
                # own guard above -- mutually exclusive with it by construction
                # (opening either one closes the other), so at most one of
                # these two branches is ever live at a time.
                if self._live_measuring_active:
                    if self._live_measuring_preview_event(ev):
                        return True
                t = ev.type()
                if t == QEvent.MouseButtonPress:
                    self._press(ev.x(), ev.y())
                elif t == QEvent.MouseMove:
                    self._move(ev.x(), ev.y())
                elif t == QEvent.MouseButtonRelease:
                    self._drag = None
            return super().eventFilter(obj, ev)

        def _press(self, px, py):
            fx, fy = frac_from_point(px, py, self._disp_rect())
            box = self.meter.box
            fixed = opposite_corner(box, fx, fy)
            if fixed is not None:
                self._drag = {"mode": "resize", "fixed": fixed}
            elif box.x0 <= fx <= box.x1 and box.y0 <= fy <= box.y1:
                self._drag = {"mode": "move", "box0": box, "frac0": (fx, fy)}
            else:
                self._drag = None

        def _move(self, px, py):
            if not self._drag:
                return
            fx, fy = frac_from_point(px, py, self._disp_rect())
            if self._drag["mode"] == "move":
                b0 = self._drag["box0"]
                f0 = self._drag["frac0"]
                self.meter.set_box(move_box(b0, fx - f0[0], fy - f0[1]))
            else:
                gx, gy = self._drag["fixed"]
                if abs(fx - gx) >= MIN_FRAC and abs(fy - gy) >= MIN_FRAC:
                    self.meter.set_box(FocusBox.from_corners(gx, gy, fx, fy))

        # --- keys -----------------------------------------------------------
        def keyPressEvent(self, ev):
            if ev.key() == Qt.Key_F:
                self._toggle_aid()
            elif ev.key() == Qt.Key_R:
                self.meter.reset_field()
            elif ev.key() == Qt.Key_Escape and self._armed is not None:
                self._cancel_armed()
            elif ev.key() == Qt.Key_Escape and self._batch_active:
                self._abort_batch()
            elif ev.key() == Qt.Key_Escape and self._live_measuring_pending_points:
                # LIVE MEASURING (PLAN_quick_ruler.md): cancels an in-progress,
                # not-yet-finished click sequence, same convention as the
                # armed-burst/batch-abort branches above.
                self._live_measuring_cancel_pending()
            elif (ev.key() == Qt.Key_Escape and ev.modifiers() & Qt.ControlModifier
                  and self._is_fullscreen):
                # FULL SCREEN MODE: Ctrl+Escape exits, not plain Escape --
                # that key already does real work above (cancel an armed
                # burst, abort a batch sequence) and shouldn't be overloaded
                # with a third meaning. Being a distinct key combination, this
                # never collides with either branch above; no ordering needed.
                self._toggle_fullscreen()
            elif ev.key() == Qt.Key_F11:
                self._toggle_fullscreen()
            elif ev.key() == Qt.Key_P and self._is_fullscreen:
                self._toggle_floating_panel()
            elif ev.key() == Qt.Key_Up and hasattr(self.camera, "focus_position"):
                self.camera.focus_position += 0.25
            elif ev.key() == Qt.Key_Down and hasattr(self.camera, "focus_position"):
                self.camera.focus_position -= 0.25
            else:
                super().keyPressEvent(ev)

        def closeEvent(self, ev):
            # Persist whatever width the panel was dragged to, so next launch
            # restores it instead of resetting to the hardcoded default. Wrapped
            # in try/except like every other save_pref call: a failed write here
            # should never block the window from actually closing.
            try:
                # FULL SCREEN MODE: only meaningful while self._panel is
                # actually a splitter child -- mid-float (full screen with
                # the floating panel toggle in play) it isn't, and
                # .sizes() would no longer describe it at all. Skip the
                # save rather than persist a stale/wrong width.
                if self._splitter.indexOf(self._panel) != -1:
                    sizes = self._splitter.sizes()
                    if len(sizes) >= 2:
                        save_pref("panel_width", int(sizes[1]))
            except Exception:
                pass
            self.timer.stop()
            # --- RECORD BUTTON (separable): finish an in-flight recording
            # before anything else tears down. The worker thread holding the
            # recording open is the thread ffmpeg's life is tied to (see
            # _toggle_recording's docstring), so quitting while it is parked
            # would SIGKILL ffmpeg mid-file -- the original no-file bug, just
            # reached by closing the window instead. Signal it, then WAIT for
            # it: stop_encoder has to actually run and ffmpeg has to write its
            # trailer before this process goes away.
            try:
                if self.camera.is_recording() and self._record_stop_event is not None:
                    self._record_stop_event.set()
                    if self._record_thread is not None:
                        # Bounded: a hung encoder should delay the close, not
                        # wedge the window shut permanently. A timeout here
                        # means the file may be incomplete, which is strictly
                        # better than not closing at all.
                        self._record_thread.join(timeout=10.0)
            except Exception:
                pass
            # --- end record button (close handling) -----------------------
            try:
                self.camera.stop()
            except Exception:
                pass
            super().closeEvent(ev)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Zynergy capture GUI (fake or Pi camera).")
    ap.add_argument("--camera", action="store_true",
                    help="use the Pi camera (Picamera2Camera); default is the fake")
    # Display-processing flags, forwarded to hdr_from_session.py on a
    # process offer via build_display_flags.
    ap.add_argument("--wl", default=65520, help="sensor white level for processing")
    ap.add_argument("--lw", default=2.2, help="Reinhard white point for the HDR path")
    ap.add_argument("--gains", nargs=2, metavar=("RED", "BLUE"), default=None,
                    help="ColourGains white balance for processing")
    ap.add_argument("--ca", default=None, metavar="CALIB_JSON")
    ap.add_argument("--sharpen", default=None, metavar="RADIUS")
    ap.add_argument("--shadow-deepen", action="store_true")
    ap.add_argument("--archive-raws", action="store_true",
                    help="tar+remove raws after a process offer (no prompt)")
    ap.add_argument("--no-onboarding", action="store_true",
                    help="suppress the one-time 'calibrate now?' prompt even on "
                         "a display-capable launch (see should_show_onboarding_gate)")
    a = ap.parse_args(argv)
    if not _HAVE_QT:
        sys.exit("PyQt5 not available. Use --render-check for the headless self-check "
                 "test, or install python3-pyqt5 for the GUI.")
    app = QApplication(sys.argv)
    theme_qss = resolve_theme_qss_path(load_pref("theme", None))
    if theme_qss is not None:
        app.setStyleSheet(load_theme_stylesheet(theme_qss))

    # Folder-layout prefs (Part 03: provenance relocation) -- applied to
    # provenance.py's own root globals BEFORE any Session gets constructed,
    # since Session/new_session_dirs/new_zstack_root_dirs all read these as
    # their defaults. Module-attribute assignment, not a `global` rebind
    # (same reasoning as the PROFILE_PATH note elsewhere in this file):
    # qt_shell.py does not own these names, it only ever configures them.
    provenance.PROVENANCE_ROOT = Path(
        load_pref("provenance_folder", str(Path.home() / "provenance")))
    provenance.OUT_ROOT = Path(load_pref("capture_folder", str(Path.home() / "captures")))
    provenance.FLAT_ROOT = Path(load_pref("flat_library_folder", str(Path.home() / "flat")))

    # Green-plane cache auto-clean (Part 04): runs once here, at launch, not
    # on a recurring in-app timer -- this app has no other background
    # housekeeping timer to hang it off, sessions are typically short-lived
    # (started and closed, not left running for days), and startup is
    # already where every other "apply a persisted setting" step in main()
    # happens. A settled placement call, not a silently guessed one --
    # revisit if a long-running session ever turns out to need it mid-run.
    if _plane_cache is not None and load_pref("cache_auto_clean_enabled", False):
        _plane_cache.clean_cache(older_than_days=load_pref("cache_auto_clean_days", 30))

    if a.camera:
        try:
            from .camera_backend import Picamera2Camera
        except ImportError:
            from camera_backend import Picamera2Camera
        camera = Picamera2Camera(
            **capture_resolution_kwargs(load_pref("capture_resolution", None)))
    else:
        camera = FakeCamera()

    # One application, one window, one layout (Preferences-dialog plan set,
    # PLAN_00_context_and_supersession.md) -- no mode, no launch branch, no
    # second window class. Casual Mode used to pick between window classes
    # here; that branch is gone.
    display_flags = build_display_flags(a)
    win = FocusPreviewWindow(camera, FocusMeter(), display_flags=display_flags,
                              no_onboarding=a.no_onboarding)
    win.setWindowTitle("Zynergy capture GUI" + ("" if a.camera else "  (fake)"))
    win.resize(1550, 760)          # fallback size if the window manager ever
                                    # ignores the maximize request below
    win.showMaximized()
    app.exec_()


# ---------------------------------------------------------------------------
# Headless self-check for the pure parts (no PyQt, no camera)
# ---------------------------------------------------------------------------
def render_check():
    # Declared up front (Python requires `global` to precede every use of
    # a name in the function, including reads) -- rebound later, briefly,
    # by the Export absent-vs-empty coverage and forced-failure checks
    # (temporarily setting _gallery/_plane_cache/_export, always restored
    # in their own finally blocks).
    global _gallery, _plane_cache, _export

    # Never touch the REAL ~/imx/profile.json for the whole duration of this
    # function, no matter how many FocusPreviewWindow instances get built
    # below or what triggers save_profile's own probe-and-save fallback --
    # real hardware exposure/gain/WB data got silently overwritten with
    # fake FakeCamera-probed values TWICE during this session's own testing
    # despite save_profile() itself already being made atomic (see
    # CHANGELOG.md), and a second occurrence could not be pinned to a
    # specific reproducible trigger. Not wrapped in try/finally: a failed
    # assertion here ends the process immediately anyway (this is a one-shot
    # script, not a long-running service), so there is no real window where
    # a restore would matter and one didn't happen. Module-attribute
    # assignment on provenance, not a `global` rebind here -- qt_shell.py no
    # longer owns this name, and a `global` rebind would only ever shadow a
    # local, never reach the real provenance.PROFILE_PATH every consumer
    # (this file, gallery.py, wizard_pages.py) actually reads.
    _orig_profile_path_for_render_check = provenance.PROFILE_PATH
    provenance.PROFILE_PATH = Path("/tmp/zynergy_render_check_profile.json")

    # Same one-shot reasoning as PROFILE_PATH just above, extended to the
    # three Part 03 folder-layout globals: every provenance.Session(...)
    # built anywhere below this point (and every list_sessions/
    # capture_correction_status call, via _provenance_dir_for) reads these
    # as its defaults, so they must point at disposable temp dirs for the
    # whole function, never the real ~/provenance, ~/captures, or ~/flat.
    provenance.PROVENANCE_ROOT = Path("/tmp/zynergy_render_check_provenance_root")
    provenance.OUT_ROOT = Path("/tmp/zynergy_render_check_capture_root")
    provenance.FLAT_ROOT = Path("/tmp/zynergy_render_check_flat_root")
    for _r in (provenance.PROVENANCE_ROOT, provenance.OUT_ROOT, provenance.FLAT_ROOT):
        if _r.exists():
            shutil.rmtree(_r)

    box = FocusBox.centered(0.5, 0.4)
    bar = BarState(fill=0.5, current=0.02, hi=0.03, lo=0.0, at_peak=False, settled=True)
    st = FocusState(valid=True, source="green", raw=0.02, smoothed=0.02, bar=bar)
    ov = render_overlay(LORES_RES, box, st)
    assert ov.shape == (LORES_RES[1], LORES_RES[0], 4), "overlay shape"
    r0, r1, c0, c1 = box.pixel_rect((LORES_RES[1], LORES_RES[0]))
    mid = (c0 + c1) // 2
    assert ov[r0, mid, 3] > 0 and ov[r1 - 1, mid, 3] > 0, "box edges not drawn"

    def filled(fill):
        b = BarState(fill=fill, current=0, hi=1, lo=0, at_peak=False, settled=True)
        s = FocusState(valid=True, source="green", raw=0, smoothed=0, bar=b)
        o = render_overlay(LORES_RES, box, s)
        br0, br1, bc0, bc1 = box.pixel_rect((LORES_RES[1], LORES_RES[0]))
        band = o[br0:br1, max(bc1 - 10, 0):bc1, :]     # the bar column
        return int((band[..., 3] == 255).sum())

    assert filled(0.9) > filled(0.1), "bar fill not monotonic"

    dr = displayed_rect(1000, 600, 4 / 3)          # 4:3 image in a wider widget
    fx, fy = frac_from_point(dr[0] + dr[2] // 2, dr[1] + dr[3] // 2, dr)
    assert abs(fx - 0.5) < 0.02 and abs(fy - 0.5) < 0.02, "letterbox centre mapping"

    moved = move_box(box, 0.3, 0.3)
    assert moved.same_size_as(box), "move changed size"
    print("render-check PASS: overlay shape, box edges, bar fill monotonic, "
          "letterbox mapping, move keeps size")

    # --- XY ruler ---------------------------------------------------------
    # nice_tick_step_um: a 1000um field targeting ~10 ticks should land on a
    # round number close to 100, never on 1000/10=100 exactly by coincidence
    # alone -- check a few fields that do NOT divide evenly too.
    assert nice_tick_step_um(1000.0, target_ticks=10) == 100
    assert nice_tick_step_um(37.0, target_ticks=10) in _NICE_TICK_STEPS_UM
    assert nice_tick_step_um(0.0) is None, "a degenerate field of view should not raise"
    assert nice_tick_step_um(None) is None

    # ruler_ticks: a clean 1000 x 500 um field at step=100 should give 9
    # ticks on X (100..900, the 1000 mark itself excluded since frac >= 1.0
    # is dropped) and 4 on Y (100..400), with every 5th flagged major, and X
    # and Y must share the SAME step (both derived from the width).
    x_ticks, y_ticks = ruler_ticks(1000.0, 500.0, target_ticks=10, major_every=5)
    assert len(x_ticks) == 9, "expected ticks at 100..900um, got {}".format(x_ticks)
    assert len(y_ticks) == 4, "expected ticks at 100..400um, got {}".format(y_ticks)
    assert abs(x_ticks[0][0] - 0.1) < 1e-9, "first X tick should sit at 10% across"
    majors = [i for i, (_, major) in enumerate(x_ticks, start=1) if major]
    assert majors == [5], "only the 5th minor tick should be flagged major"
    empty_x, empty_y = ruler_ticks(0.0, 500.0)
    assert empty_x == () and empty_y == (), "a zero-width field should give no ticks at all"
    print("ruler_ticks check PASS: round step selection, correct tick count and "
          "spacing, major every 5th, degenerate field gives no ticks")

    # render_overlay with ruler_ticks composites both without ruler ticks
    # clobbering the box, or vice versa (drawn into the same buffer, ruler
    # first per the docstring, box on top).
    ov_r = render_overlay(LORES_RES, box, st, ruler_ticks=(x_ticks, y_ticks))
    assert ov_r[r0, mid, 3] > 0, "box top edge missing once ruler ticks were added"
    top_row_alpha = ov_r[0, :, 3]
    assert top_row_alpha.sum() > 0, "no ruler tick pixels drawn along the top edge"
    ov_plain = render_overlay(LORES_RES, box, st)      # no ruler_ticks arg at all
    assert ov_plain[0, :, 3].sum() == 0, "a plain render_overlay call must draw no ruler"
    print("render_overlay ruler compositing check PASS: ruler ticks and the "
          "focus box coexist in one buffer; omitting ruler_ticks draws no ruler")

    # overlay_signature must change when only the ruler config changes, even
    # though the box/state are identical -- otherwise a ruler-only change
    # would get silently skipped as "nothing to redraw".
    sig_no_ruler = overlay_signature(box, st, (LORES_RES[1], LORES_RES[0], 4), ruler_key=None)
    sig_with_ruler = overlay_signature(box, st, (LORES_RES[1], LORES_RES[0], 4),
                                       ruler_key=("40x",))
    assert sig_no_ruler != sig_with_ruler, "ruler_key must affect the signature"
    print("overlay_signature ruler-sensitivity check PASS")

    # --- onboarding gate (calibration integration) --------------------------
    # PLAN_onboarding_gate_headless.md (a user-provided intent doc, not
    # checked into the repo): the gate must never fire when nothing can
    # dismiss it. Full 8-combination truth table for the predicate --
    # True only when genuinely unshown AND uncalibrated AND interactive.
    for _og_shown, _og_calib, _og_interactive, _og_expected in [
        (False, False, True,  True),
        (False, False, False, False),
        (False, True,  True,  False),
        (False, True,  False, False),
        (True,  False, True,  False),
        (True,  False, False, False),
        (True,  True,  True,  False),
        (True,  True,  False, False),
    ]:
        _og_got = should_show_onboarding_gate(_og_shown, _og_calib, _og_interactive)
        assert _og_got is _og_expected, (
            "should_show_onboarding_gate(already_shown={}, any_calibration_exists={}, "
            "interactive={}) must be {}, got {}".format(
                _og_shown, _og_calib, _og_interactive, _og_expected, _og_got))
    print("should_show_onboarding_gate check PASS: one-time nudge only when "
          "genuinely unshown, uncalibrated, AND interactive -- never a recurring "
          "nag, and never a prompt nothing can dismiss")

    # _onboarding_session_is_interactive: errs toward True by design -- only
    # offscreen/minimal and the explicit opt-out read as non-interactive; an
    # unrecognized platform name is left alone, never guessed at, since a
    # wrongly-suppressed prompt (a user who silently never learns to
    # calibrate) is worse than a wrongly-shown one (costs nothing -- the
    # Calibrate menu action is always there). Checked BEFORE this function
    # constructs its own QApplication below, on purpose: this is the one
    # point in render_check() where "no live QApplication instance" is
    # actually true and testable for real, not simulated.
    if _HAVE_QT and QApplication.instance() is None:
        assert _onboarding_session_is_interactive(no_onboarding_flag=False) is False, \
            "no live QApplication instance must read as non-interactive"
        print("_onboarding_session_is_interactive check PASS (no QApplication yet): "
              "correctly non-interactive before any QApplication exists")
    qtapp_og = QApplication.instance() or QApplication([])
    _orig_qt_qpa_og = os.environ.get("QT_QPA_PLATFORM")
    try:
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        assert _onboarding_session_is_interactive(no_onboarding_flag=False) is True, \
            "a real display-capable platform with no opt-out must read as interactive"
        assert _onboarding_session_is_interactive(no_onboarding_flag=True) is False, \
            "the explicit --no-onboarding opt-out must always suppress, regardless of platform"
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        assert _onboarding_session_is_interactive(no_onboarding_flag=False) is False, \
            "offscreen must never be treated as interactive"
        os.environ["QT_QPA_PLATFORM"] = "offscreen:some=option"
        assert _onboarding_session_is_interactive(no_onboarding_flag=False) is False, \
            "a platform value with backend options after a colon must still " \
            "match on the platform name alone"
        os.environ["QT_QPA_PLATFORM"] = "minimal"
        assert _onboarding_session_is_interactive(no_onboarding_flag=False) is False, \
            "minimal must never be treated as interactive"
        os.environ["QT_QPA_PLATFORM"] = "some-unrecognized-platform"
        assert _onboarding_session_is_interactive(no_onboarding_flag=False) is True, \
            "an unrecognized platform must default to interactive, never be " \
            "guessed non-interactive"
    finally:
        if _orig_qt_qpa_og is None:
            os.environ.pop("QT_QPA_PLATFORM", None)
        else:
            os.environ["QT_QPA_PLATFORM"] = _orig_qt_qpa_og
    print("_onboarding_session_is_interactive check PASS: errs toward interactive "
          "-- only offscreen/minimal (matched on the platform name alone, ignoring "
          "any ':'-separated backend option) and the explicit --no-onboarding flag "
          "suppress; an unrecognized platform is left alone, never guessed "
          "non-interactive")

    if _calibrate is None:
        print("Onboarding gate non-interactive-suppression check SKIPPED: "
              "calibrate.py not importable here")
    else:
        # This is the real regression coverage for the freeze-fix session's
        # own side finding: a genuinely fresh environment (no calibration on
        # record, prompt never shown) used to hang --render-check forever.
        # Redirect both PREFS_PATH and CALIBRATION_PATH so this can run
        # against a real, isolated "nothing on record" state without ever
        # touching ~/.zynergy/gui_prefs.json or ~/.zynergy/calibration.json.
        # `global PREFS_PATH` declared ONCE here (Python disallows a second
        # `global X` anywhere later in the same function once X has been
        # used -- moved the Preferences dialog check's own redundant
        # declaration out for exactly that reason; both sections reference
        # the same module attribute regardless of which block declares it).
        global PREFS_PATH
        _orig_prefs_path_og = PREFS_PATH
        PREFS_PATH = Path("/tmp/zynergy_render_check_onboarding_prefs.json")
        _orig_calib_path_og = _calibrate.CALIBRATION_PATH
        _calibrate.CALIBRATION_PATH = Path(
            "/tmp/zynergy_render_check_onboarding_calibration.json")
        try:
            # Case: suppression (via --no-onboarding here, the same code path
            # a genuinely non-interactive platform takes) must construct no
            # dialog AND must leave the one-time-prompt pref completely
            # unwritten -- "nobody's here" is not "asked and answered." This
            # is the assertion that actually matters: the suppression path
            # not writing the pref is invisible if it regresses (nothing
            # fails loudly, a user just quietly loses their one-time prompt),
            # so this checks the pref file's real key set, not merely that
            # no dialog appeared.
            PREFS_PATH.unlink(missing_ok=True)
            _calibrate.CALIBRATION_PATH.unlink(missing_ok=True)
            og_cam1 = FakeCamera(async_delay_s=0.0)
            og_win1 = FocusPreviewWindow(og_cam1, FocusMeter(), no_onboarding=True)
            try:
                og_calls1 = []
                real_question1 = QMessageBox.question
                def _stub_question1(*a, **kw):
                    og_calls1.append(1)
                    return QMessageBox.No
                QMessageBox.question = _stub_question1
                try:
                    og_win1._maybe_show_onboarding_gate()
                finally:
                    QMessageBox.question = real_question1
                assert og_calls1 == [], \
                    "a suppressed (non-interactive) gate must never construct " \
                    "the dialog at all"
                assert "onboarding_calibration_prompt_shown" not in load_prefs(), \
                    "suppression must NOT write the one-time-prompt pref -- " \
                    "writing it here would silently burn the user's real " \
                    "prompt for their eventual first interactive launch"
            finally:
                og_cam1.stop()
            print("Onboarding gate suppression check PASS: a suppressed gate "
                  "shows no dialog and, critically, leaves the one-time-prompt "
                  "pref file completely unwritten")

            # Case: the interactive path must still write the pref BEFORE the
            # dialog -- a regression guard on that exact ordering, since a
            # crash/force-quit mid-dialog must not re-prompt on every later
            # launch. Forced interactive here (xcb) purely so this path runs
            # at all under this function's own offscreen process; the real
            # blocking QMessageBox.question is stubbed out (never actually
            # shown) so this doesn't hang the very check that proves it won't.
            PREFS_PATH.unlink(missing_ok=True)
            _calibrate.CALIBRATION_PATH.unlink(missing_ok=True)
            og_cam2 = FakeCamera(async_delay_s=0.0)
            og_win2 = FocusPreviewWindow(og_cam2, FocusMeter(), no_onboarding=False)
            _orig_qt_qpa_og2 = os.environ.get("QT_QPA_PLATFORM")
            try:
                os.environ["QT_QPA_PLATFORM"] = "xcb"
                og_pref_state_at_dialog = []
                real_question2 = QMessageBox.question
                def _stub_question2(*a, **kw):
                    og_pref_state_at_dialog.append(
                        bool(load_pref("onboarding_calibration_prompt_shown", False)))
                    return QMessageBox.No
                QMessageBox.question = _stub_question2
                try:
                    og_win2._maybe_show_onboarding_gate()
                finally:
                    QMessageBox.question = real_question2
            finally:
                if _orig_qt_qpa_og2 is None:
                    os.environ.pop("QT_QPA_PLATFORM", None)
                else:
                    os.environ["QT_QPA_PLATFORM"] = _orig_qt_qpa_og2
                og_cam2.stop()
            assert og_pref_state_at_dialog == [True], \
                "the interactive path must call save_pref BEFORE the dialog " \
                "runs -- the pref must already read True by the time the " \
                "dialog function is invoked"
            print("Onboarding gate interactive-ordering check PASS: save_pref "
                  "fires before the dialog on a real interactive path, "
                  "preserving crash-mid-dialog safety")

            # Case: --no-onboarding suppresses even an otherwise-interactive
            # (display-capable) session -- the explicit opt-out, not the
            # platform auto-detection, is what's under test here.
            PREFS_PATH.unlink(missing_ok=True)
            _calibrate.CALIBRATION_PATH.unlink(missing_ok=True)
            og_cam3 = FakeCamera(async_delay_s=0.0)
            og_win3 = FocusPreviewWindow(og_cam3, FocusMeter(), no_onboarding=True)
            _orig_qt_qpa_og3 = os.environ.get("QT_QPA_PLATFORM")
            try:
                os.environ["QT_QPA_PLATFORM"] = "xcb"   # otherwise genuinely interactive
                og_calls3 = []
                real_question3 = QMessageBox.question
                def _stub_question3(*a, **kw):
                    og_calls3.append(1)
                    return QMessageBox.No
                QMessageBox.question = _stub_question3
                try:
                    og_win3._maybe_show_onboarding_gate()
                finally:
                    QMessageBox.question = real_question3
            finally:
                if _orig_qt_qpa_og3 is None:
                    os.environ.pop("QT_QPA_PLATFORM", None)
                else:
                    os.environ["QT_QPA_PLATFORM"] = _orig_qt_qpa_og3
                og_cam3.stop()
            assert og_calls3 == [], \
                "--no-onboarding must suppress the gate even on an otherwise " \
                "display-capable platform"
            assert "onboarding_calibration_prompt_shown" not in load_prefs(), \
                "the --no-onboarding path must not write the pref either"
            print("Onboarding gate --no-onboarding check PASS: the explicit "
                  "opt-out suppresses the prompt even when the platform itself "
                  "would otherwise read as interactive")
        finally:
            PREFS_PATH = _orig_prefs_path_og
            _calibrate.CALIBRATION_PATH = _orig_calib_path_og
    # --- end onboarding gate (calibration integration) -----------------------

    # Shutter stop table: standard photographic full stops within the sensor's
    # range, endpoints reachable, monotonic, and every position round-trips to
    # the exact stop it names (no in-between guesses). Pure, no camera, no Qt.
    slo, shi = 60, 50_000
    stops = build_shutter_stops(slo, shi)
    assert len(stops) >= 8, "expected several full stops between 60us and 50ms"
    assert stops == sorted(stops), "stops not ascending"
    assert stops[0] <= slo * 1.03, "lowest stop does not reach near the sensor floor"
    assert stops[-1] == float(shi), "highest stop should be the sensor ceiling anchor"
    for i, v in enumerate(stops):
        assert pos_to_shutter_stop(i, stops) == v, "position did not round-trip to its stop"
        assert shutter_stop_pos(v, stops) == i, "exact stop value did not map back to its position"
    assert shutter_stop_pos(200, stops) == shutter_stop_pos(210, stops), \
        "two close arbitrary readings should land on the same nearest stop"
    assert fmt_shutter_fraction(500) == "1/2000s", "fraction format off for 500us"
    assert fmt_shutter_fraction(1_500_000) == "1.5s", "seconds format off above 1s"

    # Long-exposure table: must reach the 3.0s cap, include whole-second stops the
    # fast table never sees, and a value that exists in both tables (e.g. 1s)
    # round-trips in each rather than only being valid in one.
    long_stops = build_shutter_stops(slo, LONG_EXPOSURE_MAX_US)
    assert long_stops[-1] == float(LONG_EXPOSURE_MAX_US), "long table should reach the 3.0s cap"
    assert 1_000_000.0 in long_stops, "long table should include the 1s stop"
    assert 2_000_000.0 in long_stops, "long table should include the 2s stop"
    one_s_pos = shutter_stop_pos(1_000_000, long_stops)
    assert pos_to_shutter_stop(one_s_pos, long_stops) == 1_000_000.0, \
        "1s did not round-trip in the long table"

    glo, ghi = 1.0, 16.0
    assert abs(pos_to_linear(linear_to_pos(4.0, glo, ghi), glo, ghi) - 4.0) < 0.05, \
        "gain round-trip off"
    # a sensor that reports a 0 (or negative) shutter minimum must not blow up
    # the stop table (this crashed an earlier log-scale version via math.log(0))
    zero_min_stops = build_shutter_stops(0, shi)
    assert zero_min_stops[0] > 0, "zero-min shutter table produced a non-positive stop"
    print("slider-map check PASS: shutter stop table + fraction format, gain linear, "
          "long-exposure table to 3.0s, zero-min safe")

    # capture_resolution_kwargs (BUILD_LIST Tier 1 item 5): no pref set means
    # no kwarg at all (the camera's own FULL_RES default applies, unchanged
    # behavior), a set pref becomes an explicit full_res tuple. video_
    # resolution_kwargs no longer exists (ROADMAP item 1): it used to feed
    # this same persisted pref into preview_res, which paired against the
    # hardcoded 4:3 LORES_RES and broke lores whenever the pref was a
    # non-4:3 mode. Removed rather than fixed in place, since nothing reads
    # self._video_res yet for that preference to honestly drive.
    assert capture_resolution_kwargs(None) == {}, \
        "no preference should mean no kwarg, not some hardcoded default"
    assert capture_resolution_kwargs([1920, 1080]) == {"full_res": (1920, 1080)}
    assert capture_resolution_kwargs((2048, 1080)) == {"full_res": (2048, 1080)}, \
        "must accept a tuple too, not just the list JSON round-trips through"
    print("capture_resolution_kwargs check PASS: no preference means no kwarg "
          "(camera's own default applies), a set preference becomes an "
          "explicit full_res tuple, both list and tuple input accepted")

    # format_lores_config_summary: pure formatter for camera_backend.py's
    # lores_config_at_failure, tested standalone (no FocusPreviewWindow
    # needed) before the fuller _readout round trip below proves it's
    # actually wired in.
    assert format_lores_config_summary(None) == "active config not yet captured"
    assert format_lores_config_summary({"error": "camera busy"}) == \
        "camera_configuration() itself failed: camera busy"
    assert format_lores_config_summary({"streams_present": []}) == \
        "streams: none (lores MISSING)"
    assert format_lores_config_summary({
        "streams_present": ["lores", "main"],
        "main": {"size": (1332, 990), "format": "XBGR8888"},
        "lores": {"size": (320, 240), "format": "RGB888"},
    }) == ("streams: lores=320x240@RGB888, main=1332x990@XBGR8888 "
          "(lores PRESENT)")
    print("format_lores_config_summary check PASS: no capture yet, "
          "camera_configuration() itself failing, no streams at all, and a "
          "real main+lores config all render distinctly, lores presence "
          "stated explicitly rather than left implicit in the stream list")

    # Themes (BUILD_LIST Tier 1 item 3): discover_themes scans a real folder
    # tree rather than trusting a hardcoded list, load_theme_stylesheet
    # substitutes {{ASSETS}} for the theme's own absolute assets/ path, and
    # resolve_theme_qss_path degrades a stale/deleted preference to None
    # (stock look) instead of crashing main().
    themes_tmp = Path("/tmp/zynergy_render_check_themes")
    if themes_tmp.exists():
        shutil.rmtree(themes_tmp)
    (themes_tmp / "dark" / "assets").mkdir(parents=True)
    (themes_tmp / "dark" / "style.qss").write_text(
        "#side_panel { background-image: url({{ASSETS}}/bg.png); }")
    (themes_tmp / "no_qss_here").mkdir()   # a folder with no style.qss: not a theme
    (themes_tmp / "not_a_dir.txt").write_text("ignored")   # not a directory: not a theme

    found = discover_themes(themes_tmp)
    assert [name for name, _ in found] == ["dark"], \
        "only a subdirectory that actually contains style.qss counts as a theme"
    assert discover_themes(themes_tmp / "nonexistent") == [], \
        "a themes root that doesn't exist yet (no themes designed) must be " \
        "empty, not an error"

    dark_name, dark_qss = found[0]
    stylesheet = load_theme_stylesheet(dark_qss)
    expected_assets = str(themes_tmp / "dark" / "assets")
    assert expected_assets in stylesheet and "{{ASSETS}}" not in stylesheet, \
        "the {{ASSETS}} placeholder must be substituted for this theme's " \
        "own absolute assets/ path, not left literal or pointed at the wrong theme"

    assert resolve_theme_qss_path(None, themes_tmp) is None, \
        "no preference set should resolve to None (stock look)"
    assert resolve_theme_qss_path("dark", themes_tmp) == dark_qss
    assert resolve_theme_qss_path("a_theme_that_was_deleted", themes_tmp) is None, \
        "a stale preference naming a theme that no longer exists must " \
        "degrade to None (stock look), never raise into main()"
    shutil.rmtree(themes_tmp, ignore_errors=True)
    print("themes check PASS: discover_themes finds only real style.qss-bearing "
          "folders (ignoring files and folders missing a style.qss), "
          "{{ASSETS}} resolves to the correct theme's own absolute assets "
          "path, a stale/missing preference degrades to the stock look "
          "rather than raising")

    # Capture-enforces-lock, at the CameraBackend seam: _enforce_exposure_lock reads
    # the live metered values, then calls apply_exposure_lock with that exact
    # snapshot. This checks the seam holds up that contract; the Qt half (the
    # sliders/checkboxes _enforce_exposure_lock also updates) needs PyQt5 to run
    # and is not exercised here.
    lockcam = FakeCamera()
    lockcam.set_exposure(auto_exposure=True, auto_white_balance=True)
    metered = lockcam.read_exposure()
    assert metered["auto_exposure"] and metered["auto_white_balance"], \
        "expected auto on before enforcing a lock"
    lockcam.apply_exposure_lock({k: metered[k] for k in
        ("shutter_us", "analogue_gain", "awb_red_gain", "awb_blue_gain")})
    locked = lockcam.read_exposure()
    assert not locked["auto_exposure"] and not locked["auto_white_balance"], \
        "lock did not drop auto exposure/white balance"
    assert (locked["shutter_us"], locked["analogue_gain"]) == \
           (metered["shutter_us"], metered["analogue_gain"]), \
        "locked values drifted from the metered snapshot taken just before the lock"
    print("capture-lock check PASS: metered snapshot -> apply_exposure_lock -> auto off, values held")

    # record_capture/record_burst/record_hdr mechanics (sidecar writing,
    # session-record shape, HDR level-dict stripping) are now proven by
    # provenance.py's own --render-check, not re-proven here -- see
    # HANDOFF.md's provenance.py extraction note. What's left below reuses
    # provenance.Session/record_burst purely as infrastructure to build
    # fixture sessions for the processing-wizard helpers that DO stay in
    # this file (list_sessions/load_session_json/processable_captures/
    # capture_correction_status/archive_session_raws).

    # Processing wizard pure helpers: list_sessions/load_session_json/
    # processable_captures/capture_correction_status, exercised against
    # a flat+science session built here as fixture data, plus a second
    # session to confirm list_sessions finds multiple and sorts
    # most-recent-first.
    # The real (already-patched, temp) provenance.OUT_ROOT itself, not a
    # subfolder of it or an independent path: Session()'s implicit
    # provenance-dir pairing (new_session_dirs(root) with no explicit
    # provenance_dir override) only mirrors root's own relative position
    # when root truly IS the global OUT_ROOT -- exactly how _ensure_session
    # constructs the real capture session, so this matches it precisely.
    # A root that were some other subfolder would mint a provenance dir at
    # the bare PROVENANCE_ROOT with no matching subfolder, which
    # _provenance_dir_for could never resolve back correctly (this is why
    # the z-stack path builds its own provenance_dir explicitly instead).
    wiz_root = provenance.OUT_ROOT
    if wiz_root.exists():
        shutil.rmtree(wiz_root)
    s_old = provenance.Session(wiz_root, {}, [])
    wcam = FakeCamera()
    # Flat is a replaced-outright standing library (provenance.FLAT_ROOT,
    # Part 03), never session-scoped -- written straight to the library, not
    # into s_old.dir, mirroring how _run_burst_kind now does it.
    old_flat = wcam.capture_burst(provenance.FLAT_ROOT, "flat_", 2, shutter_us=5000)
    provenance.record_burst(s_old, "flat", "flat_", old_flat)
    old_sci = wcam.capture_burst(s_old.dir, "science_", 2)
    provenance.record_burst(s_old, "science", "science_", old_sci)
    import time as _time
    _time.sleep(1.05)   # session dirs are timestamp-named; force a distinct, later name
    s_new = provenance.Session(wiz_root, {}, [])
    new_sci = wcam.capture_burst(s_new.dir, "science_", 2)
    provenance.record_burst(s_new, "science", "science_", new_sci)

    found = list_sessions(wiz_root)
    assert len(found) == 2, "list_sessions should find both session dirs"
    assert found[0] == s_new.dir, "list_sessions should list most-recent-first"

    sj_old = load_session_json(s_old.dir)
    proc_old = processable_captures(sj_old)
    assert len(proc_old) == 1 and proc_old[0]["kind"] == "science", \
        "processable_captures should list science but exclude flat"

    status_old = capture_correction_status(s_old.dir, sj_old, proc_old[0])
    assert status_old["flat_frames"] == 2, \
        "expected 2 flat frames found in the standing library"
    assert status_old["dark_frames"] == 0, "no standalone dark shot yet"
    assert status_old["own_frames"] == 2, "expected 2 own science frames"

    # Flat is shared across every session now (Part 03: one standing
    # library, not scanned out of any particular session's own captures
    # list) -- s_new never shot its own flat, but must still see s_old's.
    sj_new = load_session_json(s_new.dir)
    proc_new = processable_captures(sj_new)
    status_new = capture_correction_status(s_new.dir, sj_new, proc_new[0])
    assert status_new["flat_frames"] == 2, \
        "flat is a standing library shared across every session -- a " \
        "session with no flat capture of its own must still see it"
    print("processing wizard helpers check PASS: sessions listed most-recent-first, "
          "processable captures filtered correctly, flat status accurate and "
          "correctly shared across sessions via the standing library")

    # Standalone dark, nested under dir/"dark" (Part 03) -- must be found by
    # capture_correction_status and paired with the science capture, exactly
    # like an HDR capture's own per-level dark_levels already are.
    dark_dir = s_old.dir / "dark"
    dark_dir.mkdir()
    dark_result = wcam.capture_burst(dark_dir, "dark_", 2)
    provenance.record_burst(s_old, "dark", "dark_", dark_result)
    sj_old2 = load_session_json(s_old.dir)
    sci_cap = next(c for c in processable_captures(sj_old2) if c["kind"] == "science")
    status_with_dark = capture_correction_status(s_old.dir, sj_old2, sci_cap)
    assert status_with_dark["dark_frames"] == 2, \
        "standalone dark frames nested under dir/'dark' must be found"
    print("capture_correction_status dark-nesting check PASS: standalone dark "
          "under session_dir/'dark' is found and paired with the science capture")

    # archive_session_raws: no-op with nothing to archive, then a real
    # bundle-and-remove against the science + nested-dark files on disk in
    # s_old (flat lives in the separate standing library and is deliberately
    # never archived alongside any one session -- it isn't this session's
    # own raw), verified against the exact same tar safety order
    # hdr_from_session.py's own archive_raws uses.
    empty_result = archive_session_raws(Path("/tmp/zynergy_render_check_no_such_dir"))
    assert empty_result == {"archived": 0, "tar_path": None, "mb": 0.0}, \
        "archiving an empty/missing dir should be a clean no-op"

    raws_before = sorted(s_old.dir.glob("*.tif")) + sorted(dark_dir.glob("*.tif"))
    assert len(raws_before) == 4, "expected 2 science + 2 dark raw files before archiving"
    arch_result = archive_session_raws(s_old.dir)
    assert arch_result["archived"] == 4, \
        "expected all 4 raws archived (own dir + nested dark/)"
    assert arch_result["tar_path"].exists(), "tar file should exist on disk"
    assert not list(s_old.dir.glob("*.tif")), "loose science raws should be removed after archiving"
    assert not list(dark_dir.glob("*.tif")), "loose dark raws should be removed after archiving"
    with tarfile.open(str(arch_result["tar_path"])) as tf:
        names = set(tf.getnames())
    assert names == {p.name for p in raws_before}, \
        "tar contents should exactly match the original raw filenames"
    print("archive_session_raws check PASS: no-op on empty/missing dir, real bundle+verify+"
          "remove covers both the session dir and its nested dark/ subfolder")

    # _on_tag_stack: needs a real FocusPreviewWindow (a QMainWindow subclass),
    # so this one check -- unlike everything above it in render_check -- does
    # need PyQt5. Gated so `--render-check` keeps working without PyQt5
    # installed, same SKIPPED convention used elsewhere in this project.
    if not _HAVE_QT:
        print("_on_tag_stack check SKIPPED: PyQt5 not available here")
    else:
        qtapp = QApplication.instance() or QApplication([])
        tag_root = Path("/tmp/zynergy_render_check_tag")
        if tag_root.exists():
            shutil.rmtree(tag_root)
        tcam = FakeCamera(async_delay_s=0.0)
        win = FocusPreviewWindow(tcam, FocusMeter())
        win._session = provenance.Session(tag_root, {}, [])
        infos = []
        win._flat_information = lambda title, text: infos.append((title, text))

        # empty session: refused, not a crash
        win._on_tag_stack()
        assert "No capture in this session" in infos[-1][1]

        def _shoot(stem):
            d = threading.Event()
            g = {}
            tcam.capture_still_async(win._session.dir, stem,
                                     lambda r: (g.__setitem__("r", r), d.set()))
            d.wait(timeout=5.0)
            idx = provenance.record_capture(win._session, g["r"])
            win._session.captures[idx]["kind"] = "science"
            win._session.write()
            return idx

        _shoot("science_frame_0000")
        win._flat_ask_text = lambda title, label, value="": ("T9", True)
        win._flat_ask_int = lambda title, label, value, minv, maxv, step=1: (5, True)
        win._on_tag_stack()
        cap = win._session.captures[0]
        assert cap.get("stack") == "T9" and cap.get("plane") == 5, \
            "the tag should be written onto the session's own capture record"
        assert "Tagged" in infos[-1][0]
        # session.json lives in prov_dir now, not beside the raw frames
        # (Part 03) -- read it from there directly rather than dir.
        on_disk = json.loads((win._session.prov_dir / "session.json").read_text())
        assert on_disk["captures"][0]["stack"] == "T9", \
            "the tag must be persisted to session.json, not just held in memory"

        # collision: a second capture claiming the SAME (stack, plane) refuses
        # and must not tag the second capture either
        _shoot("science_frame_0001")
        win._flat_ask_text = lambda title, label, value="": ("T9", True)
        win._flat_ask_int = lambda title, label, value, minv, maxv, step=1: (5, True)
        win._on_tag_stack()
        assert "already held" in infos[-1][1]
        assert win._session.captures[1].get("stack") is None, \
            "a refused collision must leave the second capture untagged"

        # blank stack id refuses before ever calling stacks.apply_tag
        win._flat_ask_text = lambda title, label, value="": ("", True)
        win._on_tag_stack()
        assert "blank" in infos[-1][1]

        # the plane offered as next default increments from the last tag made
        offered = {}

        def _capture_offered_plane(title, label, value, minv, maxv, step=1):
            offered["value"] = value
            return 6, True

        win._flat_ask_int = _capture_offered_plane
        win._flat_ask_text = lambda title, label, value="": ("T9", True)
        win._on_tag_stack()
        assert offered["value"] == 6, "the next plane offered should be last tag's plane + 1"

        # reset_field must auto-fire on a SUCCESSFUL tag only (spec:
        # focus_aid_fps_and_stack_reset.md part 2) -- never on a refused tag
        # (blank ID, (stack, plane) collision) and never on an unrelated
        # capture, so a plain Capture press mid-hunt can't silently wipe
        # someone else's in-progress focus history.
        reset_calls = []
        real_reset = win.meter.reset_field
        win.meter.reset_field = lambda: (reset_calls.append(1), real_reset())

        _shoot("science_frame_0002")
        win._flat_ask_text = lambda title, label, value="": ("T10", True)
        win._flat_ask_int = lambda title, label, value, minv, maxv, step=1: (1, True)
        win._on_tag_stack()
        assert len(reset_calls) == 1, "a successful tag must reset the focus meter's field"

        win._flat_ask_text = lambda title, label, value="": ("", True)
        win._on_tag_stack()
        assert len(reset_calls) == 1, "a blank-ID refusal must not reset the focus meter"

        _shoot("science_frame_0003")
        win._flat_ask_text = lambda title, label, value="": ("T10", True)
        win._flat_ask_int = lambda title, label, value, minv, maxv, step=1: (1, True)
        win._on_tag_stack()
        assert len(reset_calls) == 1, "a collision refusal must not reset the focus meter"

        _shoot("science_frame_0004")
        assert len(reset_calls) == 1, "an untagged capture must not reset the focus meter"

        win.meter.reset_field = real_reset
        tcam.stop()
        shutil.rmtree(tag_root, ignore_errors=True)
        print("_on_tag_stack check PASS: empty-session guard, tag applied and "
              "persisted to session.json, (stack, plane) collision refuses "
              "without tagging the contender, blank stack ID refused, next "
              "plane default auto-increments, focus meter resets on a "
              "successful tag only")

        # Focus aid "no real lores frames received" diagnostic (_readout,
        # widened after the on-rig report that changing video resolution
        # breaks the lores stream): drives the real _readout method directly
        # (the same way _on_tag_stack above drives its own internals), not a
        # reimplementation of its branching, since camera_backend.py's own
        # self-check already covers _stash_lores's classification in
        # isolation -- this is the other half, proving _readout actually
        # reads and formats what that classification records.
        rcam = FakeCamera(async_delay_s=0.0)
        rwin = FocusPreviewWindow(rcam, FocusMeter())
        try:
            rcam.lores_frames_received = 0
            rcam.lores_decode_errors = 0
            rcam.last_lores_error = None
            rwin._zero_lores_ticks = 31          # cross the >30-tick threshold in one call
            rstate = rwin.meter.update(rcam.focus_frame())
            rwin._readout(rstate)
            assert rwin.readout.text() == (
                "no real lores frames received -- lores stream is not "
                "reaching the camera backend, not a scoring bug"), (
                "post_callback never reaching the backend at all (both "
                "counters at 0) must still show the original generic message")

            rcam.lores_decode_errors = 3
            rcam.last_lores_error = "bad main/lores pairing"
            rcam.lores_config_at_failure = None   # not yet captured (e.g. camera_configuration() itself failed)
            rwin._readout(rstate)
            assert rwin.readout.text() == (
                "lores stream configured but failing to decode "
                "(3 time(s)): bad main/lores pairing -- active config: "
                "active config not yet captured"), (
                "a real, recorded decode failure must surface its own error "
                "text instead of the generic guess, and say plainly when no "
                "config was captured rather than silently omitting it")

            rcam.lores_config_at_failure = {
                "streams_present": ["main", "raw"],
                "main": {"size": (1920, 1080), "format": "XBGR8888"},
                "raw": {"size": (4056, 3040), "format": "SBGGR12"},
            }
            rwin._readout(rstate)
            assert rwin.readout.text() == (
                "lores stream configured but failing to decode "
                "(3 time(s)): bad main/lores pairing -- active config: "
                "streams: main=1920x1080@XBGR8888, raw=4056x3040@SBGGR12 "
                "(lores MISSING)"), (
                "the captured active config must be rendered into the "
                "readout, explicitly flagging lores as MISSING -- the exact "
                "fact candidate 1 (create_preview_configuration() silently "
                "dropping lores) turns on")
        finally:
            rcam.stop()
        print("focus-aid lores diagnostic check PASS: _readout shows the "
              "original generic message when the backend never receives a "
              "lores frame at all, the real captured error text once "
              "camera_backend.py has recorded a decode failure, and the "
              "active config (streams present, lores present/missing) once "
              "that's been captured too")

        # Z-STACK AID (BUILD_LIST Tier 3 item 6): a full FakeCamera round
        # trip through the real toggle -- _start_zstack, two more
        # (repurposed) _start_capture presses, _end_zstack -- exercising the
        # real worker thread + queued cross-thread signal, not a bypassed
        # direct call. Since the signal is genuinely queued (the worker runs
        # on a background thread, the slot lives on this GUI-thread window),
        # processEvents() is pumped until each capture's own _capturing flag
        # clears, the same mechanism the real app relies on via its own
        # event loop -- nothing here is faked or skipped.
        import time

        def _pump_until_idle(timeout=5.0):
            deadline = time.time() + timeout
            while zwin._capturing and time.time() < deadline:
                qtapp.processEvents()
                time.sleep(0.005)
            assert not zwin._capturing, "z-stack plane capture never completed"

        zroot = Path("/tmp/zynergy_render_check_zstack")
        if zroot.exists():
            shutil.rmtree(zroot)
        zroot.mkdir(parents=True)
        zcam = FakeCamera(async_delay_s=0.0)
        zwin = FocusPreviewWindow(zcam, FocusMeter())
        # SPEC_focus_aid_fps_and_stack_reset.md part 2, carried over to the
        # z-stack aid per that spec's own forward-looking note: reset_field
        # must fire on every SUCCESSFUL plane capture (this flow's version of
        # "a stack plane tag succeeded"), and must NOT fire on a failed one.
        zstack_reset_calls = []
        real_zstack_reset = zwin.meter.reset_field
        zwin.meter.reset_field = lambda: (zstack_reset_calls.append(1), real_zstack_reset())
        # Module-attribute assignment on provenance, not a `global` rebind --
        # same reasoning as the PROFILE_PATH isolation at the top of this
        # function: _start_zstack/_ensure_session/etc. all read
        # provenance.OUT_ROOT by attribute, so this is what actually
        # redirects them during the test.
        _orig_out_root = provenance.OUT_ROOT
        provenance.OUT_ROOT = zroot
        try:
            # Guard: starting while a capture is already in flight is a no-op.
            zwin._capturing = True
            zwin._start_zstack()
            assert zwin._zstack is None, \
                "_start_zstack must refuse to start while a capture is in flight"
            zwin._capturing = False

            zwin._start_zstack()
            assert zwin._zstack is not None, "_start_zstack must actually start a stack"
            stack_id = zwin._zstack["stack_id"]
            stack_root = zwin._zstack["root"]
            assert stack_root.name == "zstack_{}".format(stack_id)
            assert not zwin.capture_kind_combo.isEnabled(), \
                "other capture kinds must be disabled while a stack is active"
            assert not zwin.record_btn.isEnabled(), \
                "Record must be disabled while a stack is active"
            _pump_until_idle()
            assert zwin._zstack["next_plane"] == 1, \
                "plane 0 must be captured as part of starting, no separate press needed"
            assert len(zstack_reset_calls) == 1, \
                "plane 0's own successful capture+tag must reset the focus " \
                "meter's field, same as a manual stack-plane tag always has"

            # Two more Capture presses -- the REPURPOSED _start_capture path,
            # not a direct call to _capture_zstack_plane, proving the
            # repurposing branch at _start_capture's own top actually fires.
            zwin._start_capture()
            _pump_until_idle()
            zwin._start_capture()
            _pump_until_idle()
            assert zwin._zstack["next_plane"] == 3, \
                "each Capture press while active must capture the next plane"
            assert len(zstack_reset_calls) == 3, \
                "each successful plane capture must reset the focus meter's " \
                "field, so refocusing for the next plane never starts from " \
                "a stale, already-settled peak left over from the last one"

            plane_dirs = sorted(stack_root.glob("plane_*"))
            assert [p.name for p in plane_dirs] == ["plane_0", "plane_1", "plane_2"], \
                "folder layout must be zstack_<ts>/plane_0, plane_1, plane_2 -- " \
                "one real, independent session per plane"
            for i, pd in enumerate(plane_dirs):
                # session.json lives in the mirrored provenance dir now, not
                # beside the plane's own raw frames (Part 03) -- go through
                # load_session_json rather than assuming co-location.
                sj = load_session_json(pd)
                cap = sj["captures"][0]
                assert cap["kind"] == "science", \
                    "a plane capture must be kind=science, the only kind " \
                    "_on_tag_stack/apply_tag's own convention taggs"
                assert cap.get("stack") == stack_id and cap.get("plane") == i, \
                    "each plane must be tagged with the stack's own id and " \
                    "its own plane number, automatically -- no dialog involved"
                assert isinstance(cap.get("sharpness_score"), float), \
                    "a plane capture should get the same post-capture QC " \
                    "score a normal science capture gets"

            # A FAILED plane capture must NOT reset the field -- the spec's
            # own constraint carried over exactly: only a successful tag.
            # Run last (after the folder-layout/tagging checks above), since
            # this deliberately leaves a plane_3 folder behind that would
            # otherwise break the "exactly plane_0/1/2" assertion.
            real_apply_tag = _stacks.apply_tag
            _stacks.apply_tag = lambda *a, **k: (_ for _ in ()).throw(
                ValueError("simulated tag failure"))
            try:
                zwin._start_capture()
                _pump_until_idle()
            finally:
                _stacks.apply_tag = real_apply_tag
            assert zwin._zstack["next_plane"] == 3, \
                "a failed plane capture must not advance next_plane"
            assert len(zstack_reset_calls) == 3, \
                "a failed plane capture/tag must NOT reset the focus meter's " \
                "field -- only a successful tag may"

            # Guard: ending while a capture is in flight is a no-op.
            zwin._capturing = True
            zwin._end_zstack()
            assert zwin._zstack is not None, \
                "_end_zstack must refuse to end while a plane capture is in flight"
            zwin._capturing = False

            # The hand-off's own Yes/No gate: No must NOT open the wizard.
            zwin._flat_question = lambda title, text, default=None: QMessageBox.No
            opened = {}

            class _FakeWizard:
                def __init__(self, out_root, parent=None):
                    opened["out_root"] = out_root
                    opened.setdefault("exec_called", False)
                    select_all_calls = opened.setdefault("select_all_calls", [])
                    self.file_page = type("FP", (), {
                        "gallery": type("G", (), {
                            "list_widget": type("LW", (), {
                                "selectAll": lambda s: select_all_calls.append(1)})()
                        })()
                    })()

                def exec_(self):
                    opened["exec_called"] = True

            global _process_wizard
            _orig_process_wizard = _process_wizard
            _process_wizard = type("FakeModule", (), {"ProcessWizard": _FakeWizard})
            try:
                zwin._end_zstack()
                assert zwin._zstack is None, "_end_zstack must always clear the stack state"
                # validate_all must actually find and read each plane's
                # session.json (mapped through _provenance_dir_for, Part 03)
                # rather than silently seeing nothing -- a real 3-plane,
                # correctly-tagged stack should validate clean.
                assert "No issues found." in zwin.capture_status.toolTip(), \
                    "validate_all should find no issues in a clean 3-plane " \
                    "stack -- got: {!r}".format(zwin.capture_status.toolTip())
                assert not opened, \
                    "declining the 'process now?' offer must never open the wizard"
                assert zwin.capture_kind_combo.isEnabled(), \
                    "ending a stack must re-enable the other capture kinds"
                assert zwin.record_btn.isEnabled(), \
                    "ending a stack must re-enable Record"

                # Yes must open the wizard, scoped to the stack's OWN root
                # (never the global OUT_ROOT), with every plane pre-selected.
                zwin._start_zstack()
                _pump_until_idle()
                stack_root_2 = zwin._zstack["root"]
                zwin._flat_question = lambda title, text, default=None: QMessageBox.Yes
                zwin._end_zstack()
                assert opened["out_root"] == stack_root_2, \
                    "the wizard must be scoped to the stack's own root folder, " \
                    "not the global OUT_ROOT -- that's what keeps its embedded " \
                    "Gallery showing only this stack's own planes"
                assert opened["exec_called"] is True
                assert opened["select_all_calls"] == [1], \
                    "every plane must be pre-selected, not left for the user to click"
            finally:
                _process_wizard = _orig_process_wizard

            # Regression: with no stack active, Capture must behave exactly
            # as it always has -- the repurposing branch must not fire.
            assert zwin._zstack is None
            called = []
            zwin._capture_zstack_plane = lambda: called.append(1)
            zwin._session = provenance.Session(zroot / "plain", {}, [])
            zwin._start_capture()
            _pump_until_idle()
            assert not called, \
                "with no active stack, _start_capture must never repurpose " \
                "into _capture_zstack_plane"
        finally:
            # FIX: this was a dead local rebind (`OUT_ROOT = ...`, no
            # `provenance.` prefix) that never actually restored the real
            # attribute -- harmless before Part 03 (nothing downstream read
            # provenance.OUT_ROOT), but now load-bearing: _provenance_dir_for
            # (used by list_sessions/load_session_json/capture_correction_
            # status, exercised again later in this same render_check run)
            # depends on provenance.OUT_ROOT matching the real capture root.
            provenance.OUT_ROOT = _orig_out_root
            zcam.stop()
            shutil.rmtree(zroot, ignore_errors=True)
        print("z-stack aid check PASS: start guard refuses mid-capture, "
              "starting captures plane 0 immediately, other capture kinds "
              "and Record disabled while active, each repurposed Capture "
              "press captures and auto-tags the next plane (real worker "
              "thread + real queued signal, not bypassed), folder layout is "
              "zstack_<ts>/plane_0/plane_1/plane_2 with one independent "
              "science-kind session each, end guard refuses mid-capture, "
              "declining the process offer never opens the wizard, accepting "
              "opens it scoped to the stack's own root with every plane "
              "pre-selected, a plain Capture press with no active stack "
              "is completely unaffected, and (SPEC_focus_aid_fps_and_stack_"
              "reset.md part 2, carried over) the focus meter resets on "
              "every successful plane capture and never on a failed one")

        # Full screen mode with a floating panel (BUILD_LIST Tier 2): real
        # frameless-window + manual-geometry calls (not showFullScreen()/
        # showNormal() -- see the comment on self._is_fullscreen in
        # FocusPreviewWindow.__init__ for why), real reparenting of the
        # actual panel widget between the splitter and the floating window,
        # real menu-bar visibility, real key routing -- nothing bypassed.
        fscam = FakeCamera(async_delay_s=0.0)
        fswin = FocusPreviewWindow(fscam, FocusMeter())
        try:
            assert fswin._splitter.indexOf(fswin._panel) != -1, \
                "the panel must start docked in the splitter, normal mode"
            assert not fswin._is_fullscreen

            fswin._toggle_fullscreen()
            assert fswin._is_fullscreen
            assert not fswin.menuBar().isVisible(), \
                "the menu bar must hide on entering full screen"
            assert fswin._splitter.indexOf(fswin._panel) == -1, \
                "the panel must be reparented OUT of the splitter on entry"
            assert fswin._floating_panel is not None
            assert not fswin._floating_panel.isVisible(), \
                "the floating panel starts HIDDEN on entry -- explicit " \
                "toggle by design, not auto-shown (that would defeat the " \
                "whole point of maximizing the preview)"

            # P toggles the floating panel while full screen; it must be a
            # genuine no-op outside full screen (checked further below).
            fswin._toggle_floating_panel()
            assert fswin._floating_panel.isVisible()
            fswin._toggle_floating_panel()
            assert not fswin._floating_panel.isVisible()

            # Exiting restores normal mode exactly, regardless of whatever
            # state the floating panel toggle was left in.
            fswin._toggle_floating_panel()
            assert fswin._floating_panel.isVisible()
            fswin._toggle_fullscreen()
            assert not fswin._is_fullscreen
            assert fswin.menuBar().isVisible(), \
                "the menu bar must come back on exiting full screen"
            assert fswin._splitter.indexOf(fswin._panel) == 1, \
                "the panel must be reparented back into the splitter at " \
                "its original index on exit"
            assert not fswin._floating_panel.isVisible(), \
                "the floating panel must be hidden on exit even if it was " \
                "left visible mid-full-screen"

            # P must be a genuine no-op outside full screen -- not swallowed
            # silently, just never routed to _toggle_floating_panel at all.
            called = []
            fswin._toggle_floating_panel = lambda: called.append(1)
            p_ev = QKeyEvent(QEvent.KeyPress, Qt.Key_P, Qt.NoModifier)
            fswin.keyPressEvent(p_ev)
            assert not called, "P must do nothing while not full screen"

            # Ctrl+Escape only exits full screen when nothing else claims
            # Escape first -- an armed burst still takes priority, matching
            # the two existing Escape branches' own established order.
            fswin._toggle_fullscreen()
            assert fswin._is_fullscreen
            fswin._armed = {"kind": "science", "n": 1, "prefix": "science_"}
            cancelled = []
            fswin._cancel_armed = lambda: cancelled.append(1)
            ctrl_esc_ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.ControlModifier)
            fswin.keyPressEvent(ctrl_esc_ev)
            assert fswin._is_fullscreen, \
                "an armed burst must still take priority over exiting " \
                "full screen, same as it already does over anything else"
            assert cancelled == [1]
            fswin._armed = None

            fswin.keyPressEvent(ctrl_esc_ev)
            assert not fswin._is_fullscreen, \
                "Ctrl+Escape must exit full screen once nothing else claims Escape"

            # closeEvent's panel_width guard: must not raise, and must not
            # save a bogus width, while the panel is mid-float.
            fswin._toggle_fullscreen()
            fswin._toggle_floating_panel()
            assert fswin._splitter.indexOf(fswin._panel) == -1
            fswin.closeEvent(QCloseEvent())   # must not raise
        finally:
            fscam.stop()
        print("full screen mode check PASS: entering hides the menu bar and "
              "reparents the real panel widget out of the splitter (hidden "
              "by default, explicit toggle); P shows/hides it while full "
              "screen; exiting always restores the menu bar and the "
              "panel's original splitter position regardless of the "
              "floating panel's own visibility state; P is a genuine "
              "no-op outside full screen; Ctrl+Escape exits only once an "
              "armed burst no longer claims Escape first, same priority "
              "order the existing Escape branches already have; "
              "closeEvent's panel_width save does not raise or misbehave "
              "while the panel is mid-float")

        # Preferences dialog (Preferences-dialog plan set, Part 01): replaces
        # the old standalone Video resolution/Theme/Casual Mode menu entries
        # with one sectioned dialog, populated from camera.get_capabilities()
        # (PLAN_02) rather than a hardcoded list.
        # (No `global PREFS_PATH` here -- the onboarding gate check earlier
        # in this same function already declares it; Python disallows a
        # second `global X` statement anywhere later in a function once X
        # has been used, so this section relies on that earlier one.)
        orig_prefs_path = PREFS_PATH
        PREFS_PATH = Path("/tmp/zynergy_render_check_prefs_dialog.json")
        PREFS_PATH.unlink(missing_ok=True)
        try:
            # A capability the driver doesn't report produces no control at
            # all, not an empty/disabled one -- the default FakeCamera
            # matches Picamera2Camera's current no-stream-server behavior.
            pcam = FakeCamera(async_delay_s=0.0)
            dlg = PreferencesDialog(pcam)
            assert dlg._stream_fmt_combo is None and dlg._stream_res_combo is None, \
                "a capability the driver omits must produce no control at all"

            # Capture/Video Options is built from get_capabilities(), never
            # a hardcoded list -- confirm the combos hold the fake's own
            # synthetic values.
            caps = pcam.get_capabilities()
            res_items = {dlg._capture_res_combo.itemData(i)
                        for i in range(dlg._capture_res_combo.count())}
            assert set(caps["capture_resolutions"]) <= res_items, \
                "capture resolution combo must be built from get_capabilities()"
            fmt_items = {dlg._capture_fmt_combo.itemData(i)
                        for i in range(dlg._capture_fmt_combo.count())}
            assert set(caps["capture_formats"]) == fmt_items, \
                "capture format combo must be built from get_capabilities()"

            # Next-launch settings (Capture/Video/Appearance): persist only
            # on OK, not on every selection change.
            idx = PreferencesDialog._index_for_data(dlg._capture_res_combo, (2028, 1520))
            assert idx >= 0
            dlg._capture_res_combo.setCurrentIndex(idx)
            assert load_pref("capture_resolution", "sentinel") == "sentinel", \
                "a next-launch setting must not persist before OK is pressed"
            dlg._on_accept()
            assert load_pref("capture_resolution", None) == [2028, 1520], \
                "OK must persist every next-launch setting"

            # Live-apply settings (Advanced): persist immediately on change,
            # independent of OK/Cancel. Keep RAW Images defaults on.
            dlg2 = PreferencesDialog(pcam)
            assert dlg2._keep_raw_check.isChecked() is True, \
                "Keep RAW Images must default on (this project's usual " \
                "provenance-by-default stance -- raws are what green-plane " \
                "measurement is taken from)"
            dlg2._keep_raw_check.setChecked(False)
            assert load_pref("keep_raw_images", "sentinel") is False, \
                "an Advanced setting must persist immediately, before OK"
            dlg2.reject()
            assert load_pref("keep_raw_images", "sentinel") is False, \
                "Cancel must not revert a live-applied Advanced setting"

            # Additional export formats (Part 03, lifted from casual_mode.py,
            # then genuinely completed once debayer.py's tonemap/write split
            # removed TIFF's old structural-byproduct lock): TIFF/PNG/JPG/DNG
            # are all four real, independent checkboxes now, none locked.
            # PNG/JPG/TIFF default on, DNG defaults off; Process DNG only
            # enables once DNG is checked, same shape casual_mode.py's own
            # dng_merge_check used.
            dlg3 = PreferencesDialog(pcam)
            assert dlg3._fmt_tiff_check.isChecked() is True and \
                dlg3._fmt_tiff_check.isEnabled(), \
                "TIFF defaults checked but must be a REAL, togglable checkbox now"
            assert dlg3._fmt_png_check.isChecked() is True
            assert dlg3._fmt_jpg_check.isChecked() is True
            assert dlg3._fmt_dng_check.isChecked() is False
            assert dlg3._fmt_dng_merge_check.isEnabled() is False, \
                "Process DNG must start disabled -- DNG itself isn't checked yet"
            dlg3._fmt_tiff_check.setChecked(False)
            assert load_pref("export_format_tiff", "sentinel") is False, \
                "TIFF must persist immediately like every other Advanced export setting"
            dlg3._fmt_dng_check.setChecked(True)
            assert load_pref("export_format_dng", "sentinel") is True, \
                "an Advanced export-format setting must persist immediately"
            assert dlg3._fmt_dng_merge_check.isEnabled() is True, \
                "checking DNG must enable Process DNG"
            dlg3._fmt_png_check.setChecked(False)
            assert load_pref("export_format_png", "sentinel") is False
            dlg3.reject()
            assert load_pref("export_format_tiff", "sentinel") is False and \
                load_pref("export_format_dng", "sentinel") is True and \
                load_pref("export_format_png", "sentinel") is False, \
                "Cancel must not revert live-applied export-format settings either"

            # _resolution_combo fallback fix: a persisted resolution
            # preference that doesn't match anything get_capabilities()
            # currently reports must still show as ITSELF, not silently
            # fall back to "Default (current preview)" -- exactly the
            # scenario a real rig hit (video_resolution persisted as
            # [2028, 1080], no exact sensor mode of that size). Found while
            # investigating a user-reported roadmap item unrelated to this
            # combo's own construction logic; exercised here against the
            # disabled Video resolution combo, the control the defect was
            # actually reported against, but the fix lives in the one
            # shared _resolution_combo() helper every resolution combo in
            # this dialog (capture/video/stream, and any future one) uses.
            save_pref("video_resolution", [2028, 1080])
            assert (2028, 1080) not in pcam.get_capabilities()["video_resolutions"], \
                "test setup: this value must genuinely be absent from the " \
                "reported list for the fallback case to be exercised"
            dlg4 = PreferencesDialog(pcam)
            assert dlg4._video_res_combo.currentData() == (2028, 1080), \
                "a disabled control must display the TRUE persisted value, " \
                "even one absent from get_capabilities(), not silently " \
                "fall back to Default"
            assert dlg4._video_res_combo.currentText() == "2028x1080"
            dlg4._on_accept()
            assert load_pref("video_resolution", None) == [2028, 1080], \
                "OK must round-trip the disabled control's own displayed " \
                "value unchanged, never clobber it with a fallback"

            pcam.stop()
        finally:
            PREFS_PATH = orig_prefs_path
        print("Preferences dialog check PASS: capture/video controls built "
              "entirely from get_capabilities() (an omitted capability "
              "produces no control), next-launch settings persist only on "
              "OK, Advanced settings persist immediately regardless of "
              "OK/Cancel, Keep RAW Images defaults on, TIFF/PNG/JPG/DNG "
              "export-format settings are all four real, independently "
              "togglable checkboxes that persist immediately, and a "
              "resolution preference absent from get_capabilities() "
              "displays as itself rather than silently falling back to "
              "Default (and round-trips through OK unchanged)")

        # Preferences dialog, part 2: stream_formats/stream_resolutions
        # present -> real controls appear (the flip side of the omitted-
        # capability check above); a missing gui_prefs.json and a stale
        # "casual_mode" key left over from a superseded build both degrade
        # gracefully rather than raising.
        orig_prefs_path2 = PREFS_PATH
        PREFS_PATH = Path("/tmp/zynergy_render_check_prefs_dialog2.json")
        PREFS_PATH.unlink(missing_ok=True)   # confirms a missing file doesn't raise
        try:
            scam = FakeCamera(async_delay_s=0.0, stream_caps=True)
            sdlg = PreferencesDialog(scam)
            assert sdlg._stream_fmt_combo is not None and sdlg._stream_res_combo is not None, \
                "a capability the driver DOES report must produce a real control"
            assert sdlg._stream_fmt_combo.count() == len(scam.get_capabilities()["stream_formats"])
            scam.stop()

            # A gui_prefs.json carrying the superseded "casual_mode" key
            # (from an old build) must be ignored without error.
            save_pref("casual_mode", True)
            stale_cam = FakeCamera(async_delay_s=0.0)
            PreferencesDialog(stale_cam)   # must not raise
            stale_cam.stop()
        finally:
            PREFS_PATH = orig_prefs_path2
        print("Preferences dialog check PASS (part 2): a reported stream "
              "capability produces a real control, a missing gui_prefs.json "
              "and a stale casual_mode key both degrade gracefully")

        # "Clean cache now" (Preferences-dialog plan set, Part 04): a real
        # plane_cache.clean_cache() call driven through the actual button
        # handler, reporting real counts -- no longer the "no cache to
        # clean yet" stub Part 01 shipped with. Lands under
        # provenance.PROVENANCE_ROOT, already redirected to a disposable
        # temp dir for this whole render_check() run (see near the top of
        # this function), so plane_cache.cache_root()'s live attribute read
        # picks it up with no extra redirect needed here. annotations.json
        # gets the same redirect treatment (temp path, swapped back in
        # finally) so this exercises the button's real referenced=None ->
        # plane_cache.referenced_hashes() -> annotations.load_annotations()
        # chain end to end, deterministically, never the real
        # ~/.zynergy/annotations.json.
        if _plane_cache is not None and _plane_cache._annotations is not None:
            orig_prefs_path3 = PREFS_PATH
            PREFS_PATH = Path("/tmp/zynergy_render_check_prefs_dialog3.json")
            PREFS_PATH.unlink(missing_ok=True)
            orig_annotation_path = _plane_cache._annotations.ANNOTATION_PATH
            _plane_cache._annotations.ANNOTATION_PATH = Path(
                "/tmp/zynergy_render_check_prefs_dialog3_annotations.json")
            _plane_cache._annotations.ANNOTATION_PATH.unlink(missing_ok=True)
            try:
                ccam = FakeCamera(async_delay_s=0.0)
                cache_dlg = PreferencesDialog(ccam)
                referenced_plane = np.zeros((4, 4), dtype=np.uint16)
                unreferenced_plane = np.ones((4, 4), dtype=np.uint16)
                ref_path, ref_hash = _plane_cache.store_plane(referenced_plane)
                unref_path, _unref_hash = _plane_cache.store_plane(unreferenced_plane)
                # A real committed mark under ref_hash -- the same call
                # measure.py's own mark-commit path makes -- is what the
                # button's live annotations lookup must actually find.
                _plane_cache._annotations.save_mark(
                    ref_hash, {"type": "distance", "note": "render_check"},
                    record_defaults={"shape": [4, 4], "dtype": "uint16", "kind": "green"})
                cache_dlg._on_clean_cache_now()
                assert ref_path.is_file(), \
                    "a plane with a real committed mark must survive the real button click"
                assert not unref_path.is_file(), \
                    "an unmarked plane must be removed by the real button click"
                status_text = cache_dlg._clean_cache_status.text()
                assert "no cache to clean yet" not in status_text, \
                    "the stub message must be gone now that a real cache exists"
                assert "removed 1" in status_text and "kept 1" in status_text, \
                    "the status label must report the real removed/kept counts"
                ccam.stop()
            finally:
                PREFS_PATH = orig_prefs_path3
                _plane_cache._annotations.ANNOTATION_PATH = orig_annotation_path
            print("Clean cache now check PASS: the real button handler "
                  "removes an unmarked cached plane and retains one with a "
                  "real committed mark, driven through the actual live "
                  "annotations lookup (not a hand-fed referenced set), and "
                  "the status label reports the real result, not the "
                  "Part 01 stub")

        # Green-plane extraction utility (BUILD_LIST Tier 1 item 4): a real
        # subprocess call to debayer.py --green, driven the same way the
        # z-stack aid's own worker thread was proven -- processEvents()
        # pumped until _capturing clears, since green_extract_done_signal is
        # a genuinely queued cross-thread connection too.
        import tifffile

        assert default_green_output_path(Path("/a/b/science_frame_0000.dng")) == \
            Path("/a/b/science_frame_0000_green.tif"), \
            "must match debayer.py's own default CLI naming exactly"

        gx_root = Path("/tmp/zynergy_render_check_green_extract")
        if gx_root.exists():
            shutil.rmtree(gx_root)
        gx_root.mkdir(parents=True)
        gxcam = FakeCamera(async_delay_s=0.0)
        gxwin = FocusPreviewWindow(gxcam, FocusMeter())
        try:
            mosaic = np.random.default_rng(0).integers(
                0, 4000, size=(32, 32)).astype(np.uint16)
            raw_path = gx_root / "science_frame_0000.tif"
            tifffile.imwrite(str(raw_path), mosaic)
            out_path = gx_root / "extracted_green.tif"

            gxwin._run_green_extract_cmd(raw_path, out_path)
            _pump_gxwin_deadline = time.time() + 15.0
            while gxwin._capturing and time.time() < _pump_gxwin_deadline:
                qtapp.processEvents()
                time.sleep(0.005)
            assert not gxwin._capturing, "green extraction never completed"
            assert out_path.is_file(), "debayer.py --green must have written the output"
            desc = json.loads(tifffile.TiffFile(str(out_path)).pages[0].description)
            assert desc.get("software") == "debayer.py"
            assert desc.get("transform") == "single_green_extraction", \
                "must be a real debayer.py --green extraction, not a copy"
            assert "green" in gxwin.capture_status.text().lower()

            # A failure (bad input path) must be reported, not silently
            # swallowed or left looking like it's still running.
            gxwin._run_green_extract_cmd(gx_root / "does_not_exist.tif", out_path)
            _pump_gxwin_deadline = time.time() + 15.0
            while gxwin._capturing and time.time() < _pump_gxwin_deadline:
                qtapp.processEvents()
                time.sleep(0.005)
            assert not gxwin._capturing
            assert "failed" in gxwin.capture_status.text().lower()
        finally:
            gxcam.stop()
            shutil.rmtree(gx_root, ignore_errors=True)
        print("green-plane extraction check PASS: default output naming "
              "matches debayer.py's own CLI convention exactly, a real "
              "debayer.py --green subprocess call produces a real, "
              "correctly-provenanced green-plane file, and a failed "
              "extraction is reported rather than swallowed")

        # Export / Publish menu actions (MeasureWindow extraction, step 3):
        # workers driven directly (bypassing GalleryPickDialog.exec_, which
        # can't run headless, same reason the green-extraction check above
        # calls its worker directly), processEvents() pumped until
        # _capturing clears since both done signals are genuinely queued
        # cross-thread connections too.
        ex_root = Path("/tmp/zynergy_render_check_export_publish")
        if ex_root.exists():
            shutil.rmtree(ex_root)
        ex_cap_root = ex_root / "captures"
        ex_prov_root = ex_root / "provenance"
        ex_cap_root.mkdir(parents=True)
        ex_prov_root.mkdir(parents=True)

        _orig_out_root4, _orig_prov_root4 = provenance.OUT_ROOT, provenance.PROVENANCE_ROOT
        provenance.OUT_ROOT, provenance.PROVENANCE_ROOT = ex_cap_root, ex_prov_root
        orig_annotation_path2 = _annotations.ANNOTATION_PATH
        _annotations.ANNOTATION_PATH = ex_root / "annotations.json"
        excam = FakeCamera(async_delay_s=0.0)
        exwin = FocusPreviewWindow(excam, FocusMeter())
        try:
            green_h2, green_w2 = GREEN_PLANE_RES[1], GREEN_PLANE_RES[0]

            # A cache-only plane -- committed through Live Measuring, never
            # written as a capture session under OUT_ROOT -- is exactly the
            # regression case the known_green_hashes/list_cached_hashes
            # union exists to catch: it must NOT show up as an orphan.
            cache_plane = np.random.default_rng(5).integers(
                0, 4096, size=(green_h2, green_w2)).astype(np.uint16)
            _cache_path, cache_hash = _plane_cache.store_plane(cache_plane)
            _annotations.save_mark(
                cache_hash, {"type": "distance", "note": "render_check"},
                record_defaults={"shape": [green_h2, green_w2], "dtype": "uint16",
                                 "kind": "green", "calibration_ref": None,
                                 "source_sha256": None})

            # A genuinely orphaned record: no matching capture, no cached
            # plane -- must be the ONLY hash reported when both scans run.
            orphan_hash = "0" * 64
            _annotations.save_mark(
                orphan_hash, {"type": "distance", "note": "render_check orphan"},
                record_defaults={"shape": [4, 4], "dtype": "uint16",
                                 "kind": "green", "calibration_ref": None,
                                 "source_sha256": None})

            ex_out_path = ex_root / "measurements.json"
            exwin._run_export_results_cmd(ex_out_path)
            _pump_deadline = time.time() + 15.0
            while exwin._capturing and time.time() < _pump_deadline:
                qtapp.processEvents()
                time.sleep(0.005)
            assert not exwin._capturing, "export never completed"
            assert ex_out_path.is_file(), "export must write the file"
            exported = json.loads(ex_out_path.read_text())
            assert exported["total_measurements"] == 2
            status_text = exwin.capture_status.toolTip()
            assert orphan_hash in status_text, \
                "the genuinely orphaned record must be reported"
            assert cache_hash not in status_text, \
                "a cache-only plane with a real committed mark must NOT be " \
                "reported as an orphan -- this is the union-of-hashes regression test"

            # Absent vs empty: with _gallery/_plane_cache unavailable, the
            # write must still land, but orphan evidence must say
            # "unavailable", never an empty (or any) orphan list -- a
            # partial known-hashes set is worse than no evidence at all.
            orig_gallery, orig_plane_cache_mod = _gallery, _plane_cache
            _gallery = None
            _plane_cache = None
            try:
                ex_out_path2 = ex_root / "measurements2.json"
                exwin._run_export_results_cmd(ex_out_path2)
                _pump_deadline = time.time() + 15.0
                while exwin._capturing and time.time() < _pump_deadline:
                    qtapp.processEvents()
                    time.sleep(0.005)
                assert not exwin._capturing
                assert ex_out_path2.is_file(), \
                    "the write must still happen even with no orphan-scan coverage"
                status_text2 = exwin.capture_status.toolTip()
                assert "unavailable" in status_text2.lower(), \
                    "coverage unavailable must be reported explicitly"
                assert orphan_hash not in status_text2, \
                    "an empty/absent known-hashes set must never masquerade " \
                    "as a real orphan list"
            finally:
                _gallery, _plane_cache = orig_gallery, orig_plane_cache_mod

            # A failure (export.export_measurements raising) must be
            # reported, not silently swallowed.
            orig_export_mod = _export

            class _FailingExport:
                @staticmethod
                def export_measurements(store=None, out_path=None):
                    raise RuntimeError("forced export failure")

            _export = _FailingExport
            try:
                ex_out_path3 = ex_root / "measurements3.json"
                exwin._run_export_results_cmd(ex_out_path3)
                _pump_deadline = time.time() + 15.0
                while exwin._capturing and time.time() < _pump_deadline:
                    qtapp.processEvents()
                    time.sleep(0.005)
                assert not exwin._capturing
                assert "failed" in exwin.capture_status.text().lower()
            finally:
                _export = orig_export_mod

            print("Export measurement results check PASS: writes the "
                  "results file, a cache-only plane with a real committed "
                  "mark is not reported as an orphan (the union-of-hashes "
                  "regression test), a genuinely orphaned record is, "
                  "coverage-unavailable is distinguishable from a clean "
                  "scan, and a forced failure is reported rather than "
                  "swallowed")

            # Publish: pick a real on-disk capture (GalleryPickDialog would
            # do this interactively; driven directly here), publish it, and
            # confirm the package's calibration_ref comes from the
            # RECORD'S OWN stored ref (Option B+), not whatever is
            # currently active for some objective.
            pub_session = ex_cap_root / "2024-03-01_000001"
            pub_session.mkdir()
            pub_prov = ex_prov_root / "2024-03-01_000001"
            pub_prov.mkdir()
            pub_cap = {"index": 0, "kind": "snap", "file_prefix": "snap_",
                      "frame_count": 1, "timestamp": "2024-03-01T00:00:01+00:00"}
            (pub_prov / "session.json").write_text(
                json.dumps({"capture_dir": str(pub_session), "captures": [pub_cap]}))
            pub_plane = np.random.default_rng(6).integers(
                0, 4096, size=(green_h2, green_w2)).astype(np.uint16)
            pub_raw = pub_session / "snap_frame_0000.tif"
            tifffile.imwrite(str(pub_raw), pub_plane)
            pub_hash = _pixel_hash.pixel_sha256(pub_plane)
            stored_ref = {"objective": "40x", "entry_id": "fit_render_check",
                         "um_per_px": 0.5}
            _annotations.save_mark(
                pub_hash, {"type": "distance", "note": "render_check publish"},
                record_defaults={"shape": [green_h2, green_w2], "dtype": "uint16",
                                 "kind": "green", "calibration_ref": stored_ref,
                                 "source_sha256": None})

            pub_out_dir = ex_root / "package"
            exwin._run_publish_package_cmd(pub_raw, pub_out_dir)
            _pump_deadline = time.time() + 15.0
            while exwin._capturing and time.time() < _pump_deadline:
                qtapp.processEvents()
                time.sleep(0.005)
            assert not exwin._capturing, "publish never completed"
            assert (pub_out_dir / "green_plane.tif").is_file()
            manifest = json.loads((pub_out_dir / "manifest.json").read_text())
            assert manifest["calibration"] == stored_ref, \
                "publish must use the record's OWN stored calibration_ref " \
                "(Option B+), not whatever is currently active"
            assert manifest["results"]["total_measurements"] == 1
            assert "published" in exwin.capture_status.text().lower()

            # Forced-failure case (bad input path): must be reported, not
            # left looking like it's still running.
            exwin._run_publish_package_cmd(ex_root / "does_not_exist.tif", pub_out_dir)
            _pump_deadline = time.time() + 15.0
            while exwin._capturing and time.time() < _pump_deadline:
                qtapp.processEvents()
                time.sleep(0.005)
            assert not exwin._capturing
            assert "failed" in exwin.capture_status.text().lower()

            print("Publish package check PASS: picks its own image, writes "
                  "a real green_plane.tif/results.json/manifest.json, the "
                  "manifest's calibration_ref comes from the record's own "
                  "stored ref (Option B+), and a forced failure is "
                  "reported rather than hanging")
        finally:
            excam.stop()
            provenance.OUT_ROOT, provenance.PROVENANCE_ROOT = _orig_out_root4, _orig_prov_root4
            _annotations.ANNOTATION_PATH = orig_annotation_path2
            shutil.rmtree(ex_root, ignore_errors=True)

        # _score_capture_sharpness (section 13's post-capture QC): a real
        # FakeCamera burst, scored against its OWN written frame via
        # calibrate.load_green_plane + focus.score_capture_sharpness --
        # both called for real, nothing mocked here.
        qc_root = Path("/tmp/zynergy_render_check_qc")
        if qc_root.exists():
            shutil.rmtree(qc_root)
        qcam = FakeCamera(async_delay_s=0.0)
        qc_session = provenance.Session(qc_root, {}, [])
        qc_result = qcam.capture_burst(qc_session.dir, "science_", 2)
        qc_idx = provenance.record_burst(qc_session, "science", "science_", qc_result)
        assert "sharpness_score" not in qc_session.captures[qc_idx], \
            "record_burst itself must not invent a score -- only " \
            "_score_capture_sharpness (called separately, after) does"
        win._score_capture_sharpness(qc_session, qc_idx, qc_result)
        score = qc_session.captures[qc_idx].get("sharpness_score")
        assert isinstance(score, float), \
            "a real capture should score as a real float, got {!r}".format(score)
        on_disk_qc = json.loads((qc_session.prov_dir / "session.json").read_text())
        assert on_disk_qc["captures"][qc_idx]["sharpness_score"] == score, \
            "the score must be persisted to session.json, not just held in memory"

        # A scoring failure (green extraction broke, file unreadable, whatever)
        # must record None and must NOT raise into the capture flow.
        orig_load_green = _calibrate.load_green_plane
        _calibrate.load_green_plane = lambda *a, **k: (_ for _ in ()).throw(
            ValueError("simulated extraction failure"))
        try:
            win._score_capture_sharpness(qc_session, qc_idx, qc_result)
        finally:
            _calibrate.load_green_plane = orig_load_green
        assert qc_session.captures[qc_idx]["sharpness_score"] is None, \
            "a scoring failure should record None, not leave the old score " \
            "or raise out of this method"

        qcam.stop()
        shutil.rmtree(qc_root, ignore_errors=True)
        print("_score_capture_sharpness check PASS: a real FakeCamera capture "
              "scores as a real float via calibrate.load_green_plane + "
              "focus.score_capture_sharpness (nothing mocked), the score "
              "persists to session.json, a simulated extraction failure "
              "records None rather than raising into the capture flow")

        # Auto-processing (Part 03): _auto_process/_run_process_cmd/
        # _on_process_finished, end to end through a REAL hdr_from_session.py
        # subprocess -- no Yes/No gate in between anymore (Snap/Science/HDR
        # all reach this now), and the flat/dark correction status it prints
        # must land on the capture's own session.json entry as the named
        # technique's actual outcome (CORRECTION_flat_dark_framing.md), not
        # just print to stdout and vanish.
        # FLAT_ROOT may already hold frames from an earlier check in this
        # same render_check run (e.g. the processing-wizard-helpers check
        # above, which shoots into it and does not clean up -- it is a
        # standing library by design, so nothing here normally would);
        # clear it so this check's "no flat shot" expectation is real
        # rather than order-dependent on what ran before it.
        if provenance.FLAT_ROOT.exists():
            shutil.rmtree(provenance.FLAT_ROOT)
        ap_session = provenance.Session(provenance.OUT_ROOT, {}, [])
        ap_result = win.camera.capture_burst(ap_session.dir, "science_", 2)
        ap_idx = provenance.record_burst(ap_session, "science", "science_", ap_result)
        win._session = ap_session
        win._flat_question = lambda title, text, default=None: QMessageBox.No   # decline archive offer
        win._auto_process("science", ap_idx)
        assert win._capturing, \
            "_auto_process must go straight into processing, no Yes/No gate first"
        ap_deadline = time.time() + 20.0
        while win._capturing and time.time() < ap_deadline:
            qtapp.processEvents()
            time.sleep(0.01)
        assert not win._capturing, "auto-processing must finish within the deadline"
        assert "processed" in win.capture_status.text().lower(), \
            "a successful auto-process must report completion: {!r}".format(
                win.capture_status.text())
        assert (ap_session.dir / "final_display.tif").exists(), \
            "the real hdr_from_session.py subprocess must have produced a display image"
        on_disk_ap = json.loads((ap_session.prov_dir / "session.json").read_text())
        cap_entry = on_disk_ap["captures"][ap_idx]
        assert cap_entry.get("flat_correction") == \
            "skipped (no flat_ frames in the flat library)", \
            "flat_correction must be recorded as the named technique's " \
            "actual outcome: {!r}".format(cap_entry.get("flat_correction"))
        assert cap_entry.get("dark_correction") == \
            "skipped (no standalone dark_ frames)", \
            "dark_correction must be recorded as the named technique's " \
            "actual outcome: {!r}".format(cap_entry.get("dark_correction"))
        assert cap_entry.get("raw_discarded") is False, \
            "Keep RAW Images defaults on -- raw_discarded must be recorded " \
            "False, not just absent, so a reader never has to guess"
        assert (ap_session.dir / "science_frame_0000.tif").exists(), \
            "Keep RAW Images on: raw frames must survive processing"
        shutil.rmtree(ap_session.dir, ignore_errors=True)
        shutil.rmtree(ap_session.prov_dir, ignore_errors=True)
        print("auto-processing check PASS: _auto_process runs immediately with "
              "no Yes/No gate, a real hdr_from_session.py subprocess run "
              "produces a display image, and flat/dark correction status is "
              "parsed back out of its stdout and persisted onto the capture's "
              "own session.json entry as the named technique's actual outcome, "
              "never a generic 'processing complete'; Keep RAW Images on "
              "(the default) leaves raw_discarded explicitly False and the "
              "raw frames themselves in place")

        # Keep RAW Images OFF: raw frames + the linear master must be
        # deleted once processing succeeds, and session.json must record
        # the discard as deliberate (raw_discarded=True + a stated reason) --
        # never a silent gap that could be mistaken for corruption.
        save_pref("keep_raw_images", False)
        try:
            kr_session = provenance.Session(provenance.OUT_ROOT, {}, [])
            kr_result = win.camera.capture_burst(kr_session.dir, "science_", 2)
            kr_idx = provenance.record_burst(kr_session, "science", "science_", kr_result)
            win._session = kr_session
            win._auto_process("science", kr_idx)
            kr_deadline = time.time() + 20.0
            while win._capturing and time.time() < kr_deadline:
                qtapp.processEvents()
                time.sleep(0.01)
            assert not win._capturing, "auto-processing must finish within the deadline"
            assert "processed" in win.capture_status.text().lower(), \
                "Keep RAW Images off must still report a successful processing " \
                "run, not a failure: {!r}".format(win.capture_status.text())
            assert not any(kr_session.dir.glob("science_frame_*.tif")), \
                "Keep RAW Images off must delete this capture's own raw frames"
            assert not (kr_session.dir / "single_master.tif").exists(), \
                "Keep RAW Images off must delete the linear master too"
            assert (kr_session.dir / "final_display.tif").exists(), \
                "Keep RAW Images off must NEVER touch the processed result itself"
            on_disk_kr = json.loads((kr_session.prov_dir / "session.json").read_text())
            kr_entry = on_disk_kr["captures"][kr_idx]
            assert kr_entry.get("raw_discarded") is True, \
                "session.json must record the discard, not leave it silent"
            assert kr_entry.get("raw_discard_reason"), \
                "the discard must be recorded as DELIBERATE, with a reason -- " \
                "a later reader must be able to tell 'chose not to keep' from " \
                "'a file is missing'"
        finally:
            save_pref("keep_raw_images", True)
            shutil.rmtree(kr_session.dir, ignore_errors=True)
            shutil.rmtree(kr_session.prov_dir, ignore_errors=True)
        print("Keep RAW Images check PASS: off deletes this capture's own raw "
              "frames + linear master once processing succeeds, never the "
              "processed result itself, and session.json records the discard "
              "as deliberate with a stated reason, never a silent gap")

        # Additional export formats (Part 03, lifted from casual_mode.py,
        # TIFF genuinely optional since the debayer.py tonemap/write split):
        # TIFF off + PNG off + JPG on + DNG on (merged) must reach
        # hdr_from_session.py as real --no-export-tiff/--no-export-png/
        # --export-jpg/--export-dng/--export-dng-merge flags and produce
        # exactly the files those flags promise -- "whatever's checked is
        # what gets written, full stop" now genuinely holds for all three
        # display formats, not just PNG/JPG.
        save_pref("export_format_tiff", False)
        save_pref("export_format_png", False)
        save_pref("export_format_jpg", True)
        save_pref("export_format_dng", True)
        save_pref("export_format_dng_merge", True)
        try:
            ef_session = provenance.Session(provenance.OUT_ROOT, {}, [])
            ef_result = win.camera.capture_burst(ef_session.dir, "science_", 2)
            ef_idx = provenance.record_burst(ef_session, "science", "science_", ef_result)
            win._session = ef_session
            win._auto_process("science", ef_idx)
            ef_deadline = time.time() + 20.0
            while win._capturing and time.time() < ef_deadline:
                qtapp.processEvents()
                time.sleep(0.01)
            assert not win._capturing, "auto-processing must finish within the deadline"
            assert "processed" in win.capture_status.text().lower(), \
                "export-format processing must still report success: {!r}".format(
                    win.capture_status.text())
            assert not (ef_session.dir / "final_display.tif").exists(), \
                "TIFF unchecked must mean TIFF is never produced at all -- " \
                "the old structural-byproduct lock is gone"
            assert not (ef_session.dir / "final_display.png").exists(), \
                "PNG unchecked must mean PNG is never produced at all, not " \
                "produced-then-deleted"
            assert (ef_session.dir / "final_display.jpg").exists(), \
                "JPG checked must produce final_display.jpg"
            assert (ef_session.dir / "science_raw.tif").exists(), \
                "DNG + Process DNG merge checked must produce the merged " \
                "raw-domain deliverable, honestly named .tif (never .dng)"
            # Content, not just the name, must match the MERGED master --
            # single_master.tif already got deleted by Keep RAW Images
            # default-on's own "keep it" behavior here (Keep RAW Images
            # wasn't touched in this block, still on), so it's still on
            # disk to compare against.
            assert (ef_session.dir / "science_raw.tif").read_bytes() == \
                (ef_session.dir / "single_master.tif").read_bytes(), \
                "Process DNG merge checked must copy the MERGED master's " \
                "actual bytes, not just produce a same-named placeholder"

            # DNG checked WITHOUT Process DNG merge: the deliverable must be
            # the first untouched raw frame instead -- same checkbox, same
            # session, opposite merge state, to prove the branch really
            # switches on the checkbox rather than always doing one thing.
            save_pref("export_format_dng_merge", False)
            (ef_session.dir / "science_raw.tif").unlink()
            ef_idx2 = provenance.record_burst(
                ef_session, "science", "science2_",
                win.camera.capture_burst(ef_session.dir, "science2_", 2))
            win._auto_process("science", ef_idx2)
            ef_deadline2 = time.time() + 20.0
            while win._capturing and time.time() < ef_deadline2:
                qtapp.processEvents()
                time.sleep(0.01)
            assert not win._capturing
            assert (ef_session.dir / "science2_raw.tif").read_bytes() == \
                (ef_session.dir / "science2_frame_0000.tif").read_bytes(), \
                "DNG checked WITHOUT Process DNG merge must copy the FIRST " \
                "RAW FRAME's actual bytes untouched, not the merged master"
        finally:
            for k in ("export_format_tiff", "export_format_png", "export_format_jpg",
                     "export_format_dng", "export_format_dng_merge"):
                save_pref(k, {"export_format_tiff": True, "export_format_png": True,
                             "export_format_jpg": True, "export_format_dng": False,
                             "export_format_dng_merge": False}[k])
            shutil.rmtree(ef_session.dir, ignore_errors=True)
            shutil.rmtree(ef_session.prov_dir, ignore_errors=True)
        print("export-format check PASS: Preferences > Advanced's TIFF/PNG/JPG/DNG "
              "settings reach hdr_from_session.py as real CLI flags and "
              "produce exactly the promised files -- TIFF and PNG unchecked "
              "both mean never produced (not produced-then-deleted; TIFF's "
              "old structural-byproduct lock is gone), JPG and a merged DNG "
              "deliverable land exactly when checked")

        # --- MEASURE MENU (separable): _launch_measure opens a real
        # measure.MeasureWindow, same "raise the existing one, don't open a
        # second" contract _launch_calibrate already has (untested until
        # now, since _launch_calibrate itself has no render_check coverage
        # either -- this fills that gap for the new Measure action).
        if _measure is None:
            print("_launch_measure check SKIPPED: measure.py not importable here")
        else:
            mcam = FakeCamera()
            mwin = FocusPreviewWindow(mcam, FocusMeter())
            assert mwin._measure_action.isEnabled()
            mwin.ruler_objective_combo.setCurrentText("40x")
            assert getattr(mwin, "_measure_window", None) is None
            mwin._launch_measure()
            assert mwin._measure_window is not None and mwin._measure_window.isVisible()
            assert mwin._measure_window.objective_combo.currentText() == "40x", \
                "the ruler's own objective should pre-fill MeasureWindow's combo"
            first = mwin._measure_window
            mwin._launch_measure()
            assert mwin._measure_window is first, \
                "a second trigger while the window is open must reuse it, not " \
                "open a duplicate"
            mwin._measure_window.close()
            mcam.stop()
            print("_launch_measure check PASS: menu action enabled, opens a "
                  "real MeasureWindow pre-filled from the ruler's objective, "
                  "a second trigger reuses the existing window rather than "
                  "opening a duplicate")

        # --- LIVE MEASURE PANEL (Preferences-dialog plan set, Part 05) ------
        # "Verification, planned" (HANDOFF.md's own Part 05 section) lists
        # exactly what this must prove, self-check-only (no rig access here):
        # the click's own coordinates convert to the plane's real native
        # pixel coordinates; freezing happens exactly once per panel session
        # and the hash is stable across later clicks; a commit writes to a
        # temp-redirected annotations.json keyed to the frozen plane's real
        # hash; closing discards uncommitted marks and keeps committed ones;
        # Point is greyed on a miss and live on a hit; Delete never touches a
        # committed mark; the three pen states are visually distinct.
        if _measure is None or _annotations is None:
            print("Live measure panel check SKIPPED: measure.py and/or "
                  "annotations.py not importable here")
        else:
            # native_point_from_preview_click: asserted on the real converted
            # values, not just "a mark exists" -- this is the claim the whole
            # freeze design rests on. An arbitrary fixed disp_rect makes a
            # hand-computed expectation easy to check independently.
            rect = (10, 0, 100, 50)
            nx, ny = native_point_from_preview_click(60, 25, rect, GREEN_PLANE_RES)
            fx, fy = frac_from_point(60, 25, rect)
            assert (nx, ny) == (fx * GREEN_PLANE_RES[0], fy * GREEN_PLANE_RES[1]), \
                "native_point_from_preview_click must scale frac_from_point's " \
                "own fraction into the green plane's real resolution, not " \
                "reimplement the mapping"

            orig_calib_path_lm = _calibrate.CALIBRATION_PATH
            orig_annot_path_lm = _annotations.ANNOTATION_PATH
            _calibrate.CALIBRATION_PATH = Path(
                "/tmp/zynergy_render_check_live_measure_calibration.json")
            _annotations.ANNOTATION_PATH = Path(
                "/tmp/zynergy_render_check_live_measure_annotations.json")
            _calibrate.CALIBRATION_PATH.unlink(missing_ok=True)
            _annotations.ANNOTATION_PATH.unlink(missing_ok=True)
            try:
                calib_entry = _calibrate.build_calibration_entry(
                    Path("/tmp/fake.dng"), (0.0, 0.0), (500.0, 0.0), 500.0,
                    objective="40x", target_type="stage micrometer", focus_score=300.0)
                _calibrate.save_calibration("40x", calib_entry)

                lmcam = FakeCamera(async_delay_s=0.0,
                                   capture_shape=(GREEN_PLANE_RES[1], GREEN_PLANE_RES[0]))
                lmwin = FocusPreviewWindow(lmcam, FocusMeter())
                lmwin.ruler_objective_combo.setCurrentText("40x")
                try:
                    assert lmwin._live_measure_action.isEnabled(), \
                        "the Live measure... action must be enabled when both " \
                        "measure.py and annotations.py are importable"
                    assert lmwin._live_measure_panel is None
                    lmwin._launch_live_measure()
                    assert lmwin._live_measure_panel is not None and \
                        lmwin._live_measure_panel.isVisible()
                    assert lmwin._live_measure_active

                    # a second trigger while open reuses the panel -- same
                    # "raise, don't duplicate" contract every other launcher
                    # in this file already has.
                    first_panel = lmwin._live_measure_panel
                    lmwin._launch_live_measure()
                    assert lmwin._live_measure_panel is first_panel, \
                        "a second trigger while open must reuse the panel, " \
                        "not open a duplicate"

                    lmwin._live_measure_panel.distance_btn.setChecked(True)
                    assert lmwin._live_measure_tool == "distance"

                    # A 4:3-aspect resize (matching LORES_RES's own aspect, so
                    # the mapping below is un-letterboxed and easy to reason
                    # about), at least _FakePreview's own setMinimumSize(480,
                    # 360) in both dimensions or resize() silently clamps to
                    # that floor instead of the requested size -- gives
                    # _disp_rect() real, non-degenerate geometry to map
                    # through. An unshown widget's width()/height() default to
                    # 0, which displayed_rect degenerates to a 1x1 rect,
                    # collapsing any two distinct clicks to the same fraction.
                    lmwin.preview.resize(800, 600)
                    assert lmwin._disp_rect() == (0, 0, 800, 600), \
                        "sanity check: a 4:3 resize on a 4:3-aspect preview " \
                        "should need no letterboxing"

                    # The freeze-triggering click itself, routed through the
                    # REAL eventFilter (not called directly) -- this is what
                    # actually proves the click-repurposing wiring: ordinary
                    # box-drag must never fire while the panel is open.
                    assert lmwin._drag is None
                    press1 = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 300),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    lmwin.eventFilter(lmwin.preview, press1)
                    deadline = time.time() + 15.0
                    while lmwin._live_measure_freezing and time.time() < deadline:
                        qtapp.processEvents()
                        time.sleep(0.005)
                    assert lmwin._drag is None, \
                        "box-drag must never see a click while live measure " \
                        "is active"
                    assert lmwin._live_measure_frozen, "freeze must complete"
                    assert lmwin._preview_stack_layout.currentWidget() is \
                        lmwin._live_measure_canvas, \
                        "a completed freeze must swap the stack to the canvas"
                    first_hash = lmwin._live_measure_pixel_sha256
                    assert first_hash is not None
                    # the freeze-triggering click's own point became the
                    # armed tool's first point -- one pending point already
                    # logged, not a throwaway trigger click.
                    assert len(lmwin._live_measure_canvas._pending_points) == 1
                    first_point = lmwin._live_measure_canvas._pending_points[0]

                    # A stray click still routed to self.preview post-freeze
                    # must be a pure no-op (the defensive _live_measure_frozen
                    # branch _live_measure_preview_event's own docstring
                    # describes) -- freezing happens exactly once per panel
                    # session, the hash stays stable, and nothing about the
                    # in-progress shape changes. Real post-freeze clicks land
                    # on _live_measure_canvas instead (self.preview is no
                    # longer the visible top of the stack, so Qt would never
                    # actually deliver them here on the real widget tree) --
                    # exercised just below via add_point_programmatic, the
                    # same entry point the canvas's own mousePressEvent uses.
                    stray = QMouseEvent(QEvent.MouseButtonPress, QPointF(600, 300),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    lmwin.eventFilter(lmwin.preview, stray)
                    assert lmwin._live_measure_pixel_sha256 == first_hash, \
                        "freezing must happen exactly once per panel session; " \
                        "the hash must stay stable across a later click"
                    assert len(lmwin._live_measure_canvas._pending_points) == 1, \
                        "a stray click still routed to self.preview post-freeze " \
                        "must be a no-op, not a second point"

                    # The real second point: injected the same way canvas's
                    # own mousePressEvent would after mapToScene, at a native
                    # green-plane coordinate distinct from the first point --
                    # completes the 2-point distance shape.
                    second_point = (first_point[0] + 100.0, first_point[1])
                    lmwin._live_measure_canvas.add_point_programmatic(*second_point)
                    assert len(lmwin._live_measure_marks) == 1, \
                        "2 points for a distance mark must auto-finish the shape"
                    entry = lmwin._live_measure_marks[0]
                    assert not entry["committed"]
                    assert entry["items"][0].pen().color().getRgb()[:3] == (255, 140, 0), \
                        "a finished-but-uncommitted mark must draw with UNCOMMITTED_PEN"

                    # Point hit test: a miss (far from the mark) is None; a
                    # hit (near a real segment, converted through the SAME
                    # mapFromScene the production menu handler uses) finds
                    # the entry.
                    miss = lmwin._live_measure_hit_test((5, 5), lmwin._live_measure_canvas)
                    assert miss is None, "a click far from any mark must miss"
                    pt_a = entry["mark"]["input"]["points"][0]
                    view_a = lmwin._live_measure_canvas.mapFromScene(QPointF(*pt_a))
                    hit = lmwin._live_measure_hit_test(
                        (view_a.x(), view_a.y()), lmwin._live_measure_canvas)
                    assert hit is entry, "a click on a real mark's own geometry must hit it"

                    # Commit writes to the temp-redirected annotations.json,
                    # keyed to the frozen plane's real hash -- not a hand-fed
                    # store, the real save_mark call through the real handler.
                    lmwin._live_measure_commit_entry(entry)
                    assert entry["committed"]
                    assert entry["items"][0].pen().color().getRgb()[:3] == (80, 220, 255), \
                        "a committed mark must draw with COMMITTED_PEN"
                    stored = _annotations.load_annotations()
                    assert first_hash in stored and len(stored[first_hash]["marks"]) == 1, \
                        "commit must write to the real annotations store, " \
                        "keyed to the frozen plane's own pixel_sha256"

                    # Commit round trip (MeasureWindow extraction, step 2):
                    # the mark just committed through THIS panel's real click/
                    # commit dispatch must resolve, by pixel_sha256 alone, in
                    # measure.ReviewWindow -- the new recall/review capability
                    # Part 05's own design assumed measure.py would keep
                    # providing. Never previously confirmed end to end; this
                    # is that confirmation. Reuses this block's own frozen
                    # plane, hash, and already-committed mark -- no parallel
                    # fixture, same "reach the code the way the app does"
                    # discipline as the freeze click above.
                    if _measure is None:
                        print("commit round-trip check SKIPPED: measure.py "
                              "not importable here")
                    else:
                        committed_mark = stored[first_hash]["marks"][0]
                        import tifffile as _rc_tifffile
                        rc_tif = Path(
                            "/tmp/zynergy_render_check_commit_roundtrip_plane.tif")
                        _rc_tifffile.imwrite(str(rc_tif), lmwin._live_measure_plane)
                        try:
                            review_win = _measure.ReviewWindow()
                            review_win._load_image(str(rc_tif))
                            assert review_win._pixel_sha256 == first_hash, \
                                "the same plane opened through two different " \
                                "windows must hash identically"
                            record_via_review = _annotations.image_record_for(
                                review_win._pixel_sha256)
                            assert record_via_review is not None
                            assert record_via_review["marks"][0] == committed_mark, \
                                "the exact mark Part 05's panel committed " \
                                "must be the one ReviewWindow resolves"
                        finally:
                            rc_tif.unlink(missing_ok=True)
                        print("commit round-trip check PASS: a mark committed "
                              "through Part 05's real "
                              "_live_measure_finish_points/"
                              "_live_measure_commit_entry path resolves by "
                              "pixel_sha256 in measure.ReviewWindow, exact "
                              "mark match")

                    # Delete never touches a committed mark (the store never
                    # deletes) -- a no-op, not silently swallowed into
                    # looking like it worked.
                    lmwin._live_measure_delete_entry(entry)
                    assert entry in lmwin._live_measure_marks, \
                        "Delete must be a no-op against an already-committed mark"

                    # Closing discards uncommitted marks; the one already
                    # committed above landed durably in annotations.json and
                    # is unaffected by anything close does.
                    lmwin._live_measure_panel.close()
                    assert lmwin._live_measure_marks == [], \
                        "closing must discard every in-memory mark entry"
                    assert not lmwin._live_measure_active
                    assert not lmwin._live_measure_frozen
                    assert lmwin._preview_stack_layout.currentWidget() is lmwin.preview, \
                        "closing must restore the live preview"
                    stored_after_close = _annotations.load_annotations()
                    assert first_hash in stored_after_close and \
                        len(stored_after_close[first_hash]["marks"]) == 1, \
                        "closing must not touch a mark that was already committed"
                finally:
                    lmcam.stop()
            finally:
                _calibrate.CALIBRATION_PATH = orig_calib_path_lm
                _annotations.ANNOTATION_PATH = orig_annot_path_lm
            print("Live measure panel check PASS: native_point_from_preview_click "
                  "scales the real preview-to-sensor fraction into the green "
                  "plane's actual resolution; the freeze-triggering click routes "
                  "through the real eventFilter and suppresses ordinary box-drag; "
                  "freezing happens exactly once per panel session (hash stable "
                  "across a later click); a finished 2-point shape auto-holds in "
                  "memory with the uncommitted pen; Point hit-test misses empty "
                  "space and finds a real mark by its own geometry; commit writes "
                  "to the real, temp-redirected annotations store keyed to the "
                  "frozen plane's actual hash and flips the pen to the committed "
                  "color; Delete is a no-op against a committed mark; closing "
                  "discards every uncommitted entry, restores the live preview, "
                  "and never touches a mark already committed")

            # --- Freeze-fix regression coverage (freeze-on-first-click) ------
            # PLAN_live_measure_freeze_fix.md's own five cases. Each gets a
            # fresh camera/window so a failure in one cannot mask a bug in
            # another:
            #   1. _calibrate is None -> fails clean, mode is not bricked.
            #   2. set_image raises -> same postconditions; the direct
            #      regression test for the reported freeze-forever bug.
            #   3. happy path -> the triggering click's own point lands as
            #      the frozen canvas's first pending point.
            #   4. no tool armed -> no capture at all, click still consumed.
            #   5. _capturing lifecycle on the two exit paths not already
            #      proven by cases 1-3 (freeze failure, load failure, and a
            #      synchronous capture_still_async raise).
            def _fresh_live_measure_window():
                cam = FakeCamera(async_delay_s=0.0,
                                  capture_shape=(GREEN_PLANE_RES[1], GREEN_PLANE_RES[0]))
                win = FocusPreviewWindow(cam, FocusMeter())
                win.ruler_objective_combo.setCurrentText("40x")
                win._launch_live_measure()
                win._live_measure_panel.distance_btn.setChecked(True)
                win.preview.resize(800, 600)
                return cam, win

            def _pump_until_not_freezing(win, timeout_s=15.0):
                deadline = time.time() + timeout_s
                while win._live_measure_freezing and time.time() < deadline:
                    qtapp.processEvents()
                    time.sleep(0.005)

            orig_calib_path_ff = _calibrate.CALIBRATION_PATH
            orig_annot_path_ff = _annotations.ANNOTATION_PATH
            _calibrate.CALIBRATION_PATH = Path(
                "/tmp/zynergy_render_check_live_measure_freeze_fix_calibration.json")
            _annotations.ANNOTATION_PATH = Path(
                "/tmp/zynergy_render_check_live_measure_freeze_fix_annotations.json")
            _calibrate.CALIBRATION_PATH.unlink(missing_ok=True)
            _annotations.ANNOTATION_PATH.unlink(missing_ok=True)
            try:
                calib_entry_ff = _calibrate.build_calibration_entry(
                    Path("/tmp/fake_freeze_fix.dng"), (0.0, 0.0), (500.0, 0.0), 500.0,
                    objective="40x", target_type="stage micrometer", focus_score=300.0)
                _calibrate.save_calibration("40x", calib_entry_ff)

                # Case 1: _calibrate is None.
                cam1, win1 = _fresh_live_measure_window()
                try:
                    real_calibrate_ff = globals()['_calibrate']
                    globals()['_calibrate'] = None
                    try:
                        press = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 300),
                                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                        win1.eventFilter(win1.preview, press)
                        _pump_until_not_freezing(win1)
                    finally:
                        globals()['_calibrate'] = real_calibrate_ff
                    assert not win1._live_measure_frozen, \
                        "a _calibrate-is-None freeze must not set _live_measure_frozen"
                    assert win1._preview_stack_layout.currentWidget() is win1.preview, \
                        "the live preview must stay the visible stack widget on failure"
                    assert win1.capture_status.text() == "Live measure unavailable", \
                        "status must report the real unavailable reason"
                    assert not win1._capturing, \
                        "_capturing must clear after a _calibrate-is-None failure"

                    # Not bricked: _calibrate is restored above, so a later
                    # click must still be able to complete a real freeze.
                    press2 = QMouseEvent(QEvent.MouseButtonPress, QPointF(210, 300),
                                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    win1.eventFilter(win1.preview, press2)
                    _pump_until_not_freezing(win1)
                    assert win1._live_measure_frozen, \
                        "a subsequent click must still complete a real freeze -- " \
                        "the failure above must not have bricked the mode"
                finally:
                    cam1.stop()
                print("Live measure freeze-fix check PASS (case 1): a "
                      "_calibrate-is-None freeze fails cleanly -- frozen stays "
                      "False, the live preview stays the visible stack widget, "
                      "status reports the real reason, _capturing clears, and a "
                      "later click still completes a real freeze")

                # Case 2: set_image raises -- the direct regression test.
                cam2, win2 = _fresh_live_measure_window()
                try:
                    def _raising_set_image(pixmap):
                        raise RuntimeError("forced set_image failure (render-check)")
                    real_set_image = win2._live_measure_canvas.set_image
                    win2._live_measure_canvas.set_image = _raising_set_image
                    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 300),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    win2.eventFilter(win2.preview, press)
                    _pump_until_not_freezing(win2)
                    assert not win2._live_measure_frozen, \
                        "a set_image failure must not set _live_measure_frozen -- " \
                        "this is the direct regression case for the reported bug"
                    assert win2._preview_stack_layout.currentWidget() is win2.preview, \
                        "the live preview must stay the visible stack widget on failure"
                    assert win2.capture_status.text() == "Live measure freeze failed", \
                        "status must report the freeze failure"
                    assert not win2._capturing, \
                        "_capturing must clear after a set_image failure"

                    win2._live_measure_canvas.set_image = real_set_image
                    press2 = QMouseEvent(QEvent.MouseButtonPress, QPointF(210, 300),
                                         Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    win2.eventFilter(win2.preview, press2)
                    _pump_until_not_freezing(win2)
                    assert win2._live_measure_frozen, \
                        "a later click must still complete a real freeze"
                finally:
                    cam2.stop()
                print("Live measure freeze-fix check PASS (case 2): a set_image "
                      "failure fails cleanly with the same postconditions as "
                      "case 1 -- the mode is never bricked by a failed swap")

                # Case 3: happy path registers the triggering click as point 1.
                cam3, win3 = _fresh_live_measure_window()
                try:
                    click_x, click_y = 250.0, 320.0
                    expected = native_point_from_preview_click(
                        click_x, click_y, win3._disp_rect(), GREEN_PLANE_RES)
                    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(click_x, click_y),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    win3.eventFilter(win3.preview, press)
                    _pump_until_not_freezing(win3)
                    assert win3._live_measure_frozen
                    assert len(win3._live_measure_canvas._pending_points) == 1, \
                        "a successful freeze with a tool armed must register " \
                        "the triggering click as the shape's first point"
                    assert win3._live_measure_canvas._pending_points[0] == expected, \
                        "the registered point must equal the triggering click's " \
                        "own converted coordinate, never dropped or substituted"
                finally:
                    cam3.stop()
                print("Live measure freeze-fix check PASS (case 3): the "
                      "freeze-triggering click's own converted coordinate is "
                      "always registered as the frozen canvas's first point")

                # Case 4: no tool selected -- no capture, click still consumed.
                cam4, win4 = _fresh_live_measure_window()
                try:
                    win4._live_measure_tool = None
                    win4._live_measure_panel.set_status("")
                    calls = []
                    real_capture = win4.camera.capture_still_async
                    def _counting_capture(*a, **kw):
                        calls.append(1)
                        return real_capture(*a, **kw)
                    win4.camera.capture_still_async = _counting_capture
                    press = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 300),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                    consumed = win4.eventFilter(win4.preview, press)
                    assert consumed is True, \
                        "a click with no tool armed must still be consumed, " \
                        "never fall through to ordinary box-drag"
                    assert calls == [], \
                        "a click with no tool armed must never start a capture"
                    assert not win4._live_measure_freezing
                    assert not win4._live_measure_frozen
                    assert win4._live_measure_panel.status_label.text() == \
                        _live_measure_tool_hint(None), \
                        "status must prompt for a tool"
                finally:
                    cam4.stop()
                print("Live measure freeze-fix check PASS (case 4): a click "
                      "with no tool armed starts no capture, is still "
                      "consumed, and prompts for a tool")

                # Case 5: _capturing lifecycle on the remaining exit paths
                # (success and swap-failure already proven by cases 1-3 above).
                cam5, win5 = _fresh_live_measure_window()
                try:
                    # freeze failure: the delivered result is itself an Exception.
                    win5._capturing = True
                    win5._live_measure_freezing = True
                    win5._on_live_measure_freeze_done(RuntimeError("forced (render-check)"))
                    assert not win5._capturing, \
                        "_capturing must clear on a freeze failure"

                    # load failure: measure.load_measurement_plane raises.
                    real_load_plane = _measure.load_measurement_plane
                    def _raising_load_plane(*a, **kw):
                        raise RuntimeError("forced load failure (render-check)")
                    _measure.load_measurement_plane = _raising_load_plane
                    try:
                        press = QMouseEvent(QEvent.MouseButtonPress, QPointF(200, 300),
                                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
                        win5.eventFilter(win5.preview, press)
                        _pump_until_not_freezing(win5)
                    finally:
                        _measure.load_measurement_plane = real_load_plane
                    assert not win5._live_measure_frozen
                    assert not win5._capturing, \
                        "_capturing must clear on a load failure"

                    # synchronous capture_still_async raise, before any worker
                    # ever starts.
                    win5._live_measure_frozen = False
                    def _raising_capture(*a, **kw):
                        raise RuntimeError("forced sync capture failure (render-check)")
                    win5.camera.capture_still_async = _raising_capture
                    native_ff = native_point_from_preview_click(
                        200, 300, win5._disp_rect(), GREEN_PLANE_RES)
                    win5._live_measure_freeze(native_ff)
                    assert not win5._capturing, \
                        "_capturing must clear when capture_still_async raises " \
                        "synchronously, before any worker starts"
                finally:
                    cam5.stop()
                print("Live measure freeze-fix check PASS (case 5): _capturing "
                      "is set while a freeze is in flight and clears on every "
                      "exit path -- freeze failure, load failure, and a "
                      "synchronous capture_still_async raise (success and "
                      "swap-failure covered by cases 1-3 above)")
            finally:
                _calibrate.CALIBRATION_PATH = orig_calib_path_ff
                _annotations.ANNOTATION_PATH = orig_annot_path_ff

            # --- Frozen-canvas fit coverage (PLAN_live_measure_canvas_fit) ---
            # Direct _LiveMeasureCanvas unit tests, not routed through a full
            # FocusPreviewWindow -- window_ is untouched by set_image/
            # resizeEvent/showEvent/_fit_to_view/wheelEvent, so None stands
            # in for it here, same "test at the level the bug actually lives
            # at" reasoning the rest of this file already follows.
            qtapp_fit = QApplication.instance() or QApplication([])

            def _fit_expected_scale(view, pixmap):
                vp = view.viewport().size()
                return min(vp.width() / float(pixmap.width()),
                           vp.height() / float(pixmap.height()))

            fit_plane = np.zeros((GREEN_PLANE_RES[1], GREEN_PLANE_RES[0]), dtype=np.float32)
            fit_pixmap_a = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(fit_plane))
            fit_pixmap_b = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(fit_plane))

            # Case 1: first-show fit -- the direct regression test for the
            # reported thumbnail bug. set_image runs before the canvas has
            # ever had real geometry (never resized or shown), matching
            # _on_live_measure_freeze_done's own real ordering (set_image
            # before the stack-layout swap makes this canvas the current,
            # laid-out widget) -- then real geometry arrives via resize+show,
            # and resizeEvent/showEvent must refit rather than leaving the
            # stale pre-layout transform in place forever.
            fit_canvas1 = _LiveMeasureCanvas(None)
            fit_canvas1.set_image(fit_pixmap_a)
            transform_before_layout = (fit_canvas1.transform().m11(),
                                       fit_canvas1.transform().m22())
            fit_canvas1.resize(800, 600)
            fit_canvas1.show()
            qtapp_fit.processEvents()
            transform_after_layout = (fit_canvas1.transform().m11(),
                                      fit_canvas1.transform().m22())
            assert transform_before_layout != transform_after_layout, \
                "resizeEvent/showEvent must refit once real geometry arrives " \
                "-- retaining the pre-layout transform forever is the exact " \
                "reported bug (a small thumbnail on the very first freeze)"
            expected_800x600 = _fit_expected_scale(fit_canvas1, fit_pixmap_a)
            assert abs(transform_after_layout[0] - expected_800x600) < 0.05 * expected_800x600, \
                "once real geometry arrives, the transform must actually fit " \
                "the pixmap to the real viewport, not merely differ from the bad one"
            print("Live measure canvas-fit check PASS (case 1, first-show fit): "
                  "a canvas laid out with real geometry only AFTER set_image "
                  "(the freeze handler's own real ordering) refits correctly "
                  "once resizeEvent/showEvent deliver that geometry, instead "
                  "of keeping the pre-layout thumbnail transform forever")

            # Case 2: repeat freeze still fits -- guards against the fix
            # regressing the path that was already working (second and later
            # freezes, per the reported symptom, were never actually broken).
            fit_canvas1.set_image(fit_pixmap_b)
            transform_repeat = fit_canvas1.transform().m11()
            assert abs(transform_repeat - expected_800x600) < 0.05 * expected_800x600, \
                "a second set_image on an already-laid-out canvas must fit " \
                "immediately, without waiting for a further resize/show event"
            print("Live measure canvas-fit check PASS (case 2, repeat freeze): "
                  "a second set_image on an already-laid-out canvas fits " \
                  "immediately -- the already-working repeat-freeze path is " \
                  "unaffected by the fix")

            # Case 3: user zoom survives a resize -- auto-refitting on every
            # resize must not fight a manual wheelEvent zoom.
            class _FitFakeAngleDelta:
                def y(self):
                    return 120
            class _FitFakeWheelEvent:
                def angleDelta(self):
                    return _FitFakeAngleDelta()
            fit_canvas1.wheelEvent(_FitFakeWheelEvent())
            assert fit_canvas1._user_zoomed is True, \
                "wheelEvent must record that the user has taken manual control of the zoom"
            transform_after_zoom = (fit_canvas1.transform().m11(), fit_canvas1.transform().m22())
            fit_canvas1.resize(750, 550)
            qtapp_fit.processEvents()
            transform_after_resize_post_zoom = (
                fit_canvas1.transform().m11(), fit_canvas1.transform().m22())
            assert transform_after_resize_post_zoom == transform_after_zoom, \
                "a resize after a manual zoom must NOT refit -- that would " \
                "silently yank the user's own zoom away on the next window resize"
            print("Live measure canvas-fit check PASS (case 3, zoom survives "
                  "resize): a manual wheelEvent zoom is preserved across a "
                  "later resize, not overridden by auto-fit")

            # Case 4: a new set_image re-enables auto-fit -- a freshly frozen
            # plane is a new view, not a continuation of the previous zoom.
            fit_canvas1.set_image(fit_pixmap_a)
            assert fit_canvas1._user_zoomed is False, \
                "a new set_image must clear _user_zoomed so the fresh plane " \
                "gets auto-fit again"
            expected_750x550 = _fit_expected_scale(fit_canvas1, fit_pixmap_a)
            assert abs(fit_canvas1.transform().m11() - expected_750x550) < 0.05 * expected_750x550, \
                "a new set_image must actually re-fit to the current " \
                "viewport, not merely clear the flag"
            fit_canvas1.close()
            print("Live measure canvas-fit check PASS (case 4, new image "
                  "re-fits): a new set_image clears the manual-zoom flag and "
                  "actually re-fits the fresh plane to the current viewport")

            # Case 5: measurements are transform-independent -- phase 1's own
            # "not a bug" finding, locked in so a future reader doesn't go
            # looking for a calibration/scale bug behind an imprecise click.
            # A click's pixel-to-scene conversion (mapToScene, exercised by
            # cases 1-4's own real fit above) happens ONCE, at click time;
            # what's actually stored afterward (_pending_points, then a
            # committed mark's "input") is a plain scene-space coordinate
            # that build_distance_mark/annotations never re-derive from the
            # view. This proves that downstream stage directly: the exact
            # coordinate add_point_programmatic is handed (already-converted,
            # the same contract _on_live_measure_freeze_done and
            # mousePressEvent both rely on) is stored byte-identical and
            # produces an identical um reading, regardless of what the
            # canvas's OWN zoom happens to be when that storage/measurement
            # step runs -- an imprecise on-rig reading is therefore a
            # property of the CLICK (bounded by how many scene units one
            # screen pixel covers at the canvas's current fit/zoom), never a
            # scale or calibration bug introduced by measuring it afterward.
            class _FitFakeWindow:
                _live_measure_tool = None   # None: no auto-finish, just record
                def _live_measure_on_point_added(self, points):
                    pass
            fit_canvas5 = _LiveMeasureCanvas(_FitFakeWindow())
            fit_canvas5.resize(400, 300)
            fit_canvas5.show()
            qtapp_fit.processEvents()
            fit_canvas5.set_image(fit_pixmap_a)
            qtapp_fit.processEvents()
            point_a = (1000.0, 1200.0)
            point_b = (1500.0, 1200.0)

            fit_canvas5.add_point_programmatic(*point_a)
            recorded_a_at_fit_zoom = fit_canvas5._pending_points[-1]
            transform_at_recording_a = fit_canvas5.transform().m11()

            fit_canvas5.scale(2.0, 2.0)   # a manual zoom change, same as wheelEvent would
            assert fit_canvas5.transform().m11() != transform_at_recording_a, \
                "sanity check: scale() must actually change the canvas's " \
                "own transform, or this test would prove nothing"
            fit_canvas5.add_point_programmatic(*point_b)
            recorded_b_at_2x_zoom = fit_canvas5._pending_points[-1]

            assert recorded_a_at_fit_zoom == point_a and recorded_b_at_2x_zoom == point_b, \
                "add_point_programmatic must store the EXACT scene " \
                "coordinate it is handed, unaffected by the canvas's " \
                "current zoom at the moment it is recorded"

            mark_at_fit_zoom = _annotations.build_distance_mark(point_a, point_b, 0.5)
            fit_canvas5.scale(1.0 / 2.0, 1.0 / 2.0)   # back to the fitted zoom
            mark_at_original_zoom = _annotations.build_distance_mark(point_a, point_b, 0.5)
            assert (mark_at_fit_zoom["derived"]["distance_um"] ==
                   mark_at_original_zoom["derived"]["distance_um"]), \
                "the same two recorded scene points must produce the " \
                "identical um reading regardless of the canvas's zoom at " \
                "measurement time -- build_distance_mark operates on scene " \
                "coordinates alone and never reads the view transform"
            fit_canvas5.close()
            print("Live measure canvas-fit check PASS (case 5, transform-"
                  "independent measurement): add_point_programmatic stores "
                  "the exact scene coordinate it is handed regardless of the "
                  "canvas's current zoom, and the resulting um reading for "
                  "the same two points is identical across two different "
                  "zoom levels -- locks in that an imprecise on-rig reading "
                  "is a property of the click, never a calibration or scale "
                  "bug introduced by measuring it")
            # --- end frozen-canvas fit coverage -------------------------------
        # --- end live measure panel check ------------------------------------

        # --- LIVE MEASURING (PLAN_quick_ruler.md) ----------------------------
        # A pixel-only overlay on the LIVE, moving feed -- no freeze, no
        # calibration, nothing committed. Proves: the module-boundary
        # self-check actually runs clean (it is otherwise never called from
        # anywhere -- an assertion nobody runs is not a guard); the panel
        # opens/reuses the same way every other launcher in this file does;
        # it is mutually exclusive with Measure's own live panel (Part 05) in
        # BOTH directions; a real click through the REAL eventFilter converts
        # to the correct LORES_RES-space point and suppresses ordinary
        # box-drag; distance/angle auto-finish at their own point count while
        # polygon needs an explicit double-click at or past its minimum (and
        # NOT before it); Escape cancels an in-progress shape without
        # touching a finished one; the overlay push actually reaches
        # camera.set_overlay with the aid off; the hit test misses empty
        # space and finds a real mark by its own geometry; Delete Point/All
        # really mutate the mark list; closing discards everything, since
        # nothing here is ever durable.
        assert_live_measuring_has_no_calibration_dependency()

        lqcam = FakeCamera()
        lqwin = FocusPreviewWindow(lqcam, FocusMeter())
        try:
            assert lqwin._live_measuring_action.isEnabled(), \
                "Live Measuring must always be enabled -- it has no " \
                "measure.py/annotations.py dependency that could be missing"
            assert lqwin._live_measuring_panel is None
            lqwin._launch_live_measuring()
            assert lqwin._live_measuring_panel is not None and \
                lqwin._live_measuring_panel.isVisible()
            assert lqwin._live_measuring_active

            first_panel = lqwin._live_measuring_panel
            lqwin._launch_live_measuring()
            assert lqwin._live_measuring_panel is first_panel, \
                "a second trigger while open must reuse the panel, not " \
                "open a duplicate"

            # Mutual exclusion with Measure's own live panel (Part 05), both
            # directions -- both repurpose self.preview's clicks, so only one
            # may hold them at a time. Neither call here ever clicks the
            # preview, so no freeze thread is ever started -- this is purely
            # about the open/close guard, not Part 05's own capture path.
            if _measure is not None and _annotations is not None:
                lqwin._launch_live_measure()
                assert lqwin._live_measure_active
                assert not lqwin._live_measuring_active, \
                    "opening Measure's own live panel must close Live " \
                    "Measuring first"
                lqwin._live_measure_panel.close()
                assert not lqwin._live_measure_active

                lqwin._launch_live_measuring()
                assert lqwin._live_measuring_active
                lqwin._launch_live_measure()
                assert lqwin._live_measure_active
                assert not lqwin._live_measuring_active, \
                    "opening Live Measuring must close Measure's own live " \
                    "panel first -- the same guard, in the other direction"
                lqwin._live_measure_panel.close()
                assert not lqwin._live_measure_active
                lqwin._launch_live_measuring()
                assert lqwin._live_measuring_active

            # A 4:3 resize, matching LORES_RES's own aspect exactly, so the
            # mapping below needs no letterboxing -- same trick Part 05's own
            # check uses for GREEN_PLANE_RES.
            lqwin.preview.resize(800, 600)
            assert lqwin._disp_rect() == (0, 0, 800, 600)

            def expected_lores_point(px, py):
                # Computed via the SAME frac_from_point primitive production
                # code uses, not a hand-typed literal -- so this compares
                # against the real mapping's own arithmetic, not a value that
                # merely looks plausible.
                fx, fy = frac_from_point(px, py, (0, 0, 800, 600))
                return (fx * LORES_RES[0], fy * LORES_RES[1])

            def click(x, y, kind=QEvent.MouseButtonPress):
                ev = QMouseEvent(kind, QPointF(x, y), Qt.LeftButton,
                                 Qt.LeftButton, Qt.NoModifier)
                assert lqwin.eventFilter(lqwin.preview, ev), \
                    "Live Measuring must consume every click on the " \
                    "preview while active, never let it fall through to " \
                    "ordinary box-drag"

            # Distance: select the tool via the real panel button (the same
            # QButtonGroup toggle path a user's own click drives), then two
            # real clicks routed through the real eventFilter.
            lqwin._live_measuring_panel.distance_btn.setChecked(True)
            assert lqwin._live_measuring_tool == "distance"
            assert lqwin._drag is None
            click(160, 300)
            assert lqwin._drag is None, \
                "box-drag must never see a click while Live Measuring is active"
            assert lqwin._live_measuring_pending_points == [expected_lores_point(160, 300)], \
                "the click's own preview coordinates must convert through " \
                "the real frac_from_point mapping into LORES_RES-space"
            click(480, 300)
            assert lqwin._live_measuring_pending_points == [], \
                "2 points for a distance shape must auto-finish and clear " \
                "the pending list"
            assert len(lqwin._live_measuring_marks) == 1
            assert lqwin._live_measuring_marks[0]["type"] == "distance"
            assert lqwin._live_measuring_marks[0]["points"] == \
                [expected_lores_point(160, 300), expected_lores_point(480, 300)]

            # The overlay push actually happened -- aid is off by default, so
            # _live_measuring_notify_changed must have pushed directly to
            # camera.set_overlay rather than waiting on a tick loop that
            # isn't running (same rule _on_ruler_changed already follows).
            assert lqcam.last_overlay is not None and \
                (lqcam.last_overlay[..., 3] > 0).any(), \
                "a finished mark must actually reach camera.set_overlay, " \
                "not just live in the in-memory mark list"

            # Angle: 3 points, auto-finishes with no double-click needed.
            lqwin._live_measuring_panel.angle_btn.setChecked(True)
            assert lqwin._live_measuring_tool == "angle"
            click(100, 100)
            click(200, 100)
            click(100, 200)
            assert lqwin._live_measuring_pending_points == [], \
                "3 points for an angle shape must auto-finish"
            assert len(lqwin._live_measuring_marks) == 2
            assert lqwin._live_measuring_marks[1]["type"] == "angle"

            # Polygon: needs an explicit double-click at/past its own
            # minimum (3) -- unlike distance/angle, reaching the minimum with
            # a plain click must NOT finish it on its own.
            lqwin._live_measuring_panel.polygon_btn.setChecked(True)
            click(100, 100)
            click(200, 100)
            click(200, 200)
            assert len(lqwin._live_measuring_pending_points) == 3, \
                "a polygon must NOT auto-finish on reaching its minimum -- " \
                "only an explicit double-click finishes it"
            click(200, 200, kind=QEvent.MouseButtonDblClick)
            assert lqwin._live_measuring_pending_points == [], \
                "a double-click at/past the minimum must finish the polygon"
            assert len(lqwin._live_measuring_marks) == 3
            assert lqwin._live_measuring_marks[2]["type"] == "polygon"

            # A double-click BEFORE the minimum is a no-op, not a short shape.
            lqwin._live_measuring_panel.polygon_btn.setChecked(True)
            click(100, 100)
            click(100, 100, kind=QEvent.MouseButtonDblClick)
            assert len(lqwin._live_measuring_pending_points) == 1, \
                "a double-click before the minimum point count must not " \
                "finish the shape early"

            # Escape cancels the in-progress (not yet finished) sequence --
            # same convention as the armed-burst/batch-abort branches.
            esc = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
            lqwin.keyPressEvent(esc)
            assert lqwin._live_measuring_pending_points == [], \
                "Escape must cancel an in-progress Live Measuring shape"
            assert len(lqwin._live_measuring_marks) == 3, \
                "Escape must never touch an already-finished mark"

            # Hit test: a miss well away from every mark is None; a hit
            # against a real mark's own geometry (converted through the SAME
            # _live_measuring_view_point the production context menu uses)
            # finds it.
            miss = lqwin._live_measuring_hit_test((790, 590))
            assert miss is None, "a click far from every mark must miss"
            target_mark = lqwin._live_measuring_marks[0]
            hit_pos = lqwin._live_measuring_view_point(target_mark["points"][0])
            hit = lqwin._live_measuring_hit_test(hit_pos)
            assert hit is target_mark, \
                "a click on a real mark's own geometry must hit it"

            # Delete Point / Delete All -- driven directly (same reason Part
            # 05's own check calls _live_measure_delete_entry directly rather
            # than driving the actual, blocking QMenu.exec_).
            before = len(lqwin._live_measuring_marks)
            lqwin._live_measuring_delete_point(target_mark)
            assert len(lqwin._live_measuring_marks) == before - 1
            assert target_mark not in lqwin._live_measuring_marks
            lqwin._live_measuring_delete_all()
            assert lqwin._live_measuring_marks == []

            # Closing discards everything -- nothing here is ever durable, so
            # unlike Part 05's own close, there is nothing committed to
            # preserve.
            lqwin._live_measuring_marks = [{"type": "distance",
                                            "points": [(0.0, 0.0), (1.0, 1.0)]}]
            lqwin._live_measuring_pending_points = [(2.0, 2.0)]
            lqwin._live_measuring_panel.close()
            assert lqwin._live_measuring_marks == []
            assert lqwin._live_measuring_pending_points == []
            assert lqwin._live_measuring_tool is None
            assert not lqwin._live_measuring_active
        finally:
            lqcam.stop()
        print("Live Measuring check PASS: the module-boundary self-check "
              "runs clean; the panel opens/reuses like every other launcher "
              "in this file; opening either Live Measuring or Measure's own "
              "live panel (Part 05) closes the other first, in both "
              "directions; a real click through the real eventFilter "
              "converts to the correct LORES_RES-space point and suppresses "
              "ordinary box-drag; distance/angle auto-finish at their own "
              "point count while polygon needs an explicit double-click past "
              "(and not before) its minimum; Escape cancels an in-progress "
              "shape without touching a finished one; the overlay push "
              "actually reaches camera.set_overlay with the aid off; "
              "hit-test misses empty space and finds a real mark by its own "
              "geometry; Delete Point/All really mutate the mark list; "
              "closing discards every mark and pending point")
        # --- end Live Measuring check -----------------------------------------

    provenance.PROFILE_PATH = _orig_profile_path_for_render_check


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        main([a for a in sys.argv[1:] if a != "--render-check"])
