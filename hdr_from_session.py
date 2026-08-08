#!/usr/bin/env python3
"""hdr_from_session.py - turn a capture.py session into a finished display image.

Reads a session folder's session.json and runs the right processing chain for a
capture, then optionally tars the raw DNGs for storage. This is the delegated
processor that capture.py's "process now?" offer calls, and doubles as a
standalone capture-to-display tool.

Chains (display stages run ONLY if you supply their inputs):
  hdr      frame_average per level (+flat/dark) -> hdr_merge (recorded actual_s +
           white level) -> debayer (Lw 2.2 + CA/WB/sharpen you passed)
  science  frame_average (+flat/dark) -> debayer --assume-linear (Lw 1.0)
  snap     frame_average -> debayer --assume-linear (Lw 1.0)

  --ca -> CA-correct; --gains -> white balance; flat_/dark_ frames present ->
  flat/dark correction. The chain prints which stages ran and which were skipped.

Sibling tools (frame_average.py, hdr_merge.py, debayer.py, ca_lib.py) must sit in
this script's own folder; they are invoked by absolute path, so PATH need not be
set for processing.

Usage (Part 03: <provenance_dir> is where session.json lives; image bytes
live in a separate capture folder, read from session.json's own
"capture_dir" field -- see main()):
  hdr_from_session.py <provenance_dir> --wl 65520 --lw 2.2 --gains 1.89 1.59 \
      --ca ca.json --sharpen 1.5 --flat-root ~/flat
  hdr_from_session.py <provenance_dir> --kind hdr          # process last hdr capture
  hdr_from_session.py <provenance_dir> --index 3           # process captures[3]
  hdr_from_session.py <provenance_dir> --archive-raws      # tar+remove DNGs, no prompt
"""
import argparse
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

try:
    import camera_backend
except ImportError:
    camera_backend = None

SCRIPTS = Path(__file__).resolve().parent
__version__ = "1.0"

# Default --wl (sensor white level) for THIS merge path (frame_average ->
# hdr_merge -> debayer). White-level relocation: derives from
# camera_backend.BIT_DEPTH (the sensor profile's own fact, confirmed from
# the driver -- see camera_backend.py/imx477.py) rather than a hardcoded
# literal sitting in this session-processing module -- the exact defect
# Stage 3 already closed once for sensor dimensions. Still not a measured
# sensor value in the sense of "where THIS bracket actually saturates";
# it is the sensor's own bit-depth ceiling, left-justified into this
# project's uint16 raw-storage convention. qt_shell.py imports this
# constant rather than keeping its own copy -- the two used to be
# independently hardcoded and had already drifted into two different
# Python types (str default here, int default there) despite agreeing
# numerically; see CHANGELOG.md's 2026-08-03 "white_level defaults
# consolidated" entry. The real ceiling differs from this default and is
# measured, not a one-off guess: the August 2026 bracket's frame5/frame4
# ratio break puts it at ~61000, reproduced on a second bracket a month
# older. The actual merge for that bracket was run at --white-level
# 62100, landing the cutoff below the ratio's departure from 2.00 rather
# than at it, since that departure is gradual, not a step. The gain that
# ceiling is valid for is now confirmed, not unrecorded: AnalogueGain
# 3.282051, identical across all 80 science frames in that bracket's own
# capture sidecars (session.json's capture_dir plus an exact per-level
# exposure-time match tie the sidecars to this bracket unambiguously).
# This default stays 65520, NOT 61000 -- that number is only valid at
# this bracket's confirmed gain, and hardcoding it here as a new blanket
# default would repeat the exact mistake this constant's own history is
# already one instance of. That 62100 value is known wrong (unrelated to
# this relocation) and is deliberately not corrected here -- its own
# change, its own evidence, its own entry.
if camera_backend is not None:
    MERGE_WHITE_LEVEL_DEFAULT = camera_backend.white_level_for_bit_depth(
        camera_backend.BIT_DEPTH)
