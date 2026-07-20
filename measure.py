"""measure.py - the analysis GUI: canvas and measurement tools (build checklist
section 7).

Working now: the QGraphicsView canvas (pan/zoom/hit-testing all Qt-native,
per the checklist's own instruction, not the manual painting calibrate.py
and qt_shell.py use for their live overlays), and all four measurement
tools -- distance, angle, free polygon, ellipse -- reusing annotations.py's
mark builders directly.

Ellipse fit: `fit_ellipse()` below is the algebraic least-squares primitive
the checklist called out as its own piece, separate from annotations.py's
build_ellipse_mark() (which only ever recorded a fit's RESULT, never computed
one). Fitzgibbon-style direct fit, using Halir & Flusser's numerically stable
quadratic/linear split of the design matrix rather than the original 6x6
generalized eigenproblem, which is ill-conditioned near a circle. Boundary
points in (5+, clicked same as a polygon), center/semi-axes/angle out, fed
straight into build_ellipse_mark() -- no geometry duplicated between the two
files, same pattern distance/angle/polygon already use.

Provenance guard (checklist): a .tif's embedded JSON description (the one
debayer.py itself writes) is read before anything is measured on it. If it is
flagged "display-referred derivative (NOT a measurement)" -- e.g. a tonemapped
_display.tif -- this refuses outright rather than measuring apparent edges
that sharpen/CLAHE/tonemap already moved. A raw .dng has no such tag at all
(nothing Zynergy-authored ever wrote one), which is fine: no flag means no
refusal.

Input, this phase: a raw .dng/mosaic master (green-which=1 extracted, same as
calibrate.py), OR an already-extracted green-plane TIFF (debayer.py's own
--green output, or a frame_average.py average), distinguished by shape alone
-- a full-sensor mosaic and a half-res green plane are unambiguously different
sizes. Broader kind support (rgb / hdr_linear / averaged, per the annotation
record schema) is a natural near-term extension, not built this round; every
mark saved here records kind="green".

Calibration gating (checklist): every measurement tool stays disabled until
an objective is picked AND that objective has a calibration on record.
Reuses calibrate.py's own current_calibration(), never a second copy of that
lookup.

Coordinates: green-plane pixels, exact, per the checklist ("the hash pins the
plane, so pixel coordinates there are unambiguous"). No fractional coordinates
anywhere in this file, unlike the live focus box.

Two ways to run:
  python3 measure.py --render-check      headless: pure logic only (loading,
                                         the provenance guard, hash
                                         consistency, calibration gating),
                                         no PyQt5, no image file.
  python3 measure.py [image]             the GUI. image is optional; File >
                                         Open works from inside too.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

try:
    from . import calibrate as _calibrate
except ImportError:
    try:
        import calibrate as _calibrate
    except ImportError:
        _calibrate = None

try:
    from . import debayer as _debayer
except ImportError:
    try:
        import debayer as _debayer
    except ImportError:
        _debayer = None

try:
    from . import annotations as _annotations
except ImportError:
    try:
        import annotations as _annotations
    except ImportError:
        _annotations = None

try:
    from . import pixel_hash as _pixel_hash
except ImportError:
    try:
        import pixel_hash as _pixel_hash
    except ImportError:
        _pixel_hash = None

try:
    from . import stacks as _stacks
except ImportError:
    try:
        import stacks as _stacks
    except ImportError:
        _stacks = None

try:
    from . import export as _export
except ImportError:
    try:
        import export as _export
    except ImportError:
        _export = None

try:
    from .camera_backend import FULL_RES
except ImportError:
    try:
        from camera_backend import FULL_RES
    except ImportError:
        FULL_RES = (4056, 3040)   # IMX477 full sensor, matches camera_backend.py's own constant

GREEN_PLANE_RES = (FULL_RES[0] // 2, FULL_RES[1] // 2)

# The shared image-source wizard page (build checklist section 4): pick an
# image already shot, or shoot a new one live. Optional the same way every
# other integration above is -- the wizard is simply unavailable
# (MeasureWindow itself still opens fine via the CLI [image] argument) if
# wizard_pages.py is not alongside this file.
try:
    from . import wizard_pages as _wizard_pages
except ImportError:
    try:
        import wizard_pages as _wizard_pages
    except ImportError:
        _wizard_pages = None


# ---------------------------------------------------------------------------
# Pure loading + provenance guard (no Qt, no camera)
# ---------------------------------------------------------------------------

def _read_description_json(path):
    """Best-effort read of a TIFF's embedded JSON description (debayer.py's
    own provenance dict). None for a file with no description tag, or one
    that isn't valid JSON -- a raw camera .dng carries neither, which is the
    common, unflagged case, not an error."""
    try:
        import tifffile
        with tifffile.TiffFile(str(path)) as tf:
            desc = tf.pages[0].description
        return json.loads(desc) if desc else None
    except Exception:
        return None


def check_measurement_provenance(path):
    """Refuse a display-referred derivative outright. Reads the embedded
    JSON; if its 'kind' says this is a display-referred derivative (the
    exact phrase debayer.py itself writes onto a tonemapped _display.tif),
    raises ValueError rather than silently measuring apparent edges that
    sharpen/CLAHE/tonemap already moved. No description, or one with no such
    flag, passes through -- this covers a raw .dng, a green-plane extraction,
    and a linear RGB master alike, none of which debayer.py flags this way."""
    desc = _read_description_json(path)
    if desc and isinstance(desc.get("kind"), str) and "NOT a measurement" in desc["kind"]:
        raise ValueError(
            "{} is flagged as {!r}, not a measurement surface. Point at the "
            "raw .dng, an extracted green plane, or a linear master instead."
            .format(Path(path).name, desc["kind"]))


def load_measurement_plane(path):
    """The measurement substrate, whichever of the two supported input shapes
    it is: a full-sensor raw mosaic (.dng, or a frame_average.py master.tif)
    gets green-which=1 extracted (same call calibrate.py itself makes); an
    already half-res green plane (debayer.py's own --green output) is used
    as-is, no double extraction. Runs the provenance guard first. Raises
    ValueError for anything that is neither shape, or RuntimeError if
    debayer.py is not importable and extraction is actually needed."""
    if _calibrate is None:
        raise RuntimeError("calibrate.py could not be imported; needed for "
                           "resolve_raw_path/load_mosaic_array")
    check_measurement_provenance(path)
    resolved = _calibrate.resolve_raw_path(path)
    arr = _calibrate.load_mosaic_array(resolved)
    full_hw = (FULL_RES[1], FULL_RES[0])
    green_hw = (GREEN_PLANE_RES[1], GREEN_PLANE_RES[0])
    if arr.shape == full_hw:
        if _debayer is None:
            raise RuntimeError("debayer.py could not be imported; needed to "
                               "extract green from a full-sensor mosaic")
        plane, _rc = _debayer.extract_green(arr, _calibrate.DEFAULT_CFA_PATTERN,
                                            _calibrate.DEFAULT_GREEN_WHICH)
    elif arr.shape == green_hw:
        plane = arr
    else:
        raise ValueError(
            "{} has shape {}; expected a full-sensor raw mosaic {} or an "
            "already-extracted green plane {}.".format(
                Path(resolved).name, arr.shape, full_hw, green_hw))
    return plane


def current_um_per_px(objective):
    """The current um_per_px for an objective, or None if calibrate.py is
    unavailable or that objective has never been calibrated -- the single
    check every measurement tool's enabled state gates on."""
    if _calibrate is None or not objective:
        return None
    entry = _calibrate.current_calibration(objective)
    return entry["um_per_px"] if entry else None


def fit_ellipse(points):
    """The algebraic least-squares ellipse fit itself (checklist architecture
    seam #1's own piece, kept separate from annotations.py's
    build_ellipse_mark(), which only ever records a fit's RESULT). Fitzgibbon-
    style direct fit, via Halir & Flusser's numerically stable quadratic/
    linear split of the design matrix, rather than the original 6x6
    generalized eigenproblem, which is ill-conditioned near a circle -- the
    common case for a round spore.

    points: 5+ (x, y) boundary points, the same green-plane pixel
    coordinates every other mark type's points already use.

    Returns (center, axes_px, angle_deg): center is (cx, cy) in pixels;
    axes_px is (semi_major, semi_minor) in pixels (semi_major >= semi_minor
    always, regardless of which way the fit happened to come out); angle_deg
    is the semi-major axis's rotation from the +x axis, in the same y-down
    pixel frame the points came in.

    Raises ValueError for fewer than 5 points, or for points whose best-fit
    conic isn't an ellipse at all (collinear/degenerate input, or a fit that
    comes out parabolic/hyperbolic instead).
    """
    pts = np.asarray([(float(x), float(y)) for x, y in points], dtype=np.float64)
    if len(pts) < 5:
        raise ValueError("an ellipse fit needs at least 5 points, got {}".format(len(pts)))
    x = pts[:, 0]
    y = pts[:, 1]

    # Halir & Flusser: split the design matrix into its quadratic (D1) and
    # linear (D2) parts rather than building one ill-conditioned 6-column
    # matrix, then solve the quadratic part's 3x3 generalized eigenproblem
    # instead of the original's 6x6 one.
    D1 = np.vstack([x ** 2, x * y, y ** 2]).T
    D2 = np.vstack([x, y, np.ones_like(x)]).T
    S1 = D1.T @ D1
    S2 = D1.T @ D2
    S3 = D2.T @ D2
    try:
        T = -np.linalg.solve(S3, S2.T)
    except np.linalg.LinAlgError:
        raise ValueError("the boundary points are too degenerate to fit an ellipse")
    M = S1 + S2 @ T
    M = np.array([M[2] / 2, -M[1], M[0] / 2])
    eigval, eigvec = np.linalg.eig(M)
    # the ellipse-specific constraint 4ac - b^2 > 0 picks out the one
    # eigenvector (of three) that is actually an ellipse, not a parabola/
    # hyperbola -- Fitzgibbon's whole trick for a *direct* fit.
    cond = 4 * eigvec[0] * eigvec[2] - eigvec[1] ** 2
    valid = np.where(cond.real > 0)[0]
    if len(valid) == 0:
        raise ValueError("the boundary points do not admit an elliptical fit "
                         "(collinear or otherwise degenerate input)")
    a1 = eigvec[:, valid[0]].real
    a2 = T @ a1
    a, b, c, d, e, f = np.concatenate([a1, a2])
    return _conic_to_ellipse(a, b, c, d, e, f)


def _conic_to_ellipse(a, b, c, d, e, f):
    """General conic a*x^2 + b*xy + c*y^2 + d*x + e*y + f = 0, converted to
    (center, (semi_major, semi_minor), angle_deg) via the standard closed
    form (mathworld.wolfram.com/Ellipse.html). Raises ValueError if the
    conic isn't actually an ellipse (b^2 - 4ac >= 0, i.e. parabola or
    hyperbola) or the recovered axes are non-positive/non-finite."""
    b, d, e = b / 2.0, d / 2.0, e / 2.0
    den = b ** 2 - a * c
    if den >= 0:
        raise ValueError("fitted conic is not an ellipse (b^2 - 4ac >= 0)")
    x0 = (c * d - b * e) / den
    y0 = (a * e - b * d) / den

    num = 2 * (a * e ** 2 + c * d ** 2 + f * b ** 2 - 2 * b * d * e - a * c * f)
    fac = math.sqrt((a - c) ** 2 + 4 * b ** 2)
    axis1_sq = num / (den * (fac - a - c))
    axis2_sq = num / (den * (-fac - a - c))
    if (axis1_sq <= 0 or axis2_sq <= 0
            or not math.isfinite(axis1_sq) or not math.isfinite(axis2_sq)):
        raise ValueError("fitted ellipse has a non-positive or non-finite axis")
    axis1, axis2 = math.sqrt(axis1_sq), math.sqrt(axis2_sq)
    major, minor = max(axis1, axis2), min(axis1, axis2)

    if b == 0:
        phi = 0.0 if a < c else math.pi / 2
    else:
        phi = math.atan((2.0 * b) / (a - c)) / 2.0
        if a > c:
            phi += math.pi / 2
    if axis1 < axis2:
        phi += math.pi / 2
    phi %= math.pi

    return (x0, y0), (major, minor), math.degrees(phi)


def build_record_defaults(plane, objective):
    """The record_defaults annotations.save_mark() needs the first time an
    image is marked: shape/dtype fixed by the plane itself, kind="green"
    (the only kind this phase produces), calibration_ref naming the exact
    calibration entry in force right now."""
    calibration_ref = (_annotations.calibration_ref_for(objective)
                       if _annotations is not None else None)
    return {
        "shape": list(plane.shape),
        "dtype": str(plane.dtype),
        "kind": "green",
        "calibration_ref": calibration_ref,
        "source_sha256": None,
    }


def format_mark_result(mark):
    """A human-readable line for whatever a mark just computed, so the number
    that mattered (a measurement tool exists to produce a trustworthy number)
    is visible the moment it exists, not only recoverable by opening
    annotations.json by hand. Pure and Qt-free so it's covered by
    render_check regardless of whether PyQt5 is even installed here."""
    d = mark["derived"]
    t = mark["type"]
    if t == "distance":
        return "distance: {:.3f} \u00b5m  ({:.1f} px)".format(
            d["distance_um"], d["distance_px"])
    if t == "angle":
        return "angle: {:.2f}\u00b0".format(d["angle_deg"])
    if t == "polygon":
        return "polygon: area {:.2f} \u00b5m\u00b2, perimeter {:.2f} \u00b5m".format(
            d["area_um2"], d["perimeter_um"])
    if t == "ellipse":
        return ("ellipse: length {:.2f} \u00b5m, width {:.2f} \u00b5m, "
                "area {:.2f} \u00b5m\u00b2, Q {:.3f}").format(
            d["length_um"], d["width_um"], d["area_um2"], d["q_ratio"])
    return ""


try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel,
                                 QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
                                 QGraphicsView, QGraphicsScene, QFileDialog,
                                 QMessageBox, QButtonGroup, QWizard, QWizardPage)
    from PyQt5.QtGui import QPen, QColor, QPolygonF, QPainter
    from PyQt5.QtCore import Qt, QPointF, pyqtSignal
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


