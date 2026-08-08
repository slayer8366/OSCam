#!/usr/bin/env python3
"""
frame_average.py — average a burst of static-field frames into a clean 16-bit
TIFF, with optional flat-field and dark-frame correction.

Averaging N frames combines their independent read noise so SNR improves by
about sqrt(N); done in floating point, this recovers tonal detail below the
quantisation step of an 8-bit source. Flat-field correction then removes the
fixed structure that averaging cannot touch — vignetting, uneven illumination,
dust shadows on the optics, sensor gain variation, and (via the dark frame)
bias offset and hot pixels.

Works with any camera that can save TIFF frames (uint8 or uint16). All bursts
must share the same geometry AND the same dtype.

Usage:
    # plain averaging
    python frame_average.py sci_*.tif -o master.tif

    # averaging + full correction
    python frame_average.py sci_*.tif --flat flat_*.tif --dark dark_*.tif -o master.tif

    # dark subtraction only (no flat available)
    python frame_average.py sci_*.tif --dark dark_*.tif -o master.tif

    # correct in linear light (recommended if frames are gamma/ISP-encoded),
    # embed a per-input sha256 provenance record, write linear output:
    python frame_average.py sci_*.tif --flat flat_*.tif --dark dark_*.tif \
        --gamma 2.2 --linear-out --hash -o master.tif

Capture notes:
    - Shoot the flat and dark bursts in the SAME optical configuration as the
      science burst (same objective/lens, same illumination, same exposure and
      gain). A flat is only valid for the configuration it was taken under.
    - Flat: an evenly-lit, specimen-free field (e.g. a blank slide, slightly
      defocused), exposed well below clipping (~60-70% of range works well).
    - Dark: identical exposure/gain with all light to the sensor blocked.
    - Average several flats and darks, not single frames — they sit in the
      denominator of the correction, so their own noise propagates into it.

Precision & provenance notes:
    - The "effective bits" figure is a dithering heuristic: averaging only
      recovers sub-LSB structure when the per-frame noise is at least ~1 LSB,
      so that quantisation is dithered. If a source is quieter than that, the
      extra bits are empty. Treat the number as an upper bound, not a promise.
    - Flat-field division ( (sci-dark)/(flat-dark) ) is physically valid only
      in LINEAR light. If the frames are gamma- or ISP-encoded (e.g. 8-bit
      YUY2 straight off a UVC bridge), the division is biased by the transfer
      curve. Use --gamma G to linearise before correction and (optionally)
      --linear-out to keep the output linear. The chosen domain is recorded in
      the output's provenance block so a stranger can see exactly what was done.
    - Every output carries a JSON provenance record in its TIFF ImageDescription
      tag: inputs (optionally hashed), geometry, rejection stats, gain, clip
      counts, domain, software version, and timestamp.
    - --sidecar-dir carries analogue_gain/sensor_mode/capture_time_utc from the
      science burst's own .meta.json capture sidecars (provenance.py's naming:
      <sidecar_dir>/<raw_frame_stem>.meta.json) into this tool's own output
      provenance, under the exact key names hdr_merge.py already reads back
      out of a master (try_read_embedded_capture_meta()). This tool does not
      import provenance.py -- it only replicates its known sidecar naming, to
      stay usable with any camera that writes TIFF frames. sensor_mode reads
      back null today: it is not a field libcamera's per-frame metadata ever
      carries, on either the real or fake backend -- that's a camera_backend.py
      gap, not something reading harder here can fix. capture_time_utc reads
      back null too: the sidecar's SensorTimestamp is a monotonic hardware
      clock with no recorded epoch, not wall-clock UTC, and mapping it to a
      field named "_utc" would be a fabricated value, not a real one.

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

__version__ = "2.3"
IMG_EXT = {".tif", ".tiff"}


def collect_inputs(inputs):
    files = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files += sorted(q for q in p.iterdir() if q.suffix.lower() in IMG_EXT)
        elif p.is_file():
            files.append(p)
        else:
            # shell didn't expand it (e.g. Windows cmd) — glob it ourselves
            matches = sorted(Path().glob(item))
            hits = [q for q in matches if q.suffix.lower() in IMG_EXT]
            if hits:
                files += hits
            else:
                sys.exit(f"Input not found: {item}")
    if not files:
        sys.exit("No input TIFF frames found.")
    return files


def load_frame(path):
    a = tifffile.imread(str(path))
    if a.ndim == 2:
        a = a[:, :, None]  # treat grayscale as 1-channel
    return a


def dtype_max(dtype):
    if dtype == np.uint8:
        return 255.0
    if dtype == np.uint16:
        return 65535.0
    sys.exit(f"Unsupported input dtype {dtype}; expected uint8 or uint16.")


# ---------------------------------------------------------------------------
# Saturation mask (write side only -- nothing in this tree reads one yet).
# A record about a raw frame's own pixels, never a transformation of them:
# average_burst's own accumulator is untouched by anything below. See
# CHANGELOG.md's "Record intent: saturation mask, write side only" for the
# full design reasoning (why one file per raw frame, why packed bits, why
# this lives in the provenance tree and survives Keep RAW Images off).
# ---------------------------------------------------------------------------

def clipped_mask_for_frame(arr):
    """(clipped, white_level): clipped is a boolean array the same shape as
    `arr`, True where that raw sample hit or exceeded the sensor profile's
    own current white level -- the hard ceiling for its current bit depth,
    NOT hdr_merge.py's own sat_frac (a different, later-stage, fractional
    weight-zeroing threshold at merge time, untouched by this).

    white_level is read via camera_backend.white_level_for_bit_depth
    (camera_backend.BIT_DEPTH) -- module-attribute access on BOTH names,
    not a frozen `from` import, so a caller that reassigns
    camera_backend.BIT_DEPTH before calling this reaches the substitution.
    No second derivation lives here, and nothing is hardcoded: this is the
    one place this file asks the profile, matching camera_backend.py's own
    white_level_for_bit_depth built at 739d2e1."""
    if camera_backend is None:
        raise RuntimeError(
            "camera_backend.py could not be imported; needed to derive the "
            "sensor profile's own white level")
    white_level = camera_backend.white_level_for_bit_depth(camera_backend.BIT_DEPTH)
    return arr >= white_level, white_level


def write_clipped_mask(arr, mask_path):
    """One packed-bit mask file for a single raw frame, self-describing:
    the packed bits and the array's own original shape ride in the same
    .npz, so unpacking never depends on an external convention or a
    caller already knowing the frame's own geometry. One bit per
    photosite (np.packbits over the raveled clipped-boolean array) --
    8x smaller than a plain boolean/uint8 array, material at "survives
    longer than the raws, forever" scale.

    Returns the white_level actually used, so a caller reporting a
    clipped fraction does not need a second, separately-derived call
    that could theoretically disagree with what got packed."""
    clipped, white_level = clipped_mask_for_frame(arr)
    packed = np.packbits(clipped.ravel())
    shape = np.array(clipped.shape, dtype=np.int64)
    Path(mask_path).parent.mkdir(parents=True, exist_ok=True)
    np.savez(str(mask_path), packed=packed, shape=shape)
    return white_level


def load_clipped_mask(mask_path):
    """The exact inverse of write_clipped_mask. Used by this module's own
    render_check; available to any future consumer -- none exists yet,
    this session is write-side only."""
    with np.load(str(mask_path)) as z:
        shape = tuple(int(v) for v in z["shape"])
        packed = z["packed"]
    n = 1
    for d in shape:
        n *= d
    bits = np.unpackbits(packed, count=n)
    return bits.reshape(shape).astype(bool)


def sha256_file(path, _buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_buf), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sidecar_meta(sidecar_dir, frame_path):
    """Best-effort read of a frame's .meta.json capture sidecar, using
    provenance.py's own naming (<sidecar_dir>/<raw_frame_stem>.meta.json) --
    replicated here rather than imported, so this tool stays usable with any
    camera that writes TIFF frames, not just this project's session model.
    Returns the raw dict, or {} if there's no sidecar_dir, no matching file,
    or it isn't readable JSON -- never raises, since not every input frame is
    guaranteed to have one."""
    if sidecar_dir is None:
        return {}
    sidecar = Path(sidecar_dir) / (Path(frame_path).stem + ".meta.json")
    try:
        return json.loads(sidecar.read_text())
    except Exception:
        return {}


def aggregate_capture_field(sidecars, raw_key, caster=lambda v: v):
    """Combine one metadata field across a burst's sidecars into a single
    provenance-ready value.

    Returns (value, note):
      - no sidecar in the burst carries this key at all -> (None, a note
        saying so)
      - every sidecar that carries it agrees -> (that value, None)
      - two or more distinct values -> (None, a note listing every value
        seen, in frame order)

    A frame with no sidecar (an empty dict) simply contributes nothing to
    the vote -- "no sidecar" and "the frame's gain differed" are different
    facts and must not be conflated into the same rejection."""
    values = [caster(sc[raw_key]) for sc in sidecars if raw_key in sc]
    if not values:
        return None, "not present in any input frame's sidecar"
    if len(set(values)) == 1:
        return values[0], None
    return None, "disagreement across frames: {}".format(values)


def capture_meta_for_science(sci_files, sidecar_dir):
    """analogue_gain/sensor_mode/capture_time_utc for the science burst that
    becomes this tool's own output master, plus a note explaining every null
    (never a silent omission). See this module's docstring for exactly why
    sensor_mode and capture_time_utc are null today regardless of how many
    sidecars are found."""
    sidecars = [read_sidecar_meta(sidecar_dir, f) for f in sci_files]
    n_found = sum(1 for sc in sidecars if sc)
    gain, gain_note = aggregate_capture_field(sidecars, "AnalogueGain", float)
    return {
        "analogue_gain": gain,
        "sensor_mode": None,
        "capture_time_utc": None,
        "note": {
            "sidecar_dir": str(sidecar_dir) if sidecar_dir else None,
            "sidecars_found": n_found,
            "sidecars_expected": len(sci_files),
            "analogue_gain": gain_note,
            "sensor_mode": ("sensor mode is not part of the current per-frame "
                            "sidecar format on either camera backend -- "
                            "always null until camera_backend.py records it"),
            "capture_time_utc": ("the sidecar's SensorTimestamp is a monotonic "
                                 "hardware clock with no recorded epoch, not "
                                 "wall-clock UTC -- not converted, to avoid a "
                                 "fabricated value"),
        },
    }


def _checked_load(path, want_shape, want_dtype):
    """Load a frame and fail loudly if geometry or dtype don't match the burst."""
    a = load_frame(path)
    if a.shape != want_shape:
        sys.exit(f"Frame {path.name} shape {a.shape} != {want_shape}; all frames must match.")
    if a.dtype != want_dtype:
        sys.exit(f"Frame {path.name} dtype {a.dtype} != {want_dtype}; "
                 f"mixing dtypes in one burst would misscale. Convert first.")
    return a