else:
    # No fabricated fallback (same reasoning as Stage 3 sequence 1's own
    # FULL_RES fallback fix): camera_backend.py not being importable at
    # all is a real, rare failure this file cannot paper over with a
    # plausible-looking number.
    MERGE_WHITE_LEVEL_DEFAULT = None


def run_tool(name, args, cwd):
    cmd = [sys.executable, str(SCRIPTS / name)] + [str(a) for a in args]
    print("  $ {} {}".format(name, " ".join(str(a) for a in args)))
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("\n{} FAILED:\n{}\n{}".format(name, r.stdout, r.stderr))
    return r


def frames_for(search_dir, file_prefix, ext):
    # prefix + 'frame_*' is exact for that prefix and never catches a longer one
    # (e.g. 'dark_' won't catch 'dark_1_'): every stem is <prefix>frame_<idx>.
    return sorted(search_dir.glob("{}frame_*.{}".format(file_prefix, ext)))


def yes_no(prompt, default_no=True):
    if not sys.stdin.isatty():
        return False
    ans = input(prompt).strip().lower()
    return ans in ("y", "yes")


def display_opts(a):
    """debayer display flags, each included only if supplied. TIFF/PNG/JPG
    are three independent debayer.py write-format flags hanging off the
    SAME in-memory tone-mapped array (debayer.py's own --tonemap-tiff/
    --tonemap-8bit/--tonemap-jpg) -- none of them read another format's
    file back off disk, so checking any subset never leaves one stale or
    missing relative to what was actually computed. All three getattr with
    safe defaults matching the pre-format-checkbox behavior (TIFF/PNG on,
    JPG off), so a caller that never heard of these flags keeps working
    unchanged."""
    out = []
    if a.ca:
        out += ["--ca-correct", a.ca]
    if a.gains:
        out += ["--colour-gains", a.gains[0], a.gains[1]]
    if a.sharpen is not None:
        out += ["--sharpen", a.sharpen]
    if a.shadow_deepen:
        out += ["--shadow-deepen"]
    if not getattr(a, "export_tiff", True):
        out += ["--no-tonemap-tiff"]
    if getattr(a, "export_png", True):
        out += ["--tonemap-8bit"]
    if getattr(a, "export_jpg", False):
        out += ["--tonemap-jpg"]
    return out


def pick_capture(session, kind, index):
    caps = session.get("captures", [])
    if not caps:
        sys.exit("session.json has no captures.")
    if index is not None:
        if not (0 <= index < len(caps)):
            sys.exit("--index {} out of range (0..{}).".format(index, len(caps) - 1))
        return caps[index]
    proc = {"hdr", "science", "snap"}
    want = {kind} if kind and kind != "auto" else proc
    for cap in reversed(caps):                        # most recent matching
        if cap.get("kind") in want:
            return cap
    sys.exit("no processable capture found (kind={}).".format(kind))


