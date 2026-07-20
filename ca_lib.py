"""ca_lib.py - shared lateral chromatic aberration registration primitives.

Lateral CA is wavelength-dependent magnification: to first order each colour
channel is a radial rescale of the others about the optical centre. Green is the
reference (middle wavelength; the morphometry channel). This module holds the
warp used IDENTICALLY by ca_measure.py (to FIT the scales) and debayer.py (to
APPLY them) - one warp convention, so a fit transfers exactly.

Convention: a channel magnified by m images a feature's true-radius-r point at
radius r*m. To register it back to green, sample the channel at

    src = centre + m * (out - centre)          (backward map; no holes)

so radial_warp(channel, cx, cy, m) returns that channel registered to green.
Green is never warped. The warp is geometric (resampling) and value-scale
independent, so it must run in LINEAR space, before white balance / tone map.
"""
import numpy as np

__version__ = "1.0"


def sample_at(img, xs, ys):
    """Bilinear-sample 2-D `img` at float coords (xs, ys), edge-clamped."""
    h, w = img.shape
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    fx = (xs - x0).astype(np.float32)
    fy = (ys - y0).astype(np.float32)
    x0c = np.clip(x0, 0, w - 1); x1c = np.clip(x0 + 1, 0, w - 1)
    y0c = np.clip(y0, 0, h - 1); y1c = np.clip(y0 + 1, 0, h - 1)
    Ia = img[y0c, x0c]; Ib = img[y0c, x1c]
    Ic = img[y1c, x0c]; Id = img[y1c, x1c]
    return (Ia * (1 - fx) * (1 - fy) + Ib * fx * (1 - fy)
            + Ic * (1 - fx) * fy + Id * fx * fy)


def radial_warp(channel, cx, cy, scale):
    """Backward radial rescale of a 2-D channel about (cx, cy) by `scale`."""
    h, w = channel.shape
    yy, xx = np.mgrid[0:h, 0:w]
    src_x = cx + scale * (xx.astype(np.float32) - cx)
    src_y = cy + scale * (yy.astype(np.float32) - cy)
    out = sample_at(channel.astype(np.float32), src_x.ravel(), src_y.ravel())
    return out.reshape(h, w)


def apply_ca_correction(rgb, cx, cy, m_r, m_b):
    """Return a copy of rgb (H,W,3) with R and B registered to green; green is
    left byte-for-byte untouched (it is the reference). Input dtype is preserved
    so green does not shift even by a rounding LSB; only the warped R/B channels
    pass through the float32 resampler."""
    out = rgb.copy()
    if m_r != 1.0:
        out[..., 0] = radial_warp(rgb[..., 0], cx, cy, m_r)
    if m_b != 1.0:
        out[..., 2] = radial_warp(rgb[..., 2], cx, cy, m_b)
    return out


def adapt_center(cx, cy, calib_shape, image_shape):
    """Scale an optical centre (px) measured at calib_shape to image_shape.

    The scale ratios m are dimensionless and transfer unchanged; the centre is
    in pixels and scales with resolution. Returns (cx', cy', resolution_ratio).
    Binned/cropped geometry may still warrant its own calibration - this only
    handles a clean resolution change."""
    ch, cw = calib_shape
    ih, iw = image_shape
    if (ch, cw) == (ih, iw):
        return float(cx), float(cy), 1.0
    sx = iw / cw
    sy = ih / ch
    return cx * sx, cy * sy, 0.5 * (sx + sy)
