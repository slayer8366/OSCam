#!/usr/bin/env python3
"""
hdr_merge.py — merge a bracketed exposure series into one linear, high-bit-depth
HDR image, in scene-referred linear light, with a full provenance record.

This is a sibling to frame_average.py and is meant to run AFTER it: average each
exposure level into a clean master first (frame_average.py), then feed those
masters here with their exposure times. You get both wins — per-level sqrt(N)
noise reduction and an extended capture range that no single exposure off the
sensor could hold.

The physics it relies on (and why linear RAW matters):
    For a linear sensor, value ≈ irradiance * exposure_time + black. So each
    frame gives an independent estimate of the per-pixel scene irradiance:

        E_i = (value_i - black) / t_i

    These estimates are combined with a per-pixel weight that trusts mid-tones,
    distrusts pixels near the noise floor, and scales down toward zero as more
    of a photosite's own RAW SAMPLES were saturated (a fully clipped pixel
    carries no recoverable value; a pixel clipped in one sample out of eight
    still carries most of its real signal, and is weighted accordingly rather
    than discarded outright). This exclusion is per-pixel, continuous, and
    comes from frame_average.py's own per-frame saturation record (its
    excluded-count sibling beside each master) — not a threshold test on this
    tool's own averaged input, which cannot see individual clipped raw samples
    that dilution has already hidden inside an average. A bracket whose
    masters carry no such record merges without saturation exclusion for that
    bracket, logged plainly rather than silently assumed clean. Longer
    exposures additionally carry more weight where they are still valid,
    because their estimate of E is less noise-dominated. The merge is
    therefore close to a physical calculation rather than a cosmetic blend —
    but ONLY if the inputs are genuinely linear. If your masters were
    gamma/ISP-encoded, linearise them first (frame_average.py --gamma ...
    --linear-out); merging encoded data is the same category error as
    flat-fielding encoded data.

What it does NOT do:
    - It does not tone-map. The output is the linear irradiance map. Tone
      mapping for display is a separate, reversible step that must never be
      baked into the measurement.
    - It does not estimate exposure ratios from the pixels. You state the
      exposure times explicitly; the merge is only as honest as those numbers,
      so they are required and are recorded verbatim in the provenance block.

Usage:
    # three masters at 1, 4 and 16 ms, 32-bit linear float output
    python hdr_merge.py \
        -e master_1ms.tif  0.001 \
        -e master_4ms.tif  0.004 \
        -e master_16ms.tif 0.016 \
        -o hdr_linear.tif

    # 12-bit RAW packed right-justified in a 16-bit container: tell it the real
    # white level, hash the inputs for an auditable record
    python hdr_merge.py -e s.tif 0.002 -e m.tif 0.008 -e l.tif 0.032 \
        --white-level 4095 --hash -o hdr_linear.tif

Capture notes:
    - Vary ONLY exposure time between brackets. Keep gain, illumination, focus
      and framing fixed; the merge assumes every frame sees the same scene.
    - Space brackets so each tonal region is well-exposed (not clipped, not
      buried) in at least one frame. ~2 stops apart is a sane default.
    - Static specimens only: any motion between brackets ghosts the merge.

Output & precision notes:
    - Default output is 32-bit float linear, normalised so a chosen high
      percentile maps to 1.0 (the divisor is recorded, so absolute irradiance
      ratios are recoverable: E = pixel * norm_divisor). Float keeps the
      recovered highlights that 16-bit would requantise or clip.
    - 16-bit output is offered for convenience but clips everything above the
      normalisation point and requantises; the clip count is recorded so the
      loss is never silent.
    - Saturation handling is the usual HDR footgun. This tool no longer infers
      it from a threshold on its own averaged input (that test cannot see a
      raw sample clipped in one frame out of eight once averaging has diluted
      it below any threshold) — it reads frame_average.py's own per-photosite
      clean-sample fraction instead, when a master carries one. A bracket with
      no such record merges without saturation exclusion, logged plainly.
      --sat is still accepted (removal is a separate, later piece of work)
      but no longer affects the merge in any way. If your white level is not
      the container's dtype max (e.g. 12-bit data in a 16-bit file), still set
      --white-level — it governs the irradiance estimate itself, independent
      of saturation exclusion.

Provenance completeness note:
    A handful of fields below are only as good as what the caller tells
    this tool. white_level is only valid for the analogue gain a bracket
    was actually shot at, and this tool has no way to know that gain
    unless the caller supplies it. Capture settings (gain, sensor mode,
    real per-frame capture time) can only be recorded if an upstream
    master already carries them: the raw capture side (camera_backend.py,
    provenance.py) already records AnalogueGain/ExposureTime per frame,
    but frame_average.py — the tool that turns those raw frames into the
    "master" files this tool consumes — does not yet read that and does
    not yet stamp any of it into its own output's provenance block. Until
    that upstream propagation exists, this tool records those fields as
    an explicit `null` rather than omitting them or guessing — a reader
    should never have to wonder whether a missing field means "not
    applicable" or "nobody checked."

Requires: numpy, tifffile.
"""
import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import tifffile

try:
    import camera_backend
except ImportError:
    camera_backend = None

__version__ = "1.1"


