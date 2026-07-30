#!/usr/bin/env python3
"""test_sensor_generality.py - two regression cases that FAIL today,
deliberately, per the multi-sensor audit report's own finding: the
existing --render-check suites (measure.py's, qt_shell.py's,
plane_cache.py's) validate GREEN_PLANE_RES-style click mapping and
shape-based plane dispatch against IMX477's OWN numbers -- so a regression
on a DIFFERENT sensor's geometry has nothing in this project to catch it.
These two cases exist to be the target Tasks 2-5 turn green, not to pass
right now. No PyQt5, no camera, no picamera2.

Case 1 (mock_oddgreen.py): a Bayer sensor whose full-array resolution is
odd in both axes. debayer.extract_green()'s real output for such an array
is ceil-sized; every FULL_RES//2-style constant in this project is
floor-sized. A genuinely-extracted green plane from this sensor is
rejected by measure.load_measurement_plane's shape-based dispatch.

Case 2 (mock_mono.py): a monochrome sensor, no CFA. Nothing in this
project currently records "no CFA" as a fact any caller can check, so a
mono capture that happens to land at a full-array-sized shape is silently
run through extract_green() and handed back as a quarter-resolution
"measurement plane" -- wrong data, no error, no warning. This is the worse
failure mode of the two: case 1 at least fails loudly.

Run: python3 test_sensor_generality.py
Exits non-zero while either case is still broken (both are, today).
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import tifffile

import calibrate
import debayer
import measure
import mock_mono
import mock_oddgreen

FAILURES = []


def _report(name, ok, detail):
    tag = "PASS" if ok else "FAIL"
    print("[{}] {}: {}".format(tag, name, detail))
    if not ok:
        FAILURES.append(name)


def _synthetic_bayer_mosaic(h, w, seed=0):
    """A plausible raw mosaic: real per-quad-position structure (not flat),
    so extract_green's stride-slice is exercised against real-looking
    data, not degenerate all-zero input."""
    rng = np.random.default_rng(seed)
    base = rng.integers(200, 4000, size=(2, 2)).astype(np.uint16)
    tile = np.tile(base, (h // 2 + 1, w // 2 + 1))[:h, :w]
    return (tile + rng.integers(0, 50, size=(h, w))).astype(np.uint16)


def _with_measure_res(full_res, body):
    """Run `body` with measure.FULL_RES/GREEN_PLANE_RES swapped to the
    mock sensor's own numbers -- the best-case stand-in for "a future Task
    wired this sensor's geometry in" -- and always restore afterward, even
    for a case that will fail below. Note: even granting the sensor its
    own FULL_RES, the FLOOR-division formula deriving GREEN_PLANE_RES from
    it is itself the bug under test in case 1, so this helper does not
    paper over that; it only removes the (already-separately-flagged and
    now-fixed) "wrong module constant entirely" failure mode from the two
    cases below, so each isolates the ONE remaining defect it targets."""
    orig_full, orig_green = measure.FULL_RES, measure.GREEN_PLANE_RES
    try:
        measure.FULL_RES = full_res
        measure.GREEN_PLANE_RES = (full_res[0] // 2, full_res[1] // 2)
        body()
    finally:
        measure.FULL_RES, measure.GREEN_PLANE_RES = orig_full, orig_green


def case_1_odd_resolution_green_plane():
    """A REAL green plane, correctly extracted by debayer.py's own
    production extract_green(), from a sensor whose full-array size is odd
    in both axes. measure.load_measurement_plane must recognise it as a
    green plane the same way it does for the IMX477's own half-res
    planes. It does not."""
    w, h = mock_oddgreen.FULL_ARRAY_SIZE
    mosaic = _synthetic_bayer_mosaic(h, w)
    green, _ = debayer.extract_green(
        mosaic, calibrate.DEFAULT_CFA_PATTERN, calibrate.DEFAULT_GREEN_WHICH)

    def body():
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "oddgreen_master_green.tif"
            tifffile.imwrite(str(path), green)
            try:
                loaded = measure.load_measurement_plane(str(path))
                ok = np.array_equal(loaded, green)
                _report("case_1_odd_resolution_green_plane", ok,
                        "measure.load_measurement_plane accepted the real "
                        "green plane" if ok else
                        "loaded array did not match the real green plane "
                        "(shape {} vs {})".format(loaded.shape, green.shape))
            except ValueError as exc:
                _report("case_1_odd_resolution_green_plane", False,
                        "measure.load_measurement_plane REJECTED a "
                        "genuinely-extracted green plane (shape {}, from a "
                        "{} full array): {}".format(
                            green.shape, mock_oddgreen.FULL_ARRAY_SIZE, exc))

    _with_measure_res(mock_oddgreen.FULL_ARRAY_SIZE, body)


def case_2_mono_no_cfa():
    """A monochrome sensor's real captured frame -- smooth luminance, no
    2x2 periodic colour structure of any kind -- landing at this mock
    sensor's own full-array size. There is no CFA, so nothing should be
    'green-extracted' from it: a correct pipeline either measures directly
    on the full frame, or refuses with a clear no-CFA error. Today's
    measure.load_measurement_plane does neither: it cannot tell this array
    apart from a genuine Bayer mosaic of the same shape, so it silently
    strides it down to quarter resolution and calls the result a
    measurement plane."""
    w, h = mock_mono.FULL_ARRAY_SIZE
    yy, xx = np.mgrid[0:h, 0:w]
    mono_frame = (1000 + 50 * np.sin(xx / 37.0)
                  + 50 * np.cos(yy / 29.0)).astype(np.uint16)

    def body():
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mono_master.tif"
            tifffile.imwrite(str(path), mono_frame)
            try:
                loaded = measure.load_measurement_plane(str(path))
                ok = (loaded.shape == mono_frame.shape
                      and np.array_equal(loaded, mono_frame))
                _report("case_2_mono_no_cfa", ok,
                        "measurement plane preserved the full mono frame"
                        if ok else
                        "mono frame {} was silently 'green-extracted' down "
                        "to {} -- CFA_PRESENT={} on this sensor, nothing "
                        "should have been extracted, and no error was "
                        "raised".format(mono_frame.shape, loaded.shape,
                                        mock_mono.CFA_PRESENT))
            except ValueError as exc:
                # Also a failure today, just a different shape of one: no
                # code path anywhere consults CFA_PRESENT, so whichever
                # branch actually fires is accidental, not designed.
                _report("case_2_mono_no_cfa", False,
                        "measure.load_measurement_plane raised a generic "
                        "shape error rather than a clear no-CFA refusal: "
                        "{}".format(exc))

    _with_measure_res(mock_mono.FULL_ARRAY_SIZE, body)


def main():
    case_1_odd_resolution_green_plane()
    case_2_mono_no_cfa()
    print()
    if FAILURES:
        print("{}/2 case(s) FAILING today (expected until Tasks 2-5 land): "
              "{}".format(len(FAILURES), ", ".join(FAILURES)))
        sys.exit(1)
    print("both cases pass -- multi-sensor generality gap closed")


if __name__ == "__main__":
    main()
