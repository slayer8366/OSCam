"""qt_shell.py - the live focus-aid + capture window (sections 5 and 6 wired
together: exposure panel, capture-enforces-lock, burst/HDR walkthroughs with
a real worker thread, the ruler, and calibration integration all built).

Session/profile management (Session, load_profile/save_profile, new_session_dir,
_dump_meta) is baked directly into this file rather than a separate capture.py:
it is generic workflow code (session folders, metadata, profile persistence),
not sensor-specific, so it lives with the GUI that is its only remaining
caller rather than as its own module. camera_backend.py stays the place for
anything that IS sensor-specific (IMX477 resolutions, lores format, ON-RIG
lines).

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
import subprocess
import sys
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from .camera_backend import FakeCamera, LORES_RES, FULL_RES
    from .focus import FocusMeter, FocusBox, FocusState, BarState, score_capture_sharpness
except ImportError:                 # run directly as a script, not as a package module
    from camera_backend import FakeCamera, LORES_RES, FULL_RES
    from focus import FocusMeter, FocusBox, FocusState, BarState, score_capture_sharpness

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

# The green plane calibrate.py measures on: half the sensor's resolution each
# axis (see debayer.py's extract_green / the build checklist's own invariant).
# The ruler's field-of-view-in-microns is derived from THIS width/height, not
# the lores preview's own pixel count, since um_per_px in calibration.json is
# a green-plane number.
GREEN_PLANE_RES = (FULL_RES[0] // 2, FULL_RES[1] // 2)

# --- Session and profile management (from capture.py, now baked in) --------
# Generic capture workflow: session folders, profile persistence, metadata
# recording. Not IMX477-specific; reusable with any camera sensor.

OUT_ROOT = Path.home() / "captures"
PROFILE_PATH = Path.home() / "imx" / "profile.json"
DEFAULT_BURST = 8
MAX_BURST = 10
DEFAULT_STOPS = [-2.0, -1.0, 0.0, 1.0, 2.0]
PROCESSOR = Path(__file__).resolve().parent / "hdr_from_session.py"
FULL_MODE_LBL = "4056:3040:12:U"
DENOISE = "off"
SHARPNESS = "0"


def load_profile():
    """Load camera profile (exposure, gains, WB) from disk if it exists."""
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    return None


def save_profile(locked):
    """Persist camera profile (exposure, gains, WB) to disk."""
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(locked, indent=2))


def _dump_meta(path, md):
    """Write capture metadata to a JSON sidecar file."""
    def _j(o):
        try:
            json.dumps(o)
            return o
        except TypeError:
            return str(o)
    path.write_text(json.dumps({k: _j(v) for k, v in md.items()}, indent=2))


def new_session_dir(root=None):
    """Create a new timestamped session directory for captures."""
    root = Path(root) if root else OUT_ROOT
    ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    d = root / ts
    n = 1
    while d.exists():
        d = root / "{}_{}".format(ts, n)
        n += 1
    d.mkdir(parents=True, exist_ok=True)
    return ts, d


class Session:
    """Session state: captures directory, locked settings, and session.json log."""

    def __init__(self, root, locked, display_flags):
        self.root = Path(root)
        self.locked = dict(locked)
        self.display_flags = list(display_flags)
        self.ts, self.dir = new_session_dir(root)
        self.captures = []
        self.write()

    def write(self):
        """Write session.json with current state."""
        payload = {
            "session_timestamp": self.ts,
            "tool": "qt_shell.py",
            "mode": FULL_MODE_LBL,
            "denoise": DENOISE,
            "sharpness": SHARPNESS,
            "display_flags": self.display_flags,
            "captures": self.captures,
        }
        (self.dir / "session.json").write_text(json.dumps(payload, indent=2))

    def existing(self, prefixes):
        """Files already present for any of these prefixes."""
        hits = []
        for p in prefixes:
            hits += list(self.dir.glob("{}frame_*".format(p)))
        return hits

    def clear(self, prefixes, kinds):
        """Remove files for these prefixes and capture entries of these kinds."""
        removed = 0
        for f in self.existing(prefixes):
            f.unlink()
            removed += 1
        self.captures = [c for c in self.captures if c.get("kind") not in kinds]
        self._reindex()
        self.write()
        return removed

    def _reindex(self):
        for i, c in enumerate(self.captures):
            c["index"] = i

    def record(self, entry):
        """Record a new capture entry to the session."""
        entry["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry["locked_settings"] = dict(self.locked)
        self.captures.append(entry)
        self._reindex()
        self.write()
        return entry["index"]

    def has_captures(self):
        return len(self.captures) > 0

    def close(self):
        """Remove the folder iff nothing was captured (only session.json on disk)."""
        if self.has_captures():
            return False
        others = [p for p in self.dir.iterdir() if p.name != "session.json"]
        if others:
            return False
        (self.dir / "session.json").unlink()
        self.dir.rmdir()
        return True


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
VIDEO_OUT_ROOT = OUT_ROOT / "video"

try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QLabel, QWidget,
                                 QVBoxLayout, QPushButton, QSlider, QCheckBox,
                                 QHBoxLayout, QSplitter, QMessageBox, QInputDialog,
                                 QDialog, QComboBox)
    from PyQt5.QtCore import QTimer, Qt, QRect, QEvent, pyqtSignal, QObject
    from PyQt5.QtGui import QImage, QPainter
    _HAVE_QT = True
except ImportError:                 # PyQt5 absent: --render-check still runs
    _HAVE_QT = False

MIN_FRAC = 0.03                      # smallest box a resize will commit (fractional)
HANDLE_FRAC = 0.05                   # how near a corner counts as grabbing it

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


def render_ruler_only_into(ov, ticks):
    """Just the ruler: for when the focus aid is off, so there is no box or
    bar to draw and no FocusState needed at all. Clears the buffer first."""
    ov[:] = 0
    _draw_ruler_ticks_into(ov, *ticks)
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
# triggers the gate. calibrate.py itself needs no changes either way; it
# already runs standalone, unmodified, exactly as before.
# ============================================================================

def should_show_onboarding_gate(already_shown, any_calibration_exists):
    """The onboarding gate's decision (checklist section 4), pure and
    testable apart from any Qt or filesystem state: show the "calibrate now
    or skip" prompt at most ONCE EVER. already_shown gates it out regardless
    of calibration state afterward -- skip is a real, respected choice, not
    a "not yet" that gets asked again next launch -- and it never shows at
    all once ANYTHING has been calibrated for any objective, shown or not.
    The "Calibrate" menu action is the whenever-you're-ready path either
    way, so a one-time miss here costs nothing."""
    return (not already_shown) and (not any_calibration_exists)
# ============================================================================


def render_overlay_into(ov, box, state, line=3, ruler_ticks=None):
    """Draw the overlay into an existing (H, W, 4) buffer, clearing it first. The
    GUI reuses one buffer per tick instead of allocating ~1.2 MB every frame.
    ruler_ticks (x_ticks, y_ticks), if given, draws first so the box+bar (the
    thing actively being dragged) stays visually on top."""
    ov[:] = 0
    if ruler_ticks is not None:
        _draw_ruler_ticks_into(ov, *ruler_ticks)
    h, w = ov.shape[:2]
    r0, r1, c0, c1 = box.pixel_rect((h, w))
    col = state_color(state)
    _rect_outline(ov, r0, r1, c0, c1, col, line)
    if state.bar is not None:
        _draw_bar(ov, r0, r1, c1, state.bar.fill, col)
    return ov


def render_overlay(size, box, state, line=3, ruler_ticks=None):
    """RGBA overlay (H, W, 4 uint8): the focus box outline plus a session-relative
    bar filled from the bottom, colour-coded by state, plus an optional ruler.
    Pure; the GUI hands the result to set_overlay. `size` is (width, height)."""
    w, h = size
    return render_overlay_into(np.zeros((h, w, 4), dtype=np.uint8), box, state,
                               line, ruler_ticks=ruler_ticks)


def overlay_signature(box, state, overlay_shape, ruler_key=None):
    """A cheap fingerprint of what the overlay would draw: the box pixel rect, the
    colour, the bar fill in whole pixels, and the ruler's config. When it is
    unchanged, the overlay is identical and the GPU upload can be skipped."""
    h, w = overlay_shape[:2]
    r0, r1, c0, c1 = box.pixel_rect((h, w))
    filled = -1
    if state.bar is not None:
        filled = int(round(min(max(state.bar.fill, 0.0), 1.0) * (r1 - r0)))
    return (r0, r1, c0, c1, state_color(state), filled, state.valid, ruler_key)


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
# Recording a shot (pure: no Qt, so the record path is testable off-rig)
# ---------------------------------------------------------------------------
def record_capture(session, result):
    """Persist a CaptureResult into a Session: write the metadata sidecar next to
    the raw and append a 'snap' record. The real exposure and gain of the
    auto-exposed shot come off the metadata, since the GUI is not locking them.
    Returns the capture index. Qt-free on purpose, so the whole record-a-shot
    flow runs under --render-check with the FakeCamera."""
    sidecar = result.raw.parent / (result.raw.stem + ".meta.json")
    _dump_meta(sidecar, result.metadata or {})
    md = result.metadata or {}
    files = [result.raw.name] + ([result.preview.name] if result.preview else [])
    return session.record({
        "kind": "snap",
        "note": "",
        "file_prefix": "snap_",
        "frame_count": 1,
        "files": files,
        "actual_us": md.get("ExposureTime"),
        "actual_s": (md.get("ExposureTime") / 1e6) if md.get("ExposureTime") else None,
        "analogue_gain": md.get("AnalogueGain"),
    })


def record_burst(session, kind, file_prefix, result, note=""):
    """Persist a capture_burst() result into a Session: write every frame's
    .meta.json sidecar, then ONE session-level record for the whole burst (one
    session.record call per burst, not per frame). `result` is capture_burst's
    return value: {"actual_us": ..., "frames": [CaptureResult, ...]}. Returns the
    capture index. Qt-free, so this runs under --render-check with FakeCamera."""
    for i, frame in enumerate(result["frames"]):
        sidecar = frame.raw.parent / (frame.raw.stem + ".meta.json")
        _dump_meta(sidecar, frame.metadata or {})
    actual = result["actual_us"]
    return session.record({
        "kind": kind,
        "note": note,
        "file_prefix": file_prefix,
        "frame_count": len(result["frames"]),
        "requested_us": actual,
        "actual_us": actual,
        "actual_s": (actual / 1e6) if actual else None,
    })


def record_hdr(session, sci_levels, dark_levels, note=""):
    """Persist an HDR bracket (the two capture_bracket_phase results, science then
    dark) into a Session: write every frame's sidecar across both phases, then
    ONE 'hdr' record carrying both level lists, mirroring do_hdr exactly. The
    CaptureResult objects are stripped out of the level dicts before they go into
    session.json (only JSON-serializable fields belong there; each frame's full
    metadata already went into its own sidecar). Returns the capture index."""
    def _write_sidecars_and_strip(levels):
        stripped = []
        for lv in levels:
            for frame in lv["frames"]:
                sidecar = frame.raw.parent / (frame.raw.stem + ".meta.json")
                _dump_meta(sidecar, frame.metadata or {})
            stripped.append({k: v for k, v in lv.items() if k != "frames"})
        return stripped

    sci_clean = _write_sidecars_and_strip(sci_levels)
    dark_clean = _write_sidecars_and_strip(dark_levels)
    return session.record({
        "kind": "hdr", "note": note,
        "levels": sci_clean, "dark_levels": dark_clean,
    })


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


def list_sessions(out_root):
    """Every session directory under out_root that has a session.json, most
    recent first (session directories are timestamp-named, so name order is
    chronological order). Returns a list of Paths."""
    out_root = Path(out_root)
    if not out_root.exists():
        return []
    found = [d for d in out_root.iterdir() if d.is_dir() and (d / "session.json").exists()]
    return sorted(found, key=lambda d: d.name, reverse=True)


def load_session_json(session_dir):
    try:
        return json.loads((Path(session_dir) / "session.json").read_text())
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
      - flat: the LAST 'flat' kind capture in the session ("last flat wins"),
        searched by its own file_prefix.
      - dark: for an hdr capture, its own per-level dark_levels prefixes; for
        science/snap, the standalone "dark_" prefix (pairs with science).
    Also detects the raw file extension in use (dng on-rig, tif off-rig)
    from whatever is actually on disk, so the wizard's eventual subprocess
    call passes the right --raw-ext without guessing. Returns a dict with
    flat_frames/dark_frames/own_frames counts and the detected ext."""
    session_dir = Path(session_dir)

    def _frames_for(prefix):
        # Restricted to the actual raw extensions (not a bare "*.*" wildcard,
        # which also matches each frame's own ".meta.json" sidecar and both
        # double-counts frames and can misdetect the extension).
        if not prefix:
            return []
        matches = []
        for ext in ("dng", "tif"):
            matches += session_dir.glob("{}frame_*.{}".format(prefix, ext))
        return sorted(matches)

    flat_prefix = None
    for c in session_json.get("captures", []):
        if c.get("kind") == "flat":
            flat_prefix = c.get("file_prefix")
    flat_frames = _frames_for(flat_prefix)

    kind = cap.get("kind")
    if kind == "hdr":
        own_prefix = cap["levels"][0]["file_prefix"] if cap.get("levels") else None
        dark_frames = []
        for lvl in cap.get("dark_levels", []):
            dark_frames += _frames_for(lvl.get("file_prefix"))
    else:
        own_prefix = cap.get("file_prefix")
        dark_frames = _frames_for("dark_")

    own_frames = _frames_for(own_prefix)
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
    raws = []
    for ext in ("dng", "tif"):
        raws += sorted(session_dir.glob("*.{}".format(ext)))
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

        def __init__(self, camera, meter, tick_ms=33, display_flags=None):
            super().__init__()
            self.camera = camera
            self.meter = meter
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

            self.preview = camera.widget if hasattr(camera, "widget") \
                else _FakePreview(camera)

            self.readout = QLabel("focus aid off, press F")
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
            if load_profile is not None:
                startup_locked = load_profile()
            if startup_locked is None:
                startup_locked = self.camera.probe()
                if save_profile is not None:
                    save_profile(startup_locked)
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
            # A floor, not a hard lock: the splitter below is what holds this
            # column's width steady against content changes; this minimum just
            # keeps a drag from squeezing it down to something unusable.
            panel.setMinimumWidth(250)
            col = QVBoxLayout(panel)
            col.addWidget(self.capture_status)
            capture_row = QHBoxLayout()
            capture_row.addWidget(self.capture_btn)
            capture_row.addWidget(self.record_btn)
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
            splitter = QSplitter(Qt.Horizontal)
            splitter.addWidget(self.preview)
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
            filemenu.addAction("Archive session raws...", self._open_archive_wizard)
            filemenu.addAction("Browse captures...", self._open_gallery_browser)
            filemenu.addAction("Quit", self.close)
            view = self.menuBar().addMenu("View")
            view.addAction("Reset field (R)").triggered.connect(self.meter.reset_field)
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
                                    ruler_key=self._ruler_key())
            if sig != self._last_sig:
                buf = self._ov_bufs[self._ov_idx]
                render_overlay_into(buf, self.meter.box, state, ruler_ticks=ruler)
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
                ruler = self._current_ruler_ticks()
                if ruler is not None:
                    # The aid drives the timer, but the ruler is its own toggle;
                    # turning the aid off should not also erase the ruler.
                    buf = self._ov_bufs[self._ov_idx]
                    render_ruler_only_into(buf, ruler)
                    self.camera.set_overlay(buf)
                    self._ov_idx ^= 1
                else:
                    self.camera.set_overlay(None)  # clear the box (and any ruler) off the preview
                self._last_sig = None
                txt = "focus aid off, press F"
                if ruler is not None:
                    txt += "  (ruler on)"
                self.readout.setText(txt)
                self._last_readout = txt

        def _toggle_aid(self):
            self._set_aid(not self._aid_on)

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
            ruler = self._current_ruler_ticks()
            if ruler is not None:
                buf = self._ov_bufs[self._ov_idx]
                render_ruler_only_into(buf, ruler)
                self.camera.set_overlay(buf)
                self._ov_idx ^= 1
            else:
                self.camera.set_overlay(None)
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
        # --- end measure menu (method) ---------------------------------------

        def _maybe_show_onboarding_gate(self):
            """The first-launch prompt itself (checklist section 4): ask once
            whether to calibrate now, using should_show_onboarding_gate's pure
            decision. Skipping (or closing the dialog without choosing) just
            continues into the GUI exactly as it would otherwise; the
            Calibrate menu action covers "whenever" either way."""
            if _calibrate is None:
                return
            already_shown = bool(load_pref("onboarding_calibration_prompt_shown", False))
            any_calibration_exists = bool(_calibrate.load_calibrations())
            if not should_show_onboarding_gate(already_shown, any_calibration_exists):
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
            if save_profile is not None:
                save_profile(result)
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
                # OUT_ROOT via Session (both baked into this file; see the
                # "Session and profile management" section above).
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
                idx = record_capture(self._session, result)
                self._set_capture_status(
                    "saved {}".format(result.raw.name),
                    "saved {}  (session {}, capture #{})".format(
                        result.raw.name, self._session.ts, idx))
                # No offer-to-process here: a single snap is one frame, frame
                # averaging (and the rest of the processing chain) only makes
                # sense for a multi-frame burst. Science and HDR still offer
                # it (see _on_burst_finished).
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
                self._session = Session(OUT_ROOT, load_profile() or {}, self._display_flags)
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
            if not self._reshoot_guard([prefix], kinds_set, kind):
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
                    # held open across the pause.
                    self.camera.enter_still_mode()
                    try:
                        dark_levels = self.camera.capture_bracket_phase(
                            session.dir, "dark_", armed["nd"], base_us, ordered)
                    finally:
                        self.camera.exit_still_mode(base_us)
                    idx = record_hdr(session, armed["sci_levels"], dark_levels)
                    sci_n = sum(lv["frame_count"] for lv in armed["sci_levels"])
                    dark_n = sum(lv["frame_count"] for lv in dark_levels)
                    return {"kind": "hdr", "phase": "dark", "index": idx,
                           "summary": "{} science + {} dark frames across {} levels"
                           .format(sci_n, dark_n, len(ordered))}
            else:
                prefix = armed["prefix"]
                result = self.camera.capture_burst(session.dir, prefix, armed["n"])
                idx = record_burst(session, kind, prefix, result)
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
                    idx = record_hdr(self._session, sci_levels, [])
                    result = {"kind": "hdr", "phase": "dark", "index": idx,
                             "summary": "science-only (dark phase skipped)"}
                except Exception as exc:
                    result = exc
                self.burst_done_signal.emit(result)

            threading.Thread(target=_worker, daemon=True).start()

        def _offer_process(self, kind, index):
            # Invokes hdr_from_session.py (--index/display flags), but only
            # called for science and hdr here (see _on_burst_finished), not
            # snap: a single capture is one frame, and frame averaging only
            # makes sense across a burst. A GUI Yes/No instead of a blocking
            # terminal prompt; the actual run is shared with the manual
            # processing wizard (see _run_process_cmd).
            resp = self._flat_question(
                "Process capture?",
                "Process capture #{} to a display image now?\n"
                "(frame averaging, flat/dark correction, tonemap, debayer)"
                .format(index))
            if resp != QMessageBox.Yes:
                return
            self._run_process_cmd(self._session.dir, index)

        def _run_process_cmd(self, session_dir, index, extra_args=None):
            # Shared by the automatic offer (_offer_process) and the manual
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
            cmd = ([sys.executable, str(PROCESSOR), str(session_dir),
                   "--index", str(index), "--keep-raws"]
                  + list(self._display_flags) + list(extra_args or []))
            self._last_process_session_dir = Path(session_dir)
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
            dlg = ProcessSessionDialog(OUT_ROOT, self._display_flags, self)
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
            dlg = ArchiveSessionDialog(OUT_ROOT, self)
            if dlg.exec_() != QDialog.Accepted:
                return
            session_dir = dlg.selected_session_dir()
            if session_dir is None:
                return
            self._offer_archive_raws(session_dir)

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
            dlg = _gallery.GalleryBrowseWindow(OUT_ROOT, self)
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

        def _on_process_finished(self, payload):
            self._capturing = False
            self._set_capture_controls(enabled=True, label="Capture")
            ok, stdout, stderr = payload
            if ok:
                self._set_capture_status("processed",
                                         "processing complete\n\n" + stdout[-4000:])
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
            # Offer to process for the two burst-produced kinds that can be
            # (flat and dark are calibration-only, never offered). This also
            # fires when HDR's dark phase was cancelled mid-sequence
            # (science-only, dark_levels empty): hdr_from_session.py's own
            # process() already handles an empty dark_levels dict gracefully,
            # just skipping that correction stage.
            if result["kind"] in ("science", "hdr"):
                self._offer_process(result["kind"], result["index"])

        # --- box interaction ------------------------------------------------
        def _disp_rect(self):
            return displayed_rect(self.preview.width(), self.preview.height(),
                                  self._aspect)

        def eventFilter(self, obj, ev):
            if obj is self.preview:
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
    a = ap.parse_args(argv)
    if not _HAVE_QT:
        sys.exit("PyQt5 not available. Use --render-check for the headless self-check "
                 "test, or install python3-pyqt5 for the GUI.")
    app = QApplication(sys.argv)
    if a.camera:
        try:
            from .camera_backend import Picamera2Camera
        except ImportError:
            from camera_backend import Picamera2Camera
        camera = Picamera2Camera()
    else:
        camera = FakeCamera()
    display_flags = build_display_flags(a)
    win = FocusPreviewWindow(camera, FocusMeter(), display_flags=display_flags)
    win.resize(1550, 760)          # fallback size if the window manager ever
                                    # ignores the maximize request below
    win.setWindowTitle("Zynergy capture GUI" + ("" if a.camera else "  (fake)"))
    win.showMaximized()
    app.exec_()


