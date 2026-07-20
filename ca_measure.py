#!/usr/bin/env python3
"""ca_measure.py - measure lateral chromatic aberration from a full-field target.

Fits a first-order lateral-CA model - optical centre (cx, cy) plus radial scales
m_R, m_B that register red and blue to GREEN - by minimising the warped-channel
residual against green over high-gradient edge pixels. Green is the reference and
is never altered. Outputs a calibration JSON (with a supersedes slot, like the
px/um chain) and, crucially, an INDEPENDENT offset-vs-radius table so you can see
the fit rather than trust it: each annulus's displacement is fit directly as a
radial shift (a 1-D search, not the global scale that was fitted), so agreement
between the two is a real cross-check, accurate even at the field edge.

Input must be a DEMOSAICED, LINEAR RGB image of a sharp, high-contrast target that
fills the field corner-to-corner (a grid, your stage micrometer, etc.). A single
centred spore will NOT do - it samples one narrow radial band and cannot constrain
a radial fit. This calibration is per optical configuration (objective + reducer);
shoot it in the configuration you will image in.

Model: constant_radial_scale. If the offset-vs-radius table curves away from the
fitted line at large radius, a higher-order term is warranted - report back and a
poly2 model (m(r) = 1 + c1 r^2 + c2 r^4) can be added.

Usage:
    python3 ca_measure.py target_rgb.tif -o ca_calib.json
    python3 ca_measure.py target_rgb.tif -o ca_calib.json --plot ca_fit.png
    python3 ca_measure.py target_rgb.tif -o ca_calib.json --supersedes ca_prev.json
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
    from .ca_lib import sample_at, __version__ as _lib_version
except ImportError:  # run directly as a script, not as a package module
    from ca_lib import sample_at, __version__ as _lib_version

__version__ = "1.0"

SCALE_LO, SCALE_HI = 0.99, 1.01      # +/-1% magnification search (CA is tiny)
CENTER_FRAC = 0.18                    # centre searched within +/-18% of mid
MAX_EDGE_PTS = 200_000               # cap edge pixels for speed
N_RADIAL_BINS = 12


def load_rgb(path):
    with tifffile.TiffFile(path) as tf:
        a = tf.pages[0].asarray()
    if a.ndim != 3 or a.shape[2] != 3:
        sys.exit(f"{path}: expected a demosaiced RGB image (H,W,3); got shape {a.shape}.")
    return a.astype(np.float64)


def sha256_file(path, _buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_buf), b""):
            h.update(chunk)
    return h.hexdigest()


def golden(f, a, b, tol=1e-5, itmax=80):
    g = (5 ** 0.5 - 1) / 2
    c = b - g * (b - a); d = a + g * (b - a)
    fc, fd = f(c), f(d); it = 0
    while (b - a) > tol and it < itmax:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a); fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a); fd = f(d)
        it += 1
    return 0.5 * (a + b)


def main():
    ap = argparse.ArgumentParser(description="Measure lateral CA from a full-field target.")
    ap.add_argument("input", help="demosaiced LINEAR RGB target image")
    ap.add_argument("-o", "--output", required=True, help="calibration JSON to write")
    ap.add_argument("--edge-percentile", type=float, default=90.0,
                    help="green-gradient percentile kept as edges (default 90)")
    ap.add_argument("--plot", default=None, metavar="PNG",
                    help="also write an offset-vs-radius plot (needs matplotlib)")
    ap.add_argument("--supersedes", default=None, metavar="JSON",
                    help="prior calibration this one replaces (recorded in trail)")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit(f"Input not found: {inp}")
    rgb = load_rgb(inp)
    H, W = rgb.shape[:2]
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    print(f"Input: {inp.name}  ({H}, {W}, 3)  reference=green")

    # --- edge mask + green radial-gradient (central differences) -------------
    gx = np.zeros_like(G); gy = np.zeros_like(G)
    gx[:, 1:-1] = 0.5 * (G[:, 2:] - G[:, :-2])
    gy[1:-1, :] = 0.5 * (G[2:, :] - G[:-2, :])
    mag = np.hypot(gx, gy)
    thr = np.percentile(mag, args.edge_percentile)
    ys_e, xs_e = np.nonzero(mag >= thr)
    if len(xs_e) < 500:
        sys.exit("Too few edge pixels - is the target high-contrast and in focus?")
    if len(xs_e) > MAX_EDGE_PTS:
        idx = np.random.default_rng(0).choice(len(xs_e), MAX_EDGE_PTS, replace=False)
        xs_e, ys_e = xs_e[idx], ys_e[idx]
    xs_e = xs_e.astype(np.float64); ys_e = ys_e.astype(np.float64)
    ge = G[ys_e.astype(int), xs_e.astype(int)]
    print(f"Edge pixels used: {len(xs_e)} (top {100 - args.edge_percentile:g}% of green gradient)")

    def chan_rms(ch, cx, cy, scale):
        sx = cx + scale * (xs_e - cx)
        sy = cy + scale * (ys_e - cy)
        d = sample_at(ch, sx, sy) - ge
        return float(np.sqrt(np.mean(d * d)))

    # --- coordinate-descent fit: scales, then centre, iterate ----------------
    cx, cy = W / 2.0, H / 2.0
    m_r, m_b = 1.0, 1.0
    for _ in range(4):
        m_r = golden(lambda s: chan_rms(R, cx, cy, s), SCALE_LO, SCALE_HI)
        m_b = golden(lambda s: chan_rms(B, cx, cy, s), SCALE_LO, SCALE_HI)
        cx = golden(lambda x: chan_rms(R, x, cy, m_r) + chan_rms(B, x, cy, m_b),
                    W * (0.5 - CENTER_FRAC), W * (0.5 + CENTER_FRAC))
        cy = golden(lambda y: chan_rms(R, cx, y, m_r) + chan_rms(B, cx, y, m_b),
                    H * (0.5 - CENTER_FRAC), H * (0.5 + CENTER_FRAC))

    rms_r_before, rms_r_after = chan_rms(R, cx, cy, 1.0), chan_rms(R, cx, cy, m_r)
    rms_b_before, rms_b_after = chan_rms(B, cx, cy, 1.0), chan_rms(B, cx, cy, m_b)

    print(f"\nFit (constant radial scale, centre free):")
    print(f"  optical centre (px):  ({cx:.1f}, {cy:.1f})   "
          f"[image centre ({W/2:.1f}, {H/2:.1f})]")
    print(f"  scale R/G:  {m_r:.6f}   ({(m_r-1)*1e2:+.4f}% magnification vs green)")
    print(f"  scale B/G:  {m_b:.6f}   ({(m_b-1)*1e2:+.4f}% magnification vs green)")
    print(f"  residual RMS R-vs-G:  {rms_r_before:.5g} -> {rms_r_after:.5g}  "
          f"({100*(1-rms_r_after/max(rms_r_before,1e-12)):.0f}% lower)")
    print(f"  residual RMS B-vs-G:  {rms_b_before:.5g} -> {rms_b_after:.5g}  "
          f"({100*(1-rms_b_after/max(rms_b_before,1e-12)):.0f}% lower)")

    # --- INDEPENDENT per-annulus radial-shift cross-check --------------------
    # For each radial annulus, fit the single radial shift (px) that best aligns
    # R (and B) to green. Independent of the global scale fit, and - unlike a
    # first-order differential estimate - accurate at large displacement, so the
    # edges (where higher-order CA shows up) are trustworthy. Measured shifts on
    # a straight line through the origin => constant-scale model holds; curvature
    # at large radius argues for a poly2 term.
    r_all = np.hypot(xs_e - cx, ys_e - cy)
    rxh = (xs_e - cx) / np.maximum(r_all, 1e-6)
    ryh = (ys_e - cy) / np.maximum(r_all, 1e-6)
    edges = np.linspace(0, r_all.max(), N_RADIAL_BINS + 1)
    table = []
    for i in range(N_RADIAL_BINS):
        sel = (r_all >= edges[i]) & (r_all < edges[i + 1])
        if sel.sum() < 50:
            continue
        xb, yb, gb = xs_e[sel], ys_e[sel], ge[sel]
        rxb, ryb = rxh[sel], ryh[sel]

        def shift_rms(ch, d):
            e = sample_at(ch, xb + d * rxb, yb + d * ryb) - gb
            return float(np.mean(e * e))

        dR = golden(lambda d: shift_rms(R, d), -4.0, 4.0, tol=1e-3)
        dB = golden(lambda d: shift_rms(B, d), -4.0, 4.0, tol=1e-3)
        rr = float(r_all[sel].mean())
        table.append({"radius_px": rr, "n": int(sel.sum()),
                      "dR_measured": dR, "dR_predicted": (m_r - 1) * rr,
                      "dB_measured": dB, "dB_predicted": (m_b - 1) * rr})

    print(f"\nOffset vs radius  (measured = per-annulus radial-shift fit, "
          f"independent of\nthe scale fit; predicted = (m-1)*r. Lines should agree; "
          f"curvature => poly2.)")
    print(f"  {'radius':>7} {'n':>7} | {'dR meas':>8} {'dR pred':>8} | "
          f"{'dB meas':>8} {'dB pred':>8}   (px)")
    for t in table:
        print(f"  {t['radius_px']:7.0f} {t['n']:7d} | {t['dR_measured']:8.3f} "
              f"{t['dR_predicted']:8.3f} | {t['dB_measured']:8.3f} {t['dB_predicted']:8.3f}")

    # --- optional plot -------------------------------------------------------
    if args.plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            print("  (plot skipped: matplotlib not installed)", file=sys.stderr)
        else:
            rr = [t["radius_px"] for t in table]
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.scatter(rr, [t["dR_measured"] for t in table], c="r", s=18, label="R measured")
            ax.scatter(rr, [t["dB_measured"] for t in table], c="b", s=18, label="B measured")
            rl = np.linspace(0, max(rr), 50)
            ax.plot(rl, (m_r - 1) * rl, "r-", lw=1, label=f"R fit (m={m_r:.5f})")
            ax.plot(rl, (m_b - 1) * rl, "b-", lw=1, label=f"B fit (m={m_b:.5f})")
            ax.axhline(0, color="k", lw=0.5)
            ax.set_xlabel("radius from optical centre (px)")
            ax.set_ylabel("radial offset vs green (px)")
            ax.set_title("Lateral CA: measured offset vs fitted model")
            ax.legend(fontsize=8); fig.tight_layout()
            fig.savefig(args.plot, dpi=120)
            print(f"\nWrote plot {args.plot}")

    # --- calibration JSON ----------------------------------------------------
    calib = {
        "software": "ca_measure.py",
        "version": __version__,
        "ca_lib_version": _lib_version,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "kind": "lateral chromatic aberration registration (per optical configuration)",
        "model": "constant_radial_scale",
        "reference_channel": "green",
        "image_shape": [H, W],
        "optical_center_px": [cx, cy],
        "scale_red_over_green": m_r,
        "scale_blue_over_green": m_b,
        "residual_rms": {
            "R_vs_G_before": rms_r_before, "R_vs_G_after": rms_r_after,
            "B_vs_G_before": rms_b_before, "B_vs_G_after": rms_b_after,
        },
        "edge_pixels_used": int(len(xs_e)),
        "edge_percentile": args.edge_percentile,
        "offset_vs_radius": table,
        "source_target": {"path": str(inp), "sha256": sha256_file(inp), "shape": [H, W]},
        "optical_config_note": ("valid only for the objective + reducer + tube this "
                                "was shot with; re-measure if any optic changes"),
        "supersedes": str(args.supersedes) if args.supersedes else None,
    }
    Path(args.output).write_text(json.dumps(calib, indent=2))
    print(f"\nWrote {args.output}")
    print("Apply with:  debayer.py <master> --rgb --ca-correct "
          f"{args.output} --tonemap reinhard ...")


if __name__ == "__main__":
    main()