if _HAVE_QT:

    MARK_PEN = QPen(QColor(80, 220, 255), 2)        # committed marks
    PENDING_PEN = QPen(QColor(255, 210, 80), 2)      # in-progress
    PENDING_PEN.setStyle(Qt.DashLine)
    POINT_RADIUS = 4

    # ---------------------------------------------------------------------------
    # Z-stack support: filmstrip + onion-skin (build checklist section 8)
    # ---------------------------------------------------------------------------

    class FilmstripWidget(QWidget):
        """Filmstrip for z-stacks: thumbnails down the side, inactive dimmed,
        active lit. Tracks plane count and allows clicking to switch active
        plane. Per checklist §8, this is the home for per-plane sharpness
        score and exclude toggle (future section 13 work)."""

        active_plane_changed = pyqtSignal(int)  # emitted when user clicks a plane

        def __init__(self, parent=None):
            super().__init__(parent)
            self.planes = []  # list of {"plane_idx": int, "pixmap": QPixmap, "active": bool}
            self.scroll_area = None
            self.layout_ = None
            self._init_ui()

        def _init_ui(self):
            from PyQt5.QtWidgets import QScrollArea
            self.scroll_area = QScrollArea()
            self.scroll_area.setWidgetResizable(True)
            container = QWidget()
            self.layout_ = QVBoxLayout(container)
            self.layout_.setSpacing(2)
            self.layout_.setContentsMargins(0, 0, 0, 0)
            self.scroll_area.setWidget(container)
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(QLabel("Stack:"))
            lay.addWidget(self.scroll_area, 1)

        def set_planes(self, planes_list, active_idx=0):
            """planes_list: list of {"idx": int, "pixmap": QPixmap, "active": bool}."""
            # Clear old buttons
            while self.layout_.count() > 0:
                item = self.layout_.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            self.planes = planes_list
            for info in planes_list:
                btn = QPushButton()
                btn.setIconSize(info["pixmap"].size())
                btn.setIcon(info["pixmap"])
                btn.setMaximumSize(120, 90)
                btn.setFlat(False)
                idx = info["idx"]
                btn.clicked.connect(lambda checked=False, i=idx: self.active_plane_changed.emit(i))
                # active plane: normal, inactive: dimmed
                if info.get("active"):
                    btn.setStyleSheet("border: 2px solid yellow")
                else:
                    btn.setStyleSheet("opacity: 0.5")
                self.layout_.addWidget(btn)
            self.layout_.addStretch()

    class MeasureView(QGraphicsView):
        """QGraphicsView supplies the pan/zoom/hit-testing the checklist calls
        for; this class only decides what a click sequence MEANS for the
        active tool, and hands the result to annotations.py's own
        build_*_mark functions -- no geometry math lives here that isn't
        already in annotations.py."""

        def __init__(self, window):
            self.scene_ = QGraphicsScene()
            super().__init__(self.scene_)
            self.window_ = window
            self.setRenderHint(QPainter.Antialiasing)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self._pixmap_item = None
            self._onionskin_items = []  # faint neighbor planes
            self._pending_points = []   # native green-plane (x, y) floats
            self._pending_items = []    # scene items for the in-progress mark
            self.onionskin_enabled = False

        def set_image(self, pixmap, onionskin_pixmaps=None):
            """Set the active image. onionskin_pixmaps: list of QPixmap for
            neighbor planes (one for each neighbor, in order, for onion-skin
            overlay). If onionskin_enabled is true, render them faintly
            behind the active image."""
            self.scene_.clear()
            self._onionskin_items = []
            # Render onion-skin neighbors behind (drawn first, so they appear behind)
            if self.onionskin_enabled and onionskin_pixmaps:
                for pix in onionskin_pixmaps:
                    item = self.scene_.addPixmap(pix)
                    item.setOpacity(0.3)
                    self._onionskin_items.append(item)
            # Render active image on top
            self._pixmap_item = self.scene_.addPixmap(pixmap)
            self.scene_.setSceneRect(self._pixmap_item.boundingRect())
            self._pending_points = []
            self._pending_items = []
            self.resetTransform()
            self.fitInView(self._pixmap_item, Qt.KeepAspectRatio)

        def set_onionskin_enabled(self, enabled):
            """Toggle onion-skin display. Requires re-rendering the image."""
            self.onionskin_enabled = enabled

        def wheelEvent(self, ev):
            factor = 1.15 if ev.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)

        def mousePressEvent(self, ev):
            if self._pixmap_item is None or self.window_.active_tool is None:
                super().mousePressEvent(ev)
                return
            if ev.button() == Qt.RightButton:
                self._cancel_pending()
                return
            pt = self.mapToScene(ev.pos())
            self._pending_points.append((pt.x(), pt.y()))
            self._draw_pending_point(pt)
            self.window_.on_point_added(self._pending_points)
            needed = {"distance": 2, "angle": 3}.get(self.window_.active_tool)
            if needed is not None and len(self._pending_points) >= needed:
                self.window_.commit_mark(list(self._pending_points))
                self._clear_pending()

        def mouseDoubleClickEvent(self, ev):
            min_points = {"polygon": 3, "ellipse": 5}.get(self.window_.active_tool)
            if min_points is not None and len(self._pending_points) >= min_points:
                self.window_.commit_mark(list(self._pending_points))
                self._clear_pending()
            else:
                super().mouseDoubleClickEvent(ev)

        def _draw_pending_point(self, pt):
            r = POINT_RADIUS
            item = self.scene_.addEllipse(pt.x() - r, pt.y() - r, 2 * r, 2 * r, PENDING_PEN)
            self._pending_items.append(item)
            if len(self._pending_points) >= 2:
                a = self._pending_points[-2]
                self._pending_items.append(
                    self.scene_.addLine(a[0], a[1], pt.x(), pt.y(), PENDING_PEN))

        def _clear_pending(self):
            for it in self._pending_items:
                self.scene_.removeItem(it)
            self._pending_items = []
            self._pending_points = []

        def _cancel_pending(self):
            self._clear_pending()
            self.window_.on_point_added([])

        # --- committed marks, drawn from whatever build_*_mark produced -----
        def draw_distance(self, mark):
            p = mark["input"]["points"]
            self.scene_.addLine(p[0][0], p[0][1], p[1][0], p[1][1], MARK_PEN)

        def draw_angle(self, mark):
            v = mark["input"]["vertex"]
            a = mark["input"]["arm_a"]
            b = mark["input"]["arm_b"]
            self.scene_.addLine(v[0], v[1], a[0], a[1], MARK_PEN)
            self.scene_.addLine(v[0], v[1], b[0], b[1], MARK_PEN)

        def draw_polygon(self, mark):
            pts = mark["input"]["points"]
            self.scene_.addPolygon(QPolygonF([QPointF(x, y) for x, y in pts]), MARK_PEN)

        def draw_ellipse(self, mark):
            cx, cy = mark["derived"]["center"]
            major_px, minor_px = mark["derived"]["axes_px"]
            item = self.scene_.addEllipse(-major_px, -minor_px, 2 * major_px, 2 * minor_px, MARK_PEN)
            item.setPos(cx, cy)
            item.setRotation(mark["derived"]["angle_deg"])


    class MeasureWindow(QMainWindow):
        """The analysis GUI: pick an objective, open an image, pick a tool,
        click. Every tool button stays off until the selected objective has a
        calibration on record -- the checklist's own gating rule, checked the
        same way qt_shell.py's ruler checks it, via calibrate.py's own
        current_calibration."""

        # Emitted by "Restart wizard...", right before this window closes, so
        # main()'s event loop knows to run MeasureWizard again rather than
        # exit -- the "manually restart to set new data points" path onto an
        # otherwise-unchanged window.
        restart_requested = pyqtSignal()

        def __init__(self, image_path=None, objective=None):
            super().__init__()
            self.setWindowTitle("Zynergy measurement")
            self.active_tool = None
            self._plane = None
            self._pixel_sha256 = None
            # Z-stack support (checklist section 8)
            self._stack = []  # list of {"path": Path, "idx": int, "plane": ndarray, "pixmap": QPixmap}
            self._active_plane_idx = 0
            self._session_dir = None

            self.view = MeasureView(self)
            self.filmstrip = FilmstripWidget()
            self.filmstrip.active_plane_changed.connect(self._on_filmstrip_plane_selected)

            self.objective_combo = QComboBox()
            self.objective_combo.setEditable(True)
            for obj in (getattr(_calibrate, "DEFAULT_OBJECTIVES", None)
                       or ["4x", "10x", "40x", "100x"]):
                self.objective_combo.addItem(obj)
            if objective:
                idx = self.objective_combo.findText(objective)
                if idx >= 0:
                    self.objective_combo.setCurrentIndex(idx)
                else:
                    self.objective_combo.setCurrentText(objective)
            self.objective_combo.currentTextChanged.connect(self._refresh_gating)

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

            self.calib_status = QLabel("")
            self.calib_status.setWordWrap(True)
            self.point_status = QLabel("")
            self.mark_count_label = QLabel("")
            self.result_label = QLabel("")
            self.result_label.setWordWrap(True)

            open_btn = QPushButton("Open image...")
            open_btn.clicked.connect(self._on_open)

            restart_btn = QPushButton("Restart wizard...")
            restart_btn.setEnabled(_wizard_pages is not None)
            if _wizard_pages is None:
                restart_btn.setToolTip("wizard_pages.py not alongside this file")
            restart_btn.clicked.connect(self._on_restart_wizard)

            export_btn = QPushButton("Export results...")
            export_btn.setEnabled(_export is not None)
            if _export is None:
                export_btn.setToolTip("export.py not alongside this file")
            export_btn.clicked.connect(self._on_export_results)

            self.onionskin_btn = QPushButton("Onion-skin")
            self.onionskin_btn.setCheckable(True)
            self.onionskin_btn.setChecked(False)
            self.onionskin_btn.clicked.connect(self._on_onionskin_toggled)

            top = QHBoxLayout()
            top.addWidget(open_btn)
            top.addWidget(restart_btn)
            top.addWidget(export_btn)
            top.addWidget(QLabel("Objective:"))
            top.addWidget(self.objective_combo)
            top.addStretch(1)
            top.addWidget(self.onionskin_btn)
            top.addWidget(self.distance_btn)
            top.addWidget(self.angle_btn)
            top.addWidget(self.polygon_btn)
            top.addWidget(self.ellipse_btn)

            bottom = QVBoxLayout()
            bottom.addWidget(self.calib_status)
            bottom.addWidget(self.point_status)
            bottom.addWidget(self.result_label)
            bottom.addWidget(self.mark_count_label)

            central = QWidget()
            lay = QHBoxLayout(central)
            canvas_layout = QVBoxLayout()
            canvas_layout.addLayout(top)
            canvas_layout.addWidget(self.view, 1)
            canvas_layout.addLayout(bottom)
            lay.addLayout(canvas_layout, 1)
            lay.addWidget(self.filmstrip, 0)  # filmstrip on the right, narrow
            self.setCentralWidget(central)

            self._refresh_gating()
            if image_path:
                self._load_image(image_path)

        # --- tools -----------------------------------------------------------
        def _on_tool_toggled(self, name, checked):
            self.active_tool = name if checked else None
            self.view._clear_pending()
            self.point_status.setText(self._tool_hint(name) if checked else "")
            self.result_label.setText("")

        @staticmethod
        def _tool_hint(name):
            return {
                "distance": "distance: click two points",
                "angle": "angle: click the vertex, then two arm points",
                "polygon": "polygon: click each vertex, double-click to finish (3+ points)",
                "ellipse": "ellipse: click 5+ boundary points, double-click to finish",
            }.get(name, "")

        def on_point_added(self, points):
            n = len(points)
            tool = self.active_tool
            if tool == "distance":
                self.point_status.setText("distance: {} of 2 points".format(n))
            elif tool == "angle":
                self.point_status.setText("angle: {} of 3 points (vertex first)".format(n))
            elif tool == "polygon":
                self.point_status.setText(
                    "polygon: {} point(s), double-click to finish (3+ needed)".format(n))
            elif tool == "ellipse":
                self.point_status.setText(
                    "ellipse: {} point(s), double-click to finish (5+ needed)".format(n))
            else:
                self.point_status.setText("")

        def _refresh_gating(self):
            obj = self.objective_combo.currentText().strip()
            um_per_px = current_um_per_px(obj)
            ok = um_per_px is not None
            for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                btn.setEnabled(ok)
            if ok:
                self.calib_status.setText(
                    "Calibration: {} at {:.4f} \u00b5m/px".format(obj, um_per_px))
            else:
                self.calib_status.setText(
                    "No calibration on record for {} -- measurement tools "
                    "disabled".format(obj or "(no objective set)"))
                if not ok and self.active_tool is not None:
                    for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                        btn.setChecked(False)

        # --- image loading -----------------------------------------------------
        def _on_open(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Open image to measure", "",
                "Raw / mosaic (*.dng *.tif *.tiff);;JPEG preview (*.jpg *.jpeg);;"
                "All files (*)")
            if path:
                self._load_image(path)

        def _on_restart_wizard(self):
            # Just signals + closes; main()'s loop is what actually reruns
            # MeasureWizard and opens the next window, mirroring
            # calibrate.py's CalibrationWindow._on_restart_wizard exactly.
            self.restart_requested.emit()
            self.close()

        def _on_export_results(self):
            """Export all measurements to a JSON results file (checklist §11)."""
            if _export is None or _annotations is None:
                QMessageBox.warning(self, "Export not available",
                                   "export.py or annotations.py not importable")
                return
            path, _ = QFileDialog.getSaveFileName(
                self, "Export measurement results", "measurements.json",
                "JSON (*.json);;All files (*)")
            if not path:
                return
            try:
                store = _annotations.load_annotations()
                _export.export_measurements(store=store, out_path=path)
                QMessageBox.information(
                    self, "Exported",
                    "Exported {} measurements to {}".format(
                        sum(len(r.get("marks", [])) for r in store.values()),
                        Path(path).name))
            except Exception as exc:
                QMessageBox.warning(self, "Export failed", str(exc))

        def _load_image(self, path):
            try:
                plane = load_measurement_plane(path)
            except (ValueError, RuntimeError) as exc:
                QMessageBox.warning(self, "Could not load image", str(exc))
                return
            except Exception as exc:
                QMessageBox.warning(self, "Could not load image",
                                   "Failed to read {}: {}".format(Path(path).name, exc))
                return
            self._plane = plane
            self._pixel_sha256 = (_pixel_hash.pixel_sha256(plane)
                                  if _pixel_hash is not None else None)
            pixmap = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(plane))
            self.view.set_image(pixmap)
            self.result_label.setText("")
            self._render_existing_marks()

        def _render_existing_marks(self):
            if _annotations is None or self._pixel_sha256 is None:
                self.mark_count_label.setText("")
                return
            record = _annotations.image_record_for(self._pixel_sha256)
            marks = record["marks"] if record else []
            for m in marks:
                self._draw_mark(m)
            self.mark_count_label.setText(
                "{} mark(s) on record for this image".format(len(marks)))

        def _draw_mark(self, mark):
            drawer = {"distance": self.view.draw_distance,
                     "angle": self.view.draw_angle,
                     "polygon": self.view.draw_polygon,
                     "ellipse": self.view.draw_ellipse}.get(mark.get("type"))
            if drawer:
                drawer(mark)

        # --- z-stack support (checklist section 8) --------------------------
        def _on_filmstrip_plane_selected(self, plane_idx):
            """User clicked a plane in the filmstrip; switch to it."""
            if 0 <= plane_idx < len(self._stack):
                self._active_plane_idx = plane_idx
                self._render_stack_plane()

        def _on_onionskin_toggled(self, checked):
            """Toggle onion-skin display and re-render."""
            self.view.set_onionskin_enabled(checked)
            self._render_stack_plane()

        def _render_stack_plane(self):
            """Render the active plane with optional onion-skin neighbors."""
            if not self._stack:
                return
            active = self._stack[self._active_plane_idx]
            self._plane = active["plane"]
            self._pixel_sha256 = (_pixel_hash.pixel_sha256(self._plane)
                                  if _pixel_hash is not None else None)
            pixmap = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(self._plane))

            onionskin_pixmaps = []
            if self.view.onionskin_enabled:
                # Render neighbors (previous + next planes) faintly behind
                for idx in [self._active_plane_idx - 1, self._active_plane_idx + 1]:
                    if 0 <= idx < len(self._stack):
                        neighbor = self._stack[idx]
                        neighbor_pixmap = _calibrate.array_to_qimage(
                            _calibrate.stretch_to_uint8(neighbor["plane"]))
                        onionskin_pixmaps.append(neighbor_pixmap)

            self.view.set_image(pixmap, onionskin_pixmaps=onionskin_pixmaps)
            self.result_label.setText("")
            self._render_existing_marks()

        def _load_stack(self, session_dir):
            """Load a z-stack from a session directory (via stacks.py).
            Populates self._stack and updates the filmstrip."""
            if _stacks is None:
                QMessageBox.warning(self, "Z-stack not available",
                                   "stacks.py is not importable; z-stack view disabled.")
                return
            session_dir = Path(session_dir)
            session_json = _stacks.load_session(session_dir)
            if not session_json:
                return
            # Collect all planes from the session
            planes_by_stack_plane = {}  # (stack_id, plane_idx) -> capture_entry
            for cap in session_json.get("captures", []):
                if _stacks.is_active(cap):
                    stack_id = cap.get(_stacks.STACK)
                    plane_idx = _stacks.plane_of(cap)
                    if stack_id and plane_idx is not None:
                        key = (stack_id, plane_idx)
                        planes_by_stack_plane[key] = cap
            if not planes_by_stack_plane:
                return
            # Load the first stack's planes (the most recent/most common case)
            first_stack = min(planes_by_stack_plane.keys())[0]
            stack_captures = sorted(
                [(p, c) for (s, p), c in planes_by_stack_plane.items() if s == first_stack])
            self._stack = []
            self._session_dir = session_dir
            for plane_idx, cap in stack_captures:
                # Locate the raw file for this capture
                base = cap.get("base")
                if not base:
                    continue
                raw_path = session_dir / base / f"{base}.dng"
                if not raw_path.exists():
                    continue
                try:
                    plane = load_measurement_plane(raw_path)
                except Exception:
                    continue
                pixmap = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(plane))
                self._stack.append({
                    "path": raw_path,
                    "idx": plane_idx,
                    "plane": plane,
                    "pixmap": pixmap,
                })
            # Update filmstrip
            if self._stack:
                filmstrip_info = [{"idx": i, "pixmap": p["pixmap"], "active": (i == 0)}
                                  for i, p in enumerate(self._stack)]
                self.filmstrip.set_planes(filmstrip_info, active_idx=0)
                self._active_plane_idx = 0
                self._render_stack_plane()

        # --- committing a mark --------------------------------------------------
        def commit_mark(self, points):
            if self._plane is None or _annotations is None:
                return
            obj = self.objective_combo.currentText().strip()
            um_per_px = current_um_per_px(obj)
            if um_per_px is None:
                QMessageBox.warning(self, "No calibration",
                                   "No calibration on record for {}.".format(obj))
                return
            tool = self.active_tool
            try:
                if tool == "distance":
                    mark = _annotations.build_distance_mark(points[0], points[1], um_per_px)
                elif tool == "angle":
                    mark = _annotations.build_angle_mark(points[0], points[1], points[2])
                elif tool == "polygon":
                    mark = _annotations.build_polygon_mark(points, um_per_px)
                elif tool == "ellipse":
                    center, axes_px, angle_deg = fit_ellipse(points)
                    mark = _annotations.build_ellipse_mark(
                        points, center, axes_px, angle_deg, um_per_px)
                else:
                    return
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot measure", str(exc))
                return
            defaults = build_record_defaults(self._plane, obj)
            _annotations.save_mark(self._pixel_sha256, mark, record_defaults=defaults)
            self._draw_mark(mark)
            self.result_label.setText(format_mark_result(mark))
            record = _annotations.image_record_for(self._pixel_sha256)
            self.mark_count_label.setText(
                "{} mark(s) on record for this image".format(len(record["marks"])))


    class _SetupPage(QWizardPage):
        """Wizard page 1: pick a calibrated objective. Next disabled until the
        chosen objective has a calibration on record -- reuses
        current_um_per_px, the exact gate MeasureWindow's own
        _refresh_gating already checks, never a second copy of that rule."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setTitle("Objective")
            self.setSubTitle("Pick a calibrated objective to measure with.")

            self.objective_combo = QComboBox()
            self.objective_combo.setEditable(True)
            for obj in (getattr(_calibrate, "DEFAULT_OBJECTIVES", None)
                       or ["4x", "10x", "40x", "100x"]):
                self.objective_combo.addItem(obj)
            self.objective_combo.currentTextChanged.connect(self._on_changed)

            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)

            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Objective:"))
            lay.addWidget(self.objective_combo)
            lay.addWidget(self.status_label)
            self._refresh()

        def _on_changed(self, _text):
            self._refresh()
            self.completeChanged.emit()

        def _refresh(self):
            obj = self.objective_combo.currentText().strip()
            um_per_px = current_um_per_px(obj)
            if um_per_px is not None:
                self.status_label.setText(
                    "Calibration: {} at {:.4f} µm/px".format(obj, um_per_px))
            else:
                self.status_label.setText(
                    "No calibration on record for {} -- calibrate it first "
                    "(calibrate.py) before it can be used here.".format(
                        obj or "(no objective set)"))

        def isComplete(self):
            return current_um_per_px(self.objective_combo.currentText().strip()) is not None

        def objective(self):
            return self.objective_combo.currentText().strip()


    class MeasureWizard(QWizard):
        """The paged wizard (build checklist section 4): page 1 picks a
        calibrated objective, page 2 gets an image -- an existing file or a
        fresh live capture, via wizard_pages.ImageSourcePage. Finishing hands
        (objective, image_path) to main(), which opens the unchanged
        MeasureWindow with them; this only replaces how that window gets its
        two startup arguments, never its own canvas/tool logic."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Zynergy measurement - setup")
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
                plane = load_measurement_plane(path)
            except (ValueError, RuntimeError) as exc:
                return False, str(exc)
            except Exception as exc:
                return False, "Failed to read {}: {}".format(Path(path).name, exc)
            return True, "Loaded {} ({} x {} plane)".format(
                Path(path).name, plane.shape[1], plane.shape[0])

        def objective(self):
            return self.setup_page.objective()

        def image_path(self):
            return self.image_page.resolved_path