def load_frame(path):
    a = tifffile.imread(str(path))
    if a.ndim == 2:
        a = a[:, :, None]          # treat grayscale / Bayer mosaic as 1-channel
    return a


def dtype_max(dtype):
    if dtype == np.uint8:
        return 255.0
    if dtype == np.uint16:
        return 65535.0
    if dtype in (np.float32, np.float64):
        return 1.0                 # assume float inputs are already in [0, 1]
    sys.exit(f"Unsupported input dtype {dtype}; expected uint8, uint16 or float.")


def sha256_file(path, _buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_buf), b""):
            h.update(chunk)
    return h.hexdigest()


def try_read_embedded_exposure(path):
    """If a master carries a JSON provenance block with an 'exposure_s' field,
    return it; otherwise None. Lets a future frame_average.py stamp exposure
    time and have it picked up automatically, without ever guessing."""
    try:
        with tifffile.TiffFile(str(path)) as tf:
            desc = tf.pages[0].description
        meta = json.loads(desc)
        val = meta.get("exposure_s")
        return float(val) if val is not None else None
    except Exception:
        return None


def try_read_embedded_capture_meta(path):
    """Best-effort read of per-frame capture settings (analogue gain, sensor
    mode, real capture time) from a master's own provenance block. Returns a
    dict with all three keys always present, None for any the upstream
    writer didn't stamp — so a reader can tell "this sensor session didn't
    record it" from "this tool never looked." No upstream writer stamps
    these yet (frame_average.py's own provenance block has no such fields —
    confirmed by reading it, not assumed), so today every one of these reads
    back None; this is the read-side half of that fix, landing ahead of it
    so nothing else has to change in this file once the write side exists."""
    keys = ("analogue_gain", "sensor_mode", "capture_time_utc")
    try:
        with tifffile.TiffFile(str(path)) as tf:
            desc = tf.pages[0].description
        meta = json.loads(desc)
    except Exception:
        meta = {}
    return {k: meta.get(k) for k in keys}


def load_clean_fraction(master_path, expected_shape):
    """Per-photosite clean-sample fraction for one master, derived from
    frame_average.py's own excluded-count sibling (c75ab94) -- NOT a
    re-read of 24a07c6's raw per-frame .satmask.npz masks. The sibling IS
    their numerically exact aggregate (excluded_count > 0 at a photosite
    exactly where the raw masks show clipped-in-at-least-one-frame,
    excluded_count == n exactly where they show clipped-in-every-frame),
    already computed once by average_burst during averaging -- reading it
    here does not lose information the per-frame masks carried and does
    not re-derive something frame_average.py already got right.

    Returns (clean_fraction, mask_used, note):
      - sibling absent: (None, False, why) -- the bracket has no mask
        data (2026-08-03_230856/2026-08-04_013732 today, or any master
        predating c75ab94). Never a fabricated all-ones array standing
        in for "not measured" -- a caller must not be able to mistake
        "no data" for "measured and found clean."
      - sibling present but unusable (no frames_averaged in the master's
        own provenance, or a shape mismatch): (None, False, why) -- same
        contract, refuse rather than guess.
      - sibling present and usable: (array in [0, 1], True, note).
        clean_fraction = (n - excluded_count) / n, clipped to [0, 1] only
        to guard against a corrupt excluded_count > n; n itself is never
        assumed, always read from the master's own precision.
        frames_averaged field.
    """
    master_path = Path(master_path)
    excl_path = master_path.with_name(master_path.stem + "_excluded_count.tif")
    if not excl_path.is_file():
        return None, False, f"no excluded-count sibling found at {excl_path.name}"
    try:
        with tifffile.TiffFile(str(master_path)) as tf:
            desc = tf.pages[0].description
        meta = json.loads(desc)
        n = meta.get("precision", {}).get("frames_averaged")
    except Exception as exc:
        return None, False, (
            f"{excl_path.name} exists but {master_path.name}'s own provenance "
            f"could not be read ({exc}); refusing to use an excluded-count "
            f"sibling with no known frame count")
    if not n:
        return None, False, (
            f"{excl_path.name} exists but {master_path.name}'s own provenance "
            f"has no precision.frames_averaged to normalise it against")
    excl = tifffile.imread(str(excl_path)).astype(np.float64)
    if excl.ndim == 2:
        excl = excl[:, :, None]
    if excl.shape != expected_shape:
        return None, False, (
            f"{excl_path.name} shape {excl.shape} != master shape "
            f"{expected_shape}; refusing a mismatched mask rather than "
            f"guessing how to reconcile it")
    clean_fraction = np.clip((n - excl) / n, 0.0, 1.0)
    return clean_fraction, True, f"loaded from {excl_path.name}, n={n}"


def parse_exposures(raw_pairs):
    """raw_pairs: list of (path_str, seconds_str). Returns list of dicts sorted
    ascending by exposure time. Fails loudly on bad or non-positive times."""
    items = []
    for path_str, sec_str in raw_pairs:
        p = Path(path_str)
        if not p.is_file():
            sys.exit(f"Exposure frame not found: {path_str}")
        if sec_str.lower() == "auto":
            t = try_read_embedded_exposure(p)
            if t is None:
                sys.exit(f"--exposure {path_str} auto: no 'exposure_s' found in "
                         f"its provenance; give the time explicitly.")
            source = "embedded"
        else:
            try:
                t = float(sec_str)
            except ValueError:
                sys.exit(f"Bad exposure time {sec_str!r} for {path_str}.")
            source = "explicit"
        if not (t > 0):
            sys.exit(f"Exposure time must be > 0 (got {t} for {path_str}).")
        items.append({"path": p, "t": t, "t_source": source})
    if len(items) < 2:
        sys.exit("HDR merge needs at least two exposures.")
    items.sort(key=lambda d: d["t"])           # shortest first
    return items


