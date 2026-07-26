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
import os
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

# provenance.py (Qt-free, same as every other import above) rather than
# qt_shell.py itself: this module only needs OUT_ROOT/PROVENANCE_ROOT for
# the capture<->provenance directory mapping below (Part 03), not the Qt
# capture GUI. measure.py "never depends on qt_shell.Session" (see
# _on_exclude_toggled's own comment) stays true -- this is a path-mapping
# dependency on provenance.py's constants, not a Session dependency.
try:
    from . import provenance as _provenance
except ImportError:
    try:
        import provenance as _provenance
    except ImportError:
        _provenance = None

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


def _raw_discard_reason(path):
    """If `path` sits in a capture directory whose OWNING entry in
    session.json recorded raw_discarded=True (Keep RAW Images off, Part 03
    -- see hdr_from_session.py's process()), return that capture's own
    raw_discard_reason string. Lets a caller report the TRUE cause of a
    missing raw sibling instead of calibrate.resolve_raw_path's generic
    "this suggests the file moved on its own" wording, which is actively
    wrong for a deliberate discard -- the whole point of recording a
    reason in session.json is for a reader (human or agent) to be told the
    real one, not left to guess corruption. None if no owning capture is
    found, or none of them recorded a discard matching this filename."""
    if _provenance is None:
        return None
    prov_dir = _provenance_dir_for(Path(path).parent)
    if prov_dir is None:
        return None
    sj_path = prov_dir / "session.json"
    if not sj_path.is_file():
        return None
    try:
        data = json.loads(sj_path.read_text())
    except Exception:
        return None
    stem = Path(path).stem
    for cap in data.get("captures", []):
        if not cap.get("raw_discarded"):
            continue
        prefix = cap.get("file_prefix") or ""
        names = cap.get("files") or []
        if (prefix and stem.startswith(prefix)) or any(Path(n).stem == stem for n in names):
            return cap.get("raw_discard_reason") or (
                "Keep RAW Images was off; the raw frame was deleted once "
                "processing succeeded.")
    return None


def load_measurement_plane(path):
    """The measurement substrate, whichever of the two supported input shapes
    it is: a full-sensor raw mosaic (.dng, or a frame_average.py master.tif)
    gets green-which=1 extracted (same call calibrate.py itself makes); an
    already half-res green plane (debayer.py's own --green output) is used
    as-is, no double extraction. Runs the provenance guard first. Raises
    ValueError for anything that is neither shape, or RuntimeError if
    debayer.py is not importable and extraction is actually needed.

    A missing raw sibling (e.g. path is a .jpg preview whose .dng was
    deleted) is refused by calibrate.resolve_raw_path itself -- this never
    silently falls back to measuring the JPG. What changes here (Part 03):
    if that missing raw was a DELIBERATE discard (Keep RAW Images off),
    the refusal names that reason explicitly instead of resolve_raw_path's
    generic "this suggests the file moved on its own", which would
    misdescribe a deliberate choice as an anomaly."""
    def _discard_error(bad_path, fallback):
        reason = _raw_discard_reason(path)
        if reason:
            return ValueError(
                "{} has no raw file to measure -- it was deliberately "
                "discarded: {} The display JPG/TIFF is a tonemapped "
                "derivative, never a substitute (measuring it would measure "
                "apparent edges the tonemap already moved).".format(
                    Path(bad_path).name, reason))
        return fallback

    if _calibrate is None:
        raise RuntimeError("calibrate.py could not be imported; needed for "
                           "resolve_raw_path/load_mosaic_array")
    check_measurement_provenance(path)
    try:
        resolved = _calibrate.resolve_raw_path(path)
    except ValueError as exc:
        raise _discard_error(path, exc) from exc
    if not Path(resolved).is_file():
        raise _discard_error(resolved, ValueError("{} does not exist.".format(resolved)))
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


class CalibrationMissing(ValueError):
    """Raised by commit_measurement when the objective has no calibration on
    record. A ValueError subclass so a bare `except ValueError` still catches
    it, but a caller that wants a distinct dialog title can catch it first."""


def commit_measurement(plane, pixel_sha256, objective, tool, points):
    """The commit-mark orchestration, Qt-free: build the mark for `tool` from
    `points`, save it to the annotations store, and hand back both. Shared by
    MeasureWindow.commit_mark and ReviewWindow's own commit wrapper so there
    is exactly one place this sequence exists (qt_shell.py's Part-05 Live
    Measure Panel still has its own independent copy -- not migrated to this
    function yet, a deliberate later step, not an oversight).

    The calibration gate is unconditional -- even an angle mark, which never
    actually uses um_per_px, is blocked without a calibration on record. This
    matches MeasureWindow's pre-extraction behavior exactly, on purpose: a
    refactor that also changes behavior makes any bug reported afterward
    ambiguous about which part caused it. Part 05's panel gates angle marks
    more loosely (exempt from the calibration check, since build_angle_mark
    takes no um_per_px) -- that is probably the more correct end state, but
    adopting it here would be a silent behavior change riding on an
    extraction. Whoever migrates Part 05 to call this function instead of
    its own inline copy is DECIDING to drop that exemption, not discovering
    that it was already gone.

    Raises CalibrationMissing if `objective` has no calibration on record,
    ValueError for degenerate/invalid points (propagated from the
    build_*_mark functions and fit_ellipse, not caught here -- callers catch
    it, same as calibrate.py's build_calibration_entry). Returns
    {"mark": mark, "record": record} where `record` is the exact,
    already-updated store entry for `pixel_sha256` -- annotations.save_mark
    already computes and returns this, so no separate image_record_for
    re-read is needed."""
    if _annotations is None:
        raise RuntimeError("annotations.py not importable")
    um_per_px = current_um_per_px(objective)
    if um_per_px is None:
        raise CalibrationMissing(
            "No calibration on record for {}.".format(objective))
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
        raise ValueError("unrecognized tool {!r}".format(tool))
    defaults = build_record_defaults(plane, objective)
    store = _annotations.save_mark(pixel_sha256, mark, record_defaults=defaults)
    return {"mark": mark, "record": store[pixel_sha256]}


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


# ---------------------------------------------------------------------------
# Z-stack assembly (build checklist section 8): pure, Qt-free, built ON
# stacks.py's own group_by_stack/ordered_planes -- one session contributes
# one plane, so a stack is assembled ACROSS session folders, never read out
# of a single session's captures list.
# ---------------------------------------------------------------------------

# Same root qt_shell.py's Session writes into (its OUT_ROOT); a plain
# constant here rather than an import, since pulling in qt_shell.py from this
# module would drag the whole capture GUI along just for one path.
DEFAULT_CAPTURES_ROOT = Path.home() / "captures"


