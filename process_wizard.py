"""process_wizard.py - the "choose your operations" processing wizard
(BUILD_LIST Tier 3, item 5): frame-average, debayer (green plane or
tone-mapped RGB), optional color-correct, applied to a user-selected set of
files, picked via gallery.py.

A separate, additional path from qt_shell.py's existing "Process
session..." (ProcessSessionDialog + hdr_from_session.py) -- that flow stays
exactly as it is; it is the right tool for a session's own recorded HDR
bracket. This wizard is for the more general case: any set of Gallery
captures or loose files, each run through the same fixed pipeline shape
(frame-average -> debayer), independent of which session (if any) they
came from.

Not built here: HDR-merge grouping from arbitrary files. HDR merge is a
real pipeline stage, but turning it on for arbitrary picked files means a
real grouping UI (partition N files into exposure levels, enter each
level's exposure time) that the structured session/kind path already does
correctly for real brackets. Skipped deliberately -- see BUILD_LIST notes
-- not an oversight.

Two ways to run:
  python3 process_wizard.py --render-check   headless: InputGroup
                                             construction and a real
                                             frame_average.py/debayer.py
                                             subprocess round-trip in both
                                             green and rgb mode, no PyQt5.
  python3 process_wizard.py                  not a standalone tool; import
                                             ProcessWizard from qt_shell.py.
"""
from __future__ import annotations

import sys
from collections import namedtuple
from datetime import datetime
from pathlib import Path

try:
    from . import stacks as _stacks
except ImportError:
    import stacks as _stacks

try:
    from . import gallery as _gallery
except ImportError:
    import gallery as _gallery

try:
    from . import hdr_from_session as _hdr_from_session
except ImportError:
    import hdr_from_session as _hdr_from_session

try:
    from . import provenance
except ImportError:
    import provenance

# hdr_from_session.py's own default --wl (sensor white level, scaled to the
# 16-bit container frame_average.py writes) -- reused, not reinvented, so a
# master this wizard produces is interpreted with the same assumption
# hdr_from_session.py's science/snap branch already uses.
DEFAULT_WHITE_LEVEL = 65520.0


InputGroup = namedtuple("InputGroup", ["label", "frames", "stack_id", "stack_plane"])


def _entry_label(entry):
    return "{}_{}".format(Path(entry.session_dir).name, entry.capture_index)


def group_for_entry(entry):
    """One InputGroup per Gallery selection, its full burst (not just frame
    0) via gallery.capture_frame_paths -- the whole reason GalleryEntry
    grew file_prefix/stack_id/stack_plane."""
    return InputGroup(label=_entry_label(entry),
                      frames=_gallery.capture_frame_paths(entry),
                      stack_id=entry.stack_id, stack_plane=entry.stack_plane)


def group_for_manual_file(path):
    """A manually-added file (the Gallery pick dialog's escape hatch) has no
    session context to expand into a burst, so it is its own one-frame
    group -- run through the exact same pipeline as any Gallery selection,
    not refused and not a special-cased pass-through."""
    path = Path(path)
    return InputGroup(label=path.stem, frames=[path], stack_id=None, stack_plane=None)


def _run_tool(name, args, cwd):
    """hdr_from_session.py's own run_tool (command construction, stdout/
    stderr capture, the same sibling-script-by-absolute-path invocation),
    reused rather than reimplemented. Its sys.exit-on-failure is right for
    a one-shot CLI tool but wrong for a batch -- one bad group must not
    abort the rest -- so SystemExit is caught here and re-raised as a plain
    RuntimeError run_pipeline_for_group can catch and record."""
    try:
        return _hdr_from_session.run_tool(name, args, cwd)
    except SystemExit as exc:
        raise RuntimeError(str(exc))


