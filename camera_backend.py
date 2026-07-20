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
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

# Rig defaults (match capture.py). Tunable.
FULL_RES = (4056, 3040)
PREVIEW_RES = (1332, 990)
LORES_RES = (640, 480)     # small enough for real-time scoring, 4:3 like the sensor


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
                 fail_capture: bool = False):
        self._w, self._h = lores_res
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
        frame = self._rng.integers(0, 4096, size=(64, 64)).astype(np.uint16)
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


class Picamera2Camera(CameraBackend):
    """Picamera2 implementation of the seam for the Pi HQ camera on a Pi 5.

    The GUI creates its QApplication first, then this backend, then embeds
    `self.widget` (the QGlPicamera2 preview) in the layout. Do NOT call
    start_preview: the embedded widget is the preview and Qt's exec() drives it.
    """

    def __init__(self, preview_res=PREVIEW_RES, lores_res=LORES_RES,
                 full_res=FULL_RES):
        if not _HAVE_PICAMERA2:
            raise RuntimeError("picamera2 not available; this backend runs on the Pi. "
                               "Use FakeCamera off-rig.")
        # ON-RIG: confirm this import path on your Picamera2 version.
        from picamera2.previews.qt import QGlPicamera2

        self._picam2 = Picamera2()
        self._preview_res = preview_res
        self._lores_res = lores_res
        self._full_res = full_res

        # ON-RIG: RGB lores is Pi 5 + recent libcamera. If unavailable, set the
        # format to "YUV420" and source to "luma" (the score then runs on luma).
        # Some stacks only surface an unsupported format at configure/start, so
        # treat this whole block as a shakeout point.
        self._source = "green"
        self._preview_cfg = self._picam2.create_preview_configuration(
            main={"size": preview_res},
            lores={"size": lores_res, "format": "RGB888"},
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
        # still config carries the raw plane, as capture.py does, so capture_still
        # can save a DNG.
        self._still_cfg = self._picam2.create_still_configuration(
            main={"size": full_res}, raw={"size": full_res}, buffer_count=2)

        # --- RECORD BUTTON (separable): video's own adjustable resolution,
        # NOT a config built once and fixed forever. A future menu is
        # expected to let this change at runtime (set_video_resolution
        # below), so the video config itself is built fresh inside
        # start_recording() from whatever self._video_res currently is,
        # rather than baked into a _video_cfg here that could only ever
        # match the resolution the camera happened to start up with.
        # lores stays fixed (LORES_RES): it does double duty as both the
        # widget's display source during recording and the focus aid's own
        # input, and the future resolution menu is about the RECORDED
        # file's size, not that.
        self._video_res = preview_res

        self._picam2.configure(self._preview_cfg)

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
        # stream is not reaching this callback at all -- either post_callback
        # never fires on this Picamera2 version, or make_array("lores") is
        # failing every time. qt_shell.py's tick surfaces this directly
        # instead of showing a numeric reading that looks valid but is not.
        self.lores_frames_received = 0

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
        except RuntimeError:
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


if __name__ == "__main__":
    # Self-check with no hardware: sweep the fake through focus, exercise the
    # exposure surface, the async capture path, and the two burst primitives.
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
    with cam:
        print("focus_position -> laplacian variance (fake, peaks at best_focus=0):")
        for z in [-3, -2, -1, 0, 1, 2, 3]:
            cam.focus_position = float(z)
            fr = cam.focus_frame()
            print(f"  z={z:+d}  source={fr.source:5s}  score={_lap_var(fr.data):10.4f}")
        cam.set_overlay(np.zeros((10, 10, 4), dtype=np.uint8))
        print("overlay set:", cam.last_overlay is not None)

        p = cam.capture_still(Path("/tmp/zynergy_fake"), "selfcheck")
        print("still written:", p)

        # Non-blocking capture (fake): fire it, wait for the callback (which lands
        # off the calling thread, per the seam contract), and confirm the delivered
        # result carries a CaptureResult with a real file.
        done = threading.Event()
        delivered = {}

        def _on_capture(result):
            delivered["result"] = result
            done.set()

        cam.capture_still_async(Path("/tmp/zynergy_fake"), "selfcheck_async", _on_capture)
        fired = done.wait(timeout=2.0)
        assert fired and isinstance(delivered["result"], CaptureResult), \
            "async capture did not deliver a CaptureResult"
        assert delivered["result"].raw.exists(), "async capture's file does not exist"
        print("async capture fired ->", delivered["result"].raw)

        # Capture-enforces-lock, at the CameraBackend seam: a metered snapshot goes
        # into apply_exposure_lock, and auto drops on both channels with the exact
        # metered values held. The Qt half (the sliders/checkboxes
        # _enforce_exposure_lock also updates) needs PyQt5 to run and is not
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
        burst_dir = Path("/tmp/zynergy_fake_burst")
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
        vid_dir = Path("/tmp/zynergy_fake_video")
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

        print("camera_backend self-check PASS")