def _provenance_dir_for(capture_dir):
    """The provenance directory mirroring capture_dir's own position under
    the global provenance.OUT_ROOT -- session.json lives there now, never
    beside the raw frames (Part 03, provenance relocation; mirrors
    qt_shell.py's own _provenance_dir_for exactly, duplicated rather than
    imported since pulling in qt_shell.py here would drag the whole Qt
    capture GUI along just for one path-mapping helper). None if
    capture_dir is not actually under OUT_ROOT (e.g. a folder browsed from
    outside the managed capture tree) or provenance.py is unavailable --
    callers degrade to "no session.json found" rather than raising, same
    temperament as the rest of this file's stack-scanning code."""
    if _provenance is None:
        return None
    try:
        rel = Path(capture_dir).relative_to(_provenance.OUT_ROOT)
    except ValueError:
        return None
    return _provenance.PROVENANCE_ROOT / rel


def resolve_capture_raw(session_dir, cap):
    """The on-disk raw file for one tagged capture entry: the first recorded
    filename that still exists (a snap's `files` list), else the first frame
    of the capture's own `file_prefix` glob (a burst -- frame 0 stands for
    the burst, since per-plane measurement wants one plane image, and every
    frame of a burst shares the same subject and exposure). None if nothing
    resolves, so a half-deleted session degrades to a missing plane rather
    than an exception."""
    sd = Path(session_dir)
    for name in cap.get("files") or []:
        p = sd / name
        if p.is_file():
            return p
    prefix = cap.get("file_prefix") or ""
    for ext in (".dng", ".tif", ".tiff"):
        hits = sorted(sd.glob("{}frame_*{}".format(prefix, ext)))
        if hits:
            return hits[0]
    return None


def collect_stack_planes(captures_root):
    """Every tagged z-stack under a captures root, planes ordered and resolved
    to real files: {stack_id: [{"plane": int, "path": Path, "session_dir":
    Path, "excluded": bool, "sharpness_score": float|None}, ...]}. Ordering
    comes from stacks.ordered_planes (integer plane, never folder order).

    Membership uses stacks.group_by_stack(..., include_excluded=True) --
    UNLIKE that function's own default -- so an excluded plane still shows
    up here, marked, rather than vanishing outright: section 13's own rule
    is that exclude "keeps a frame's stack intent on record", and a human
    reviewing the filmstrip needs to actually SEE a cut plane (and its
    sharpness_score, section 13's post-capture QC number) to judge whether
    the cut was right, and to toggle it back if not.

    A plane whose raw file no longer resolves is dropped here -- same
    missing-plane temperament as zstack_process's own flag, not an error.

    captures_root's direct children are CAPTURE directories (raw frames);
    session.json for each lives in the mirrored provenance directory now
    (Part 03, provenance relocation) -- stacks.py itself is untouched by
    that split (it just reads session.json from whatever directory it's
    given), so group_by_stack/ordered_planes are called against the
    PROVENANCE side here, with a capture-dir lookup kept alongside to
    resolve each plane's actual raw file. The "session_dir" key in the
    returned dict is therefore the provenance dir (its one real consumer,
    _on_exclude_toggled, reads/writes session.json through it) -- not the
    capture dir, which callers never need directly since "path" already
    points at the resolved raw file.
    """
    if _stacks is None:
        raise RuntimeError("stacks.py could not be imported; needed for the z-stack view")
    root = Path(captures_root)
    if not root.is_dir():
        return {}
    cap_by_prov = {}
    for p in sorted(root.iterdir()):
        prov = _provenance_dir_for(p)
        if prov is not None and (prov / "session.json").is_file():
            cap_by_prov[prov] = p
    out = {}
    groups = _stacks.group_by_stack(list(cap_by_prov), include_excluded=True)
    for stack_id, members in groups.items():
        planes = []
        for prov_dir, cap in _stacks.ordered_planes(members):
            raw = resolve_capture_raw(cap_by_prov[prov_dir], cap)
            if raw is not None:
                planes.append({"plane": _stacks.plane_of(cap), "path": raw,
                               "session_dir": prov_dir, "excluded": not _stacks.is_active(cap),
                               "sharpness_score": cap.get("sharpness_score")})
        if planes:
            out[stack_id] = planes
    return out


