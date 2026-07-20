#!/usr/bin/env python3
"""
test_burst_backend.py - on-rig smoke test for the two burst primitives added
to camera_backend.py: capture_burst (flat/science/dark-style single-exposure
burst) and enter_still_mode / capture_bracket_phase / exit_still_mode (HDR's
two-phase pattern). Exercises both together in one run, since the GUI wiring
(submenu, walkthrough dialogs, worker thread) is not built yet and there is no
reason to need two separate on-rig sessions to check the backend seam itself.

This is NOT part of the GUI and does not replace it. It talks to
Picamera2Camera directly so the seam can be confirmed against real hardware
before anything is built on top of it.

WHY THIS RUNS ON A WORKER THREAD: Picamera2Camera's preview is a QGlPicamera2
widget, and its own docstring says "Qt's exec() drives it" -- capture_still_
async already depends on that (completion is routed through the widget's Qt
signal), and probe()'s blocking capture_metadata() calls hang the same way
without a running event loop: nothing pumps a frame through, so the call waits
forever. An earlier version of this script created a QApplication but never
ran it, which hung exactly that way on real hardware even though rpicam-hello
worked fine on its own. The fix: the checks below run on a background thread,
the main thread runs app.exec_() to keep frames flowing, and the worker thread
signals back through a Qt signal (the same done_signal pattern
capture_still_async already uses) when it's finished.

Run on the Pi, from the same folder as camera_backend.py:
    python3 test_burst_backend.py

A small preview window will pop up during the run; that's expected, the same
widget the real GUI embeds. Writes into ~/captures/_burst_backend_test/,
deleting and recreating that folder each run, so it is safe to run repeatedly.
Prints OK/FAIL per check and a final summary line; exits 0 if everything
passed, 1 otherwise.
"""
import shutil
import sys
import time
import threading
from pathlib import Path

try:
    from camera_backend import Picamera2Camera
except ImportError:
    sys.exit("This test needs camera_backend.py in the same folder, on the Pi "
             "(with Picamera2 available). Use the GUI's --render-check for the "
             "off-rig / no-hardware checks instead.")

try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtCore import QObject, pyqtSignal
except ImportError:
    sys.exit("PyQt5 not available; Picamera2Camera's preview widget needs a "
             "running Qt application to deliver frames at all.")

TEST_DIR = Path.home() / "captures" / "_burst_backend_test"

_results = []
_exit_code = [1]


def check(label, cond):
    _results.append(bool(cond))
    print(("  OK   " if cond else "  FAIL ") + label)
    return bool(cond)


def note(label):
    # Informational only, not a pass/fail: some checks (like "is the preview
    # showing something other than a blank frame") depend on scene content and
    # timing in a way that would make a hard assertion noisy rather than useful.
    print("  --   " + label)


class _Done(QObject):
    # Marshals the worker thread's completion back onto the Qt thread so
    # app.quit() is called safely, the same cross-thread signal pattern
    # capture_still_async already uses via the widget's done_signal.
    finished = pyqtSignal()


