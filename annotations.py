"""annotations.py - the central annotation store for Zynergy.

Per the build checklist (sections 3 and 9): one JSON store, keyed at the top
level by pixel_sha256 (from pixel_hash.py), each value an image record plus
its marks. Marks are a non-destructive vector layer over a captured green
frame, stored as data, never pixels -- the green TIFF stays byte-for-byte
the sensor's, always.

Scope of this module: the STORE and the mark SCHEMA, not the canvas GUI or
any fitting algorithm. Architecture seam #1 in the checklist lists "ellipse
fit" and "store" as distinct pure-logic pieces on purpose; this file is the
second one. build_ellipse_mark() below records a fit's result (boundary
points in, center/axes/angle/derived microns out) -- it does not compute a
fit. The Fitzgibbon-style algebraic least-squares fit itself is canvas/GUI
work (section 7) and is not built here.

Four mark types, matching the checklist's shapes list (section 7):
  * distance  - two points, a length
  * angle     - a vertex and two arms, an interior angle in degrees
  * polygon   - three or more points, a free outline (perimeter + area)
  * ellipse   - a fitted spore outline (length, width, area, Q ratio);
                schema only, see above

Every mark stores both halves the checklist calls for: the raw input the
user actually dropped (green-plane pixel coordinates, exact, zero
transform), and the derived physical result once a calibration converts
pixels to microns. Distance and area do NOT scale the same way: a length
scales by um_per_px, an area by um_per_px squared. Getting that backwards
silently produces a plausible-looking but wrong number, so it is called out
explicitly at each area computation below, not left implicit.

Every write is atomic (temp file, then rename), the same pattern
calibrate.py's own store uses. Orphans -- a mark whose image hash no longer
resolves to a file on disk, because the master was legitimately reprocessed
-- are tolerated and surfaced honestly (find_orphans), never silently
dropped or errored on; the checklist explicitly calls this "not a bug to
design out."

Run standalone for the headless self-check (no PyQt5, no image files,
no camera):
  python3 annotations.py
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

# pixel_sha256 / hash_tiff: the identity this whole store keys on.
try:
    from . import pixel_hash as _pixel_hash
except ImportError:
    try:
        import pixel_hash as _pixel_hash
    except ImportError:
        _pixel_hash = None

# calibrate.py's own append-only store, reused (not duplicated) so a
# calibration_ref names the EXACT entry in force, not just an objective.
try:
    from . import calibrate as _calibrate
except ImportError:
    try:
        import calibrate as _calibrate
    except ImportError:
        _calibrate = None

ANNOTATION_PATH = Path.home() / ".zynergy" / "annotations.json"

MARK_TYPES = ("distance", "angle", "polygon", "ellipse")
IMAGE_KINDS = ("green", "rgb", "hdr_linear", "averaged")


# ---------------------------------------------------------------------------
# Pure geometry
# ---------------------------------------------------------------------------

def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


# ---------------------------------------------------------------------------
# The store: one JSON file, keyed by pixel_sha256
# ---------------------------------------------------------------------------

def load_annotations():
    """The whole annotation store: {pixel_sha256: image_record}. {} if none
    saved yet or the file is unreadable, never raises -- same defensive
    contract as calibrate.py's load_calibrations()."""
    try:
        return json.loads(ANNOTATION_PATH.read_text())
    except Exception:
        return {}


def new_image_record(pixel_sha256, shape, dtype, kind,
                      calibration_ref=None, source_sha256=None):
    """The empty per-image record shell (section 9's record fields), marks
    list empty. kind must be one of IMAGE_KINDS. source_sha256 is the
    parent master's hash when this plane is one of several siblings (e.g. a
    z-stack plane averaged from a burst), so sibling planes of one capture
    stay findable; None when there is no such parent."""
    if kind not in IMAGE_KINDS:
        raise ValueError("kind must be one of {}, got {!r}".format(IMAGE_KINDS, kind))
    return {
        "pixel_sha256": pixel_sha256,
        "shape": list(shape),
        "dtype": str(dtype),
        "kind": kind,
        "calibration_ref": calibration_ref,
        "source_sha256": source_sha256,
        "marks": [],
    }