def merge(exposures, white_level, black, sat_frac, norm_percentile, hash_inputs,
          channel_layout=None, cfa_pattern=None):
    """Stream the bracket set into a linear irradiance estimate.

    One pass over the files; memory is bounded to a few full frames regardless
    of how many brackets there are. Per pixel, per exposure:

        E_i           = (value_i/white - black) / t_i     # irradiance estimate
        p_i           = clip((value_i/white - black)/(1-black), 0, 1)
        w_hat         = 4*p_i*(1-p_i)                      # mid-tone hat (0 at ends)
        clean_i       = load_clean_fraction(...)            # frame_average.py's own
                                                              # per-photosite record, in
                                                              # [0,1]; 1.0 (i.e. no-op)
                                                              # ONLY when no mask exists
                                                              # for this bracket, logged
        w_valid       = w_hat * clean_i
        w_valid       = 0  where value_i/white <= black    # at/below black floor
        w_i           = w_valid * t_i                       # favour longer valid exposures
        E             = sum_i w_i E_i / sum_i w_i

    sat_frac is accepted but no longer used -- see load_clean_fraction and
    this module's own docstring for why a threshold on the AVERAGED value
    cannot see a raw sample clipped in one frame out of eight, and why this
    is now a per-pixel fraction rather than a binary any-clipped test: that
    binary form would discard the very case (a photosite genuinely clipped
    in a minority of its raw samples) this replacement exists to keep.

    Pixels with zero total weight (fully clipped or black in every frame) fall
    back: saturated-everywhere -> estimate from the SHORTEST exposure (least clipped);
    black-everywhere -> 0; any other zero-weight pixel -> the per-pixel estimate
    from the frame nearest mid-tone. All three are counted in the provenance.
    """
    first = load_frame(exposures[0]["path"])
    H, W, C = first.shape
    in_dtype = first.dtype
    if white_level is not None:
        wl = float(white_level)
    else:
        # White level derives from the sensor profile's own bit depth,
        # never from container width (PHILOSOPHY.md, verbatim) -- this
        # used to be dtype_max(in_dtype), container width outright, zero
        # reference to the sensor. camera_backend.BIT_DEPTH is read via
        # module-attribute access, not a frozen `from` import, so a check
        # can substitute a different profile before calling merge() and
        # have this fallback actually follow it.
        if camera_backend is None:
            raise RuntimeError(
                "camera_backend.py could not be imported; needed to derive "
                "white_level from the sensor profile when --white-level is "
                "not given")
        container_bits = np.dtype(in_dtype).itemsize * 8
        wl = float(camera_backend.white_level_for_bit_depth(
            camera_backend.BIT_DEPTH, container_bits))
    denom_span = max(1.0 - black, 1e-9)

    acc_num = np.zeros((H, W, C), dtype=np.float64)
    acc_den = np.zeros((H, W, C), dtype=np.float64)
    best_dist = np.full((H, W, C), np.inf, dtype=np.float64)
    best_E = np.zeros((H, W, C), dtype=np.float64)
    E_short = None
    sat_all = np.ones((H, W, C), dtype=bool)
    blk_all = np.ones((H, W, C), dtype=bool)

    records = []
    for idx, ex in enumerate(exposures):
        a = load_frame(ex["path"])
        if a.shape != (H, W, C):
            sys.exit(f"{ex['path'].name} shape {a.shape} != {(H, W, C)}; all "
                     f"brackets must share geometry.")
        vn = a.astype(np.float64) / wl
        t = ex["t"]

        signal = vn - black
        E_i = signal / t
        p = np.clip((vn - black) / denom_span, 0.0, 1.0)
        w_valid = 4.0 * p * (1.0 - p)

        # Saturation exclusion: per-pixel clean-sample FRACTION from
        # frame_average.py's own mask-derived record, not a binary
        # threshold on this exposure's own averaged value. One clipped
        # raw sample out of eight (clean_fraction=0.875) reduces this
        # exposure's weight there, it does not zero it -- sat_frac's old
        # `vn >= sat_frac` hard cutoff is gone from this computation
        # entirely, replaced, not supplemented (the parameter itself is
        # still accepted for CLI/signature compatibility this sequence;
        # it is no longer read below -- its removal is sequence 2's own
        # three-phase landing).
        clean_fraction, mask_used, mask_note = load_clean_fraction(ex["path"], (H, W, C))
        if mask_used:
            w_valid = w_valid * clean_fraction
            fully_clipped = clean_fraction <= 0.0
            n_partial = int(((clean_fraction > 0.0) & (clean_fraction < 1.0)).sum())
            n_full = int(fully_clipped.sum())
            clipped_report = f"{n_full} fully-clipped px, {n_partial} partially-clipped px (mask)"
        else:
            # No mask for this bracket: merge WITHOUT exclusion -- the
            # mid-tone hat function alone governs weight here, same as
            # every exposure's weight worked before this sequence
            # existed. Never silently treated as "no saturation" (that
            # would misrepresent absence-of-data as a measurement) and
            # never falling back to the old sat_frac threshold test
            # either (that mechanism is being replaced, not kept as a
            # shadow path) -- logged so the output is traceably
            # unexcluded, not indistinguishable from a clean merge.
            fully_clipped = vn >= 1.0   # the only signal available with no mask
            n_full = int(fully_clipped.sum())
            clipped_report = f"NO SATURATION MASK -- merged WITHOUT exclusion ({mask_note})"

        belowblk = vn <= black
        w_valid = np.where(belowblk, 0.0, w_valid)
        w = w_valid * t

        acc_num += w * E_i
        acc_den += w

        dist = np.abs(p - 0.5)
        better = dist < best_dist
        best_dist = np.where(better, dist, best_dist)
        best_E = np.where(better, E_i, best_E)
        if idx == 0:
            E_short = E_i                       # shortest exposure (sorted)

        sat_all &= fully_clipped
        blk_all &= belowblk

        rec = {"name": ex["path"].name, "exposure_s": t, "t_source": ex["t_source"],
               "capture": try_read_embedded_capture_meta(ex["path"]),
               "exclusion": {"mask_used": mask_used, "note": mask_note,
                             "fully_clipped_px": n_full}}
        if hash_inputs:
            rec["sha256"] = sha256_file(ex["path"])
        records.append(rec)
        print(f"  [{idx}] {ex['path'].name:32s} t={t:g}s  {clipped_report}")

    good = acc_den > 0
    E = np.where(good, acc_num / np.where(good, acc_den, 1.0), 0.0)
    # zero-weight fallbacks, in priority order
    E = np.where(~good & sat_all, E_short, E)
    E = np.where(~good & ~sat_all & ~blk_all, best_E, E)
    # blk_all stays 0

    E = np.clip(E, 0.0, None)                    # irradiance is non-negative

    if C == 1:
        geom_channel_layout = channel_layout        # "mosaic" / "mono" / None
        geom_cfa_pattern = cfa_pattern if geom_channel_layout == "mosaic" else None
    else:
        geom_channel_layout = "rgb"
        geom_cfa_pattern = None

    info = {
        "geometry": {"width": W, "height": H, "channels": C,
                     "input_bits": (8 if in_dtype == np.uint8
                                    else 16 if in_dtype == np.uint16 else "float"),
                     "channel_layout": geom_channel_layout,
                     "cfa_pattern": geom_cfa_pattern},
        "white_level": wl,
        "black": black,
        "sat_frac": sat_frac,
        "sat_frac_note": ("accepted for CLI/signature compatibility only -- no "
                          "longer applied to any weighting decision as of this "
                          "sequence; saturation exclusion now comes from the "
                          "per-exposure mask-derived clean_fraction recorded "
                          "under each exposure's own 'exclusion' record. "
                          "Removal of this parameter itself is a separate, "
                          "later sequence."),
        "n_exposures": len(exposures),
        "saturated_in_all_px": int(sat_all.sum()),
        "black_in_all_px": int(blk_all.sum()),
        "zero_weight_mid_fallback_px": int((~good & ~sat_all & ~blk_all).sum()),
        "exposures": records,
        "exposure_ratios_vs_shortest": [round(ex["t"] / exposures[0]["t"], 4)
                                        for ex in exposures],
    }
    return E, info


