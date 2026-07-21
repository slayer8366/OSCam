"""gallery.py - the shared capture-browsing widget (BUILD_LIST Tier 3, item 4):
thumbnails from the JPG previews every real capture already writes alongside
its raw .dng/.tif, no raw decode needed just to populate a grid.

One widget, two modes:
  pick mode    replaces the plain QFileDialog.getOpenFileName "Open image..."
               calls in wizard_pages.py / measure.py / calibrate.py. Shows
               capture kind, timestamp, and stack tag for free (all already
               on the capture record in session.json); multi-select from the
               start, so the same widget is the processing wizard's
               file-selection step later, not a second one built for it.
  browse mode  a standalone "Browse captures" action for just looking.

Whether a capture already has annotations is a SEPARATE, deliberately lazy
check (capture_has_annotation), not part of the cheap listing: annotations
are keyed by the green-plane hash (measure.py's load_measurement_plane is
the one true path to that substrate -- see its own docstring), never a
display-referred derivative, so answering this honestly means decoding the
raw mosaic, not just hashing a small file that happens to sit in the
session folder. GalleryWidget only ever runs this in a background thread,
after the grid already shows the cheap data, so opening the gallery on a
large folder tree is never gated on it.

Two ways to run:
  python3 gallery.py --render-check   headless: entry listing (kind/
                                      timestamp/stack tag, no raw decode)
                                      plus a real capture_has_annotation
                                      round-trip against a temp annotations
                                      store. No PyQt5, no camera.
  python3 gallery.py                  not a standalone tool; import
                                      GalleryWidget/GalleryPickDialog/
                                      GalleryBrowseWindow from qt_shell.py,
                                      measure.py, calibrate.py, or
                                      wizard_pages.py.
"""
from __future__ import annotations

import json
import sys
from collections import namedtuple
from pathlib import Path

# stacks/annotations/pixel_hash: none of these import gallery.py, so a
# top-level import is safe -- unlike qt_shell.py and measure.py below, which
# will import GalleryPickDialog/GalleryBrowseWindow from here and would
# create a real import cycle if this file imported them back at load time.
try:
    from . import stacks as _stacks
except ImportError:
    import stacks as _stacks

try:
    from . import annotations as _annotations
except ImportError:
    import annotations as _annotations

try:
    from . import pixel_hash as _pixel_hash
except ImportError:
    import pixel_hash as _pixel_hash


def _lazy_qt_shell():
    """qt_shell.py's list_sessions/load_session_json/OUT_ROOT, imported
    lazily -- qt_shell.py imports this module (for GalleryBrowseWindow), so
    a top-level import back into it here would be circular. Same trick
    wizard_pages.py already uses for the same reason (see its own
    _lazy_qt_shell)."""
    try:
        from . import qt_shell as _qt_shell
    except ImportError:
        import qt_shell as _qt_shell
    return _qt_shell


def _lazy_measure():
    """measure.py's load_measurement_plane -- the one true path to the
    green-plane measurement substrate (provenance-guarded, handles both a
    raw mosaic and an already-extracted green plane). Lazy for the same
    reason as _lazy_qt_shell: measure.py will import GalleryPickDialog from
    here."""
    try:
        from . import measure as _measure
    except ImportError:
        import measure as _measure
    return _measure


GalleryEntry = namedtuple(
    "GalleryEntry",
    ["session_dir", "capture_index", "kind", "timestamp", "frame_count",
     "preview_path", "raw_path", "stack_tag"])


def _capture_file_prefix(cap):
    """The file_prefix a capture's frames are written under. HDR captures
    nest theirs one level down, per level (mirrors qt_shell.py's own
    capture_correction_status, which resolves this the same way)."""
    if cap.get("kind") == "hdr":
        levels = cap.get("levels") or []
        return levels[0]["file_prefix"] if levels else None
    return cap.get("file_prefix")


def _first_frame_paths(session_dir, prefix):
    """(raw_path, preview_path) for a capture's first frame, or (None, None)
    if nothing is found -- checks both raw extensions in use across the
    project (dng on-rig, tif off-rig), same as capture_correction_status's
    own _frames_for. The preview is only ever a sibling .jpg; it is never
    returned as the raw_path itself, since a Gallery pick must resolve to
    the same kind of path the old QFileDialog handed callers (a raw file),
    never the cosmetic proxy used to browse."""
    if not prefix:
        return None, None
    session_dir = Path(session_dir)
    for ext in ("dng", "tif"):
        raw = session_dir / "{}frame_0000.{}".format(prefix, ext)
        if raw.is_file():
            preview = session_dir / "{}frame_0000.jpg".format(prefix)
            return raw, (preview if preview.is_file() else None)
    return None, None