def run_checks(cam):
    """Everything that actually exercises the camera. Runs on the worker
    thread; app.exec_() on the main thread is what lets its frames arrive."""
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)

    with cam:
        print("[probe] metering to get a real, known exposure to burst at ...")
        locked = cam.probe()
        cam.apply_exposure_lock(locked)
        print("  locked: shutter={}us gain={}".format(
            locked["shutter_us"], locked["analogue_gain"]))

        # --- capture_burst: the flat / science / dark-style single-exposure
        # burst. One still-mode session for all 3 frames, not one per frame. ---
        print("\n[capture_burst] 3 frames, prefix 'testburst_', at the locked exposure")
        result = cam.capture_burst(TEST_DIR, "testburst_", 3)
        frames = result["frames"]
        check("3 frames returned", len(frames) == 3)
        check("every frame's .dng exists on disk", all(f.raw.exists() for f in frames))
        check("every frame's .jpg preview exists",
              all(f.preview and f.preview.exists() for f in frames))
        check("file naming is testburst_frame_0000 .. 0002",
              sorted(f.raw.stem for f in frames) ==
              ["testburst_frame_0000", "testburst_frame_0001", "testburst_frame_0002"])
        check("actual_us is close to the locked shutter (within 10%)",
              abs(result["actual_us"] - locked["shutter_us"]) <= 0.10 * locked["shutter_us"])
        print("  actual_us={}  (locked was {})".format(result["actual_us"], locked["shutter_us"]))

        # --- capture_burst at an explicit override (a flat/dark shot at a
        # level OTHER than the locked value) ---
        override_us = max(100, locked["shutter_us"] // 2)
        print("\n[capture_burst] 1 frame at an explicit override ({}us, half the "
              "locked value)".format(override_us))
        result2 = cam.capture_burst(TEST_DIR, "testoverride_", 1, shutter_us=override_us)
        check("override actual_us is close to the requested override (within 10%)",
              abs(result2["actual_us"] - override_us) <= 0.10 * override_us)

        # --- HDR bracket phase: enter_still_mode ONCE, several levels, exit
        # ONCE, mirroring how an actual HDR sequence runs two phases (science,
        # then dark) under one still-mode session. ---
        print("\n[capture_bracket_phase] 3 levels (-1, 0, +1 EV), 1 frame each, "
              "one still-mode session")
        cam.enter_still_mode()
        try:
            levels = cam.capture_bracket_phase(
                TEST_DIR, "testbracket_", 1, base_us=locked["shutter_us"],
                stops=[-1.0, 0.0, 1.0])
        finally:
            cam.exit_still_mode(locked["shutter_us"])
        check("3 levels returned", len(levels) == 3)
        check("levels are 1-based in stops order",
              [lv["level"] for lv in levels] == [1, 2, 3])
        check("the 0 EV level's requested_us equals the locked shutter",
              levels[1]["requested_us"] == locked["shutter_us"])
        check("levels increase monotonically with the stops order",
              levels[0]["requested_us"] < levels[1]["requested_us"] < levels[2]["requested_us"])
        for lv in levels:
            check("level {} (ev {:+g}): file(s) exist, actual_us={}".format(
                      lv["level"], lv["ev"], lv["actual_us"]),
                  all(f.raw.exists() for f in lv["frames"]))

        # Preview resume after exit_still_mode: give one frame period to land,
        # then just report what came back. Not a hard pass/fail -- a flat or
        # dark scene can legitimately look near-uniform, so "all zero" is not
        # on its own proof anything is wrong. Look at it, don't just trust it.
        time.sleep(0.5)
        lores = cam.focus_frame()
        note("lores frame after resume: shape={}, source={!r}, mean={:.4f} "
             "(look at the actual preview window to confirm it's live, this "
             "line is informational only)".format(
                 lores.data.shape, lores.source, float(lores.data.mean())))

    passed = sum(_results)
    total = len(_results)
    print("\n{}/{} checks passed.".format(passed, total))
    print("Test files are in", TEST_DIR, "(safe to delete).")
    _exit_code[0] = 0 if passed == total else 1


def main():
    app = QApplication(sys.argv)
    cam = Picamera2Camera()
    cam.widget.show()          # matches how the real GUI uses this backend;
                                # an unshown widget is the headless assumption
                                # that caused the original hang
    done = _Done()
    done.finished.connect(app.quit)   # connected on the main/Qt thread

    def _worker():
        try:
            run_checks(cam)
        except Exception as exc:
            print("\nERROR during checks:", exc)
            _exit_code[0] = 1
        finally:
            done.finished.emit()      # safe to emit from this thread; Qt
                                        # marshals it to app.quit on the main one

    threading.Thread(target=_worker, daemon=True).start()
    app.exec_()                # keeps the event loop alive so frames actually
                                # arrive; returns once the worker's signal fires
    return _exit_code[0]


if __name__ == "__main__":
    sys.exit(main())