def average_burst(files, sigma_clip=None, report_deviation=False, label="frames",
                  gamma=None, mask_dir=None):
    """Average a burst of identically-framed TIFFs into a float image in [0, 1].

    Memory is bounded to a few full-frame buffers regardless of burst length:
    the mean (and, for sigma clipping, the running sum-of-squares) are
    accumulated by streaming the files, never by holding the whole stack. The
    cost is a second read pass when sigma clipping or deviation reporting is on.

    With gamma None each frame is accumulated in its native value domain and
    the mean is divided by dmax once at the end — numerically identical to the
    original full-stack routine. With gamma=G each frame is first linearised as
    (value/dmax)**G so that the downstream flat-field division is physical;
    averaging and rejection then happen in that linear domain.

    Sigma rejection is a single iteration (one mean, one std, one pass of
    rejection — not iterated to convergence), matching the original semantics.
    The std is computed from explicit deviations (a second streaming pass),
    which is numerically stable and avoids the catastrophic cancellation of the
    sum-of-squares shortcut.

    mask_dir (optional, write side only): when given, one packed saturation
    mask is ALSO written per RAW FRAME in this burst, alongside the
    streaming mean below -- read once, in pass 1's own loop, no second pass,
    no second read. Independent of exclusion below: whether or not mask_dir
    is given, every raw sample this burst reads is tested against the same
    per-frame clip detection; mask_dir only controls whether that per-frame
    result is ALSO persisted to disk. Filenames match provenance.py's own
    sidecar convention (`<raw_frame_stem>.satmask.npz` inside `mask_dir`) so
    a mask shares its key with the raw it describes by construction, the
    same way `<raw_frame_stem>.meta.json` already does -- no index or
    mapping table introduced to keep the two in sync.

    Clipped-sample exclusion (unconditional, not gated behind mask_dir):
    per photosite, per channel, any raw sample >= the sensor profile's own
    white level is excluded from `acc` and from that position's own
    divisor -- the average is acc / clean_count there, never acc / n. A
    photosite with zero clean samples (every sample in the burst clipped)
    has nothing to average; it holds white_level itself (in whichever
    domain `mean` is already in -- native, or linearised under --gamma) --
    never 0 (indistinguishable from a legitimately dark photosite) and
    never the container ceiling 65535 (indistinguishable from a real
    bright one). That sentinel is NOT self-disambiguating: a genuine
    photosite whose raw value happened to equal white_level in every
    sample would look identical. The per-photosite excluded-count this
    function also returns is the only thing that tells the two apart --
    excluded_count == n at that position is the actual, unambiguous
    all-clipped signal, and is why that count is returned at full
    resolution rather than only as an aggregate.

    Requires camera_backend to be importable (it resolves the sensor
    profile's own white level -- the same requirement
    clipped_mask_for_frame already has). Refuses (sys.exit) rather than
    silently falling back to summing every sample unconditionally, which
    would reproduce the exact wrong behavior this function used to have,
    under this version's name, with no record that it happened.

    Returns (mean01, info, excluded_count) -- excluded_count is a full
    (H, W, C) int64 array, same shape as mean01, the per-photosite record
    "alongside the master" a caller can persist or inspect directly.
    """
    if camera_backend is None:
        sys.exit(
            "camera_backend.py could not be imported; needed to resolve the "
            "sensor profile's own white level for clipped-sample exclusion. "
            "Refusing rather than falling back to the old unconditional "
            "sum, which would silently reproduce the prior wrong behavior "
            "under this version's name.")

    first = load_frame(files[0])
    H, W, C = first.shape
    in_dtype = first.dtype
    dmax = dtype_max(in_dtype)
    in_bits = 8 if in_dtype == np.uint8 else 16
    n = len(files)

    if gamma is None:
        # native domain: identical arithmetic to the original routine
        def to_work(a):
            return a.astype(np.float64)
        final_scale = 1.0 / dmax
    else:
        def to_work(a):
            return (a.astype(np.float64) / dmax) ** gamma
        final_scale = 1.0

    # ---- pass 1: streaming mean (memory bounded to a few full frames),
    # now excluding clipped raw samples per photosite as it streams.
    mask_clipped_fraction = {}
    mask_white_level = None
    white_level = None
    acc = np.zeros((H, W, C), dtype=np.float64)
    excluded_count = np.zeros((H, W, C), dtype=np.int64)
    for f in files:
        raw = _checked_load(f, (H, W, C), in_dtype)
        clipped, white_level = clipped_mask_for_frame(raw)
        acc += np.where(clipped, 0.0, to_work(raw))
        excluded_count += clipped.astype(np.int64)
        if mask_dir is not None:
            mask_path = Path(mask_dir) / (Path(f).stem + ".satmask.npz")
            mask_white_level = write_clipped_mask(raw, mask_path)
            mask_clipped_fraction[Path(f).stem] = round(
                float(np.count_nonzero(raw >= mask_white_level)) / raw.size, 6)

    clean_count = n - excluded_count
    all_clipped = clean_count == 0
    safe_clean = np.maximum(clean_count, 1)
    sentinel = float(white_level) if gamma is None else (float(white_level) / dmax) ** gamma
    mean = np.where(all_clipped, sentinel, acc / safe_clean)

    partial_mask = (excluded_count > 0) & ~all_clipped
    info = {"n": n, "geometry": (W, H, C), "bits": in_bits,
            "domain": "linear" if gamma is None else f"linearised(gamma={gamma})"}
    info["exclusion"] = {
        "white_level": float(white_level),
        "sentinel_value": sentinel,
        "partially_clipped_photosites": int(partial_mask.sum()),
        "fully_clipped_photosites": int(all_clipped.sum()),
        "total_excluded_samples": int(excluded_count.sum()),
        "total_samples": int(n * H * W * C),
    }
    print(f"clipped-sample exclusion ({label}): white_level={white_level:g}, "
          f"{info['exclusion']['partially_clipped_photosites']} partially-clipped "
          f"photosite(s), {info['exclusion']['fully_clipped_photosites']} "
          f"fully-clipped photosite(s) (sentinel written there), "
          f"{info['exclusion']['total_excluded_samples']} of "
          f"{info['exclusion']['total_samples']} raw sample(s) excluded "
          f"({100*info['exclusion']['total_excluded_samples']/info['exclusion']['total_samples']:.4f}%).")
    if mask_dir is not None:
        info["mask"] = {"dir": str(mask_dir), "white_level": mask_white_level,
                        "bit_depth": camera_backend.BIT_DEPTH,
                        "clipped_fraction_per_frame": mask_clipped_fraction}

    if sigma_clip is not None:
        # pass 2: variance from deviations (stable), then pass 3: clipped mean
        sq = np.zeros((H, W, C), dtype=np.float64)
        for f in files:
            d = to_work(_checked_load(f, (H, W, C), in_dtype)) - mean
            sq += d * d
        sd = np.sqrt(sq / n)
        lo_th = mean - sigma_clip * sd
        hi_th = mean + sigma_clip * sd
        ssum = np.zeros((H, W, C), dtype=np.float64)
        kcount = np.zeros((H, W, C), dtype=np.int64)
        for f in files:
            x = to_work(_checked_load(f, (H, W, C), in_dtype))
            keep = (x >= lo_th) & (x <= hi_th)
            ssum += np.where(keep, x, 0.0)
            kcount += keep
        mean = np.where(kcount > 0, ssum / np.maximum(kcount, 1), mean)
        rejected = int(n * H * W * C - kcount.sum())
        frac = 100 * rejected / (n * H * W * C)
        print(f"sigma-clip ({label}): rejected {rejected} pixel-samples "
              f"({frac:.4f}% of all samples).")
        info["sigma_clip"] = sigma_clip
        info["rejected_samples"] = rejected
        info["rejected_pct"] = round(frac, 4)

    elif report_deviation:
        # diagnostic-only pass: per-frame mean abs deviation vs the mean,
        # reported in native levels so the numbers are interpretable.
        unit = "native levels" if gamma is None else f"linearised[0,1]x{dmax:.0f}"
        scale = 1.0 if gamma is None else dmax
        print(f"\nframe deviation from mean ({label}, mean abs diff, {unit}):")
        for f in files:
            dev = np.abs(to_work(_checked_load(f, (H, W, C), in_dtype)) - mean).mean()
            print(f"  {f.name:32s} {dev * scale:6.3f}")

    return mean * final_scale, info, excluded_count