def run_pipeline_for_group(group, out_dir, mode, gains=None):
    """frame_average.py (always -- even a 1-frame group goes through
    averaging as a real group with a real provenance record, the same "one
    uniform path" instinct as calibrate.py's own append-only design, not a
    special-cased pass-through for "already a master") then one debayer.py
    call. mode is "green" or "rgb". Output filename is stacks.output_name()
    when the group came from a tagged capture, else "<label>_final.tif".
    Never raises: a failed group is recorded in the returned dict's "error"
    key so the rest of a batch still runs, same standard
    hdr_from_session.py's own subprocess handling already holds."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"group": group.label, "output": None, "stages": [], "error": None}
    if not group.frames:
        result["error"] = "no frames found for this group"
        return result

    if group.stack_id is not None:
        out_name = _stacks.output_name(group.stack_id, group.stack_plane)
    else:
        out_name = "{}_final.tif".format(group.label)
    final = out_dir / out_name

    try:
        master = out_dir / "{}_master.tif".format(group.label)
        _run_tool("frame_average.py",
                 [str(f) for f in group.frames] + ["-o", str(master)], out_dir)
        result["stages"].append("frame_average ({} frame(s))".format(len(group.frames)))

        if mode == "green":
            _run_tool("debayer.py", [str(master), "--green", "-o", str(final)], out_dir)
            result["stages"].append("debayer --green")
        elif mode == "rgb":
            rgb_out = out_dir / "{}_rgb.tif".format(group.label)
            db_args = [str(master), "--rgb", "-o", str(rgb_out),
                      "--assume-linear", str(DEFAULT_WHITE_LEVEL),
                      "--tonemap", "reinhard", "--tonemap-white", "1.0",
                      "--tonemap-out", str(final)]
            if gains:
                db_args += ["--colour-gains", str(gains[0]), str(gains[1])]
            _run_tool("debayer.py", db_args, out_dir)
            result["stages"].append(
                "debayer --rgb (assume-linear {}, tonemap reinhard)".format(DEFAULT_WHITE_LEVEL))
        else:
            raise ValueError("mode must be 'green' or 'rgb', got {!r}".format(mode))

        result["output"] = final
    except Exception as exc:
        result["error"] = str(exc)
    return result


def run_pipeline(groups, out_dir, mode, gains=None):
    """run_pipeline_for_group over every group, in order. A failed group
    never stops the rest -- see that function's own contract."""
    return [run_pipeline_for_group(g, out_dir, mode, gains=gains) for g in groups]


def new_output_dir(out_root=None):
    """A fresh timestamped folder under out_root/processed, collision-
    avoiding the same way provenance.new_session_dir already does (mirrored,
    not imported, since this folder lives under processed/, not among the
    session dirs new_session_dir itself mints)."""
    out_root = Path(out_root) if out_root is not None else provenance.OUT_ROOT
    processed_root = out_root / "processed"
    processed_root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    d = processed_root / ts
    n = 1
    while d.exists():
        d = processed_root / "{}_{}".format(ts, n)
        n += 1
    d.mkdir(parents=True)
    return d


try:
    from PyQt5.QtWidgets import (QWizard, QWizardPage, QWidget, QVBoxLayout,
                                 QHBoxLayout, QLabel, QPushButton, QRadioButton,
                                 QCheckBox, QDoubleSpinBox, QPlainTextEdit,
                                 QFileDialog, QButtonGroup)
    from PyQt5.QtCore import QThread, pyqtSignal
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


