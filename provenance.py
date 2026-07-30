"""provenance.py - session creation, per-capture sidecar writing, and the
camera exposure/gain/white-balance profile store (BUILD_LIST Tier 3, item 1,
phase 1).

Pulled out of qt_shell.py verbatim, not rewritten: provenance recording
predates qt_shell.py itself and only ended up embedded in it when
capture.py got folded in (see HANDOFF.md's own account). This session's own
Tier 0 investigation confirmed camera_backend.py has zero session/
provenance awareness of its own -- the "session" it talks about there is a
still-mode camera-mode session, an unrelated concept -- so this module sits
at the base of the import graph: stdlib only, no PyQt6, no camera_backend
import at module level (only --render-check below reaches for FakeCamera,
to prove the mechanics against something real rather than hand-built
stand-ins).

Reading provenance back out for browsing/processing-prep (list_sessions,
load_session_json, processable_captures, capture_correction_status,
archive_session_raws) is a different concern from writing new provenance
records and stays in qt_shell.py -- out of phase-1 scope.

FULL_MODE_LBL/DENOISE/SHARPNESS moved here too, alongside Session, even
though the original plan didn't call them out by name: Session.write() is
their only consumer anywhere in the project, and leaving them behind in
qt_shell.py would have meant either a reverse import (provenance.py
reaching back into qt_shell.py, which qt_shell.py itself now imports --
a real cycle) or duplicating them in two places. They describe the fixed
capture-mode/denoise/sharpness settings recorded into session.json, which
is provenance data, not GUI state, so this module is where they belong.

Two ways to run:
  python3 provenance.py --render-check   headless: Session/record_capture/
                                         record_burst/record_hdr mechanics,
                                         load_profile/save_profile atomic-
                                         write behavior, new_session_dir/
                                         new_zstack_root_dir collision
                                         avoidance and session_dir= override.
                                         No PyQt6, no real camera (FakeCamera
                                         only).
  python3 provenance.py                  not a standalone tool; import from
                                         qt_shell.py, gallery.py, or
                                         wizard_pages.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# OUT_ROOT / PROFILE_PATH: reference these by ATTRIBUTE ONLY --
# provenance.OUT_ROOT / provenance.PROFILE_PATH -- never
# `from provenance import OUT_ROOT` or `from provenance import PROFILE_PATH`.
# qt_shell.py's own render_check() mutates these as module state for the
# whole duration of a self-check run (isolating test fixtures, and -- after
# two real incidents that silently overwrote the real ~/imx/profile.json
# with fake FakeCamera-probed data -- keeping the ENTIRE self-check off the
# real file). A `from X import Y` creates a second, independent binding
# that stops tracking the name the moment either side reassigns it, which
# would silently break that isolation guard. Every consumer (qt_shell.py,
# gallery.py, wizard_pages.py) must go through the module object.
OUT_ROOT = Path.home() / "captures"
PROFILE_PATH = Path.home() / "imx" / "profile.json"

# PROVENANCE_ROOT / FLAT_ROOT: added for the provenance-relocation build
# (Preferences-dialog plan set, Part 03). Same attribute-only-reference rule
# as OUT_ROOT/PROFILE_PATH above -- render_check() redirects all four for
# the whole duration of a self-check run.
#
# OUT_ROOT is now the CAPTURE root only (raw + processed image bytes, no
# session.json, no .meta.json sidecars) -- before this part it held both.
# PROVENANCE_ROOT holds session.json + every .meta.json sidecar, nothing
# else; a session's images live in a sibling directory under OUT_ROOT that
# shares its timestamp name, recorded explicitly in session.json's own
# "capture_dir" field (see Session.write) rather than re-derived by
# convention, so a reader never has to guess it back from a naming pattern.
# FLAT_ROOT is not timestamped at all: one standing library, replaced
# outright by each new Flat capture, reused across every session -- flat
# is a reusable calibration artifact, not a per-session capture.
PROVENANCE_ROOT = Path.home() / "provenance"
FLAT_ROOT = Path.home() / "flat"

# Recorded into every session.json (see Session.write below); not camera
# control inputs anywhere in this project, just fixed provenance fields
# describing the capture mode/denoise/sharpness settings in force.
FULL_MODE_LBL = "4056:3040:12:U"
DENOISE = "off"
SHARPNESS = "0"


def load_profile():
    """Load camera profile (exposure, gains, WB) from disk if it exists."""
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    return None


def save_profile(locked):
    """Persist camera profile (exposure, gains, WB) to disk. Atomic
    (temp file, then os.replace), same pattern save_pref/save_calibration/
    save_mark already use -- this was the one store writer in the file
    still using a direct write_text, and it cost real data: two overlapping
    --render-check processes racing a direct write against a read corrupted
    the actual on-disk hardware profile with fake FakeCamera-probed values
    during an earlier session's own testing (caught via git diff, not
    something the render-check suite itself would ever catch, since nothing
    here exercises concurrent writers)."""
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(locked, indent=2))
    os.replace(tmp, PROFILE_PATH)


def _dump_meta(path, md):
    """Write capture metadata to a JSON sidecar file."""
    def _j(o):
        try:
            json.dumps(o)
            return o
        except TypeError:
            return str(o)
    path.write_text(json.dumps({k: _j(v) for k, v in md.items()}, indent=2))


def new_session_dir(root=None):
    """Create a new timestamped session directory for captures."""
    root = Path(root) if root else OUT_ROOT
    ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    d = root / ts
    n = 1
    while d.exists():
        d = root / "{}_{}".format(ts, n)
        n += 1
    d.mkdir(parents=True, exist_ok=True)
    return ts, d


def new_session_dirs(capture_root=None, provenance_root=None):
    """Like new_session_dir, but mints a CAPTURE dir and a PROVENANCE dir
    together, sharing one timestamp name, checking both roots for a
    collision in lockstep so the two can never drift to different names.
    Returns (ts, capture_dir, provenance_dir); both directories are
    created. Provenance-relocation build (Part 03) -- see PROVENANCE_ROOT's
    own comment above for why the two are separate at all."""
    capture_root = Path(capture_root) if capture_root else OUT_ROOT
    provenance_root = Path(provenance_root) if provenance_root else PROVENANCE_ROOT
    ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    name = ts
    n = 1
    while (capture_root / name).exists() or (provenance_root / name).exists():
        name = "{}_{}".format(ts, n)
        n += 1
    cap_dir = capture_root / name
    prov_dir = provenance_root / name
    cap_dir.mkdir(parents=True, exist_ok=True)
    prov_dir.mkdir(parents=True, exist_ok=True)
    return ts, cap_dir, prov_dir


def new_zstack_root_dirs(capture_root=None, provenance_root=None):
    """Create a new timestamped z-stack root directory pair (the parent
    folders for one stack run's plane_0/plane_1/... sessions) -- same
    collision-avoiding timestamp loop as new_session_dirs, just named
    zstack_<timestamp> instead of <timestamp>, so it never collides with an
    ordinary session dir either. The returned ts doubles as the stack's own
    stack_id (stacks.apply_tag's tag value): the on-disk folder names and
    the tag they hold visibly match, rather than inventing a second ID
    scheme. Defaults to OUT_ROOT/"focal" and PROVENANCE_ROOT/"focal" -- a
    z-stack's captures live under a "focal" subfolder of the capture root,
    keeping them out of the flat list of ordinary sessions (Part 03)."""
    capture_root = Path(capture_root) if capture_root else (OUT_ROOT / "focal")
    provenance_root = Path(provenance_root) if provenance_root else (PROVENANCE_ROOT / "focal")
    ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    ts_n = ts
    n = 1
    while (capture_root / "zstack_{}".format(ts_n)).exists() or \
            (provenance_root / "zstack_{}".format(ts_n)).exists():
        ts_n = "{}_{}".format(ts, n)
        n += 1
    cap_dir = capture_root / "zstack_{}".format(ts_n)
    prov_dir = provenance_root / "zstack_{}".format(ts_n)
    cap_dir.mkdir(parents=True, exist_ok=True)
    prov_dir.mkdir(parents=True, exist_ok=True)
    return ts_n, cap_dir, prov_dir


class Session:
    """Session state: a CAPTURE directory (self.dir, image bytes only) and a
    separate PROVENANCE directory (self.prov_dir, session.json + every
    .meta.json sidecar, no image bytes) -- see PROVENANCE_ROOT's own comment
    for why these split apart in Part 03. The two share a timestamp name by
    construction whenever this class mints them itself; a caller providing
    an explicit session_dir must provide a matching explicit provenance_dir
    too, since only the caller knows the parallel structure it wants (e.g.
    the z-stack aid's focal/<stack_id>/plane_N/ shape on both sides)."""

    def __init__(self, root, locked, display_flags, session_dir=None, provenance_dir=None):
        self.root = Path(root)
        self.locked = dict(locked)
        self.display_flags = list(display_flags)
        if session_dir is not None:
            # Explicit directories (e.g. a z-stack's own plane_N naming)
            # instead of the usual auto-timestamped pair -- ts is just
            # whatever the capture directory is actually called.
            if provenance_dir is None:
                raise ValueError(
                    "Session(session_dir=...) requires a matching provenance_dir; "
                    "the two are no longer the same folder (Part 03).")
            self.dir = Path(session_dir)
            self.dir.mkdir(parents=True, exist_ok=True)
            self.ts = self.dir.name
            self.prov_dir = Path(provenance_dir)
            self.prov_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.ts, self.dir, self.prov_dir = new_session_dirs(root)
        self.captures = []
        self.write()

    def write(self):
        """Write session.json (into prov_dir, never dir) with current state.
        "capture_dir" is the one place a reader learns where this session's
        images actually live -- stored explicitly rather than re-derived
        from prov_dir's own path, since the two roots are independently
        configurable and nothing guarantees they share a common ancestor."""
        payload = {
            "session_timestamp": self.ts,
            "tool": "qt_shell.py",
            "mode": FULL_MODE_LBL,
            "denoise": DENOISE,
            "sharpness": SHARPNESS,
            "display_flags": self.display_flags,
            "capture_dir": str(self.dir),
            "captures": self.captures,
        }
        (self.prov_dir / "session.json").write_text(json.dumps(payload, indent=2))

    def existing(self, prefixes):
        """Files already present for any of these prefixes. Dark frames
        (any prefix starting with "dark_" -- HDR's per-level dark_1_/dark_2_
        etc. included, matching frames_for's own note in hdr_from_session.py
        that a plain 'dark_' won't catch 'dark_1_') live one level down, in
        dir/"dark"/ -- session-scoped imagery, nested under its own session
        rather than flat alongside science/hdr frames (Part 03)."""
        hits = []
        for p in prefixes:
            base = (self.dir / "dark") if p.startswith("dark_") else self.dir
            hits += list(base.glob("{}frame_*".format(p)))
        return hits

    def clear(self, prefixes, kinds):
        """Remove files for these prefixes and capture entries of these
        kinds -- both the frame itself and its .meta.json sidecar (which
        lives in prov_dir now, not beside the frame), so a reshoot never
        leaves an orphaned sidecar describing a file that no longer exists."""
        removed = 0
        for f in self.existing(prefixes):
            sidecar = self.prov_dir / (f.stem + ".meta.json")
            if sidecar.exists():
                sidecar.unlink()
            f.unlink()
            removed += 1
        self.captures = [c for c in self.captures if c.get("kind") not in kinds]
        self._reindex()
        self.write()
        return removed

    def _reindex(self):
        for i, c in enumerate(self.captures):
            c["index"] = i

    def record(self, entry):
        """Record a new capture entry to the session."""
        entry["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry["locked_settings"] = dict(self.locked)
        self.captures.append(entry)
        self._reindex()
        self.write()
        return entry["index"]

    def has_captures(self):
        return len(self.captures) > 0

    def close(self):
        """Remove both folders iff nothing was captured (only session.json
        on the provenance side, nothing at all on the capture side)."""
        if self.has_captures():
            return False
        cap_others = list(self.dir.iterdir())
        prov_others = [p for p in self.prov_dir.iterdir() if p.name != "session.json"]
        if cap_others or prov_others:
            return False
        (self.prov_dir / "session.json").unlink()
        self.prov_dir.rmdir()
        self.dir.rmdir()
        return True


# ---------------------------------------------------------------------------
# Recording a shot (pure: no Qt, so the record path is testable off-rig)
# ---------------------------------------------------------------------------
def record_capture(session, result):
    """Persist a CaptureResult into a Session: write the metadata sidecar next to
    the raw and append a 'snap' record. The real exposure and gain of the
    auto-exposed shot come off the metadata, since the GUI is not locking them.
    Returns the capture index. Qt-free on purpose, so the whole record-a-shot
    flow runs under --render-check with the FakeCamera."""
    sidecar = session.prov_dir / (result.raw.stem + ".meta.json")
    _dump_meta(sidecar, result.metadata or {})
    md = result.metadata or {}
    files = [result.raw.name] + ([result.preview.name] if result.preview else [])
    return session.record({
        "kind": "snap",
        "note": "",
        "file_prefix": "snap_",
        "frame_count": 1,
        "files": files,
        "actual_us": md.get("ExposureTime"),
        "actual_s": (md.get("ExposureTime") / 1e6) if md.get("ExposureTime") else None,
        "analogue_gain": md.get("AnalogueGain"),
    })


def record_burst(session, kind, file_prefix, result, note=""):
    """Persist a capture_burst() result into a Session: write every frame's
    .meta.json sidecar, then ONE session-level record for the whole burst (one
    session.record call per burst, not per frame). `result` is capture_burst's
    return value: {"actual_us": ..., "frames": [CaptureResult, ...]}. Returns the
    capture index. Qt-free, so this runs under --render-check with FakeCamera."""
    for i, frame in enumerate(result["frames"]):
        sidecar = session.prov_dir / (frame.raw.stem + ".meta.json")
        _dump_meta(sidecar, frame.metadata or {})
    actual = result["actual_us"]
    return session.record({
        "kind": kind,
        "note": note,
        "file_prefix": file_prefix,
        "frame_count": len(result["frames"]),
        "requested_us": actual,
        "actual_us": actual,
        "actual_s": (actual / 1e6) if actual else None,
    })


def record_hdr(session, sci_levels, dark_levels, note=""):
    """Persist an HDR bracket (the two capture_bracket_phase results, science then
    dark) into a Session: write every frame's sidecar across both phases, then
    ONE 'hdr' record carrying both level lists, mirroring do_hdr exactly. The
    CaptureResult objects are stripped out of the level dicts before they go into
    session.json (only JSON-serializable fields belong there; each frame's full
    metadata already went into its own sidecar). Returns the capture index."""
    def _write_sidecars_and_strip(levels):
        stripped = []
        for lv in levels:
            for frame in lv["frames"]:
                sidecar = session.prov_dir / (frame.raw.stem + ".meta.json")
                _dump_meta(sidecar, frame.metadata or {})
            stripped.append({k: v for k, v in lv.items() if k != "frames"})
        return stripped

    sci_clean = _write_sidecars_and_strip(sci_levels)
    dark_clean = _write_sidecars_and_strip(dark_levels)
    return session.record({
        "kind": "hdr", "note": note,
        "levels": sci_clean, "dark_levels": dark_clean,
    })


# ---------------------------------------------------------------------------
# Headless self-check (no Qt, no real camera -- FakeCamera only)
# ---------------------------------------------------------------------------
def render_check():
    import shutil
    import threading

    try:
        from .camera_backend import FakeCamera
    except ImportError:
        from camera_backend import FakeCamera

    global OUT_ROOT, PROFILE_PATH, PROVENANCE_ROOT, FLAT_ROOT

    # --- new_session_dir: basic creation + collision avoidance -------------
    root = Path("/tmp/zynergy_provenance_render_check_sessions")
    if root.exists():
        shutil.rmtree(root)
    ts1, d1 = new_session_dir(root)
    assert d1.is_dir() and d1.name == ts1, "new_session_dir must create and return the dir"
    # Force a real collision deterministically: pre-occupy the next two names
    # a call made "right now" would pick, rather than relying on two calls
    # landing in the same wall-clock second by luck.
    now_ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    (root / now_ts).mkdir(parents=True, exist_ok=True)
    (root / "{}_1".format(now_ts)).mkdir(parents=True, exist_ok=True)
    ts2, d2 = new_session_dir(root)
    assert d2.name not in (now_ts, "{}_1".format(now_ts)), \
        "new_session_dir must skip past both pre-occupied names, got {!r}".format(d2.name)
    assert d2.is_dir()
    # The returned ts is the RAW timestamp, not necessarily d.name -- only
    # the loop variable `d` gets the "_1"/"_2" collision suffix, `ts` itself
    # is never reassigned. Session.__init__ relies on exactly this: self.ts
    # can differ from self.dir.name after a collision. Not a defect
    # introduced by this move -- verbatim behavior from qt_shell.py, worth
    # this explicit note since it is easy to assume otherwise.
    assert ts2 == now_ts, \
        "the returned ts is the raw timestamp regardless of collision suffixing"
    print("new_session_dir check PASS: creates a fresh timestamped dir, "
          "skips past pre-occupied names on collision (via the returned "
          "dir's own name -- the returned ts stays the raw timestamp)")

    # --- new_session_dirs: capture+provenance pair, collision in lockstep --
    cap_root = Path("/tmp/zynergy_provenance_render_check_dual_cap")
    prov_root = Path("/tmp/zynergy_provenance_render_check_dual_prov")
    for r in (cap_root, prov_root):
        if r.exists():
            shutil.rmtree(r)
    ts3, cd1, pd1 = new_session_dirs(cap_root, prov_root)
    assert cd1.is_dir() and pd1.is_dir(), "new_session_dirs must create both dirs"
    assert cd1.name == pd1.name == ts3, "capture and provenance dirs must share the same name"
    # Pre-occupy the capture-side name only -- collision avoidance must
    # trigger from EITHER root, not just the one a caller happens to check.
    now_ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    (cap_root / now_ts).mkdir(parents=True, exist_ok=True)
    ts4, cd2, pd2 = new_session_dirs(cap_root, prov_root)
    assert cd2.name != now_ts and pd2.name != now_ts, \
        "a capture-side collision must also push the provenance-side name"
    assert cd2.name == pd2.name, "the two dirs must still match after a collision bump"
    print("new_session_dirs check PASS: capture+provenance dirs share one "
          "name, a collision on either root bumps both in lockstep")

    # --- new_zstack_root_dirs: zstack_ prefix, focal/ default, same pairing
    zcap_parent = Path("/tmp/zynergy_provenance_render_check_zstack_cap")
    zprov_parent = Path("/tmp/zynergy_provenance_render_check_zstack_prov")
    for r in (zcap_parent, zprov_parent):
        if r.exists():
            shutil.rmtree(r)
    stack_id1, sroot1, sprov1 = new_zstack_root_dirs(zcap_parent, zprov_parent)
    assert sroot1.name == sprov1.name == "zstack_{}".format(stack_id1), \
        "the stack_id returned must be exactly what both folder names carry"
    now_ts = datetime.strftime(datetime.now(), "%Y-%m-%d_%H%M%S")
    (zprov_parent / "zstack_{}".format(now_ts)).mkdir(parents=True, exist_ok=True)
    stack_id2, sroot2, sprov2 = new_zstack_root_dirs(zcap_parent, zprov_parent)
    assert stack_id2 != now_ts, \
        "new_zstack_root_dirs must skip a name pre-occupied on EITHER side"
    assert sroot2.name == sprov2.name == "zstack_{}".format(stack_id2)
    print("new_zstack_root_dirs check PASS: zstack_<ts> naming on both "
          "sides, stack_id matches, collision avoidance checks either root")

    # --- load_profile / save_profile: atomic write, round-trip -------------
    _orig_profile_path = PROFILE_PATH
    PROFILE_PATH = Path("/tmp/zynergy_provenance_render_check_profile.json")
    try:
        if PROFILE_PATH.exists():
            PROFILE_PATH.unlink()
        assert load_profile() is None, "load_profile on a missing file must return None"
        fake_locked = {"shutter_us": 12345, "analogue_gain": 2.0,
                       "awb_red_gain": 1.5, "awb_blue_gain": 1.8}
        save_profile(fake_locked)
        assert PROFILE_PATH.exists(), "save_profile must write the profile file"
        assert not PROFILE_PATH.with_suffix(".tmp").exists(), \
            "save_profile must not leave its temp file behind (atomic write via os.replace)"
        assert load_profile() == fake_locked, "load_profile must round-trip exactly what was saved"
    finally:
        PROFILE_PATH = _orig_profile_path
    print("load_profile / save_profile check PASS: missing file reads as None, "
          "atomic write leaves no .tmp behind, round-trips exactly")

    # --- Session: session_dir=/provenance_dir= override (z-stack plane_N) --
    explicit_cap_root = Path("/tmp/zynergy_provenance_render_check_explicit_cap")
    explicit_prov_root = Path("/tmp/zynergy_provenance_render_check_explicit_prov")
    for r in (explicit_cap_root, explicit_prov_root):
        if r.exists():
            shutil.rmtree(r)
    explicit_dir = explicit_cap_root / "plane_0"
    explicit_prov_dir = explicit_prov_root / "plane_0"
    try:
        Session(explicit_cap_root, {}, [], session_dir=explicit_dir)
        raise AssertionError("session_dir without provenance_dir must raise, not silently reuse it")
    except ValueError:
        pass
    s = Session(explicit_cap_root, {}, [], session_dir=explicit_dir,
                provenance_dir=explicit_prov_dir)
    assert s.dir == explicit_dir and s.ts == "plane_0", \
        "session_dir= must be used exactly as given, not auto-timestamped"
    assert s.prov_dir == explicit_prov_dir, \
        "provenance_dir= must be used exactly as given"
    assert (explicit_prov_dir / "session.json").exists(), \
        "session.json must land in prov_dir, not dir"
    assert not (explicit_dir / "session.json").exists(), \
        "session.json must NOT land in the capture dir (Part 03 split)"
    on_disk = json.loads((explicit_prov_dir / "session.json").read_text())
    assert on_disk["capture_dir"] == str(explicit_dir), \
        "session.json must record capture_dir explicitly, not leave it to be derived"
    print("Session session_dir=/provenance_dir= override check PASS: uses the "
          "exact given directories, session.json lands only in prov_dir and "
          "records capture_dir explicitly, a session_dir with no matching "
          "provenance_dir raises rather than guessing")

    # --- record_capture: sidecar written to prov_dir, session record appended
    tmp_root = Path("/tmp/zynergy_provenance_render_check_captures")
    tmp_prov_root = Path("/tmp/zynergy_provenance_render_check_captures_prov")
    for r in (tmp_root, tmp_prov_root):
        if r.exists():
            shutil.rmtree(r)
    _orig_prov_root = PROVENANCE_ROOT
    PROVENANCE_ROOT = tmp_prov_root
    try:
        session = Session(tmp_root, {}, [])
        cam = FakeCamera(async_delay_s=0.0)
        done = threading.Event()
        got = {}

        def _on_done(result):
            got["result"] = result
            done.set()

        cam.capture_still_async(session.dir, "snap_frame_0000", _on_done)
        done.wait(timeout=2.0)
        idx = record_capture(session, got["result"])
        assert idx == 0, "first recorded capture should be index 0"
        assert session.captures[0]["kind"] == "snap", "record_capture did not record a snap"
        sidecar = session.prov_dir / (got["result"].raw.stem + ".meta.json")
        assert sidecar.exists(), "record_capture must write its .meta.json sidecar into prov_dir"
        assert not (session.dir / sidecar.name).exists(), \
            "record_capture must NOT also leave a sidecar beside the raw frame"
        print("record_capture check PASS: sidecar written into prov_dir "
              "(not beside the raw frame), session record appended")

        # --- record_burst / record_hdr: sidecars in prov_dir, dark under dir/dark/
        bsession = Session(tmp_root, {}, [])
        bcam = FakeCamera()

        flat_result = bcam.capture_burst(bsession.dir, "flat_", 3, shutter_us=5000)
        flat_idx = record_burst(bsession, "flat", "flat_", flat_result)
        assert flat_idx == 0, "first burst record should be index 0"
        rec = bsession.captures[0]
        assert rec["kind"] == "flat" and rec["frame_count"] == 3, \
            "record_burst did not record a 3-frame flat"
        for i in range(3):
            sidecar = bsession.prov_dir / "flat_frame_{:04d}.meta.json".format(i)
            assert sidecar.exists(), "record_burst missing prov_dir sidecar for frame {}".format(i)

        bcam.enter_still_mode()
        sci = bcam.capture_bracket_phase(bsession.dir, "", 2, 10_000, [-1.0, 0.0, 1.0])
        dark_dir = bsession.dir / "dark"
        dark_dir.mkdir(exist_ok=True)
        dark = bcam.capture_bracket_phase(dark_dir, "dark_", 2, 10_000, [-1.0, 0.0, 1.0])
        bcam.exit_still_mode(8000)
        hdr_idx = record_hdr(bsession, sci, dark)
        assert hdr_idx == 1, "HDR should be the second record in this session"
        hdr_rec = bsession.captures[1]
        assert hdr_rec["kind"] == "hdr", "record_hdr did not record kind=hdr"
        assert len(hdr_rec["levels"]) == 3 and len(hdr_rec["dark_levels"]) == 3, \
            "record_hdr level counts off"
        assert "frames" not in hdr_rec["levels"][0], \
            "record_hdr must strip CaptureResult objects before writing session.json"
        for lv in sci:
            for i in range(2):
                sidecar = bsession.prov_dir / "{}frame_{:04d}.meta.json".format(lv["file_prefix"], i)
                assert sidecar.exists(), "record_hdr missing a science sidecar in prov_dir"
        json.loads((bsession.prov_dir / "session.json").read_text())   # must be JSON-serializable
        print("record_burst / record_hdr check PASS: sidecars written into "
              "prov_dir, session records appended, HDR level dicts JSON-clean")

        # --- existing()/clear(): dark_* prefixes resolve under dir/dark/,
        # and clear() removes both the frame and its prov_dir sidecar. Per-
        # level HDR dark prefixes are "dark_1_"/"dark_2_"/... (from `dark`
        # above), not the plain "dark_" a standalone science/snap dark burst
        # uses -- existing()'s startswith("dark_") check must catch both. --
        dark_prefixes = [lv["file_prefix"] for lv in dark]   # e.g. dark_1_, dark_2_, dark_3_
        found_dark = bsession.existing(dark_prefixes)
        assert found_dark and all(f.parent == dark_dir for f in found_dark), \
            "existing() must resolve dark_N_ prefixes under dir/dark/, not dir/ itself"
        n_removed = bsession.clear(dark_prefixes, {"hdr"})
        assert n_removed == 6, "clear() should have removed all 6 dark frames (3 levels x 2)"
        assert not any(dark_dir.glob("dark_*frame_*")), "clear() left a dark frame behind"
        assert not any(bsession.prov_dir.glob("dark_*frame_*.meta.json")), \
            "clear() must also remove the matching prov_dir sidecars, not just the frames"
        assert not any(c.get("kind") == "hdr" for c in bsession.captures), \
            "clear() must drop the matching capture entries too"
        print("existing()/clear() check PASS: dark_N_ prefixes resolve under "
              "dir/dark/, clear() removes frames AND their prov_dir sidecars")

        # --- close(): removes both dirs when nothing was captured ----------
        empty_session = Session(tmp_root, {}, [])
        empty_cap_dir, empty_prov_dir = empty_session.dir, empty_session.prov_dir
        assert empty_session.close() is True, "close() on an empty session must succeed"
        assert not empty_cap_dir.exists() and not empty_prov_dir.exists(), \
            "close() must remove both the capture dir and the provenance dir"
        print("close() check PASS: an empty session's capture dir AND "
              "provenance dir are both removed")
    finally:
        PROVENANCE_ROOT = _orig_prov_root

    cam.stop()
    bcam.stop()
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(cap_root, ignore_errors=True)
    shutil.rmtree(prov_root, ignore_errors=True)
    shutil.rmtree(zcap_parent, ignore_errors=True)
    shutil.rmtree(zprov_parent, ignore_errors=True)
    shutil.rmtree(explicit_cap_root, ignore_errors=True)
    shutil.rmtree(explicit_prov_root, ignore_errors=True)
    shutil.rmtree(tmp_root, ignore_errors=True)
    shutil.rmtree(tmp_prov_root, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("provenance.py is not a standalone tool; import its functions, "
                 "or run with --render-check for the headless self-check.")
