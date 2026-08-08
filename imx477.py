"""imx477.py - sensor profile for the Sony IMX477 (Raspberry Pi HQ Camera).

PRIORITY_click_mapping_fix.md: the freeze-triggering click in Live Measure
mismapped because the preview stream and the green plane come from two
different IMX477 sensor modes that read two different crop rectangles off
the full sensor array. A scale factor can't express that (an off-centre
crop isn't a ratio), so this module exposes, for any output size this
sensor can produce, the crop rectangle that mode actually reads -- origin
AND extent, in full-array pixel units.

Module name matches Picamera2().camera_properties['Model'] EXACTLY
("imx477") -- this is deliberate, not a readability choice. camera_backend
.py resolves a sensor to its profile module with a direct import of the
string the hardware itself reports, never a separate mapping table that
could drift from reality. An unrecognised sensor fails as a missing module
named after the real model, never a silent fallback to this file's own
geometry (see camera_backend.py's _resolve_sensor_profile).

This is driver-layer code, per PHILOSOPHY.md's hardened rule that
camera_backend.py is the only file allowed to know what an IMX477 is:
this module is imported ONLY from camera_backend.py (never from anything
above the camera seam), never imports picamera2/libcamera itself, and
camera_backend.py's own dispatch logic never hardcodes the string
"imx477" -- only FakeCamera does, deliberately, as a stand-in for this
project's real rig. Both halves of the rule hold; "driver layer" here
means camera_backend.py plus the per-sensor profile modules it dispatches
to by hardware-reported name, not literally one file total.

FULL_ARRAY_SIZE and the entries in _CROP_TABLE below are the OFF-RIG
fallback and --render-check fixture only. On real hardware, the
authoritative source is a live Picamera2().sensor_modes read (its own
crop_limits field) -- camera_backend.py caches that at construction and
only falls back to crop_for_size() here for a size that read doesn't
cover. The five sizes below are the ones this project's own on-rig
session already confirmed sensor_modes reports (see HANDOFF.md's "Camera
capability query: sensor_modes hardware-verified" entry): full-FOV
2028x1520 (2x2 binned) and 4056x3040 (unbinned) both read the whole
array; the 16:9-cropped 2028x1080/4056x2160 pair reads a centred
(0, 440, 4056, 2160) window (binned/unbinned versions of the same crop);
1332x990 reads a centred (696, 530, 2664, 1980) window, derived from that
mode's own 2x2-binning-and-centring arithmetic, NOT independently
confirmed against a real crop_limits read. That derivation implies a
preview/still FOV ratio of 4056/2664 ~= 1.523, which lines up closely with
PRIORITY_click_mapping_fix.md's own "expected ratio roughly 1.52" note --
reasonable corroboration, but a live sensor_modes read should still
replace this table's role as anything more than a fallback/fixture the
moment real hardware is available.
"""

FULL_ARRAY_SIZE = (4056, 3040)

# White-level relocation: confirmed from Picamera2Camera's own real still-
# capture config request (camera_backend.py's create_still_configuration
# call, no explicit raw format anywhere in that file) -- cam._still_cfg
# ['raw'] reads {'format': 'SRGGB12_CSI2P', 'size': FULL_ARRAY_SIZE},
# 12-bit, unmutated, before any hardware negotiation. The sensor genuinely
# supports 8/10/12-bit readout (Picamera2().sensor_modes reports all
# three at every mode size) -- this is NOT "the" IMX477 bit depth in some
# absolute sense, it is the bit depth THIS PROJECT'S OWN capture code
# actually requests, today, confirmed rather than assumed. If that
# request ever changes, this must change with it, the same way FULL_
# ARRAY_SIZE would if the sensor mode table changed.
#
# CAVEAT: 12 is correct by CURRENT CONFIGURATION, not by hardware -- the
# same shape as the frozen-GREEN_PLANE_RES-constant bug Stage 3 fixed in
# qt_shell.py, correct by coincidence, wrong the moment a setting moves.
# get_capabilities() reports three real formats on this sensor --
# SRGGB8, SRGGB10, SRGGB12 (confirmed live, camera_backend.py's own
# sensor_modes sweep) -- and Preferences already exposes a capture-
# format selector built from that exact same capability set
# (qt_shell.py's PreferencesDialog._capture_fmt_combo; its own
# render_check asserts this). That selector's chosen value is
# persisted (save_pref("capture_format", ...)) but not yet applied to
# any real capture -- camera_backend.py has no format-selection hook
# for a still capture today (grep confirms "capture_format" is only
# ever saved and loaded, never read by anything that builds a still or
# preview config), so this constant is not silently wrong YET. It
# becomes silently wrong the moment that hook is built and a user picks
# 8-bit or 10-bit: white_level_for_bit_depth would keep deriving from
# this frozen 12, nothing would recompute, nothing would raise, and the
# merged numbers would just shift. Fixing this properly means deriving
# bit depth from the CONFIGURED mode (the same way preview_crop/
# still_crop already derive from preview_res/still_res rather than a
# constant) instead of this constant -- deliberately not done here.
BIT_DEPTH = 12

