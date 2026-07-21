#!/usr/bin/env python3
"""publish.py - publication packages for reproducible measurement results.

Per build checklist §12: Publish the green plane as the reproducible
measurement image (its pixel hash + calibration lets anyone re-derive every
number on the spot). Publish display images for display (their provenance
names the green master), and mark any burned-in marks as "NOT a measurement".

A publication package contains:
  * green_plane.tif — the measurement image (kind="green"), with pixel_sha256
  * results.json — measurements from export.py, with calibration_ref for each
  * manifest.json — provenance: which calibration, which green hash, metadata
  * (optional) display_*.tif — display derivatives, marked kind="display",
    with source_sha256 pointing to the green master
  * (optional) marked_*.tif — flattened figures (marks burned in), marked
    kind="NOT a measurement" (same shelf as _display.tif)

Results cross freely as physical quantities: a caption in µm is honest on any
image, because the number is scale-space-independent. Pixel geometry is
plane-bound; green-to-display is not identity (×2 plus fixed 1px x offset on
bilinear for green-which=1; sub-pixel quad offset on binned).

Two ways to run:
  python3 publish.py --render-check      headless: schema and provenance
                                         validation, no PyQt5 or images.
  python3 publish.py -g green.tif        the interactive GUI (future work:
    -c calib.json -o /path/to/package    allow display derivative creation
    (output is a directory with the                    and mark burning).
    complete publication package).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from . import pixel_hash as _pixel_hash
except ImportError:
    try:
        import pixel_hash as _pixel_hash
    except ImportError:
        _pixel_hash = None

try:
    from . import export as _export
except ImportError:
    try:
        import export as _export
    except ImportError:
        _export = None

try:
    from . import annotations as _annotations
except ImportError:
    try:
        import annotations as _annotations
    except ImportError:
        _annotations = None

__version__ = "1.0"


def create_publication_manifest(green_plane_path, green_sha256, calibration_ref,
                                 results_count=0, display_images=None, now=None):
    """The manifest: provenance document tying green plane, calibration, and
    results together. Anyone can re-derive every measurement by knowing:
      * which green plane (pixel_sha256)
      * which calibration (entry_id + um_per_px snapshot)
      * the measurement records (from results.json)

    display_images: list of {"path": str, "kind": str} for derivative images.
    """
    return {
        "publication_version": __version__,
        "published_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "green_plane": {
            "path": str(Path(green_plane_path).name),
            "pixel_sha256": green_sha256,
            "kind": "green",
            "description": "The reproducible measurement image. All pixel "
                          "coordinates in the results refer to this plane's "
                          "coordinate frame.",
        },
        "calibration": calibration_ref or {
            "note": "No calibration on record; results are in pixels only."
        },
        "results": {
            "total_measurements": results_count,
            "provenance": "Each measurement names which image (pixel_sha256) "
                         "and which calibration (entry_id) produced its microns."
        },
        "display_derivatives": display_images or [],
        "notes": {
            "reproducibility": "Re-derive every number: load green_plane.tif, "
                              "apply calibration.scale_um_per_px, apply marks "
                              "from results.json (all coordinates are green-plane pixels).",
            "coordinate_plane": "Results are bound to the green plane "
                               "(green-which=1). Green-to-display is not identity: "
                               "×2 scale + 1px x offset on bilinear. See the "
                               "calibrate.py documentation for exact transforms.",
            "display_images": "Any _display.tif or marked_*.tif are export "
                            "derivatives (kind != 'measurement'), for viewing only. "
                            "Do not measure on them; they are post-processed "
                            "(tonemap, CLAHE, sharpening may have moved edges).",
        },
    }


def publish_measurements(green_plane_path, calibration_ref=None, out_dir=None,
                         include_export=True):
    """Create a publication package around an on-disk green plane: hash it,
    pull ITS marks out of the central annotation store, and (if out_dir is
    given) write results.json + manifest.json alongside it. The caller is
    responsible for placing the green plane file itself (measure.py's
    publish button writes the in-memory plane as a deflate TIFF into out_dir
    first, then calls this) -- this function never copies or rewrites pixel
    data, keeping the write-once rule for measurement masters.

    results.json holds ONLY this image's measurements (the store sliced to
    the green plane's own hash), so its record count and the manifest's
    total_measurements always agree -- a package is one image's evidence,
    not a dump of every measurement ever made on this machine.

    If out_dir is None, returns just the manifest dict (for testing)."""
    if _pixel_hash is None or _export is None or _annotations is None:
        raise RuntimeError(
            "pixel_hash.py, export.py, and annotations.py must all be importable")

    green_plane_path = Path(green_plane_path)
    if not green_plane_path.exists():
        raise FileNotFoundError(f"Green plane not found: {green_plane_path}")

    green_sha256 = _pixel_hash.hash_tiff(green_plane_path)

    store = _annotations.load_annotations()
    image_record = store.get(green_sha256)
    marks = image_record.get("marks", []) if image_record else []
    results_count = len(marks)

    manifest = create_publication_manifest(
        green_plane_path, green_sha256, calibration_ref, results_count=results_count)

    if out_dir is None:
        return manifest

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if include_export:
        # The store sliced to this one image, so the exported records match
        # the manifest's count exactly (see docstring).
        store_slice = {green_sha256: image_record} if image_record else {}
        _export.export_measurements(store=store_slice,
                                    out_path=out_dir / "results.json")

    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def render_check():
    """Self-test: build synthetic publication, verify schema and provenance."""
    assert _pixel_hash is not None, "pixel_hash.py must be importable"
    assert _export is not None, "export.py must be importable"
    assert _annotations is not None, "annotations.py must be importable"

    # Create synthetic data
    import tempfile
    tmp = Path(tempfile.mkdtemp())

    green = np.arange(64, dtype=np.uint16).reshape(8, 8)
    green_path = tmp / "green.tif"
    import tifffile
    tifffile.imwrite(str(green_path), green, compression="deflate")

    green_sha256 = _pixel_hash.hash_tiff(green_path)

    # Synthetic calibration ref
    calib_ref = {"objective": "40x", "entry_id": "fit_abc123", "um_per_px": 0.5}

    # Create manifest
    manifest = create_publication_manifest(
        green_path, green_sha256, calib_ref, results_count=3)

    # Verify schema
    assert manifest["publication_version"] == __version__
    assert "published_at" in manifest
    assert manifest["green_plane"]["pixel_sha256"] == green_sha256
    assert manifest["green_plane"]["kind"] == "green"
    assert manifest["calibration"]["objective"] == "40x"
    assert manifest["results"]["total_measurements"] == 3
    assert "reproducibility" in manifest["notes"]

    print("create_publication_manifest check PASS: schema correct, provenance "
          "chain documented (green_sha256 -> calibration -> results)")

    # Test that display images are properly marked as derivatives
    display_info = [
        {"path": "display.tif", "kind": "display", "source_sha256": green_sha256}
    ]
    manifest_with_display = create_publication_manifest(
        green_path, green_sha256, calib_ref, display_images=display_info)
    assert len(manifest_with_display["display_derivatives"]) == 1
    assert manifest_with_display["display_derivatives"][0]["source_sha256"] == green_sha256
    assert manifest_with_display["display_derivatives"][0]["kind"] == "display"

    print("publication manifest check PASS: display derivatives properly marked "
          "as non-measurement (kind='display'), sourced to green_sha256")

    # publish_measurements end-to-end, against a temp annotation store (the
    # same path-swap isolation annotations.py's own render_check uses):
    # marks exist for THIS green and for an unrelated image, and the
    # package must contain only this green's -- results.json and the
    # manifest count must agree, which is the whole point of the slice.
    orig_annotation_path = _annotations.ANNOTATION_PATH
    _annotations.ANNOTATION_PATH = tmp / "annotations.json"
    try:
        mark_here = _annotations.build_distance_mark((0, 0), (30, 40), um_per_px=0.5)
        _annotations.save_mark(green_sha256, mark_here, record_defaults={
            "shape": list(green.shape), "dtype": str(green.dtype),
            "kind": "green", "calibration_ref": calib_ref, "source_sha256": None})
        mark_elsewhere = _annotations.build_distance_mark((0, 0), (3, 4), um_per_px=1.0)
        _annotations.save_mark("f" * 64, mark_elsewhere, record_defaults={
            "shape": [4, 4], "dtype": "uint16", "kind": "green",
            "calibration_ref": None, "source_sha256": None})

        pkg_dir = tmp / "package"
        m = publish_measurements(green_path, calibration_ref=calib_ref, out_dir=pkg_dir)
        assert m["results"]["total_measurements"] == 1, \
            "manifest should count only THIS image's marks"
        results = json.loads((pkg_dir / "results.json").read_text())
        assert results["total_measurements"] == 1, \
            "results.json must hold only this image's measurements, not the whole store"
        assert results["measurements"][0]["pixel_sha256"] == green_sha256
        assert results["measurements"][0]["values"]["distance_um"] == 25.0  # 50px * 0.5
        manifest_on_disk = json.loads((pkg_dir / "manifest.json").read_text())
        assert manifest_on_disk["green_plane"]["pixel_sha256"] == green_sha256
        assert manifest_on_disk["results"]["total_measurements"] == 1, \
            "manifest.json on disk must agree with results.json"

        # An image with NO marks still publishes honestly: zero-count package.
        bare = np.arange(16, dtype=np.uint16).reshape(4, 4)
        bare_path = tmp / "bare.tif"
        tifffile.imwrite(str(bare_path), bare, compression="deflate")
        bare_dir = tmp / "bare_package"
        m2 = publish_measurements(bare_path, calibration_ref=None, out_dir=bare_dir)
        assert m2["results"]["total_measurements"] == 0
        bare_results = json.loads((bare_dir / "results.json").read_text())
        assert bare_results["total_measurements"] == 0 and bare_results["measurements"] == []
        assert "No calibration" in m2["calibration"].get("note", ""), \
            "an uncalibrated publish should say so, not fake a calibration"
        print("publish_measurements check PASS: package holds only its own image's "
              "measurements (store sliced by hash), counts agree between manifest "
              "and results.json, zero-mark and no-calibration publishes stay honest")
    finally:
        _annotations.ANNOTATION_PATH = orig_annotation_path

    # Clean up
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        import argparse
        ap = argparse.ArgumentParser(
            description="Create a publication package with reproducible provenance.")
        ap.add_argument("-g", "--green", required=True, help="green plane TIFF")
        ap.add_argument("-c", "--calibration", default=None,
                       help="calibration JSON (optional, for metadata)")
        ap.add_argument("-o", "--output", required=True,
                       help="output directory for the publication package")
        ap.add_argument("--render-check", action="store_true")
        args = ap.parse_args()

        if args.render_check:
            render_check()
        else:
            calib = None
            if args.calibration:
                calib = json.loads(Path(args.calibration).read_text())
            publish_measurements(args.green, calibration_ref=calib, out_dir=args.output)
            print(f"Published to {args.output}")