def image_record_for(pixel_sha256, store=None):
    """The image record for a given hash, or None if nothing is stored for
    it yet."""
    store = store if store is not None else load_annotations()
    return store.get(pixel_sha256)


def save_mark(pixel_sha256, mark, record_defaults=None):
    """Append one mark to the image record for pixel_sha256, creating the
    record shell first if this is the first mark ever saved for that hash.
    record_defaults (a dict of shape/dtype/kind/calibration_ref/
    source_sha256) is required only that first time; on every later call
    for the same hash it is ignored, since marks accumulate onto one
    shell rather than each redefining it. Same atomic-write pattern as
    calibrate.py's save_calibration: read the whole store, mutate, write to
    a temp file, then rename over the live one, so a crash mid-write can
    never truncate it."""
    ANNOTATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    store = load_annotations()
    record = store.get(pixel_sha256)
    if record is None:
        if record_defaults is None:
            raise ValueError(
                "no annotation record exists yet for {}... and no "
                "record_defaults given to create one (shape/dtype/kind are "
                "required the first time an image is marked)"
                .format(pixel_sha256[:12]))
        record = new_image_record(pixel_sha256, **record_defaults)
        store[pixel_sha256] = record
    record["marks"].append(mark)
    tmp = ANNOTATION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    os.replace(tmp, ANNOTATION_PATH)
    return store


def find_orphans(store, known_hashes):
    """Which image records in the store no longer resolve to a known green
    plane on disk (known_hashes: the pixel_sha256 of every green master
    still present, computed by the caller). NOT a bug: a master gets
    legitimately reprocessed and its hash changes. Surfaced as a plain list
    of orphaned hashes, the same temperament as zstack_process's own
    missing-plane flag: honest, not silently hidden, not treated as an
    error either. What the caller does with the list (warn, keep, offer to
    re-bind) is a GUI decision, not this function's."""
    known = set(known_hashes)
    return [h for h in store if h not in known]


def calibration_ref_for(objective, store=None):
    """The {"objective", "entry_id", "um_per_px"} pointer to record on a new
    mark: EXACTLY which calibration entry was current when the mark was
    made, not just which objective, so a mark's provenance survives that
    objective being recalibrated later. um_per_px is snapshotted at mark
    time too, so a mark's already-computed microns stay tied to the
    conversion actually used, even if a future recalibration changes what
    current_calibration() would return. None if calibrate.py is not
    importable, or that objective has never been calibrated."""
    if _calibrate is None:
        return None
    entry = _calibrate.current_calibration(objective, store)
    if entry is None:
        return None
    return {
        "objective": objective,
        "entry_id": entry.get("entry_id"),
        "um_per_px": entry.get("um_per_px"),
    }


# ---------------------------------------------------------------------------
# Mark builders: distance, angle, polygon (pure geometry, no fit),
# and ellipse (schema for a fit's RESULT; the fit itself is not built here)
# ---------------------------------------------------------------------------

def build_distance_mark(point_a, point_b, um_per_px, now=None):
    """A two-point distance mark, green-plane pixel coordinates in, a length
    in microns out. Raises ValueError if the two points coincide (a
    zero-length "distance" is not a measurement, it's a misclick)."""
    dist_px = _dist(point_a, point_b)
    if dist_px <= 0:
        raise ValueError("the two points coincide; a distance mark needs two distinct points")
    return {
        "mark_id": uuid.uuid4().hex,
        "type": "distance",
        "created_at": (now or datetime.now()).isoformat(),
        "input": {"points": [[float(point_a[0]), float(point_a[1])],
                              [float(point_b[0]), float(point_b[1])]]},
        # length scales LINEARLY with um_per_px
        "derived": {"distance_px": dist_px, "distance_um": dist_px * um_per_px},
    }


