"""calibrate.py - standalone spatial (um/px) calibration tool for Zynergy.

Separate from qt_shell.py on purpose: calibration is an infrequent, deliberate
task (redo it when an objective gets bumped, cleaned, or swapped), not
something needed during a live capture session, so it stays out of the
capture GUI's own scope entirely. (Onboarding-gate integration -- greying out
measurement tools until a calibration exists -- is a separate, still-open
decision; this rework does not touch that.)

REWORKED to close gaps against the build checklist found while cross-checking
Handoff D:

  * Measures on the GREEN PLANE, not the RGB JPEG preview. Point this tool at
    the raw .dng a snap writes (or a frame_average.py mosaic master.tif); it
    reads the undemosaiced CFA plane the same way debayer.py does, and
    extracts ONE green sub-plane (green-which=1, zero interpolation) -- the
    same canonical measurement channel the rest of the project uses
    everywhere else. Half the sensor's resolution each axis, pitch doubled:
    a DIFFERENT scale space from the full-res mosaic or any demosaiced RGB,
    which is exactly why measuring on it directly (rather than the JPEG)
    matters. A .jpg argument auto-resolves to its sibling .dng (capture.py
    always writes both from the same stem); a .dng/.tif/.tiff argument is
    used as-is.

  * Calibration store is now append-only with a supersedes chain. A redo
    adds a new entry and chains 'supersedes' to whatever was current before;
    nothing already saved is ever edited or removed, so the full audit
    history for an objective survives every re-calibration.

  * Each entry now records the conditions it was made under: the objective,
    the fixed reduction lens, what kind of target was used, and a focus
    score off the actual captured green frame (evidence the shot was sharp;
    not a gate -- a low score is recorded honestly, not hidden or blocked).

Workflow, otherwise unchanged: zoom/pan to two clear ruling marks, click both,
type the known real-world distance between them, save. Calibration is PER
OBJECTIVE (4x/10x/40x/100x, or whatever is in use), stored at
~/.zynergy/calibration.json. For the most accurate result, pick two ruling
marks as far apart as the field of view allows: the relative error in a
pixel-distance measurement shrinks as the total span grows, so spanning many
stage-micrometer divisions beats two adjacent ones.

Two ways to run:
  python3 calibrate.py --render-check      headless: exercises the pure
                                           distance/calibration/green-plane
                                           math and the JSON store, no PyQt5,
                                           no real image file.
  python3 calibrate.py [image] [--objective NAME]
                                           the GUI. Both arguments are
                                           optional; the GUI can open a file
                                           and type/pick an objective if
                                           neither is given up front.
"""
from __future__ import annotations

import json
import math
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

import numpy as np
import tifffile

# extract_green is reused from debayer.py rather than reimplemented, the same
# way ca_lib.py is already shared between ca_measure.py and debayer.py. Same
# try-relative-then-bare pattern debayer.py itself uses for ca_lib.
try:
    from . import debayer as _debayer
except ImportError:
    try:
        import debayer as _debayer
    except ImportError:
        _debayer = None

# variance_of_laplacian for the post-hoc focus score. Optional: if focus.py
# is not alongside this file, the score is simply recorded as unavailable
# rather than blocking calibration on a module that has nothing to do with
# the measurement itself.
try:
    from . import focus as _focus
except ImportError:
    try:
        import focus as _focus
    except ImportError:
        _focus = None

# The shared image-source wizard page (build checklist section 4): pick an
# image already shot, or shoot a new one live. Optional the same way debayer/
# focus are -- the wizard is simply unavailable (CalibrationWindow itself
# still opens fine via the CLI [image] argument) if wizard_pages.py is not
# alongside this file.
try:
    from . import wizard_pages as _wizard_pages
except ImportError:
    try:
        import wizard_pages as _wizard_pages
    except ImportError:
        _wizard_pages = None

CALIBRATION_PATH = Path.home() / ".zynergy" / "calibration.json"
DEFAULT_OBJECTIVES = ["4x", "10x", "40x", "100x"]
DEFAULT_TARGET_TYPES = ["stage micrometer"]
# The IMX477's physical order (matches debayer.py's own default) and the
# project-wide canonical green (green-which=1, "One canonical green" in the
# build checklist's invariants). Fixed hardware/convention facts, not a
# per-calibration choice, so they are not exposed as GUI controls.
DEFAULT_CFA_PATTERN = "BGGR"
DEFAULT_GREEN_WHICH = 1
# The fixed reduction lens every objective sits behind. A constant, recorded
# in every entry's provenance per the checklist ("objective, the .5x...").
REDUCTION_LENS = 0.5
# Below this many pixels apart (in the loaded plane's own native pixels), a
# click imprecision of even 1-2px swings the result too much to trust;
# refuse rather than silently save a bad number. Plane-agnostic: whichever
# plane is loaded (now always the green plane), this is native pixels of it.
MIN_CALIBRATION_PX = 20.0


# ---------------------------------------------------------------------------
# Pure logic (Qt-free, testable under --render-check)
# ---------------------------------------------------------------------------
def pixel_distance(p1, p2):
    """Euclidean distance in pixels between two (x, y) points."""
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def compute_calibration(pixel_dist, known_distance_um):
    """um_per_px and px_per_um from a measured pixel distance and the known
    real-world distance (in um) it corresponds to. Raises ValueError for a
    degenerate input (points too close together, or a non-positive known
    distance) rather than silently returning a meaningless huge or negative
    calibration -- a fat-fingered double-click on the same ruling mark should
    fail loudly, not produce a number that looks plausible until it is used."""
    if pixel_dist < MIN_CALIBRATION_PX:
        raise ValueError(
            "the two points are only {:.1f}px apart (minimum {:.0f}px) -- pick "
            "two ruling marks further apart for an accurate result"
            .format(pixel_dist, MIN_CALIBRATION_PX))
    if known_distance_um <= 0:
        raise ValueError("known distance must be a positive number of micrometers")
    um_per_px = known_distance_um / pixel_dist
    return {"um_per_px": um_per_px, "px_per_um": 1.0 / um_per_px}


