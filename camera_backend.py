"""camera_backend.py - the thin seam between the live camera and everything above it.

Section 1 of the build. The pure logic (focus score, bar math, coordinate
mapping, overlay rendering) sits ON this interface, never on Picamera2 directly,
so a camera swap rewrites only the adapter and the focus math above it never
learns the camera changed. Two facts drive the shape:

  * The score runs on GREEN content. Whether green is available (a Pi 5 RGB
    lores) or only luma is (a YUV420 lores) is a camera-specific fact, so it is
    decided HERE and the score just receives a 2-D array. The camera picks the
    channel; the pure code never sees the format.

  * Nothing the camera shows is a measurement. focus_frame() is the ISP
    preview's lores, an aiming signal; set_overlay() draws on a separate layer
    that never touches a capturable pixel. The recorded number comes off
    capture_still() / capture_burst() / capture_bracket_phase(), processed by
    the existing debayer path, not off anything here.

FakeCamera implements the whole interface with no hardware, so every pure piece
above the seam is testable anywhere. Picamera2Camera is the on-rig backend; the
few lines that only settle on the actual Pi (lores format, overlay compositing,
capture timing, exact Qt import) are marked ON-RIG. Those are the shakeout
points, kept contained to this file on purpose.

RECONSTRUCTION NOTE (2026-07-11): this file was rebuilt from verified fragments
pulled out of a prior conversation's tool-call history after the on-disk project
copy was found to be stale. Every method body below was matched against a direct
quote from that history; nothing here is a guess about behavior. The one place
this note flags explicitly is Picamera2Camera._stash_lores, where two edits from
two different sessions had to be reconciled (see the comment there).
"""
from __future__ import annotations

import abc
import ast
import importlib
import io
import re
import sys
import threading
import tokenize
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# imx477.py is this project's own sensor-profile module (driver layer,
# same directory) -- FakeCamera reuses it directly below (deliberate: a
# FakeCamera is a stand-in for THIS project's real rig, and its
# get_capabilities() already reports real IMX477 mode sizes). Picamera2Camera
# never references this name directly; see _resolve_sensor_profile below for
# why (PRIORITY_click_mapping_fix.md's exact-model-name dispatch).
try:
    from . import imx477 as _imx477
except ImportError:
    import imx477 as _imx477

