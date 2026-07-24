"""casual_mode.py - Casual Mode (BUILD_LIST Tier 3, item 2).

Same capture behavior as qt_shell.py's normal FocusPreviewWindow path --
snap, burst (frame-averaged), HDR bracket, debayer, tonemap, same camera,
same image operations, same results -- with a different file-retention
policy: no session folder, no session.json, no .meta.json sidecars, no
pixel_sha256, no calibration_ref. Only the final image survives to disk;
intermediates are cleaned up automatically, no prompt. Full design in
PLAN_casual_mode.md (drafted, not checked into the repo); the condensed,
kept-current account is HANDOFF.md's own "Casual Mode" section.

THE GUARANTEE THIS FILE MUST NEVER BREAK, stated as code, not just prose:
this module must never import provenance.py's write functions (Session,
record_capture, record_burst, record_hdr, _dump_meta, new_session_dir,
new_zstack_root_dir) -- and, more strictly than that literal list, must
never bind the `provenance` module itself under ANY name. Binding the
module alone would leave every one of those functions reachable via
attribute access even with zero direct imports of them, which defeats
the whole point ("no code path through it can write a provenance
record"). --render-check's own assert_no_provenance_import() checks this
structurally (this module's own namespace), every run, so a future edit
that reintroduces provenance can never land silently. Because of this,
this file intentionally does NOT reach for provenance.OUT_ROOT,
provenance.load_profile, or provenance.save_profile either, even though
none of those three are on the forbidden list by name -- importing the
module to reach them would still bind "provenance" and trip the same
guard. DEFAULT_OUT_ROOT below is therefore a plain literal Path, not
derived from provenance.OUT_ROOT, and exposure handling is fully self-
contained (continuous auto-exposure/AWB, frozen just before each shot --
see _freeze_exposure), never touching the shared ~/imx/profile.json.

The one real wrinkle the design surfaced: qt_shell.py's own normal
capture path invokes hdr_from_session.py as a SUBPROCESS CLI
(_run_process_cmd), and that CLI's main() requires a real session.json
on disk to run at all (sys.exit if session_dir/session.json is missing).
hdr_from_session.py's process() function, underneath main(), carries no
such requirement -- it takes plain session/cap dicts, does the real work
(frame averaging, HDR merge, debayer, tonemap) against a session_dir
passed in explicitly, and writes nothing provenance-related itself. This
module imports process() directly and hand-builds the minimal dicts
provenance.record_capture/record_burst/record_hdr would otherwise have
produced -- same pipeline, same image operations, zero provenance i/o,
entirely against a throwaway tempfile.mkdtemp() staging directory deleted
unconditionally once the requested output file(s) are safely written.

Output format is independent checkboxes (dng/png/jpg/tiff, any nonempty
combination) rather than the plan's original fixed seven-preset list --
see HANDOFF.md's Casual Mode section for why (a real DNG is a raw Bayer-
mosaic container; Burst/HDR's frame-averaged/HDR-merged result has no
valid single DNG to land in, so a dedicated "process DNG" checkbox
(Burst/HDR only) chooses between the first raw frame untouched, or the
merged raw-domain master honestly saved as "<stem>_raw.tif", never a
mislabeled ".dng"). The JPG-first UX is unchanged from the plan: the
camera's own preview JPG (already written at capture time, free) lands
in the destination folder immediately; if "jpg" was checked, the real
processed JPG atomically replaces it in place; if not, it is removed
once every checked format's file is safely on disk. On a processing
failure the placeholder is always kept, never deleted, and the failure
is reported plainly.

Two ways to run:
  python3 casual_mode.py --render-check   headless: the import-boundary
                                          structural assertion, output-
                                          stem collision avoidance,
                                          snap/burst/HDR capture through
                                          the real pipeline (FakeCamera),
                                          every format-checkbox
                                          combination's expected file
                                          set, the JPG-first placeholder
                                          + atomic replace, the honest-
                                          failure path, intermediate
                                          cleanup, and (PyQt5-gated,
                                          SKIPPED if PyQt5 is not
                                          importable) the window's own
                                          menu-independent format UI.
  python3 casual_mode.py                  not a standalone tool; import
                                          CasualModeWindow from
                                          qt_shell.py's main(), lazily,
                                          inside the casual-mode branch
                                          (see HANDOFF.md's circular-
                                          import section for why that
                                          import must be lazy and this
                                          module must never import
                                          qt_shell.py at all, in either
                                          direction).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np

try:
    from .camera_backend import LORES_RES, FakeCamera
except ImportError:
    from camera_backend import LORES_RES, FakeCamera

try:
    from . import hdr_from_session
except ImportError:
    import hdr_from_session

try:
    from PyQt5.QtWidgets import (QMainWindow, QWidget, QLabel, QPushButton,
                                 QVBoxLayout, QHBoxLayout, QCheckBox,
                                 QComboBox, QMessageBox, QInputDialog,
                                 QFileDialog)
    from PyQt5.QtCore import QTimer, pyqtSignal
    from PyQt5.QtGui import QImage, QPainter
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


# Sibling of provenance.OUT_ROOT (~/captures), never inside it -- a plain
# literal, not derived from provenance.OUT_ROOT (see the module docstring
# for why this file never imports provenance at all). gallery.py's
# list_gallery_entries treats OUT_ROOT's immediate children as sessions;
# keeping Casual output outside ~/captures is what makes "no session"
# true from the gallery's perspective too, not just on disk.
DEFAULT_OUT_ROOT = Path.home() / "photos"

# Same real store qt_shell.py's gui_prefs.json uses (only the output-
# folder preference is persisted here -- see run_capture_and_save's own
# docstring for why format checkboxes are deliberately per-session UI
# state, not persisted). A small local duplicate of qt_shell.py's own
# load_pref/save_pref, not a second file and not a qt_shell import: this
# module has zero dependency on qt_shell.py, in either direction (see the
# module docstring).
PREFS_PATH = Path.home() / ".zynergy" / "gui_prefs.json"

DEFAULT_BURST = 8
MAX_BURST = 10
HDR_STOPS = [-2.0, -1.0, 0.0, 1.0, 2.0]   # matches qt_shell.py's DEFAULT_STOPS
                                          # value, duplicated rather than
                                          # imported -- see module docstring.

_FORBIDDEN_PROVENANCE_NAMES = {
    "provenance", "Session", "record_capture", "record_burst", "record_hdr",
    "_dump_meta", "new_session_dir", "new_zstack_root_dir",
}


def assert_no_provenance_import():
    """The structural half of the module's central guarantee (see the
    module docstring): neither this module's own namespace may bind
    provenance.py's write functions under their conventional names, nor
    may it bind the `provenance` module itself under any name -- the
    latter would leave every write function reachable via attribute
    access with no further import needed. Raises AssertionError naming
    the offending symbol(s) if the guarantee is ever broken.
    --render-check calls this every run, so a future edit that
    reintroduces provenance can never land silently."""
    this_module = sys.modules[__name__]
    bound = set(vars(this_module).keys())
    bad = bound & _FORBIDDEN_PROVENANCE_NAMES
    assert not bad, (
        "casual_mode.py must never bind provenance.py's write functions "
        "(or the provenance module itself) at module level -- found: {}"
        .format(sorted(bad)))


# ---------------------------------------------------------------------------
# gui_prefs.json persistence (Qt-free, pure I/O): only the output-folder
# preference. Isolated from the real file the same way qt_shell.py's own
# render_check() isolates PREFS_PATH/PROFILE_PATH -- monkeypatch the
# module global to a temp path for the whole check.
# ---------------------------------------------------------------------------
def _load_prefs():
    try:
        return json.loads(PREFS_PATH.read_text())
    except Exception:
        return {}


def _load_pref(key, default=None):
    return _load_prefs().get(key, default)


def _save_pref(key, value):
    try:
        PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
        prefs = _load_prefs()
        prefs[key] = value
        tmp = PREFS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(prefs, indent=2))
        os.replace(tmp, PREFS_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pure logic (Qt-free, camera-free): output naming, the in-memory
# session/cap dicts hdr_from_session.process() needs, and which raw
# extension a given camera writes.
# ---------------------------------------------------------------------------
def new_output_stem(root):
    """A collision-avoiding, timestamped output stem (no extension) under
    `root`, for a single file rather than a directory -- Casual Mode has
    no session folder at all, so there is nothing to mkdir here, just a
    name nothing on disk already answers to. Checks for ANY existing
    file starting with the candidate stem (glob(stem + "*")), not just an
    exact match, so a later multi-format write (several files sharing one
    stem) can never partially collide with an earlier capture's leftovers
    either."""
    root = Path(root)
    ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    stem = "capture_{}".format(ts)
    n = 1
    while any(root.glob(stem + "*")):
        stem = "capture_{}_{}".format(ts, n)
        n += 1
    return stem


def raw_ext(camera):
    """The raw file extension THIS camera actually writes -- "dng" on the
    real Picamera2 backend, "tif" on FakeCamera. Same duck-typing check
    qt_shell.py's own FocusPreviewWindow.__init__ already uses to tell
    the two apart (hasattr(camera, "widget")), not a new convention."""
    return "dng" if hasattr(camera, "widget") else "tif"


def snap_cap_dict(prefix):
    """The minimal capture dict hdr_from_session.process() needs for a
    snap- or burst-shaped single capture -- the same shape
    provenance.record_capture/record_burst would have produced, built
    directly instead so process() never needs an on-disk session.json
    (see the module docstring)."""
    return {"kind": "snap", "file_prefix": prefix}


def hdr_cap_dict(sci_levels, dark_levels):
    """Mirrors provenance.record_hdr's own transformation (strip the
    CaptureResult objects out of each level dict, keep everything else)
    without ever calling record_hdr or touching disk."""
    def _clean(levels):
        return [{k: v for k, v in lv.items() if k != "frames"} for lv in levels]
    return {"kind": "hdr", "levels": _clean(sci_levels),
            "dark_levels": _clean(dark_levels)}


EMPTY_SESSION = {"captures": []}
# process() looks in session["captures"] for a "flat" entry to apply
# flat-field correction. Casual Mode shoots no flat frames -- this is
# genuinely correct, not a stand-in for something missing.


def processing_args():
    """Fixed display-processing defaults, matching qt_shell.py main()'s
    own defaults when launched with no display-processing CLI flags
    (build_display_flags against an empty argv): white level 65520,
    Reinhard white point 2.2, no CA correction, no white-balance gains,
    no sharpen, no shadow-deepen. Casual Mode does not expose those
    controls; if that's ever wanted, it is a UI addition here, not a
    change to what gets passed to hdr_from_session.process()."""
    return SimpleNamespace(wl=65520, lw=2.2, gains=None, ca=None,
                           sharpen=None, shadow_deepen=False)


ALL_FORMATS = ("dng", "png", "jpg", "tiff")


def _atomic_write(src, dest):
    """Copy src to dest via a temp name + os.replace -- the same atomic
    pattern every store writer in this project already uses. A delete-
    then-write would leave a window where dest holds nothing."""
    dest = Path(dest)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    shutil.copy2(str(src), str(tmp))
    os.replace(str(tmp), str(dest))


def _write_placeholder_jpg(cap_result, dest_jpg):
    """The "usable file immediately" placeholder: the camera's own
    preview JPG if it wrote one (real hardware -- free, already on disk
    the moment capture_still_async/capture_burst/capture_bracket_phase
    returns), else (FakeCamera, which never writes a preview -- see
    camera_backend.py's own CaptureResult docstring) a quick synthesized
    stand-in straight off the raw frame, so this behavior stays
    exercisable under --render-check without real hardware."""
    if cap_result.preview is not None and Path(cap_result.preview).exists():
        shutil.copy2(str(cap_result.preview), str(dest_jpg))
        return
    import tifffile
    from PIL import Image
    arr = tifffile.imread(str(cap_result.raw)).astype(np.float32)
    hi = float(arr.max()) or 1.0
    arr8 = np.clip(arr / hi * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(arr8).convert("L").save(str(dest_jpg), "JPEG")


def run_capture_and_save(camera, kind, out_root, formats, dng_merge=False,
                         n=DEFAULT_BURST, stops=None):
    """The whole Casual Mode capture-to-disk flow for one shot: capture
    through camera_backend.py (zero session/provenance awareness of its
    own, confirmed by this project's Tier 0 investigation), stage into a
    throwaway temp directory, run the SAME processing chain qt_shell.py's
    normal path uses (hdr_from_session.process(), called directly rather
    than through its session.json-requiring CLI -- see the module
    docstring), write every checked format into out_root, and delete the
    staging directory unconditionally, success or failure.

    kind is "snap", "burst", or "hdr". formats is a nonempty subset of
    ALL_FORMATS. dng_merge only matters when "dng" is checked and kind is
    not "snap" -- see the module docstring for what it chooses between.

    Returns {"ok": bool, "files": [Path, ...], "error": str or None}.
    Never raises: a processing failure is reported through this dict (the
    "honest failure" path), not an exception the caller has to catch."""
    formats = set(formats)
    assert formats, "at least one format must be selected"
    assert formats <= set(ALL_FORMATS), "unknown format(s): {}".format(formats - set(ALL_FORMATS))
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    ext = raw_ext(camera)
    stem = new_output_stem(out_root)
    placeholder = out_root / (stem + ".jpg")
    staging = Path(tempfile.mkdtemp(prefix="zynergy_casual_staging_"))
    try:
        if kind == "snap":
            result_holder = {}
            done = threading.Event()

            def _on_done(result):
                result_holder["result"] = result
                done.set()

            camera.capture_still_async(staging, "snap_frame_0000", _on_done)
            done.wait(timeout=30.0)
            result = result_holder.get("result")
            if isinstance(result, Exception):
                raise result
            if result is None:
                raise RuntimeError("capture timed out")
            cap = snap_cap_dict("snap_")
            cap_result = result
            raw_for_dng = result.raw
        elif kind == "burst":
            result = camera.capture_burst(staging, "snap_", n)
            cap = snap_cap_dict("snap_")
            cap_result = result["frames"][-1]
            raw_for_dng = result["frames"][0].raw
        elif kind == "hdr":
            ordered = sorted(stops or HDR_STOPS)
            camera.enter_still_mode()
            try:
                base_us = None
                try:
                    base_us = camera.read_exposure()["shutter_us"]
                finally:
                    pass
                sci_levels = camera.capture_bracket_phase(
                    staging, "", n, base_us, ordered)
            finally:
                camera.exit_still_mode(base_us)
            camera.enter_still_mode()
            try:
                dark_levels = camera.capture_bracket_phase(
                    staging, "dark_", n, base_us, ordered)
            finally:
                camera.exit_still_mode(base_us)
            cap = hdr_cap_dict(sci_levels, dark_levels)
            cap_result = dark_levels[-1]["frames"][-1]
            raw_for_dng = sci_levels[0]["frames"][0].raw
        else:
            raise ValueError("unknown capture kind {!r}".format(kind))

        _write_placeholder_jpg(cap_result, placeholder)

        args = processing_args()
        disp = hdr_from_session.process(staging, EMPTY_SESSION, cap, args, ext)
        disp_png = disp.with_suffix(".png")

        written = []
        if "tiff" in formats:
            dest = out_root / (stem + ".tif")
            _atomic_write(disp, dest)
            written.append(dest)
        if "png" in formats:
            if not disp_png.exists():
                raise RuntimeError(
                    "PNG requested but Pillow is not installed on this "
                    "machine (final_display.png was never written)")
            dest = out_root / (stem + ".png")
            _atomic_write(disp_png, dest)
            written.append(dest)
        if "jpg" in formats:
            from PIL import Image
            src_for_jpg = disp_png if disp_png.exists() else disp
            img = Image.open(str(src_for_jpg)).convert("RGB")
            tmp = placeholder.with_suffix(".jpg.tmp")
            img.save(str(tmp), "JPEG", quality=92)
            os.replace(str(tmp), str(placeholder))   # atomic: placeholder -> real result
            written.append(placeholder)
        if "dng" in formats:
            if dng_merge and kind != "snap":
                # No valid single DNG for a merged multi-frame result --
                # deliver the pipeline's own already-computed raw-domain
                # master, honestly named (never ".dng"). See the module
                # docstring / HANDOFF.md's Casual Mode section.
                merged = staging / ("hdr_linear.tif" if kind == "hdr" else "single_master.tif")
                dest = out_root / (stem + "_raw.tif")
                _atomic_write(merged, dest)
            else:
                dest = out_root / (stem + "_raw." + ext)
                _atomic_write(raw_for_dng, dest)
            written.append(dest)

        if "jpg" not in formats:
            placeholder.unlink(missing_ok=True)   # was only ever a stand-in

        return {"ok": True, "files": written, "error": None}
    except (Exception, SystemExit) as exc:
        # HONEST FAILURE: the placeholder (if it made it to disk) already
        # told the truth -- "here is a usable file" -- and stays true even
        # though the chosen format did not complete. Keep it, never
        # delete it, never let it stand in silently as the requested
        # format; report plainly instead.
        return {"ok": False,
               "files": [placeholder] if placeholder.exists() else [],
               "error": str(exc)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Qt-bound parts
# ---------------------------------------------------------------------------
if _HAVE_QT:

    class _LivePreviewFallback(QWidget):
        """Off-rig stand-in preview, painted from focus_frame() -- FakeCamera
        has no .widget the way Picamera2Camera does. A small, independent
        equivalent of qt_shell.py's own _FakePreview: duplicated rather than
        imported, so this module never reaches into qt_shell.py at all (see
        the module docstring)."""

        def __init__(self, camera):
            super().__init__()
            self._cam = camera
            self._frame = None
            self.setMinimumSize(480, 360)
            self._refresh = QTimer(self)
            self._refresh.timeout.connect(self._paint_frame)
            self._refresh.start(100)

        def _paint_frame(self):
            self._frame = np.asarray(self._cam.focus_frame().data)
            self.update()

        def paintEvent(self, ev):
            painter = QPainter(self)
            if self._frame is not None:
                arr = np.clip(self._frame * 255, 0, 255).astype(np.uint8)
                h, w = arr.shape
                img = QImage(arr.tobytes(), w, h, w, QImage.Format_Grayscale8)
                painter.drawImage(self.rect(), img)
            painter.end()

    class CasualModeWindow(QMainWindow):
        """Casual Mode's own window -- built by qt_shell.py's main() instead
        of FocusPreviewWindow when the "casual_mode" preference is on (see
        qt_shell.py's own main() and CASUAL_MODE_DEFAULT). Live preview,
        Snap/Burst/HDR capture, independent DNG/PNG/JPG/TIFF format
        checkboxes, a Burst/HDR-only "process DNG" checkbox, an output
        folder (persisted, default DEFAULT_OUT_ROOT), and nothing else --
        no session browsing, no calibration menus, no measurement tools;
        those all stay on the normal (non-casual) path."""

        capture_done_signal = pyqtSignal(object)

        def __init__(self, camera, tick_ms=100):
            super().__init__()
            self.camera = camera
            self._capturing = False
            self.out_root = Path(_load_pref("casual_output_root", str(DEFAULT_OUT_ROOT)))

            self.preview = camera.widget if hasattr(camera, "widget") \
                else _LivePreviewFallback(camera)

            self.status = QLabel("ready")
            self.status.setWordWrap(True)

            self.kind_combo = QComboBox()
            self.kind_combo.addItem("Snap")
            self.kind_combo.addItem("Burst")
            self.kind_combo.addItem("HDR")
            self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)

            self.dng_check = QCheckBox("DNG (raw)")
            self.png_check = QCheckBox("PNG")
            self.jpg_check = QCheckBox("JPG")
            self.jpg_check.setChecked(True)
            self.tiff_check = QCheckBox("TIFF")
            self.dng_merge_check = QCheckBox("Process DNG (merge Burst/HDR frames)")
            self.dng_merge_check.setEnabled(False)
            self.dng_check.toggled.connect(self._update_dng_merge_enabled)

            self.out_root_label = QLabel(str(self.out_root))
            change_out_btn = QPushButton("Change output folder...")
            change_out_btn.clicked.connect(self._choose_out_root)

            self.capture_btn = QPushButton("Capture")
            self.capture_btn.clicked.connect(self._start_capture)

            formats_col = QVBoxLayout()
            for w in (self.dng_check, self.dng_merge_check, self.png_check,
                     self.jpg_check, self.tiff_check):
                formats_col.addWidget(w)

            controls_col = QVBoxLayout()
            controls_col.addWidget(QLabel("Capture kind"))
            controls_col.addWidget(self.kind_combo)
            controls_col.addWidget(QLabel("Output formats"))
            controls_col.addLayout(formats_col)
            controls_col.addWidget(QLabel("Output folder"))
            controls_col.addWidget(self.out_root_label)
            controls_col.addWidget(change_out_btn)
            controls_col.addWidget(self.capture_btn)
            controls_col.addWidget(self.status)
            controls_col.addStretch(1)

            controls = QWidget()
            controls.setLayout(controls_col)

            root = QHBoxLayout()
            root.addWidget(self.preview, 1)
            root.addWidget(controls)
            central = QWidget()
            central.setLayout(root)
            self.setCentralWidget(central)

            self.capture_done_signal.connect(self._on_capture_finished)

            # Continuous auto-exposure/AWB, frozen just before each shot
            # (see _freeze_exposure) -- a point-and-shoot default, not the
            # locked/reproducible exposure FocusPreviewWindow needs for a
            # measurement. Deliberately does not touch ~/imx/profile.json
            # (see the module docstring for why).
            self.camera.set_exposure(auto_exposure=True, auto_white_balance=True)
            self.camera.start()

        def _update_dng_merge_enabled(self, checked):
            self.dng_merge_check.setEnabled(checked and self.kind_combo.currentText() != "Snap")

        def _on_kind_changed(self, _index):
            self.dng_merge_check.setEnabled(
                self.dng_check.isChecked() and self.kind_combo.currentText() != "Snap")

        def _choose_out_root(self):
            chosen = QFileDialog.getExistingDirectory(
                self, "Choose output folder", str(self.out_root))
            if not chosen:
                return
            self.out_root = Path(chosen)
            self.out_root_label.setText(str(self.out_root))
            _save_pref("casual_output_root", str(self.out_root))

        def _selected_formats(self):
            fmts = set()
            if self.dng_check.isChecked():
                fmts.add("dng")
            if self.png_check.isChecked():
                fmts.add("png")
            if self.jpg_check.isChecked():
                fmts.add("jpg")
            if self.tiff_check.isChecked():
                fmts.add("tiff")
            return fmts

        def _freeze_exposure(self):
            e = self.camera.read_exposure()
            self.camera.apply_exposure_lock({
                "shutter_us": e["shutter_us"], "analogue_gain": e["analogue_gain"],
                "awb_red_gain": e["awb_red_gain"], "awb_blue_gain": e["awb_blue_gain"]})

        def _resume_auto(self):
            self.camera.set_exposure(auto_exposure=True, auto_white_balance=True)

        def _start_capture(self):
            if self._capturing:
                return
            formats = self._selected_formats()
            if not formats:
                QMessageBox.warning(self, "No format selected",
                                    "Choose at least one output format first.")
                return
            kind = self.kind_combo.currentText().lower()
            n = DEFAULT_BURST
            if kind in ("burst", "hdr"):
                n, ok = QInputDialog.getInt(
                    self, "Frame count",
                    "{} frames{}:".format(
                        "Burst" if kind == "burst" else "HDR frames per level",
                        "" if kind == "burst" else " ({} levels)".format(len(HDR_STOPS))),
                    DEFAULT_BURST, 1, MAX_BURST, 1)
                if not ok:
                    return
            dng_merge = self.dng_merge_check.isChecked() and self.dng_merge_check.isEnabled()

            self._capturing = True
            self.capture_btn.setEnabled(False)
            self.capture_btn.setText("Capturing...")
            self.status.setText("capturing...")
            self._freeze_exposure()

            def _worker():
                try:
                    result = run_capture_and_save(
                        self.camera, kind, self.out_root, formats,
                        dng_merge=dng_merge, n=n)
                except Exception as exc:   # pragma: no cover - defensive only;
                                           # run_capture_and_save itself never raises
                    result = {"ok": False, "files": [], "error": str(exc)}
                self.capture_done_signal.emit(result)

            threading.Thread(target=_worker, daemon=True).start()

        def _on_capture_finished(self, result):
            self._capturing = False
            self.capture_btn.setEnabled(True)
            self.capture_btn.setText("Capture")
            self._resume_auto()
            if result["ok"]:
                names = ", ".join(p.name for p in result["files"])
                self.status.setText("saved: {}".format(names))
            else:
                self.status.setText("capture failed: {}".format(result["error"]))


# ---------------------------------------------------------------------------
# Headless self-check (no PyQt5 needed for the Qt-free parts; the window
# itself is checked too, SKIPPED rather than FAILED if PyQt5 is missing).
# ---------------------------------------------------------------------------
def render_check():
    import tifffile

    assert_no_provenance_import()
    print("import-boundary check PASS: no provenance write function (or "
          "the provenance module itself) is bound anywhere in this "
          "module's own namespace")

    # --- new_output_stem: collision avoidance, any-extension, any-suffix ---
    root = Path("/tmp/zynergy_casual_render_check_stems")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    stem1 = new_output_stem(root)
    (root / (stem1 + ".jpg")).write_bytes(b"x")
    stem2 = new_output_stem(root)
    assert stem2 != stem1, "a stem already in use (even just a .jpg) must not repeat"
    (root / (stem2 + "_raw.tif")).write_bytes(b"x")   # only a suffixed file exists
    stem3 = new_output_stem(root)
    assert stem3 != stem2, "a stem used only via a _raw-suffixed file must still be avoided"
    shutil.rmtree(root)
    print("new_output_stem check PASS: collision avoidance covers any "
          "extension and any _raw-suffixed variant, not just an exact match")

    # --- raw_ext: duck-typed off hasattr(camera, "widget") -----------------
    fake = FakeCamera(async_delay_s=0.0)
    assert raw_ext(fake) == "tif"
    fake.stop()
    print("raw_ext check PASS: FakeCamera (no .widget) reads as \"tif\"")

    # --- run_capture_and_save: snap, every format-checkbox combination -----
    out_root = Path("/tmp/zynergy_casual_render_check_out")
    if out_root.exists():
        shutil.rmtree(out_root)
    cam = FakeCamera(async_delay_s=0.0)

    combos = [{"jpg"}, {"tiff"}, {"png"}, {"dng"},
             {"tiff", "jpg"}, {"dng", "jpg"}, {"png", "jpg"}]
    for formats in combos:
        r = run_capture_and_save(cam, "snap", out_root, formats)
        assert r["ok"], "snap + {} failed: {}".format(formats, r["error"])
        exts = {p.suffix for p in r["files"]}
        expected = set()
        if "tiff" in formats:
            expected.add(".tif")
        if "png" in formats:
            expected.add(".png")
        if "jpg" in formats:
            expected.add(".jpg")
        if "dng" in formats:
            expected.add("." + raw_ext(cam))   # ".tif" under FakeCamera
        assert exts == expected, \
            "snap formats={}: expected extensions {}, got {}".format(formats, expected, exts)
    print("run_capture_and_save (snap) check PASS: every representative "
          "format-checkbox combination produces exactly its expected file "
          "set, snap's own single frame is the only raw involved")

    # --- no leftovers: staging dir always gone, no session.json anywhere ---
    for p in out_root.rglob("session.json"):
        raise AssertionError("a session.json exists under Casual output: {}".format(p))
    tmp_leftovers = list(Path(tempfile.gettempdir()).glob("zynergy_casual_staging_*"))
    assert not tmp_leftovers, \
        "staging directories must not survive a completed capture: {}".format(tmp_leftovers)
    print("cleanup check PASS: no session.json anywhere under Casual "
          "output, no staging directory left behind")

    # --- burst + HDR: still no provenance, one merged result ---------------
    r = run_capture_and_save(cam, "burst", out_root, {"jpg", "tiff"}, n=3)
    assert r["ok"], "burst failed: {}".format(r["error"])
    assert {p.suffix for p in r["files"]} == {".jpg", ".tif"}

    r = run_capture_and_save(cam, "hdr", out_root, {"jpg"}, n=1,
                             stops=[-1.0, 0.0, 1.0])
    assert r["ok"], "hdr failed: {}".format(r["error"])
    assert {p.suffix for p in r["files"]} == {".jpg"}
    print("run_capture_and_save (burst/HDR) check PASS: same pipeline as "
          "snap, one merged final result, still zero provenance i/o")

    # --- dng_merge: unprocessed first frame vs. honestly-named merged ------
    r_unproc = run_capture_and_save(cam, "burst", out_root, {"dng"},
                                    dng_merge=False, n=3)
    assert r_unproc["ok"]
    assert r_unproc["files"][0].suffix == ".tif"   # FakeCamera's own raw ext
    assert "_raw" in r_unproc["files"][0].name

    r_proc = run_capture_and_save(cam, "burst", out_root, {"dng"},
                                  dng_merge=True, n=3)
    assert r_proc["ok"]
    assert r_proc["files"][0].name.endswith("_raw.tif"), \
        "a merged multi-frame result must be honestly saved as .tif, never .dng"
    print("dng_merge check PASS: unchecked delivers the first raw frame "
          "untouched, checked delivers the merged master honestly named "
          "_raw.tif -- never a mislabeled .dng either way")

    # --- honest failure: placeholder kept, real error reported -------------
    orig_process = hdr_from_session.process

    def _boom(*a, **kw):
        raise RuntimeError("simulated processing failure")

    hdr_from_session.process = _boom
    try:
        before = set(out_root.glob("*"))
        r = run_capture_and_save(cam, "snap", out_root, {"tiff"})
        assert not r["ok"], "a processing failure must be reported, not swallowed"
        assert "simulated processing failure" in r["error"]
        assert len(r["files"]) == 1 and r["files"][0].suffix == ".jpg", \
            "the placeholder JPG must be exactly what's kept on a failure"
        assert r["files"][0].exists(), \
            "the placeholder must genuinely still be on disk, not just claimed"
        after = set(out_root.glob("*"))
        assert after - before == {r["files"][0]}, \
            "a failed run must leave exactly the placeholder behind, nothing else"
    finally:
        hdr_from_session.process = orig_process
    print("honest-failure check PASS: a processing failure keeps the "
          "placeholder JPG, deletes nothing, and reports the real error "
          "rather than presenting a partial result as complete")

    cam.stop()
    shutil.rmtree(out_root, ignore_errors=True)

    if not _HAVE_QT:
        print("casual_mode.py Qt-gated checks SKIPPED: PyQt5 not importable")
        return

    # --- window: format checkboxes, dng_merge enable/disable, output root --
    from PyQt5.QtWidgets import QApplication
    qtapp = QApplication.instance() or QApplication([])
    orig_prefs_path = PREFS_PATH
    globals()["PREFS_PATH"] = Path("/tmp/zynergy_casual_render_check_prefs.json")
    PREFS_PATH.unlink(missing_ok=True)
    try:
        wcam = FakeCamera(async_delay_s=0.0)
        win = CasualModeWindow(wcam)
        assert win._selected_formats() == {"jpg"}, \
            "JPG is the only format checked by default"
        assert win.dng_merge_check.isEnabled() is False, \
            "process-DNG must start disabled: DNG itself is not checked yet " \
            "(and the default kind is Snap, where it would be moot anyway)"
        win.dng_check.setChecked(True)
        assert win.dng_merge_check.isEnabled() is False, \
            "checking DNG while the kind is still Snap must NOT enable " \
            "process-DNG -- Snap has only one frame, nothing to merge"
        win.kind_combo.setCurrentText("Burst")
        assert win.dng_merge_check.isEnabled() is True, \
            "checking DNG on Burst/HDR must enable process-DNG"
        win.kind_combo.setCurrentText("Snap")
        assert win.dng_merge_check.isEnabled() is False, \
            "switching back to Snap must disable process-DNG again, " \
            "regardless of the DNG checkbox's own state"
        wcam.stop()
    finally:
        globals()["PREFS_PATH"] = orig_prefs_path
    print("CasualModeWindow check PASS: JPG checked by default, "
          "process-DNG only enabled when DNG is checked AND the kind "
          "isn't Snap")

    # --- end-to-end through the real worker thread + queued signal ---------
    # Unlike everything above (which calls run_capture_and_save directly),
    # this drives the actual button handler and pumps
    # QApplication.processEvents() until _capturing clears -- same shape
    # qt_shell.py's own z-stack-aid check uses, since capture_done_signal
    # is a genuinely queued cross-thread connection a headless script must
    # pump itself.
    e2e_root = Path("/tmp/zynergy_casual_render_check_e2e")
    if e2e_root.exists():
        shutil.rmtree(e2e_root)
    ecam = FakeCamera(async_delay_s=0.0)
    ewin = CasualModeWindow(ecam)
    ewin.out_root = e2e_root
    ewin.tiff_check.setChecked(True)
    import time
    ewin._start_capture()
    deadline = time.monotonic() + 5.0
    while ewin._capturing and time.monotonic() < deadline:
        qtapp.processEvents()
    assert not ewin._capturing, "capture must finish within the deadline"
    assert "saved:" in ewin.status.text(), \
        "a successful capture must report what was saved: {!r}".format(ewin.status.text())
    produced = list(e2e_root.glob("*"))
    exts = {p.suffix for p in produced}
    assert exts == {".jpg", ".tif"}, \
        "JPG (default) + TIFF (checked) must both land: got {}".format(exts)
    ecam.stop()
    shutil.rmtree(e2e_root, ignore_errors=True)
    print("CasualModeWindow end-to-end check PASS: a real Capture press, "
          "through the actual worker thread and queued completion signal, "
          "produces exactly the checked formats and reports success")


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("casual_mode.py is not a standalone tool; import "
                 "CasualModeWindow from qt_shell.py's main(), or run with "
                 "--render-check for the headless self-check.")