def resolve_raw_path(path):
    """A .jpg/.jpeg argument resolves to its sibling .dng (capture.py always
    writes both from the same stem, per its own do_snap/do_burst); a
    .dng/.tif/.tiff argument (a raw file directly, or a frame_average.py
    mosaic master) is used as-is. Raises ValueError if a resolved sibling
    does not actually exist, rather than silently measuring the JPEG instead
    -- that would be exactly the bug this rework exists to close."""
    p = Path(path)
    if p.suffix.lower() in (".jpg", ".jpeg"):
        sibling = p.with_suffix(".dng")
        if not sibling.is_file():
            raise ValueError(
                "{} has no sibling {} -- capture.py writes both together, so "
                "this suggests the file moved on its own. Point at the .dng "
                "directly if it lives somewhere else.".format(p.name, sibling.name))
        return sibling
    return p


def load_mosaic_array(path):
    """Read a raw-Bayer-mosaic TIFF/DNG: tifffile.imread on a Pi HQ DNG
    returns the undemosaiced CFA plane directly (same fact debayer.py's own
    load_mosaic relies on); a frame_average.py master.tif is the same shape
    of thing. Raises ValueError on anything that is not a single-channel
    plane -- debayer.py's own load_mosaic calls sys.exit for this, which is
    fine for a CLI tool but would kill a live GUI outright, so this is a
    from-scratch read rather than a reuse of that function."""
    with tifffile.TiffFile(str(path)) as tf:
        arr = tf.pages[0].asarray()
    if arr.ndim != 2:
        raise ValueError(
            "{} has shape {}; expected a single-channel raw mosaic (a stage-"
            "micrometer .dng straight off the camera, or a frame_average.py "
            "mosaic master.tif). A .jpg preview or an already-demosaiced RGB "
            "image cannot be measured on the green plane."
            .format(Path(path).name, arr.shape))
    return arr


def load_green_plane(path, pattern=DEFAULT_CFA_PATTERN, which=DEFAULT_GREEN_WHICH):
    """The measurement substrate: load a raw mosaic and extract ONE green
    sub-plane, zero interpolation -- the same rule as debayer.py --green and
    the project's green-which=1 convention everywhere else. Half the
    sensor's pixel count each axis, pitch doubled: this IS the scale space
    calibrated here, not the full-res mosaic or any demosaiced RGB."""
    if _debayer is None:
        raise RuntimeError(
            "debayer.py could not be imported (extract_green comes from it); "
            "keep calibrate.py in the same folder as debayer.py.")
    arr = load_mosaic_array(path)
    plane, _rc = _debayer.extract_green(arr, pattern, which)
    return plane


def stretch_to_uint8(plane, lo_pct=1.0, hi_pct=99.0):
    """Percentile auto-stretch to 0-255 uint8, DISPLAY ONLY. Never used for
    the actual measurement: clicks map through widget_to_native onto the
    native green-plane array regardless of how it is stretched for on-screen
    viewing -- the same overlay-vs-pixel-data separation the rest of the
    project holds everywhere. Percentile rather than bare min/max so a
    handful of hot or dead sensor pixels cannot wash out the whole display
    range."""
    a = np.asarray(plane, dtype=np.float64)
    lo, hi = np.percentile(a, [lo_pct, hi_pct])
    if hi <= lo:
        lo, hi = float(a.min()), float(a.max())
    if hi <= lo:
        return np.zeros(a.shape, dtype=np.uint8)
    out = (a - lo) / (hi - lo)
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def load_calibrations():
    """The whole per-objective calibration store: {objective: [entry, ...]},
    each list chronological and append-only (oldest first, current last).
    {} if none saved yet or the file is unreadable, never raises -- a
    missing/corrupt store should not crash the tool, just look like no
    calibrations exist yet."""
    try:
        return json.loads(CALIBRATION_PATH.read_text())
    except Exception:
        return {}


def current_calibration(objective, store=None):
    """The active calibration for an objective: the LAST entry in its
    history, or None if it has never been calibrated. List order IS the
    supersedes chain (append-only, current last), so this is unambiguous;
    each entry also carries its own explicit 'supersedes' id so the chain is
    auditable even if entries were ever inspected out of list order."""
    store = store if store is not None else load_calibrations()
    history = store.get(objective) or []
    return history[-1] if history else None


def save_calibration(objective, entry):
    """Append-only: adds a new entry to the objective's history and chains
    'supersedes' to whatever was current before. Per the build checklist,
    'a redo is a new entry that supersedes; nothing is overwritten' -- a
    redo is a new fact, not a correction to the old one, so nothing already
    saved is ever edited or removed. Same atomic-write pattern as before
    (temp file, then rename over the live store), so a crash mid-write can
    never corrupt calibrations for OTHER objectives, or truncate this
    objective's own history."""
    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    store = load_calibrations()
    history = store.setdefault(objective, [])
    prior = history[-1] if history else None
    entry = dict(entry)
    entry["entry_id"] = uuid.uuid4().hex
    entry["supersedes"] = prior["entry_id"] if prior else None
    history.append(entry)
    tmp = CALIBRATION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    os.replace(tmp, CALIBRATION_PATH)
    return store


def build_calibration_entry(image_path, point_a, point_b, known_distance_um,
                             objective, target_type, focus_score,
                             pattern=DEFAULT_CFA_PATTERN,
                             which=DEFAULT_GREEN_WHICH, now=None):
    """The full record saved for one objective: the result PLUS the full
    provenance behind it, per the build checklist ("each writes the
    conditions it was made under... objective, the .5x, target type, and the
    focus score at capture"): source image (the raw plane actually
    measured), exact click points (native GREEN-PLANE pixels), the known
    distance entered, the objective, the fixed reduction lens, what kind of
    target was used, which green sub-plane/CFA pattern, and a focus score
    off the captured frame as evidence it was actually sharp (not a gate --
    a low score is recorded honestly, not hidden or blocked). Raises
    ValueError via compute_calibration for a degenerate measurement."""
    dist_px = pixel_distance(point_a, point_b)
    calib = compute_calibration(dist_px, known_distance_um)
    return {
        "um_per_px": calib["um_per_px"],
        "px_per_um": calib["px_per_um"],
        "calibrated_at": (now or datetime.now()).isoformat(),
        "source_image": str(image_path) if image_path else None,
        "point_a": [float(point_a[0]), float(point_a[1])],
        "point_b": [float(point_b[0]), float(point_b[1])],
        "pixel_distance": dist_px,
        "known_distance_um": float(known_distance_um),
        "objective": objective,
        "reduction_lens": REDUCTION_LENS,
        "target_type": target_type,
        "focus_score": None if focus_score is None else float(focus_score),
        "measurement_plane": {"cfa_pattern": pattern, "green_which": which},
    }