def flat_field(sci01, flat01, dark01):
    """Apply dark and flat correction to a science image (all in [0, 1]).

        corrected = (science - dark) / (flat - dark) * median(flat - dark)

    The dark frame is the additive floor (bias offset, hot pixels); subtracting
    it leaves only light-driven signal. Dividing by the dark-subtracted flat
    removes everything that scales with the illumination but is not the
    specimen: vignetting, uneven lighting, dust shadows, and per-pixel gain.
    Multiplying back by the median of the flat restores the original overall
    brightness so the output stays on the same scale as the input. Each colour
    channel uses its own median, preserving colour balance.

    NB: this is a linear-light operation. If sci01/flat01/dark01 are gamma- or
    ISP-encoded, linearise first (see average_burst's `gamma`).

    flat01 and/or dark01 may be None (sci01 is required).
    """
    has_dark = dark01 is not None
    D = dark01 if has_dark else 0.0
    info = {}

    if flat01 is None:
        info["mode"] = "dark-subtraction only"
        info["formula"] = "per pixel: science - dark"
        # do NOT floor here: negatives from dark over-subtraction must survive
        # to the clip accounting in main(). The final uint16 cast floors them.
        return sci01 - D, info

    denom = flat01 - D
    bad = denom <= 1e-6                         # flat <= dark: no usable signal here
    safe_denom = np.where(bad, 1.0, denom)

    C = sci01.shape[2]
    gain = np.empty(C)
    for c in range(C):
        good = ~bad[:, :, c]
        gain[c] = np.median(denom[:, :, c][good]) if good.any() else 1.0

    corrected = (sci01 - D) / safe_denom * gain
    corrected = np.where(bad, sci01 - D, corrected)   # leave un-correctable px as plain dark-sub

    # falloff stats computed on usable pixels only, so bad px don't skew them
    good_all = ~bad
    if good_all.any():
        lo, hi = np.percentile(denom[good_all], 2), np.percentile(denom[good_all], 98)
        info["flat_falloff_pct"] = round(float(100 * (1 - lo / max(hi, 1e-9))), 1)
    info["flat_uncorrectable_px"] = int(bad.sum())
    info["gain_per_channel"] = [round(float(g), 6) for g in gain]
    info["mode"] = "dark + flat-field" if has_dark else "flat-field only"
    dark_term = "dark" if has_dark else "0"
    info["formula"] = (f"per channel c: (science - {dark_term}) / "
                       f"(flat - {dark_term}) * median_c(flat - {dark_term})")
    return corrected, info