def _stack_tag_of(cap):
    if not _stacks.is_tagged(cap):
        return None
    plane = _stacks.plane_of(cap)
    return "{} plane {}".format(cap.get(_stacks.STACK), plane if plane is not None else "?")


def list_gallery_entries(out_root=None):
    """Every capture across every session under out_root, most recent
    session first (list_sessions' own order), as GalleryEntry tuples.
    Filesystem/JSON metadata only -- no raw decode, so this stays instant
    even over a large captures tree. Defaults to qt_shell.py's own OUT_ROOT
    (~/captures) when out_root is None."""
    qt_shell = _lazy_qt_shell()
    out_root = Path(out_root) if out_root is not None else qt_shell.OUT_ROOT
    entries = []
    for session_dir in qt_shell.list_sessions(out_root):
        session_json = qt_shell.load_session_json(session_dir)
        for cap in session_json.get("captures", []):
            prefix = _capture_file_prefix(cap)
            raw_path, preview_path = _first_frame_paths(session_dir, prefix)
            entries.append(GalleryEntry(
                session_dir=session_dir,
                capture_index=cap.get("index"),
                kind=cap.get("kind"),
                timestamp=cap.get("timestamp"),
                frame_count=cap.get("frame_count", 1),
                preview_path=preview_path,
                raw_path=raw_path,
                stack_tag=_stack_tag_of(cap)))
    return entries


def capture_has_annotation(raw_path):
    """Whether raw_path's real measurement substrate (its green plane, per
    measure.py's own load_measurement_plane -- decoded fresh from the raw
    mosaic, or used as-is if raw_path is already one) has ever been
    annotated. Decodes the raw mosaic, so this is deliberately never called
    for a whole folder tree up front -- see GalleryWidget. Never raises:
    anything that is not a valid measurement substrate (a flat/dark
    calibration frame, an unreadable file, calibrate.py/debayer.py not
    importable) just means 'no', the same defensive contract
    annotations.load_annotations() already holds."""
    try:
        measure = _lazy_measure()
        plane = measure.load_measurement_plane(raw_path)
        h = _pixel_hash.pixel_sha256(plane)
        return h in _annotations.load_annotations()
    except Exception:
        return False


try:
    from PyQt5.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout,
                                 QPushButton, QListWidget, QListWidgetItem,
                                 QAbstractItemView, QFileDialog)
    from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal
    from PyQt5.QtGui import QIcon, QPixmap
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False


