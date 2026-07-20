"""focus.py - section 6: the subject focus aid, as pure logic on the seam.

Everything here sits on camera_backend.CameraBackend and never touches Picamera2.
The camera hands down a LoresFrame whose data is the green channel where the
backend can give it (luma otherwise), so the score just receives a 2-D array and
the green-versus-luma choice stays behind the seam.

Pieces, each testable on its own:
  variance_of_laplacian  the sharpness score (numpy only, light pre-blur)
  RollingScore           the smoother that feeds the bar
  FocusBox               the box in fractional field coordinates, plus crop
  FocusBar               the session-relative bar with a per-field high-water mark
  FocusMeter             the coordinator: frame -> crop -> score -> smooth -> bar

None of this is a measurement. The score is dimensionless and converts nothing to
microns, so it needs no calibration and runs on frame one. It is an aiming signal
on the ISP preview; the number you might one day record comes off the captured
green frame (capture_still, then the debayer path), never off this.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    from .camera_backend import FakeCamera, LoresFrame
except ImportError:              # run directly as a script, not as a package module
    from camera_backend import FakeCamera, LoresFrame


# ---------------------------------------------------------------------------
# Score: variance of the Laplacian (numpy only, so the aid needs no scipy)
# ---------------------------------------------------------------------------
def _binomial(radius):
    """Normalized 1-D binomial kernel (a cheap Gaussian approximation)."""
    k = np.array([1.0])
    for _ in range(2 * radius):
        k = np.convolve(k, np.array([1.0, 1.0]))
    return k / k.sum()


def _sep_blur(plane, kernel1d):
    """Separable reflect-padded blur along both axes."""
    r = len(kernel1d) // 2
    a = plane.astype(np.float64)
    ph = np.pad(a, ((0, 0), (r, r)), mode="reflect")
    out = np.zeros_like(a)
    for i, kv in enumerate(kernel1d):
        out += kv * ph[:, i:i + a.shape[1]]
    pv = np.pad(out, ((r, r), (0, 0)), mode="reflect")
    res = np.zeros_like(a)
    for i, kv in enumerate(kernel1d):
        res += kv * pv[i:i + a.shape[0], :]
    return res


def _laplacian(plane):
    """4-neighbour Laplacian, reflect-padded. Kernel [[0,1,0],[1,-4,1],[0,1,0]]."""
    a = plane.astype(np.float64)
    p = np.pad(a, 1, mode="reflect")
    h, w = a.shape
    return (p[0:h, 1:w + 1] + p[2:h + 2, 1:w + 1]
            + p[1:h + 1, 0:w] + p[1:h + 1, 2:w + 2] - 4.0 * a)


def variance_of_laplacian(plane, blur_radius=1):
    """Sharpness score: variance of the Laplacian, higher at better focus.

    A light binomial pre-blur (blur_radius, numpy only) tames the noise the bare
    Laplacian would otherwise amplify: a Laplacian is a high-pass, sensor noise is
    high frequency, so a grainy dark field scores falsely high. blur_radius=0
    gives the raw Laplacian variance, which reproduces the figure the
    camera_backend self-check prints (the pre-blur only scales it, so the focus
    curve keeps its shape either way).
    """
    a = np.asarray(plane, dtype=np.float64)
    if a.ndim != 2:
        raise ValueError("variance_of_laplacian expects a 2-D plane")
    if blur_radius and blur_radius > 0:
        a = _sep_blur(a, _binomial(int(blur_radius)))
    return float(_laplacian(a).var())


# ---------------------------------------------------------------------------
# Smoother
# ---------------------------------------------------------------------------
class RollingScore:
    """Moving average over the last `window` scores. Smooths the per-frame twitch
    (sensor noise, hunting hands) so the eye tracks the plateau, not the sparkle.
    The bar reads this smoothed value, never the raw score."""

    def __init__(self, window=5):
        if window < 1:
            raise ValueError("window must be >= 1")
        self._window = int(window)
        self._buf = deque(maxlen=self._window)

    def push(self, value):
        self._buf.append(float(value))
        return self.value

    @property
    def value(self):
        return float(np.mean(self._buf)) if self._buf else 0.0

    @property
    def full(self):
        return len(self._buf) == self._window

    def reset(self):
        self._buf.clear()


# ---------------------------------------------------------------------------
# The box: fractional field coordinates, so the same region maps onto the
# preview, the lores, and the green plane with no offset to carry. (Measurement
# marks use green-plane pixels instead; two conventions, two jobs.)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FocusBox:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self):
        if not (0.0 <= self.x0 < self.x1 <= 1.0 and 0.0 <= self.y0 < self.y1 <= 1.0):
            raise ValueError("invalid fractional box "
                             f"{(self.x0, self.y0, self.x1, self.y1)}")

    @classmethod
    def centered(cls, w=0.4, h=0.4):
        return cls((1 - w) / 2, (1 - h) / 2, (1 + w) / 2, (1 + h) / 2)

    @classmethod
    def from_corners(cls, ax, ay, bx, by):
        """Order and clamp two raw drag corners into a valid fractional box. The
        GUI does the letterbox conversion (mouse point -> displayed image rect)
        before calling this; here the corners are already fractions of the field."""
        x0, x1 = sorted((ax, bx))
        y0, y1 = sorted((ay, by))
        clamp = lambda v: min(max(v, 0.0), 1.0)
        return cls(clamp(x0), clamp(y0), clamp(x1), clamp(y1))

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    def same_size_as(self, other, tol=1e-4):
        return (abs(self.width - other.width) <= tol
                and abs(self.height - other.height) <= tol)

    def pixel_rect(self, shape):
        h, w = shape[:2]
        c0 = min(max(int(round(self.x0 * w)), 0), w)
        c1 = min(max(int(round(self.x1 * w)), c0 + 1), w)
        r0 = min(max(int(round(self.y0 * h)), 0), h)
        r1 = min(max(int(round(self.y1 * h)), r0 + 1), h)
        return r0, r1, c0, c1

    def crop(self, plane):
        r0, r1, c0, c1 = self.pixel_rect(plane.shape)
        return plane[r0:r1, c0:c1]


# ---------------------------------------------------------------------------
# The bar: session-relative, scoped to the field being focused. Auto-ranges to
# the min/max seen this field (not zero-based), so its width covers the narrow
# band scores occupy near focus. A full bar means best-seen-this-sweep.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BarState:
    fill: float        # 0..1, current smoothed value within [lo, hi] this field
    current: float
    hi: float          # per-field high-water mark
    lo: float
    at_peak: bool      # current within eps of hi (re-pinned)
    settled: bool      # range trustworthy (>= warmup frames, non-degenerate)


class FocusBar:
    """Tracks the running min and max of the smoothed score since the last field
    reset. While you climb, every frame is a new best and the bar pins full; once
    focus is bracketed the range is fixed, and the bar then reads current against
    best-seen, so pushing past the peak drops it and easing back re-pins it."""

    def __init__(self, warmup=5, peak_eps=0.02):
        self._warmup = int(warmup)
        self._peak_eps = float(peak_eps)
        self.reset()

    def reset(self):
        self._hi = None
        self._lo = None
        self._n = 0

    @property
    def high_water(self):
        return self._hi

    def update(self, value):
        v = float(value)
        self._n += 1
        self._hi = v if self._hi is None else max(self._hi, v)
        self._lo = v if self._lo is None else min(self._lo, v)
        span = self._hi - self._lo
        fill = 1.0 if span <= 1e-12 else (v - self._lo) / span
        fill = min(max(fill, 0.0), 1.0)
        at_peak = (self._hi - v) <= self._peak_eps * max(abs(self._hi), 1e-12)
        settled = (self._n >= self._warmup) and (span > 1e-12)
        return BarState(fill=fill, current=v, hi=self._hi, lo=self._lo,
                        at_peak=at_peak, settled=settled)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FocusState:
    valid: bool
    source: str            # 'green' or 'luma', passed through from the frame
    raw: float
    smoothed: float
    bar: Optional[BarState]
    reason: str = ""


class FocusMeter:
    """Ties one lores frame to a bar reading: crop to the box, score on the green
    content, smooth, update the bar. Owns the reset rules.

      reset_field()  launch, restart, or the manual reset key: clear the score
                     tracking, keep the box (position and size carry forward).
      set_box()      a resize resets the tracking (comparability holds only at a
                     fixed region); a same-size move does not.
    """

    def __init__(self, box=None, window=5, warmup=5, blur_radius=1, min_box_px=16):
        self.box = box if box is not None else FocusBox.centered()
        self._smoother = RollingScore(window)
        self._bar = FocusBar(warmup)
        self._blur_radius = blur_radius
        self._min_box_px = int(min_box_px)
        self._last_valid_bar: Optional[BarState] = None

    @property
    def high_water(self):
        return self._bar.high_water

    def reset_field(self):
        self._smoother.reset()
        self._bar.reset()
        self._last_valid_bar = None

    def set_box(self, new_box):
        if not new_box.same_size_as(self.box):
            self._smoother.reset()
            self._bar.reset()
            self._last_valid_bar = None
        self.box = new_box

    def update(self, frame: LoresFrame) -> FocusState:
        crop = self.box.crop(frame.data)
        if min(crop.shape[:2]) < self._min_box_px:
            # too few pixels to score; hold, and do not poison the high-water mark
            return FocusState(valid=False, source=frame.source, raw=0.0,
                              smoothed=self._smoother.value,
                              bar=self._last_valid_bar, reason="box too small")
        raw = variance_of_laplacian(crop, self._blur_radius)
        smoothed = self._smoother.push(raw)
        bar = self._bar.update(smoothed)
        self._last_valid_bar = bar
        return FocusState(valid=True, source=frame.source, raw=raw,
                          smoothed=smoothed, bar=bar, reason="")


# ---------------------------------------------------------------------------
# Self-check: no hardware. Reproduces the seam figure, then drives the meter
# through a focus sweep to show the bar climb, peak, re-pin, reset, and the
# resize-versus-move rule. Run:  python3 focus.py
# ---------------------------------------------------------------------------
def _bar(fill, width=24):
    n = int(round(min(max(fill, 0.0), 1.0) * width))
    return "[" + "#" * n + "-" * (width - n) + "]"


def _dwell(meter, cam, z, frames=6):
    """Sit at focus position z for `frames` preview frames (lets the smoother
    settle), return the final state."""
    cam.focus_position = float(z)
    st = None
    for _ in range(frames):
        st = meter.update(cam.focus_frame())
    return st


if __name__ == "__main__":
    cam = FakeCamera()
    cam.start()

    cam.focus_position = 0.0
    raw0 = variance_of_laplacian(cam.focus_frame().data, blur_radius=0)
    print(f"tie-back: full-frame raw score (blur off) at z=0 = {raw0:.4f}  "
          f"(the camera_backend self-check figure)\n")

    zs = [-3, -2, -1, 0, 1, 2, 3]
    meter = FocusMeter(window=3, warmup=5)

    print("pass 1 - first sweep: the bar pins full at each new best, drops on overshoot")
    for z in zs:
        st = _dwell(meter, cam, z)
        print(f"  z={z:+d}  smoothed={st.smoothed:8.4f}  {_bar(st.bar.fill)}")

    print("\npass 2 - range now bracketed: current read against best-seen")
    for z in zs:
        st = _dwell(meter, cam, z)
        tag = "  <-- re-pinned at peak" if (st.bar.at_peak and st.bar.settled) else ""
        print(f"  z={z:+d}  smoothed={st.smoothed:8.4f}  {_bar(st.bar.fill)}{tag}")

    print("\nmanual reset (new field / restart / reset key) clears the high-water mark")
    meter.reset_field()
    st = _dwell(meter, cam, -1)
    print(f"  after reset at z=-1: high_water={meter.high_water:.4f}  "
          f"{_bar(st.bar.fill)}  (fresh range)")

    print("\nresize resets the bar; a same-size move keeps it")
    for z in zs:
        _dwell(meter, cam, z)
    meter.set_box(FocusBox.centered(0.5, 0.5))               # different size
    print(f"  after resize: high_water = {meter.high_water}")
    for z in zs:
        _dwell(meter, cam, z)
    kept = meter.high_water
    meter.set_box(FocusBox.from_corners(0.30, 0.30, 0.80, 0.80))   # same size, moved
    print(f"  after same-size move: high_water = {meter.high_water:.4f}  "
          f"(unchanged from {kept:.4f})")

    cam.stop()