def render_check():
    import tifffile

    # --- provenance guard --------------------------------------------------
    tmp = Path("/tmp/zynergy_measure_render_check_display.tif")
    tifffile.imwrite(str(tmp), np.zeros((4, 4), dtype=np.uint16),
                     description=json.dumps({"kind": "display-referred derivative (NOT a measurement)"}))
    try:
        try:
            check_measurement_provenance(tmp)
            raise AssertionError("expected ValueError for a flagged display-referred derivative")
        except ValueError:
            pass
    finally:
        tmp.unlink(missing_ok=True)

    tmp2 = Path("/tmp/zynergy_measure_render_check_clean.tif")
    tifffile.imwrite(str(tmp2), np.zeros((4, 4), dtype=np.uint16),
                     description=json.dumps({"kind": "green", "transform": "single_green_extraction"}))
    check_measurement_provenance(tmp2)   # must NOT raise
    tmp2.unlink(missing_ok=True)
    tmp3 = Path("/tmp/zynergy_measure_render_check_none.tif")
    tifffile.imwrite(str(tmp3), np.zeros((4, 4), dtype=np.uint16))   # no description at all
    check_measurement_provenance(tmp3)   # must NOT raise: no tag is not a flag
    tmp3.unlink(missing_ok=True)
    print("check_measurement_provenance check PASS: flagged derivative refused, "
          "an unflagged green/no-description file passes through")

    # --- load_measurement_plane: both supported input shapes -----------------
    assert _debayer is not None and _calibrate is not None, \
        "debayer.py and calibrate.py must both be importable from this directory"
    full_h, full_w = FULL_RES[1], FULL_RES[0]
    mosaic = (np.arange(full_h * full_w, dtype=np.uint32) % 4096).astype(np.uint16).reshape(full_h, full_w)
    mosaic_path = Path("/tmp/zynergy_measure_render_check_mosaic.tif")
    tifffile.imwrite(str(mosaic_path), mosaic)
    try:
        plane_from_mosaic = load_measurement_plane(mosaic_path)
        expected_plane, _rc = _debayer.extract_green(
            mosaic, _calibrate.DEFAULT_CFA_PATTERN, _calibrate.DEFAULT_GREEN_WHICH)
        assert plane_from_mosaic.shape == (GREEN_PLANE_RES[1], GREEN_PLANE_RES[0])
        assert np.array_equal(plane_from_mosaic, expected_plane), \
            "green extraction from a full mosaic must match debayer.py's own extract_green exactly"
    finally:
        mosaic_path.unlink(missing_ok=True)

    green_h, green_w = GREEN_PLANE_RES[1], GREEN_PLANE_RES[0]
    already_green = (np.arange(green_h * green_w, dtype=np.uint32) % 4096).astype(np.uint16).reshape(green_h, green_w)
    green_path = Path("/tmp/zynergy_measure_render_check_green.tif")
    tifffile.imwrite(str(green_path), already_green)
    try:
        plane_from_green = load_measurement_plane(green_path)
        assert np.array_equal(plane_from_green, already_green), \
            "an already-extracted green plane must be used AS-IS, not re-extracted"
    finally:
        green_path.unlink(missing_ok=True)

    bad_path = Path("/tmp/zynergy_measure_render_check_bad.tif")
    tifffile.imwrite(str(bad_path), np.zeros((10, 10), dtype=np.uint16))   # neither shape
    try:
        try:
            load_measurement_plane(bad_path)
            raise AssertionError("expected ValueError for a shape matching neither input type")
        except ValueError:
            pass
    finally:
        bad_path.unlink(missing_ok=True)
    print("load_measurement_plane check PASS: full-mosaic extraction matches "
          "debayer.py exactly, an already-green plane passes through unchanged, "
          "an unrecognized shape refuses")

    # --- hash consistency: same pixels, same identity, regardless of path ----
    if _pixel_hash is not None:
        h_direct = _pixel_hash.pixel_sha256(expected_plane)
        h_via_loader = _pixel_hash.pixel_sha256(plane_from_mosaic)
        assert h_direct == h_via_loader, \
            "loading via measure.py must hash identically to debayer.py's own extract_green"
        print("pixel hash consistency check PASS: measure.py's loader and "
              "debayer.py's own extraction hash identically")

    # --- calibration gating --------------------------------------------------
    if _calibrate is not None:
        orig_path = _calibrate.CALIBRATION_PATH
        tmp_dir = Path("/tmp/zynergy_measure_render_check_calib")
        if tmp_dir.exists():
            import shutil
            shutil.rmtree(tmp_dir)
        _calibrate.CALIBRATION_PATH = tmp_dir / "calibration.json"
        try:
            assert current_um_per_px("40x") is None, "an uncalibrated objective should gate closed"
            entry = _calibrate.build_calibration_entry(
                Path("/tmp/fake.dng"), (0.0, 0.0), (500.0, 0.0), 500.0,
                objective="40x", target_type="stage micrometer", focus_score=300.0)
            _calibrate.save_calibration("40x", entry)
            assert abs(current_um_per_px("40x") - 1.0) < 1e-9, "a calibrated objective should gate open"

            defaults = build_record_defaults(already_green, "40x")
            assert defaults["shape"] == list(already_green.shape)
            assert defaults["kind"] == "green"
            assert defaults["calibration_ref"]["objective"] == "40x"
            print("calibration gating check PASS: closed with no calibration, "
                  "open once calibrated, record_defaults carry the right ref")
        finally:
            _calibrate.CALIBRATION_PATH = orig_path
    else:
        print("calibration gating check SKIPPED: calibrate.py not importable")

    # --- fit_ellipse: recover a known ellipse from sampled boundary points ---
    true_center = (50.0, 30.0)
    true_major, true_minor = 40.0, 20.0
    true_angle_deg = 25.0
    true_angle_rad = math.radians(true_angle_deg)
    thetas = np.linspace(0, 2 * math.pi, 12, endpoint=False)
    ex = true_major * np.cos(thetas)
    ey = true_minor * np.sin(thetas)
    rx = ex * math.cos(true_angle_rad) - ey * math.sin(true_angle_rad) + true_center[0]
    ry = ex * math.sin(true_angle_rad) + ey * math.cos(true_angle_rad) + true_center[1]
    sample_points = list(zip(rx.tolist(), ry.tolist()))
    fit_center, fit_axes_px, fit_angle_deg = fit_ellipse(sample_points)
    assert abs(fit_center[0] - true_center[0]) < 1e-6 and abs(fit_center[1] - true_center[1]) < 1e-6, \
        "fit_ellipse should recover the true center from noiseless boundary points"
    assert abs(fit_axes_px[0] - true_major) < 1e-6 and abs(fit_axes_px[1] - true_minor) < 1e-6, \
        "fit_ellipse should recover the true semi-major/semi-minor axes"
    angle_err = min(abs(fit_angle_deg - true_angle_deg) % 180,
                    180 - abs(fit_angle_deg - true_angle_deg) % 180)
    assert angle_err < 1e-4, \
        "fit_ellipse's recovered angle {} should match the true {} (mod 180)".format(
            fit_angle_deg, true_angle_deg)
    try:
        fit_ellipse([(0, 0), (1, 0), (2, 0), (3, 0)])
        raise AssertionError("expected ValueError for under 5 points")
    except ValueError:
        pass
    try:
        fit_ellipse([(0, 0), (1, 0), (2, 0), (3, 0), (4, 0)])
        raise AssertionError("expected ValueError for collinear (degenerate) points")
    except ValueError:
        pass
    print("fit_ellipse check PASS: recovers a known ellipse's center/axes/angle "
          "exactly from noiseless boundary points, both degenerate-input guards hold")

    # --- result readout text -------------------------------------------------
    assert _annotations is not None, "annotations.py must be importable"
    dist_mark = _annotations.build_distance_mark((0.0, 0.0), (100.0, 0.0), um_per_px=0.5)
    txt = format_mark_result(dist_mark)
    assert "50.000" in txt and "\u00b5m" in txt, "distance readout should show the computed microns"
    angle_mark = _annotations.build_angle_mark((0.0, 0.0), (5.0, 0.0), (0.0, 5.0))
    assert "90.00" in format_mark_result(angle_mark)
    poly_mark = _annotations.build_polygon_mark([(0, 0), (10, 0), (10, 10), (0, 10)], um_per_px=2.0)
    poly_txt = format_mark_result(poly_mark)
    assert "400.00" in poly_txt and "80.00" in poly_txt, \
        "polygon readout should show both area (um^2, quadratic scaling) and perimeter (um, linear)"
    ellipse_mark = _annotations.build_ellipse_mark(
        sample_points, fit_center, fit_axes_px, fit_angle_deg, um_per_px=0.5)
    ellipse_txt = format_mark_result(ellipse_mark)
    assert "40.00" in ellipse_txt and "20.00" in ellipse_txt and "2.000" in ellipse_txt, \
        "ellipse readout should show length/width in microns and the Q ratio"
    print("format_mark_result check PASS: distance/angle/polygon/ellipse readouts "
          "all show their actual computed numbers")


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Zynergy measurement GUI: canvas "
                                             "and measurement tools.")
    ap.add_argument("image", nargs="?", default=None,
                    help="image to measure: a raw .dng, its sibling .jpg "
                         "(auto-resolves), a frame_average.py mosaic master, "
                         "or an already-extracted green-plane TIFF")
    ap.add_argument("--objective", default=None)
    ap.add_argument("--render-check", action="store_true")
    args = ap.parse_args()

    if args.render_check:
        render_check()
        return

    if not _HAVE_QT:
        print("PyQt5 is not available; only --render-check can run here.", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)

    if args.image or args.objective:
        # CLI shortcut, unchanged: skip the wizard, open the window directly.
        win = MeasureWindow(image_path=args.image, objective=args.objective)
        win.resize(1200, 800)
        win.show()
        sys.exit(app.exec_())

    # No args: the wizard is the new default interactive entry point. Looping
    # on app.exec_() is what makes "Restart wizard..." work -- see
    # calibrate.py's main() for the identical pattern.
    while True:
        wizard = MeasureWizard()
        if wizard.exec_() != QWizard.Accepted:
            return
        win = MeasureWindow(image_path=wizard.image_path(), objective=wizard.objective())
        win.resize(1200, 800)
        restarted = []
        win.restart_requested.connect(lambda: restarted.append(True))
        win.show()
        app.exec_()
        if not restarted:
            return


if __name__ == "__main__":
    main()