def widget_to_native(widget_point, zoom):
    """A click position in the (possibly zoomed) display maps back to NATIVE
    image pixel coordinates by dividing out the zoom factor. Pure so the
    exact mapping used by the GUI is independently checkable."""
    zoom = zoom if zoom > 0 else 1.0
    return (widget_point[0] / zoom, widget_point[1] / zoom)


# ---------------------------------------------------------------------------
# Config-drift invalidation (build checklist section 13): "objective change
# or a resolution/binning change invalidates both calibrations; the recorded
# objective and .5x let the tool flag a re-measure when [the config] no
# longer matches." There is currently no variable resolution/binning mode on
# this rig (camera_backend.py's FULL_RES/GREEN_PLANE_RES are fixed IMX477
# constants), so reduction lens and the CFA/green-which convention are the
# two things that can actually drift if the physical rig, or its assumed
# hardware convention, ever changes.
# ---------------------------------------------------------------------------
def current_rig_config():
    """This rig's live, code-level optical config -- the three numbers every
    calibration entry's own recorded config gets compared against."""
    return {"reduction_lens": REDUCTION_LENS, "cfa_pattern": DEFAULT_CFA_PATTERN,
            "green_which": DEFAULT_GREEN_WHICH}


def calibration_staleness(entry, rig_config=None):
    """Whether a calibration entry's own recorded config still matches the
    CURRENT rig config -- evidence only, same "recorded honestly, never a
    gate" rule this project already follows for poly2_flag and
    sharpness_relative_flag. Returns a list of human-readable mismatch
    reasons, empty if nothing has drifted.

    Only compares fields the entry ACTUALLY carries: an entry saved before
    a given field existed (an older spatial entry predating reduction_lens,
    or any CA entry from before this check existed) is silently trusted on
    that field rather than flagged for something it never recorded --
    absence is not evidence of a mismatch, the same discipline
    sharpness_relative_flag already applies to a missing score.

    Works on either shape of entry: a spatial calibration entry (a
    top-level reduction_lens, plus measurement_plane.cfa_pattern/
    green_which) or a CA calibration entry (reduction_lens and cfa_pattern
    directly at the top level, no measurement_plane and no green_which --
    CA operates on demosaiced RGB, not a green sub-plane extraction, so
    green_which never applies to it; see ca_measure.py's
    build_ca_calibration_entry).
    """
    rig_config = rig_config if rig_config is not None else current_rig_config()
    reasons = []

    recorded_lens = entry.get("reduction_lens")
    if recorded_lens is not None and recorded_lens != rig_config["reduction_lens"]:
        reasons.append(
            "reduction lens changed (calibrated at {}x, rig is now {}x)"
            .format(recorded_lens, rig_config["reduction_lens"]))

    measurement_plane = entry.get("measurement_plane") or {}
    recorded_cfa = measurement_plane.get("cfa_pattern", entry.get("cfa_pattern"))
    if recorded_cfa is not None and recorded_cfa != rig_config["cfa_pattern"]:
        reasons.append(
            "CFA pattern changed (calibrated with {}, rig is now {})"
            .format(recorded_cfa, rig_config["cfa_pattern"]))

    recorded_which = measurement_plane.get("green_which")
    if recorded_which is not None and recorded_which != rig_config["green_which"]:
        reasons.append(
            "green-which changed (calibrated with green_which={}, rig is now {})"
            .format(recorded_which, rig_config["green_which"]))

    return reasons


def format_staleness_suffix(reasons):
    """The one shared phrasing every status display appends when
    calibration_staleness finds a mismatch, so calibrate.py's own window,
    both calibration wizards, and measure.py's gating status all say the
    same thing rather than each inventing its own wording. "" if reasons
    is empty (nothing to append, not even a trailing space)."""
    if not reasons:
        return ""
    return "  ⚠ STALE ({}) -- re-measure recommended".format("; ".join(reasons))


try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel,
                                 QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
                                 QLineEdit, QScrollArea, QFileDialog, QMessageBox,
                                 QWizard, QWizardPage)
    from PyQt5.QtGui import QPixmap, QPainter, QPen, QColor, QImage
    from PyQt5.QtCore import Qt, pyqtSignal
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


if _HAVE_QT:

    def array_to_qimage(u8):
        """A 2-D uint8 array (already display-stretched by stretch_to_uint8)
        as a QPixmap, Format_Grayscale8. .copy() takes QImage off the numpy
        buffer entirely, so a transient array going out of scope afterward
        cannot corrupt the pixmap."""
        u8 = np.ascontiguousarray(u8)
        h, w = u8.shape
        qimg = QImage(u8.data, w, h, w, QImage.Format_Grayscale8)
        return QPixmap.fromImage(qimg.copy())


