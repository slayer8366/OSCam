"""wizard_pages.py - the shared "get an image" wizard page for calibrate.py's
and measure.py's paged wizards (build checklist section 4): pick an image
already shot, or shoot a new one live, with a focus box/bar so a fresh capture
is not a guess. Next/Finish gates on the CALLER's own validate(path) callback
succeeding, so calibrate.py's green-plane+focus-score rules and measure.py's
provenance-guarded load stay exactly as different as those two tools' own
"Open image..." always were -- neither is duplicated here.

Two ways to run:
  python3 wizard_pages.py --render-check   headless: the ad hoc session-dir
                                           helper plus a full FakeCamera
                                           capture round-trip, no PyQt5, no
                                           real camera.
  python3 wizard_pages.py                  not a standalone tool; import
                                           ImageSourcePage from calibrate.py
                                           or measure.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

try:
    from .camera_backend import LORES_RES
    from .focus import FocusMeter, FocusBox
except ImportError:
    from camera_backend import LORES_RES
    from focus import FocusMeter, FocusBox

try:
    from picamera2 import Picamera2  # noqa: F401  (only to probe availability)
    _HAVE_PICAMERA2 = True
except ImportError:
    _HAVE_PICAMERA2 = False

ADHOC_OUT_ROOT = Path.home() / "captures" / "adhoc"


def _lazy_qt_shell():
    """qt_shell.py's session/profile helpers (formerly capture.py, now baked
    into qt_shell.py -- see qt_shell.py's own module docstring), imported
    lazily rather than at module load time: qt_shell.py itself imports
    calibrate.py, and calibrate.py imports this module for ImageSourcePage,
    so importing qt_shell.py here at load time would be a circular import
    partway through calibrate.py's own load. Deferred to first real use (well
    after every module involved has finished loading), the cycle never
    actually closes. Same reasoning, same pattern as _overlay_helpers()
    below -- kept as two functions rather than one shared one so each stays
    a one-line change if either's import set ever diverges."""
    try:
        from . import qt_shell as _qt_shell
    except ImportError:
        import qt_shell as _qt_shell
    return _qt_shell


def new_adhoc_dir(root=None):
    """A fresh timestamped folder for one ad hoc wizard capture, via
    qt_shell.py's own new_session_dir (reused, not reimplemented) so naming
    matches every other capture folder in the project. Raises RuntimeError if
    qt_shell.py is not importable -- its collision-avoiding timestamp logic is
    not worth re-deriving here."""
    try:
        qt_shell = _lazy_qt_shell()
    except ImportError:
        raise RuntimeError("qt_shell.py could not be imported; needed for new_session_dir")
    root = Path(root) if root is not None else ADHOC_OUT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    _ts, d = qt_shell.new_session_dir(root)
    return d


def next_snap_stem(counter):
    """Same <prefix>frame_<idx> convention every capture kind in the project
    uses, so a wizard-shot .dng/.jpg pair looks exactly like one qt_shell.py
    session capture could have written."""
    return "snap_frame_{:04d}".format(int(counter))


def _overlay_helpers():
    """qt_shell.py's own Qt-free render_overlay_into/state_color, via the
    same lazy import as new_adhoc_dir (see _lazy_qt_shell's docstring for
    why this cannot be a top-level import)."""
    qt_shell = _lazy_qt_shell()
    return qt_shell.render_overlay_into, qt_shell.state_color


try:
    from PyQt5.QtWidgets import (QWizardPage, QWidget, QLabel, QVBoxLayout,
                                 QHBoxLayout, QPushButton, QDialog, QMessageBox)
    from PyQt5.QtCore import QTimer, pyqtSignal
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


