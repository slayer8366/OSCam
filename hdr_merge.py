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
    distrusts pixels near the noise floor, and HARD-EXCLUDES pixels at or above
    saturation (a clipped pixel carries no recoverable value, and the mean of a
    clipped value is still clipped). Longer exposures additionally carry more
    weight where they are still valid, because their estimate of E is less
    noise-dominated. The merge is therefore close to a physical calculation
    rather than a cosmetic blend — but ONLY if the inputs are genuinely linear.
    If your masters were gamma/ISP-encoded, linearise them first
    (frame_average.py --gamma ... --linear-out); merging encoded data is the
    same category error as flat-fielding encoded data.

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
    - Saturation handling is the usual HDR footgun. --sat is a fraction of the
      white level; pixels at/above it are given zero weight. If your white level
      is not the container's dtype max (e.g. 12-bit data in a 16-bit file), set
      --white-level or the saturation test will be meaningless.

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
    of how many brackets there are. Per pixel:

        E_i      = (value_i/white - black) / t_i          # irradiance estimate
        p_i      = clip((value_i/white - black)/(1-black), 0, 1)
        w_valid  = 4*p_i*(1-p_i)                           # mid-tone hat (0 at ends)
        w_valid  = 0  where value_i/white >= sat_frac      # hard clip exclusion
                   or value_i/white <= black               # at/below black floor
        w_i      = w_valid * t_i                           # favour longer valid exposures
        E        = sum_i w_i E_i / sum_i w_i

    Pixels with zero total weight (clipped or black in every frame) fall back:
    saturated-everywhere -> estimate from the SHORTEST exposure (least clipped);
    black-everywhere -> 0; any other zero-weight pixel -> the per-pixel estimate
    from the frame nearest mid-tone. All three are counted in the provenance.
    """
    first = load_frame(exposures[0]["path"])
    H, W, C = first.shape
    in_dtype = first.dtype
    wl = float(white_level) if white_level is not None else dtype_max(in_dtype)
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
        clipped = vn >= sat_frac
        belowblk = vn <= black
        w_valid = np.where(clipped | belowblk, 0.0, w_valid)
        w = w_valid * t

        acc_num += w * E_i
        acc_den += w

        dist = np.abs(p - 0.5)
        better = dist < best_dist
        best_dist = np.where(better, dist, best_dist)
        best_E = np.where(better, E_i, best_E)
        if idx == 0:
            E_short = E_i                       # shortest exposure (sorted)

        sat_all &= clipped
        blk_all &= belowblk

        rec = {"name": ex["path"].name, "exposure_s": t, "t_source": ex["t_source"],
               "capture": try_read_embedded_capture_meta(ex["path"])}
        if hash_inputs:
            rec["sha256"] = sha256_file(ex["path"])
        records.append(rec)
        print(f"  [{idx}] {ex['path'].name:32s} t={t:g}s  "
              f"clipped px={int(clipped.sum())}")

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


def main():
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
                    help="saturation cutoff as a fraction of white level; pixels "
                         "at/above this are given zero weight (default 0.95).")
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
                  "w_i = 4p(1-p)*t_i with p the black-to-white position, "
                  "zero weight where v_i/white >= sat or <= black"),
        "domain": "linear, scene-referred (NOT tone-mapped)",
        "white_level": info["white_level"],
        "white_level_source": args.white_level_source,
        "black": info["black"],
        "black_note": args.black_note,
        "sat_frac": info["sat_frac"],
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