def render_check():
    """This file's first self-check (none existed before -- confirmed,
    `grep` for `render_check`/`if __name__` found only main()'s own
    dispatch). Scoped to this session's own work: the saturation mask
    reader/writer, in POSITIVE form -- position-exact assertions on a
    synthetic frame with known clipped positions, not a count (a mask
    that is always full or always empty passes a count-based check,
    which is exactly why an all-clipped and an all-clean frame are each
    exercised separately, not folded into the position-exact case) --
    plus the profile-substitution requirement: a different bit depth
    must change which positions get marked, reached through
    write_clipped_mask's own real call path, not a value the check
    pre-computed and handed in from outside."""
    import shutil
    import tempfile

    tmp_dir = Path(tempfile.mkdtemp(prefix="zynergy_frame_average_render_check_"))
    try:
        assert camera_backend is not None, "camera_backend.py must be importable"

        # --- position-exact: known clipped positions, not a count ----------
        _, white_level = clipped_mask_for_frame(np.zeros((1, 1), dtype=np.uint16))
        h, w = 5, 7
        arr = np.full((h, w), white_level - 1, dtype=np.uint16)   # all unclipped
        clipped_positions = {(0, 0), (2, 3), (4, 6), (1, 6)}
        for r, c in clipped_positions:
            arr[r, c] = white_level   # exactly at the ceiling -- clipped, by >= definition

        mask_path = tmp_dir / "known.satmask.npz"
        used_white = write_clipped_mask(arr, mask_path)
        assert used_white == white_level
        got = load_clipped_mask(mask_path)
        assert got.shape == arr.shape, "unpacked mask shape must match the source frame exactly"
        assert got.dtype == bool
        for r in range(h):
            for c in range(w):
                expected = (r, c) in clipped_positions
                assert bool(got[r, c]) == expected, (
                    "position ({}, {}) marked {!r}, expected {!r} -- position-exact "
                    "check, not a count".format(r, c, bool(got[r, c]), expected))
        assert int(got.sum()) == len(clipped_positions)

        # --- all-clipped / all-clean: a count-based check passes these vacuously ---
        all_clipped = np.full((h, w), white_level, dtype=np.uint16)
        write_clipped_mask(all_clipped, tmp_dir / "allclipped.satmask.npz")
        got_all = load_clipped_mask(tmp_dir / "allclipped.satmask.npz")
        assert got_all.all(), "an all-clipped frame must mark every position"

        all_clean = np.full((h, w), white_level - 1, dtype=np.uint16)
        write_clipped_mask(all_clean, tmp_dir / "allclean.satmask.npz")
        got_clean = load_clipped_mask(tmp_dir / "allclean.satmask.npz")
        assert not got_clean.any(), "an all-clean frame must mark nothing"

        # --- profile substitution: a different bit depth changes the marked set ---
        # Reassignment, not a frozen `from` import -- clipped_mask_for_frame
        # reads camera_backend.BIT_DEPTH via module-attribute access, fresh
        # each call, so this reaches it. Nothing in this chain caches a
        # value at any "construction" moment (there is no camera object at
        # all here) -- unlike Picamera2Camera's own mode-crop table, there
        # is no equivalent too-late hazard to guard against for this
        # consumer; stated rather than assumed away, same as the two prior
        # sessions' own substitution checks.
        real_bit_depth = camera_backend.BIT_DEPTH
        real_white = camera_backend.white_level_for_bit_depth(real_bit_depth)
        try:
            camera_backend.BIT_DEPTH = 10   # a real IMX477 mode option, genuinely different
            synth_white = camera_backend.white_level_for_bit_depth(camera_backend.BIT_DEPTH)
            assert synth_white != real_white, \
                "test setup: substituted bit depth must derive a different white level"
            probe_value = (synth_white + real_white) // 2   # strictly between the two
            probe = np.full((3, 3), probe_value, dtype=np.uint16)
            write_clipped_mask(probe, tmp_dir / "synth.satmask.npz")
            got_synth = load_clipped_mask(tmp_dir / "synth.satmask.npz")
        finally:
            camera_backend.BIT_DEPTH = real_bit_depth
        write_clipped_mask(probe, tmp_dir / "real.satmask.npz")
        got_real = load_clipped_mask(tmp_dir / "real.satmask.npz")
        assert got_synth.all() and not got_real.any(), (
            "the SAME probe frame must be marked clipped under the "
            "substituted (lower) bit depth's white level and NOT clipped "
            "under the real one, reached through write_clipped_mask's own "
            "real call path -- got synth_all={!r} real_any={!r}"
            .format(got_synth.all(), got_real.any()))

        # -------------------------------------------------------------
        # Exclusion-averaging: average_burst must exclude clipped raw
        # samples per photosite, dividing by the count of samples
        # actually included there -- not the frame count. Position-exact,
        # not count-based: two positions with DIFFERENT clean counts (3
        # vs 2), so a bug that applies one divisor to the whole frame
        # cannot pass by using the same wrong divisor everywhere.
        # -------------------------------------------------------------
        dmax = 65535.0
        n_burst = 4
        # (0,0) clean in every frame; (0,1) clipped in 1 of 4 (frame 2);
        # (0,2) clipped in every frame (all-clipped); (0,3) clipped in 2
        # of 4 (frames 1 and 3) -- a DIFFERENT clean count than (0,1).
        frames_native = [
            [1000, 1000,       real_white,  1000],
            [1000, 1100,       real_white,  real_white + 5],
            [1000, real_white, real_white,  1050],
            [1000, 1200,       real_white,  real_white + 10],
        ]
        expected_clipped = [
            [False, False, True, False],
            [False, False, True, True],
            [False, True,  True, False],
            [False, False, True, True],
        ]
        burst_dir = tmp_dir / "exclusion_burst"
        burst_dir.mkdir()
        burst_files = []
        for i, row in enumerate(frames_native):
            p = burst_dir / f"frame_{i:04d}.tif"
            tifffile.imwrite(str(p), np.array([row], dtype=np.uint16))
            burst_files.append(p)

        mean01, exc_info, excluded = average_burst(burst_files, label="exclusion-check")

        # excluded-count matches the mask at every position, not just a total
        expected_excluded = np.array(expected_clipped, dtype=bool).sum(axis=0)
        assert excluded.shape == (1, 4, 1), f"unexpected excluded shape {excluded.shape}"
        for c in range(4):
            assert int(excluded[0, c, 0]) == int(expected_excluded[c]), (
                "position (0,{}) excluded count {} != expected {}".format(
                    c, int(excluded[0, c, 0]), int(expected_excluded[c])))

        # (0,0): clean in every frame -- bit-identical to today's
        # unconditional average, computed in the SAME reciprocal-multiply
        # order the code itself uses (not a fresh division -- SWEEP_
        # CHECKS.md's own caution about float-tie divergence applies here
        # exactly as it did to the earlier dark-subtraction re-derivation).
        old_style_mean01_00 = (sum(row[0] for row in frames_native) / n_burst) * (1.0 / dmax)
        assert mean01[0, 0, 0] == old_style_mean01_00, (
            "clean photosite must be bit-identical to today's unconditional "
            "average: got {!r}, expected {!r}".format(mean01[0, 0, 0], old_style_mean01_00))

        # (0,1): clipped in 1 of 4 -- averages only the 3 clean samples,
        # divides by 3, not 4.
        expected_01 = ((1000 + 1100 + 1200) / 3) * (1.0 / dmax)
        assert mean01[0, 1, 0] == expected_01, (
            "partially-clipped photosite (0,1) must average only its clean "
            "samples and divide by 3: got {!r}, expected {!r}".format(
                mean01[0, 1, 0], expected_01))
        naive_01 = ((1000 + 1100 + real_white + 1200) / n_burst) * (1.0 / dmax)
        assert mean01[0, 1, 0] != naive_01, (
            "test is not exercising exclusion: new result must differ from "
            "today's unconditional (diluted) average at a clipped position")

        # (0,3): clipped in 2 of 4 -- a DIFFERENT clean count than (0,1)
        # (2 clean samples, not 3). A per-frame or per-plane uniform
        # divisor bug would still pass (0,1) alone; this position is what
        # catches it.
        expected_03 = ((1000 + 1050) / 2) * (1.0 / dmax)
        assert mean01[0, 3, 0] == expected_03, (
            "partially-clipped photosite (0,3) (different clean count than "
            "(0,1)) must divide by its own count of 2: got {!r}, expected {!r}"
            .format(mean01[0, 3, 0], expected_03))

        # (0,2): clipped in every frame -- takes the defined all-clipped
        # path. Sentinel = white_level itself (native domain here, since
        # this call uses gamma=None) -- never 0 (indistinguishable from a
        # legitimately dark pixel), never the container ceiling 65535
        # (indistinguishable from a real bright pixel). Detectable as such
        # via the excluded-count record: that is the ONLY thing that
        # disambiguates it from a genuine photosite whose raw value
        # happened to be exactly white_level in every sample.
        expected_02 = float(real_white) * (1.0 / dmax)
        assert mean01[0, 2, 0] == expected_02, (
            "all-clipped photosite must hold the white_level sentinel: "
            "got {!r}, expected {!r}".format(mean01[0, 2, 0], expected_02))
        assert int(excluded[0, 2, 0]) == n_burst, \
            "all-clipped photosite's excluded count must equal the frame count"
        assert mean01[0, 2, 0] != 65535.0 * (1.0 / dmax), \
            "all-clipped sentinel must not be the container ceiling"
        assert mean01[0, 2, 0] != 0.0, \
            "all-clipped sentinel must not be silently zero"

        assert "exclusion" in exc_info, "average_burst must report exclusion stats in info"
        exc = exc_info["exclusion"]
        assert exc["white_level"] == real_white
        assert exc["partially_clipped_photosites"] == 2, (  # (0,1) and (0,3)
            "expected 2 partially-clipped photosites, got {}".format(
                exc["partially_clipped_photosites"]))
        assert exc["fully_clipped_photosites"] == 1, (  # (0,2)
            "expected 1 fully-clipped photosite, got {}".format(
                exc["fully_clipped_photosites"]))
        assert exc["total_excluded_samples"] == int(expected_excluded.sum()), (
            "info['exclusion']['total_excluded_samples'] must match the "
            "excluded-count array's own total")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("saturation mask check PASS: position-exact on a synthetic frame "
          "with known clipped positions, not a count; all-clipped and "
          "all-clean frames both exercised separately; a substituted bit "
          "depth changes which positions get marked, through write_clipped_"
          "mask's own real call path, not a value handed in from outside")