if _HAVE_QT:

    class _FileSelectPage(QWizardPage):
        """Page 1: gallery.GalleryWidget embedded directly (multi-select),
        same pattern wizard_pages.ImageSourcePage already uses to embed
        _CapturePane -- no dialog layer, the page IS the picker. Plus a
        manual-file button for loose files outside any session; a manually
        added file runs through the exact same pipeline as a Gallery pick,
        as its own one-frame group (group_for_manual_file), never refused."""

        def __init__(self, out_root=None, parent=None):
            super().__init__(parent)
            self.setTitle("Select files")
            self.setSubTitle(
                "Pick one or more captures to process. Add a loose file with "
                "the button below if it isn't in a session.")
            self._manual_paths = []

            self.gallery = _gallery.GalleryWidget(out_root, multi_select=True)
            self.gallery.list_widget.itemSelectionChanged.connect(self.completeChanged)
            manual_btn = QPushButton("Add file manually...")
            manual_btn.clicked.connect(self._on_manual)
            self.manual_label = QLabel("")
            self.manual_label.setWordWrap(True)

            lay = QVBoxLayout(self)
            lay.addWidget(self.gallery, 1)
            lay.addWidget(manual_btn)
            lay.addWidget(self.manual_label)

        def _on_manual(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Add file", "",
                "Raw / mosaic (*.dng *.tif *.tiff);;All files (*)")
            if path:
                self._manual_paths.append(Path(path))
                self.manual_label.setText(
                    "Manually added: " + ", ".join(p.name for p in self._manual_paths))
                self.completeChanged.emit()

        def isComplete(self):
            return bool(self.gallery.list_widget.selectedItems()) or bool(self._manual_paths)

        def cleanupPage(self):
            self.gallery.stop()

        def groups(self):
            groups = [group_for_entry(e) for e in self.gallery.selected_entries()]
            groups += [group_for_manual_file(p) for p in self._manual_paths]
            return groups


    class _OperationsPage(QWizardPage):
        """Page 2: one shared operation set for the whole batch (not
        per-file -- keeps this legible). Green plane (measurement) or RGB
        (display, tone-mapped); color-correct gains only offered for RGB,
        since debayer.py's --colour-gains is display-branch-only."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setTitle("Operations")
            self.setSubTitle(
                "Every selected file goes through frame-average, then this "
                "debayer mode.")

            self.green_radio = QRadioButton("Green plane (measurement)")
            self.rgb_radio = QRadioButton("RGB (display, tone-mapped)")
            self.green_radio.setChecked(True)
            group = QButtonGroup(self)
            group.addButton(self.green_radio)
            group.addButton(self.rgb_radio)

            self.gains_box = QCheckBox("Apply color-correct gains")
            self.red_gain = QDoubleSpinBox()
            self.red_gain.setRange(0.1, 8.0)
            self.red_gain.setDecimals(3)
            self.red_gain.setValue(1.0)
            self.blue_gain = QDoubleSpinBox()
            self.blue_gain.setRange(0.1, 8.0)
            self.blue_gain.setDecimals(3)
            self.blue_gain.setValue(1.0)
            self.gains_box.setEnabled(False)
            self.red_gain.setEnabled(False)
            self.blue_gain.setEnabled(False)
            self.rgb_radio.toggled.connect(self._on_mode_toggled)
            self.gains_box.toggled.connect(self._on_gains_toggled)

            gains_row = QHBoxLayout()
            gains_row.addWidget(QLabel("Red:"))
            gains_row.addWidget(self.red_gain)
            gains_row.addWidget(QLabel("Blue:"))
            gains_row.addWidget(self.blue_gain)

            lay = QVBoxLayout(self)
            lay.addWidget(self.green_radio)
            lay.addWidget(self.rgb_radio)
            lay.addWidget(self.gains_box)
            lay.addLayout(gains_row)
            lay.addStretch(1)

        def _on_mode_toggled(self, on):
            self.gains_box.setEnabled(on)
            if not on:
                self.gains_box.setChecked(False)

        def _on_gains_toggled(self, on):
            self.red_gain.setEnabled(on)
            self.blue_gain.setEnabled(on)

        def mode(self):
            return "rgb" if self.rgb_radio.isChecked() else "green"

        def gains(self):
            if self.rgb_radio.isChecked() and self.gains_box.isChecked():
                return (self.red_gain.value(), self.blue_gain.value())
            return None


    class _RunWorker(QThread):
        result_signal = pyqtSignal(dict)
        done_signal = pyqtSignal()

        def __init__(self, groups, out_dir, mode, gains, parent=None):
            super().__init__(parent)
            self._groups = groups
            self._out_dir = out_dir
            self._mode = mode
            self._gains = gains

        def run(self):
            for group in self._groups:
                result = run_pipeline_for_group(group, self._out_dir, self._mode, self._gains)
                self.result_signal.emit(result)
            self.done_signal.emit()


    class _RunPage(QWizardPage):
        """Page 3: lists the resolved groups, runs them on a worker thread
        (same persistent-worker-thread / busy-guard shape as qt_shell.py's
        own _run_process_cmd) once Run is pressed, streams each group's
        result into a log as it lands. Finish enables once the run
        completes, success or failure alike -- a failure is shown, never
        hidden."""

        def __init__(self, out_root=None, parent=None):
            super().__init__(parent)
            self.setTitle("Run")
            self._out_root = out_root
            self._worker = None
            self._done = False
            self._out_dir = None

            self.summary_label = QLabel("")
            self.summary_label.setWordWrap(True)
            self.run_btn = QPushButton("Run")
            self.run_btn.clicked.connect(self._on_run)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)

            lay = QVBoxLayout(self)
            lay.addWidget(self.summary_label)
            lay.addWidget(self.run_btn)
            lay.addWidget(self.log, 1)

        def initializePage(self):
            self._done = False
            self._out_dir = new_output_dir(self._out_root)
            groups = self.wizard().file_page.groups()
            lines = ["Output folder: {}".format(self._out_dir), ""]
            for g in groups:
                lines.append("{}: {} frame(s)".format(g.label, len(g.frames)))
            self.summary_label.setText("\n".join(lines))
            self.log.clear()
            self.run_btn.setEnabled(True)
            self.completeChanged.emit()

        def _on_run(self):
            self.run_btn.setEnabled(False)
            groups = self.wizard().file_page.groups()
            ops_page = self.wizard().ops_page
            self._worker = _RunWorker(groups, self._out_dir, ops_page.mode(), ops_page.gains())
            self._worker.result_signal.connect(self._on_group_result)
            self._worker.done_signal.connect(self._on_run_done)
            self._worker.start()

        def _on_group_result(self, result):
            if result["error"]:
                self.log.appendPlainText("{}: FAILED -- {}".format(
                    result["group"], result["error"]))
            else:
                self.log.appendPlainText("{}: wrote {} ({})".format(
                    result["group"], result["output"], ", ".join(result["stages"])))

        def _on_run_done(self):
            self._done = True
            self.completeChanged.emit()

        def isComplete(self):
            return self._done


    class ProcessWizard(QWizard):
        """The processing wizard (BUILD_LIST Tier 3 item 5): file select ->
        operations -> run. Separate from qt_shell.py's existing "Process
        session..." path -- see this module's own docstring."""

        def __init__(self, out_root=None, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Process files")
            self.file_page = _FileSelectPage(out_root)
            self.ops_page = _OperationsPage()
            self.run_page = _RunPage(out_root)
            self.addPage(self.file_page)
            self.addPage(self.ops_page)
            self.addPage(self.run_page)
            self.resize(820, 620)


def render_check():
    import shutil
    import tempfile

    import numpy as np
    import tifffile

    tmp_root = Path(tempfile.mkdtemp()) / "process_wizard_check"
    tmp_root.mkdir(parents=True)

    def _mosaic(seed):
        return np.random.default_rng(seed).integers(0, 4000, size=(32, 32)).astype(np.uint16)

    # A stack-tagged, 3-frame burst.
    tagged = _gallery.GalleryEntry(
        session_dir=tmp_root, capture_index=0, kind="science",
        timestamp="2024-01-01T00:00:00+00:00", frame_count=3,
        file_prefix="science_", preview_path=None, raw_path=None,
        stack_id="T4", stack_plane=2)
    for i in range(3):
        tifffile.imwrite(str(tmp_root / "science_frame_{:04d}.tif".format(i)), _mosaic(i))
    tagged = tagged._replace(raw_path=tmp_root / "science_frame_0000.tif")

    group = group_for_entry(tagged)
    assert group.label == "{}_0".format(tmp_root.name)
    assert len(group.frames) == 3, "must recover all 3 frames of the burst, not just frame 0"

    # green mode
    out_dir_green = tmp_root / "out_green"
    result = run_pipeline_for_group(group, out_dir_green, mode="green")
    assert result["error"] is None, "green pipeline should not fail: {}".format(result["error"])
    assert result["output"].name == _stacks.output_name("T4", 2), \
        "a stack-tagged group's output must be named via stacks.output_name"
    assert result["output"].is_file(), "the green output file must actually exist"

    master = out_dir_green / "{}_master.tif".format(group.label)
    assert master.is_file(), "frame_average.py must have written the intermediate master"
    import json as _json
    desc = _json.loads(tifffile.TiffFile(str(master)).pages[0].description)
    assert desc.get("software") == "frame_average.py", \
        "the master's provenance must be a real frame_average.py record, not a copy"
    assert desc["science"]["count"] == 3, \
        "provenance must record all 3 frames were actually averaged"
    print("run_pipeline_for_group (green) check PASS: full burst recovered, real "
          "frame_average.py provenance on the intermediate master (even though "
          "this is a real multi-frame group, proving the pipeline is not a "
          "pass-through), output named via stacks.output_name for a tagged group")

    # rgb mode, 1-frame group (a manual file), proving the "always average, even
    # for one frame" contract with real provenance, not a special case.
    manual_path = tmp_root / "loose_frame.tif"
    tifffile.imwrite(str(manual_path), _mosaic(99))
    manual_group = group_for_manual_file(manual_path)
    assert manual_group.stack_id is None and len(manual_group.frames) == 1

    out_dir_rgb = tmp_root / "out_rgb"
    result_rgb = run_pipeline_for_group(manual_group, out_dir_rgb, mode="rgb",
                                        gains=(1.2, 1.4))
    assert result_rgb["error"] is None, "rgb pipeline should not fail: {}".format(
        result_rgb["error"])
    assert result_rgb["output"].name == "loose_frame_final.tif", \
        "an untagged group's output must be <label>_final.tif"
    assert result_rgb["output"].is_file()
    manual_master = out_dir_rgb / "loose_frame_master.tif"
    manual_desc = _json.loads(tifffile.TiffFile(str(manual_master)).pages[0].description)
    assert manual_desc["science"]["count"] == 1, \
        "a 1-frame group must still go through frame_average.py as a real " \
        "1-frame average, not a special-cased copy"
    print("run_pipeline_for_group (rgb, 1-frame manual group) check PASS: a "
          "single manually-added file still produces a real frame_average.py "
          "provenance record (not skipped), rgb output correctly named "
          "<label>_final.tif for an untagged group")

    # A deliberately-broken group (frames that don't exist) must report an
    # error, not raise, so the rest of a batch still runs.
    broken_group = InputGroup(label="broken", frames=[tmp_root / "does_not_exist.tif"],
                              stack_id=None, stack_plane=None)
    batch = run_pipeline([broken_group, manual_group], tmp_root / "out_batch", mode="green")
    assert batch[0]["error"] is not None, "a missing input frame must be a recorded error"
    assert batch[1]["error"] is None, \
        "one broken group in a batch must not stop the next group from running"
    print("run_pipeline batch check PASS: a broken group reports an error and "
          "the rest of the batch still runs to completion")

    shutil.rmtree(tmp_root.parent, ignore_errors=True)


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("process_wizard.py is not a standalone tool; import "
                 "ProcessWizard from qt_shell.py, or run with --render-check "
                 "for the headless self-check.")