# (x, y, w, h), all in full-sensor-array pixel units -- see the module
# docstring for provenance/confidence on each entry.
_CROP_TABLE = {
    (4056, 3040): (0, 0, 4056, 3040),      # full res, unbinned, full FOV
    (2028, 1520): (0, 0, 4056, 3040),      # 2x2 binned, full FOV
    (2028, 1080): (0, 440, 4056, 2160),    # 2x2 binned, 16:9 crop
    (4056, 2160): (0, 440, 4056, 2160),    # unbinned, same 16:9 crop
    (1332, 990): (696, 530, 2664, 1980),   # 2x2 binned, centred crop
}


def crop_for_size(size):
    """(x, y, w, h) crop rectangle, in FULL_ARRAY_SIZE pixel units, for
    the IMX477 mode that produces `size`. Raises ValueError for a size
    this table doesn't know -- never a silent guess (e.g. "assume full
    array"), since a wrong guess here is exactly the class of bug this
    module exists to close. Extend _CROP_TABLE with a real, on-rig-
    confirmed crop_limits value rather than adding a guessed entry."""
    key = (int(size[0]), int(size[1]))
    try:
        return _CROP_TABLE[key]
    except KeyError:
        raise ValueError(
            "imx477: no known crop rectangle for output size {!r} -- add "
            "it to _CROP_TABLE with a real, on-rig-confirmed crop_limits "
            "value (from Picamera2().sensor_modes), not a guess".format(key))


if __name__ == "__main__":
    # Self-check, no hardware: internal consistency of the fallback table
    # and crop_for_size's own contract.
    fw, fh = FULL_ARRAY_SIZE
    for size, (x, y, w, h) in _CROP_TABLE.items():
        assert 0 <= x and 0 <= y and x + w <= fw and y + h <= fh, \
            "{!r} crop {!r} falls outside FULL_ARRAY_SIZE {!r}".format(
                size, (x, y, w, h), FULL_ARRAY_SIZE)
        # Binning must not change aspect ratio: the pre-crop window's own
        # aspect (w/h) should match the output size's aspect (both are the
        # same rectangle, just sampled at different pixel pitches).
        assert abs((w / h) - (size[0] / size[1])) < 1e-6, \
            "{!r}'s crop {!r} has a different aspect ratio than the " \
            "output size itself".format(size, (x, y, w, h))
        assert crop_for_size(size) == (x, y, w, h)
    print("crop table internal-consistency check PASS: {} sizes, all "
          "within FULL_ARRAY_SIZE, all aspect-preserving".format(len(_CROP_TABLE)))

    try:
        crop_for_size((999, 999))
        raise AssertionError("expected ValueError for an unknown size")
    except ValueError:
        pass
    print("crop_for_size unknown-size check PASS: raises rather than "
          "guessing full-array")

    # The specific cross-check PRIORITY_click_mapping_fix.md itself calls
    # for: the 1332x990 mode's implied FOV ratio against the still mode
    # should land close to the brief's own "roughly 1.52" expectation.
    still_crop = crop_for_size((4056, 3040))
    preview_crop = crop_for_size((1332, 990))
    ratio = still_crop[2] / preview_crop[2]
    assert 1.4 < ratio < 1.6, \
        "1332x990's implied FOV ratio {!r} is nowhere near the brief's " \
        "own ~1.52 expectation -- table entry likely wrong".format(ratio)
    print("FOV-ratio cross-check PASS: 4056x3040 vs 1332x990 implies "
          "{:.4f}x (brief's on-rig division count: ~1.42x; expected: "
          "~1.52x)".format(ratio))

    print("imx477 self-check PASS")