if _HAVE_QT:

    class ImageCanvas(QLabel):
        """Displays a QPixmap at a controllable zoom level and reports clicks
        in NATIVE image pixel coordinates (see widget_to_native), not widget
        coordinates. Draws the calibration points and the line between them
        directly in paintEvent, over a CACHED pre-scaled pixmap (recomputed
        only when the zoom or source image actually changes, not on every
        repaint) -- overlay only, the same rule qt_shell.py's own preview
        overlay follows: nothing drawn here ever touches the underlying image
        data, only what is composited on top of it for display.
        """

        def __init__(self, on_click):
            super().__init__()
            self._pixmap = None
            self._scaled_pixmap = None
            self.zoom = 1.0
            self._points = []   # up to 2 native (x, y) tuples
            self._on_click = on_click
            self.setMouseTracking(True)

        def set_image(self, pixmap):
            self._pixmap = pixmap
            self._points = []
            self._apply_zoom()

        def set_zoom(self, zoom):
            self.zoom = max(0.05, min(8.0, zoom))
            self._apply_zoom()

        def native_size(self):
            if self._pixmap is None:
                return (0, 0)
            return (self._pixmap.width(), self._pixmap.height())

        def _apply_zoom(self):
            if self._pixmap is None:
                return
            w = max(1, int(self._pixmap.width() * self.zoom))
            h = max(1, int(self._pixmap.height() * self.zoom))
            # Target w/h are computed from the SAME zoom factor applied to
            # both source dimensions, so they already share the source's
            # aspect ratio; KeepAspectRatio here is just a safety backstop; it
            # should not actually rescale beyond int-rounding of w/h.
            self._scaled_pixmap = self._pixmap.scaled(
                w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.setFixedSize(self._scaled_pixmap.size())
            self.update()

        def set_points(self, points):
            self._points = list(points)
            self.update()

        def mousePressEvent(self, ev):
            if self._pixmap is None:
                return
            native = widget_to_native((ev.x(), ev.y()), self.zoom)
            self._on_click(native)

        def paintEvent(self, ev):
            painter = QPainter(self)
            if self._scaled_pixmap is not None:
                painter.drawPixmap(0, 0, self._scaled_pixmap)
            pen = QPen(QColor(255, 210, 0))
            pen.setWidth(2)
            painter.setPen(pen)
            screen_pts = [(x * self.zoom, y * self.zoom) for x, y in self._points]
            for (sx, sy) in screen_pts:
                painter.drawLine(int(sx - 8), int(sy), int(sx + 8), int(sy))
                painter.drawLine(int(sx), int(sy - 8), int(sx), int(sy + 8))
            if len(screen_pts) == 2:
                painter.drawLine(int(screen_pts[0][0]), int(screen_pts[0][1]),
                                 int(screen_pts[1][0]), int(screen_pts[1][1]))
            painter.end()


    class CalibrationWindow(QMainWindow):
        """The calibration tool's window: an objective picker (with the
        currently-saved calibration for it shown alongside), a zoomable/
        pannable image view for clicking two stage-micrometer ruling marks,
        a known-distance entry, and Compute & Save.
        """

        # Emitted by "Restart wizard...", right before this window closes, so
        # main()'s event loop knows to run CalibrationWizard again rather than
        # exit -- the "manually restart to set new data points" path onto an
        # otherwise-unchanged window.
        restart_requested = pyqtSignal()

        def __init__(self, image_path=None, objective=None):
            super().__init__()
            self.setWindowTitle("Zynergy spatial calibration")
            self._image_path = None
            self._green_plane = None
            self._focus_score = None
            self._native_points = []   # up to 2 (x, y) in native GREEN-PLANE pixels

            self.canvas = ImageCanvas(self._on_canvas_click)
            self.scroll = QScrollArea()
            self.scroll.setWidget(self.canvas)
            self.scroll.setWidgetResizable(False)

            self.objective_combo = QComboBox()
            self.objective_combo.setEditable(True)
            for obj in DEFAULT_OBJECTIVES:
                self.objective_combo.addItem(obj)
            self.objective_combo.currentTextChanged.connect(self._on_objective_changed)

            self.target_combo = QComboBox()
            self.target_combo.setEditable(True)
            for t in DEFAULT_TARGET_TYPES:
                self.target_combo.addItem(t)

            self.lens_label = QLabel("Lens: {:.1f}x (fixed)".format(REDUCTION_LENS))

            self.existing_label = QLabel("")
            self.existing_label.setWordWrap(True)

            self.focus_label = QLabel("Focus score: (open an image)")
            self.focus_label.setWordWrap(True)

            self.zoom_out_btn = QPushButton("-")
            self.zoom_in_btn = QPushButton("+")
            self.zoom_fit_btn = QPushButton("Fit")
            self.zoom_100_btn = QPushButton("100%")
            self.zoom_label = QLabel("100%")
            self.zoom_out_btn.clicked.connect(self._zoom_out)
            self.zoom_in_btn.clicked.connect(self._zoom_in)
            self.zoom_fit_btn.clicked.connect(self._fit_to_window)
            self.zoom_100_btn.clicked.connect(self._zoom_100)

            self.points_label = QLabel("Open an image, then click two ruling "
                                       "marks on the stage micrometer.")
            self.points_label.setWordWrap(True)

            self.distance_input = QLineEdit()
            self.distance_input.setPlaceholderText(
                "known distance between the two points, in micrometers")

            self.compute_btn = QPushButton("Compute && Save")
            self.compute_btn.setEnabled(False)
            self.compute_btn.clicked.connect(self._on_compute_and_save)

            self.reset_btn = QPushButton("Reset points")
            self.reset_btn.clicked.connect(self._on_reset_points)

            open_btn = QPushButton("Open image...")
            open_btn.clicked.connect(self._on_open)

            restart_btn = QPushButton("Restart wizard...")
            restart_btn.setEnabled(_wizard_pages is not None)
            if _wizard_pages is None:
                restart_btn.setToolTip("wizard_pages.py not alongside this file")
            restart_btn.clicked.connect(self._on_restart_wizard)

            top_row = QHBoxLayout()
            top_row.addWidget(open_btn)
            top_row.addWidget(restart_btn)
            top_row.addWidget(QLabel("Objective:"))
            top_row.addWidget(self.objective_combo)
            top_row.addWidget(QLabel("Target:"))
            top_row.addWidget(self.target_combo)
            top_row.addWidget(self.lens_label)
            top_row.addStretch(1)
            top_row.addWidget(self.zoom_out_btn)
            top_row.addWidget(self.zoom_label)
            top_row.addWidget(self.zoom_in_btn)
            top_row.addWidget(self.zoom_fit_btn)
            top_row.addWidget(self.zoom_100_btn)

            bottom = QVBoxLayout()
            bottom.addWidget(self.existing_label)
            bottom.addWidget(self.focus_label)
            bottom.addWidget(self.points_label)
            dist_row = QHBoxLayout()
            dist_row.addWidget(self.distance_input, 1)
            dist_row.addWidget(self.reset_btn)
            dist_row.addWidget(self.compute_btn)
            bottom.addLayout(dist_row)

            central = QWidget()
            main_lay = QVBoxLayout(central)
            main_lay.addLayout(top_row)
            main_lay.addWidget(self.scroll, 1)
            main_lay.addLayout(bottom)
            self.setCentralWidget(central)

            if image_path:
                self._load_image(image_path)
            if objective:
                idx = self.objective_combo.findText(objective)
                if idx >= 0:
                    self.objective_combo.setCurrentIndex(idx)
                else:
                    self.objective_combo.setEditText(objective)
            self._refresh_existing_label()

        # --- image loading ---------------------------------------------------
        def _load_image(self, path):
            try:
                raw_path = resolve_raw_path(path)
                green = load_green_plane(raw_path)
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "Could not load image", str(exc))
                return
            except Exception as exc:   # tifffile/IO errors etc.
                QMessageBox.warning(
                    self, "Could not load image",
                    "Failed to read {}: {}".format(Path(path).name, exc))
                return
            self._image_path = raw_path
            self._green_plane = green
            if _focus is not None:
                self._focus_score = _focus.variance_of_laplacian(green, blur_radius=1)
                self.focus_label.setText(
                    "Focus score: {:.2f} (evidence only, not a gate -- compare "
                    "against a known-sharp shot before trusting a low one)"
                    .format(self._focus_score))
            else:
                self._focus_score = None
                self.focus_label.setText(
                    "Focus score: unavailable (focus.py not found next to this file)")
            pixmap = array_to_qimage(stretch_to_uint8(green))
            self.canvas.set_image(pixmap)
            self._native_points = []
            self._update_points_ui()
            self._fit_to_window()

        def _on_open(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Open calibration image", "",
                "Raw / mosaic (*.dng *.tif *.tiff);;JPEG preview (*.jpg *.jpeg);;"
                "All files (*)")
            if path:
                self._load_image(path)

        def _on_restart_wizard(self):
            # Just signals + closes; main()'s loop is what actually reruns
            # CalibrationWizard and opens the next window. Keeping that in
            # main() (rather than doing it here) means this window never
            # needs to construct or outlive its own replacement.
            self.restart_requested.emit()
            self.close()

        # --- points ------------------------------------------------------
        def _on_canvas_click(self, native_point):
            if len(self._native_points) >= 2:
                self._native_points = []   # a third click starts over
            self._native_points.append(native_point)
            self.canvas.set_points(self._native_points)
            self._update_points_ui()

        def _on_reset_points(self):
            self._native_points = []
            self.canvas.set_points([])
            self._update_points_ui()

        def _update_points_ui(self):
            if len(self._native_points) == 2:
                dist = pixel_distance(*self._native_points)
                self.points_label.setText(
                    "Point A: ({:.1f}, {:.1f})   Point B: ({:.1f}, {:.1f})\n"
                    "Pixel distance: {:.2f} px".format(
                        self._native_points[0][0], self._native_points[0][1],
                        self._native_points[1][0], self._native_points[1][1], dist))
                self.compute_btn.setEnabled(True)
            elif len(self._native_points) == 1:
                self.points_label.setText("Point A set. Click a second ruling mark "
                                         "(as far from the first as the field of "
                                         "view allows, for the best accuracy).")
                self.compute_btn.setEnabled(False)
            else:
                self.points_label.setText(
                    "Click two ruling marks on the stage micrometer.")
                self.compute_btn.setEnabled(False)

        # --- zoom ------------------------------------------------------
        def _zoom_in(self):
            self.canvas.set_zoom(self.canvas.zoom * 1.25)
            self._update_zoom_label()

        def _zoom_out(self):
            self.canvas.set_zoom(self.canvas.zoom / 1.25)
            self._update_zoom_label()

        def _zoom_100(self):
            self.canvas.set_zoom(1.0)
            self._update_zoom_label()

        def _fit_to_window(self):
            nw, nh = self.canvas.native_size()
            if nw == 0 or nh == 0:
                return
            avail_w = max(1, self.scroll.viewport().width())
            avail_h = max(1, self.scroll.viewport().height())
            zoom = min(avail_w / nw, avail_h / nh)
            self.canvas.set_zoom(zoom)
            self._update_zoom_label()

        def _update_zoom_label(self):
            self.zoom_label.setText("{:.0f}%".format(self.canvas.zoom * 100))

        # --- objective / calibration -------------------------------------
        def _on_objective_changed(self, text):
            self._refresh_existing_label()

        def _refresh_existing_label(self):
            store = load_calibrations()
            obj = self.objective_combo.currentText().strip()
            entry = current_calibration(obj, store)
            if entry:
                n = len(store.get(obj, []))
                self.existing_label.setText(
                    "Current calibration for {}: {:.4f} \u00b5m/px (set {}, "
                    "#{} in history){}".format(
                        obj, entry["um_per_px"],
                        entry.get("calibrated_at", "unknown date"), n,
                        format_staleness_suffix(calibration_staleness(entry))))
            else:
                self.existing_label.setText(
                    "No calibration saved yet for {}.".format(obj or "(no objective set)"))

        def _on_compute_and_save(self):
            if len(self._native_points) != 2:
                return
            try:
                known_um = float(self.distance_input.text().strip())
            except ValueError:
                QMessageBox.warning(self, "Invalid distance",
                                   "Enter a positive number of micrometers.")
                return
            obj = self.objective_combo.currentText().strip()
            if not obj:
                QMessageBox.warning(self, "Objective required",
                                   "Enter or choose an objective name.")
                return
            target_type = self.target_combo.currentText().strip() or "unspecified"
            try:
                entry = build_calibration_entry(
                    self._image_path, self._native_points[0], self._native_points[1],
                    known_um, objective=obj, target_type=target_type,
                    focus_score=self._focus_score)
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot calibrate", str(exc))
                return
            store = load_calibrations()
            existing = current_calibration(obj, store)
            if existing:
                n = len(store.get(obj, []))
                resp = QMessageBox.question(
                    self, "Add new calibration?",
                    "{} already has {} calibration(s) on record, most recent "
                    "{:.4f} \u00b5m/px ({}). This adds a new entry as current; "
                    "nothing already saved is deleted or overwritten. Continue?"
                    .format(obj, n, existing["um_per_px"],
                            existing.get("calibrated_at", "unknown date")),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if resp != QMessageBox.Yes:
                    return
            save_calibration(obj, entry)
            self._refresh_existing_label()
            QMessageBox.information(
                self, "Saved", "Saved calibration for {}: {:.4f} \u00b5m/px"
                .format(obj, entry["um_per_px"]))


    class _SetupPage(QWizardPage):
        """Wizard page 1: objective + target type. Next enabled once an
        objective string is chosen. Shows the existing calibration for it via
        current_calibration -- reused, not reimplemented -- the same lookup
        CalibrationWindow's own _refresh_existing_label already calls."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setTitle("Objective")
            self.setSubTitle("Pick the objective and target this calibration is for.")

            self.objective_combo = QComboBox()
            self.objective_combo.setEditable(True)
            for obj in DEFAULT_OBJECTIVES:
                self.objective_combo.addItem(obj)
            self.objective_combo.currentTextChanged.connect(self._on_changed)

            self.target_combo = QComboBox()
            self.target_combo.setEditable(True)
            for t in DEFAULT_TARGET_TYPES:
                self.target_combo.addItem(t)

            self.existing_label = QLabel("")
            self.existing_label.setWordWrap(True)

            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Objective:"))
            lay.addWidget(self.objective_combo)
            lay.addWidget(QLabel("Target type:"))
            lay.addWidget(self.target_combo)
            lay.addWidget(self.existing_label)
            self._refresh_existing()

        def _on_changed(self, _text):
            self._refresh_existing()
            self.completeChanged.emit()

        def _refresh_existing(self):
            obj = self.objective_combo.currentText().strip()
            entry = current_calibration(obj) if obj else None
            if entry:
                n = len(load_calibrations().get(obj, []))
                self.existing_label.setText(
                    "Current calibration for {}: {:.4f} \u00b5m/px (set {}, "
                    "#{} in history){}".format(
                        obj, entry["um_per_px"], entry.get("calibrated_at", "unknown date"), n,
                        format_staleness_suffix(calibration_staleness(entry))))
            else:
                self.existing_label.setText(
                    "No calibration saved yet for {}.".format(obj or "(no objective set)"))

        def isComplete(self):
            return bool(self.objective_combo.currentText().strip())

        def objective(self):
            return self.objective_combo.currentText().strip()

        def target_type(self):
            return self.target_combo.currentText().strip() or "unspecified"


    class CalibrationWizard(QWizard):
        """The paged wizard (build checklist section 4): page 1 picks the
        objective/target, page 2 gets an image -- an existing file or a fresh
        live capture, via wizard_pages.ImageSourcePage. Finishing hands
        (objective, image_path) to main(), which opens the unchanged
        CalibrationWindow with them; this only replaces how that window gets
        its two startup arguments, never its own load/measure/save logic."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Zynergy spatial calibration - setup")
            if _wizard_pages is None:
                raise RuntimeError(
                    "wizard_pages.py could not be imported; needed for the "
                    "image-source page")
            self.setup_page = _SetupPage()
            self.image_page = _wizard_pages.ImageSourcePage(self._validate_image)
            self.addPage(self.setup_page)
            self.addPage(self.image_page)
            self.finished.connect(lambda _res: self.image_page.capture_pane.stop())

        def _validate_image(self, path):
            try:
                raw_path = resolve_raw_path(path)
                green = load_green_plane(raw_path)
            except (ValueError, RuntimeError) as exc:
                return False, str(exc)
            except Exception as exc:
                return False, "Failed to read {}: {}".format(Path(path).name, exc)
            msg = "Loaded {} ({} x {} green plane)".format(
                Path(raw_path).name, green.shape[1], green.shape[0])
            if _focus is not None:
                score = _focus.variance_of_laplacian(green, blur_radius=1)
                msg += "\nFocus score: {:.2f} (evidence only, not a gate)".format(score)
            return True, msg

        def objective(self):
            return self.setup_page.objective()

        def target_type(self):
            return self.setup_page.target_type()

        def image_path(self):
            return self.image_page.resolved_path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Zynergy spatial (\u00b5m/px) calibration tool.")
    ap.add_argument("image", nargs="?", default=None,
                    help="stage micrometer image: the .dng capture.py writes "
                         "(recommended), or its sibling .jpg preview, which "
                         "auto-resolves to the .dng next to it")
    ap.add_argument("--objective", default=None, help="objective name, e.g. 40x")
    a = ap.parse_args(argv)
    if not _HAVE_QT:
        sys.exit("PyQt5 not available. Use --render-check for the headless self-check, "
                 "or install python3-pyqt5 for the GUI.")
    app = QApplication(sys.argv)

    if a.image or a.objective:
        # CLI shortcut, unchanged: skip the wizard, open the window directly.
        win = CalibrationWindow(image_path=a.image, objective=a.objective)
        win.resize(1200, 800)
        win.show()
        app.exec_()
        return

    # No args: the wizard is the new default interactive entry point. Looping
    # on app.exec_() is what makes "Restart wizard..." work -- closing the
    # window ends that inner event loop (quitOnLastWindowClosed), and the
    # restarted flag (set only by CalibrationWindow.restart_requested) decides
    # whether to run the wizard again or return.
    while True:
        wizard = CalibrationWizard()
        if wizard.exec_() != QWizard.Accepted:
            return
        win = CalibrationWindow(image_path=wizard.image_path(), objective=wizard.objective())
        win.resize(1200, 800)
        restarted = []
        win.restart_requested.connect(lambda: restarted.append(True))
        win.show()
        app.exec_()
        if not restarted:
            return