def _assert_single_description_tag(path):
    """Confirm exactly one ImageDescription (TIFF tag 270) exists in the file
    just written. Two tags in one IFD is invalid TIFF with reader-dependent
    resolution — this is a structural check on the file itself, not a trust
    that metadata=None above was enough."""
    with tifffile.TiffFile(str(path)) as tf:
        tags270 = [t for t in tf.pages[0].tags if t.code == 270]
    if len(tags270) != 1:
        sys.exit(f"INTERNAL: expected exactly one ImageDescription (tag 270) "
                 f"in {path}, found {len(tags270)} — provenance would be "
                 f"ambiguous to any reader.")
    return len(tags270)


def render_check():
    """This file's first self-check (none existed before -- confirmed,
    `grep` for `render_check`/`if __name__` found only main()'s own
    dispatch). Scoped narrowly to the one thing this session's own work
    touches: white_level derives from the sensor profile's own bit
    depth, in POSITIVE form (a substitution, not an absence check --
    absence passes on dead code), reached through merge()'s own real
    call path, not a direct read of camera_backend.BIT_DEPTH or
    white_level_for_bit_depth in isolation."""
    import shutil
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="zynergy_hdr_merge_render_check_"))
    try:
        h, w = 4, 4
        frame0 = (np.arange(h * w, dtype=np.uint16).reshape(h, w) + 1000)
        frame1 = frame0 + 500
        p0, p1 = tmp_dir / "e0.tif", tmp_dir / "e1.tif"
        tifffile.imwrite(str(p0), frame0)
        tifffile.imwrite(str(p1), frame1)
        exposures = [{"path": p0, "t": 1.0, "t_source": "explicit"},
                    {"path": p1, "t": 2.0, "t_source": "explicit"}]

        # Real profile: the None-fallback, reached through merge()'s own
        # real call path, must equal what white_level_for_bit_depth
        # derives for the REAL camera_backend.BIT_DEPTH -- and that must
        # still be exactly 65520, the relocation-not-correction claim,
        # checked, not just stated.
        assert camera_backend is not None, "camera_backend.py must be importable"
        _, info_real = merge(exposures, None, 0.0, 0.95, 99.5, False)
        expected_real = camera_backend.white_level_for_bit_depth(camera_backend.BIT_DEPTH)
        assert info_real["white_level"] == expected_real, (
            "merge()'s None-fallback white_level {!r} does not match "
            "white_level_for_bit_depth(BIT_DEPTH={!r}) = {!r}".format(
                info_real["white_level"], camera_backend.BIT_DEPTH, expected_real))
        assert info_real["white_level"] == 65520.0, (
            "the real profile's derived white level must still be exactly "
            "65520.0 today -- this is a relocation, not a correction; "
            "got {!r}".format(info_real["white_level"]))

        # Substitute a synthetic profile bit depth BEFORE calling merge()
        # -- module-attribute reassignment, not a frozen `from` import,
        # so this reaches merge()'s own fresh attribute lookup at call
        # time. Unlike Picamera2Camera's own mode-crop table (Stage 3's
        # own finding), nothing in this chain caches BIT_DEPTH at any
        # "construction" moment -- there is no camera object here at
        # all -- so there is no equivalent too-late hazard to guard
        # against for this consumer; stated rather than assumed away.
        real_bit_depth = camera_backend.BIT_DEPTH
        try:
            camera_backend.BIT_DEPTH = 10   # a real IMX477 mode option, genuinely different
            _, info_synth = merge(exposures, None, 0.0, 0.95, 99.5, False)
            expected_synth = camera_backend.white_level_for_bit_depth(10)
            assert expected_synth != expected_real, \
                "test setup: the substituted bit depth must derive a DIFFERENT white level"
            assert info_synth["white_level"] == expected_synth, (
                "merge()'s None-fallback white_level did not follow the "
                "substituted BIT_DEPTH=10, reached through merge()'s own "
                "real call path -- got {!r}, expected {!r} (still the "
                "real profile's own BIT_DEPTH={!r} value {!r} would be a "
                "failure to substitute)".format(
                    info_synth["white_level"], expected_synth,
                    real_bit_depth, expected_real))
        finally:
            camera_backend.BIT_DEPTH = real_bit_depth

        # An explicit --white-level must always win outright, profile or not.
        _, info_explicit = merge(exposures, 62100.0, 0.0, 0.95, 99.5, False)
        assert info_explicit["white_level"] == 62100.0

        # -------------------------------------------------------------
        # Mask consumption: per-pixel clean-fraction weighting, not a
        # binary any-clipped test -- the actual design requirement this
        # sequence exists to satisfy. wl=1000.0 explicit, chosen for
        # hand-checkable arithmetic, not the real sensor's own value.
        # -------------------------------------------------------------
        def write_master(path, native_row, n_frames):
            arr = np.array([native_row], dtype=np.uint16)   # shape (1, W)
            prov = {"software": "frame_average.py", "version": "2.3",
                    "precision": {"frames_averaged": n_frames}}
            tifffile.imwrite(str(path), arr, description=json.dumps(prov))

        def write_excluded_count(master_path, excluded_row):
            p = Path(master_path).with_name(Path(master_path).stem + "_excluded_count.tif")
            tifffile.imwrite(str(p), np.array([excluded_row], dtype=np.uint16))

        wl = 1000.0
        # position 0: clean (0/8 excluded); position 1: 1-of-8 clipped
        # (clean_fraction=0.875 -- the 5,407-pixel case's own shape);
        # position 2: 8-of-8 clipped (clean_fraction=0.0); position 3:
        # clean, a second unaffected position, not adjacent to the others.
        e0_dir = tmp_dir / "mask_case_a"
        e0_dir.mkdir()
        p_short = e0_dir / "short.tif"
        p_long = e0_dir / "long.tif"
        write_master(p_short, [400, 400, 400, 400], n_frames=8)
        write_excluded_count(p_short, [0, 0, 0, 0])          # fully clean
        write_master(p_long, [500, 900, 999, 300], n_frames=8)
        write_excluded_count(p_long, [0, 1, 8, 0])           # 0/1/8/0 of 8 excluded

        exposures_a = [{"path": p_short, "t": 1.0, "t_source": "explicit"},
                      {"path": p_long, "t": 2.0, "t_source": "explicit"}]
        E_a, info_a = merge(exposures_a, wl, 0.0, 0.95, 99.5, False)

        assert info_a["exposures"][0]["exclusion"]["mask_used"] is True
        assert info_a["exposures"][1]["exclusion"]["mask_used"] is True
        # Stop condition (c): the fully-clipped position (8/8) must get
        # ZERO weight from the long exposure -- confirmed structurally
        # (fully_clipped_px counts it) AND numerically (E there collapses
        # to EXACTLY the short exposure's own E_i, proving the long
        # exposure contributed nothing).
        assert info_a["exposures"][1]["exclusion"]["fully_clipped_px"] == 1, (
            "expected exactly 1 fully-clipped photosite (position 2), got {}"
            .format(info_a["exposures"][1]["exclusion"]["fully_clipped_px"]))
        E_i_short_at_all = (400.0 / wl - 0.0) / 1.0    # short exposure alone, any position
        assert E_a[0, 2, 0] == E_i_short_at_all, (
            "fully-clipped position must collapse to exactly the short "
            "exposure's own estimate (long exposure contributed zero "
            "weight there) -- got {!r}, expected {!r}"
            .format(E_a[0, 2, 0], E_i_short_at_all))

        # Stop condition (b): the partially-clipped position (1/8) must
        # get a REDUCED but NONZERO weight from the long exposure -- not
        # discarded. Verified by hand-computing the exact merge formula
        # for BOTH the true (0.875 clean_fraction) case and the WRONG
        # (binary-excluded, clean_fraction forced to 0) case, and
        # confirming the real output matches the former, not the latter.
        def hand_merge_position(native_short, native_long, t_short, t_long, clean_frac_long):
            vn_s, vn_l = native_short / wl, native_long / wl
            p_s, p_l = np.clip(vn_s, 0, 1), np.clip(vn_l, 0, 1)
            w_s = 4 * p_s * (1 - p_s) * 1.0 * t_short        # short exposure always clean here
            w_l = 4 * p_l * (1 - p_l) * clean_frac_long * t_long
            Ei_s, Ei_l = vn_s / t_short, vn_l / t_long
            return (w_s * Ei_s + w_l * Ei_l) / (w_s + w_l)

        expected_partial = hand_merge_position(400, 900, 1.0, 2.0, 0.875)
        expected_if_wrongly_binary = hand_merge_position(400, 900, 1.0, 2.0, 0.0)
        assert expected_partial != expected_if_wrongly_binary, (
            "test setup: the two hand-computed expectations must differ, "
            "or this check cannot distinguish correct from wrong behaviour")
        assert E_a[0, 1, 0] == expected_partial, (
            "partially-clipped position (1 of 8 excluded, clean_fraction="
            "0.875) must be weighted by that fraction, not discarded -- "
            "got {!r}, expected {!r} (the binary-exclusion wrong answer "
            "would have been {!r})".format(
                E_a[0, 1, 0], expected_partial, expected_if_wrongly_binary))

        # Fully-clean position, unaffected -- sanity check the machinery
        # isn't perturbing positions the mask says nothing about.
        expected_clean = hand_merge_position(400, 500, 1.0, 2.0, 1.0)
        assert E_a[0, 0, 0] == expected_clean

        # -------------------------------------------------------------
        # Missing mask: a bracket with NO excluded-count sibling at all
        # merges WITHOUT exclusion -- never silently treated as clean,
        # never falling back to the old sat_frac threshold. Both the
        # returned structure (mask_used=False) and the printed log line
        # are checked -- structure because that's what a caller can act
        # on, the log because the instruction asks for it explicitly.
        # -------------------------------------------------------------
        import io
        import contextlib

        e0_dir_b = tmp_dir / "mask_case_b_no_mask"
        e0_dir_b.mkdir()
        p_short_b = e0_dir_b / "short.tif"
        p_long_b = e0_dir_b / "long.tif"
        write_master(p_short_b, [400, 400, 400, 400], n_frames=8)
        write_master(p_long_b, [500, 900, 999, 300], n_frames=8)
        # deliberately no write_excluded_count() calls -- no sibling exists

        exposures_b = [{"path": p_short_b, "t": 1.0, "t_source": "explicit"},
                      {"path": p_long_b, "t": 2.0, "t_source": "explicit"}]
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            E_b, info_b = merge(exposures_b, wl, 0.0, 0.95, 99.5, False)
        log_text = captured.getvalue()

        assert info_b["exposures"][0]["exclusion"]["mask_used"] is False
        assert info_b["exposures"][1]["exclusion"]["mask_used"] is False
        assert "no excluded-count sibling found" in info_b["exposures"][0]["exclusion"]["note"]
        assert log_text.count("NO SATURATION MASK") == 2, (
            "expected the missing-mask log line once per exposure (2 "
            "exposures, neither has a mask) -- got {} occurrence(s) in:\n{}"
            .format(log_text.count("NO SATURATION MASK"), log_text))

        # Numerically: with no mask, weighting must equal the pure
        # mid-tone hat function alone -- the SAME formula every exposure
        # used before this sequence existed, at every position including
        # the one that would have been down-weighted had a mask existed.
        expected_no_mask_pos1 = hand_merge_position(400, 900, 1.0, 2.0, 1.0)
        assert E_b[0, 1, 0] == expected_no_mask_pos1, (
            "with no mask, position 1 must use hat-function-only weighting "
            "(clean_fraction=1.0, i.e. no exclusion applied at all) -- got "
            "{!r}, expected {!r}".format(E_b[0, 1, 0], expected_no_mask_pos1))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("white-level-follows-profile-bit-depth check PASS: merge()'s own "
          "None-fallback derives white_level from camera_backend.BIT_DEPTH, "
          "reached through merge()'s real call path (not a direct attribute "
          "read); reproduces today's real value (65520.0) exactly; follows "
          "a substituted BIT_DEPTH=10 to a genuinely different value when "
          "the profile is swapped before this consumer runs; an explicit "
          "--white-level still overrides either way")
    print("mask-consumption check PASS: a 1-of-8-clipped photosite is "
          "weighted by its own clean_fraction (0.875), not discarded; an "
          "8-of-8-clipped photosite collapses exactly to the other "
          "exposure's own estimate (zero weight, confirmed numerically, "
          "not just structurally); a bracket with no excluded-count "
          "sibling merges without exclusion, logged once per exposure "
          "(not silently clean, not falling back to the old sat_frac "
          "threshold), verified against the pure hat-function formula")


