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

__version__ = "2.1"
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


def sha256_file(path, _buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_buf), b""):
            h.update(chunk)
    return h.hexdigest()


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
                  gamma=None):
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

    Returns (mean01, info).
    """
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

    # ---- pass 1: streaming mean (memory bounded to a few full frames)
    acc = np.zeros((H, W, C), dtype=np.float64)
    for f in files:
        acc += to_work(_checked_load(f, (H, W, C), in_dtype))
    mean = acc / n
    info = {"n": n, "geometry": (W, H, C), "bits": in_bits,
            "domain": "linear" if gamma is None else f"linearised(gamma={gamma})"}

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

    return mean * final_scale, info


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


def main():
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
    sci01, sci_info = average_burst(sci_files, args.sigma_clip, report_deviation=True,
                                    label="science", gamma=args.gamma)
    W, H, C = sci_info["geometry"]
    in_bits, n = sci_info["bits"], sci_info["n"]
    print(f"Frame geometry: {W}x{H}, {C} channel(s), {in_bits}-bit input.")
    prov["science"] = file_record(sci_files)
    prov["geometry"] = {"width": W, "height": H, "channels": C, "input_bits": in_bits}
    prov["domain_processed"] = sci_info["domain"]
    if "sigma_clip" in sci_info:
        prov["sigma_clip"] = {"k": sci_info["sigma_clip"],
                              "rejected_samples": sci_info["rejected_samples"],
                              "rejected_pct": sci_info["rejected_pct"]}

    # ---- optional blank bursts (averaged with the same machinery & transform)
    flat01 = dark01 = None
    if args.dark:
        dark_files = collect_inputs(args.dark)
        print(f"\nFound {len(dark_files)} dark frame(s).")
        dark01, di = average_burst(dark_files, args.sigma_clip, label="dark", gamma=args.gamma)
        if di["geometry"] != sci_info["geometry"]:
            sys.exit(f"Dark geometry {di['geometry']} != science {sci_info['geometry']}.")
        prov["dark"] = file_record(dark_files)
    if args.flat:
        flat_files = collect_inputs(args.flat)
        print(f"Found {len(flat_files)} flat frame(s).")
        flat01, fi = average_burst(flat_files, args.sigma_clip, label="flat", gamma=args.gamma)
        if fi["geometry"] != sci_info["geometry"]:
            sys.exit(f"Flat geometry {fi['geometry']} != science {sci_info['geometry']}.")
        prov["flat"] = file_record(flat_files)

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
    print(f"  provenance JSON embedded in ImageDescription "
          f"({len(description)} bytes; read with tifffile.TiffFile(...).pages[0].description)")


if __name__ == "__main__":
    main()