def main():
    if "--render-check" in sys.argv:
        render_check()
        return
    ap = argparse.ArgumentParser(
        description="Average a static-field burst into a 16-bit TIFF, "
                    "with optional flat-field / dark-frame correction.")
    ap.add_argument("inputs", nargs="+", help="science frame files (glob) or a directory")
    ap.add_argument("-o", "--output", default="averaged_16bit.tif", help="output 16-bit TIFF path")
    ap.add_argument("--flat", nargs="+", metavar="FRAME",
                    help="flat-field burst: evenly-lit, specimen-free field shot in the "
                         "same optical configuration (removes vignetting, dust, gain)")
    ap.add_argument("--dark", nargs="+", metavar="FRAME",
                    help="dark burst: same exposure/gain with light blocked "
                         "(removes bias offset and hot pixels)")
    ap.add_argument("--sigma-clip", type=float, default=None, metavar="K",
                    help="reject per-pixel outliers beyond K sigma before averaging "
                         "(streamed, memory-light; good for vibration/cosmic hits). "
                         "Applied to every burst.")
    ap.add_argument("--gamma", type=float, default=None, metavar="G",
                    help="linearise inputs as linear = (value)**G before averaging and "
                         "correction (e.g. 2.2 for sRGB-ish encoding). Required for a "
                         "physical flat-field if frames are gamma/ISP-encoded.")
    ap.add_argument("--linear-out", action="store_true",
                    help="with --gamma, write the result in linear light instead of "
                         "re-encoding back to the input domain.")
    ap.add_argument("--hash", action="store_true",
                    help="record a sha256 of every input frame in the output provenance "
                         "block (slower, but makes the result independently verifiable).")
    ap.add_argument("--no-compress", action="store_true", help="write uncompressed instead of deflate")
    ap.add_argument("--sidecar-dir", default=None, metavar="DIR",
                    help="directory holding the science burst's .meta.json capture "
                         "sidecars (provenance.py's naming: <raw_frame_stem>.meta.json), "
                         "typically a session's own provenance directory. Carries "
                         "analogue_gain/sensor_mode/capture_time_utc into this tool's "
                         "own output provenance -- null per-field (never omitted) "
                         "wherever a value isn't available or the burst disagrees.")
    ap.add_argument("--mask-dir", default=None, metavar="DIR",
                    help="ALSO persist one packed saturation mask per science raw "
                         "frame into this directory (<raw_frame_stem>.satmask.npz, "
                         "same keying convention as --sidecar-dir's .meta.json files) "
                         "-- typically a session's own provenance directory. "
                         "Clipped-sample exclusion itself (see -o's own output and "
                         "the printed per-burst summary) is unconditional and does "
                         "NOT depend on this flag; omitting it only skips writing the "
                         "per-frame mask files to disk.")
    args = ap.parse_args()

    if args.linear_out and args.gamma is None:
        sys.exit("--linear-out only makes sense together with --gamma.")

    prov = {
        "software": "frame_average.py",
        "version": __version__,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }

    def file_record(paths):
        rec = {"count": len(paths), "files": [p.name for p in paths]}
        if args.hash:
            rec["sha256"] = {p.name: sha256_file(p) for p in paths}
        return rec

    # ---- science burst
    sci_files = collect_inputs(args.inputs)
    print(f"Found {len(sci_files)} science frame(s).")
    sci01, sci_info, sci_excluded = average_burst(sci_files, args.sigma_clip, report_deviation=True,
                                    label="science", gamma=args.gamma,
                                    mask_dir=args.mask_dir)
    W, H, C = sci_info["geometry"]
    in_bits, n = sci_info["bits"], sci_info["n"]
    print(f"Frame geometry: {W}x{H}, {C} channel(s), {in_bits}-bit input.")
    prov["science"] = file_record(sci_files)
    prov["geometry"] = {"width": W, "height": H, "channels": C, "input_bits": in_bits}
    prov["domain_processed"] = sci_info["domain"]
    prov["exclusion"] = sci_info["exclusion"]
    if "mask" in sci_info:
        prov["saturation_mask"] = sci_info["mask"]
        frac_str = ", ".join(f"{k}={v:.4%}" for k, v in
                             sci_info["mask"]["clipped_fraction_per_frame"].items())
        print(f"  saturation masks written to {args.mask_dir} "
              f"(white_level={sci_info['mask']['white_level']:g}): {frac_str}")
    capture_meta = capture_meta_for_science(sci_files, args.sidecar_dir)
    prov["analogue_gain"] = capture_meta["analogue_gain"]
    prov["sensor_mode"] = capture_meta["sensor_mode"]
    prov["capture_time_utc"] = capture_meta["capture_time_utc"]
    prov["capture_metadata_note"] = capture_meta["note"]
    if "sigma_clip" in sci_info:
        prov["sigma_clip"] = {"k": sci_info["sigma_clip"],
                              "rejected_samples": sci_info["rejected_samples"],
                              "rejected_pct": sci_info["rejected_pct"]}

    # ---- optional blank bursts (averaged with the same machinery & transform)
    flat01 = dark01 = None
    if args.dark:
        dark_files = collect_inputs(args.dark)
        print(f"\nFound {len(dark_files)} dark frame(s).")
        dark01, di, _dark_excluded = average_burst(dark_files, args.sigma_clip, label="dark", gamma=args.gamma)
        if di["geometry"] != sci_info["geometry"]:
            sys.exit(f"Dark geometry {di['geometry']} != science {sci_info['geometry']}.")
        prov["dark"] = file_record(dark_files)
        prov["dark_exclusion"] = di["exclusion"]
    if args.flat:
        flat_files = collect_inputs(args.flat)
        print(f"Found {len(flat_files)} flat frame(s).")
        flat01, fi, _flat_excluded = average_burst(flat_files, args.sigma_clip, label="flat", gamma=args.gamma)
        if fi["geometry"] != sci_info["geometry"]:
            sys.exit(f"Flat geometry {fi['geometry']} != science {sci_info['geometry']}.")
        prov["flat"] = file_record(flat_files)
        prov["flat_exclusion"] = fi["exclusion"]

    # ---- correction
    if flat01 is not None or dark01 is not None:
        print("\nApplying correction ...")
        corrected, ff = flat_field(sci01, flat01, dark01)
        if ff.get("mode"):
            print(f"  {ff['mode']}")
        if "flat_falloff_pct" in ff:
            print(f"  flat illumination falloff removed: ~{ff['flat_falloff_pct']}%")
        if ff.get("flat_uncorrectable_px"):
            print(f"  {ff['flat_uncorrectable_px']} px had flat <= dark (left uncorrected)")
        prov["correction"] = ff
    else:
        corrected = sci01
        prov["correction"] = {"mode": "none (plain average)"}

    # ---- clip accounting: record railed pixels on the corrected result BEFORE
    # any flooring or re-encoding, so the count is honest in every mode. The
    # re-encode below floors negatives only because the fractional power is
    # undefined for them, and the final uint16 cast floors again; doing the
    # accounting first means neither step can silently hide under-range pixels.
    clip_domain = ("linear" if (args.gamma is None or args.linear_out)
                   else f"linearised(gamma={args.gamma})")
    clipped_low = int(np.count_nonzero(corrected < 0.0))
    clipped_high = int(np.count_nonzero(corrected > 1.0))
    total_px = corrected.size
    if clipped_low or clipped_high:
        print(f"  clipping: {clipped_high} px > full-scale "
              f"({100*clipped_high/total_px:.4f}%), "
              f"{clipped_low} px < 0 ({100*clipped_low/total_px:.4f}%) — values railed.")
    prov["clipping"] = {
        "measured_in": clip_domain,
        "above_full_scale_px": clipped_high,
        "below_zero_px": clipped_low,
        "above_full_scale_pct": round(100 * clipped_high / total_px, 4),
        "below_zero_pct": round(100 * clipped_low / total_px, 4),
    }

    # ---- re-encode if we linearised and the user wants the input domain back.
    # Negatives are floored here only to feed the fractional power; they have
    # already been counted above.
    if args.gamma is not None and not args.linear_out:
        corrected = np.clip(corrected, 0.0, None) ** (1.0 / args.gamma)
        prov["output_domain"] = f"re-encoded(gamma={args.gamma})"
    elif args.gamma is not None:
        prov["output_domain"] = "linear"
    else:
        prov["output_domain"] = "linear (input treated as linear)"

    # ---- precision summary (heuristic; see module docstring caveat)
    noise_factor = float(np.sqrt(n))
    eff_bits = in_bits + 0.5 * np.log2(n)
    prov["precision"] = {
        "frames_averaged": n,
        "noise_reduction_factor": round(noise_factor, 3),
        "effective_bits_estimate": round(float(eff_bits), 2),
        "effective_bits_note": "upper bound; valid only if per-frame noise >= ~1 LSB (dithered)",
    }

    # ---- scale to 16-bit and write
    out = np.clip(np.rint(corrected * 65535.0), 0, 65535).astype(np.uint16)
    if C == 1:
        out = out[:, :, 0]
        photometric = "minisblack"
    else:
        photometric = "rgb"
    comp = None if args.no_compress else "deflate"

    # ---- excluded-count sibling: the user-facing, full-resolution half of
    # exclusion. Alongside the master, not only summarised in its
    # provenance -- a photosite's own excluded-sample count is what
    # actually disambiguates the all-clipped sentinel above from a
    # genuine reading of exactly white_level. Science burst only: dark/
    # flat are exclusion-corrected too (uniform path, see average_burst),
    # but neither has an independent -o path of its own to hang a sibling
    # off today.
    excluded_out_path = str(Path(args.output).with_name(Path(args.output).stem + "_excluded_count.tif"))
    excluded_write = sci_excluded[:, :, 0] if C == 1 else sci_excluded
    excluded_desc = json.dumps({
        "software": "frame_average.py", "version": __version__,
        "paired_master": str(args.output),
        "meaning": "count of raw science samples excluded (clipped, >= white_level) "
                   "per photosite, out of n total -- n == frame count is the "
                   "all-clipped case; the master's own value there is a sentinel "
                   "(white_level), not a measured average",
        "n": n, "white_level": sci_info["exclusion"]["white_level"],
    }, separators=(",", ":"))
    tifffile.imwrite(excluded_out_path, excluded_write.astype(np.uint16),
                     photometric="minisblack", compression=comp, description=excluded_desc)
    prov["excluded_count_output"] = excluded_out_path

    prov["output"] = {"path": str(args.output), "dtype": "uint16",
                      "compression": "deflate" if comp else "none",
                      "value_range": [int(out.min()), int(out.max())]}
    description = json.dumps(prov, separators=(",", ":"))
    tifffile.imwrite(args.output, out, photometric=photometric, compression=comp,
                     description=description)

    # ---- report
    print(f"\nWrote {args.output}")
    print(f"  output: {out.shape} uint16, {'deflate' if comp else 'uncompressed'}")
    print(f"  averaged {n} frames -> noise reduced ~{noise_factor:.2f}x "
          f"(~{eff_bits:.1f} effective bits vs {in_bits}-bit source; upper bound)")
    print(f"  16-bit value range: {int(out.min())}..{int(out.max())}")
    print(f"  excluded-sample count written to {excluded_out_path} "
          f"({sci_info['exclusion']['partially_clipped_photosites']} partially-clipped, "
          f"{sci_info['exclusion']['fully_clipped_photosites']} fully-clipped photosite(s))")
    print(f"  provenance JSON embedded in ImageDescription "
          f"({len(description)} bytes; read with tifffile.TiffFile(...).pages[0].description)")


if __name__ == "__main__":
    main()