def main():
    if "--render-check" in sys.argv:
        render_check()
        return
    ap = argparse.ArgumentParser(
        description="Merge a bracketed exposure series into a linear HDR image.")
    ap.add_argument("-e", "--exposure", nargs=2, action="append", required=True,
                    metavar=("FRAME", "SECONDS"), dest="exposures",
                    help="one exposure master and its exposure time in seconds "
                         "(repeat per bracket). SECONDS may be 'auto' to read "
                         "an embedded 'exposure_s' provenance field.")
    ap.add_argument("-o", "--output", default="hdr_linear.tif",
                    help="output TIFF path (linear, scene-referred)")
    ap.add_argument("--white-level", type=float, default=None, metavar="V",
                    help="full-scale value in native input units (default: dtype "
                         "max). SET THIS for sub-container data, e.g. 4095 for "
                         "12-bit RAW right-justified in a 16-bit file.")
    ap.add_argument("--black", type=float, default=0.0, metavar="B",
                    help="black level as a fraction of white level to subtract "
                         "before merging (default 0.0; masters from a dark-"
                         "corrected average are already near zero).")
    ap.add_argument("--sat", type=float, default=0.95, metavar="F",
                    help="DEPRECATED, no longer applied to any weighting "
                         "decision: saturation exclusion now comes from "
                         "frame_average.py's own per-photosite clean-sample "
                         "record (its excluded-count sibling beside each "
                         "master), read automatically, not this threshold. "
                         "Still accepted and recorded for CLI compatibility; "
                         "removal is a separate, later piece of work.")
    ap.add_argument("--norm-percentile", type=float, default=99.5, metavar="P",
                    help="percentile of the merged irradiance mapped to 1.0 in "
                         "the output (default 99.5; robust to a few hot pixels).")
    ap.add_argument("--out-bits", type=int, choices=(16, 32), default=32,
                    help="32 = linear float (default, lossless range); 16 = "
                         "normalised uint16 (clips above the norm point).")
    ap.add_argument("--hash", action="store_true",
                    help="record a sha256 of every input master in the "
                         "provenance block.")
    ap.add_argument("--no-compress", action="store_true",
                    help="write uncompressed instead of deflate.")
    ap.add_argument("--white-level-source", default=None, metavar="TEXT",
                    help="how --white-level was determined (e.g. 'empirical: "
                         "frame5/frame4 median-ratio break'). Recorded "
                         "verbatim in provenance; null if omitted. white_level "
                         "is only valid for the analogue gain a bracket was "
                         "actually shot at — see --analogue-gain.")
    ap.add_argument("--analogue-gain", type=float, default=None, metavar="G",
                    help="analogue gain this bracket was shot at, if known. "
                         "Not auto-recoverable today (see this module's "
                         "docstring); recorded in provenance, null if omitted.")
    ap.add_argument("--black-note", default=None, metavar="TEXT",
                    help="where/how any pedestal (black-level) subtraction "
                         "was handled upstream of this tool. black=0.0 alone "
                         "can't distinguish 'verified no pedestal' from "
                         "'never handled' — this note is how a reader tells "
                         "them apart. Recorded verbatim; null if omitted.")
    ap.add_argument("--channel-layout", choices=("mosaic", "mono"), default=None,
                    help="for a 1-channel plane: whether it's a raw Bayer "
                         "mosaic or a true mono/already-extracted plane. The "
                         "file's own TIFF tags can't tell a reader this "
                         "(MINISBLACK with no CFA tag looks the same either "
                         "way) — recorded in provenance, null if omitted.")
    ap.add_argument("--cfa-pattern", default=None, metavar="PATTERN",
                    help="CFA pattern name (e.g. BGGR), only meaningful with "
                         "--channel-layout mosaic. Recorded verbatim.")
    args = ap.parse_args()

    if not (0.0 <= args.black < 1.0):
        sys.exit("--black must be in [0, 1).")
    if not (0.0 < args.sat <= 1.0):
        sys.exit("--sat must be in (0, 1].")

    exposures = parse_exposures(args.exposures)
    print(f"Merging {len(exposures)} exposures "
          f"({exposures[0]['t']:g}s .. {exposures[-1]['t']:g}s):")

    E, info = merge(exposures, args.white_level, args.black, args.sat,
                    args.norm_percentile, args.hash,
                    channel_layout=args.channel_layout, cfa_pattern=args.cfa_pattern)

    # ---- normalise: map a high percentile to 1.0 so the divisor is recoverable
    pos = E[E > 0]
    norm_div = float(np.percentile(pos, args.norm_percentile)) if pos.size else 1.0
    norm_div = max(norm_div, 1e-12)
    E_norm = E / norm_div

    W, H, C = info["geometry"]["width"], info["geometry"]["height"], info["geometry"]["channels"]
    print(f"\nFrame geometry: {W}x{H}, {C} channel(s).")
    if info["saturated_in_all_px"]:
        print(f"  {info['saturated_in_all_px']} px clipped in EVERY bracket "
              f"(highlight unrecoverable — shorten the shortest exposure).")
    if info["black_in_all_px"]:
        print(f"  {info['black_in_all_px']} px at/below black in every bracket "
              f"(set to 0).")

    prov = {
        "software": "hdr_merge.py",
        "version": __version__,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "merge": ("E = sum_i w_i*(v_i/white - black)/t_i / sum_i w_i ; "
                  "w_i = 4p(1-p)*clean_fraction_i*t_i with p the black-to-white "
                  "position and clean_fraction_i this exposure's own per-photosite "
                  "clean-sample fraction from frame_average.py's excluded-count "
                  "record (1.0 -- no exclusion -- where no such record exists "
                  "for this bracket, logged per exposure); zero weight where "
                  "v_i/white <= black"),
        "domain": "linear, scene-referred (NOT tone-mapped)",
        "white_level": info["white_level"],
        "white_level_source": args.white_level_source,
        "black": info["black"],
        "black_note": args.black_note,
        "sat_frac": info["sat_frac"],
        "sat_frac_note": info["sat_frac_note"],
        "analogue_gain": args.analogue_gain,
        "white_level_gain_dependency": (
            "white_level is only valid for the analogue gain this bracket "
            "was actually shot at. analogue_gain is null (not supplied via "
            "--analogue-gain and not recoverable from the input masters) — "
            "treat white_level as valid for this bracket only and "
            "re-measure before reuse on a different capture."
            if args.analogue_gain is None else None
        ),
        "exposures": info["exposures"],
        "exposure_ratios_vs_shortest": info["exposure_ratios_vs_shortest"],
        "geometry": info["geometry"],
        "fallback_counts": {
            "saturated_in_all_px": info["saturated_in_all_px"],
            "black_in_all_px": info["black_in_all_px"],
            "zero_weight_mid_fallback_px": info["zero_weight_mid_fallback_px"],
        },
        "normalisation": {
            "percentile_mapped_to_one": args.norm_percentile,
            "divisor": norm_div,
            "recover_absolute": "E_absolute = pixel_value * divisor",
        },
    }

    comp = None if args.no_compress else "deflate"
    if args.out_bits == 32:
        out = E_norm.astype(np.float32)
        out_dtype = "float32"
        clipped_hi = int(np.count_nonzero(out > 1.0))   # informational only; not clipped
        prov["output"] = {"dtype": "float32", "clipped": "none (range preserved)",
                          "above_norm_point_px": clipped_hi}
    else:
        clipped_hi = int(np.count_nonzero(E_norm > 1.0))
        out = np.clip(np.rint(E_norm * 65535.0), 0, 65535).astype(np.uint16)
        out_dtype = "uint16"
        prov["output"] = {"dtype": "uint16",
                          "clipped_above_norm_point_px": clipped_hi,
                          "clipped_above_norm_point_pct":
                              round(100 * clipped_hi / E_norm.size, 4)}
        if clipped_hi:
            print(f"  16-bit output clips {clipped_hi} px above the norm point "
                  f"({100*clipped_hi/E_norm.size:.4f}%) — use --out-bits 32 to keep them.")

    if C == 1:
        out = out[:, :, 0]
        photometric = "minisblack"
    else:
        photometric = "rgb"

    out_path = str(Path(args.output).resolve())
    prov["output"].update({"path": out_path, "compression":
                           "deflate" if comp else "none",
                           "value_range": [float(out.min()), float(out.max())]})
    description = json.dumps(prov, separators=(",", ":"))
    # metadata=None suppresses tifffile's own default {"shape": [...]} JSON,
    # which otherwise lands in a SECOND ImageDescription (tag 270) alongside
    # `description` above — two tags in one IFD is invalid TIFF, and which
    # one a reader sees is undefined, so the provenance block above could be
    # silently the one that gets dropped.
    tifffile.imwrite(args.output, out, photometric=photometric,
                     compression=comp, description=description, metadata=None)
    n_desc_tags = _assert_single_description_tag(args.output)

    print(f"\nWrote {args.output}")
    print(f"  output: {out.shape} {out_dtype}, {'deflate' if comp else 'uncompressed'}, linear")
    print(f"  norm divisor (p{args.norm_percentile:g} -> 1.0): {norm_div:.6g}  "
          f"[absolute E = pixel * {norm_div:.6g}]")
    print(f"  dynamic range spanned by brackets: "
          f"{info['exposure_ratios_vs_shortest'][-1]:g}x "
          f"({np.log2(info['exposure_ratios_vs_shortest'][-1]):.1f} stops)")
    print(f"  provenance JSON embedded in ImageDescription ({len(description)} bytes); "
          f"{n_desc_tags} ImageDescription tag in file (confirmed, not assumed)")


if __name__ == "__main__":
    main()