try:
    from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel,
                                 QVBoxLayout, QHBoxLayout, QPushButton, QComboBox,
                                 QGraphicsView, QGraphicsScene, QFileDialog,
                                 QDialog, QMessageBox, QButtonGroup, QWizard,
                                 QWizardPage, QInputDialog)
    from PyQt5.QtGui import QPen, QColor, QPolygonF, QPainter, QPixmap, QIcon
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

    FILMSTRIP_THUMB = (110, 82)   # thumbnail bounds; source planes are full-res

    class FilmstripWidget(QWidget):
        """Filmstrip for z-stacks: thumbnails down the side, inactive dimmed,
        active lit. Clicking a thumbnail switches the active plane. Per
        checklist §8/§13, this is also the home for per-plane sharpness
        score and the exclude toggle: each thumbnail shows its recorded
        score (section 13's post-capture QC number, off qt_shell.py's own
        capture path) and an Include/Exclude button, since a QC score is
        evidence a human acts on here, never an automatic gate."""

        active_plane_changed = pyqtSignal(int)   # user clicked a thumbnail
        exclude_toggled = pyqtSignal(int)        # user clicked a plane's Exclude/Include

        def __init__(self, parent=None):
            super().__init__(parent)
            self.planes = []  # list of {"idx": int, "pixmap": QPixmap, "label": str,
                              #          "active": bool, "excluded": bool, "flagged": bool|None}
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

        @staticmethod
        def _thumb(pixmap, dimmed):
            """A thumbnail-sized copy, darkened for inactive planes. The dim is
            painted into the pixels (semi-opaque black over the whole thumb)
            because Qt stylesheets have no `opacity` property on plain
            widgets -- a stylesheet attempt is silently ignored."""
            t = pixmap.scaled(FILMSTRIP_THUMB[0], FILMSTRIP_THUMB[1],
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if not dimmed:
                return t
            t = QPixmap(t)   # detach before painting on it
            p = QPainter(t)
            p.fillRect(t.rect(), QColor(0, 0, 0, 140))
            p.end()
            return t

        @staticmethod
        def _border_color(active, excluded, flagged):
            # Priority: excluded (structural, cut from the built stack) beats
            # flagged (evidence only, still in the stack) beats active/inactive.
            if excluded:
                return "#cc4444"
            if flagged:
                return "#e0a030"
            return "#ffd24e" if active else "#444444"

        def set_planes(self, planes_list):
            """planes_list: list of {"idx": int, "pixmap": QPixmap, "label": str,
            "active": bool, "excluded": bool, "score": float|None,
            "flagged": bool|None}. Rebuilds the strip; callers re-invoke this
            on every active-plane switch or exclude toggle so the lit/dimmed/
            excluded state always tracks the canvas and the store."""
            while self.layout_.count() > 0:
                item = self.layout_.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            self.planes = planes_list
            for info in planes_list:
                active = bool(info.get("active"))
                excluded = bool(info.get("excluded"))
                flagged = info.get("flagged")
                score = info.get("score")
                idx = info["idx"]

                thumb = self._thumb(info["pixmap"], dimmed=not active)
                btn = QPushButton()
                btn.setIcon(QIcon(thumb))
                btn.setIconSize(thumb.size())
                score_text = ("score {:.1f}".format(score) if score is not None
                             else "no score recorded")
                flag_text = "  (soft relative to this stack's best)" if flagged else ""
                btn.setToolTip("{}\n{}{}".format(info.get("label", ""), score_text, flag_text))
                btn.clicked.connect(
                    lambda checked=False, i=idx: self.active_plane_changed.emit(i))
                btn.setStyleSheet("border: 2px solid {}".format(
                    self._border_color(active, excluded, flagged)))
                self.layout_.addWidget(btn)

                label_text = info.get("label", "")
                if excluded:
                    label_text += "  [excluded]"
                elif flagged:
                    label_text += "  [soft?]"
                lbl = QLabel(label_text)
                lbl.setStyleSheet("color: {}".format(
                    self._border_color(active, excluded, flagged)))
                self.layout_.addWidget(lbl)

                exclude_btn = QPushButton("Include" if excluded else "Exclude")
                exclude_btn.clicked.connect(
                    lambda checked=False, i=idx: self.exclude_toggled.emit(i))
                self.layout_.addWidget(exclude_btn)
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
                self.window_._reset_tool_hint()

        def mouseDoubleClickEvent(self, ev):
            min_points = {"polygon": 3, "ellipse": 5}.get(self.window_.active_tool)
            if min_points is not None and len(self._pending_points) >= min_points:
                self.window_.commit_mark(list(self._pending_points))
                self._clear_pending()
                self.window_._reset_tool_hint()
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
            # Z-stack support (checklist section 8, plus section 13's
            # post-capture QC: excluded/sharpness_score per plane)
            self._stack = []  # list of {"path": Path, "plane": int (z-position),
                              #          "array": ndarray, "pixmap": QPixmap,
                              #          "excluded": bool, "sharpness_score": float|None}
            self._active_plane_idx = 0
            self._current_stack_id = None

            self.view = MeasureView(self)
            self.filmstrip = FilmstripWidget()
            self.filmstrip.active_plane_changed.connect(self._on_filmstrip_plane_selected)
            self.filmstrip.exclude_toggled.connect(self._on_exclude_toggled)

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

            open_stack_btn = QPushButton("Open stack...")
            open_stack_btn.setEnabled(_stacks is not None)
            if _stacks is None:
                open_stack_btn.setToolTip("stacks.py not alongside this file")
            open_stack_btn.clicked.connect(self._on_open_stack)

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

            publish_btn = QPushButton("Publish package...")
            publish_btn.setEnabled(_publish is not None)
            if _publish is None:
                publish_btn.setToolTip("publish.py not alongside this file")
            publish_btn.clicked.connect(self._on_publish_package)

            self.onionskin_btn = QPushButton("Onion-skin")
            self.onionskin_btn.setCheckable(True)
            self.onionskin_btn.setChecked(False)
            self.onionskin_btn.clicked.connect(self._on_onionskin_toggled)

            top = QHBoxLayout()
            top.addWidget(open_btn)
            top.addWidget(open_stack_btn)
            top.addWidget(restart_btn)
            top.addWidget(export_btn)
            top.addWidget(publish_btn)
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
            self._reset_tool_hint()
            self.result_label.setText("")

        def _reset_tool_hint(self):
            """The point-status line's ready-for-the-next-mark state: the
            active tool's own hint, same text _on_tool_toggled shows when the
            tool is first picked. Called after a commit too (see the canvas'
            mousePressEvent/mouseDoubleClickEvent) -- without this, the
            status line kept showing the LAST pre-commit count/hint (e.g. a
            polygon commit still read "double-click to finish") until the
            next point happened to overwrite it."""
            self.point_status.setText(self._tool_hint(self.active_tool))

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
            entry = (_calibrate.current_calibration(obj)
                    if _calibrate is not None and obj else None)
            um_per_px = entry["um_per_px"] if entry else None
            ok = um_per_px is not None
            for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                btn.setEnabled(ok)
            if ok:
                # Section 13: config drift (reduction lens / CFA / green-which)
                # is evidence, never a gate -- the tool stays enabled even on
                # a stale calibration, same as poly2_flag never blocking a CA
                # save. A human decides whether to re-measure.
                staleness = _calibrate.format_staleness_suffix(
                    _calibrate.calibration_staleness(entry))
                self.calib_status.setText(
                    "Calibration: {} at {:.4f} \u00b5m/px{}".format(obj, um_per_px, staleness))
            else:
                self.calib_status.setText(
                    "No calibration on record for {} -- measurement tools "
                    "disabled".format(obj or "(no objective set)"))
                if not ok and self.active_tool is not None:
                    for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                        btn.setChecked(False)

        # --- image loading -----------------------------------------------------
        def _on_open(self):
            try:
                from . import gallery as _gallery
            except ImportError:
                import gallery as _gallery
            dlg = _gallery.GalleryPickDialog(parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            paths = dlg.selected_paths()
            if paths:
                self._load_image(str(paths[0]))

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

        def _on_publish_package(self):
            """Publish a complete package with reproducible provenance
            (checklist §12): green_plane.tif (the measurement image, written
            deflate so its decode re-hashes to the same pixel_sha256 --
            pixel_hash.py's own round-trip guarantee), results.json (this
            image's marks), and manifest.json (the provenance chain)."""
            if _publish is None or _pixel_hash is None:
                QMessageBox.warning(self, "Publish not available",
                                   "publish.py or pixel_hash.py not importable")
                return
            if self._plane is None:
                QMessageBox.warning(self, "No image loaded",
                                   "Load an image first before publishing.")
                return
            out_dir = QFileDialog.getExistingDirectory(
                self, "Create publication package in directory")
            if not out_dir:
                return
            try:
                # Publish's calibration_ref names the calibration a mark's
                # microns were actually computed under, not whatever is
                # currently active for the objective in objective_combo --
                # if the objective is recalibrated after marks were made,
                # "currently active" would misreport a manifest as if the
                # marks used a calibration they never did (see
                # HANDOFF.md's MeasureWindow-extraction step-3 section).
                pixel_sha256 = _pixel_hash.pixel_sha256(self._plane)
                calib_ref = (_annotations.stored_calibration_ref(pixel_sha256)
                            if _annotations is not None else None)
                import tifffile
                green_path = Path(out_dir) / "green_plane.tif"
                tifffile.imwrite(str(green_path), self._plane, compression="deflate")
                manifest = _publish.publish_measurements(
                    green_path, calibration_ref=calib_ref, out_dir=out_dir)
                QMessageBox.information(
                    self, "Published",
                    "Wrote publication package to {}:\n"
                    "  green_plane.tif  (pixel_sha256 {}...)\n"
                    "  results.json  ({} measurement(s) for this image)\n"
                    "  manifest.json  (provenance chain{})".format(
                        out_dir,
                        manifest["green_plane"]["pixel_sha256"][:16],
                        manifest["results"]["total_measurements"],
                        "" if calib_ref else "; NO calibration on record -- "
                        "results are pixel-only"))
            except Exception as exc:
                QMessageBox.warning(self, "Publish failed", str(exc))

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
            # A single image replaces any loaded stack outright -- otherwise
            # the filmstrip would keep showing (and switching back to) planes
            # of a stack that is no longer what's on the canvas.
            self._stack = []
            self._active_plane_idx = 0
            self.filmstrip.set_planes([])
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

        def _refresh_filmstrip(self):
            # best_score: the stack's own best recorded sharpness_score, so
            # sharpness_relative_flag has something to compare each plane
            # against -- computed fresh each call so a rescored plane (or a
            # freshly loaded stack) always compares against the CURRENT best,
            # never a stale one.
            scores = [p.get("sharpness_score") for p in self._stack
                     if p.get("sharpness_score") is not None]
            best_score = max(scores) if scores else None
            self.filmstrip.set_planes([
                {"idx": i, "pixmap": p["pixmap"],
                 "label": "plane {}".format(p["plane"]),
                 "active": (i == self._active_plane_idx),
                 "excluded": bool(p.get("excluded")),
                 "score": p.get("sharpness_score"),
                 "flagged": (_stacks.sharpness_relative_flag(
                     p.get("sharpness_score"), best_score) if _stacks else None)}
                for i, p in enumerate(self._stack)])

        def _render_stack_plane(self):
            """Render the active plane with optional onion-skin neighbors.
            Marks bind to THIS plane's own pixel_sha256 -- the ghosted
            neighbours are display, the active plane is the measurement
            (checklist §8's binding rule)."""
            if not self._stack:
                return
            active = self._stack[self._active_plane_idx]
            self._plane = active["array"]
            self._pixel_sha256 = (_pixel_hash.pixel_sha256(self._plane)
                                  if _pixel_hash is not None else None)
            onionskin_pixmaps = []
            if self.view.onionskin_enabled:
                # Neighbours (previous + next planes) faintly behind
                for idx in (self._active_plane_idx - 1, self._active_plane_idx + 1):
                    if 0 <= idx < len(self._stack):
                        onionskin_pixmaps.append(self._stack[idx]["pixmap"])
            self.view.set_image(active["pixmap"], onionskin_pixmaps=onionskin_pixmaps)
            self.result_label.setText("")
            self._render_existing_marks()
            self._refresh_filmstrip()

        def _on_open_stack(self):
            """Open a tagged z-stack: scan a captures root for sessions whose
            captures carry stack tags (collect_stack_planes, built on
            stacks.py's own cross-session grouping -- one session contributes
            one plane), pick a stack if several exist, load every plane."""
            root = QFileDialog.getExistingDirectory(
                self, "Captures root (the folder holding session folders)",
                str(DEFAULT_CAPTURES_ROOT))
            if not root:
                return
            try:
                found = collect_stack_planes(root)
            except RuntimeError as exc:
                QMessageBox.warning(self, "Z-stack not available", str(exc))
                return
            if not found:
                QMessageBox.information(
                    self, "No stacks", "No tagged z-stack captures found under "
                    "{} (planes are tagged at capture time; see stacks.py)".format(root))
                return
            if len(found) == 1:
                stack_id = next(iter(found))
            else:
                stack_id, ok = QInputDialog.getItem(
                    self, "Choose stack", "Stack:", sorted(found), 0, False)
                if not ok:
                    return
            self._load_stack(stack_id, found[stack_id])

        def _load_stack(self, stack_id, planes):
            """Load resolved stack planes (collect_stack_planes output) into
            memory and the filmstrip. A plane whose file fails to load is
            reported and skipped, not fatal -- same missing-plane temperament
            as the rest of the stack tooling.

            The initial active plane defaults to the first NON-excluded one
            (falling back to plane 0 only if every plane is excluded), so
            opening a stack lands on a plane that's actually part of the
            built stack rather than a cut one."""
            loaded, failed = [], []
            for info in planes:
                try:
                    arr = load_measurement_plane(info["path"])
                except Exception:
                    failed.append(info["path"].name)
                    continue
                pixmap = _calibrate.array_to_qimage(_calibrate.stretch_to_uint8(arr))
                # "plane" is the integer z-position from the tag; "array" is
                # the pixel data -- kept as two distinct keys on purpose.
                loaded.append({"path": info["path"], "plane": info["plane"],
                               "session_dir": info["session_dir"], "array": arr,
                               "pixmap": pixmap, "excluded": info.get("excluded", False),
                               "sharpness_score": info.get("sharpness_score")})
            self._stack = loaded
            self._current_stack_id = stack_id
            if not self._stack:
                QMessageBox.warning(self, "Stack empty",
                                   "No plane of stack {!r} could be loaded ({})".format(
                                       stack_id, ", ".join(failed) or "no files"))
                return
            if failed:
                QMessageBox.information(
                    self, "Planes skipped",
                    "{} plane(s) could not be loaded and were skipped: {}".format(
                        len(failed), ", ".join(failed)))
            non_excluded = [i for i, p in enumerate(self._stack) if not p["excluded"]]
            self._active_plane_idx = non_excluded[0] if non_excluded else 0
            self._render_stack_plane()

        def _on_exclude_toggled(self, plane_idx):
            """Toggle the exclude flag on one plane (section 13): a
            deliberate, reversible human action, never automatic. Writes
            straight to that plane's OWN session.json (measure.py never
            depends on qt_shell.Session; this is the same read-modify-write
            shape Session.write() itself uses, just scoped to one capture).
            entry["session_dir"] is the PROVENANCE directory (Part 03) --
            see collect_stack_planes's own docstring for why that key means
            provenance dir, not capture dir, in this returned shape."""
            if not (0 <= plane_idx < len(self._stack)) or _stacks is None:
                return
            entry = self._stack[plane_idx]
            session_json_path = entry["session_dir"] / "session.json"
            try:
                data = json.loads(session_json_path.read_text())
                cap = _stacks.find_tagged(data["captures"], self._current_stack_id,
                                          entry["plane"])
                if cap is None:
                    raise ValueError("capture for plane {} not found in {}".format(
                        entry["plane"], session_json_path))
                new_excluded = not entry["excluded"]
                _stacks.set_exclude(cap, new_excluded)
                tmp = session_json_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2))
                os.replace(tmp, session_json_path)
            except Exception as exc:
                QMessageBox.warning(self, "Could not update exclude", str(exc))
                return
            entry["excluded"] = new_excluded
            self._refresh_filmstrip()

        # --- committing a mark --------------------------------------------------
        def commit_mark(self, points):
            """Thin Qt wrapper around commit_measurement(): pull plain values
            out of this window's own widgets, call the shared Qt-free
            orchestration, then do GUI-only follow-up (draw, labels). See
            commit_measurement's own docstring for why the calibration gate
            is unconditional (including for angle marks)."""
            if self._plane is None or _annotations is None:
                return
            obj = self.objective_combo.currentText().strip()
            tool = self.active_tool
            if tool not in ("distance", "angle", "polygon", "ellipse"):
                return
            try:
                result = commit_measurement(self._plane, self._pixel_sha256, obj, tool, points)
            except CalibrationMissing as exc:
                QMessageBox.warning(self, "No calibration", str(exc))
                return
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot measure", str(exc))
                return
            self._draw_mark(result["mark"])
            self.result_label.setText(format_mark_result(result["mark"]))
            self.mark_count_label.setText(
                "{} mark(s) on record for this image".format(len(result["record"]["marks"])))


    class ReviewWindow(QMainWindow):
        """Recall/review (Preferences-dialog plan set, MeasureWindow
        extraction, step 2): open a previously captured image, see its
        existing marks, place new ones. Same four tools, same MeasureView
        canvas, same commit_measurement() orchestration MeasureWindow itself
        now calls -- deliberately smaller than MeasureWindow: no z-stack/
        filmstrip/export/publish/wizard-restart, which either belong to
        other steps of the extraction or are being removed outright (see
        PLAN_measurewindow_extraction.md). MeasureView needs zero changes to
        support this window -- it already only expects `.active_tool`,
        `.commit_mark(points)`, `.on_point_added(points)`, and
        `._reset_tool_hint()` on whatever `window_` it's given, and this
        class provides exactly that same shape MeasureWindow does."""

        def __init__(self, image_path=None, objective=None):
            super().__init__()
            self.setWindowTitle("Zynergy review")
            self.active_tool = None
            self._plane = None
            self._pixel_sha256 = None

            self.view = MeasureView(self)

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

            top = QHBoxLayout()
            top.addWidget(open_btn)
            top.addWidget(QLabel("Objective:"))
            top.addWidget(self.objective_combo)
            top.addStretch(1)
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
            lay = QVBoxLayout(central)
            lay.addLayout(top)
            lay.addWidget(self.view, 1)
            lay.addLayout(bottom)
            self.setCentralWidget(central)

            self._refresh_gating()
            if image_path:
                self._load_image(image_path)

        # --- tools (identical to MeasureWindow's own) -------------------------
        def _on_tool_toggled(self, name, checked):
            self.active_tool = name if checked else None
            self.view._clear_pending()
            self._reset_tool_hint()
            self.result_label.setText("")

        def _reset_tool_hint(self):
            self.point_status.setText(self._tool_hint(self.active_tool))

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
            entry = (_calibrate.current_calibration(obj)
                    if _calibrate is not None and obj else None)
            um_per_px = entry["um_per_px"] if entry else None
            ok = um_per_px is not None
            for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                btn.setEnabled(ok)
            if ok:
                staleness = _calibrate.format_staleness_suffix(
                    _calibrate.calibration_staleness(entry))
                self.calib_status.setText(
                    "Calibration: {} at {:.4f} µm/px{}".format(obj, um_per_px, staleness))
            else:
                self.calib_status.setText(
                    "No calibration on record for {} -- measurement tools "
                    "disabled".format(obj or "(no objective set)"))
                if not ok and self.active_tool is not None:
                    for btn in (self.distance_btn, self.angle_btn, self.polygon_btn, self.ellipse_btn):
                        btn.setChecked(False)

        # --- image loading (identical to MeasureWindow's own, minus the
        # z-stack reset -- ReviewWindow has no filmstrip) ---------------------
        def _on_open(self):
            try:
                from . import gallery as _gallery
            except ImportError:
                import gallery as _gallery
            dlg = _gallery.GalleryPickDialog(parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            paths = dlg.selected_paths()
            if paths:
                self._load_image(str(paths[0]))

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

        # --- committing a mark (thin wrapper, mirrors MeasureWindow's own) --
        def commit_mark(self, points):
            if self._plane is None or _annotations is None:
                return
            obj = self.objective_combo.currentText().strip()
            tool = self.active_tool
            if tool not in ("distance", "angle", "polygon", "ellipse"):
                return
            try:
                result = commit_measurement(self._plane, self._pixel_sha256, obj, tool, points)
            except CalibrationMissing as exc:
                QMessageBox.warning(self, "No calibration", str(exc))
                return
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot measure", str(exc))
                return
            self._draw_mark(result["mark"])
            self.result_label.setText(format_mark_result(result["mark"]))
            self.mark_count_label.setText(
                "{} mark(s) on record for this image".format(len(result["record"]["marks"])))


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

    # --- load_measurement_plane: deliberate raw discard (Part 03, Keep RAW
    # Images off) must name the TRUE reason, not calibrate.resolve_raw_
    # path's generic "this suggests the file moved on its own" wording --
    # and must never silently measure the JPG instead.
    if _provenance is None:
        print("raw-discard legible-failure check SKIPPED: provenance.py not importable here")
    else:
        import shutil as _rc_shutil
        import tempfile as _rc_tempfile
        rd_base = Path(_rc_tempfile.mkdtemp())
        rd_cap_root = rd_base / "captures"
        rd_prov_root = rd_base / "provenance"
        rd_cap_root.mkdir()
        rd_prov_root.mkdir()
        _orig_rd_out_root = _provenance.OUT_ROOT
        _orig_rd_prov_root = _provenance.PROVENANCE_ROOT
        _provenance.OUT_ROOT = rd_cap_root
        _provenance.PROVENANCE_ROOT = rd_prov_root
        try:
            rd_cap_dir = rd_cap_root / "2026-02-01_000001"
            rd_prov_dir = rd_prov_root / "2026-02-01_000001"
            rd_cap_dir.mkdir()
            rd_prov_dir.mkdir()
            reason = ("Keep RAW Images preference was off; raw frames and "
                      "the linear master were deleted once processing succeeded.")
            (rd_prov_dir / "session.json").write_text(json.dumps({"captures": [
                {"index": 0, "kind": "science", "file_prefix": "science_",
                 "raw_discarded": True, "raw_discard_reason": reason},
            ]}))
            # Only the JPG preview survives on disk -- the .dng sibling
            # really is gone, matching what Keep RAW Images off leaves behind.
            jpg_path = rd_cap_dir / "science_frame_0000.jpg"
            jpg_path.write_bytes(b"\xff\xd8\xff\xd9")
            try:
                load_measurement_plane(jpg_path)
                raise AssertionError(
                    "a raw-discarded capture must refuse to load, never "
                    "silently measure the JPG")
            except ValueError as exc:
                msg = str(exc)
                assert reason in msg, \
                    "the refusal must name the TRUE recorded reason: {!r}".format(msg)
                assert "moved on its own" not in msg, \
                    "a deliberate discard must not be misdescribed as an " \
                    "anomaly: {!r}".format(msg)

            # A genuinely unexplained missing sibling (no owning session.json
            # at all -- a capture dir this scan never recorded) keeps
            # calibrate.resolve_raw_path's own generic wording: this check
            # must not swallow every missing-raw case into the discard
            # message, only the ones session.json actually explains.
            rd_cap_dir2 = rd_cap_root / "2026-02-01_000002"
            rd_cap_dir2.mkdir()   # no matching provenance dir at all
            unexplained_jpg = rd_cap_dir2 / "science_frame_0000.jpg"
            unexplained_jpg.write_bytes(b"\xff\xd8\xff\xd9")
            try:
                load_measurement_plane(unexplained_jpg)
                raise AssertionError("expected ValueError for a genuinely missing sibling")
            except ValueError as exc:
                assert "moved on its own" in str(exc), \
                    "an unexplained missing sibling should keep the generic " \
                    "refusal, not be mistaken for a recorded discard: {!r}".format(exc)
        finally:
            _provenance.OUT_ROOT = _orig_rd_out_root
            _provenance.PROVENANCE_ROOT = _orig_rd_prov_root
            _rc_shutil.rmtree(rd_base, ignore_errors=True)
        print("raw-discard legible-failure check PASS: a deliberately "
              "discarded raw refuses with the TRUE recorded reason (never "
              "the generic 'file moved on its own' wording, never a silent "
              "fallback to measuring the JPG), while a genuinely unexplained "
              "missing sibling keeps the generic refusal")

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

            # --- commit_measurement(): the extracted, Qt-free orchestration
            # MeasureWindow.commit_mark and ReviewWindow.commit_mark both now
            # wrap (MeasureWindow extraction, step 2). Own isolated
            # annotations store -- never the real ~/.zynergy/annotations.json.
            orig_annot_path = _annotations.ANNOTATION_PATH
            _annotations.ANNOTATION_PATH = tmp_dir / "annotations.json"
            try:
                cm_plane = already_green
                cm_sha = "cm_render_check_" + "0" * 48

                cm_distance = commit_measurement(
                    cm_plane, cm_sha, "40x", "distance", [(0.0, 0.0), (10.0, 0.0)])
                assert cm_distance["mark"]["type"] == "distance"
                assert len(cm_distance["record"]["marks"]) == 1

                cm_angle = commit_measurement(
                    cm_plane, cm_sha, "40x", "angle",
                    [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)])
                assert cm_angle["mark"]["type"] == "angle"
                assert len(cm_angle["record"]["marks"]) == 2, \
                    "commit_measurement's returned record must be the real, " \
                    "already-updated store entry (no separate re-fetch needed)"

                cm_polygon = commit_measurement(
                    cm_plane, cm_sha, "40x", "polygon",
                    [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)])
                assert cm_polygon["mark"]["type"] == "polygon"

                cm_ellipse_pts = [
                    (10.0 * math.cos(t), 10.0 * math.sin(t))
                    for t in (0.0, 1.2, 2.4, 3.6, 4.8, 6.0)]
                cm_ellipse = commit_measurement(
                    cm_plane, cm_sha, "40x", "ellipse", cm_ellipse_pts)
                assert cm_ellipse["mark"]["type"] == "ellipse"

                on_disk = _annotations.load_annotations()
                assert len(on_disk[cm_sha]["marks"]) == 4, \
                    "all four commits must have landed in the real (temp-" \
                    "redirected) store, keyed by the same pixel_sha256"

                # The strict gate (this step's explicit decision, see
                # commit_measurement's own docstring): ALL FOUR tools, angle
                # included, refuse on an uncalibrated objective. Part 05's
                # panel exempts angle from this gate in its own separate
                # inline copy -- commit_measurement deliberately does not,
                # to keep this extraction behavior-neutral for MeasureWindow.
                for tool, pts in (
                        ("distance", [(0.0, 0.0), (10.0, 0.0)]),
                        ("angle", [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]),
                        ("polygon", [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]),
                        ("ellipse", cm_ellipse_pts)):
                    try:
                        commit_measurement(cm_plane, cm_sha, "no-such-objective", tool, pts)
                        raise AssertionError(
                            "{} must refuse without calibration, angle "
                            "included -- it is not exempt here".format(tool))
                    except CalibrationMissing:
                        pass

                # A degenerate shape's ValueError propagates uncaught, same
                # as build_calibration_entry -- the Qt wrapper catches it,
                # commit_measurement itself does not.
                try:
                    commit_measurement(cm_plane, cm_sha, "40x", "distance",
                                       [(5.0, 5.0), (5.0, 5.0)])
                    raise AssertionError("expected ValueError for coincident distance points")
                except CalibrationMissing:
                    raise AssertionError("coincident points is a ValueError, not CalibrationMissing")
                except ValueError:
                    pass

                print("commit_measurement check PASS: all four tools commit "
                      "and land in the real store keyed by pixel_sha256, the "
                      "returned record is the store's own post-save entry "
                      "(no redundant re-fetch), the calibration gate is "
                      "strict for all four tools including angle, and a "
                      "degenerate shape's ValueError propagates uncaught")
            finally:
                _annotations.ANNOTATION_PATH = orig_annot_path

            # section 13: _refresh_gating's status label surfaces staleness
            # (config drift), but never re-closes the gate over it -- evidence,
            # not a block, same as every other flag this project raises.
            if not _HAVE_QT:
                print("_refresh_gating staleness check SKIPPED: PyQt5 not available")
            else:
                qtapp = QApplication.instance() or QApplication([])
                win = MeasureWindow(objective="40x")
                win._refresh_gating()
                assert "STALE" not in win.calib_status.text(), \
                    "a fresh calibration should not show a staleness warning"
                assert win.distance_btn.isEnabled(), \
                    "tools should stay enabled on a fresh (non-stale) calibration"

                drifted_entry = dict(entry, reduction_lens=entry["reduction_lens"] + 1.0)
                _calibrate.save_calibration("40x", drifted_entry)
                win._refresh_gating()
                assert "STALE" in win.calib_status.text() and \
                    "reduction lens" in win.calib_status.text(), \
                    "a drifted reduction lens should surface in the status text: " \
                    "{!r}".format(win.calib_status.text())
                assert win.distance_btn.isEnabled(), \
                    "a stale calibration must still gate tools OPEN -- evidence, " \
                    "never a block"
                print("_refresh_gating staleness check PASS: a fresh calibration "
                      "is quiet, a drifted one shows the staleness reason in the "
                      "status text without disabling any measurement tool")

                # BUILD_LIST Tier 1 item 2: after a mark commits, the status
                # line used to keep showing the pre-commit count/hint (a
                # polygon commit still read "double-click to finish") until
                # the next point happened to overwrite it. Drives the REAL
                # mousePressEvent/mouseDoubleClickEvent handlers with
                # synthetic QMouseEvents against a real loaded image, not a
                # reimplementation of the fix.
                from PyQt5.QtCore import QEvent
                from PyQt5.QtGui import QMouseEvent

                def _click(view, x, y, dbl=False):
                    kind = QEvent.MouseButtonDblClick if dbl else QEvent.MouseButtonPress
                    ev = QMouseEvent(kind, QPointF(x, y), Qt.LeftButton,
                                     Qt.LeftButton, Qt.NoModifier)
                    if dbl:
                        view.mouseDoubleClickEvent(ev)
                    else:
                        view.mousePressEvent(ev)

                # green_path itself was already unlink()ed by the earlier
                # load_measurement_plane check's own finally: block -- a
                # fresh, self-contained fixture here, not a reuse of a path
                # whose lifecycle belongs to that earlier test.
                #
                # This check drives real commits (distance, then polygon)
                # through win.commit_mark -- own isolated annotations store,
                # same reasoning as the commit_measurement/ReviewWindow
                # blocks elsewhere in this function. Before this fix, this
                # was the one gap: every measure.py --render-check run
                # committed two real marks (a fixed, deterministic
                # pixel_sha256 -- the fixture array never varies) straight
                # into the real ~/.zynergy/annotations.json, unredirected.
                # Found and recorded (not silently patched away) in
                # CHANGELOG.md/HANDOFF.md for the MeasureWindow extraction,
                # step 2 entry; this is that fix landing on its own, since
                # it doesn't depend on how (or whether) the records already
                # written to any real deployment's store get handled --
                # PHILOSOPHY.md's append-only rule forbids editing or
                # deleting store entries outright, "clean up a store" is
                # named directly as something never to do, so those
                # existing entries are a separate decision, not resolved
                # here.
                status_green_path = Path("/tmp/zynergy_measure_render_check_status.tif")
                tifffile.imwrite(str(status_green_path), already_green)
                orig_annot_path_status = _annotations.ANNOTATION_PATH
                _annotations.ANNOTATION_PATH = tmp_dir / "status_line_annotations.json"
                try:
                    win._load_image(str(status_green_path))

                    win.distance_btn.setChecked(True)
                    assert win.point_status.text() == "distance: click two points"
                    _click(win.view, 10, 10)
                    assert win.point_status.text() == "distance: 1 of 2 points"
                    _click(win.view, 20, 20)
                    assert win.point_status.text() == "distance: click two points", \
                        "a distance mark auto-commits on its 2nd point; the " \
                        "status line must reset to the tool hint, not keep " \
                        "showing '1 of 2'"

                    win.polygon_btn.setChecked(True)
                    assert win.point_status.text() == (
                        "polygon: click each vertex, double-click to finish (3+ points)")
                    _click(win.view, 10, 10)
                    _click(win.view, 20, 10)
                    _click(win.view, 20, 20)
                    assert "3 point(s)" in win.point_status.text()
                    _click(win.view, 20, 20, dbl=True)
                    assert win.point_status.text() == (
                        "polygon: click each vertex, double-click to finish (3+ points)"), \
                        "a polygon commits on double-click; the status line " \
                        "must reset to the tool hint, not keep reading the " \
                        "pre-commit 'double-click to finish' text"
                finally:
                    status_green_path.unlink(missing_ok=True)
                    _annotations.ANNOTATION_PATH = orig_annot_path_status
                print("mark-commit status-line reset check PASS: both the "
                      "auto-commit path (distance/angle) and the double-click "
                      "commit path (polygon/ellipse) reset the point-status "
                      "line to the tool's own hint immediately after a commit, "
                      "matching what picking the tool fresh already showed")

                # --- ReviewWindow: recall/review, now editable (step 2) ----
                # Own isolated annotations store, same reasoning as the
                # commit_measurement block above -- never the real store.
                orig_annot_path_rw = _annotations.ANNOTATION_PATH
                _annotations.ANNOTATION_PATH = tmp_dir / "review_annotations.json"
                rw_green_path = Path("/tmp/zynergy_measure_render_check_review.tif")
                tifffile.imwrite(str(rw_green_path), already_green)
                try:
                    rwin = ReviewWindow(objective="40x")
                    assert rwin.distance_btn.isEnabled(), \
                        "ReviewWindow must gate on calibration the same way MeasureWindow does"
                    rwin._load_image(str(rw_green_path))
                    assert rwin.mark_count_label.text() == "0 mark(s) on record for this image", \
                        "a freshly-loaded image with no prior marks must show a zero count"

                    rwin.distance_btn.setChecked(True)
                    _click(rwin.view, 10, 10)
                    _click(rwin.view, 20, 20)
                    assert rwin.mark_count_label.text() == "1 mark(s) on record for this image"
                    committed_sha = rwin._pixel_sha256
                    on_disk_rw = _annotations.load_annotations()
                    assert committed_sha in on_disk_rw and \
                        len(on_disk_rw[committed_sha]["marks"]) == 1, \
                        "ReviewWindow's commit must land in the real (temp-" \
                        "redirected) annotations store, keyed by pixel_sha256"

                    # Recall half: a FRESH ReviewWindow, loading the same
                    # file again, must replay the mark that the first
                    # instance committed -- the exact round trip this
                    # capability exists to prove works at all.
                    rwin2 = ReviewWindow(objective="40x")
                    rwin2._load_image(str(rw_green_path))
                    assert rwin2._pixel_sha256 == committed_sha
                    assert rwin2.mark_count_label.text() == "1 mark(s) on record for this image", \
                        "a fresh ReviewWindow opening the same image must " \
                        "recall the mark committed by a different instance, " \
                        "resolved purely by pixel_sha256"
                finally:
                    rw_green_path.unlink(missing_ok=True)
                    _annotations.ANNOTATION_PATH = orig_annot_path_rw
                print("ReviewWindow check PASS: gates on calibration the same "
                      "way MeasureWindow does, a committed mark lands in the "
                      "real store keyed by pixel_sha256, and a second, "
                      "independent ReviewWindow instance opening the same "
                      "image recalls that mark purely by hash")
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

    # --- z-stack assembly (section 8's pure half) --------------------------
    # Synthetic captures root: two tagged sessions of one stack (planes shot
    # out of order, to prove ordering is by the integer tag), one untagged
    # session, and one excluded plane. resolve_capture_raw is exercised on
    # both of its paths: an explicit `files` list, and the file_prefix glob.
    import shutil as _shutil
    import tempfile as _tempfile
    if _stacks is None:
        print("z-stack assembly check SKIPPED: stacks.py not importable here")
    elif _provenance is None:
        print("z-stack assembly check SKIPPED: provenance.py not importable here")
    else:
        # Split capture/provenance roots (Part 03): raw frames go under
        # zroot (capture side), session.json under zprov (provenance side),
        # mirrored by name -- collect_stack_planes/_provenance_dir_for map
        # between the two via provenance.OUT_ROOT/PROVENANCE_ROOT, so both
        # must be patched to point here for the whole fixture's lifetime.
        _tmp_base = Path(_tempfile.mkdtemp())
        zroot = _tmp_base / "captures"
        zprov = _tmp_base / "provenance"
        zroot.mkdir()
        zprov.mkdir()
        _orig_out_root = _provenance.OUT_ROOT
        _orig_prov_root = _provenance.PROVENANCE_ROOT
        _provenance.OUT_ROOT = zroot
        _provenance.PROVENANCE_ROOT = zprov

        def _fake_session(name, captures, files):
            d = zroot / name
            pd = zprov / name
            d.mkdir(parents=True)
            pd.mkdir(parents=True)
            (pd / "session.json").write_text(json.dumps({"captures": captures}))
            for f in files:
                (d / f).write_bytes(b"")
            return d

        # plane 2 shot FIRST (earlier session name), resolved via glob, with a
        # recorded sharpness_score (section 13's post-capture QC number)
        _fake_session("2026-01-01_0001",
                      [{"kind": "science", "file_prefix": "science_",
                        "stack": "T1", "plane": 2, "sharpness_score": 88.0}],
                      ["science_frame_0000.dng", "science_frame_0001.dng"])
        # plane 1 shot second, resolved via its files list, no score recorded
        # (predates section 13, or scoring failed -- both look the same: None)
        _fake_session("2026-01-01_0002",
                      [{"kind": "science", "file_prefix": "science_",
                        "files": ["science_frame_0000.dng"],
                        "stack": "T1", "plane": 1}],
                      ["science_frame_0000.dng"])
        # untagged session: never part of any stack
        _fake_session("2026-01-01_0003",
                      [{"kind": "science", "file_prefix": "science_"}],
                      ["science_frame_0000.dng"])
        # excluded plane: documented (section 13's own rule), must still
        # SURFACE here (unlike group_by_stack's own default), marked excluded
        _fake_session("2026-01-01_0004",
                      [{"kind": "science", "file_prefix": "science_",
                        "stack": "T1", "plane": 3, "exclude": True,
                        "sharpness_score": 12.0}],
                      ["science_frame_0000.dng"])

        try:
            found = collect_stack_planes(zroot)
            assert list(found) == ["T1"], "exactly one stack should be found, got {}".format(list(found))
            planes = found["T1"]
            assert [p["plane"] for p in planes] == [1, 2, 3], \
                "planes must be ordered by the integer tag, INCLUDING the excluded " \
                "one (section 13: documented, not deleted); got {}".format(
                    [p["plane"] for p in planes])
            assert planes[0]["path"].name == "science_frame_0000.dng"
            assert planes[1]["path"].name == "science_frame_0000.dng", \
                "glob fallback should resolve frame 0 of the burst"
            assert planes[0]["session_dir"].name == "2026-01-01_0002", \
                "plane 1 must come from its own PROVENANCE session (Part 03: " \
                "session_dir now means provenance dir), regardless of shoot order"
            assert planes[0]["session_dir"] == zprov / "2026-01-01_0002", \
                "session_dir must resolve to the provenance mirror, not the capture dir"

            by_plane = {p["plane"]: p for p in planes}
            assert by_plane[1]["excluded"] is False and by_plane[1]["sharpness_score"] is None
            assert by_plane[2]["excluded"] is False and by_plane[2]["sharpness_score"] == 88.0
            assert by_plane[3]["excluded"] is True and by_plane[3]["sharpness_score"] == 12.0, \
                "the excluded plane must carry excluded=True and its own recorded score"

            assert collect_stack_planes(zroot / "no_such_dir") == {}, \
                "a missing root should give no stacks, not raise"

            # a capture whose files vanished resolves to None and its plane is dropped
            assert resolve_capture_raw(zroot / "2026-01-01_0003", {"file_prefix": "nope_"}) is None
            print("z-stack assembly check PASS: cross-session grouping via stacks.py, "
                  "integer-plane ordering INCLUDING the excluded plane (marked, not "
                  "dropped), untagged session ignored, both raw-resolution paths "
                  "(files list + prefix glob), missing root and missing files "
                  "degrade cleanly, sharpness_score passed through per plane, "
                  "session_dir resolves through the provenance mirror (Part 03)")

            # --- MeasureWindow._load_stack / _on_exclude_toggled, against the
            # SAME synthetic stack, exercising the real GUI methods end to end ---
            if not _HAVE_QT:
                print("_load_stack / _on_exclude_toggled check SKIPPED: PyQt5 not available")
            else:
                qtapp = QApplication.instance() or QApplication([])

                def _write_fake_green_plane(path):
                    # resolve_capture_raw pointed at empty stub .dng files above;
                    # overwrite each with a real, loadable TIFF shaped as an
                    # already-extracted green plane -- load_measurement_plane
                    # accepts that shape directly, no debayer extraction needed,
                    # far cheaper than writing a full-sensor mosaic for this test.
                    import tifffile
                    green_hw = (GREEN_PLANE_RES[1], GREEN_PLANE_RES[0])
                    arr = np.random.default_rng(0).integers(
                        0, 4096, size=green_hw).astype(np.uint16)
                    tifffile.imwrite(str(path), arr)

                for p in planes:
                    _write_fake_green_plane(p["path"])

                win = MeasureWindow()
                win._load_stack("T1", planes)
                assert len(win._stack) == 3, "all 3 planes (incl. excluded) should load"
                # initial active plane must be the first NON-excluded one (1),
                # never the excluded plane 3, even though 3 sorts last
                assert win._stack[win._active_plane_idx]["plane"] == 1, \
                    "the initially active plane should be the first non-excluded " \
                    "one, got plane {}".format(win._stack[win._active_plane_idx]["plane"])

                # the filmstrip actually reflects excluded/score/flagged state
                win._refresh_filmstrip()
                assert len(win.filmstrip.planes) == 3
                plane3_info = next(fp for fp in win.filmstrip.planes
                                   if "3" in fp["label"])
                assert plane3_info["excluded"] is True
                assert plane3_info["score"] == 12.0
                # best score in this stack is 88.0 (plane 2); plane 3's 12.0 is
                # well below half of that, so it should ALSO be flagged as soft
                # -- independent evidence, on top of already being excluded
                assert plane3_info["flagged"] is True, \
                    "plane 3's score (12.0) should register as soft relative to " \
                    "the stack's best (88.0)"

                # toggle plane 3 (index 2, since planes are ordered 1,2,3) back
                # to included via the REAL _on_exclude_toggled path
                excluded_idx = next(i for i, e in enumerate(win._stack) if e["plane"] == 3)
                win._on_exclude_toggled(excluded_idx)
                assert win._stack[excluded_idx]["excluded"] is False, \
                    "toggling should flip the in-memory state immediately"
                on_disk = json.loads((zprov / "2026-01-01_0004" / "session.json").read_text())
                assert on_disk["captures"][0].get("exclude") is None, \
                    "toggling back to included must clear the exclude key in " \
                    "session.json (set_exclude's own pop-not-False rule), not " \
                    "just flip it to false in memory"

                # a fresh collect_stack_planes call must now see plane 3 as active
                found2 = collect_stack_planes(zroot)
                by_plane2 = {p["plane"]: p for p in found2["T1"]}
                assert by_plane2[3]["excluded"] is False, \
                    "the exclude toggle must be visible to a fresh scan of the " \
                    "captures root, not just held in this window's own memory"

                # toggle it back to excluded again, confirm the round trip
                win._on_exclude_toggled(excluded_idx)
                assert win._stack[excluded_idx]["excluded"] is True
                on_disk2 = json.loads((zprov / "2026-01-01_0004" / "session.json").read_text())
                assert on_disk2["captures"][0].get("exclude") is True

                print("_load_stack / _on_exclude_toggled check PASS: initial active "
                      "plane skips the excluded one, filmstrip carries excluded/"
                      "score/flagged per plane, toggling exclude writes through to "
                      "session.json (both directions) and is visible to a fresh scan")
        finally:
            _provenance.OUT_ROOT = _orig_out_root
            _provenance.PROVENANCE_ROOT = _orig_prov_root
            _shutil.rmtree(_tmp_base, ignore_errors=True)


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
    ap.add_argument("--review", action="store_true",
                    help="open ReviewWindow (MeasureWindow extraction, step 2) "
                         "instead of MeasureWindow")
    args = ap.parse_args()

    if args.render_check:
        render_check()
        return

    if not _HAVE_QT:
        print("PyQt5 is not available; only --render-check can run here.", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)

    if args.review:
        win = ReviewWindow(image_path=args.image, objective=args.objective)
        win.resize(1200, 800)
        win.show()
        sys.exit(app.exec_())

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