def render_check():
    # Distance + calibration math
    assert abs(pixel_distance((0, 0), (3, 4)) - 5.0) < 1e-9, "3-4-5 triangle distance wrong"
    calib = compute_calibration(500.0, 5000.0)   # 500px == 5000um -> 10 um/px
    assert abs(calib["um_per_px"] - 10.0) < 1e-9, "um_per_px arithmetic wrong"
    assert abs(calib["px_per_um"] - 0.1) < 1e-9, "px_per_um is not the reciprocal"

    try:
        compute_calibration(5.0, 100.0)   # well under MIN_CALIBRATION_PX
        raise AssertionError("expected ValueError for a too-close pair of points")
    except ValueError:
        pass
    try:
        compute_calibration(500.0, 0.0)   # non-positive known distance
        raise AssertionError("expected ValueError for a non-positive known distance")
    except ValueError:
        pass
    print("distance/calibration math check PASS: 3-4-5 triangle, reciprocal check, "
          "both degenerate-input guards raise")

    # widget_to_native: a click at (250, 100) on a 2x-zoomed image is native (125, 50)
    nx, ny = widget_to_native((250.0, 100.0), 2.0)
    assert abs(nx - 125.0) < 1e-9 and abs(ny - 50.0) < 1e-9, "zoom-to-native mapping wrong"
    print("widget_to_native check PASS")

    # --- green-plane extraction from a raw mosaic -------------------------
    assert _debayer is not None, (
        "debayer.py must import cleanly from calibrate.py's own directory "
        "(extract_green is reused from it, not reimplemented)")
    mosaic = np.arange(64, dtype=np.uint16).reshape(8, 8)
    tmp_mosaic = Path("/tmp/zynergy_calib_render_check_mosaic.tif")
    tifffile.imwrite(str(tmp_mosaic), mosaic)
    try:
        green = load_green_plane(tmp_mosaic)   # default BGGR, green-which=1
        assert green.shape == (4, 4), "BGGR green-which=1 should be half-res each axis"
        assert green[0, 0] == 1 and green[-1, -1] == 55, \
            "wrong sub-plane extracted -- green-which/pattern mismatch"
        print("load_green_plane check PASS: correct half-res green sub-plane "
              "extracted from a synthetic raw mosaic")
    finally:
        tmp_mosaic.unlink(missing_ok=True)

    # a non-2D (already-demosaiced) input must raise ValueError, not crash a GUI
    tmp_bad = Path("/tmp/zynergy_calib_render_check_bad.tif")
    tifffile.imwrite(str(tmp_bad), np.zeros((4, 4, 3), dtype=np.uint8))
    try:
        try:
            load_mosaic_array(tmp_bad)
            raise AssertionError("expected ValueError for a non-mosaic (RGB) input")
        except ValueError:
            pass
        print("load_mosaic_array check PASS: rejects a non single-channel input")
    finally:
        tmp_bad.unlink(missing_ok=True)

    # --- resolve_raw_path: sibling resolution, pass-through, missing sibling
    rr_dir = Path("/tmp/zynergy_calib_render_check_paths")
    if rr_dir.exists():
        import shutil
        shutil.rmtree(rr_dir)
    rr_dir.mkdir(parents=True)
    try:
        jpg = rr_dir / "snap_frame_0000.jpg"
        dng = rr_dir / "snap_frame_0000.dng"
        jpg.touch()
        dng.touch()
        assert resolve_raw_path(jpg) == dng, "a .jpg should resolve to its sibling .dng"
        assert resolve_raw_path(dng) == dng, "a .dng should pass through unchanged"
        orphan = rr_dir / "orphan_frame_0000.jpg"
        orphan.touch()
        try:
            resolve_raw_path(orphan)
            raise AssertionError("expected ValueError for a .jpg with no sibling .dng")
        except ValueError:
            pass
        print("resolve_raw_path check PASS: sibling resolution, pass-through, "
              "and a missing sibling refuses rather than guessing")
    finally:
        import shutil
        shutil.rmtree(rr_dir)

    # --- stretch_to_uint8: display-only, never the measurement -------------
    ramp = np.linspace(0.0, 1000.0, 100).reshape(10, 10)
    u8 = stretch_to_uint8(ramp)
    assert u8.dtype == np.uint8 and u8.shape == ramp.shape
    assert u8.min() < 20 and u8.max() > 235, "percentile stretch should span most of 0-255"
    flat = np.full((4, 4), 42.0)
    u8_flat = stretch_to_uint8(flat)
    assert np.all(u8_flat == 0), "a degenerate constant plane should not divide by zero"
    print("stretch_to_uint8 check PASS: spans the display range, degenerate input safe")

    # --- build_calibration_entry: full provenance now recorded -------------
    entry = build_calibration_entry(
        Path("/tmp/fake.dng"), (0.0, 0.0), (500.0, 0.0), 500.0,
        objective="40x", target_type="stage micrometer", focus_score=300.0)
    assert entry["objective"] == "40x"
    assert entry["reduction_lens"] == REDUCTION_LENS
    assert entry["target_type"] == "stage micrometer"
    assert entry["focus_score"] == 300.0
    assert entry["measurement_plane"] == {"cfa_pattern": DEFAULT_CFA_PATTERN,
                                          "green_which": DEFAULT_GREEN_WHICH}
    entry_no_focus = build_calibration_entry(
        Path("/tmp/fake.dng"), (0.0, 0.0), (500.0, 0.0), 500.0,
        objective="40x", target_type="stage micrometer", focus_score=None)
    assert entry_no_focus["focus_score"] is None, "an unavailable focus score stays None"
    print("build_calibration_entry check PASS: objective, reduction lens, target "
          "type, focus score, and measurement-plane provenance all recorded")

    # --- calibration store: append-only, supersedes chain -------------------
    global CALIBRATION_PATH
    orig_path = CALIBRATION_PATH
    tmp_dir = Path("/tmp/zynergy_calib_render_check")
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)
    CALIBRATION_PATH = tmp_dir / "calibration.json"
    try:
        assert load_calibrations() == {}, "a missing store should load as {}"
        assert current_calibration("40x") is None, "no history yet should read as None"

        entry_40x_v1 = build_calibration_entry(
            Path("/tmp/fake_40x.dng"), (100.0, 100.0), (600.0, 100.0), 500.0,
            objective="40x", target_type="stage micrometer", focus_score=321.5)
        assert abs(entry_40x_v1["um_per_px"] - 1.0) < 1e-9, "40x entry arithmetic wrong"
        store = save_calibration("40x", entry_40x_v1)
        saved_v1 = store["40x"][-1]
        assert saved_v1["supersedes"] is None, "the first entry supersedes nothing"
        assert "entry_id" in saved_v1

        entry_100x = build_calibration_entry(
            Path("/tmp/fake_100x.dng"), (50.0, 50.0), (450.0, 50.0), 200.0,
            objective="100x", target_type="stage micrometer", focus_score=410.0)
        save_calibration("100x", entry_100x)

        # Re-calibrating 40x must APPEND, not overwrite: v1 must survive unchanged.
        entry_40x_v2 = build_calibration_entry(
            Path("/tmp/fake_40x_redo.dng"), (0.0, 0.0), (1000.0, 0.0), 800.0,
            objective="40x", target_type="stage micrometer", focus_score=455.2)
        save_calibration("40x", entry_40x_v2)

        store2 = load_calibrations()
        assert len(store2["40x"]) == 2, "a redo should APPEND, not replace"
        assert store2["40x"][0] == saved_v1, "the original entry must survive byte-for-byte"
        assert store2["40x"][1]["supersedes"] == saved_v1["entry_id"], \
            "the new entry must chain 'supersedes' to the one it replaces as current"
        assert store2["40x"][1]["source_image"] == "/tmp/fake_40x_redo.dng"
        assert abs(store2["40x"][1]["um_per_px"] - 0.8) < 1e-9
        assert len(store2["100x"]) == 1, "saving 40x must not disturb 100x's own history"
        assert store2["100x"][0]["source_image"] == "/tmp/fake_100x.dng", \
            "provenance (source image) not preserved"

        cur = current_calibration("40x", store2)
        assert cur is store2["40x"][-1] and abs(cur["um_per_px"] - 0.8) < 1e-9, \
            "current_calibration must read the LATEST entry, not the first"

        print("calibration store check PASS: append-only, supersedes chain intact, "
              "per-objective isolation, provenance preserved, missing store is a "
              "clean {}")
    finally:
        CALIBRATION_PATH = orig_path

    # --- config-drift invalidation (section 13) -----------------------------
    rig = current_rig_config()
    assert rig == {"reduction_lens": REDUCTION_LENS, "cfa_pattern": DEFAULT_CFA_PATTERN,
                  "green_which": DEFAULT_GREEN_WHICH}

    # a spatial entry matching the current rig exactly: no staleness
    fresh_spatial = {
        "reduction_lens": REDUCTION_LENS,
        "measurement_plane": {"cfa_pattern": DEFAULT_CFA_PATTERN,
                              "green_which": DEFAULT_GREEN_WHICH},
    }
    assert calibration_staleness(fresh_spatial) == []
    assert format_staleness_suffix(calibration_staleness(fresh_spatial)) == ""

    # a reduction-lens drift is flagged, worded via the shared formatter
    stale_lens = dict(fresh_spatial, reduction_lens=1.0)
    reasons = calibration_staleness(stale_lens)
    assert len(reasons) == 1 and "reduction lens" in reasons[0], reasons
    suffix = format_staleness_suffix(reasons)
    assert suffix.startswith("  ⚠ STALE") and "reduction lens" in suffix

    # a CFA/green-which drift, checked independently and together
    stale_cfa = dict(fresh_spatial, measurement_plane={
        "cfa_pattern": "RGGB", "green_which": DEFAULT_GREEN_WHICH})
    assert len(calibration_staleness(stale_cfa)) == 1 and \
        "CFA pattern" in calibration_staleness(stale_cfa)[0]

    stale_which = dict(fresh_spatial, measurement_plane={
        "cfa_pattern": DEFAULT_CFA_PATTERN, "green_which": 2})
    assert len(calibration_staleness(stale_which)) == 1 and \
        "green-which" in calibration_staleness(stale_which)[0]

    stale_both = dict(reduction_lens=1.0, measurement_plane={
        "cfa_pattern": "RGGB", "green_which": 2})
    assert len(calibration_staleness(stale_both)) == 3, \
        "all three drifted fields should each contribute their own reason"

    # a CA-shaped entry: top-level reduction_lens + cfa_pattern, no
    # measurement_plane, no green_which -- must work the same way, and must
    # never invent a green_which mismatch for a field CA entries don't have
    fresh_ca = {"reduction_lens": REDUCTION_LENS, "cfa_pattern": DEFAULT_CFA_PATTERN}
    assert calibration_staleness(fresh_ca) == []
    stale_ca = {"reduction_lens": 1.0, "cfa_pattern": DEFAULT_CFA_PATTERN}
    ca_reasons = calibration_staleness(stale_ca)
    assert len(ca_reasons) == 1 and "reduction lens" in ca_reasons[0]

    # an entry predating a field entirely (no reduction_lens at all) must be
    # trusted on that field, not flagged -- absence is not evidence
    no_lens_recorded = {"measurement_plane": {"cfa_pattern": DEFAULT_CFA_PATTERN,
                                              "green_which": DEFAULT_GREEN_WHICH}}
    assert calibration_staleness(no_lens_recorded) == [], \
        "a field the entry never recorded must not be flagged as stale"
    assert calibration_staleness({}) == [], \
        "an entry with none of these fields at all should read as fresh, " \
        "not raise or falsely flag"

    print("calibration_staleness check PASS: fresh entry is quiet, each of "
          "reduction lens/CFA pattern/green-which drift is independently "
          "detected (and all three together), CA-shaped entries (no "
          "measurement_plane, no green_which) work identically, fields an "
          "entry never recorded are trusted rather than flagged")


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        main([a for a in sys.argv[1:] if a != "--render-check"])
