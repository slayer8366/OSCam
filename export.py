#!/usr/bin/env python3
"""export.py - JSON export of measurement results from the annotation store.

Per build checklist §11: flat results view, one record per measurement,
carrying pixel_sha256, calibration_ref (which calibration produced the microns),
mark type/coordinates, and computed values. An exported number states which
image and which calibration it came from, not just the figure.

Run standalone for the headless self-check:
  python3 export.py --render-check
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

try:
    from . import annotations as _annotations
except ImportError:
    try:
        import annotations as _annotations
    except ImportError:
        _annotations = None

__version__ = "1.0"


def extract_values_from_mark(mark):
    """Extract the computed values dict from a mark, keyed by type-specific
    field names (distance_um, angle_deg, area_um2, length_um, etc.). These are
    the fields a user cares about when exporting results."""
    derived = mark.get("derived", {})
    mark_type = mark.get("type")
    if mark_type == "distance":
        return {"distance_um": derived.get("distance_um"),
                "distance_px": derived.get("distance_px")}
    elif mark_type == "angle":
        return {"angle_deg": derived.get("angle_deg")}
    elif mark_type == "polygon":
        return {"perimeter_um": derived.get("perimeter_um"),
                "perimeter_px": derived.get("perimeter_px"),
                "area_um2": derived.get("area_um2"),
                "area_px2": derived.get("area_px2")}
    elif mark_type == "ellipse":
        return {"length_um": derived.get("length_um"),
                "width_um": derived.get("width_um"),
                "area_um2": derived.get("area_um2"),
                "q_ratio": derived.get("q_ratio")}
    return {}


def build_measurement_record(pixel_sha256, image_record, mark):
    """One record per measurement: the mark's data plus image/calibration
    provenance, ready for export."""
    return {
        "pixel_sha256": pixel_sha256,
        "image_kind": image_record.get("kind"),
        "image_shape": image_record.get("shape"),
        "calibration_ref": image_record.get("calibration_ref"),
        "mark_id": mark.get("mark_id"),
        "mark_type": mark.get("type"),
        "created_at": mark.get("created_at"),
        "input": mark.get("input"),
        "derived": mark.get("derived"),
        "values": extract_values_from_mark(mark),
    }


def export_measurements(store=None, out_path=None):
    """Flatten the annotation store into a results view: one record per
    measurement. If store is None, loads the central store. If out_path is
    given, writes the JSON; otherwise returns the dict."""
    if _annotations is None:
        raise RuntimeError("annotations.py must be importable")
    store = store if store is not None else _annotations.load_annotations()
    measurements = []
    for pixel_sha256, image_record in store.items():
        for mark in image_record.get("marks", []):
            rec = build_measurement_record(pixel_sha256, image_record, mark)
            measurements.append(rec)
    result = {
        "export_version": __version__,
        "export_date": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "total_measurements": len(measurements),
        "measurements": measurements,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=2))
    return result


def render_check():
    """Self-test: build synthetic annotation store, export, verify schema."""
    assert _annotations is not None, "annotations.py must be importable"

    # Build a mock annotation store with one image and several marks
    import numpy as np
    green = np.arange(64, dtype=np.uint16).reshape(8, 8)
    h = "abc123def456"  # fake pixel_sha256

    dist_mark = _annotations.build_distance_mark((0.0, 0.0), (100.0, 0.0), um_per_px=0.5)
    angle_mark = _annotations.build_angle_mark((0.0, 0.0), (5.0, 0.0), (0.0, 5.0))
    poly_mark = _annotations.build_polygon_mark([(0, 0), (10, 0), (10, 10), (0, 10)], um_per_px=2.0)
    ellipse_mark = _annotations.build_ellipse_mark(
        [(10, 0), (0, 10), (-10, 0), (0, -10)], (0.0, 0.0), (10.0, 10.0), 0.0, um_per_px=0.5)

    calib_ref = {"objective": "40x", "entry_id": "fit_12345", "um_per_px": 0.5}
    image_rec = _annotations.new_image_record(h, list(green.shape), str(green.dtype),
                                               kind="green", calibration_ref=calib_ref)
    image_rec["marks"] = [dist_mark, angle_mark, poly_mark, ellipse_mark]

    store = {h: image_rec}
    result = export_measurements(store)

    # Verify the export structure
    assert result["export_version"] == __version__
    assert "export_date" in result
    assert result["total_measurements"] == 4, "should export 4 marks"
    assert len(result["measurements"]) == 4

    # Verify each measurement record has the right shape
    for i, meas in enumerate(result["measurements"]):
        assert meas["pixel_sha256"] == h
        assert meas["image_kind"] == "green"
        assert meas["image_shape"] == [8, 8]
        assert meas["calibration_ref"] == calib_ref
        assert "mark_id" in meas
        assert "mark_type" in meas
        assert "created_at" in meas
        assert "input" in meas
        assert "derived" in meas
        assert "values" in meas

    # Spot-check values
    dist_meas = result["measurements"][0]
    assert dist_meas["mark_type"] == "distance"
    assert "distance_um" in dist_meas["values"]
    assert "distance_px" in dist_meas["values"]
    assert dist_meas["values"]["distance_um"] == 50.0  # 100 px * 0.5 um/px

    angle_meas = result["measurements"][1]
    assert angle_meas["mark_type"] == "angle"
    assert "angle_deg" in angle_meas["values"]

    poly_meas = result["measurements"][2]
    assert poly_meas["mark_type"] == "polygon"
    assert "area_um2" in poly_meas["values"]
    assert "perimeter_um" in poly_meas["values"]

    ellipse_meas = result["measurements"][3]
    assert ellipse_meas["mark_type"] == "ellipse"
    assert "length_um" in ellipse_meas["values"]
    assert "width_um" in ellipse_meas["values"]
    assert "q_ratio" in ellipse_meas["values"]

    print("export_measurements check PASS: schema correct, all mark types "
          "represented, values extracted correctly")


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        import argparse
        ap = argparse.ArgumentParser(description="Export measurements to JSON.")
        ap.add_argument("-o", "--output", required=True, help="output JSON file")
        ap.add_argument("--render-check", action="store_true")
        args = ap.parse_args()
        result = export_measurements(out_path=args.output)
        print(f"Exported {result['total_measurements']} measurements to {args.output}")