# ---------------------------------------------------------------------------
# Headless self-check for the pure parts (no PyQt, no camera)
# ---------------------------------------------------------------------------
def render_check():
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
    assert should_show_onboarding_gate(already_shown=False, any_calibration_exists=False) is True, \
        "never shown before, nothing calibrated -> should show"
    assert should_show_onboarding_gate(already_shown=True, any_calibration_exists=False) is False, \
        "already shown once -> never show again, regardless of calibration state"
    assert should_show_onboarding_gate(already_shown=False, any_calibration_exists=True) is False, \
        "something already calibrated -> no nudge needed even if never shown"
    assert should_show_onboarding_gate(already_shown=True, any_calibration_exists=True) is False
    print("should_show_onboarding_gate check PASS: one-time nudge only when "
          "genuinely both unshown and uncalibrated, never a recurring nag")

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

    # record_capture: Qt-free, exercised against a FakeCamera capture straight off
    # the async seam, into a throwaway Session.
    # record_capture: Qt-free, exercised against a FakeCamera capture straight off
    # the async seam, into a throwaway Session (Session is baked into this file;
    # see the "Session and profile management" section above).
    import shutil
    from camera_backend import CaptureResult
    tmp_root = Path("/tmp/zynergy_render_check_captures")
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    session = Session(tmp_root, {}, [])
    cam = FakeCamera(async_delay_s=0.0)
    done = threading.Event()
    got = {}

    def _on_done(result):
        got["result"] = result
        done.set()

    cam.capture_still_async(session.dir, "snap_frame_0000", _on_done)
    done.wait(timeout=2.0)
    idx = record_capture(session, got["result"])
    assert idx == 0, "first recorded capture should be index 0"
    assert session.captures[0]["kind"] == "snap", "record_capture did not record a snap"
    sidecar = got["result"].raw.parent / (got["result"].raw.stem + ".meta.json")
    assert sidecar.exists(), "record_capture did not write a .meta.json sidecar"
    print("record_capture check PASS: sidecar written, session record appended")

    # record_burst / record_hdr: the two new burst-wiring helpers, exercised
    # against FakeCamera.capture_burst / capture_bracket_phase directly (no
    # Qt), so the whole record path for all four burst kinds is covered
    # off-rig before ever touching a PyQt5 widget.
    from camera_backend import CaptureResult as _CR   # noqa: F401 (already imported above)
    burst_root = Path("/tmp/zynergy_render_check_burst")
    if burst_root.exists():
        shutil.rmtree(burst_root)
    bsession = Session(burst_root, {}, [])
    bcam = FakeCamera()

    flat_result = bcam.capture_burst(bsession.dir, "flat_", 3, shutter_us=5000)
    flat_idx = record_burst(bsession, "flat", "flat_", flat_result)
    assert flat_idx == 0, "first burst record should be index 0"
    rec = bsession.captures[0]
    assert rec["kind"] == "flat" and rec["frame_count"] == 3, \
        "record_burst did not record a 3-frame flat"
    for i in range(3):
        sidecar = bsession.dir / "flat_frame_{:04d}.meta.json".format(i)
        assert sidecar.exists(), "record_burst missing sidecar for frame {}".format(i)

    bcam.enter_still_mode()
    sci = bcam.capture_bracket_phase(bsession.dir, "", 2, 10_000, [-1.0, 0.0, 1.0])
    dark = bcam.capture_bracket_phase(bsession.dir, "dark_", 2, 10_000, [-1.0, 0.0, 1.0])
    bcam.exit_still_mode(8000)
    hdr_idx = record_hdr(bsession, sci, dark)
    assert hdr_idx == 1, "HDR should be the second record in this session"
    hdr_rec = bsession.captures[1]
    assert hdr_rec["kind"] == "hdr", "record_hdr did not record kind=hdr"
    assert len(hdr_rec["levels"]) == 3 and len(hdr_rec["dark_levels"]) == 3, \
        "record_hdr level counts off"
    assert "frames" not in hdr_rec["levels"][0], \
        "record_hdr must strip CaptureResult objects before writing session.json"
    for lv in sci:
        for i in range(2):
            sidecar = bsession.dir / "{}frame_{:04d}.meta.json".format(lv["file_prefix"], i)
            assert sidecar.exists(), "record_hdr missing a science sidecar"
    json.loads((bsession.dir / "session.json").read_text())   # must be JSON-serializable
    print("record_burst / record_hdr check PASS: sidecars written, session records "
          "appended, HDR level dicts JSON-clean")

    # Processing wizard pure helpers: list_sessions/load_session_json/
    # processable_captures/capture_correction_status, exercised against
    # the same flat+hdr session just built above, plus a second session
    # to confirm list_sessions finds multiple and sorts most-recent-first.
    wiz_root = Path("/tmp/zynergy_render_check_wizard")
    if wiz_root.exists():
        shutil.rmtree(wiz_root)
    s_old = Session(wiz_root, {}, [])
    wcam = FakeCamera()
    old_flat = wcam.capture_burst(s_old.dir, "flat_", 2, shutter_us=5000)
    record_burst(s_old, "flat", "flat_", old_flat)
    old_sci = wcam.capture_burst(s_old.dir, "science_", 2)
    record_burst(s_old, "science", "science_", old_sci)
    import time as _time
    _time.sleep(1.05)   # session dirs are timestamp-named; force a distinct, later name
    s_new = Session(wiz_root, {}, [])
    new_sci = wcam.capture_burst(s_new.dir, "science_", 2)
    record_burst(s_new, "science", "science_", new_sci)

    found = list_sessions(wiz_root)
    assert len(found) == 2, "list_sessions should find both session dirs"
    assert found[0] == s_new.dir, "list_sessions should list most-recent-first"

    sj_old = load_session_json(s_old.dir)
    proc_old = processable_captures(sj_old)
    assert len(proc_old) == 1 and proc_old[0]["kind"] == "science", \
        "processable_captures should list science but exclude flat"

    status_with_flat = capture_correction_status(s_old.dir, sj_old, proc_old[0])
    assert status_with_flat["flat_frames"] == 2, "expected 2 flat frames found"
    assert status_with_flat["dark_frames"] == 0, "no standalone dark shot yet"
    assert status_with_flat["own_frames"] == 2, "expected 2 own science frames"

    sj_new = load_session_json(s_new.dir)
    proc_new = processable_captures(sj_new)
    status_no_flat = capture_correction_status(s_new.dir, sj_new, proc_new[0])
    assert status_no_flat["flat_frames"] == 0, \
        "a different session's flat must not leak into this one's status"
    print("processing wizard helpers check PASS: sessions listed most-recent-first, "
          "processable captures filtered correctly, flat/dark status accurate and "
          "session-scoped")

    # archive_session_raws: no-op with nothing to archive, then a real
    # bundle-and-remove against the flat+science files already on disk
    # in s_old (built above), verified against the exact same tar
    # safety order hdr_from_session.py's own archive_raws uses.
    empty_result = archive_session_raws(Path("/tmp/zynergy_render_check_no_such_dir"))
    assert empty_result == {"archived": 0, "tar_path": None, "mb": 0.0}, \
        "archiving an empty/missing dir should be a clean no-op"

    raws_before = sorted(s_old.dir.glob("*.tif"))
    assert len(raws_before) == 4, "expected 2 flat + 2 science raw files before archiving"
    arch_result = archive_session_raws(s_old.dir)
    assert arch_result["archived"] == 4, "expected all 4 raws archived"
    assert arch_result["tar_path"].exists(), "tar file should exist on disk"
    assert not list(s_old.dir.glob("*.tif")), "loose raws should be removed after archiving"
    with tarfile.open(str(arch_result["tar_path"])) as tf:
        names = set(tf.getnames())
    assert names == {p.name for p in raws_before}, \
        "tar contents should exactly match the original raw filenames"
    print("archive_session_raws check PASS: no-op on empty/missing dir, real bundle+verify+"
          "remove matches hdr_from_session.py's own tar safety order")

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
        win._session = Session(tag_root, {}, [])
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
            idx = record_capture(win._session, g["r"])
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
        on_disk = json.loads((win._session.dir / "session.json").read_text())
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

        # _score_capture_sharpness (section 13's post-capture QC): a real
        # FakeCamera burst, scored against its OWN written frame via
        # calibrate.load_green_plane + focus.score_capture_sharpness --
        # both called for real, nothing mocked here.
        qc_root = Path("/tmp/zynergy_render_check_qc")
        if qc_root.exists():
            shutil.rmtree(qc_root)
        qcam = FakeCamera(async_delay_s=0.0)
        qc_session = Session(qc_root, {}, [])
        qc_result = qcam.capture_burst(qc_session.dir, "science_", 2)
        qc_idx = record_burst(qc_session, "science", "science_", qc_result)
        assert "sharpness_score" not in qc_session.captures[qc_idx], \
            "record_burst itself must not invent a score -- only " \
            "_score_capture_sharpness (called separately, after) does"
        win._score_capture_sharpness(qc_session, qc_idx, qc_result)
        score = qc_session.captures[qc_idx].get("sharpness_score")
        assert isinstance(score, float), \
            "a real capture should score as a real float, got {!r}".format(score)
        on_disk_qc = json.loads((qc_session.dir / "session.json").read_text())
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


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        main([a for a in sys.argv[1:] if a != "--render-check"])