def process(capture_dir, dark_dir, session, cap, a, ext, publish_dir=None):
    """capture_dir is where this capture's OWN frames and outputs are READ
    and WRITTEN during processing -- the real session directory for a
    direct/manual run, or a same-device staging directory when the caller
    passed --capture-dir (gallery-race staging design, CHANGELOG.md's
    2026-08-05 entries). dark_dir is passed separately and ALWAYS resolves
    from the real session directory, never from a staging override: dark
    captures (standalone or HDR's per-level dark_N_) are a separate,
    never-staged call site in qt_shell.py's own capture flow, so they
    physically live at the real session_dir/"dark" even while capture_dir
    itself points at staging. Flat comes from a.flat_root instead -- a
    standing library shared across every session, never scanned out of
    this session's own captures list (Part 03: "last flat wins" now means
    "whatever is in the flat library right now", not "the last flat_
    capture recorded in this session").

    publish_dir, when given, is the real session directory to publish the
    finished set into once retention has run: each surviving file (never
    a directory) is moved from capture_dir into publish_dir individually
    via os.replace, after retention and after correction_status is built
    -- see the publish step near the end of this function for exactly
    which files and why this is per-file rather than a single directory
    rename. None (the default) skips publishing entirely, matching every
    non-staged caller (the manual processing wizard, the archive dialog),
    which already operate directly on the real session directory and have
    nothing to publish.

    Returns (display_path, correction_status). display_path is the
    conventional final_display.tif location -- a Path, not a promise it
    exists on disk, since TIFF is now a genuinely optional write-format
    (see display_opts()'s own note); nothing in this codebase currently
    reads the returned path back (main() discards it; the GUI runs this
    module as a subprocess and never sees the Python return value at all),
    so this is contract documentation, not a live dependency. correction_
    status is a dict with "flat_correction"/"dark_correction" plain-
    language strings naming the technique and whether it ran, plus
    "raw_discarded" (and "raw_discard_reason" when true) recording whether
    a.delete_raw_on_success deleted this capture's own raw frames + linear
    master once processing succeeded (Keep RAW Images off -- see main()'s
    own flag docstring). Never folded into a generic "processing complete"
    (CORRECTION_flat_dark_framing.md); a later reader must be able to tell
    "the user chose not to keep these" from "a file is missing" -- absence
    with a recorded reason is provenance, absence without one looks like
    corruption. ran/skipped below still drive the printed stage summary
    for a direct CLI user; correction_status is the same information in
    the shape a caller can persist onto the session record."""
    flat = frames_for(Path(a.flat_root), "flat_", ext)
    ran, skipped = [], []
    if flat:
        ran.append("flat-field ({} frames)".format(len(flat)))
        flat_status = "applied ({} frames)".format(len(flat))
    else:
        skipped.append("flat-field (no flat_ frames in the flat library)")
        flat_status = "skipped (no flat_ frames in the flat library)"

    kind = cap["kind"]
    if kind == "hdr":
        masters, times = [], []
        raw_files = []   # every science + dark frame across every level, for Keep RAW deletion
        dark_levels = {d["level"]: d["file_prefix"] for d in cap.get("dark_levels", [])}
        for lvl in cap["levels"]:
            L = lvl["level"]
            sci = frames_for(capture_dir, lvl["file_prefix"], ext)
            if not sci:
                sys.exit("level {}: no frames {}frame_*.{}".format(L, lvl["file_prefix"], ext))
            raw_files += sci
            fa = [f.name for f in sci] + ["-o", "master_{}.tif".format(L)]
            if flat:
                fa += ["--flat"] + [str(f) for f in flat]
            dpre = dark_levels.get(L)
            dfr = frames_for(dark_dir, dpre, ext) if dpre else []
            raw_files += dfr
            if dfr:
                fa += ["--dark"] + [str(f) for f in dfr]
            run_tool("frame_average.py", fa, capture_dir)
            masters.append("master_{}.tif".format(L))
            times.append(lvl["actual_s"])
        if any(dark_levels.values()):
            ran.append("dark ({} levels)".format(len(dark_levels)))
            dark_status = "applied ({} levels)".format(len(dark_levels))
        else:
            skipped.append("dark (no dark_ frames)")
            dark_status = "skipped (no dark_ frames)"
        hm = []
        for m, t in zip(masters, times):
            hm += ["-e", m, "{:.6g}".format(t)]
        hm += ["--white-level", a.wl, "-o", "hdr_linear.tif"]
        run_tool("hdr_merge.py", hm, capture_dir)
        ran.append("hdr_merge ({} levels, WL {})".format(len(masters), a.wl))
        db = ["hdr_linear.tif", "--rgb", "-o", "final.tif",
              "--tonemap", "reinhard", "--tonemap-white", a.lw] + display_opts(a)
        run_tool("debayer.py", db, capture_dir)
        ran.append("debayer (Lw {})".format(a.lw))
        # The linear master(s): per-level master_<L>.tif plus the merged
        # hdr_linear.tif -- final.tif/final_display.* are the whole POINT
        # of processing and are never touched here, only the raw-domain
        # intermediates Keep RAW Images off is meant to discard.
        master_files = [capture_dir / m for m in masters] + [capture_dir / "hdr_linear.tif"]

    elif kind in ("science", "snap"):
        sci = frames_for(capture_dir, cap["file_prefix"], ext)
        if not sci:
            sys.exit("no frames {}frame_*.{}".format(cap["file_prefix"], ext))
        raw_files = list(sci)
        fa = [f.name for f in sci] + ["-o", "single_master.tif"]
        if flat:
            fa += ["--flat"] + [str(f) for f in flat]
        dark = frames_for(dark_dir, "dark_", ext)          # standalone dark_frame_*
        raw_files += dark
        if dark:
            fa += ["--dark"] + [str(f) for f in dark]
            ran.append("dark ({} frames)".format(len(dark)))
            dark_status = "applied ({} frames)".format(len(dark))
        else:
            skipped.append("dark (no standalone dark_ frames)")
            dark_status = "skipped (no standalone dark_ frames)"
        run_tool("frame_average.py", fa, capture_dir)
        db = ["single_master.tif", "--rgb", "-o", "final.tif",
              "--assume-linear", a.wl, "--tonemap", "reinhard",
              "--tonemap-white", "1.0"] + display_opts(a)
        run_tool("debayer.py", db, capture_dir)
        ran.append("debayer --assume-linear {} (Lw 1.0)".format(a.wl))
        master_files = [capture_dir / "single_master.tif"]
    else:
        sys.exit("capture kind {!r} is not processable.".format(kind))

    if not a.ca:
        skipped.append("CA-correct (no --ca)")
    if not a.gains:
        skipped.append("white-balance (no --gains)")

    # final.tif is debayer.py's own primary -o output, always written --
    # the RGB measurement master, not a display format, no checkbox.
    # final_display.tif/.png/.jpg are now THREE independent, genuinely
    # optional debayer.py write-formats hanging off the same in-memory
    # tone-mapped array (debayer.py's own --tonemap-tiff/--tonemap-8bit/
    # --tonemap-jpg, wired from display_opts() above) -- none reads
    # another format's file back off disk, so this function never does a
    # post-hoc disk-round-trip conversion between them; debayer.py writes
    # exactly the formats asked for in one subprocess call. All three
    # default the same as before format checkboxes existed (TIFF/PNG on,
    # JPG off) via display_opts()'s own getattr defaults.
    disp = capture_dir / "final_display.tif"
    png = capture_dir / "final_display.png"
    jpg = capture_dir / "final_display.jpg"
    # final.tif itself (the debayer.py -o target set above, always written,
    # no checkbox) had no Python-side Path of its own before this -- every
    # reference to it was a bare string literal handed to the debayer.py
    # subprocess call. Named here so the publish step near the end of this
    # function can find it; found missing from that step's first on-rig
    # run (it was left behind in staging, published nowhere) precisely
    # because nothing else in this function ever needed to look at it again.
    final_tif = capture_dir / "final.tif"

    # Validate, don't just trust the debayer.py subprocess's exit status:
    # run_tool() above only checked returncode, and debayer.py itself
    # degrades a missing Pillow to a stderr warning with returncode still
    # 0 (a checked PNG/JPG that silently never got written would otherwise
    # be invisible -- _on_process_finished's success path only surfaces
    # stdout, never stderr). Check each requested format actually landed.
    for label, requested, path in (
        ("TIFF", getattr(a, "export_tiff", True), disp),
        ("PNG", getattr(a, "export_png", True), png),
        ("JPG", getattr(a, "export_jpg", False), jpg),
    ):
        if not requested:
            continue
        if path.exists():
            ran.append("{} display export".format(label))
        else:
            skipped.append(
                "{} display export (requested but not written -- check "
                "stderr above, e.g. Pillow missing for PNG/JPG)".format(label))

    # Hoisted above the export_dng block that sets it, and initialized here
    # rather than left absent: the publish step near the end of this
    # function needs to know whether a DNG export happened at all, and an
    # undefined-unless-exported local would make that a NameError instead
    # of the plain "was it requested" check dng_dest is None already reads
    # as everywhere else.
    dng_dest = None
    if getattr(a, "export_dng", False):
        # <file_prefix>raw.<ext> -- never a bare <stem>.dng for a merged
        # multi-frame result (a merge produces a derivative; a DNG
        # container would mislabel it as raw), same rule casual_mode.py's
        # own dng_merge handling already followed. own_prefix: hdr's own
        # "file_prefix" key is never set on the capture record itself
        # (only per-level), so fall back to the first level's.
        own_prefix = cap.get("file_prefix") or (
            cap["levels"][0]["file_prefix"] if kind == "hdr" else "")
        try:
            if getattr(a, "export_dng_merge", False) and kind != "snap":
                src = master_files[-1]   # hdr_linear.tif (hdr) / single_master.tif (science)
                dng_dest = capture_dir / "{}raw.tif".format(own_prefix)
            else:
                src = raw_files[0]
                dng_dest = capture_dir / "{}raw.{}".format(own_prefix, ext)
            if Path(src).exists():
                import shutil as _shutil_dng
                _shutil_dng.copy2(str(src), str(dng_dest))
                ran.append("DNG export ({})".format(dng_dest.name))
            else:
                skipped.append("DNG export (source frame already gone)")
        except Exception as exc:
            skipped.append("DNG export (failed: {})".format(exc))

    # Keep RAW Images off (a.delete_raw_on_success): delete this capture's
    # OWN raw frames now that processing has succeeded -- ONLY raw frames.
    # Never master_files (the averaged/merged intermediates: master_N.tif/
    # hdr_linear.tif for HDR, single_master.tif for science/snap) -- a user
    # leaving this off is consenting to discard raws, not derived outputs
    # built from a multi-frame bracket; the setting is named "Keep RAW
    # Images", not "Keep Intermediates". Never the shared flat library (a
    # reusable calibration artifact, not this capture's own raw), never
    # final.tif/final_display.*/the DNG or JPG exports just written above
    # (the processed/delivered results themselves). Deleting derived
    # outputs too was a real bug here until this fix (see CHANGELOG.md's
    # 2026-08-03 "Keep RAW Images narrowed to raws only" entry) -- if disk
    # pressure from keeping intermediates around is ever a real problem,
    # that needs its own explicitly-named setting and its own decision,
    # not a side effect of this one. Provenance is always written
    # regardless (main()'s own record-keeping happens on the caller's
    # side, in session.json); this only decides what survives on disk.
    # getattr with a False default, deliberately, not a required attribute
    # like a.flat_root above: the safe default for a destructive operation
    # is "don't delete", so an args object that never heard of this flag
    # (an older caller, say) must degrade to keeping everything, never to
    # discarding by surprise. Runs AFTER the DNG export above on purpose --
    # that step needs the raw frames to still exist.
    #
    # Each raw frame's own preview .jpg (Picamera2Camera writes both per
    # frame; FakeCamera never does, so this is a real-hardware-only path)
    # follows the SAME retention rule as the raw it belongs to -- removed
    # when the raw is, kept when the raw is kept -- since frames_for() only
    # ever globs the raw extension, no other code path ever cleans these up
    # and they would otherwise accumulate on every capture regardless of
    # this setting. Derived directly from each raw's own path (.with_suffix)
    # rather than a second frames_for() glob, so this can never touch a
    # frame this run didn't itself select.
    raw_discarded = False
    if getattr(a, "delete_raw_on_success", False):
        for f in raw_files:
            f = Path(f)
            if f.exists():
                f.unlink()
            preview = f.with_suffix(".jpg")
            if preview.exists():
                preview.unlink()
        raw_discarded = True

    print("\nStages run:    " + ", ".join(ran))
    print("Stages skipped: " + ", ".join(skipped))
    # Report whichever display format(s) actually landed -- TIFF is no
    # longer guaranteed, so this can no longer assume disp itself exists.
    written_display = [p.name for p in (disp, png, jpg) if p.exists()]
    print("\nDisplay image(s): {}".format(", ".join(written_display) or "none written"))
    # derived_outputs_discarded/derived_outputs_note are unconditional and
    # always False/the same text today -- explicit, never omitted, matching
    # frame_average.py's/hdr_merge.py's own explicit-value-plus-note
    # provenance fields (e.g. white_level_source, black_note) -- so a
    # reader of session.json never has to guess whether keeping derived
    # outputs was even considered, only infer it from raw_discarded alone.
    correction_status = {
        "flat_correction": flat_status, "dark_correction": dark_status,
        "raw_discarded": raw_discarded,
        "derived_outputs_discarded": False,
        "derived_outputs_note": (
            "Keep RAW Images only ever discards this capture's own raw "
            "frames; averaged/merged intermediates (master_N.tif/"
            "hdr_linear.tif for HDR, single_master.tif for science/snap) "
            "are retained regardless of this setting."),
    }
    if raw_discarded:
        correction_status["raw_discard_reason"] = (
            "Keep RAW Images preference was off; raw frames were deleted "
            "once processing succeeded.")
    print("CORRECTION_STATUS_JSON: " + json.dumps(correction_status))

    # Publish (gallery-race staging design, CHANGELOG.md's 2026-08-05
    # entries): only reached when the caller passed --publish-dir, i.e.
    # capture_dir above was a staging directory, not the real session
    # directory -- every non-staged caller (the manual processing wizard,
    # the archive dialog) leaves publish_dir None and this is a no-op,
    # since those already operate directly on the real session directory
    # and have nothing to move. Per-file os.replace, deliberately never a
    # single os.replace(capture_dir, publish_dir) directory-level rename:
    # a directory rename only succeeds against an EMPTY destination, which
    # holds for a session's first auto-processed capture but not its
    # second (self._session in qt_shell.py is never reset, so a re-Snap or
    # a science reshoot routinely adds another capture into the same
    # already-populated session directory) -- see the CHANGELOG entry for
    # the full reasoning. Runs AFTER retention and AFTER correction_status
    # above, never before: retention has already decided what survives,
    # so this publishes exactly that set, nothing it might promise to
    # remove later. master_files and whichever display/DNG exports were
    # actually written are never subject to Keep RAW Images and always
    # publish if they exist; raw_files (each one's own preview .jpg
    # sidecar included, same pairing the retention loop above uses)
    # publish only when they were not discarded. Each os.replace is
    # same-device-atomic on POSIX and on Windows both -- unlike a
    # directory-level replace, this has no platform limitation to record.
    if publish_dir is not None:
        publish_dir = Path(publish_dir)
        to_publish = list(master_files) + [final_tif, disp, png, jpg]
        if dng_dest is not None:
            to_publish.append(dng_dest)
        if not raw_discarded:
            to_publish += raw_files
            to_publish += [Path(f).with_suffix(".jpg") for f in raw_files]
        for f in to_publish:
            f = Path(f)
            if f.exists():
                os.replace(str(f), str(publish_dir / f.name))

    return disp, correction_status