if _HAVE_QT:

    _THUMB_SIZE = (160, 120)

    def _placeholder_icon():
        # No JPG preview (FakeCamera writes none off-rig, or a kind that
        # never gets one): a flat tile, not a raw decode just to have
        # something to show.
        pm = QPixmap(*_THUMB_SIZE)
        pm.fill(Qt.darkGray)
        return QIcon(pm)

    def _thumb_icon(preview_path):
        if preview_path is None:
            return _placeholder_icon()
        pm = QPixmap(str(preview_path))
        if pm.isNull():
            return _placeholder_icon()
        pm = pm.scaled(_THUMB_SIZE[0], _THUMB_SIZE[1], Qt.KeepAspectRatio,
                       Qt.SmoothTransformation)
        return QIcon(pm)

    class _AnnotationWorker(QThread):
        """Walks candidates (already-filtered to entries with a raw_path)
        calling the expensive capture_has_annotation one at a time, off the
        GUI thread -- same persistent-worker-thread shape the project
        already uses for anything not instant (_toggle_recording,
        _run_process_cmd in qt_shell.py). stop() is polled between items
        rather than killing the thread outright, so a half-decoded frame is
        never left in a bad state."""

        found_signal = pyqtSignal(int, bool)

        def __init__(self, candidates, parent=None):
            super().__init__(parent)
            self._candidates = candidates
            self._stop_flag = False

        def stop(self):
            self._stop_flag = True

        def run(self):
            for index, raw_path in self._candidates:
                if self._stop_flag:
                    return
                self.found_signal.emit(index, capture_has_annotation(raw_path))


    class GalleryWidget(QWidget):
        """One grid, driven by list_gallery_entries. multi_select=True is
        what lets this double as the processing wizard's file-selection
        step later -- the same widget, not a second one."""

        def __init__(self, out_root=None, multi_select=False, parent=None):
            super().__init__(parent)
            self._out_root = out_root
            self._entries = []
            self._worker = None

            self.list_widget = QListWidget()
            self.list_widget.setViewMode(QListWidget.IconMode)
            self.list_widget.setIconSize(QSize(*_THUMB_SIZE))
            self.list_widget.setResizeMode(QListWidget.Adjust)
            self.list_widget.setMovement(QListWidget.Static)
            self.list_widget.setSpacing(8)
            self.list_widget.setSelectionMode(
                QAbstractItemView.ExtendedSelection if multi_select
                else QAbstractItemView.SingleSelection)

            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self.list_widget)

            self.refresh()

        def refresh(self):
            self._stop_worker()
            self._entries = list_gallery_entries(self._out_root)
            self.list_widget.clear()
            for entry in self._entries:
                item = QListWidgetItem(_caption(entry))
                item.setIcon(_thumb_icon(entry.preview_path))
                self.list_widget.addItem(item)
            self._start_worker()

        def _start_worker(self):
            candidates = [(i, e.raw_path) for i, e in enumerate(self._entries)
                         if e.raw_path is not None]
            if not candidates:
                return
            self._worker = _AnnotationWorker(candidates)
            self._worker.found_signal.connect(self._on_annotation_found)
            self._worker.start()

        def _stop_worker(self):
            if self._worker is not None:
                self._worker.stop()
                self._worker.wait()
                self._worker = None

        def stop(self):
            """Owning dialogs/windows call this from their own close/reject
            path, so the background decode never keeps running against a
            widget that is about to be destroyed."""
            self._stop_worker()

        def _on_annotation_found(self, index, has_annotation):
            if not has_annotation or index >= self.list_widget.count():
                return
            item = self.list_widget.item(index)
            item.setText(_caption(self._entries[index], annotated=True))

        def selected_paths(self):
            paths = []
            for item in self.list_widget.selectedItems():
                idx = self.list_widget.row(item)
                raw = self._entries[idx].raw_path
                if raw is not None:
                    paths.append(raw)
            return paths


    def _caption(entry, annotated=False):
        parts = [entry.kind or "?", entry.timestamp or ""]
        if entry.stack_tag:
            parts.append(entry.stack_tag)
        if annotated:
            parts.append("annotated")
        return "\n".join(p for p in parts if p)


    class GalleryPickDialog(QDialog):
        """Pick mode: OK/Cancel over a GalleryWidget, plus a manual-file
        escape hatch so a session missing from the grid (or no sessions at
        all yet) is never a dead end -- falls back to exactly the plain
        QFileDialog this replaces."""

        def __init__(self, out_root=None, parent=None, multi_select=False):
            super().__init__(parent)
            self.setWindowTitle("Choose a capture")
            self._manual_path = None
            self.gallery = GalleryWidget(out_root, multi_select=multi_select)

            ok_btn = QPushButton("OK")
            cancel_btn = QPushButton("Cancel")
            manual_btn = QPushButton("Choose file manually...")
            ok_btn.clicked.connect(self.accept)
            cancel_btn.clicked.connect(self.reject)
            manual_btn.clicked.connect(self._on_manual)

            lay = QVBoxLayout(self)
            lay.addWidget(self.gallery, 1)
            btn_row = QHBoxLayout()
            btn_row.addWidget(manual_btn)
            btn_row.addStretch(1)
            btn_row.addWidget(ok_btn)
            btn_row.addWidget(cancel_btn)
            lay.addLayout(btn_row)
            self.resize(760, 540)

        def _on_manual(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "Open image", "",
                "Raw / mosaic (*.dng *.tif *.tiff);;JPEG preview (*.jpg *.jpeg);;"
                "All files (*)")
            if path:
                self._manual_path = Path(path)
                self.accept()

        def selected_paths(self):
            if self._manual_path is not None:
                return [self._manual_path]
            return self.gallery.selected_paths()

        def accept(self):
            self.gallery.stop()
            super().accept()

        def reject(self):
            self.gallery.stop()
            super().reject()


    class GalleryBrowseWindow(QDialog):
        """Browse mode: the standalone 'Browse captures' action. Same
        widget, no commit -- just looking."""

        def __init__(self, out_root=None, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Browse captures")
            self.gallery = GalleryWidget(out_root, multi_select=False)
            close_btn = QPushButton("Close")
            close_btn.clicked.connect(self.reject)

            lay = QVBoxLayout(self)
            lay.addWidget(self.gallery, 1)
            lay.addWidget(close_btn)
            self.resize(760, 540)

        def reject(self):
            self.gallery.stop()
            super().reject()

        def closeEvent(self, ev):
            self.gallery.stop()
            super().closeEvent(ev)


def render_check():
    import shutil
    import tempfile

    import numpy as np
    import tifffile

    tmp_root = Path(tempfile.mkdtemp()) / "gallery_check"
    tmp_root.mkdir(parents=True)

    # Session 1: untagged snap, with a preview jpg.
    s1 = tmp_root / "2024-01-01_000001"
    s1.mkdir()
    cap1 = {"index": 0, "kind": "snap", "file_prefix": "snap_",
            "frame_count": 1, "timestamp": "2024-01-01T00:00:01+00:00"}
    (s1 / "session.json").write_text(json.dumps({"captures": [cap1]}))
    tifffile.imwrite(str(s1 / "snap_frame_0000.tif"),
                      np.zeros((10, 10), dtype=np.uint16))
    (s1 / "snap_frame_0000.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    # Session 2: stack-tagged science capture, no preview written.
    s2 = tmp_root / "2024-01-01_000002"
    s2.mkdir()
    cap2 = {"index": 0, "kind": "science", "file_prefix": "science_",
            "frame_count": 1, "timestamp": "2024-01-01T00:00:02+00:00",
            "stack": "T4", "plane": 2}
    (s2 / "session.json").write_text(json.dumps({"captures": [cap2]}))
    tifffile.imwrite(str(s2 / "science_frame_0000.tif"),
                      np.zeros((10, 10), dtype=np.uint16))

    entries = list_gallery_entries(tmp_root)
    assert len(entries) == 2, "one entry per capture across both sessions"
    by_kind = {e.kind: e for e in entries}
    assert by_kind["snap"].stack_tag is None, \
        "an untagged capture must report no stack tag"
    assert by_kind["snap"].preview_path is not None, \
        "session 1's jpg must resolve as this capture's preview"
    assert by_kind["snap"].raw_path.name == "snap_frame_0000.tif"
    assert by_kind["science"].stack_tag == "T4 plane 2", \
        "a tagged capture's stack/plane must be surfaced, no raw decode needed"
    assert by_kind["science"].preview_path is None, \
        "no jpg was written for session 2; must not fabricate one"
    print("list_gallery_entries check PASS: one entry per capture, stack tag "
          "and preview resolved from session.json + filesystem alone, no "
          "raw decode performed")

    # capture_has_annotation: real green-plane hashes against a temp
    # annotations store, proving this checks the actual measurement
    # substrate (never a display-referred derivative, which structurally
    # can never be a key in annotations.json).
    measure = _lazy_measure()
    green_h, green_w = measure.GREEN_PLANE_RES[1], measure.GREEN_PLANE_RES[0]

    annotated_plane = np.random.default_rng(0).integers(
        0, 4096, size=(green_h, green_w)).astype(np.uint16)
    annotated_path = tmp_root / "annotated_green.tif"
    tifffile.imwrite(str(annotated_path), annotated_plane)
    h = _pixel_hash.pixel_sha256(annotated_plane)

    unannotated_plane = np.random.default_rng(1).integers(
        0, 4096, size=(green_h, green_w)).astype(np.uint16)
    unannotated_path = tmp_root / "unannotated_green.tif"
    tifffile.imwrite(str(unannotated_path), unannotated_plane)

    orig_annotation_path = _annotations.ANNOTATION_PATH
    _annotations.ANNOTATION_PATH = tmp_root / "annotations.json"
    try:
        record = _annotations.new_image_record(
            h, annotated_plane.shape, annotated_plane.dtype, kind="green")
        _annotations.ANNOTATION_PATH.write_text(json.dumps({h: record}))

        assert capture_has_annotation(annotated_path) is True, \
            "a green plane whose hash is in the store must report annotated"
        assert capture_has_annotation(unannotated_path) is False, \
            "a green plane never marked must report not annotated"
    finally:
        _annotations.ANNOTATION_PATH = orig_annotation_path
    print("capture_has_annotation check PASS: checks the real green-plane "
          "hash (measure.py's own load_measurement_plane substrate), "
          "correctly distinguishes an annotated plane from an unannotated "
          "sibling")

    shutil.rmtree(tmp_root.parent, ignore_errors=True)


if __name__ == "__main__":
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("gallery.py is not a standalone tool; import GalleryWidget/ "
                 "GalleryPickDialog/GalleryBrowseWindow from qt_shell.py, "
                 "measure.py, calibrate.py, or wizard_pages.py, or run with "
                 "--render-check for the headless self-check.")
