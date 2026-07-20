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
fitted line at large radius, a higher-order term is warranted - the wizard's
review page (and the CLI's own printed table) surface this via poly2_flag(), but
no poly2 model is implemented anywhere yet (debayer.py --ca-correct only accepts
model == "constant_radial_scale"); report back and one can be added.

Two ways to run:
    python3 ca_measure.py target_rgb.tif -o ca_calib.json
    python3 ca_measure.py target_rgb.tif -o ca_calib.json --plot ca_fit.png
    python3 ca_measure.py target_rgb.tif -o ca_calib.json --supersedes ca_prev.json
    python3 ca_measure.py --wizard        the interactive setup wizard (build
                                          checklist section 4): pick an
                                          objective, get an image (existing
                                          file or a fresh live capture), review
                                          the fit (including the poly2 flag),
                                          then save to a central, per-objective,
                                          supersedes-chained store
                                          (~/.zynergy/ca_calibration.json) --
                                          separate from the -o/--supersedes
                                          flags above, which still just write a
                                          standalone file exactly as before.
    python3 ca_measure.py --render-check  headless: fit_lateral_ca recovers a
                                          known injected CA shift, poly2_flag's
                                          both branches, and the calibration
                                          store's supersedes chain, no PyQt5,
                                          no image file.
"""
import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import numpy as np
import tifffile

try:
    from .ca_lib import sample_at, __version__ as _lib_version
except ImportError:  # run directly as a script, not as a package module
    from ca_lib import sample_at, __version__ as _lib_version

# calibrate.py's own DEFAULT_OBJECTIVES / DEFAULT_CFA_PATTERN / resolve_raw_path,
# reused by the wizard rather than a second copy of any of them -- the same
# fixed hardware convention (BGGR) and the same .jpg-resolves-to-sibling-.dng
# rule every other tool here already follows.
try:
    from . import calibrate as _calibrate
except ImportError:
    try:
        import calibrate as _calibrate
    except ImportError:
        _calibrate = None

# debayer.demosaic_bilinear, so a freshly-shot raw mosaic (from the wizard's
# live-capture page) can be turned into the demosaiced linear RGB this file's
# own math needs, without a second demosaic implementation.
try:
    from . import debayer as _debayer
except ImportError:
    try:
        import debayer as _debayer
    except ImportError:
        _debayer = None

# The shared image-source wizard page (build checklist section 4), the same
# one calibrate.py's and measure.py's wizards use.
try:
    from . import wizard_pages as _wizard_pages
except ImportError:
    try:
        import wizard_pages as _wizard_pages
    except ImportError:
        _wizard_pages = None

__version__ = "1.0"

SCALE_LO, SCALE_HI = 0.99, 1.01      # +/-1% magnification search (CA is tiny)
CENTER_FRAC = 0.18                    # centre searched within +/-18% of mid
MAX_EDGE_PTS = 200_000               # cap edge pixels for speed
N_RADIAL_BINS = 12

CA_CALIBRATION_PATH = Path.home() / ".zynergy" / "ca_calibration.json"


# ---------------------------------------------------------------------------
# Pure logic (Qt-free, testable under --render-check)
# ---------------------------------------------------------------------------
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


def fit_lateral_ca(rgb, edge_percentile=90.0):
    """Fit the constant_radial_scale lateral-CA model on a demosaiced LINEAR
    RGB target, plus the independent per-annulus offset-vs-radius cross-check
    -- the exact math the CLI has always run, extracted so the wizard calls
    the same code instead of a second copy of it. Pure: no file I/O, and
    raises ValueError (not sys.exit) on too few edge pixels, the same
    CLI-vs-GUI-safe rule calibrate.py's own load_mosaic_array already
    established over debayer.py's sys.exit-based load_mosaic -- a dim or
    out-of-focus target should not be able to kill a live wizard.

    Returns a dict: image_shape, optical_center_px, scale_red_over_green,
    scale_blue_over_green, residual_rms, edge_pixels_used, edge_percentile,
    offset_vs_radius (the same fields the CLI's own calibration JSON and
    printed report both need).
    """
    H, W = rgb.shape[:2]
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    # --- edge mask + green radial-gradient (central differences) -----------
    gx = np.zeros_like(G); gy = np.zeros_like(G)
    gx[:, 1:-1] = 0.5 * (G[:, 2:] - G[:, :-2])
    gy[1:-1, :] = 0.5 * (G[2:, :] - G[:-2, :])
    mag = np.hypot(gx, gy)
    thr = np.percentile(mag, edge_percentile)
    ys_e, xs_e = np.nonzero(mag >= thr)
    if len(xs_e) < 500:
        raise ValueError(
            "Too few edge pixels ({}) -- is the target high-contrast and in "
            "focus?".format(len(xs_e)))
    if len(xs_e) > MAX_EDGE_PTS:
        idx = np.random.default_rng(0).choice(len(xs_e), MAX_EDGE_PTS, replace=False)
        xs_e, ys_e = xs_e[idx], ys_e[idx]
    xs_e = xs_e.astype(np.float64); ys_e = ys_e.astype(np.float64)
    ge = G[ys_e.astype(int), xs_e.astype(int)]

    def chan_rms(ch, cx, cy, scale):
        sx = cx + scale * (xs_e - cx)
        sy = cy + scale * (ys_e - cy)
        d = sample_at(ch, sx, sy) - ge
        return float(np.sqrt(np.mean(d * d)))

    # --- coordinate-descent fit: scales, then centre, iterate ---------------
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

    # --- INDEPENDENT per-annulus radial-shift cross-check -------------------
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

    return {
        "image_shape": [H, W],
        "optical_center_px": [cx, cy],
        "scale_red_over_green": m_r,
        "scale_blue_over_green": m_b,
        "residual_rms": {
            "R_vs_G_before": rms_r_before, "R_vs_G_after": rms_r_after,
            "B_vs_G_before": rms_b_before, "B_vs_G_after": rms_b_after,
        },
        "edge_pixels_used": int(len(xs_e)),
        "edge_percentile": float(edge_percentile),
        "offset_vs_radius": table,
    }


def format_offset_table(table):
    """The exact offset-vs-radius table layout the CLI has always printed,
    factored out so the wizard's review page shows identical numbers in an
    identical layout, not a second formatting of the same data."""
    lines = ["  {:>7} {:>7} | {:>8} {:>8} | {:>8} {:>8}   (px)".format(
        "radius", "n", "dR meas", "dR pred", "dB meas", "dB pred")]
    for t in table:
        lines.append("  {:7.0f} {:7d} | {:8.3f} {:8.3f} | {:8.3f} {:8.3f}".format(
            t["radius_px"], t["n"], t["dR_measured"], t["dR_predicted"],
            t["dB_measured"], t["dB_predicted"]))
    return "\n".join(lines)


def render_ca_plot(table, m_r, m_b, out_path):
    """The offset-vs-radius plot, factored out of the CLI's own --plot block
    so the wizard's review page can generate and embed the identical plot
    rather than a second implementation of it. Raises ImportError if
    matplotlib is not installed -- each caller decides what an optional plot
    means for it (the CLI skips with a stderr note; the wizard's review page
    shows a plain text note instead of an image)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rr = [t["radius_px"] for t in table]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(rr, [t["dR_measured"] for t in table], c="r", s=18, label="R measured")
    ax.scatter(rr, [t["dB_measured"] for t in table], c="b", s=18, label="B measured")
    rl = np.linspace(0, max(rr), 50)
    ax.plot(rl, (m_r - 1) * rl, "r-", lw=1, label="R fit (m={:.5f})".format(m_r))
    ax.plot(rl, (m_b - 1) * rl, "b-", lw=1, label="B fit (m={:.5f})".format(m_b))
    ax.axhline(0, color="k", lw=0.5)
    ax.set_xlabel("radius from optical centre (px)")
    ax.set_ylabel("radial offset vs green (px)")
    ax.set_title("Lateral CA: measured offset vs fitted model")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(str(out_path), dpi=120)
    plt.close(fig)


def poly2_flag(offset_vs_radius, rel_tol=0.15, min_abs_px=0.5):
    """Whether the offset-vs-radius table's OUTERMOST annulus (the table's own
    docstring already calls out "curves away from the fitted line at large
    radius" as the poly2 signal) departs from the fitted constant_radial_scale
    prediction by more than rel_tol of the predicted shift, and at least
    min_abs_px (so a near-zero predicted shift near the centre can never trip
    this on noise alone). Evidence only, never a gate -- the same "recorded
    honestly, never hidden, never blocking" rule calibrate.py's own focus
    score already follows: a flagged calibration still saves and applies
    exactly as constant_radial_scale, since no poly2 model exists anywhere
    yet to apply instead (debayer.py --ca-correct only accepts
    model == "constant_radial_scale"). Returns (flagged, detail).
    """
    if not offset_vs_radius:
        return False, "no annuli to check (offset_vs_radius is empty)"
    outer = offset_vs_radius[-1]
    for ch in ("R", "B"):
        meas = outer["d{}_measured".format(ch)]
        pred = outer["d{}_predicted".format(ch)]
        dev = abs(meas - pred)
        if dev >= min_abs_px and dev >= rel_tol * max(abs(pred), 1e-9):
            return True, (
                "outer annulus (r={:.0f}px): {} measured {:.3f}px vs predicted "
                "{:.3f}px (off by {:.3f}px) -- the fit may be under-fitting at "
                "the field edge; a poly2 model would need to be added to "
                "debayer.py/ca_lib.py to correct this (not yet implemented)"
                .format(outer["radius_px"], ch, meas, pred, dev))
    return False, "outer annulus tracks the fitted line within tolerance"


# ---------------------------------------------------------------------------
# Central, supersedes-chained CA calibration store (build checklist section
# 3): mirrors calibrate.py's own store exactly (append-only, entry_id +
# supersedes chain, atomic write) -- a different path/key, not a second
# design. Additive: the CLI's own -o/--supersedes flags are untouched and
# keep writing a standalone file exactly as before; this is the wizard's own
# save path.
# ---------------------------------------------------------------------------
def load_ca_calibrations():
    """The whole per-objective CA calibration store: {objective: [entry, ...]}.
    {} if none saved yet or the file is unreadable, never raises."""
    try:
        return json.loads(CA_CALIBRATION_PATH.read_text())
    except Exception:
        return {}


def current_ca_calibration(objective, store=None):
    """The active CA calibration for an objective: the LAST entry in its
    history, or None if it has never been calibrated."""
    store = store if store is not None else load_ca_calibrations()
    history = store.get(objective) or []
    return history[-1] if history else None


def save_ca_calibration(objective, entry):
    """Append-only: adds a new entry to the objective's history and chains
    'supersedes' to whatever was current before -- a redo is a new entry, not
    a correction to the old one, so nothing already saved is ever edited or
    removed. Same atomic-write pattern (temp file, then rename) as
    calibrate.py's own store."""
    CA_CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    store = load_ca_calibrations()
    history = store.setdefault(objective, [])
    prior = history[-1] if history else None
    entry = dict(entry)
    entry["entry_id"] = uuid.uuid4().hex
    entry["supersedes"] = prior["entry_id"] if prior else None
    history.append(entry)
    tmp = CA_CALIBRATION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    os.replace(tmp, CA_CALIBRATION_PATH)
    return store


def build_ca_calibration_entry(fit_result, objective, source_path, now=None):
    """The full record saved for one objective's CA calibration: the fit
    result plus enough provenance to audit it later (objective, source target
    image + hash) and the poly2 flag computed from the fit's own
    offset-vs-radius table -- the checklist's own "poly2-flag step", recorded
    honestly rather than only ever shown once and forgotten."""
    flagged, detail = poly2_flag(fit_result["offset_vs_radius"])
    source_path = Path(source_path)
    return {
        "software": "ca_measure.py",
        "version": __version__,
        "ca_lib_version": _lib_version,
        "created_utc": (now or _dt.datetime.now(_dt.timezone.utc)).isoformat(timespec="seconds"),
        "kind": "lateral chromatic aberration registration (per optical configuration)",
        "model": "constant_radial_scale",
        "reference_channel": "green",
        "objective": objective,
        "image_shape": fit_result["image_shape"],
        "optical_center_px": fit_result["optical_center_px"],
        "scale_red_over_green": fit_result["scale_red_over_green"],
        "scale_blue_over_green": fit_result["scale_blue_over_green"],
        "residual_rms": fit_result["residual_rms"],
        "edge_pixels_used": fit_result["edge_pixels_used"],
        "edge_percentile": fit_result["edge_percentile"],
        "offset_vs_radius": fit_result["offset_vs_radius"],
        "poly2_flagged": flagged,
        "poly2_detail": detail,
        "source_target": {
            "path": str(source_path),
            "sha256": sha256_file(source_path) if source_path.is_file() else None,
            "shape": fit_result["image_shape"],
        },
        "optical_config_note": ("valid only for the objective + reducer + tube this "
                                "was shot with; re-measure if any optic changes"),
    }


def export_ca_calibration(entry, out_path):
    """Write one CA calibration store entry back out as a standalone JSON
    debayer.py --ca-correct can point at directly. entry_id/supersedes are
    the central store's own audit-chain bookkeeping (see save_ca_calibration)
    and are dropped here: the full history already lives in the store
    itself, and an exported file is just the numbers --ca-correct actually
    reads (it only ever checks 'model')."""
    out = {k: v for k, v in entry.items() if k not in ("entry_id", "supersedes")}
    Path(out_path).write_text(json.dumps(out, indent=2))
    return out


def load_rgb_or_mosaic(path):
    """The wizard's own image loader: EITHER an already-demosaiced (H,W,3)
    linear RGB TIFF (the CLI's normal input, used as-is) OR a raw single-
    channel mosaic (a fresh capture, or any raw .dng picked directly) which
    gets demosaiced in-memory via debayer.demosaic_bilinear -- the project's
    one shared demosaic implementation, using the fixed BGGR convention
    (calibrate.DEFAULT_CFA_PATTERN) every other tool here already assumes
    rather than a per-calibration choice. Raises ValueError (never sys.exit
    or a bare crash) for anything else, so a bad pick cannot kill the wizard.
    """
    if _debayer is None or _calibrate is None:
        raise RuntimeError(
            "calibrate.py and debayer.py must both be importable next to "
            "this file for the wizard's image step.")
    with tifffile.TiffFile(str(path)) as tf:
        arr = tf.pages[0].asarray()
    if arr.ndim == 3 and arr.shape[2] == 3:
        return arr.astype(np.float64)
    if arr.ndim == 2:
        return _debayer.demosaic_bilinear(arr, _calibrate.DEFAULT_CFA_PATTERN)
    raise ValueError(
        "{} has shape {}; expected a demosaiced (H,W,3) RGB target or a "
        "single-channel raw mosaic.".format(Path(path).name, arr.shape))


# ---------------------------------------------------------------------------
# Qt-bound wizard (build checklist section 4)
# ---------------------------------------------------------------------------
try:
    from PyQt5.QtWidgets import (QApplication, QWizard, QWizardPage, QWidget,
                                 QLabel, QVBoxLayout, QHBoxLayout, QPushButton,
                                 QComboBox, QDoubleSpinBox, QFileDialog,
                                 QMessageBox)
    from PyQt5.QtGui import QPixmap
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


if _HAVE_QT:

    class _SetupPage(QWizardPage):
        """Wizard page 1: objective + edge-percentile. Next enabled once an
        objective string is chosen. Shows the existing CA calibration for it
        via current_ca_calibration, reused rather than a second lookup."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setTitle("Objective")
            self.setSubTitle("Pick the objective this CA calibration is for.")

            self.objective_combo = QComboBox()
            self.objective_combo.setEditable(True)
            for obj in (getattr(_calibrate, "DEFAULT_OBJECTIVES", None)
                       or ["4x", "10x", "40x", "100x"]):
                self.objective_combo.addItem(obj)
            self.objective_combo.currentTextChanged.connect(self._on_changed)

            self.edge_pct = QDoubleSpinBox()
            self.edge_pct.setRange(0.0, 100.0)
            self.edge_pct.setDecimals(1)
            self.edge_pct.setValue(90.0)

            self.existing_label = QLabel("")
            self.existing_label.setWordWrap(True)

            lay = QVBoxLayout(self)
            lay.addWidget(QLabel("Objective:"))
            lay.addWidget(self.objective_combo)
            lay.addWidget(QLabel("Edge percentile (green-gradient percentile "
                                 "kept as edges):"))
            lay.addWidget(self.edge_pct)
            lay.addWidget(self.existing_label)
            self._refresh_existing()

        def _on_changed(self, _text):
            self._refresh_existing()
            self.completeChanged.emit()

        def _refresh_existing(self):
            obj = self.objective_combo.currentText().strip()
            entry = current_ca_calibration(obj) if obj else None
            if entry:
                n = len(load_ca_calibrations().get(obj, []))
                flag_note = " (poly2-flagged)" if entry.get("poly2_flagged") else ""
                self.existing_label.setText(
                    "Current CA calibration for {}: R/G={:.6f} B/G={:.6f} "
                    "(set {}, #{} in history){}".format(
                        obj, entry["scale_red_over_green"],
                        entry["scale_blue_over_green"],
                        entry.get("created_utc", "unknown date"), n, flag_note))
            else:
                self.existing_label.setText(
                    "No CA calibration saved yet for {}.".format(obj or "(no objective set)"))

        def isComplete(self):
            return bool(self.objective_combo.currentText().strip())

        def objective(self):
            return self.objective_combo.currentText().strip()

        def edge_percentile(self):
            return float(self.edge_pct.value())


    class _ReviewPage(QWizardPage):
        """Wizard page 3: review the fit before saving -- the checklist's own
        "review/accept/redo/poly2-flag step". Re-renders every time it
        becomes current (initializePage), so Back -> reshoot/repick -> Next
        always shows the CURRENT fit, never a stale one. "Redo" is just the
        wizard's own stock Back button; Finish is Accept & Save."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setTitle("Review")
            self.setSubTitle("Check the fit, then Finish to save, or Back to redo.")

            self.summary_label = QLabel("")
            self.summary_label.setWordWrap(True)
            self.flag_label = QLabel("")
            self.flag_label.setWordWrap(True)
            self.table_label = QLabel("")
            self.table_label.setWordWrap(False)
            self.plot_label = QLabel("")
            self.plot_label.setWordWrap(True)

            export_btn = QPushButton("Export for debayer.py --ca-correct...")
            export_btn.clicked.connect(self._on_export)

            lay = QVBoxLayout(self)
            lay.addWidget(self.summary_label)
            lay.addWidget(self.flag_label)
            lay.addWidget(self.table_label)
            lay.addWidget(self.plot_label)
            lay.addWidget(export_btn)

        def initializePage(self):
            wiz = self.wizard()
            result = wiz.fit_result
            W, H = result["image_shape"][1], result["image_shape"][0]
            cx, cy = result["optical_center_px"]
            m_r = result["scale_red_over_green"]
            m_b = result["scale_blue_over_green"]
            rms = result["residual_rms"]
            self.summary_label.setText(
                "Objective: {}\n"
                "optical centre (px): ({:.1f}, {:.1f})  [image centre ({:.1f}, {:.1f})]\n"
                "scale R/G: {:.6f}  ({:+.4f}% magnification vs green)\n"
                "scale B/G: {:.6f}  ({:+.4f}% magnification vs green)\n"
                "residual RMS R-vs-G: {:.5g} -> {:.5g}\n"
                "residual RMS B-vs-G: {:.5g} -> {:.5g}\n"
                "edge pixels used: {}".format(
                    wiz.setup_page.objective(), cx, cy, W / 2.0, H / 2.0,
                    m_r, (m_r - 1) * 1e2, m_b, (m_b - 1) * 1e2,
                    rms["R_vs_G_before"], rms["R_vs_G_after"],
                    rms["B_vs_G_before"], rms["B_vs_G_after"],
                    result["edge_pixels_used"]))
            flagged, detail = poly2_flag(result["offset_vs_radius"])
            self.flag_label.setText(
                ("POLY2 FLAG: " if flagged else "poly2 check: ") + detail)
            self.table_label.setText(format_offset_table(result["offset_vs_radius"]))
            self._render_plot(result, m_r, m_b)

        def _render_plot(self, result, m_r, m_b):
            try:
                import tempfile
                path = Path(tempfile.mkdtemp()) / "ca_review_plot.png"
                render_ca_plot(result["offset_vs_radius"], m_r, m_b, path)
            except ImportError:
                self.plot_label.setText(
                    "(plot skipped: matplotlib not installed -- the table "
                    "above has the same numbers)")
                return
            pix = QPixmap(str(path))
            self.plot_label.setText("")
            self.plot_label.setPixmap(pix)

        def isComplete(self):
            return self.wizard().fit_result is not None

        def _on_export(self):
            wiz = self.wizard()
            entry = build_ca_calibration_entry(
                wiz.fit_result, wiz.setup_page.objective(), wiz.source_path)
            path, _ = QFileDialog.getSaveFileName(
                self, "Export CA calibration for debayer.py --ca-correct",
                "ca_calib.json", "JSON (*.json)")
            if path:
                export_ca_calibration(entry, path)
                QMessageBox.information(
                    self, "Exported", "Wrote {}\n\nApply with:  debayer.py "
                    "<master> --rgb --ca-correct {} --tonemap reinhard ..."
                    .format(path, path))


    class CAWizard(QWizard):
        """The CA calibration wizard (build checklist section 4): page 1
        picks the objective (+ edge percentile), page 2 gets an image via the
        shared wizard_pages.ImageSourcePage (an existing target file, or a
        fresh live capture -- demosaiced in-memory if it's a raw mosaic),
        page 3 reviews the fit (including the poly2 flag) before Finish
        saves it to the central store. No persistent canvas to hand off to
        afterward: unlike spatial calibration or measurement, a CA
        calibration is a single measure-review-save action, not an ongoing
        session.
        """

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Zynergy CA calibration - setup")
            if _wizard_pages is None:
                raise RuntimeError(
                    "wizard_pages.py could not be imported; needed for the "
                    "image-source page")
            self.fit_result = None
            self.source_path = None

            self.setup_page = _SetupPage()
            self.image_page = _wizard_pages.ImageSourcePage(self._validate_image)
            self.image_page.setSubTitle(
                self.image_page.subTitle() + " Needs a sharp, high-contrast "
                "target that fills the field corner-to-corner (a grid, a "
                "stage micrometer) -- a single centred spore will not do.")
            self.review_page = _ReviewPage()
            self.addPage(self.setup_page)
            self.addPage(self.image_page)
            self.addPage(self.review_page)
            self.finished.connect(lambda _res: self.image_page.capture_pane.stop())

        def _validate_image(self, path):
            try:
                resolved = (_calibrate.resolve_raw_path(path)
                           if _calibrate is not None else Path(path))
                rgb = load_rgb_or_mosaic(resolved)
            except (ValueError, RuntimeError) as exc:
                return False, str(exc)
            except Exception as exc:
                return False, "Failed to read {}: {}".format(Path(path).name, exc)
            try:
                result = fit_lateral_ca(rgb, edge_percentile=self.setup_page.edge_percentile())
            except ValueError as exc:
                return False, str(exc)
            self.fit_result = result
            self.source_path = resolved
            flagged, detail = poly2_flag(result["offset_vs_radius"])
            msg = ("Fit OK: scale R/G={:.6f} B/G={:.6f}, {} edge pixels used.\n"
                  "{}".format(result["scale_red_over_green"],
                              result["scale_blue_over_green"],
                              result["edge_pixels_used"], detail))
            return True, msg


    def run_wizard():
        app = QApplication(sys.argv)
        wiz = CAWizard()
        wiz.resize(900, 700)
        if wiz.exec_() != QWizard.Accepted:
            return
        objective = wiz.setup_page.objective()
        entry = build_ca_calibration_entry(wiz.fit_result, objective, wiz.source_path)
        save_ca_calibration(objective, entry)
        QMessageBox.information(
            None, "Saved", "Saved CA calibration for {}: R/G={:.6f} B/G={:.6f}"
            .format(objective, entry["scale_red_over_green"],
                    entry["scale_blue_over_green"]))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure lateral CA from a full-field target.")
    ap.add_argument("input", nargs="?", default=None,
                    help="demosaiced LINEAR RGB target image")
    ap.add_argument("-o", "--output", default=None, help="calibration JSON to write")
    ap.add_argument("--edge-percentile", type=float, default=90.0,
                    help="green-gradient percentile kept as edges (default 90)")
    ap.add_argument("--plot", default=None, metavar="PNG",
                    help="also write an offset-vs-radius plot (needs matplotlib)")
    ap.add_argument("--supersedes", default=None, metavar="JSON",
                    help="prior calibration this one replaces (recorded in trail)")
    ap.add_argument("--wizard", action="store_true",
                    help="run the interactive CA calibration wizard instead "
                         "of the CLI fit")
    a = ap.parse_args(argv)

    if a.wizard:
        if not _HAVE_QT:
            sys.exit("PyQt5 not available. Use --render-check for the headless "
                     "self-check, or install python3-pyqt5 for --wizard.")
        run_wizard()
        return

    if a.input is None or a.output is None:
        ap.error("input and -o/--output are required unless --wizard is given")

    inp = Path(a.input)
    if not inp.exists():
        sys.exit(f"Input not found: {inp}")
    rgb = load_rgb(inp)
    H, W = rgb.shape[:2]
    print(f"Input: {inp.name}  ({H}, {W}, 3)  reference=green")

    try:
        result = fit_lateral_ca(rgb, edge_percentile=a.edge_percentile)
    except ValueError as exc:
        sys.exit(str(exc))

    cx, cy = result["optical_center_px"]
    m_r = result["scale_red_over_green"]
    m_b = result["scale_blue_over_green"]
    rms = result["residual_rms"]
    table = result["offset_vs_radius"]

    print(f"Edge pixels used: {result['edge_pixels_used']} "
          f"(top {100 - a.edge_percentile:g}% of green gradient)")
    print(f"\nFit (constant radial scale, centre free):")
    print(f"  optical centre (px):  ({cx:.1f}, {cy:.1f})   "
          f"[image centre ({W/2:.1f}, {H/2:.1f})]")
    print(f"  scale R/G:  {m_r:.6f}   ({(m_r-1)*1e2:+.4f}% magnification vs green)")
    print(f"  scale B/G:  {m_b:.6f}   ({(m_b-1)*1e2:+.4f}% magnification vs green)")
    print(f"  residual RMS R-vs-G:  {rms['R_vs_G_before']:.5g} -> {rms['R_vs_G_after']:.5g}  "
          f"({100*(1-rms['R_vs_G_after']/max(rms['R_vs_G_before'],1e-12)):.0f}% lower)")
    print(f"  residual RMS B-vs-G:  {rms['B_vs_G_before']:.5g} -> {rms['B_vs_G_after']:.5g}  "
          f"({100*(1-rms['B_vs_G_after']/max(rms['B_vs_G_before'],1e-12)):.0f}% lower)")

    print(f"\nOffset vs radius  (measured = per-annulus radial-shift fit, "
          f"independent of\nthe scale fit; predicted = (m-1)*r. Lines should agree; "
          f"curvature => poly2.)")
    print(format_offset_table(table))
    flagged, detail = poly2_flag(table)
    print("\n{}: {}".format("POLY2 FLAG" if flagged else "poly2 check", detail))

    if a.plot:
        try:
            render_ca_plot(table, m_r, m_b, a.plot)
        except ImportError:
            print("  (plot skipped: matplotlib not installed)", file=sys.stderr)
        else:
            print(f"\nWrote plot {a.plot}")

    calib = {
        "software": "ca_measure.py",
        "version": __version__,
        "ca_lib_version": _lib_version,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "kind": "lateral chromatic aberration registration (per optical configuration)",
        "model": "constant_radial_scale",
        "reference_channel": "green",
        "image_shape": result["image_shape"],
        "optical_center_px": [cx, cy],
        "scale_red_over_green": m_r,
        "scale_blue_over_green": m_b,
        "residual_rms": rms,
        "edge_pixels_used": result["edge_pixels_used"],
        "edge_percentile": a.edge_percentile,
        "offset_vs_radius": table,
        "source_target": {"path": str(inp), "sha256": sha256_file(inp), "shape": [H, W]},
        "optical_config_note": ("valid only for the objective + reducer + tube this "
                                "was shot with; re-measure if any optic changes"),
        "supersedes": str(a.supersedes) if a.supersedes else None,
    }
    Path(a.output).write_text(json.dumps(calib, indent=2))
    print(f"\nWrote {a.output}")
    print("Apply with:  debayer.py <master> --rgb --ca-correct "
          f"{a.output} --tonemap reinhard ...")


def render_check():
    # --- fit_lateral_ca: recover a known injected CA shift ------------------
    # A synthetic high-contrast checkerboard as green (the reference), then R
    # and B built by sampling green through the KNOWN inverse of the true
    # scale about a known centre (ca_lib.sample_at, the same bilinear sampler
    # fit_lateral_ca itself uses) -- the same known-ground-truth-then-fit
    # methodology measure.py's own fit_ellipse self-check already uses.
    H, W = 160, 200
    yy, xx = np.mgrid[0:H, 0:W]
    green = 500.0 + 300.0 * (((xx // 8) % 2) ^ ((yy // 8) % 2)).astype(np.float64)

    true_cx, true_cy = W * 0.52, H * 0.49
    true_m_r, true_m_b = 1.006, 0.994

    def warp_from_green(scale):
        src_x = true_cx + (xx.astype(np.float64) - true_cx) / scale
        src_y = true_cy + (yy.astype(np.float64) - true_cy) / scale
        return sample_at(green, src_x.ravel(), src_y.ravel()).reshape(H, W)

    red = warp_from_green(true_m_r)
    blue = warp_from_green(true_m_b)
    rgb = np.stack([red, green, blue], axis=-1)

    result = fit_lateral_ca(rgb, edge_percentile=90.0)
    fit_cx, fit_cy = result["optical_center_px"]
    assert abs(fit_cx - true_cx) < 2.5 and abs(fit_cy - true_cy) < 2.5, \
        "fit_lateral_ca should recover the injected centre ({}, {}), got ({:.2f}, {:.2f})".format(
            true_cx, true_cy, fit_cx, fit_cy)
    assert abs(result["scale_red_over_green"] - true_m_r) < 2e-3, \
        "fit_lateral_ca should recover the injected R/G scale {}, got {:.6f}".format(
            true_m_r, result["scale_red_over_green"])
    assert abs(result["scale_blue_over_green"] - true_m_b) < 2e-3, \
        "fit_lateral_ca should recover the injected B/G scale {}, got {:.6f}".format(
            true_m_b, result["scale_blue_over_green"])
    assert result["residual_rms"]["R_vs_G_after"] < result["residual_rms"]["R_vs_G_before"]
    assert result["residual_rms"]["B_vs_G_after"] < result["residual_rms"]["B_vs_G_before"]
    assert len(result["offset_vs_radius"]) > 0
    print("fit_lateral_ca check PASS: recovered injected centre ({:.1f}, {:.1f}) and "
          "scales R/G={:.6f} B/G={:.6f} (true: ({:.1f}, {:.1f}), {}, {}) from a "
          "synthetic CA-shifted checkerboard".format(
              fit_cx, fit_cy, result["scale_red_over_green"], result["scale_blue_over_green"],
              true_cx, true_cy, true_m_r, true_m_b))

    try:
        fit_lateral_ca(np.zeros((20, 20, 3)), edge_percentile=90.0)
        raise AssertionError("expected ValueError for a flat (no-edge) target")
    except ValueError:
        pass
    print("fit_lateral_ca check PASS: a flat target with no edges raises "
          "ValueError rather than a bare crash")

    # --- format_offset_table: layout sanity ---------------------------------
    txt = format_offset_table(result["offset_vs_radius"])
    assert "radius" in txt and "dR meas" in txt and "dB pred" in txt
    assert txt.count("\n") == len(result["offset_vs_radius"])
    print("format_offset_table check PASS: header + one row per annulus")

    # --- poly2_flag: quiet on a clean fit, raised on real outer-radius curvature
    clean_table = [
        {"radius_px": 50.0, "n": 100, "dR_measured": 0.30, "dR_predicted": 0.30,
         "dB_measured": -0.20, "dB_predicted": -0.20},
        {"radius_px": 100.0, "n": 100, "dR_measured": 0.60, "dR_predicted": 0.61,
         "dB_measured": -0.40, "dB_predicted": -0.39},
    ]
    flagged, _ = poly2_flag(clean_table)
    assert not flagged, "a clean fit tracking the predicted line should not be flagged"

    curved_table = [
        {"radius_px": 50.0, "n": 100, "dR_measured": 0.30, "dR_predicted": 0.30,
         "dB_measured": -0.20, "dB_predicted": -0.20},
        {"radius_px": 100.0, "n": 100, "dR_measured": 1.60, "dR_predicted": 0.61,
         "dB_measured": -0.40, "dB_predicted": -0.39},
    ]
    flagged, detail = poly2_flag(curved_table)
    assert flagged, "a large outer-annulus deviation should raise the poly2 flag"
    assert "outer annulus" in detail
    print("poly2_flag check PASS: quiet on a clean fit, raised on real outer-radius "
          "curvature, both evidence-only (never raises/blocks)")

    # --- CA calibration store: append-only, supersedes chain ----------------
    global CA_CALIBRATION_PATH
    orig_path = CA_CALIBRATION_PATH
    import tempfile
    import shutil
    tmp_dir = Path(tempfile.mkdtemp()) / "ca_calib_check"
    CA_CALIBRATION_PATH = tmp_dir / "ca_calibration.json"
    try:
        assert load_ca_calibrations() == {}, "a missing store should load as {}"
        assert current_ca_calibration("40x") is None, "no history yet should read as None"

        entry_v1 = build_ca_calibration_entry(result, "40x", Path("/tmp/fake_ca_target.tif"))
        assert entry_v1["objective"] == "40x"
        assert entry_v1["model"] == "constant_radial_scale"
        assert "poly2_flagged" in entry_v1 and "poly2_detail" in entry_v1
        store = save_ca_calibration("40x", entry_v1)
        saved_v1 = store["40x"][-1]
        assert saved_v1["supersedes"] is None, "the first entry supersedes nothing"
        assert "entry_id" in saved_v1

        entry_v2 = build_ca_calibration_entry(result, "40x", Path("/tmp/fake_ca_target.tif"))
        save_ca_calibration("40x", entry_v2)
        store2 = load_ca_calibrations()
        assert len(store2["40x"]) == 2, "a redo should APPEND, not replace"
        assert store2["40x"][0] == saved_v1, "the original entry must survive byte-for-byte"
        assert store2["40x"][1]["supersedes"] == saved_v1["entry_id"], \
            "the new entry must chain 'supersedes' to the one it replaces as current"

        entry_100x = build_ca_calibration_entry(result, "100x", Path("/tmp/fake_ca_target.tif"))
        save_ca_calibration("100x", entry_100x)
        store3 = load_ca_calibrations()
        assert len(store3["100x"]) == 1, "saving 40x must not disturb 100x's own history"
        assert len(store3["40x"]) == 2, "saving 100x must not disturb 40x's own history"

        cur = current_ca_calibration("40x", store3)
        assert cur is store3["40x"][-1], "current_ca_calibration must read the LATEST entry"
        print("CA calibration store check PASS: append-only, supersedes chain intact, "
              "per-objective isolation, missing store is a clean {}")

        # --- export_ca_calibration: debayer.py-compatible standalone file ----
        export_path = tmp_dir / "exported_ca.json"
        exported = export_ca_calibration(cur, export_path)
        assert "entry_id" not in exported and "supersedes" not in exported, \
            "an exported file should drop the store's own audit-chain bookkeeping"
        reread = json.loads(export_path.read_text())
        assert reread["model"] == "constant_radial_scale", \
            "debayer.py --ca-correct only checks 'model'; the exported file must carry it"
        assert reread["scale_red_over_green"] == cur["scale_red_over_green"]
        print("export_ca_calibration check PASS: writes a debayer.py --ca-correct "
              "compatible file, store-only bookkeeping fields dropped")
    finally:
        CA_CALIBRATION_PATH = orig_path
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        main([a for a in sys.argv[1:] if a != "--render-check"])