if _HAVE_QT:

    class _CapturePane(QWidget):
        """Embedded live-capture aid: preview + fixed centered focus box/bar +
        Capture button. Not modal -- lives directly on the wizard page.
        Camera lifecycle (start/stop) is owned by whoever embeds this
        (ImageSourcePage.initializePage/cleanupPage), so a Back or a cancelled
        wizard can never leave a camera running behind it.

        Fixed-centered box (FocusBox.centered()), no drag/resize: this is an
        aiming aid to get a usable frame, not a measurement, and both host
        tools already have their own pan/zoom for precise work once an image
        is loaded.
        """

        capture_done_signal = pyqtSignal(object)

        def __init__(self, on_captured, parent=None):
            super().__init__(parent)
            self._on_captured = on_captured
            self.camera = None
            self.meter = FocusMeter(box=FocusBox.centered())
            self._capturing = False
            self._counter = 0
            self._session_dir = None
            self._render_overlay_into = None
            self._state_color = None
            self._preview_widget = None

            self._tick = QTimer(self)
            self._tick.timeout.connect(self._on_tick)
            self.capture_done_signal.connect(self._on_capture_done)

            self.preview_holder = QVBoxLayout()
            self.status_label = QLabel(
                "camera unavailable: picamera2 not importable on this machine"
                if not _HAVE_PICAMERA2 else "")
            self.status_label.setWordWrap(True)
            self.capture_btn = QPushButton("Capture")
            self.capture_btn.setEnabled(False)
            self.capture_btn.clicked.connect(self._on_capture_clicked)

            lay = QVBoxLayout(self)
            lay.addLayout(self.preview_holder)
            lay.addWidget(self.status_label)
            lay.addWidget(self.capture_btn)

        def start(self):
            if self.camera is not None or not _HAVE_PICAMERA2:
                return
            try:
                self._render_overlay_into, self._state_color = _overlay_helpers()
            except ImportError:
                self._render_overlay_into, self._state_color = None, None
            try:
                from .camera_backend import Picamera2Camera
            except ImportError:
                from camera_backend import Picamera2Camera
            self.camera = Picamera2Camera()
            self.camera.start()
            self._preview_widget = self.camera.widget
            self.preview_holder.addWidget(self._preview_widget)
            self.meter.reset_field()
            self.capture_btn.setEnabled(True)
            self.status_label.setText(
                "live preview: aim, wait for the bar to peak, then Capture")
            self._tick.start(100)

        def stop(self):
            self._tick.stop()
            if self._preview_widget is not None:
                # QGlPicamera2 only stops listening on its camera notifier
                # once ITS OWN cleanup() has run, which its library code ties
                # to Qt's `destroyed` signal -- i.e. actual C++ object
                # destruction, not when this class merely drops its last
                # Python reference. That is not guaranteed synchronous: on
                # real hardware, a QSocketNotifier callback already queued
                # for this widget fired AFTER camera.stop() below had closed
                # the underlying fd, raising deep inside picamera2's own
                # background thread and aborting the whole process. Calling
                # cleanup() here ourselves, before detaching/closing anything
                # else, sets its `running` flag False immediately, so any
                # already-queued callback hits that guard instead of reading
                # a closed file. cleanup() is idempotent (guards on the same
                # flag), so this is safe even though Qt will still try to
                # call it again once the widget is actually destroyed.
                if hasattr(self._preview_widget, "cleanup"):
                    self._preview_widget.cleanup()
                self.preview_holder.removeWidget(self._preview_widget)
                self._preview_widget.setParent(None)
                self._preview_widget = None
            if self.camera is not None:
                self.camera.stop()
                self.camera = None
            self.capture_btn.setEnabled(False)

        def _on_tick(self):
            if self.camera is None:
                return
            frame = self.camera.focus_frame()
            state = self.meter.update(frame)
            if self._render_overlay_into is not None:
                h, w = frame.data.shape
                ov = np.zeros((h, w, 4), dtype=np.uint8)
                self._render_overlay_into(ov, self.meter.box, state)
                self.camera.set_overlay(ov)
            peak = "  (peak)" if (state.bar is not None and state.bar.at_peak
                                  and state.bar.settled) else ""
            self.status_label.setText(
                "focus score: {:.4f}{}".format(state.smoothed, peak))

        def _on_capture_clicked(self):
            if self.camera is None or self._capturing:
                return
            self._capturing = True
            self.capture_btn.setEnabled(False)
            if self._session_dir is None:
                self._session_dir = new_adhoc_dir()
            stem = next_snap_stem(self._counter)
            self._counter += 1
            try:
                self.camera.capture_still_async(
                    self._session_dir, stem, self.capture_done_signal.emit)
            except Exception as exc:
                self._capturing = False
                self.capture_btn.setEnabled(True)
                QMessageBox.warning(self, "Capture failed", str(exc))

        def _on_capture_done(self, result):
            self._capturing = False
            self.capture_btn.setEnabled(self.camera is not None)
            if isinstance(result, Exception):
                QMessageBox.warning(self, "Capture failed", str(result))
                return
            self._on_captured(result.raw)


    class ImageSourcePage(QWizardPage):
        """Page: pick an image already shot, or shoot a new one live. `validate`
        is the owning wizard's own loader (e.g. calibrate.py's
        resolve_raw_path+load_green_plane, or measure.py's
        load_measurement_plane) -- called on whatever path is chosen or
        captured, exactly as that tool's flat-screen "Open image..." already
        calls it. Next/Finish is gated on the last validate() call having
        succeeded.
        """

        def __init__(self, validate, parent=None):
            super().__init__(parent)
            self.setTitle("Image")
            self.setSubTitle("Use an image already shot, or shoot a new one.")
            self._validate = validate
            self.resolved_path = None
            self._complete = False

            open_btn = QPushButton("Use existing image...")
            open_btn.clicked.connect(self._on_open_existing)

            self.capture_pane = _CapturePane(self._on_captured)

            self.status_label = QLabel("No image chosen yet.")
            self.status_label.setWordWrap(True)

            lay = QVBoxLayout(self)
            lay.addWidget(open_btn)
            lay.addWidget(QLabel("-- or --"))
            lay.addWidget(self.capture_pane, 1)
            lay.addWidget(self.status_label)

        def initializePage(self):
            self.capture_pane.start()

        def cleanupPage(self):
            self.capture_pane.stop()

        def isComplete(self):
            return self._complete

        def _on_open_existing(self):
            try:
                from . import gallery as _gallery
            except ImportError:
                import gallery as _gallery
            dlg = _gallery.GalleryPickDialog(parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            paths = dlg.selected_paths()
            if paths:
                self._try_validate(paths[0])

        def _on_captured(self, path):
            self._try_validate(Path(path))

        def _try_validate(self, path):
            ok, message = self._validate(path)
            self.resolved_path = path if ok else None
            self.status_label.setText(message)
            self._set_complete(ok)

        def _set_complete(self, ok):
            if ok != self._complete:
                self._complete = ok
                self.completeChanged.emit()


def render_check():
    import tempfile
    import threading

    try:
        from .camera_backend import FakeCamera
    except ImportError:
        from camera_backend import FakeCamera

    # --- new_adhoc_dir / next_snap_stem: pure logic --------------------------
    tmp_root = Path(tempfile.mkdtemp()) / "adhoc_check"
    d1 = new_adhoc_dir(tmp_root)
    d2 = new_adhoc_dir(tmp_root)
    assert d1.is_dir() and d2.is_dir() and d1 != d2, \
        "new_adhoc_dir should mint a fresh, existing directory each call"
    assert next_snap_stem(0) == "snap_frame_0000"
    assert next_snap_stem(12) == "snap_frame_0012"
    print("new_adhoc_dir / next_snap_stem check PASS: fresh dirs each call, "
          "matching the project's snap_frame_<idx> stem convention")

    # --- capture_still_async contract the capture pane relies on -------------
    cam = FakeCamera(async_delay_s=0.0)
    cam.start()
    done = threading.Event()
    delivered = {}

    def _on_done(result):
        delivered["result"] = result
        done.set()

    cam.capture_still_async(d1, next_snap_stem(0), _on_done)
    assert done.wait(timeout=2.0), "capture_still_async never called back"
    result = delivered["result"]
    assert not isinstance(result, Exception), \
        "unexpected capture failure: {}".format(result)
    assert result.raw.exists(), "capture_still_async's file does not exist"
    cam.stop()
    print("capture_still_async round-trip check PASS: FakeCamera delivers a "
          "real file, the contract _CapturePane's capture button relies on")


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("wizard_pages.py is not a standalone tool; import "
                 "ImageSourcePage from calibrate.py or measure.py, or run "
                 "with --render-check for the headless self-check.")