# Rig defaults (match capture.py). Tunable.
# FULL_RES (Stage 3 sequence 2): collapses to _imx477.FULL_ARRAY_SIZE rather
# than its own hardcoded (4056, 3040) -- the same deliberate stand-in
# reference FakeCamera.sensor_crop_for_size already uses (see the _imx477
# import's own comment above), extended to this constant too, so this is
# still the one place a hardcoded "imx477" name is allowed to appear, not a
# second one. GREEN_PLANE_RES derives from FULL_RES, computed once, here --
# the single source measure.py/qt_shell.py ask rather than duplicate.
FULL_RES = _imx477.FULL_ARRAY_SIZE
GREEN_PLANE_RES = (FULL_RES[0] // 2, FULL_RES[1] // 2)
PREVIEW_RES = (1332, 990)
LORES_RES = (640, 480)     # small enough for real-time scoring, 4:3 like the sensor

# BIT_DEPTH: same collapse as FULL_RES above, same deliberate stand-in
# reference, extended to a third constant. White level derives from THIS,
# never from a container's own dtype -- see white_level_for_bit_depth
# below, and PHILOSOPHY.md's own rule in these exact words.
BIT_DEPTH = _imx477.BIT_DEPTH


def white_level_for_bit_depth(bit_depth, container_bits=16):
    """The full-scale value a `bit_depth`-deep sensor reading produces once
    left-justified into a `container_bits`-wide storage element -- e.g. a
    12-bit ADC value stored in this project's own uint16 TIFF/DNG
    convention (container_bits=16, the default and, today, the only width
    this project's raw-storage pipeline actually writes) lands at
    ((2**12) - 1) << (16 - 12) = 65520, not 4095 (the unshifted 12-bit
    max) and not 65535 (the container's own max, dtype_max(uint16)'s
    answer regardless of what the sensor actually reports).

    This is "white level derives from bit depth in the profile, never
    from container width" (PHILOSOPHY.md, verbatim) in formula form:
    bit_depth is the sensor fact and is what this function is FOR;
    container_bits only expresses that fact in whatever storage
    convention the caller's own array actually uses -- it never supplies
    the value's magnitude on its own the way dtype_max(in_dtype) does.
    Returns a plain int (matching a literal white-level constant's own
    type, never silently widening to float -- callers that need a float
    convert at their own call site, the same way they would a literal)."""
    if bit_depth > container_bits:
        raise ValueError(
            "bit_depth {!r} cannot fit in a {!r}-bit container".format(
                bit_depth, container_bits))
    return ((1 << bit_depth) - 1) << (container_bits - bit_depth)


def derive_lores_res(preview_res, target_pixels=LORES_RES[0] * LORES_RES[1]):
    """The lores stream size to pair against a given preview_res: same
    aspect ratio as preview_res, at roughly LORES_RES's own pixel count
    (target_pixels), rounded to even dimensions (some lores formats, e.g.
    YUV420, require them). Replaces the old fixed (640, 480) constant --
    ROADMAP item 2 (preview-resolution setting) means preview_res is no
    longer always 4:3, and pairing an arbitrary main aspect against a
    hardcoded 4:3 lores size is exactly the class of pairing failure that
    caused the focus-aid bug ROADMAP item 1 fixed. Matching the aspect
    instead of pinning it removes the mismatch outright, regardless of
    whether that specific failure mode was ever real here."""
    w, h = preview_res
    aspect = w / h
    lores_h = int(round((target_pixels / aspect) ** 0.5))
    lores_h -= lores_h % 2
    lores_w = int(round(lores_h * aspect))
    lores_w -= lores_w % 2
    return (lores_w, lores_h)


@dataclass(frozen=True)
class LoresFrame:
    """One lores frame reduced to the single channel the focus score runs on.

    data:   2-D float32. The green channel where the backend can give it, else
            luma. Full field; cropping to the focus box is pure logic above the
            seam (map the box's fractional coordinates onto data.shape).
    source: 'green' or 'luma'. Luma is a fallback and is not green-specific, so
            the UI should surface which one it is honestly.
    """
    data: np.ndarray
    source: str


@dataclass(frozen=True)
class CaptureResult:
    """What a capture hands back across the seam, so the recording layer above it
    never has to touch the camera.

    raw:      the measurement master on disk (a DNG on the Pi, a stand-in TIFF on
              the fake). This is the file the pixel hash and the record key from.
    preview:  the JPG preview if one was written, else None (the fake writes none).
    metadata: the camera metadata for the shot (ExposureTime, AnalogueGain, sensor
              timestamp, and so on). The real numbers of an auto-exposed frame live
              here, since the GUI is not locking exposure the way capture.py does.
    """
    raw: Path
    preview: Optional[Path]
    metadata: dict


class CameraBackend(abc.ABC):
    """The seam. Everything above it is camera-independent and Qt-free."""

    # --- lifecycle ---
    @abc.abstractmethod
    def start(self) -> None:
        """Open the camera and begin the live preview + lores stream."""

    @abc.abstractmethod
    def stop(self) -> None:
        """Stop the stream and release the device."""

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # --- live aid (section 6 uses these) ---
    @abc.abstractmethod
    def focus_frame(self) -> LoresFrame:
        """Return the MOST RECENT lores frame as the focus-score channel, full
        field. Non-blocking: it must never wait on the camera, because on the Qt
        backend the caller runs on the same thread that services the camera and a
        blocking wait there deadlocks. Poll it from a timer; it may hand back the
        same frame twice if polled faster than frames arrive. Cropping to the box
        is pure logic above the seam."""

    @abc.abstractmethod
    def set_overlay(self, rgba: Optional[np.ndarray]) -> None:
        """Show an RGBA overlay (H, W, 4 uint8) on the preview, or clear it with
        None. Display only: this layer never touches a capturable pixel. The
        overlay CONTENT (peaking, the box, the bar) is rendered above the seam
        and handed down as a finished array."""

    # --- single-shot capture ---
    @abc.abstractmethod
    def capture_still(self, out_dir: Path, stem: str) -> Path:
        """Capture one full-res still (with its raw) and return the raw's path.

        MUST NOT be called on the Qt main thread: it switches modes and blocks,
        which deadlocks the thread that services the camera. Run it on a worker
        thread, or wire the async signal path (switch_mode_and_capture_file with
        signal_function=... plus done_signal) in the GUI layer.

        Returns the RAW. Turning it into a green plane or a linear RGB is the
        existing debayer step, a pure stage after this, not part of the seam."""

    @abc.abstractmethod
    def capture_still_async(self, out_dir: Path, stem: str,
                            on_done: Callable[[object], None]) -> None:
        """Start a still capture WITHOUT blocking the caller; invoke on_done once
        the shot resolves. This is the GUI's capture verb: the Qt thread services
        the camera, so it must never sit in a blocking capture. capture_still stays
        for CLI and worker-thread callers where blocking is fine; this is the same
        switch-shoot-switch, made event-driven. on_done receives a CaptureResult on
        success, or an Exception on failure; either way control comes back."""

    # --- exposure control (the panel sits on these) -----------------------
    @abc.abstractmethod
    def exposure_limits(self) -> dict:
        """The sensor's own reported ranges, so a slider can never ask for a value
        the sensor will refuse. Returns {"shutter_us": (lo, hi), "gain": (lo, hi)}."""

    @abc.abstractmethod
    def probe(self) -> dict:
        """Meter the scene with AE/AWB on, settle, and return the locked values as
        {"shutter_us", "analogue_gain", "awb_red_gain", "awb_blue_gain"}. This is
        BLOCKING (it waits for AE to settle), so a GUI runs it off the Qt thread and
        applies the result via apply_exposure_lock. Same values capture.py's probe
        returns, so a lock is interchangeable with the CLI's profile.json."""

    @abc.abstractmethod
    def apply_exposure_lock(self, locked: dict) -> None:
        """Hold a fixed exposure: AE and AWB off, the four locked values applied,
        sharpness off. This is the default state, so a rigorous set is never shot
        under a floating exposure."""

    @abc.abstractmethod
    def read_exposure(self) -> dict:
        """The live exposure for the panel to display, non-blocking. Returns the
        four values plus {"auto_exposure": bool, "auto_white_balance": bool}. When
        auto is on these are the metered values the sliders should mirror."""

    @abc.abstractmethod
    def set_exposure(self, shutter_us=None, gain=None, red_gain=None,
                     blue_gain=None, auto_exposure=None, auto_white_balance=None) -> None:
        """Apply only the arguments given. Passing shutter_us or gain drops AE on its
        own (a manual value implies manual); passing red_gain or blue_gain drops AWB.
        auto_exposure / auto_white_balance set those modes explicitly. So a slider
        move is set_exposure(shutter_us=x) and a checkbox is
        set_exposure(auto_exposure=on), and the backend keeps the two consistent."""

    @abc.abstractmethod
    def set_long_exposure(self, enabled: bool, normal_max_us: int = None) -> None:
        """Raise (enabled=True) or restore (enabled=False) the sensor's frame-
        duration ceiling, so shutter times beyond the normal preview cadence
        become reachable at all. This does NOT itself change ExposureTime; the
        caller still uses set_exposure(shutter_us=...) for that, and should do
        so BEFORE disabling (so ExposureTime is back within the normal ceiling
        before that ceiling shrinks back down). `normal_max_us` is required when
        disabling: the ceiling to restore, normally whatever exposure_limits()
        reported before this was ever enabled."""

    # --- burst / HDR capture (section 5's burst kinds sit on these) --------
    @abc.abstractmethod
    def capture_burst(self, out_dir: Path, prefix: str, n: int,
                      shutter_us: int = None) -> dict:
        """Used for flat / science / dark / a multi-frame snap. BLOCKING for the
        whole burst; a GUI runs this on a worker thread, same as probe(). If
        shutter_us is given, applies and settles it first (a flat or dark shot at
        a level OTHER than the locked value); otherwise holds whatever is
        currently locked. Returns {"actual_us": the settled exposure actually
        used, "frames": [CaptureResult, ...] one per frame, for the caller to
        write sidecars and a session record from, the same division of labor the
        single-shot path already uses}."""

    @abc.abstractmethod
    def enter_still_mode(self) -> None:
        """Switch to still config and hold it, for an HDR bracket's two back-
        to-back phases (science levels, then dark levels) under ONE mode
        switch rather than one per phase or per frame. Pair with
        exit_still_mode; capture_burst does not use this, it switches and
        restores itself since it is always exactly one phase."""

    @abc.abstractmethod
    def exit_still_mode(self, restore_shutter_us: int) -> None:
        """Restore the given shutter (the locked value, so the preview comes
        back at the session's normal exposure, not whatever bracket level was
        last set) and switch back to preview config."""

    @abc.abstractmethod
    def capture_bracket_phase(self, out_dir: Path, prefix_template: str, n: int,
                              base_us: int, stops: list) -> list:
        """n frames at EACH exposure level in `stops` (EV offsets from
        base_us), mirroring capture.py's bracket_burst_phase. ASSUMES still
        mode is already active (enter_still_mode called by the caller first);
        this does not switch modes itself, so an HDR sequence can run two
        phases (science, then dark) under one still-mode session and switch
        back only once, after both. Files named
        <prefix_template><level>_frame_<idx>, level 1-based in stops order.
        Returns one dict per level: {"level", "ev", "file_prefix",
        "requested_us", "actual_us", "actual_s", "frame_count",
        "frames": [CaptureResult, ...]}."""

    # --- video recording (documentation/review only, NOT the measurement path) --
    # Added alongside the Record button in qt_shell.py. Deliberately separate
    # from every verb above: no session, no sidecar, no pixel hash, no raw
    # frames -- compressed video for watching something happen over time, the
    # same "aiming, not measuring" register as the live preview itself. A
    # raw/measurement-grade capture mode, if it's ever wanted, is a distinct
    # future feature built alongside this, not a replacement for it.
    @abc.abstractmethod
    def start_recording(self, out_dir: Path, stem: str) -> Path:
        """Start recording compressed video to out_dir/stem.mp4 and return
        that path immediately; the file grows until stop_recording() is
        called. Raises RuntimeError if a recording is already in progress.
        Whether this is safe to run WHILE a still/burst capture's mode
        switch happens has not been verified on real hardware, so the GUI
        keeps Record and Capture/burst mutually exclusive rather than
        assume they compose safely."""

    @abc.abstractmethod
    def stop_recording(self) -> Path:
        """Stop the active recording and return its finished file's path.
        Raises RuntimeError if nothing is recording."""

    @abc.abstractmethod
    def is_recording(self) -> bool:
        """Whether a recording is currently in progress, non-blocking, so
        the GUI can gate the Capture and Record controls against each
        other."""

    @abc.abstractmethod
    def set_video_resolution(self, resolution) -> None:
        """Set the (width, height) the NEXT recording will encode at; has no
        effect on one already in progress. Kept a separate setter, not a
        start_recording() parameter, matching this file's existing pattern
        for adjustable settings (set_exposure, set_long_exposure): actions
        stay simple, settings get their own call. Exists specifically so a
        future resolution menu has something to plug into without needing
        to touch start_recording()'s signature or the mode-switch logic
        again. Raises ValueError for a non-positive width/height, or
        RuntimeError if a recording is currently in progress."""

    @abc.abstractmethod
    def video_resolution(self):
        """The (width, height) the next recording will use."""

    # --- sensor crop geometry (PRIORITY_click_mapping_fix.md) ---------------
    # Preview-to-green-plane click mapping requires knowing the ACTUAL crop
    # rectangle each configured stream reads off the sensor's full array --
    # a scale factor can't express an off-centre crop, which is exactly what
    # made the old single-fraction mapping wrong. These three methods are
    # deliberately separate from get_capabilities(): that method's fixed key
    # set is about rendering choices in the Preferences dialog, this is about
    # a live coordinate conversion.
    @abc.abstractmethod
    def preview_resolution(self):
        """The ACTUAL (width, height) this camera's live preview/main stream
        is configured at -- fixed at construction, may differ from this
        module's own PREVIEW_RES constant once preview_res becomes a
        per-launch setting. Never hardcode PREVIEW_RES where this is what's
        actually needed."""

    @abc.abstractmethod
    def capture_resolution(self):
        """The ACTUAL (width, height) this camera's still-capture path is
        configured at -- same fixed-at-construction rule as
        preview_resolution, mirroring this module's FULL_RES constant."""

    @abc.abstractmethod
    def sensor_crop_for_size(self, size):
        """(x, y, w, h) crop rectangle, in full-sensor-array pixel units,
        that the mode producing `size` reads. Origin and extent, never a
        scale factor. `size` should be one of this backend's own advertised
        resolutions (preview_resolution(), capture_resolution(), or an
        entry from get_capabilities()'s capture_resolutions/
        video_resolutions) -- an unrecognised size raises rather than
        guessing."""

    # --- capability query (PLAN_02_camera_capability_query.md) -------------
    @abc.abstractmethod
    def get_capabilities(self) -> dict:
        """What this driver's hardware actually offers, translated into
        plain dicts/lists/strings/numbers/bools -- no Picamera2 or
        libcamera type may cross this boundary. This is the ONLY method
        in the project allowed to know sensor-specific facts; everything
        above the seam (the future Preferences dialog included) renders
        whatever this returns and nothing else, so a different sensor
        with a different driver dropped in this class's place changes
        only what this method returns.

        Returns a dict with these keys:
          "capture_resolutions": [(w, h), ...] -- still-capture sizes the
              sensor natively supports.
          "capture_formats":     [str, ...]    -- raw pixel formats a
              still capture can be delivered in (e.g. "SRGGB12").
          "video_resolutions":   [(w, h), ...] -- sizes the preview/
              recording main stream can be configured at.
          "video_formats":       [str, ...]    -- encoder formats a
              recording can be written in.
          "stream_formats":      [str, ...]    -- OPTIONAL. A raw
              preview-stream format (e.g. "YUY2", "MJPEG"), present only
              if this driver actually exposes one to configure.
          "stream_resolutions":  [(w, h), ...] -- OPTIONAL, same rule as
              stream_formats.

        Absent means absent, not empty: a driver that cannot report a
        capability OMITS that key entirely ("I don't know"); a driver
        that knows and has zero options returns an empty list for it
        ("I know, there are none"). The two must never be conflated --
        the caller (the Preferences dialog) renders them differently: no
        control at all versus an empty one."""

    @abc.abstractmethod
    def lores_resolution(self):
        """The (width, height) of the CURRENT lores stream, i.e. the
        coordinate space focus-aid overlays and Live Measuring marks are
        drawn/hit-tested in right now for this instance. Not necessarily
        LORES_RES: Picamera2Camera derives it from preview_res's own
        aspect (see derive_lores_res) rather than pinning it, so it varies
        per instance once a non-default preview resolution is in play.
        Anything drawing into or converting clicks against the live lores
        frame must call this instead of assuming the module constant."""


class FakeCamera(CameraBackend):
    """Hardware-free backend for building and testing everything above the seam.

    focus_frame() synthesizes a field whose high-frequency contrast peaks when
    `focus_position` reaches `best_focus`, a single-peak response like real
    focus: sweep `focus_position` from below to above `best_focus` and the
    Laplacian variance climbs to a maximum and falls. That makes the bar's
    climb, peak-hold, and per-field reset deterministic with no Pi. Set
    `frame_source` to inject exact frames instead.

    set_overlay() records the last overlay so a test can assert it was drawn and
    inspect the RGBA the renderer produced. capture_still() writes a small
    synthetic raw-ish TIFF so file-based downstream steps have real input.
    """

    def __init__(self, lores_res=LORES_RES, source: str = "green",
                 frame_source: Optional[Callable[[], np.ndarray]] = None,
                 seed: int = 0, async_delay_s: float = 0.05,
                 fail_capture: bool = False, stream_caps: bool = False,
                 capture_shape=(64, 64), preview_res=PREVIEW_RES,
                 full_res=FULL_RES):
        self._w, self._h = lores_res
        # preview_res/full_res: never varied by main() in real use (FakeCamera
        # is only ever constructed bare there), but a real render_check needs
        # to exercise preview_resolution()/capture_resolution()/
        # sensor_crop_for_size() the same general way Picamera2Camera does --
        # see PRIORITY_click_mapping_fix.md's "must be general across
        # arbitrary preview modes" requirement.
        self._preview_res = tuple(preview_res)
        self._full_res = tuple(full_res)
        # capture_shape: the (rows, cols) of the stand-in array capture_still
        # writes. Default matches this class's own long-standing hardcoded
        # shape exactly, so every existing caller is unaffected. Exists so a
        # render_check can make a fake capture come back already shaped like
        # a real green plane (Preferences-dialog plan set, Part 05's live
        # measure panel) -- construct FakeCamera(capture_shape=(GREEN_PLANE_
        # RES[1], GREEN_PLANE_RES[0])) to exercise measure.load_measurement_
        # plane's real already-extracted-green branch headlessly, with no
        # stubbing of the loader itself.
        self._capture_shape = tuple(capture_shape)
        # get_capabilities(): False (the default) matches Picamera2Camera's
        # current behavior -- no stream server implemented, so stream_formats/
        # stream_resolutions are omitted. Pass True to exercise the
        # present-key rendering path off-rig, since the real driver can't.
        self._stream_caps = bool(stream_caps)
        # Capability cache (Fix: Preferences dialog crash on
        # get_capabilities()): computed once and reused on every later call.
        # FakeCamera's synthetic result never changes anyway, but the same
        # caching shape as Picamera2Camera keeps the two classes symmetric
        # and lets --render-check assert "computed once" against either.
        self._capabilities = None
        self._source = source
        self._frame_source = frame_source
        self._rng = np.random.default_rng(seed)
        # a fixed high-frequency texture; its amplitude is scaled by focus below
        self._texture = self._rng.standard_normal((self._h, self._w)).astype(np.float32)
        # focus model: amplitude is a Gaussian bump peaking at best_focus
        self.focus_position: float = -3.0     # rack this through best_focus
        self.best_focus: float = 0.0
        self.focus_width: float = 1.0
        self.last_overlay: Optional[np.ndarray] = None
        self.started = False

        # Exposure state. Defaults match probe()'s fixed return below, so a fresh
        # FakeCamera already reads back a plausible locked state before any probe.
        self._exp = {"shutter_us": 8000, "analogue_gain": 1.0,
                     "awb_red_gain": 1.8, "awb_blue_gain": 1.6}
        self._ae_on = False
        self._awb_on = False
        self._long_exposure = False   # mirrors the real backend's FrameDurationLimits flag

        # Async capture: fires on_done after a short delay via threading.Timer, so
        # the GUI's capture-in-flight handling (disable, await, re-enable, record)
        # has a real interval to exercise with no hardware. Kept short; set to 0 in
        # a test that just wants the callback promptly.
        self._async_delay_s = float(async_delay_s)
        self._fail_capture = bool(fail_capture)
        self._capture_timers: list = []

        # Video recording state (see start_recording/stop_recording below).
        self._recording_path: Optional[Path] = None
        self._video_res = PREVIEW_RES   # matches Picamera2Camera's own default

        # Primed here, not left to the first caller, for symmetry with
        # Picamera2Camera (whose own priming is load-bearing there -- see its
        # __init__): keeps both classes' "computed once, at construction"
        # contract identical, even though FakeCamera has no live-camera
        # hazard of its own to avoid.
        self._capabilities = self.get_capabilities()

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def _focus_amplitude(self) -> float:
        z = (self.focus_position - self.best_focus) / max(self.focus_width, 1e-6)
        return float(np.exp(-0.5 * z * z))     # in (0, 1], peaks at best_focus

    def focus_frame(self) -> LoresFrame:
        if self._frame_source is not None:
            data = np.asarray(self._frame_source(), dtype=np.float32)
        else:
            # flat base plus high-frequency detail scaled by the focus bump.
            # More amplitude -> more Laplacian energy -> higher score. Numpy only.
            base = 0.5 * np.ones((self._h, self._w), dtype=np.float32)
            data = base + 0.25 * self._focus_amplitude() * self._texture
        return LoresFrame(data=data, source=self._source)

    def set_overlay(self, rgba: Optional[np.ndarray]) -> None:
        self.last_overlay = None if rgba is None else np.asarray(rgba)

    def capture_still(self, out_dir: Path, stem: str) -> Path:
        return self._fake_frame_write(out_dir, stem)

    def _fake_metadata(self) -> dict:
        # A plausible stand-in for request.get_metadata(), so the recording layer
        # above the seam has real fields to store and display off-rig.
        return {
            "ExposureTime": self._exp["shutter_us"],
            "AnalogueGain": self._exp["analogue_gain"],
            "DigitalGain": 1.0,
            "ColourGains": (self._exp["awb_red_gain"], self._exp["awb_blue_gain"]),
            "SensorTimestamp": int(datetime.now().timestamp() * 1e9),
            "source": "FakeCamera",
        }

    def capture_still_async(self, out_dir: Path, stem: str,
                            on_done: Callable[[object], None]) -> None:
        # Non-blocking on the fake: write the still exactly as capture_still does,
        # then deliver a CaptureResult after a short delay so the GUI's capture-in-
        # flight handling (disable the button, await the callback, re-enable, record)
        # has a real interval to exercise with no hardware. Qt-free on purpose: this
        # module must still import on any machine, so the deferral is threading.Timer
        # and on_done therefore lands OFF the caller's thread, per the seam contract.
        # With fail_capture set, deliver an Exception instead, to drive the GUI's
        # failure path (control re-enabled, error shown) off-rig.
        def _payload():
            if self._fail_capture:
                return RuntimeError("fake capture failure (fail_capture=True)")
            path = self.capture_still(out_dir, stem)
            return CaptureResult(raw=path, preview=None, metadata=self._fake_metadata())

        payload = _payload()
        timer = threading.Timer(self._async_delay_s, lambda: on_done(payload))
        timer.daemon = True
        self._capture_timers.append(timer)
        timer.start()

    # --- exposure control ---------------------------------------------------
    def exposure_limits(self) -> dict:
        # NOT a directly-quoted value: no fragment gave FakeCamera's exact
        # exposure_limits body. On real hardware, camera_controls reflects the
        # CURRENTLY active FrameDurationLimits, so before set_long_exposure(True)
        # is ever called the reported ceiling is the preview config's normal
        # cadence (~50ms), matching the "~50ms" figure in the long-exposure
        # writeup and the 50_000 fallback already confirmed in
        # Picamera2Camera.set_long_exposure. A static 3s ceiling here would make
        # the fast and long-exposure shutter tables identical off-rig, which
        # defeats testing the ceiling swap at all.
        return {"shutter_us": (60, 50_000), "gain": (1.0, 16.0)}

    def probe(self) -> dict:
        # Instant here; the GUI still runs it on a worker thread, which is what
        # models the real settle time.
        return {"shutter_us": 8000, "analogue_gain": 1.0,
                "awb_red_gain": 1.8, "awb_blue_gain": 1.6}

    def apply_exposure_lock(self, locked: dict) -> None:
        self._exp = {k: locked[k] for k in
                     ("shutter_us", "analogue_gain", "awb_red_gain", "awb_blue_gain")}
        self._ae_on = False
        self._awb_on = False

    def read_exposure(self) -> dict:
        out = dict(self._exp)
        out["auto_exposure"] = self._ae_on
        out["auto_white_balance"] = self._awb_on
        return out

    def set_exposure(self, shutter_us=None, gain=None, red_gain=None,
                     blue_gain=None, auto_exposure=None, auto_white_balance=None) -> None:
        if shutter_us is not None:
            self._exp["shutter_us"] = int(shutter_us)
            self._ae_on = False
        if gain is not None:
            self._exp["analogue_gain"] = float(gain)
            self._ae_on = False
        if red_gain is not None:
            self._exp["awb_red_gain"] = float(red_gain)
            self._awb_on = False
        if blue_gain is not None:
            self._exp["awb_blue_gain"] = float(blue_gain)
            self._awb_on = False
        if auto_exposure is not None:
            self._ae_on = bool(auto_exposure)
        if auto_white_balance is not None:
            self._awb_on = bool(auto_white_balance)

    def set_long_exposure(self, enabled: bool, normal_max_us: int = None) -> None:
        # No real frame-duration ceiling to move off-rig; exposure_limits()
        # already spans well past 3s and set_exposure never clamps, so a long
        # shutter value just works. Track the flag anyway, so a self-check can
        # confirm the GUI actually calls this at the right moments.
        self._long_exposure = bool(enabled)

    # --- burst / HDR ---------------------------------------------------------
    def _fake_frame_write(self, out_dir: Path, stem: str) -> Path:
        # Shared by capture_still, capture_burst, and capture_bracket_phase, so
        # all three write the exact same stand-in artifact.
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / (stem + ".tif")
        import tifffile
        frame = self._rng.integers(0, 4096, size=self._capture_shape).astype(np.uint16)
        tifffile.imwrite(str(path), frame)
        return path

    def capture_burst(self, out_dir: Path, prefix: str, n: int,
                      shutter_us: int = None) -> dict:
        actual = int(shutter_us) if shutter_us is not None else self._exp["shutter_us"]
        frames = []
        for i in range(int(n)):
            stem = "{}frame_{:04d}".format(prefix, i)
            path = self._fake_frame_write(out_dir, stem)
            md = dict(self._fake_metadata())
            md["ExposureTime"] = actual
            frames.append(CaptureResult(raw=path, preview=None, metadata=md))
        return {"actual_us": actual, "frames": frames}

    def enter_still_mode(self) -> None:
        pass   # no real mode to switch off-rig

    def exit_still_mode(self, restore_shutter_us: int) -> None:
        self._exp["shutter_us"] = int(restore_shutter_us)

    def capture_bracket_phase(self, out_dir: Path, prefix_template: str, n: int,
                              base_us: int, stops: list) -> list:
        levels = []
        for level, ev in enumerate(stops, start=1):
            target = int(round(base_us * (2.0 ** ev)))
            prefix = "{}{}_".format(prefix_template, level)
            frames = []
            for i in range(int(n)):
                stem = "{}frame_{:04d}".format(prefix, i)
                path = self._fake_frame_write(out_dir, stem)
                md = dict(self._fake_metadata())
                md["ExposureTime"] = target
                frames.append(CaptureResult(raw=path, preview=None, metadata=md))
            levels.append({"level": level, "ev": ev, "file_prefix": prefix,
                          "requested_us": target, "actual_us": target,
                          "actual_s": target / 1e6, "frame_count": int(n),
                          "frames": frames})
        return levels

    # --- video recording (fake: a real file exists, no real encoding) --------
    def start_recording(self, out_dir: Path, stem: str) -> Path:
        if self._recording_path is not None:
            raise RuntimeError("a recording is already in progress")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / (stem + ".mp4")
        path.write_bytes(b"")   # stand-in: file-based downstream steps get a real path
        self._recording_path = path
        return path

    def stop_recording(self) -> Path:
        if self._recording_path is None:
            raise RuntimeError("nothing is recording")
        path = self._recording_path
        self._recording_path = None
        return path

    def is_recording(self) -> bool:
        return self._recording_path is not None

    def set_video_resolution(self, resolution) -> None:
        w, h = resolution
        if w <= 0 or h <= 0:
            raise ValueError("resolution must be positive, got {!r}".format(resolution))
        if self.is_recording():
            raise RuntimeError("cannot change resolution while a recording is in progress")
        self._video_res = (int(w), int(h))

    def video_resolution(self):
        return self._video_res

    def lores_resolution(self):
        return (self._w, self._h)

    # --- sensor crop geometry (PRIORITY_click_mapping_fix.md) ---------------
    def preview_resolution(self):
        return self._preview_res

    def capture_resolution(self):
        return self._full_res

    def sensor_crop_for_size(self, size):
        # Delegates straight to imx477's own table: FakeCamera's
        # get_capabilities() already reports real IMX477 mode sizes, so
        # this is a plausible stand-in, not a fabricated one -- see the
        # module-level _imx477 import's own comment for why FakeCamera
        # (and only FakeCamera) may reference it directly.
        return _imx477.crop_for_size(size)

    # --- capability query ----------------------------------------------------
    def get_capabilities(self) -> dict:
        # Cached (Fix: Preferences dialog crash on get_capabilities()): see
        # Picamera2Camera's own get_capabilities for why this shape exists at
        # all -- FakeCamera mirrors it for consistency, not because its own
        # synthetic result could ever change.
        if self._capabilities is not None:
            return self._capabilities
        # A small, clearly-synthetic set -- plausible numbers, not real
        # hardware ones, so the Preferences dialog is buildable and testable
        # with no Pi. See __init__'s note on self._stream_caps.
        caps = {
            "capture_resolutions": [(4056, 3040), (2028, 1520), (1332, 990)],
            "capture_formats": ["SRGGB12", "SRGGB10"],
            "video_resolutions": [(1920, 1080), (1332, 990), (2048, 1080)],
            "video_formats": ["H264"],
        }
        if self._stream_caps:
            caps["stream_formats"] = ["YUY2", "MJPEG"]
            caps["stream_resolutions"] = [(1280, 720), (640, 480)]
        self._capabilities = caps
        return caps


# ---------------------------------------------------------------------------
# On-rig backend. Guarded like capture.py so this module imports on any machine
# (the FakeCamera above needs no camera); only constructing Picamera2Camera
# without the stack fails, and it fails clearly.
# ---------------------------------------------------------------------------
try:
    from picamera2 import Picamera2
    _HAVE_PICAMERA2 = True
except ImportError:
    _HAVE_PICAMERA2 = False


def _resolve_sensor_profile(model):
    """Resolve `model` (Picamera2().camera_properties['Model'], e.g.
    "imx477") to its sensor-profile module -- PRIORITY_click_mapping_fix.md's
    exact-name design: a direct import of the string the hardware itself
    reports, never a separate mapping table that could drift from what's
    actually attached. Restricted to a same-named .py file sitting right
    next to this one (never a same-named package elsewhere on sys.path,
    which importlib.import_module would otherwise happily resolve to) and
    to identifier-safe characters, so `model` can only ever resolve to one
    of this project's own sensor-profile modules. An unrecognised sensor
    raises, naming the real model -- never a silent fallback to some other
    sensor's geometry."""
    if not model or not re.fullmatch(r"[a-z0-9_]+", model):
        raise RuntimeError(
            "camera model {!r} is not a valid sensor-profile module name "
            "(expected lowercase letters/digits/underscore, matching "
            "camera_properties['Model'] exactly)".format(model))
    project_dir = Path(__file__).resolve().parent
    if not (project_dir / "{}.py".format(model)).is_file():
        raise RuntimeError(
            "no sensor profile for camera model {!r} -- add {}.py "
            "alongside camera_backend.py, matching imx477.py's contract "
            "(FULL_ARRAY_SIZE, crop_for_size)".format(model, model))
    return importlib.import_module(model)


class Picamera2Camera(CameraBackend):
    """Picamera2 implementation of the seam for the Pi HQ camera on a Pi 5.

    The GUI creates its QApplication first, then this backend, then embeds
    `self.widget` (the QGlPicamera2 preview) in the layout. Do NOT call
    start_preview: the embedded widget is the preview and Qt's exec() drives it.
    """

    def __init__(self, preview_res=PREVIEW_RES, lores_res=None,
                 full_res=FULL_RES):
        if not _HAVE_PICAMERA2:
            raise RuntimeError("picamera2 not available; this backend runs on the Pi. "
                               "Use FakeCamera off-rig.")
        # CAVEAT: the widget class name selects the Qt binding. picamera2's
        # previews/qt.py maps QGlPicamera2 -> PyQt5 and QGl6Picamera2 -> PyQt6
        # via module __getattr__; there is no auto-detection. Importing plain
        # QGlPicamera2 here builds a PyQt5 widget under a PyQt6 QApplication,
        # which aborts with "Must construct a QApplication before a QWidget".
        # Do not "simplify" this alias.
        from picamera2.previews.qt import QGl6Picamera2 as QGlPicamera2

        self._picam2 = Picamera2()
        self._preview_res = preview_res
        # lores_res=None (ROADMAP item 2, preview-resolution setting):
        # derive from preview_res's own aspect rather than defaulting to
        # the fixed 4:3 LORES_RES -- preview_res is no longer always 4:3
        # now that it's user-settable, and pairing an arbitrary main aspect
        # against a hardcoded 4:3 lores size is exactly the pairing
        # mismatch that broke focus aid before (see derive_lores_res). An
        # explicit lores_res override still wins, same as before.
        self._lores_res = lores_res if lores_res is not None else derive_lores_res(preview_res)
        self._full_res = full_res

        # Sensor-profile resolution (PRIORITY_click_mapping_fix.md): resolve
        # and cache once, by the hardware's OWN reported model name, never a
        # hardcoded "imx477" anywhere in this class. camera_properties is a
        # plain property dict populated at Picamera2() construction -- ON-RIG:
        # believed passive (unlike sensor_modes below, it should not trigger
        # a configure() sweep), but not independently confirmed on real
        # hardware; if it ever turns out to have a side effect, apply the
        # same read-immediately-after-Picamera2()-construction fix this class
        # already needed once for sensor_modes.
        self._sensor_profile = _resolve_sensor_profile(
            self._picam2.camera_properties["Model"])
        # Populated by get_capabilities() below, from the SAME sensor_modes
        # read that primes self._capabilities -- never a second sweep (see
        # that method's own comment on why sensor_modes can only be read once).
        self._mode_crops = None

        # Capability cache (Fix: Preferences dialog crash on
        # get_capabilities()): self._picam2.sensor_modes is not a passive
        # lookup -- reading it internally calls Picamera2.configure() once
        # per sensor mode to enumerate them, sweeping the camera through
        # every mode in turn and leaving it sitting in whatever the LAST
        # swept mode was (observed on-rig: main pinned at a small
        # placeholder size in an XBGR8888 format we never asked for, raw at
        # the final swept mode's own size/format, no lores stream at all --
        # sensor_modes never requests one). Nothing about that probe
        # re-applies our real config afterward. It MUST run here, before
        # self._preview_cfg is built and applied below via
        # self._picam2.configure() -- if it ran after (as it originally did,
        # at the end of __init__), the real preview config would be
        # clobbered by the probe's own leftover state, and neither start()
        # nor the QGlPicamera2 widget construction would ever see it, since
        # nothing re-applies self._preview_cfg once the probe has run.
        # sensor_modes describes fixed hardware capability -- it cannot
        # change between construction and any later point in this process --
        # so it is queried exactly once, HERE, and cached. get_capabilities()
        # below returns this cached value on every later call and never
        # touches _picam2 again (also why it is safe from the "camera must
        # be stopped before configuring" crash a live query after start()
        # would otherwise hit -- see get_capabilities()'s own docstring).
        self._capabilities = None
        self._capabilities = self.get_capabilities()

        # ON-RIG: RGB lores is Pi 5 + recent libcamera. If unavailable, set the
        # format to "YUV420" and source to "luma" (the score then runs on luma).
        # Some stacks only surface an unsupported format at configure/start, so
        # treat this whole block as a shakeout point.
        self._source = "green"
        self._preview_cfg = self._picam2.create_preview_configuration(
            main={"size": preview_res},
            lores={"size": self._lores_res, "format": "RGB888"},
            # RECORD BUTTON (separable): 6, not the 4 this started with.
            # create_video_configuration defaults to 6 precisely because an
            # encoder is a heavier, slower consumer than a display read, and
            # the Record button runs its encoder against THIS config's main
            # stream (no mode switch, see start_recording below), so the
            # widget needs the same headroom a video config would give it.
            # Costs a little memory continuously; buys not having to
            # reconfigure the camera at all when recording starts or stops.
            buffer_count=6,
        )
        # DIAGNOSTIC (temporary, lores decode-failure investigation): dumps the
        # config at the two points where it could diverge from what was
        # requested -- right after create_preview_configuration() builds it,
        # and again after configure() actually applies it -- so a missing
        # lores stream (or a resized main) can be pinned to construction vs.
        # libcamera negotiation instead of guessed at from symptoms alone.
        print("DIAGNOSTIC: preview config as returned by create_preview_configuration(): {}"
              .format(_summarize_camera_configuration(self._preview_cfg)), file=sys.stderr)

        # still config carries the raw plane, as capture.py does, so capture_still
        # can save a DNG.
        self._still_cfg = self._picam2.create_still_configuration(
            main={"size": full_res}, raw={"size": full_res}, buffer_count=2)

        # --- RECORD BUTTON (separable): video's own adjustable resolution,
        # NOT YET WIRED THROUGH. self._video_res and set_video_resolution()
        # below are dead code today: start_recording() does not build a
        # video config from self._video_res, it just start_encoder()s
        # stream "main" as-is (see start_recording's own history notes),
        # so the recorded file's actual resolution is preview_res, set
        # once above. A prior version of this comment described a future
        # Record-button rework's *intended* design (self._video_res
        # feeding a config built fresh inside start_recording()) as if it
        # were current behavior -- it misled at least one later planning
        # pass into assuming that wiring already existed (see HANDOFF.md's
        # "Decouple video resolution from preview" entry for the
        # correction). Kept as a placeholder for that rework, not because
        # it does anything yet. lores stays fixed at self._lores_res
        # (derived once, above, from preview_res) regardless of recording
        # state: it does double duty as both the widget's display source
        # during recording and the focus aid's own input.
        self._video_res = preview_res   # dead: nothing reads this yet

        self._picam2.configure(self._preview_cfg)

        # DIAGNOSTIC (temporary, see the matching print above): the config as
        # libcamera actually settled on, post-negotiation. Same shape as the
        # print above by construction, so a diff between the two pinpoints
        # whether a stream was lost during construction or during configure().
        print("DIAGNOSTIC: camera_configuration() after configure(): {}"
              .format(_summarize_camera_configuration(self._picam2.camera_configuration())),
              file=sys.stderr)

        # ON-RIG: confirm the QGlPicamera2 constructor kwargs on your version.
        self.widget = QGlPicamera2(self._picam2,
                                   width=preview_res[0], height=preview_res[1])

        # Live lores is served from a per-frame callback (see start / _stash_lores),
        # NOT a blocking capture, so focus_frame() never stalls the Qt thread. The
        # callback decodes a frame only when the GUI has asked for one, so we do not
        # pay a full array decode on every camera frame when the aid samples slower.
        self._lores_lock = threading.Lock()
        self._latest_lores = None
        self._want_frame = True
        self._suspend_lores = False   # raised across a still capture (no lores stream)
        self._latest_meta: Optional[dict] = None   # per-frame metadata for read_exposure()
        # Diagnostic only, not part of the CameraBackend contract: counts
        # successful make_array("lores") decodes in _stash_lores. If this
        # stays at 0 while the aid is on, focus_frame() is falling back to its
        # all-zero placeholder every tick (var=0 always -> the exact "score
        # 0.0000, fill 100%" symptom reported on-rig), meaning the lores
        # stream is not reaching this callback at all. qt_shell.py's tick
        # surfaces this directly instead of showing a numeric reading that
        # looks valid but is not.
        self.lores_frames_received = 0
        # These two distinguish WHY it's 0: post_callback never firing at all
        # (both stay 0) versus make_array("lores") raising on every real
        # frame (lores_decode_errors climbs and last_lores_error holds the
        # actual exception text) -- see _stash_lores/_classify_lores_error.
        self.lores_decode_errors = 0
        self.last_lores_error: Optional[str] = None
        # Captured once, at the first genuine decode failure (not every
        # frame -- see _stash_lores), from camera_configuration() itself
        # rather than request.config: request.config still lists 'lores'
        # for a still-mode request (the earlier "tried and failed" comment),
        # so it can't settle whether the ACTIVE config genuinely has no
        # lores stream. camera_configuration() is the one honest source for
        # that question.
        self.lores_config_at_failure: Optional[dict] = None

        self._ae_on = False           # default is a held exposure (apply_exposure_lock)
        self._awb_on = False
        self._long_exposure = False   # whether FrameDurationLimits is currently raised

        # Video recording state (see start_recording/stop_recording below).
        self._encoder = None
        self._recording_path: Optional[Path] = None

    def start(self) -> None:
        # ON-RIG: confirm make_array("lores") works in post_callback on your version.
        self._picam2.post_callback = self._stash_lores
        self._picam2.start()          # no start_preview: the widget is the preview

    def _stash_lores(self, request):
        # Runs once per camera frame, on the thread that also services the preview.
        # Decode the lores ONLY when focus_frame() has asked for one, so a full
        # array decode does not happen on every frame when the aid samples at, say,
        # 10 Hz. The request buffer is recycled after this call, so a wanted frame
        # must be copied out here; it cannot be decoded later.
        #
        # The metadata stash below runs unconditionally and is cheap: it is what
        # feeds read_exposure()'s live panel display, and it needs to keep working
        # even while a burst is mid-flight (still-mode requests still carry real
        # metadata; there is nothing lores-specific about get_metadata()).
        try:
            self._latest_meta = request.get_metadata()
        except Exception:
            pass

        # A still capture switches to a config with NO lores stream, yet this
        # preview callback still fires on those still-mode requests, and
        # make_array("lores") then raises "Stream 'lores' is not defined" which,
        # uncaught on this thread, aborts the process. Two guards, because the
        # obvious one is not enough:
        #   1. _suspend_lores is raised across a capture, so the callback stays
        #      inert while the still config is active. This is the real mechanism.
        #   2. request.config is NOT a reliable check: it still lists 'lores' for
        #      a still-mode request, so a config-membership test let the crash
        #      straight through (tried and failed). The decode below is wrapped
        #      as a backstop against any lores-less request instead.
        # Either way _want_frame is left set, so the next real preview frame
        # decodes and the aid resumes on its own.
        if self._suspend_lores or not self._want_frame:
            return
        try:
            arr = request.make_array("lores")
        except RuntimeError as exc:
            # The backstop above conflated two very different situations: a
            # still-mode request racing this callback (expected, silent, no
            # lores stream by design) and a preview-mode request whose lores
            # stream IS configured but fails to decode on every single frame
            # (a real backend defect -- e.g. libcamera rejecting this
            # main/lores size pairing -- previously invisible, surfacing only
            # as qt_shell.py's generic "no real lores frames received" after
            # a silent 2s). Re-checking _suspend_lores here, right at the
            # failure, is the same real mechanism as the guard above, just
            # evaluated after the race window instead of before it.
            if not _lores_error_is_expected(self._suspend_lores):
                self.lores_decode_errors += 1
                self.last_lores_error = str(exc)
                # First genuine failure only: the active config can't change
                # again without a fresh switch_mode/configure() call, which
                # this callback never triggers, so repeating this on every
                # one of what could be hundreds of failing frames would be
                # pure overhead on the hot per-frame preview thread for no
                # new information. Wrapped separately from make_array above
                # -- this diagnostic call must never be what turns a decode
                # failure into a crash on this thread.
                if self.lores_config_at_failure is None:
                    try:
                        cfg = self._picam2.camera_configuration()
                        self.lores_config_at_failure = _summarize_camera_configuration(cfg)
                    except Exception as cfg_exc:
                        self.lores_config_at_failure = {"error": str(cfg_exc)}
            return
        with self._lores_lock:
            self._latest_lores = arr
            self._want_frame = False
        self.lores_frames_received += 1

    def stop(self) -> None:
        self._picam2.stop()
        self._picam2.close()

    def focus_frame(self) -> LoresFrame:
        self._want_frame = True                 # ask the callback to decode a fresh one
        with self._lores_lock:
            arr = self._latest_lores
        if arr is None:
            # no frame delivered yet (the first ticks after start); return black so
            # the caller never blocks. The meter reads ~0 until a frame lands.
            data = np.zeros((self._lores_res[1], self._lores_res[0]), dtype=np.float32)
            return LoresFrame(data=data, source=self._source)
        if self._source == "green":
            data = arr[..., 1].astype(np.float32)         # RGB888 lores -> green
        else:
            # ON-RIG: YUV420 packs the Y plane in the top rows; stride/padding can
            # bite. Y is the first `height` rows, `width` columns.
            h = self._lores_res[1]
            data = arr[:h, :self._lores_res[0]].astype(np.float32)
        return LoresFrame(data=data, source=self._source)

    def set_overlay(self, rgba: Optional[np.ndarray]) -> None:
        # ON-RIG: the GL preview composites an RGBA overlay via the widget. Confirm
        # sizing/scaling behavior and how a clear (None) is expected on your version.
        self.widget.set_overlay(rgba)

    def capture_still(self, out_dir: Path, stem: str) -> Path:
        # Blocking mode switch, as capture.py's do_burst does. Off the Qt thread.
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        dng = out_dir / (stem + ".dng")
        self._suspend_lores = True                        # still config carries no lores
        self._picam2.switch_mode(self._still_cfg)
        try:
            request = self._picam2.capture_request()
            try:
                request.save("main", str(out_dir / (stem + ".jpg")))
                request.save_dng(str(dng))
            finally:
                request.release()
        finally:
            self._picam2.switch_mode(self._preview_cfg)   # ON-RIG: settle timing
            self._suspend_lores = False
        return dng

    def capture_still_async(self, out_dir: Path, stem: str,
                            on_done: Callable[[object], None]) -> None:
        # FIX (on-rig report): the previous version only ever fired
        # switch_mode_and_capture_request and relied on "the GUI layer" wiring
        # a Qt signal (self.widget.signal_done) to eventually deliver the
        # result to on_done -- but nothing anywhere actually connected to that
        # signal, so on_done was NEVER called on real hardware. Every single-
        # shot capture hung forever: _capturing never cleared, the button
        # stayed on whatever label the active capture set, and every later
        # capture action no-opped behind that stuck busy flag (this is what
        # looked like the capture menu/combo "doing nothing"). Off-rig testing
        # never caught it because FakeCamera's own capture_still_async
        # correctly calls on_done; only this backend's version was broken.
        # Replaced with the same plain-worker-thread pattern already used for
        # probe() and every burst/bracket method in this class, instead of
        # depending on Picamera2's job/signal-callback API.
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        def _worker():
            self._suspend_lores = True
            try:
                self._picam2.switch_mode(self._still_cfg)
                try:
                    result = self._save_still_request(out_dir, stem)
                finally:
                    self._picam2.switch_mode(self._preview_cfg)
            except Exception as exc:
                self._suspend_lores = False
                on_done(exc)
                return
            self._suspend_lores = False
            on_done(result)

        threading.Thread(target=_worker, daemon=True).start()

    # --- exposure control -------------------------------------------------
    def exposure_limits(self) -> dict:
        cc = self._picam2.camera_controls
        exp = cc.get("ExposureTime", (100, 10_000_000, None))
        gain = cc.get("AnalogueGain", (1.0, 16.0, None))
        return {"shutter_us": (int(exp[0]), int(exp[1])),
                "gain": (float(gain[0]), float(gain[1]))}

    def probe(self) -> dict:
        # capture.py's probe: meter with AE/AWB on, wait for AE to settle, read back.
        # BLOCKING on the settle, so the GUI calls this on a worker thread.
        import time
        self._picam2.set_controls({"AeEnable": True, "AwbEnable": True})
        self._ae_on = True
        self._awb_on = True
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if self._picam2.capture_metadata().get("AeLocked", False):
                break
        time.sleep(2.5)
        md = self._picam2.capture_metadata()
        cg = md.get("ColourGains", (1.0, 1.0))
        return {"shutter_us": int(round(md.get("ExposureTime", 0))),
                "analogue_gain": round(float(md.get("AnalogueGain", 1.0)), 4),
                "awb_red_gain": round(float(cg[0]), 4),
                "awb_blue_gain": round(float(cg[1]), 4)}

    def apply_exposure_lock(self, locked: dict) -> None:
        # capture.py's apply_lock: fixed exposure, AE/AWB off, sharpness off. Noise
        # reduction is left alone; the DNG is raw Bayer, which NR does not touch.
        self._picam2.set_controls({
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": int(locked["shutter_us"]),
            "AnalogueGain": float(locked["analogue_gain"]),
            "ColourGains": (float(locked["awb_red_gain"]), float(locked["awb_blue_gain"])),
            "Sharpness": 0.0,
        })
        self._ae_on = False
        self._awb_on = False

    def read_exposure(self) -> dict:
        md = self._latest_meta or {}
        cg = md.get("ColourGains", (1.0, 1.0))
        return {"shutter_us": int(round(md.get("ExposureTime", 0))),
                "analogue_gain": round(float(md.get("AnalogueGain", 1.0)), 4),
                "awb_red_gain": round(float(cg[0]), 4),
                "awb_blue_gain": round(float(cg[1]), 4),
                "auto_exposure": self._ae_on,
                "auto_white_balance": self._awb_on}

    def set_exposure(self, shutter_us=None, gain=None, red_gain=None,
                     blue_gain=None, auto_exposure=None, auto_white_balance=None) -> None:
        controls = {}
        if shutter_us is not None:
            controls["AeEnable"] = False
            controls["ExposureTime"] = int(shutter_us)
            self._ae_on = False
        if gain is not None:
            controls["AeEnable"] = False
            controls["AnalogueGain"] = float(gain)
            self._ae_on = False
        if red_gain is not None or blue_gain is not None:
            cur = (self._latest_meta or {}).get("ColourGains", (1.0, 1.0))
            r = red_gain if red_gain is not None else cur[0]
            b = blue_gain if blue_gain is not None else cur[1]
            controls["AwbEnable"] = False
            controls["ColourGains"] = (float(r), float(b))
            self._awb_on = False
        if auto_exposure is not None:
            controls["AeEnable"] = bool(auto_exposure)
            self._ae_on = bool(auto_exposure)
        if auto_white_balance is not None:
            controls["AwbEnable"] = bool(auto_white_balance)
            self._awb_on = bool(auto_white_balance)
        if controls:
            self._picam2.set_controls(controls)

    def set_long_exposure(self, enabled: bool, normal_max_us: int = None) -> None:
        # ON-RIG: FrameDurationLimits is the sensor's per-frame time budget; a
        # frame cannot expose longer than this. Raising it is what actually
        # makes shutter times beyond the normal preview cadence reachable, not
        # just a display change: ExposureTime itself is rejected/clamped by
        # libcamera if it exceeds this ceiling. 3_100_000 gives a little slack
        # above the 3.0s cap the shutter table itself enforces. The floor here
        # (100us) is independent of the ExposureTime floor, which is bounded
        # separately in exposure_limits(); it is not a meaningful lower bound
        # for a "long exposure" mode, just a safe minimum for the control.
        if enabled:
            self._picam2.set_controls({"FrameDurationLimits": (100, 3_100_000)})
        else:
            hi = int(normal_max_us) if normal_max_us else 50_000
            self._picam2.set_controls({"FrameDurationLimits": (100, hi)})
        self._long_exposure = bool(enabled)

    def _wait_for_exposure(self, target_us, tol=0.05, max_frames=12):
        # ON-RIG: mirrors capture.py's wait_for_exposure exactly (same
        # tolerance and frame budget) -- a set_controls change takes effect on
        # a LATER frame, not the next capture_request(), so a burst's first
        # frame can land at the old exposure without this settle wait.
        actual = 0
        for _ in range(max_frames):
            actual = self._picam2.capture_metadata().get("ExposureTime", 0)
            if target_us and abs(actual - target_us) <= tol * target_us:
                break
        return actual

    def _save_still_request(self, out_dir: Path, stem: str) -> CaptureResult:
        # One frame, ALREADY in still mode (caller's responsibility): the
        # burst methods below switch mode once for many frames, unlike
        # capture_still which switches per call. Returns a CaptureResult, not
        # a bare path, so the caller (record_burst, in qt_shell.py) can write
        # a .meta.json sidecar per frame the same way the single-shot path
        # already does off a CaptureResult.
        dng = out_dir / (stem + ".dng")
        jpg = out_dir / (stem + ".jpg")
        request = self._picam2.capture_request()
        try:
            request.save("main", str(jpg))
            request.save_dng(str(dng))
            md = request.get_metadata()
        finally:
            request.release()
        return CaptureResult(raw=dng, preview=jpg, metadata=md)

    def capture_burst(self, out_dir: Path, prefix: str, n: int,
                      shutter_us: int = None) -> dict:
        # ON-RIG: one still-mode session for the whole burst, mirroring
        # capture.py's do_burst -- switching per frame would pay the mode-
        # switch cost n times over.
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._suspend_lores = True
        self._picam2.switch_mode(self._still_cfg)
        try:
            target = int(shutter_us) if shutter_us is not None \
                else int(self.read_exposure()["shutter_us"])
            self._picam2.set_controls({"ExposureTime": target})
            actual = self._wait_for_exposure(target)
            frames = [self._save_still_request(out_dir, "{}frame_{:04d}".format(prefix, i))
                     for i in range(int(n))]
        finally:
            self._picam2.switch_mode(self._preview_cfg)
            self._suspend_lores = False
        return {"actual_us": actual, "frames": frames}

    def enter_still_mode(self) -> None:
        self._suspend_lores = True
        self._picam2.switch_mode(self._still_cfg)

    def exit_still_mode(self, restore_shutter_us: int) -> None:
        # FIX (on-rig report): the preview was resuming at whatever exposure the
        # LAST bracket level left behind, not the restored value. Root cause:
        # set_controls takes effect on a later frame (documented above, in
        # _wait_for_exposure's comment), so switching back to preview
        # immediately after requesting the restore raced that settle. Confirming
        # it first, the same way each bracket level already does, closes that gap.
        self._picam2.set_controls({"ExposureTime": int(restore_shutter_us)})
        self._wait_for_exposure(int(restore_shutter_us))
        self._picam2.switch_mode(self._preview_cfg)
        self._suspend_lores = False

    def capture_bracket_phase(self, out_dir: Path, prefix_template: str, n: int,
                              base_us: int, stops: list) -> list:
        # ON-RIG: assumes still mode is already active (enter_still_mode
        # called by the caller); no mode switch here, so an HDR sequence runs
        # two phases (science, then dark) under one still-mode session,
        # mirroring capture.py's bracket_burst_phase exactly.
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        levels = []
        for level, ev in enumerate(stops, start=1):
            target = int(round(base_us * (2.0 ** ev)))
            self._picam2.set_controls({"ExposureTime": target})
            actual = self._wait_for_exposure(target)
            prefix = "{}{}_".format(prefix_template, level)
            frames = [self._save_still_request(out_dir, "{}frame_{:04d}".format(prefix, i))
                     for i in range(int(n))]
            levels.append({"level": level, "ev": ev, "file_prefix": prefix,
                          "requested_us": target, "actual_us": actual,
                          "actual_s": (actual / 1e6) if actual else None,
                          "frame_count": int(n), "frames": frames})
        return levels

    # --- video recording ----------------------------------------------------
    def start_recording(self, out_dir: Path, stem: str) -> Path:
        # Documented pattern (Raspberry Pi's own apps/app_recording.py, which
        # combines QGlPicamera2 with recording): when the camera is ALREADY
        # running -- which it always is here, the preview widget depends on
        # it -- toggle recording with start_encoder()/stop_encoder() only.
        # Never start_recording()/stop_recording(): those are convenience
        # wrappers that also start and STOP THE CAMERA ITSELF (confirmed by a
        # Picamera2 maintainer), which yanks the camera out from under the
        # live preview and prevents the encoder's output from finalizing.
        #
        # That single fact explains the whole on-rig history of this feature:
        #   1. start_recording() on shared "main": pane froze (camera pulled
        #      out from under the widget), though the file did finalize since
        #      stop_recording()'s camera-stop came after its encoder-stop.
        #   2/3/4. Mode-switching to a dedicated video config was layered on
        #      next, assuming buffer contention. It surfaced real, separate
        #      problems (QGlPicamera2 cannot render RGB888) and added a
        #      visible pane resize and an exposure shift on every switch,
        #      but never fixed the underlying cause, so no file was written.
        # So: no switch_mode here at all, no camera stop, and lores is NOT
        # suspended -- nothing about the camera's configuration changes when
        # recording starts, which is also why the pane no longer resizes or
        # shifts exposure. The one concession to the documented contention
        # risk is the preview config's buffer_count, raised to 6 (see
        # __init__) so the encoder sharing "main" cannot starve the widget.
        if self._encoder is not None:
            raise RuntimeError("a recording is already in progress")
        from picamera2.encoders import H264Encoder
        from picamera2.outputs import FfmpegOutput
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / (stem + ".mp4")
        encoder = H264Encoder()
        output = FfmpegOutput(str(path))
        # output passed POSITIONALLY, matching Raspberry Pi's own
        # apps/app_recording.py (picam2.start_encoder(encoder, output)).
        # The previous version set encoder.output as an attribute instead and
        # produced no file at all -- start_encoder is what actually starts the
        # output object, and an output it was never handed does not get
        # started, so nothing was ever written to disk.
        self._picam2.start_encoder(encoder, output, name="main")
        self._encoder = encoder
        self._recording_path = path
        return path

    def stop_recording(self) -> Path:
        # stop_encoder(), not stop_recording(): see start_recording's note --
        # stop_recording() would stop the camera itself, breaking the live
        # preview. Note this method keeps its name (the CameraBackend verb),
        # only the Picamera2 call underneath changed.
        # try/finally so a failure part-way through still clears this
        # object's own recording state; otherwise is_recording() would stay
        # True forever and the GUI's Record button could never recover.
        if self._encoder is None:
            raise RuntimeError("nothing is recording")
        path = self._recording_path
        try:
            self._picam2.stop_encoder()
        finally:
            self._encoder = None
            self._recording_path = None
        # Verify rather than assume. This feature has now failed silently
        # several times on real hardware -- folder empty, GUI still cheerfully
        # reporting "saved" -- so confirm the file actually exists and has
        # content before claiming success. A raised error here surfaces in
        # the GUI's own status line instead of a lie.
        if not path.exists():
            raise RuntimeError(
                "recording stopped but no file was written to {}".format(path))
        if path.stat().st_size == 0:
            raise RuntimeError(
                "recording stopped but {} is empty (0 bytes)".format(path.name))
        return path

    def is_recording(self) -> bool:
        return self._encoder is not None

    def set_video_resolution(self, resolution) -> None:
        # RECORD BUTTON (separable). IMPORTANT, currently has NO EFFECT on
        # what actually gets recorded, and that is deliberate rather than a
        # bug left in place. The encoder-only pattern start_recording now
        # uses (see its note) never reconfigures the camera, so a recording
        # always encodes the preview config's own main stream, fixed at
        # PREVIEW_RES (1332x990) when the camera starts up. Changing the
        # recorded resolution therefore means building _preview_cfg with a
        # different main size at STARTUP, not switching modes at record time,
        # which is exactly what mode-switching cost us: a visible pane
        # resize, an exposure shift, and no output file at all.
        # So the future resolution menu will need to either set this before
        # the camera is constructed, or restart the camera to apply it. The
        # setter and its validation stay here so that menu has a stable
        # place to write to, and so the intent survives in one piece; it is
        # honest about not being wired through yet rather than silently
        # doing nothing. Worth knowing for whoever builds it: full sensor
        # res only reaches ~10fps, not needed for documentation/review
        # footage, so the anticipated options are 1080p and 2K, not FULL_RES.
        w, h = resolution
        if w <= 0 or h <= 0:
            raise ValueError("resolution must be positive, got {!r}".format(resolution))
        if self.is_recording():
            raise RuntimeError("cannot change resolution while a recording is in progress")
        self._video_res = (int(w), int(h))

    def video_resolution(self):
        return self._video_res

    def lores_resolution(self):
        return self._lores_res

    # --- sensor crop geometry (PRIORITY_click_mapping_fix.md) ---------------
    def preview_resolution(self):
        return self._preview_res

    def capture_resolution(self):
        return self._full_res

    def sensor_crop_for_size(self, size):
        if self._mode_crops is None:
            # Not yet built -- shouldn't happen in practice, since
            # get_capabilities() already runs in __init__, but force it here
            # too rather than assume, reusing the same cached sensor_modes
            # read (get_capabilities() never re-touches _picam2 once cached).
            self.get_capabilities()
        key = (int(size[0]), int(size[1]))
        if key in self._mode_crops:
            return self._mode_crops[key]
        # A size this real sensor_modes read didn't cover (shouldn't happen
        # for preview_resolution()/capture_resolution(), which are always
        # sensor-mode sizes themselves) -- fall back to the profile's own
        # static table rather than raising outright.
        return self._sensor_profile.crop_for_size(key)

    # --- capability query (PLAN_02_camera_capability_query.md;
    # PLAN_fix_capabilities_cache.md for the caching added after) ----------
    def get_capabilities(self) -> dict:
        # Cached: see __init__'s own note on why this cannot be a live query
        # every call -- self._picam2.sensor_modes triggers an internal
        # configure(), which raises if the camera is already running (the
        # real crash this caching exists to close). Computed once, at
        # construction, before start() can possibly have run; every later
        # call (e.g. from PreferencesDialog while the preview is live)
        # returns the cached dict without touching _picam2 again.
        if self._capabilities is not None:
            return self._capabilities
        # ON-RIG: reads self._picam2.sensor_modes and translates every value
        # to a plain Python primitive before it crosses the seam. sensor_modes'
        # own "format" field is a libcamera PixelFormat object -- NEVER read
        # it here; "unpacked" is already a plain string (e.g. "SRGGB12") and
        # is what capture_formats reports instead. Do not filter this list to
        # a "sensible" subset (Brandon's note: the existing resolution menus
        # are already too sparse) -- whatever sensor_modes contains is what
        # gets reported, unusual entries included.
        modes = self._picam2.sensor_modes
        sizes = sorted({(int(m["size"][0]), int(m["size"][1])) for m in modes})
        formats = sorted({str(m["unpacked"]) for m in modes if "unpacked" in m})

        # Crop geometry (PRIORITY_click_mapping_fix.md): built from this SAME
        # sensor_modes read, never a second self._picam2.sensor_modes access
        # (see __init__'s and this method's own notes on why that sweep must
        # only ever run once). "crop_limits" is already a plain tuple in
        # picamera2's sensor_modes (unlike "format"), but cast defensively
        # anyway -- no libcamera-typed value may cross this seam, full stop.
        # A mode missing "crop_limits" entirely is simply not entered here;
        # sensor_crop_for_size() falls back to the static profile table for
        # any size that leaves uncovered.
        self._mode_crops = {
            (int(m["size"][0]), int(m["size"][1])):
                tuple(int(v) for v in m["crop_limits"])
            for m in modes if "crop_limits" in m
        }

        # video_resolutions reuses the same sensor-mode sizes. Picamera2's
        # main stream can technically be scaled to an arbitrary size via the
        # ISP, but there is no discrete "supported list" for that the way
        # sensor_modes gives one for capture -- offering the sizes the sensor
        # modes themselves report is genuine hardware information, not a
        # fabricated continuous range collapsed into a fixed list.
        caps = {
            "capture_resolutions": sizes,
            "capture_formats": formats,
            "video_resolutions": sizes,
            "video_formats": ["H264"],   # the only encoder start_recording() uses
        }
        # No stream server exists in this backend yet, so stream_formats/
        # stream_resolutions are omitted entirely -- absent, not empty. That
        # is the honest answer today; a future streaming feature adds the
        # keys here when it actually exists, not before.
        self._capabilities = caps
        return caps


# ---------------------------------------------------------------------------
# Pure classification for Picamera2Camera._stash_lores's RuntimeError guard.
# Kept at module level, taking only the plain bool _stash_lores already has
# rather than a Picamera2Camera instance, so it's testable with no hardware
# and no request/make_array fake to build -- Picamera2Camera itself can't be
# constructed off-rig at all (see class docstring).
# ---------------------------------------------------------------------------
def _lores_error_is_expected(suspend_lores: bool) -> bool:
    """True if a RuntimeError from request.make_array("lores") is the known,
    silent, still-mode race (_suspend_lores already True again by the time
    the exception is caught -- see _stash_lores's own comment on why this is
    checked here rather than trusted from the earlier guard alone). False
    means the lores stream is configured but genuinely failing to decode,
    which the caller must not swallow without a trace."""
    return suspend_lores


def _summarize_camera_configuration(cfg: dict) -> dict:
    """Plain-typed snapshot of Picamera2.camera_configuration()'s own dict,
    for _stash_lores's decode-failure diagnostic (lores_config_at_failure).
    Pulls only "size"/"format" out of each stream entry (main/lores/raw) --
    the full dict carries libcamera objects (Transform, ColorSpace) that
    aren't safe to hold or print. Whether "lores" is present at all is the
    headline fact this exists to answer (candidate: create_preview_
    configuration() silently dropped it during its own validation), but
    it's paired with main's own size/format rather than reported alone,
    since the leading hypothesis is about the RELATIONSHIP between them
    (an aspect mismatch, a downscale-ratio ceiling), not lores's absence in
    isolation."""
    present = [name for name in ("main", "lores", "raw") if cfg.get(name) is not None]
    out = {"streams_present": sorted(present)}
    for name in present:
        stream = cfg[name]
        out[name] = {
            "size": tuple(int(v) for v in stream["size"]) if "size" in stream else None,
            "format": str(stream.get("format")),
        }
    return out


# ---------------------------------------------------------------------------
# Structural self-check support (PLAN_02_camera_capability_query.md):
# camera_backend.py is the only file in this project allowed to know
# Picamera2/libcamera exist. Kept at module level (not nested in the
# self-check block below) so any future test module can import and reuse it.
# ---------------------------------------------------------------------------
def _assert_plain_types(value, path="capabilities"):
    """No Picamera2/libcamera object may cross the get_capabilities()
    boundary -- only dicts, lists/tuples, str, bool, int, float. Recurses
    into containers; raises AssertionError naming the offending path."""
    if isinstance(value, dict):
        for k, v in value.items():
            assert isinstance(k, str), "{}: non-str key {!r}".format(path, k)
            _assert_plain_types(v, "{}[{!r}]".format(path, k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _assert_plain_types(v, "{}[{}]".format(path, i))
    else:
        assert isinstance(value, (str, int, float, bool)), (
            "{}: non-plain type {!r} ({!r}) crossed the get_capabilities() "
            "boundary".format(path, type(value), value))


def assert_only_camera_backend_imports_picamera2():
    """Structural half of "camera_backend.py is a driver, not an adapter"
    (PLAN_02_camera_capability_query.md): scans every other .py file in
    this project for a direct `picamera2`/`libcamera` import.

    `wizard_pages.py` (an availability probe, `from picamera2 import
    Picamera2  # noqa`) and `test_burst_backend.py` (a direct on-rig
    hardware test) both predate this rule and are the one documented,
    out-of-scope exception -- rearchitecting them is a separate concern
    from Part 01/Part 02, not touched by this plan set. Every OTHER file
    must come up clean, so a future regression can't land silently."""
    project_dir = Path(__file__).resolve().parent
    forbidden = re.compile(r'^\s*(from|import)\s+(picamera2|libcamera)\b', re.MULTILINE)
    exceptions = {"wizard_pages.py", "test_burst_backend.py"}
    offenders = []
    for path in sorted(project_dir.glob("*.py")):
        if path.name == "camera_backend.py" or path.name in exceptions:
            continue
        if forbidden.search(path.read_text()):
            offenders.append(path.name)
    assert not offenders, (
        "only camera_backend.py (and the documented exceptions {}) may "
        "import picamera2/libcamera directly -- found a violation in: {}"
        .format(sorted(exceptions), offenders))


def _sensor_profile_module_names(project_dir):
    """Discover sensor-profile modules by SHAPE, never by importing them
    (matches this file's existing structural-check style above -- a
    source-text scan, not a runtime import, so this never has side
    effects or a maintained list to go stale). A sensor-profile module is
    any top-level .py file (other than camera_backend.py) that defines
    both FULL_ARRAY_SIZE and crop_for_size at module level -- imx477.py's
    own contract. A future imx519.py etc. is covered automatically the
    moment it exists, with nothing here to remember to update."""
    names = set()
    for path in sorted(project_dir.glob("*.py")):
        if path.name == "camera_backend.py":
            continue
        src = path.read_text()
        if re.search(r'^FULL_ARRAY_SIZE\s*=', src, re.MULTILINE) and \
           re.search(r'^def crop_for_size\(', src, re.MULTILINE):
            names.add(path.stem)
    return names


def assert_only_camera_backend_imports_sensor_profiles():
    """Structural half of PHILOSOPHY.md's sensor-profile rule
    (PRIORITY_click_mapping_fix.md's follow-up correction to that rule,
    after review flagged the original wording as no longer checkable):
    sensor-profile modules (imx477.py today, any future sibling matching
    a hardware model name) may be imported ONLY by camera_backend.py.
    Scans every OTHER .py file for a direct import of a discovered
    profile module -- 'import NAME', 'from NAME import ...', or
    'from . import ... NAME ...' (the try-relative-then-bare pattern this
    project's other cross-module imports already use)."""
    project_dir = Path(__file__).resolve().parent
    profile_names = _sensor_profile_module_names(project_dir)
    if not profile_names:
        return
    offenders = []
    for path in sorted(project_dir.glob("*.py")):
        if path.name == "camera_backend.py" or path.stem in profile_names:
            continue
        src = path.read_text()
        for name in profile_names:
            pattern = re.compile(
                r'^\s*(?:from\s+\.\s+import\s+[\w,\s]*\b{0}\b'
                r'|import\s+{0}\b'
                r'|from\s+{0}\s+import\b)'.format(re.escape(name)),
                re.MULTILINE)
            if pattern.search(src):
                offenders.append((path.name, name))
    assert not offenders, (
        "only camera_backend.py may import a sensor-profile module -- "
        "found a violation: {}".format(offenders))


def _sensor_profile_dimension_pairs(project_dir):
    """Forbidden (w, h) pairs derived from the loaded sensor profile(s),
    never a maintained list: profile modules are discovered by the same
    shape predicate _sensor_profile_module_names already uses, then each
    one's FULL_ARRAY_SIZE and every _CROP_TABLE key are read LIVE off the
    module (an actual import, not a copied number). Both axis orders are
    included -- a numpy array's own .shape is (rows, cols), i.e. (h, w),
    the reverse of the profile's own (w, h) convention, and Stage 3 Step
    0's own reference recorded exactly this reversed form,
    (1520, 2028), as the shape load_measurement_plane compares against.
    Each pair's own integer halves are included too -- GREEN_PLANE_RES's
    entire defect is "some sensor dimension, halved," and half a
    dimension is still a fact about the sensor, not a free-standing
    number."""
    names = _sensor_profile_module_names(project_dir)
    pairs = set()
    for name in sorted(names):
        module = importlib.import_module(name)
        sizes = {tuple(int(v) for v in module.FULL_ARRAY_SIZE)}
        if hasattr(module, "_CROP_TABLE"):
            sizes |= {tuple(int(v) for v in size) for size in module._CROP_TABLE}
        for w, h in sizes:
            pairs.add((w, h))
            pairs.add((h, w))
            pairs.add((w // 2, h // 2))
            pairs.add((h // 2, w // 2))
    return pairs


def _production_region_source(path):
    """A file's own source with two kinds of self-check/test code blanked
    out (lines replaced by a blank line each, so every remaining token's
    own line number is unchanged -- a hit still gets reported at its real
    line) -- so a self-check's own plausible-but-arbitrary test fixture
    (a hash round-trip's stand-in array, a UI combo box's test value, a
    diagnostic formatter's sample dict, a structural check's own deliberate
    real-mode-size probe) never trips a scan meant to catch a PRODUCTION
    assumption about the sensor's true geometry:

      1. Everything from the file's own render_check()/`if __name__ ==
         "__main__":` self-check entry point onward, whichever comes
         first -- verified against this project's own sequence-1 baseline
         scan, where 12 of 13 hand-found hits sat inside exactly this
         region.
      2. Every top-level function whose name starts with `assert_` --
         this codebase's own established convention for a standalone
         structural check living OUTSIDE render_check() (this file's own
         assert_only_camera_backend_imports_picamera2 is the original
         example; qt_shell.py grew two more in Stage 3 sequence 3, one of
         which legitimately needs to invoke sensor_crop_for_size with real
         mode sizes to prove a round trip -- discovered when sequence 3's
         own build first ran this check, exactly the deviation its own
         intent entry named as possible). Discovered by `ast`-parsing the
         file for module-level FunctionDef nodes, never a maintained list
         of check-function names."""
    src = path.read_text()
    lines = src.splitlines(keepends=True)
    cut_line = len(lines) + 1
    for pattern in (r'^def render_check\(', r'^if __name__ == "__main__":'):
        m = re.search(pattern, src, re.MULTILINE)
        if m is not None:
            cut_line = min(cut_line, src.count("\n", 0, m.start()) + 1)
    blank = set(range(cut_line, len(lines) + 1))
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.startswith("assert_"):
                blank |= set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return "".join("\n" if (i + 1) in blank else line for i, line in enumerate(lines))


def assert_no_hardcoded_sensor_dimension_above_driver_layer():
    """SWEEP_CHECKS.md's Geometry-derivation "no hardcoded sensor
    dimension above the driver layer" row -- previously marked Implemented
    on `assert_only_camera_backend_imports_sensor_profiles`'s evidence,
    which is wrong: an import check does not test for a hardcoded
    dimension. This is the check that actually does.

    Tokenizes (`_source_without_docs_and_comments`'s own technique --
    strip comments/strings via `tokenize`, never a regex -- adapted here
    to a whole-file sweep rather than one object's source) every
    non-driver .py file's PRODUCTION region (see
    _production_region_source) for two adjacent NUMBER tokens forming a
    pair in _sensor_profile_dimension_pairs' forbidden set. Reports every
    hit for a human to review; a genuine false positive gets recorded and
    asked about, never silently filtered by this function itself."""
    project_dir = Path(__file__).resolve().parent
    profile_names = _sensor_profile_module_names(project_dir)
    forbidden = _sensor_profile_dimension_pairs(project_dir)
    exempt = {"camera_backend.py"} | {name + ".py" for name in profile_names}
    skip_types = {tokenize.COMMENT, tokenize.STRING, tokenize.NL,
                 tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                 tokenize.ENCODING, tokenize.ENDMARKER}
    hits = []
    for path in sorted(project_dir.glob("*.py")):
        if path.name in exempt:
            continue
        src = _production_region_source(path)
        try:
            toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline)
                   if t.type not in skip_types]
        except tokenize.TokenizeError:
            continue
        for i in range(len(toks) - 2):
            a, comma, b = toks[i], toks[i + 1], toks[i + 2]
            if (a.type == tokenize.NUMBER and comma.type == tokenize.OP and
                    comma.string == "," and b.type == tokenize.NUMBER):
                try:
                    pair = (int(a.string), int(b.string))
                except ValueError:
                    continue
                if pair in forbidden:
                    hits.append("{}:{} {!r}".format(path.name, a.start[0], pair))
    assert not hits, (
        "hardcoded sensor dimension(s) found above the driver layer, "
        "production region only (see _production_region_source): {}"
        .format(hits))


def _bit_depth_and_white_level_literals(project_dir):
    """Forbidden single-number values derived from the loaded profile,
    never a maintained list: each discovered profile module's own
    BIT_DEPTH, plus the white level that bit depth derives for this
    project's own uint16 raw-storage convention (white_level_for_bit_
    depth's default container_bits=16). A profile with no BIT_DEPTH
    attribute contributes nothing -- absence here means "not yet given
    this treatment," not an error, so a future profile module can still
    satisfy the shape predicate the other structural checks use without
    also having this attribute from day one."""
    names = _sensor_profile_module_names(project_dir)
    forbidden = set()
    for name in sorted(names):
        module = importlib.import_module(name)
        bit_depth = getattr(module, "BIT_DEPTH", None)
        if bit_depth is None:
            continue
        forbidden.add(int(bit_depth))
        forbidden.add(white_level_for_bit_depth(int(bit_depth)))
    return forbidden


def assert_no_hardcoded_bit_depth_or_white_level_above_driver_layer():
    """A sibling to assert_no_hardcoded_sensor_dimension_above_driver_
    layer, same infrastructure (_production_region_source, tokenize not
    grep), different shape: single NUMBER tokens, not adjacent pairs,
    against a forbidden set derived live from the profile's own BIT_
    DEPTH and its derived white level (today, {12, 65520}).

    Covers: a literal bit-depth or white-level number sitting in a
    non-driver file's production code. Does NOT cover: a value computed
    through a DIFFERENT formula that happens to land on a number outside
    this forbidden set (e.g. a hypothetical wrong derivation that still
    produced some other number would pass this scan silently -- this
    scan catches hardcoding, not incorrect derivation; the substitution
    check in hdr_merge.py's own render_check covers correctness of the
    derivation itself).

    Expect false positives on small integers (BIT_DEPTH today is 12, a
    number that appears throughout any nontrivial codebase for unrelated
    reasons) -- this scan's own first run found exactly two,
    _KNOWN_FALSE_POSITIVES below, both reviewed by direct reading (not
    filtered silently -- recorded, with the reasoning, at the point they
    were found: CHANGELOG.md's own intent entry for this work) and
    excluded here as a documented (file, value) pair, the same shape
    assert_only_camera_backend_imports_picamera2's own `exceptions` set
    already uses for a different kind of exception. Not a maintained
    list of GOOD code, only of the specific coincidental collisions
    already found and reviewed -- a NEW hit, on a different file or a
    different value, still fails loudly."""
    _KNOWN_FALSE_POSITIVES = {
        ("annotations.py", 12),   # pixel_sha256[:12], a string-slice index
        ("ca_measure.py", 12),    # N_RADIAL_BINS = 12, a CA curve-fit parameter
    }
    project_dir = Path(__file__).resolve().parent
    profile_names = _sensor_profile_module_names(project_dir)
    forbidden = _bit_depth_and_white_level_literals(project_dir)
    exempt = {"camera_backend.py"} | {name + ".py" for name in profile_names}
    skip_types = {tokenize.COMMENT, tokenize.STRING, tokenize.NL,
                 tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
                 tokenize.ENCODING, tokenize.ENDMARKER}
    hits = []
    for path in sorted(project_dir.glob("*.py")):
        if path.name in exempt:
            continue
        src = _production_region_source(path)
        try:
            toks = tokenize.generate_tokens(io.StringIO(src).readline)
            for t in toks:
                if t.type != tokenize.NUMBER:
                    continue
                try:
                    v = int(t.string)
                except ValueError:
                    continue
                if v in forbidden and (path.name, v) not in _KNOWN_FALSE_POSITIVES:
                    hits.append("{}:{} {!r}".format(path.name, t.start[0], v))
        except tokenize.TokenizeError:
            continue
    assert not hits, (
        "hardcoded bit-depth or white-level literal(s) found above the "
        "driver layer, production region only (see "
        "_production_region_source): {}".format(hits))


if __name__ == "__main__":
    # Self-check with no hardware: sweep the fake through focus, exercise the
    # exposure surface, the async capture path, and the two burst primitives.
    import shutil
    import tempfile

    def _lap_var(a: np.ndarray) -> float:
        k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
        p = np.pad(a, 1, mode="reflect")
        out = np.zeros_like(a, dtype=np.float32)
        for dy in range(3):
            for dx in range(3):
                if k[dy, dx]:
                    out += k[dy, dx] * p[dy:dy + a.shape[0], dx:dx + a.shape[1]]
        return float(out.var())

    cam = FakeCamera()
    rc_root = Path(tempfile.mkdtemp(prefix="zynergy_camera_backend_render_check_"))
    with cam:
        print("focus_position -> laplacian variance (fake, peaks at best_focus=0):")
        for z in [-3, -2, -1, 0, 1, 2, 3]:
            cam.focus_position = float(z)
            fr = cam.focus_frame()
            print(f"  z={z:+d}  source={fr.source:5s}  score={_lap_var(fr.data):10.4f}")
        cam.set_overlay(np.zeros((10, 10, 4), dtype=np.uint8))
        print("overlay set:", cam.last_overlay is not None)

        p = cam.capture_still(rc_root / "fake", "selfcheck")
        print("still written:", p)

        # Non-blocking capture (fake): fire it, wait for the callback (which lands
        # off the calling thread, per the seam contract), and confirm the delivered
        # result carries a CaptureResult with a real file.
        done = threading.Event()
        delivered = {}

        def _on_capture(result):
            delivered["result"] = result
            done.set()

        cam.capture_still_async(rc_root / "fake", "selfcheck_async", _on_capture)
        fired = done.wait(timeout=2.0)
        assert fired and isinstance(delivered["result"], CaptureResult), \
            "async capture did not deliver a CaptureResult"
        assert delivered["result"].raw.exists(), "async capture's file does not exist"
        print("async capture fired ->", delivered["result"].raw)

        # Capture-enforces-lock, at the CameraBackend seam: a metered snapshot goes
        # into apply_exposure_lock, and auto drops on both channels with the exact
        # metered values held. The Qt half (the sliders/checkboxes
        # _enforce_exposure_lock also updates) needs PyQt6 to run and is not
        # exercised here.
        lockcam = FakeCamera()
        lockcam.set_exposure(auto_exposure=True, auto_white_balance=True)
        metered = lockcam.read_exposure()
        assert metered["auto_exposure"] and metered["auto_white_balance"], \
            "expected auto on before enforcing a lock"
        lockcam.apply_exposure_lock({k: metered[k] for k in
            ("shutter_us", "analogue_gain", "awb_red_gain", "awb_blue_gain")})
        locked = lockcam.read_exposure()
        assert not locked["auto_exposure"] and not locked["auto_white_balance"], \
            "lock did not drop auto exposure/white balance"
        assert (locked["shutter_us"], locked["analogue_gain"]) == \
               (metered["shutter_us"], metered["analogue_gain"]), \
            "locked values drifted from the metered snapshot taken just before the lock"
        print("capture-lock check PASS: metered snapshot -> apply_exposure_lock -> auto off, values held")

        # Burst: flat/science/dark-style single-exposure burst. One call, n frames,
        # all reporting the same actual_us.
        burst_dir = rc_root / "fake_burst"
        b = cam.capture_burst(burst_dir, "flat_", 3, shutter_us=5000)
        assert b["actual_us"] == 5000, "capture_burst did not honor the explicit shutter_us"
        assert len(b["frames"]) == 3, "capture_burst frame count off"
        assert all(isinstance(f, CaptureResult) for f in b["frames"]), \
            "capture_burst frames are not CaptureResult"
        assert all(f.raw.exists() for f in b["frames"]), "capture_burst wrote no file for a frame"
        print("capture_burst PASS: {} frames at {}us".format(len(b["frames"]), b["actual_us"]))

        # HDR bracket phase: enter_still_mode once, run two phases (science, then
        # dark) under that one session, exit_still_mode once at the end. Levels
        # must double monotonically with the EV spacing.
        cam.enter_still_mode()
        sci = cam.capture_bracket_phase(burst_dir, "", 2, 10_000, [-1.0, 0.0, 1.0])
        dark = cam.capture_bracket_phase(burst_dir, "dark_", 2, 10_000, [-1.0, 0.0, 1.0])
        cam.exit_still_mode(8000)
        assert [lv["actual_us"] for lv in sci] == [5000, 10000, 20000], \
            "bracket levels did not double monotonically"
        assert cam.read_exposure()["shutter_us"] == 8000, \
            "exit_still_mode did not restore the locked shutter"
        assert dark[0]["file_prefix"] == "dark_1_", "dark bracket file prefix wrong"
        total_frames = sum(lv["frame_count"] for lv in sci) + sum(lv["frame_count"] for lv in dark)
        assert total_frames == 12, "unexpected total HDR frame count"
        print("capture_bracket_phase PASS: {} science + {} dark frames across {} levels, "
              "shutter restored on exit".format(
                  sum(lv["frame_count"] for lv in sci), sum(lv["frame_count"] for lv in dark),
                  len(sci)))

        # Video recording: start/stop lifecycle, mutual-exclusion guards, and
        # is_recording() tracking correctly. Not a measurement path, so just
        # the file lifecycle matters here, not its content.
        assert cam.is_recording() is False, "a fresh camera should not be recording"
        vid_dir = rc_root / "fake_video"
        vpath = cam.start_recording(vid_dir, "clip_0001")
        assert vpath.exists() and vpath.suffix == ".mp4", "start_recording did not write an .mp4"
        assert cam.is_recording() is True
        try:
            cam.start_recording(vid_dir, "clip_0002")
            raise AssertionError("expected RuntimeError: a recording was already in progress")
        except RuntimeError:
            pass
        stopped = cam.stop_recording()
        assert stopped == vpath, "stop_recording returned a different path than start_recording gave"
        assert cam.is_recording() is False
        try:
            cam.stop_recording()
            raise AssertionError("expected RuntimeError: nothing was recording")
        except RuntimeError:
            pass
        print("video recording check PASS: start/stop lifecycle, mutual-exclusion "
              "guards, is_recording() tracks correctly")

        # Resolution is adjustable ahead of a future settings menu, not fixed
        # at construction: a real setter/getter, rejects a bad value, and
        # refuses to change mid-recording rather than silently no-op-ing.
        assert cam.video_resolution() == PREVIEW_RES, \
            "default video resolution should match Picamera2Camera's own default"
        cam.set_video_resolution((1920, 1080))
        assert cam.video_resolution() == (1920, 1080)
        try:
            cam.set_video_resolution((0, 480))
            raise AssertionError("expected ValueError for a non-positive width")
        except ValueError:
            pass
        cam.start_recording(vid_dir, "clip_0003")
        try:
            cam.set_video_resolution((640, 480))
            raise AssertionError("expected RuntimeError: cannot change resolution while recording")
        except RuntimeError:
            pass
        cam.stop_recording()
        print("video resolution check PASS: adjustable ahead of a future menu, "
              "bad-value and mid-recording guards both correct")

        # Capability query (PLAN_02_camera_capability_query.md): well-formed
        # with no hardware present, only plain types cross the boundary, and
        # the absent-vs-empty split for stream_formats/stream_resolutions
        # round-trips both ways.
        caps = cam.get_capabilities()
        _assert_plain_types(caps)
        for key in ("capture_resolutions", "capture_formats",
                    "video_resolutions", "video_formats"):
            assert key in caps and len(caps[key]) > 0, \
                "FakeCamera.get_capabilities() missing/empty required key {!r}".format(key)
        assert "stream_formats" not in caps and "stream_resolutions" not in caps, \
            "default FakeCamera should omit stream keys (absent, not empty), " \
            "matching Picamera2Camera's current no-stream-server behavior"
        print("get_capabilities (stream_caps=False) PASS:", caps)

        stream_cam = FakeCamera(stream_caps=True)
        caps2 = stream_cam.get_capabilities()
        _assert_plain_types(caps2)
        assert caps2.get("stream_formats") and caps2.get("stream_resolutions"), \
            "stream_caps=True should populate both stream keys"
        print("get_capabilities (stream_caps=True) PASS:", caps2)

        # Capability cache (Fix: Preferences dialog crash on
        # get_capabilities()): a fresh FakeCamera already has __init__'s own
        # eager call cached before any explicit get_capabilities() call is
        # even made -- caps above (from the untouched `cam` built earlier)
        # must be the SAME object __init__ primed, not a fresh dict built on
        # this call, proving the cached branch (not recomputation) is what
        # ran. Object identity (`is`), not just equal values -- the whole
        # point of caching is that get_capabilities() stops doing the
        # (real-hardware: possibly crash-triggering) work on every call, and
        # only identity actually proves that. This exercises the shared
        # caching CONTRACT both backend classes share; Picamera2Camera's own
        # copy of it needs a real Picamera2 to construct at all (its
        # __init__ also builds a GL preview widget), so confirming its
        # sensor_modes is genuinely read only once -- and that the original
        # "camera must be stopped before configuring" crash is actually gone
        # -- is on-rig-only verification, not something this self-check can
        # cover; see HANDOFF.md's note on this fix for the on-rig procedure.
        assert cam.get_capabilities() is caps, \
            "a second call must return the exact cached object __init__ " \
            "already primed, not recompute a fresh one"
        # Isolates the lazy compute-then-cache branch itself (not just
        # __init__'s own eager priming, already proven above): force a cold
        # cache, then confirm the FIRST real computation still caches, so a
        # second call afterward is identity-equal to it too.
        again = FakeCamera()
        again._capabilities = None
        first_call = again.get_capabilities()
        second_call = again.get_capabilities()
        assert first_call is second_call, \
            "the first real computation must itself be cached, not just " \
            "__init__'s own eager priming"
        print("get_capabilities cache check PASS: a second call returns the "
              "exact same cached object (identity, not just equal value), "
              "both for __init__'s own eager priming and a cold first "
              "real computation")

        # derive_lores_res (ROADMAP item 2, preview-resolution setting):
        # lores size must match preview_res's own aspect, not the old fixed
        # 4:3 LORES_RES, at roughly LORES_RES's own pixel count, rounded to
        # even dimensions.
        for w, h in [(2028, 1080), (1920, 1080), (4056, 3040), (640, 480),
                     PREVIEW_RES]:
            dw, dh = derive_lores_res((w, h))
            assert dw % 2 == 0 and dh % 2 == 0, \
                "derived lores size must be even: got {}x{} for preview {}x{}".format(
                    dw, dh, w, h)
            assert abs((dw / dh) - (w / h)) < 0.01, \
                "derived lores aspect must match preview_res's own aspect: " \
                "{}x{} -> {}x{}".format(w, h, dw, dh)
        print("derive_lores_res check PASS: including the actual PREVIEW_RES "
              "default and a wide non-4:3 case (2028x1080), every derived "
              "lores size is even-dimensioned and matches preview_res's own "
              "aspect rather than pinning 4:3")

        # lores_resolution(): both backends expose the CURRENT lores size,
        # not just the module constant -- needed since Picamera2Camera's own
        # value now varies per-instance (derived above).
        assert cam.lores_resolution() == LORES_RES, \
            "a default-constructed FakeCamera's lores_resolution() must " \
            "match its own (default) lores_res"
        custom_lores_cam = FakeCamera(lores_res=(320, 240))
        assert custom_lores_cam.lores_resolution() == (320, 240), \
            "lores_resolution() must reflect an explicit override, not the " \
            "module default"
        print("lores_resolution() check PASS: reflects the instance's own "
              "lores_res, default and overridden")

        assert_only_camera_backend_imports_picamera2()
        print("assert_only_camera_backend_imports_picamera2 PASS: no other "
              "module imports picamera2/libcamera directly (documented "
              "exceptions aside)")

        # _stash_lores's RuntimeError guard, driven through the real bound
        # method (not the pure _lores_error_is_expected helper reimplemented
        # standalone) with a minimal stand-in self/request, since
        # Picamera2Camera itself can't be constructed off-rig at all. Per
        # this project's own rule (PHILOSOPHY.md), a self-check that only
        # exercised the pure classifier in isolation would leave the actual
        # wiring -- does _stash_lores call it correctly, does it touch
        # lores_decode_errors/last_lores_error only on the right branch --
        # completely unverified, the same blind spot three earlier bugs in
        # this project all shared.
        import types

        class _StubRequest:
            def __init__(self, outcome, flip_suspend_on=None):
                self._outcome = outcome
                self._flip_suspend_on = flip_suspend_on

            def get_metadata(self):
                return {}

            def make_array(self, name):
                assert name == "lores"
                if self._flip_suspend_on is not None:
                    self._flip_suspend_on._suspend_lores = True
                if isinstance(self._outcome, Exception):
                    raise self._outcome
                return self._outcome

        class _StubPicam2:
            """Stands in for self._picam2 in _stash_lores's diagnostic-only
            camera_configuration() call. Counts calls so the "captured once,
            not every failing frame" claim is actually verified, not just
            asserted in a comment."""
            def __init__(self, outcome):
                self._outcome = outcome
                self.call_count = 0

            def camera_configuration(self):
                self.call_count += 1
                if isinstance(self._outcome, Exception):
                    raise self._outcome
                return self._outcome

        def _fresh_fake_self(picam2=None):
            return types.SimpleNamespace(
                _latest_meta=None, _suspend_lores=False, _want_frame=True,
                _lores_lock=threading.Lock(), _latest_lores=None,
                lores_frames_received=0, lores_decode_errors=0,
                last_lores_error=None, lores_config_at_failure=None,
                _picam2=picam2)

        fs = _fresh_fake_self()
        real_frame = np.zeros((4, 4, 3), dtype=np.uint8)
        Picamera2Camera._stash_lores(fs, _StubRequest(real_frame))
        assert fs.lores_frames_received == 1 and fs._latest_lores is real_frame
        assert fs._want_frame is False
        assert fs.lores_decode_errors == 0 and fs.last_lores_error is None
        assert fs.lores_config_at_failure is None

        fs = _fresh_fake_self(picam2=_StubPicam2({"main": {"size": (1920, 1080)}}))
        Picamera2Camera._stash_lores(
            fs, _StubRequest(RuntimeError("Stream 'lores' is not defined"), flip_suspend_on=fs))
        assert fs.lores_frames_received == 0
        assert fs.lores_decode_errors == 0 and fs.last_lores_error is None, (
            "a still-mode race (_suspend_lores true again by the time "
            "make_array raises) must stay silent, exactly as before this fix")
        assert fs.lores_config_at_failure is None and fs._picam2.call_count == 0, (
            "the expected still-mode race must not pay for a config dump "
            "it has no diagnostic use for")

        fake_cfg = {"main": {"size": (1920, 1080), "format": "XBGR8888"},
                   "raw": {"size": (4056, 3040), "format": "SBGGR12"}}
        stub_picam2 = _StubPicam2(fake_cfg)
        fs = _fresh_fake_self(picam2=stub_picam2)
        Picamera2Camera._stash_lores(fs, _StubRequest(RuntimeError("bad main/lores pairing")))
        assert fs.lores_frames_received == 0
        assert fs.lores_decode_errors == 1 and fs.last_lores_error == "bad main/lores pairing", (
            "a real decode failure (never suspended) must be recorded, not "
            "swallowed identically to the expected still-mode race")
        assert fs.lores_config_at_failure == {
            "streams_present": ["main", "raw"],
            "main": {"size": (1920, 1080), "format": "XBGR8888"},
            "raw": {"size": (4056, 3040), "format": "SBGGR12"},
        }, "a genuine failure must capture the ACTIVE config (no 'lores' " \
           "key here -- exactly candidate 1's claim) via camera_configuration(), " \
           "not the unreliable request.config"
        assert stub_picam2.call_count == 1

        # Second genuine failure on the same instance: must NOT call
        # camera_configuration() again -- the config can't change again
        # without a fresh switch_mode/configure(), which this callback
        # never triggers, so a second capture would be pure overhead on a
        # hot per-frame thread for no new information.
        Picamera2Camera._stash_lores(fs, _StubRequest(RuntimeError("bad main/lores pairing")))
        assert fs.lores_decode_errors == 2 and stub_picam2.call_count == 1, (
            "lores_config_at_failure is captured once per process, not "
            "re-dumped on every one of what could be hundreds of failures")

        # camera_configuration() itself raising must not crash this thread --
        # the diagnostic call is wrapped separately from make_array.
        fs = _fresh_fake_self(picam2=_StubPicam2(RuntimeError("camera busy")))
        Picamera2Camera._stash_lores(fs, _StubRequest(RuntimeError("bad main/lores pairing")))
        assert fs.lores_config_at_failure == {"error": "camera busy"}

        assert _summarize_camera_configuration({"lores": None}) == {"streams_present": []}, \
            "a None stream entry (present as a key but not configured) must " \
            "not be reported as present"
        assert _lores_error_is_expected(True) is True
        assert _lores_error_is_expected(False) is False
        print("_stash_lores RuntimeError classification PASS: a successful "
              "decode still increments lores_frames_received as before; a "
              "still-mode race (_suspend_lores flips true during the failing "
              "make_array call) stays silent with no recorded error and no "
              "config dump paid for; a genuine decode failure (never "
              "suspended) now increments lores_decode_errors, records the "
              "real exception text, and captures camera_configuration() "
              "exactly once via _summarize_camera_configuration -- a second "
              "failure does not re-dump it, and a camera_configuration() "
              "failure of its own is caught rather than crashing the thread")

        assert_only_camera_backend_imports_sensor_profiles()
        print("assert_only_camera_backend_imports_sensor_profiles PASS: no "
              "other module imports a sensor-profile module (imx477.py "
              "discovered by shape, not a maintained list) directly -- the "
              "checkable half of PHILOSOPHY.md's revised sensor-profile rule")

        assert_no_hardcoded_sensor_dimension_above_driver_layer()
        print("assert_no_hardcoded_sensor_dimension_above_driver_layer PASS: "
              "no non-driver .py file's own production region contains a "
              "literal matching a profile-derived sensor dimension (or its "
              "half), in either axis order")

        assert_no_hardcoded_bit_depth_or_white_level_above_driver_layer()
        print("assert_no_hardcoded_bit_depth_or_white_level_above_driver_layer "
              "PASS: no non-driver .py file's own production region contains "
              "a literal matching the profile's own BIT_DEPTH or its derived "
              "white level")

        # Sensor crop geometry (PRIORITY_click_mapping_fix.md): FakeCamera's
        # own contract, general across arbitrary preview/full resolutions,
        # not hardcoded to PREVIEW_RES/FULL_RES.
        assert cam.preview_resolution() == PREVIEW_RES
        assert cam.capture_resolution() == FULL_RES
        assert cam.sensor_crop_for_size(PREVIEW_RES) == _imx477.crop_for_size(PREVIEW_RES)
        assert cam.sensor_crop_for_size(FULL_RES) == (0, 0, 4056, 3040), \
            "the full-resolution mode's own crop must be the whole array"
        custom_cam = FakeCamera(preview_res=(2028, 1080), full_res=(4056, 2160))
        assert custom_cam.preview_resolution() == (2028, 1080)
        assert custom_cam.capture_resolution() == (4056, 2160)
        assert custom_cam.sensor_crop_for_size((2028, 1080)) == (0, 440, 4056, 2160), \
            "sensor_crop_for_size must be general across arbitrary preview " \
            "resolutions, not hardcoded to the 1332x990 default"
        try:
            cam.sensor_crop_for_size((999, 999))
            raise AssertionError("expected ValueError for an unknown size")
        except ValueError:
            pass
        print("sensor crop geometry check PASS: preview_resolution/"
              "capture_resolution report the ACTUAL configured sizes (not "
              "just the module defaults), sensor_crop_for_size matches "
              "imx477's own table and is general across a non-default "
              "preview/full resolution pairing, and an unknown size raises "
              "rather than guessing")

        # Sensor-profile resolution by exact hardware-reported name
        # (PRIORITY_click_mapping_fix.md, per the user's own mid-brief
        # instruction): a direct import of the model string, no mapping
        # table, and a loud failure naming the real unrecognised sensor.
        resolved = _resolve_sensor_profile("imx477")
        assert resolved is _imx477, \
            "camera model 'imx477' must resolve to this project's own " \
            "imx477.py, by exact name"
        for bad_model in ("ov5647", "does_not_exist", "Imx477", "../imx477",
                          "imx477; rm -rf /", None, ""):
            try:
                _resolve_sensor_profile(bad_model)
                raise AssertionError(
                    "expected a clear failure for unrecognised/unsafe model "
                    "{!r}, not a silent resolution".format(bad_model))
            except RuntimeError:
                pass
        print("_resolve_sensor_profile check PASS: 'imx477' resolves to "
              "this project's own imx477.py by exact name; an unrecognised, "
              "wrongly-cased, or unsafe model string fails loudly rather "
              "than silently falling back to IMX477 geometry")

        shutil.rmtree(rc_root, ignore_errors=True)
        print("camera_backend self-check PASS")