def archive_raws(capture_dir, ext, mode):
    # Dark frames live one level down, in capture_dir/"dark" (Part 03: nested
    # under their own session rather than flat alongside science/hdr frames,
    # same split process()/frames_for() already follow) -- a plain top-level
    # glob would silently leave them un-archived.
    dark_dir = capture_dir / "dark"
    dngs = sorted(capture_dir.glob("*.{}".format(ext)))
    if dark_dir.is_dir():
        dngs += sorted(dark_dir.glob("*.{}".format(ext)))
    if not dngs:
        print("No .{} files to archive.".format(ext))
        return
    if mode == "keep":
        return
    do = True if mode == "force" else yes_no(
        "\nArchive {} .{} raws to a .tar and remove the loose files? [y/N] "
        .format(len(dngs), ext))
    if not do:
        print("Left {} raws in place.".format(len(dngs)))
        return
    tarpath = capture_dir / "{}_raws.tar".format(capture_dir.name)
    with tarfile.open(str(tarpath), "w") as tf:
        for d in dngs:
            tf.add(str(d), arcname=d.name)
    # only remove after the tar is safely written and re-openable
    with tarfile.open(str(tarpath)) as tf:
        n = len(tf.getnames())
    if n != len(dngs):
        sys.exit("tar verification failed ({} in tar vs {} on disk); kept raws.".format(n, len(dngs)))
    for d in dngs:
        d.unlink()
    mb = tarpath.stat().st_size / 1e6
    print("Archived {} raws -> {} ({:.1f} MB); loose .{} removed.".format(
        len(dngs), tarpath.name, mb, ext))


