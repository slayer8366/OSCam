#!/usr/bin/env python3
"""
debayer.py — turn a raw-Bayer-mosaic master (the kind frame_average.py /
hdr_merge.py produce when fed Pi HQ DNGs) into a measurable image, making the
demosaic choice explicit and recorded rather than incidental.

Why this exists:
    tifffile.imread on a Pi HQ DNG returns the undemosaiced CFA plane — a single
    channel where adjacent pixels are different colour channels. Averaging and
    merging in that mosaic space is correct and sensor-traceable, but the result
    is still a mosaic; viewed at 1:1 it shows the CFA grid ("resolution
    artifacts"). This tool performs the final, stated transform out of mosaic
    space.

Two outputs, two philosophies:
    --green   ZERO interpolation. Extracts ONE green sub-plane (2028x1520 for a
              4056x3040 sensor). Every value is a real sensor sample; nothing is
              reconstructed. The honest substrate for morphometry. Pixel pitch
              is doubled each axis, so it is a DIFFERENT scale space — calibrate
              the stage micrometer on THIS plane, not the full-res mosaic.

    --rgb     Demosaiced colour. Default --method bilinear (full-res, but edge
              positions are interpolated, i.e. reconstructed). --method binned
              gives an interpolation-free RGB at half resolution (each 2x2 RGGB
              quad -> one RGB pixel; the two greens are averaged, which is the
              only interpolation, and only within the quad).

    --tonemap An OPTIONAL, SEPARATE display-referred image derived from the
              demosaiced RGB (extended Reinhard with white point Lw). The --green
              and --rgb outputs stay measurements and are untouched; the tone-
              mapped file is written separately, with its own provenance and the
              full lineage carried forward. Requires --rgb and a LINEAR float
              input master (the kind hdr_merge.py emits) — tone mapping an integer
              mosaic of unknown encoding is refused, being the same category error
              as merging encoded data. This is the correct home for tone mapping:
              it runs AFTER demosaic, on assembled RGB, so it neither corrupts the
              merge's per-photosite saturation logic nor desaturates by acting on
              raw mosaic samples.

    --colour-gains  OPTIONAL libcamera-style white balance (R*=RED, B*=BLUE,
              green=1.0), applied ONLY on the display branch, before tone mapping.
              It encodes an illuminant assumption (a rendering choice), so it
              never touches the linear masters — those stay sensor-native. Green
              is unmodified, so the green-plane morphometry is unaffected. Recorded
              in the display file's provenance. Requires --tonemap.

CFA pattern:
    Green positions ARE determinable from a mosaic (the two greens of any
    RGGB/BGGR/GRBG/GBRG are a diagonal pair with matched means). R vs B is NOT —
    distinguishing them needs a known-colour reference, not pixel statistics. So
    the default --pattern is BGGR (the IMX477's physical order): R/B is asserted
    from the sensor, not guessed from the data. --pattern auto re-detects the
    greens but still has to assume R/B (now IMX477 order); --swap-rb flips that
    assumption; set another --pattern for a different sensor. The choice affects
    only colour rendering — the --green measurement plane is identical either way
    (both greens sit on the same diagonal in RGGB and BGGR).

Usage:
    python3 debayer.py master.tif --green -o master_green.tif
    python3 debayer.py master.tif --rgb -o master_rgb.tif
    python3 debayer.py master.tif --green --rgb            # both, auto-named
    python3 debayer.py hdr_linear.tif --rgb --method binned --hash
    python3 debayer.py hdr_linear.tif --rgb --tonemap reinhard --tonemap-white 2.2 --tonemap-8bit
    # white-balanced display (your calibrated ColourGains), tone-mapped:
    python3 debayer.py hdr_linear.tif --rgb --colour-gains 1.8 0.9 \
        --tonemap reinhard --tonemap-white 2.2 --tonemap-8bit
    # single linear DNG average (uint16): assert linearity + its white level,
    # use Lw=1.0 (measurements stay integer; only the display is normalised):
    python3 debayer.py averaged.tif --rgb --assume-linear 4095 \
        --tonemap reinhard --tonemap-white 1.0 --tonemap-8bit
    # full display pipeline in one pass (CA -> WB -> tonemap -> shadow/sharpen):
    python3 debayer.py hdr_linear.tif --rgb --ca-correct ca_calib.json \
        --colour-gains 1.89 1.59 --tonemap reinhard --tonemap-white 2.2 \
        --shadow-deepen --sharpen 1.5 --tonemap-8bit

Display branch (all DISPLAY-ONLY; never touches green / linear masters):
    demosaic -> ca-correct -> white-balance -> tone map -> shadow-deepen
    -> clahe -> local-contrast -> sharpen -> [sRGB]. Each stage is optional,
    fully recorded in the embedded provenance, and supersedes the standalone
    awb_chroma/tonemap/shadow_deepen/micro_contrast scripts.

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

__version__ = "1.0"

try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

try:
    from . import ca_lib as _ca
except ImportError:
    try:
        import ca_lib as _ca
    except ImportError:
        _ca = None


def tonemap_reinhard(lin, white_point):
    """Extended Reinhard with white point (display-referred, reversible).

        Ld = L * (1 + L / Lw^2) / (1 + L)

    Maps Lw exactly to 1.0; inputs above Lw exceed 1.0 (and clip on write).
    Monotonic and invertible given Lw, so it is a display transform and never a
    measurement. Operates on the linear RGB — the same scale as the linear
    master — so a preview tuned on master_rgb.tif reproduces here exactly."""
    lw2 = float(white_point) * float(white_point)
    return (lin * (1.0 + lin / lw2)) / (1.0 + lin)


def srgb_oetf(x):
    """Linear -> sRGB opto-electronic transfer function (gamma encode)."""
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, 12.92 * x,
                    1.055 * np.power(x, 1.0 / 2.4) - 0.055)


def apply_colour_gains(rgb, red_gain, blue_gain):
    """libcamera-style ColourGains white balance: scale R and B relative to
    green (green gain == 1.0 by definition). Linear, invertible and fully
    recorded, so it is a rendering choice that lives on the DISPLAY branch only
    and never touches the measurement masters. Green is unmodified, so the
    green-plane morphometry is unaffected by construction."""
    out = rgb.copy()
    out[..., 0] = out[..., 0] * red_gain
    out[..., 2] = out[..., 2] * blue_gain
    return out


# --- folded display-only effects (formerly shadow_deepen.py / micro_contrast.py)
# All operate on the tone-mapped, display-referred [0,1] image. They are
# DISPLAY-ONLY: shadow deepening remaps density non-linearly; CLAHE and unsharp
# shift apparent edge positions. NEVER measure off their output. Green-plane and
# linear masters never reach this code.
try:
    from scipy.ndimage import gaussian_filter as _gaussian_filter
except ImportError:
    _gaussian_filter = None
try:
    from skimage.exposure import equalize_adapthist as _equalize_adapthist
except ImportError:
    _equalize_adapthist = None

_LUMA_REC709 = np.array([0.2126, 0.7152, 0.0722])


def _luma(arr):
    return arr if arr.ndim == 2 else arr[..., :3] @ _LUMA_REC709


def _apply_luma_gain(arr, new_L, old_L):
    """Recombine a modified luminance into colour while preserving hue."""
    if arr.ndim == 2:
        return new_L
    gain = np.divide(new_L, old_L, out=np.ones_like(old_L), where=old_L > 1e-8)
    out = arr.copy()
    out[..., :3] = arr[..., :3] * gain[..., None]
    return out


def shadow_deepen(arr, pivot, softness, strength, gamma):
    """Darken shadow structure with a single luminance curve applied to every
    pixel identically (no region selection). Hue preserved; analytically
    invertible where the factor is non-zero."""
    softness = max(softness, 1e-6)
    L = _luma(arr)
    u = np.clip((pivot + softness - L) / (2.0 * softness), 0.0, 1.0)
    m = u * u * (3.0 - 2.0 * u)               # smoothstep shadow weight
    factor = 1.0 - strength * m
    if arr.ndim == 2:
        out = arr * factor
    else:
        out = arr.copy()
        out[..., :3] = arr[..., :3] * factor[..., None]
    if gamma != 1.0:
        out = np.clip(out, 0.0, None) ** gamma
    return out


def _unsharp(arr, radius, amount, threshold):
    sigma = (radius, radius, 0) if arr.ndim == 3 else radius
    blurred = _gaussian_filter(arr, sigma=sigma)
    mask = arr - blurred
    if threshold > 0:
        mask = np.where(np.abs(mask) > threshold, mask, 0.0)
    return arr + amount * mask


def stage_clahe(arr, clip, tiles):
    L = np.clip(_luma(arr), 0.0, 1.0)
    h, w = L.shape
    ks = (max(h // tiles, 1), max(w // tiles, 1))
    L2 = _equalize_adapthist(L, kernel_size=ks, clip_limit=clip)
    return _apply_luma_gain(arr, L2, _luma(arr))

GREEN_DIAGONALS = (((0, 0), (1, 1)), ((0, 1), (1, 0)))
POS = [(0, 0), (0, 1), (1, 0), (1, 1)]


def sha256_file(path, _buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_buf), b""):
            h.update(chunk)
    return h.hexdigest()


def load_mosaic(path):
    with tifffile.TiffFile(str(path)) as tf:
        page = tf.pages[0]
        desc = page.description
        arr = page.asarray()
    if arr.ndim != 2:
        sys.exit(f"{path.name} has shape {arr.shape}; expected a single-channel "
                 f"mosaic. This tool operates on raw CFA planes, not RGB images.")
    return arr, desc


def quad_means(arr):
    return {p: float(arr[p[0]::2, p[1]::2].mean()) for p in POS}


def detect_pattern(arr, swap_rb=False):
    """Identify greens as the closer-mean diagonal pair; assign R/B to the other
    diagonal (top-left = R by default). Returns (pattern_str, qmeans, note)."""
    qm = quad_means(arr)
    diffs = [(abs(qm[a] - qm[b]), (a, b)) for (a, b) in GREEN_DIAGONALS]
    diffs.sort()
    greens = diffs[0][1]
    others = diffs[1][1]
    ratio = max(qm.values()) / max(min(qm.values()), 1e-9)
    if ratio < 1.15:
        print("  WARNING: quad-plane means are nearly equal (ratio %.3f). This "
              "input may already be demosaiced/luma, not a raw mosaic." % ratio)
    # R/B is NOT determinable from mosaic statistics (it needs a known-colour
    # reference). Assume the IMX477's physical order: top-left of the non-green
    # diagonal = B (BGGR). --swap-rb flips it; --pattern asserts it outright.
    b_pos, r_pos = sorted(others)
    if swap_rb:
        r_pos, b_pos = b_pos, r_pos
    chmap = {greens[0]: "G", greens[1]: "G", r_pos: "R", b_pos: "B"}
    pattern = "".join(chmap[p] for p in POS)
    note = ("greens auto-detected at %s (matched means); R/B ASSUMED — not "
            "determinable from a mosaic — IMX477 physical order: top-left of "
            "remaining diagonal = B%s"
            % (list(greens), ", swapped" if swap_rb else ""))
    return pattern, qm, note


def masks_for(pattern, H, W):
    R = np.zeros((H, W), bool); G = np.zeros((H, W), bool); B = np.zeros((H, W), bool)
    for ch, (r, c) in zip(pattern, POS):
        {"R": R, "G": G, "B": B}[ch][r::2, c::2] = True
    return R, G, B


def _conv3(plane, kernel):
    p = np.pad(plane, 1, mode="reflect")
    out = np.zeros_like(plane, dtype=np.float64)
    H, W = plane.shape
    for dy in range(3):
        for dx in range(3):
            k = kernel[dy][dx]
            if k:
                out += k * p[dy:dy + H, dx:dx + W]
    return out


def green_position(pattern, which):
    greens = [POS[i] for i, ch in enumerate(pattern) if ch == "G"]
    greens.sort()
    return greens[0] if which == 1 else greens[1]


def extract_green(arr, pattern, which):
    r, c = green_position(pattern, which)
    return arr[r::2, c::2], (r, c)


def demosaic_bilinear(arr, pattern):
    H, W = arr.shape
    R, G, B = masks_for(pattern, H, W)
    a = arr.astype(np.float64)
    kr = [[1, 2, 1], [2, 4, 2], [1, 2, 1]]
    kg = [[0, 1, 0], [1, 4, 1], [0, 1, 0]]
    Rf = _conv3(a * R, kr) / 4.0
    Gf = _conv3(a * G, kg) / 4.0
    Bf = _conv3(a * B, kr) / 4.0
    return np.stack([Rf, Gf, Bf], axis=-1)


def demosaic_binned(arr, pattern):
    H, W = arr.shape
    H2, W2 = H // 2 * 2, W // 2 * 2
    q = arr[:H2, :W2].astype(np.float64).reshape(H2 // 2, 2, W2 // 2, 2)
    chan = {ch: [] for ch in "RGB"}
    for ch, (r, c) in zip(pattern, POS):
        chan[ch].append(q[:, r, :, c])
    R = np.mean(chan["R"], axis=0)
    G = np.mean(chan["G"], axis=0)   # average the two greens (the only interp)
    B = np.mean(chan["B"], axis=0)
    return np.stack([R, G, B], axis=-1)


def cast_like(data, ref_dtype):
    """Return data in ref_dtype: floats stay float32; integers round+clip."""
    if np.issubdtype(ref_dtype, np.floating):
        return data.astype(np.float32)
    info = np.iinfo(ref_dtype)
    return np.clip(np.rint(data), info.min, info.max).astype(ref_dtype)


def write(path, data, photometric, base_prov, extra):
    prov = dict(base_prov); prov.update(extra)
    prov["output"] = {
        "path": str(path),
        "dtype": str(data.dtype),
        "shape": list(data.shape),
        "value_range": [float(data.min()), float(data.max())],
    }
    desc = json.dumps(prov, separators=(",", ":"))
    tifffile.imwrite(str(path), data, photometric=photometric,
                     compression="deflate", description=desc)
    print(f"  wrote {path}  {data.shape} {data.dtype}")


def main():
    ap = argparse.ArgumentParser(description="Debayer a raw-mosaic master into "
                                 "a single-green plane and/or demosaiced RGB.")
    ap.add_argument("input", help="raw-Bayer-mosaic TIFF (averaged or HDR master)")
    ap.add_argument("-o", "--output", default=None,
                    help="output path. With both --green and --rgb, used as a "
                         "stem; otherwise the exact path.")
    ap.add_argument("--green", action="store_true",
                    help="emit single-green plane (zero interpolation)")
    ap.add_argument("--rgb", action="store_true",
                    help="emit demosaiced RGB")
    ap.add_argument("--method", choices=["bilinear", "binned"], default="bilinear",
                    help="RGB method: bilinear (full-res, interpolated) or "
                         "binned (half-res, interpolation-free)")
    ap.add_argument("--green-which", type=int, choices=[1, 2], default=1,
                    help="which of the two greens to extract (default 1)")
    ap.add_argument("--pattern", default="BGGR",
                    choices=["auto", "RGGB", "BGGR", "GRBG", "GBRG"],
                    help="CFA pattern. Default BGGR — the IMX477's physical order. "
                         "R vs B cannot be read from a mosaic, so it is asserted, "
                         "not guessed. Use 'auto' to re-detect greens (R/B still "
                         "assumed, IMX477 order), or set another pattern for a "
                         "different sensor.")
    ap.add_argument("--swap-rb", action="store_true",
                    help="swap the assumed R/B assignment (cosmetic only)")
    ap.add_argument("--hash", action="store_true",
                    help="record sha256 of the input in provenance")
    ap.add_argument("--tonemap", choices=["none", "reinhard"], default="none",
                    help="ALSO write a separate display-referred tone-mapped image "
                         "from the demosaiced RGB; the green/RGB measurements are "
                         "unaffected. Requires --rgb and a LINEAR float input.")
    ap.add_argument("--tonemap-white", type=float, default=2.2, metavar="LW",
                    help="Reinhard white point Lw on the linear RGB scale; maps "
                         "exactly to 1.0 (default 2.2, matching the normalised "
                         "scale hdr_merge produces). Set >= the RGB max to avoid "
                         "clipping highlights.")
    ap.add_argument("--tonemap-srgb", action="store_true",
                    help="apply the sRGB OETF after tone mapping (standard display "
                         "encoding, brighter midtones). Off by default, which "
                         "matches a preview that tone-maps without re-encoding.")
    ap.add_argument("--tonemap-out", default=None, metavar="PATH",
                    help="path for the tone-mapped TIFF (default: the RGB output "
                         "stem + '_display.tif').")
    ap.add_argument("--tonemap-8bit", action="store_true",
                    help="also write an 8-bit PNG next to the tone-mapped TIFF "
                         "(needs Pillow).")
    ap.add_argument("--colour-gains", "--color-gains", nargs=2, type=float,
                    default=None, metavar=("RED", "BLUE"), dest="colour_gains",
                    help="libcamera-style ColourGains white balance on the DISPLAY "
                         "branch only: R*=RED, B*=BLUE, green=1.0. The linear "
                         "masters stay sensor-native and green is untouched. "
                         "Requires --tonemap.")
    ap.add_argument("--assume-linear", type=float, default=None, metavar="WHITE_LEVEL",
                    help="assert that an INTEGER input is already linear (e.g. a raw "
                         "DNG average) with this white level; the display branch "
                         "normalises it (value/WHITE_LEVEL -> [0,1]) so --tonemap "
                         "can run. Pair with --tonemap-white 1.0. The integer "
                         "green/RGB measurement outputs are written unchanged. "
                         "(IMX477 is 12-bit: white level 4095, unless your file was "
                         "scaled to full 16-bit -> 65535; check the input's max.)")
    ap.add_argument("--ca-correct", default=None, metavar="CALIB_JSON",
                    help="register R and B to green using a ca_measure.py "
                         "calibration, on the DISPLAY branch only (before white "
                         "balance / tone map). Green and the measurement masters "
                         "are untouched. Requires --tonemap.")
    # --- folded display-only effects (apply after tone map; need --tonemap) ---
    ap.add_argument("--shadow-deepen", action="store_true",
                    help="deepen shadow structure (display-only, post-tonemap).")
    ap.add_argument("--sd-pivot", type=float, default=0.35)
    ap.add_argument("--sd-softness", type=float, default=0.18)
    ap.add_argument("--sd-strength", type=float, default=0.45,
                    help="max shadow darkening fraction in [0,1) (default 0.45).")
    ap.add_argument("--sd-gamma", type=float, default=1.0,
                    help="optional global gamma after shadow deepen (default 1.0).")
    ap.add_argument("--clahe", action="store_true",
                    help="CLAHE local tone (display-only; needs scikit-image).")
    ap.add_argument("--clahe-clip", type=float, default=0.01)
    ap.add_argument("--clahe-tiles", type=int, default=8)
    ap.add_argument("--local-contrast", type=float, default=None, metavar="RADIUS",
                    help="large-radius unsharp sigma px, mid-frequency (needs scipy).")
    ap.add_argument("--local-contrast-amount", type=float, default=0.30)
    ap.add_argument("--sharpen", type=float, default=None, metavar="RADIUS",
                    help="small-radius unsharp sigma px, high-frequency (needs scipy).")
    ap.add_argument("--sharpen-amount", type=float, default=0.6)
    ap.add_argument("--sharpen-threshold", type=float, default=0.0)
    args = ap.parse_args()

    if not (args.green or args.rgb):
        args.green = args.rgb = True   # default: emit both

    if args.tonemap != "none":
        if not (args.tonemap_white > 0):
            sys.exit("--tonemap-white must be > 0.")
        if not args.rgb:
            args.rgb = True
            print("note: --tonemap needs RGB; enabling --rgb "
                  "(bilinear unless --method set).")

    if args.colour_gains is not None:
        if args.tonemap == "none":
            sys.exit("--colour-gains is a display-branch white balance and needs "
                     "--tonemap (it never touches the linear masters). Add "
                     "--tonemap reinhard to produce a white-balanced display.")
        if not all(g > 0 for g in args.colour_gains):
            sys.exit("--colour-gains values must be > 0.")

    ca_calib = None
    if args.ca_correct is not None:
        if args.tonemap == "none":
            sys.exit("--ca-correct is a display-branch correction and needs "
                     "--tonemap (it never touches the measurement masters).")
        if _ca is None:
            sys.exit("--ca-correct needs ca_lib.py next to debayer.py.")
        cp = Path(args.ca_correct)
        if not cp.is_file():
            sys.exit(f"--ca-correct calibration not found: {cp}")
        ca_calib = json.loads(cp.read_text())
        if ca_calib.get("model") != "constant_radial_scale":
            sys.exit(f"--ca-correct: unsupported model {ca_calib.get('model')!r}.")

    _fx = (args.shadow_deepen or args.clahe
           or args.local_contrast is not None or args.sharpen is not None)
    if _fx and args.tonemap == "none":
        sys.exit("display effects (--shadow-deepen / --clahe / --local-contrast / "
                 "--sharpen) run on the tone-mapped image and need --tonemap.")
    if args.shadow_deepen and not (0.0 <= args.sd_strength < 1.0):
        sys.exit("--sd-strength must be in [0, 1).")
    if (args.local_contrast is not None or args.sharpen is not None) and _gaussian_filter is None:
        sys.exit("--local-contrast / --sharpen need scipy (pip install scipy).")
    if args.clahe and _equalize_adapthist is None:
        sys.exit("--clahe needs scikit-image (pip install scikit-image).")

    inp = Path(args.input)
    if not inp.is_file():
        sys.exit(f"Input not found: {inp}")
    arr, src_desc = load_mosaic(inp)
    H, W = arr.shape
    print(f"Input: {inp.name}  {arr.shape} {arr.dtype}")

    is_float_in = np.issubdtype(arr.dtype, np.floating)
    if args.tonemap != "none" and not is_float_in and args.assume_linear is None:
        sys.exit(
            f"--tonemap expects a LINEAR float master (e.g. hdr_merge.py's float32 "
            f"output); got {arr.dtype}. Tone mapping an integer mosaic of unknown "
            f"encoding is a category error. If this IS linear (e.g. a raw DNG "
            f"average), assert it: --assume-linear WHITE_LEVEL (4095 for 12-bit "
            f"IMX477, or 65535 if scaled to full 16-bit) and use --tonemap-white 1.0.")
    if args.assume_linear is not None and not (args.assume_linear > 0):
        sys.exit("--assume-linear WHITE_LEVEL must be > 0.")

    if args.pattern == "auto":
        pattern, qm, note = detect_pattern(arr, args.swap_rb)
    else:
        pattern = args.pattern
        qm = quad_means(arr)
        note = (f"CFA pattern asserted: {pattern} (R/B is a physical fact of the "
                f"sensor, not measured here)" + (", R/B swapped" if args.swap_rb else ""))
    print(f"  quad means: { {str(k): round(v,1) for k,v in qm.items()} }")
    print(f"  CFA pattern: {pattern}  ({note})")

    try:
        src_prov = json.loads(src_desc) if src_desc else None
    except Exception:
        src_prov = None

    base_prov = {
        "software": "debayer.py",
        "version": __version__,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "source_file": inp.name,
        "cfa_pattern": pattern,
        "cfa_pattern_note": note,
        "quad_means": {f"{k[0]}{k[1]}": round(v, 2) for k, v in qm.items()},
        "source_provenance": src_prov,   # full lineage carried forward
    }
    if args.hash:
        base_prov["source_sha256"] = sha256_file(inp)

    stem = Path(args.output).with_suffix("") if args.output else inp.with_suffix("")
    both = args.green and args.rgb

    if args.green:
        plane, (gr, gc) = extract_green(arr, pattern, args.green_which)
        out = (stem.parent / (stem.name + "_green.tif")) if (both or not args.output) \
              else Path(args.output)
        print(f"[green] single-green plane, no interpolation -> {plane.shape}")
        write(out, plane, "minisblack", base_prov, {
            "transform": "single_green_extraction",
            "interpolation": "none",
            "green_position": [gr, gc],
            "green_which": args.green_which,
            "scale_space_note": "pixel pitch doubled each axis vs full sensor; "
                                "calibrate the stage micrometer on THIS plane.",
        })

    if args.rgb:
        if args.method == "bilinear":
            rgb_float = demosaic_bilinear(arr, pattern)
            interp = "bilinear (edge positions reconstructed)"
        else:
            rgb_float = demosaic_binned(arr, pattern)
            interp = "none except two-green average within each 2x2 quad"
        rgb = cast_like(rgb_float, arr.dtype)
        out = (stem.parent / (stem.name + "_rgb.tif")) if (both or not args.output) \
              else Path(args.output)
        print(f"[rgb] demosaic={args.method} -> {rgb.shape}")
        write(out, rgb, "rgb", base_prov, {
            "transform": f"demosaic_{args.method}",
            "interpolation": interp,
        })

        # ---- optional, SEPARATE display-referred tone map -------------------
        # The RGB measurement above is already written and is never altered.
        # This stage only ever writes an ADDITIONAL display file.
        if args.tonemap != "none":
            lw = args.tonemap_white

            # The display branch starts from the linear RGB; the measurement is
            # already written. White balance (if any) goes here, before tone map.
            if is_float_in or args.assume_linear is None:
                disp_lin = rgb_float
                lin_note = None
            else:
                wl = float(args.assume_linear)
                disp_lin = rgb_float / wl
                lin_note = {"asserted_linear": True, "white_level": wl,
                            "normalisation": "display = demosaiced_value / white_level",
                            "applies_to": "display branch only; integer measurements unchanged"}
                print(f"[assume-linear] integer input asserted linear; normalising "
                      f"display by white level {wl:g} (post-norm max "
                      f"{float(disp_lin.max()):.4g})")

            # Lateral CA: register R and B to green BEFORE white balance and tone
            # map (geometric resample must run in linear space). Green untouched.
            ca_prov = None
            if ca_calib is not None:
                ccx, ccy = ca_calib["optical_center_px"]
                mR = float(ca_calib["scale_red_over_green"])
                mB = float(ca_calib["scale_blue_over_green"])
                calib_shape = tuple(ca_calib.get("image_shape", disp_lin.shape[:2]))
                acx, acy, res_ratio = _ca.adapt_center(ccx, ccy, calib_shape, disp_lin.shape[:2])
                disp_lin = _ca.apply_ca_correction(disp_lin, acx, acy, mR, mB)
                msg = (f"[ca-correct] R/G={mR:.6f} B/G={mB:.6f} about "
                       f"({acx:.1f},{acy:.1f})")
                if res_ratio != 1.0:
                    msg += f"  [centre scaled x{res_ratio:.3f} for resolution change]"
                print(msg)
                ca_prov = {
                    "model": "constant_radial_scale",
                    "calibration": str(args.ca_correct),
                    "optical_center_px_applied": [acx, acy],
                    "scale_red_over_green": mR, "scale_blue_over_green": mB,
                    "resolution_ratio_vs_calib": res_ratio,
                    "applied_on": "display branch only, in linear space; green and "
                                  "measurement masters untouched",
                }

            wb_prov = None
            if args.colour_gains is not None:
                rg, bg = args.colour_gains
                disp_lin = apply_colour_gains(disp_lin, rg, bg)
                wb_max = float(disp_lin.max())
                print(f"[white balance] ColourGains R={rg:g} B={bg:g} (green=1.0); "
                      f"post-WB linear max={wb_max:.4g}")
                if wb_max > lw:
                    print(f"  note: post-WB max {wb_max:.4g} exceeds Lw={lw:g}; "
                          f"highlights will clip — raise --tonemap-white toward "
                          f"{wb_max:.3g} to keep them.")
                wb_prov = {
                    "method": "colour_gains (libcamera-style; R,B relative to green=1.0)",
                    "red_gain": rg, "blue_gain": bg,
                    "applied_on": "display branch only; linear masters are sensor-native",
                    "green_channel": "unmodified (green-plane morphometry unaffected)",
                    "post_wb_linear_max": wb_max,
                }

            if args.tonemap == "reinhard":
                disp = tonemap_reinhard(disp_lin, lw)
                op_name = "reinhard_extended_whitepoint"
                op_formula = ("Ld = L*(1 + L/Lw^2)/(1 + L); L the (optionally "
                              "white-balanced) linear RGB value, Lw -> 1.0")
            else:
                sys.exit(f"Unknown tonemap operator {args.tonemap!r}.")

            above_wp = int(np.count_nonzero(disp > 1.0))
            disp = np.clip(disp, 0.0, 1.0)   # to display [0,1] before display-space effects

            # ---- folded display-only effects (post-tonemap, on [0,1]) -------
            # Formerly shadow_deepen.py / micro_contrast.py. DISPLAY-ONLY: these
            # remap density and shift apparent edges. Never measure off the result.
            fx_prov = []
            if args.shadow_deepen:
                disp = shadow_deepen(disp, args.sd_pivot, args.sd_softness,
                                     args.sd_strength, args.sd_gamma)
                fx_prov.append({"effect": "shadow_deepen", "pivot": args.sd_pivot,
                                "softness": args.sd_softness, "strength": args.sd_strength,
                                "gamma": args.sd_gamma,
                                "curve": "out=(L*(1-strength*smoothstep_shadow(L)))**gamma"})
                print(f"[shadow-deepen] pivot={args.sd_pivot:g} strength={args.sd_strength:g}")
            if args.clahe:
                disp = stage_clahe(disp, args.clahe_clip, args.clahe_tiles)
                fx_prov.append({"effect": "clahe", "clip_limit": args.clahe_clip,
                                "tiles": args.clahe_tiles})
                print(f"[clahe] clip={args.clahe_clip:g} tiles={args.clahe_tiles}")
            if args.local_contrast is not None:
                disp = _unsharp(disp, args.local_contrast, args.local_contrast_amount, 0.0)
                fx_prov.append({"effect": "local_contrast_unsharp",
                                "radius_px": args.local_contrast,
                                "amount": args.local_contrast_amount, "band": "mid-frequency"})
                print(f"[local-contrast] r={args.local_contrast:g} amt={args.local_contrast_amount:g}")
            if args.sharpen is not None:
                disp = _unsharp(disp, args.sharpen, args.sharpen_amount, args.sharpen_threshold)
                fx_prov.append({"effect": "sharpen_unsharp", "radius_px": args.sharpen,
                                "amount": args.sharpen_amount,
                                "threshold": args.sharpen_threshold, "band": "high-frequency"})
                print(f"[sharpen] r={args.sharpen:g} amt={args.sharpen_amount:g}")
            if fx_prov:
                disp = np.clip(disp, 0.0, 1.0)   # effects can overshoot

            if args.tonemap_srgb:
                disp = srgb_oetf(disp)
            disp = np.clip(disp, 0.0, 1.0)

            tm_path = Path(args.tonemap_out) if args.tonemap_out \
                      else out.with_name(out.stem + "_display.tif")

            tm16 = np.clip(np.rint(disp * 65535.0), 0, 65535).astype(np.uint16)
            wb_tag = (f"WB(R={args.colour_gains[0]:g},B={args.colour_gains[1]:g}) + "
                      if args.colour_gains is not None else "")
            print(f"[tonemap] {wb_tag}{args.tonemap}, Lw={lw:g}"
                  f"{', sRGB OETF' if args.tonemap_srgb else ', no OETF'} "
                  f"-> display-referred")
            transform = (f"demosaic_{args.method}"
                         + (" + ca_correct" if ca_calib is not None else "")
                         + (" + colour_gains" if args.colour_gains is not None else "")
                         + f" + tonemap_{args.tonemap}"
                         + "".join(" + " + fx["effect"] for fx in fx_prov))
            write(tm_path, tm16, "rgb", base_prov, {
                "kind": "display-referred derivative (NOT a measurement)",
                "transform": transform,
                "interpolation": interp,
                "derived_from_rgb": str(out),
                "input_linearisation": lin_note,
                "ca_correction": ca_prov,
                "white_balance": wb_prov,
                "display_effects": fx_prov or None,
                "tonemap": {
                    "operator": op_name,
                    "formula": op_formula,
                    "white_point_Lw": lw,
                    "srgb_oetf": bool(args.tonemap_srgb),
                    "operates_on": ("white-balanced linear RGB" if args.colour_gains is not None
                                    else "demosaiced linear RGB (same scale as the linear master)"),
                    "values_above_Lw_clipped_px": above_wp,
                    "reversible": ("yes given Lw"
                                   + (", the ColourGains" if args.colour_gains is not None else "")
                                   + (" and the sRGB OETF" if args.tonemap_srgb else "")
                                   + "; the linear RGB / master is the source of truth"),
                },
            })
            if above_wp:
                print(f"  {above_wp} px exceeded white point Lw={lw:g} and were "
                      f"clipped to 1.0 (raise --tonemap-white to keep them).")

            if args.tonemap_8bit:
                tm_png = tm_path.with_suffix(".png")
                if _PILImage is None:
                    print("  8-bit PNG skipped: Pillow not installed "
                          "(pip install pillow). 16-bit TIFF written; convert later.",
                          file=sys.stderr)
                else:
                    tm8 = np.clip(np.rint(disp * 255.0), 0, 255).astype(np.uint8)
                    _PILImage.fromarray(tm8).save(tm_png)
                    print(f"  wrote {tm_png}  {tm8.shape} uint8")

    print("done.")


if __name__ == "__main__":
    main()