def build_angle_mark(vertex, arm_a, arm_b, now=None):
    """The interior angle at `vertex` between rays to arm_a and arm_b, in
    degrees. Dimensionless, so no calibration is needed or recorded here.
    Raises ValueError if either arm collapses onto the vertex (a
    zero-length ray has no defined direction, so the angle is undefined,
    not just imprecise)."""
    v = np.asarray(vertex, dtype=float)
    a = np.asarray(arm_a, dtype=float) - v
    b = np.asarray(arm_b, dtype=float) - v
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        raise ValueError("an arm point coincides with the vertex; the angle is undefined")
    cos_theta = float(np.clip(np.dot(a, b) / (na * nb), -1.0, 1.0))
    angle_deg = math.degrees(math.acos(cos_theta))
    return {
        "mark_id": uuid.uuid4().hex,
        "type": "angle",
        "created_at": (now or datetime.now()).isoformat(),
        "input": {"vertex": [float(vertex[0]), float(vertex[1])],
                  "arm_a": [float(arm_a[0]), float(arm_a[1])],
                  "arm_b": [float(arm_b[0]), float(arm_b[1])]},
        "derived": {"angle_deg": angle_deg},
    }


def build_polygon_mark(points, um_per_px, now=None):
    """A free polygon, as many vertices as dropped (>= 3). Perimeter and
    area via the shoelace formula, in both pixels and microns. Raises
    ValueError for fewer than 3 points or a degenerate (zero-area, i.e.
    collinear or coincident) polygon.

    AREA SCALES AS um_per_px SQUARED, not linearly -- a µm² result is a
    pixel² result times the conversion factor twice, once per axis. This is
    the easiest place in this whole module to get quietly wrong, so it is
    spelled out here rather than left as an implicit exponent."""
    pts = [(float(x), float(y)) for x, y in points]
    if len(pts) < 3:
        raise ValueError("a polygon needs at least 3 points, got {}".format(len(pts)))
    n = len(pts)
    perim_px = sum(_dist(pts[i], pts[(i + 1) % n]) for i in range(n))
    shoelace = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        shoelace += x1 * y2 - x2 * y1
    area_px2 = abs(shoelace) / 2.0
    if area_px2 <= 0:
        raise ValueError("the polygon has zero area (points are collinear or coincident)")
    return {
        "mark_id": uuid.uuid4().hex,
        "type": "polygon",
        "created_at": (now or datetime.now()).isoformat(),
        "input": {"points": [[x, y] for x, y in pts]},
        "derived": {
            "perimeter_px": perim_px, "perimeter_um": perim_px * um_per_px,
            "area_px2": area_px2, "area_um2": area_px2 * (um_per_px ** 2),
        },
    }