def main():
    ap = argparse.ArgumentParser(description="Process a capture.py session to a display image.")
    ap.add_argument("session", help="provenance folder (contains session.json)")
    ap.add_argument("--kind", choices=["auto", "hdr", "science", "snap"], default="auto")
    ap.add_argument("--index", type=int, default=None, help="process captures[INDEX]")
    ap.add_argument("--wl", default=MERGE_WHITE_LEVEL_DEFAULT,
                    help="sensor white level / saturation")
    ap.add_argument("--lw", default="2.2", help="Reinhard white point for the HDR path")
    ap.add_argument("--gains", nargs=2, metavar=("RED", "BLUE"), default=None,
                    help="ColourGains white balance (green=1.0)")
    ap.add_argument("--ca", default=None, metavar="CALIB_JSON", help="CA calibration to apply")
    ap.add_argument("--sharpen", default=None, metavar="RADIUS", help="unsharp radius px")
    ap.add_argument("--shadow-deepen", action="store_true")
    ap.add_argument("--raw-ext", default="dng", help="raw frame extension (default dng)")
    ap.add_argument("--capture-dir", default=None,
                    help="gallery-race staging design (CHANGELOG.md's 2026-08-05 "
                         "entries): override where THIS capture's own raw frames "
                         "and outputs are read/written, e.g. a same-device staging "
                         "directory, instead of session[\"capture_dir\"]. Dark/flat "
                         "correction inputs are unaffected -- they always resolve "
                         "from the real session directory, never from this "
                         "override. Validated against frames_for()'s own glob "
                         "before use; must be given together with --publish-dir.")
    ap.add_argument("--publish-dir", default=None,
                    help="with --capture-dir: after processing and retention "
                         "finish in --capture-dir, publish each surviving file "
                         "individually via os.replace into this directory (the "
                         "real, final session directory) -- never a single "
                         "directory-level rename. Must be given together with "
                         "--capture-dir.")
    ap.add_argument("--flat-root", default=str(Path.home() / "flat"),
                    help="flat-field library folder (Part 03: one standing set, "
                         "replaced outright by each new Flat capture, default ~/flat)")
    ap.add_argument("--archive-raws", dest="archive", action="store_const", const="force",
                    default="prompt", help="tar+remove raws without prompting")
    ap.add_argument("--keep-raws", dest="archive", action="store_const", const="keep",
                    help="never archive raws")
    ap.add_argument("--delete-raw-on-success", action="store_true",
                    help="Keep RAW Images off (Part 03): delete this capture's own "
                         "raw frames only once processing succeeds -- never the "
                         "averaged/merged intermediates (master_N.tif/hdr_linear.tif/"
                         "single_master.tif), the shared flat library, or "
                         "final.tif/final_display.*")
    # Additional export formats (Preferences > Advanced, Part 03: lifted from
    # casual_mode.py, then genuinely completed once the debayer.py tonemap/
    # write split landed -- TIFF used to be locked on here because debayer.py
    # had no way to skip it; now it's a real flag like PNG/JPG, all three
    # independent debayer.py write-formats off the same in-memory tone-mapped
    # array, see display_opts()).
    ap.add_argument("--export-tiff", dest="export_tiff", action="store_true", default=True,
                    help="write final_display.tif (default: on)")
    ap.add_argument("--no-export-tiff", dest="export_tiff", action="store_false",
                    help="skip final_display.tif")
    ap.add_argument("--export-png", dest="export_png", action="store_true", default=True,
                    help="write final_display.png (default: on)")
    ap.add_argument("--no-export-png", dest="export_png", action="store_false",
                    help="skip final_display.png")
    ap.add_argument("--export-jpg", action="store_true",
                    help="write final_display.jpg (default: off)")
    ap.add_argument("--export-dng", action="store_true",
                    help="copy a raw-domain deliverable, <file_prefix>raw.<ext>, "
                         "into the session (default: off)")
    ap.add_argument("--export-dng-merge", action="store_true",
                    help="with --export-dng on a Burst/HDR capture: deliver the "
                         "merged raw-domain master (<file_prefix>raw.tif) instead "
                         "of the first untouched raw frame")
    a = ap.parse_args()

    # `session` is the PROVENANCE folder (session.json + sidecars only, no
    # image bytes -- Part 03, provenance relocation). Image bytes live in a
    # separate capture folder, recorded explicitly on session.json's own
    # "capture_dir" field (see provenance.py's Session.write) rather than
    # assumed to sit beside session.json the way it did before Part 03.
    # Falls back to session_dir itself for a session.json predating that
    # field, so an old on-disk session (provenance and images still sharing
    # one folder) keeps working rather than erroring on an absent key.
    session_dir = Path(a.session).resolve()
    sj = session_dir / "session.json"
    if not sj.is_file():
        sys.exit("no session.json in {}".format(session_dir))
    session = json.loads(sj.read_text())
    # CAVEAT: during the gallery-race staging design's staging window
    # (CHANGELOG.md's 2026-08-05 entries), this field is a PROMISE, not a
    # description. It always names the real/final session directory --
    # provenance never moves -- but while a capture is staged (--capture-dir
    # below), its bytes physically live elsewhere, not here yet. Any reader
    # that resolves a path through this field during that window, this one
    # included, gets a path to a file that does not exist yet. Accepted,
    # deliberately, as the same defect class as HANDOFF.md open item 2 at
    # smaller scope -- see the CHANGELOG entry, not engineered around here.
    real_capture_dir = Path(session["capture_dir"]) if "capture_dir" in session else session_dir
    if bool(a.capture_dir) != bool(a.publish_dir):
        sys.exit("--capture-dir and --publish-dir must be given together "
                 "(staging without a publish destination would strand the "
                 "processed files there; a publish destination without a "
                 "staging override has nothing to publish from).")
    if a.capture_dir:
        capture_dir = Path(a.capture_dir).resolve()
        if not capture_dir.is_dir():
            sys.exit("--capture-dir {} does not exist".format(capture_dir))
    else:
        capture_dir = real_capture_dir
    # dark_dir is NEVER the --capture-dir override -- dark captures are a
    # separate, never-staged call site in qt_shell.py's own capture flow
    # (target_dir = session.dir / "dark"), so dark frames physically live
    # under the real session directory even while capture_dir above points
    # at staging.
    dark_dir = real_capture_dir / "dark"
    cap = pick_capture(session, a.kind, a.index)
    if a.capture_dir:
        # Validated, not trusted: a bad override fails loudly here, with
        # the path in the message, rather than silently falling back to
        # session["capture_dir"] and processing the wrong directory --
        # that silent fallback is exactly the failure this flag exists to
        # prevent.
        prefixes = ([lvl.get("file_prefix") for lvl in cap.get("levels", [])]
                   if cap.get("kind") == "hdr" else [cap.get("file_prefix")])
        if not any(frames_for(capture_dir, p, a.raw_ext) for p in prefixes if p):
            sys.exit(
                "--capture-dir {} contains no frames matching {}frame_*.{} "
                "for capture #{}; refusing to fall back to "
                "session[\"capture_dir\"] and process the wrong directory."
                .format(capture_dir,
                       next((p for p in prefixes if p), "<prefix>"),
                       a.raw_ext, cap.get("index")))
    print("Processing capture #{} kind={} note={!r}".format(
        cap.get("index"), cap.get("kind"), cap.get("note", "")))
    publish_dir = Path(a.publish_dir).resolve() if a.publish_dir else None
    process(capture_dir, dark_dir, session, cap, a, a.raw_ext, publish_dir=publish_dir)
    archive_raws(capture_dir, a.raw_ext, a.archive)


if __name__ == "__main__":
    main()