def build_ellipse_mark(boundary_points, center, axes_px, angle_deg, um_per_px, now=None):
    """Records an ellipse FIT'S RESULT; does not compute the fit. Call this
    with whatever a future Fitzgibbon-style algebraic least-squares fit
    (section 7, not built here) produces from boundary_points. Stores both
    halves the checklist calls for: the raw boundary points (input) and the
    fitted center/axes/angle plus derived length/width/area/Q ratio
    (derived) -- one shape yielding all four, per the checklist's own
    framing for round spores. axes_px is (semi-major, semi-minor) in
    pixels; length/width are the full axes (2x semi-axis) in microns.
    Raises ValueError for non-positive axes."""
    major_px, minor_px = axes_px
    if major_px <= 0 or minor_px <= 0:
        raise ValueError("ellipse axes must be positive, got {!r}".format(axes_px))
    length_um = 2.0 * major_px * um_per_px
    width_um = 2.0 * minor_px * um_per_px
    # ellipse area, also scaled by um_per_px SQUARED (see build_polygon_mark)
    area_um2 = math.pi * (major_px * um_per_px) * (minor_px * um_per_px)
    q_ratio = major_px / minor_px
    return {
        "mark_id": uuid.uuid4().hex,
        "type": "ellipse",
        "created_at": (now or datetime.now()).isoformat(),
        "input": {"boundary_points": [[float(x), float(y)] for x, y in boundary_points]},
        "derived": {
            "center": [float(center[0]), float(center[1])],
            "axes_px": [float(major_px), float(minor_px)],
            "angle_deg": float(angle_deg),
            "length_um": length_um, "width_um": width_um,
            "area_um2": area_um2, "q_ratio": q_ratio,
        },
    }


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def render_check():
    # distance: 3-4-5 triangle
    d = build_distance_mark((0.0, 0.0), (3.0, 4.0), um_per_px=2.0)
    assert abs(d["derived"]["distance_px"] - 5.0) < 1e-9
    assert abs(d["derived"]["distance_um"] - 10.0) < 1e-9, "distance must scale LINEARLY"
    try:
        build_distance_mark((1.0, 1.0), (1.0, 1.0), um_per_px=2.0)
        raise AssertionError("expected ValueError for coincident points")
    except ValueError:
        pass
    print("build_distance_mark check PASS: 3-4-5 triangle, linear scaling, "
          "degenerate-input guard")

    # angle: a clean right angle at the origin
    a = build_angle_mark((0.0, 0.0), (5.0, 0.0), (0.0, 5.0))
    assert abs(a["derived"]["angle_deg"] - 90.0) < 1e-6
    # a straight line should read 180 degrees
    a2 = build_angle_mark((0.0, 0.0), (5.0, 0.0), (-5.0, 0.0))
    assert abs(a2["derived"]["angle_deg"] - 180.0) < 1e-6
    try:
        build_angle_mark((0.0, 0.0), (0.0, 0.0), (1.0, 1.0))
        raise AssertionError("expected ValueError for an arm at the vertex")
    except ValueError:
        pass
    print("build_angle_mark check PASS: right angle, straight line, "
          "degenerate-input guard")

    # polygon: a 10x10 px unit square -> known area and perimeter, and the
    # squared-vs-linear area scaling is checked explicitly, not assumed
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    p = build_polygon_mark(square, um_per_px=2.0)
    assert abs(p["derived"]["area_px2"] - 100.0) < 1e-9
    assert abs(p["derived"]["perimeter_px"] - 40.0) < 1e-9
    assert abs(p["derived"]["area_um2"] - 400.0) < 1e-9, \
        "area must scale by um_per_px SQUARED (100px^2 * 2.0^2 = 400, not 200)"
    assert abs(p["derived"]["perimeter_um"] - 80.0) < 1e-9, "perimeter scales linearly"
    try:
        build_polygon_mark([(0, 0), (1, 1)], um_per_px=1.0)
        raise AssertionError("expected ValueError for under 3 points")
    except ValueError:
        pass
    try:
        build_polygon_mark([(0, 0), (1, 0), (2, 0)], um_per_px=1.0)
        raise AssertionError("expected ValueError for collinear (zero-area) points")
    except ValueError:
        pass
    print("build_polygon_mark check PASS: known square area/perimeter, area "
          "scales quadratically (not linearly), both degenerate-input guards")

    # ellipse: a circle (major == minor) is the simplest checkable case
    e = build_ellipse_mark(
        boundary_points=[(10, 0), (0, 10), (-10, 0), (0, -10)],
        center=(0.0, 0.0), axes_px=(10.0, 10.0), angle_deg=0.0, um_per_px=0.5)
    assert abs(e["derived"]["length_um"] - 10.0) < 1e-9   # 2*10*0.5
    assert abs(e["derived"]["width_um"] - 10.0) < 1e-9
    assert abs(e["derived"]["q_ratio"] - 1.0) < 1e-9       # a circle: Q == 1
    expected_area = math.pi * (10 * 0.5) * (10 * 0.5)
    assert abs(e["derived"]["area_um2"] - expected_area) < 1e-6
    try:
        build_ellipse_mark([(0, 0)], (0, 0), (0.0, 5.0), 0.0, 1.0)
        raise AssertionError("expected ValueError for a non-positive axis")
    except ValueError:
        pass
    print("build_ellipse_mark check PASS: circle special case (Q=1), area "
          "formula, degenerate-axis guard (fit math itself not built here)")

    # the store: create, append, orphan-check, all against a temp path so
    # this never touches a real ~/.zynergy/annotations.json
    global ANNOTATION_PATH
    orig_path = ANNOTATION_PATH
    tmp_dir = Path("/tmp/zynergy_annotations_render_check")
    if tmp_dir.exists():
        import shutil
        shutil.rmtree(tmp_dir)
    ANNOTATION_PATH = tmp_dir / "annotations.json"
    try:
        assert load_annotations() == {}, "a missing store should load as {}"

        green = np.arange(64, dtype=np.uint16).reshape(8, 8)
        assert _pixel_hash is not None, "pixel_hash.py must be importable"
        h = _pixel_hash.pixel_sha256(green)

        defaults = {"shape": list(green.shape), "dtype": str(green.dtype),
                    "kind": "green", "calibration_ref": None, "source_sha256": None}
        store = save_mark(h, d, record_defaults=defaults)
        assert store[h]["pixel_sha256"] == h
        assert store[h]["kind"] == "green"
        assert len(store[h]["marks"]) == 1

        # a second mark on the SAME hash must APPEND, not replace, and must
        # not require (or accept silently wrong) record_defaults again
        store = save_mark(h, a)
        assert len(store[h]["marks"]) == 2, "a second mark on one image should accumulate"
        assert store[h]["marks"][0]["mark_id"] == d["mark_id"], \
            "the first mark must survive unchanged"
        assert store[h]["marks"][1]["mark_id"] == a["mark_id"]

        # a hash never seen before, with no record_defaults, must refuse
        try:
            save_mark("0" * 64, p)
            raise AssertionError("expected ValueError: no record_defaults for a new hash")
        except ValueError:
            pass

        # orphans: h is "known", a second, never-saved hash is not present
        # anyway, so the real check is that a KNOWN hash is never reported
        assert find_orphans(load_annotations(), known_hashes={h}) == []
        assert find_orphans(load_annotations(), known_hashes=set()) == [h], \
            "a hash missing from the known set should surface as an orphan"

        print("annotation store check PASS: create-on-first-mark, marks "
              "accumulate without disturbing earlier ones, a brand-new hash "
              "without record_defaults refuses, orphan detection correct")
    finally:
        ANNOTATION_PATH = orig_path

    # calibration_ref_for: exercised against calibrate.py's real store logic
    # (its own temp-path swap), skipped gracefully if calibrate.py is absent
    if _calibrate is not None:
        orig_calib_path = _calibrate.CALIBRATION_PATH
        tmp_calib_dir = Path("/tmp/zynergy_annotations_render_check_calib")
        if tmp_calib_dir.exists():
            import shutil
            shutil.rmtree(tmp_calib_dir)
        _calibrate.CALIBRATION_PATH = tmp_calib_dir / "calibration.json"
        try:
            assert calibration_ref_for("40x") is None, \
                "an uncalibrated objective should give no ref, not raise"
            entry = _calibrate.build_calibration_entry(
                Path("/tmp/fake.dng"), (0.0, 0.0), (500.0, 0.0), 500.0,
                objective="40x", target_type="stage micrometer", focus_score=300.0)
            _calibrate.save_calibration("40x", entry)
            saved = _calibrate.current_calibration("40x")   # entry_id is assigned on save
            ref = calibration_ref_for("40x")
            assert ref["objective"] == "40x"
            assert ref["entry_id"] == saved["entry_id"] is not None
            assert abs(ref["um_per_px"] - 1.0) < 1e-9
            print("calibration_ref_for check PASS: None for an uncalibrated "
                  "objective, names the exact current entry_id once calibrated")
        finally:
            _calibrate.CALIBRATION_PATH = orig_calib_path
    else:
        print("calibration_ref_for check SKIPPED: calibrate.py not importable "
              "from this directory")


if __name__ == "__main__":
    render_check()
