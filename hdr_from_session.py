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

Usage:
  hdr_from_session.py <session_dir> --wl 65520 --lw 2.2 --gains 1.89 1.59 \
      --ca ca.json --sharpen 1.5
  hdr_from_session.py <session_dir> --kind hdr          # process last hdr capture
  hdr_from_session.py <session_dir> --index 3           # process captures[3]
  hdr_from_session.py <session_dir> --archive-raws      # tar+remove DNGs, no prompt
"""
import argparse
import json
import subprocess
import sys
import tarfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
__version__ = "1.0"


def run_tool(name, args, cwd):
    cmd = [sys.executable, str(SCRIPTS / name)] + [str(a) for a in args]
    print("  $ {} {}".format(name, " ".join(str(a) for a in args)))
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("\n{} FAILED:\n{}\n{}".format(name, r.stdout, r.stderr))
    return r


def frames_for(session_dir, file_prefix, ext):
    # prefix + 'frame_*' is exact for that prefix and never catches a longer one
    # (e.g. 'dark_' won't catch 'dark_1_'): every stem is <prefix>frame_<idx>.
    return sorted(session_dir.glob("{}frame_*.{}".format(file_prefix, ext)))


def yes_no(prompt, default_no=True):
    if not sys.stdin.isatty():
        return False
    ans = input(prompt).strip().lower()
    return ans in ("y", "yes")


def display_opts(a):
    """debayer display flags, each included only if supplied."""
    out = []
    if a.ca:
        out += ["--ca-correct", a.ca]
    if a.gains:
        out += ["--colour-gains", a.gains[0], a.gains[1]]
    if a.sharpen is not None:
        out += ["--sharpen", a.sharpen]
    if a.shadow_deepen:
        out += ["--shadow-deepen"]
    out += ["--tonemap-8bit"]
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


def process(session_dir, session, cap, a, ext):
    flat_pre = None
    for c in session.get("captures", []):
        if c.get("kind") == "flat":
            flat_pre = c["file_prefix"]                # last flat wins
    flat = frames_for(session_dir, flat_pre, ext) if flat_pre else []
    ran, skipped = [], []
    if flat:
        ran.append("flat-field ({} frames)".format(len(flat)))
    else:
        skipped.append("flat-field (no flat_ frames)")

    kind = cap["kind"]
    if kind == "hdr":
        masters, times = [], []
        dark_levels = {d["level"]: d["file_prefix"] for d in cap.get("dark_levels", [])}
        for lvl in cap["levels"]:
            L = lvl["level"]
            sci = frames_for(session_dir, lvl["file_prefix"], ext)
            if not sci:
                sys.exit("level {}: no frames {}frame_*.{}".format(L, lvl["file_prefix"], ext))
            fa = [f.name for f in sci] + ["-o", "master_{}.tif".format(L)]
            if flat:
                fa += ["--flat"] + [f.name for f in flat]
            dpre = dark_levels.get(L)
            dfr = frames_for(session_dir, dpre, ext) if dpre else []
            if dfr:
                fa += ["--dark"] + [f.name for f in dfr]
            run_tool("frame_average.py", fa, session_dir)
            masters.append("master_{}.tif".format(L))
            times.append(lvl["actual_s"])
        if any(dark_levels.values()):
            ran.append("dark ({} levels)".format(len(dark_levels)))
        else:
            skipped.append("dark (no dark_ frames)")
        hm = []
        for m, t in zip(masters, times):
            hm += ["-e", m, "{:.6g}".format(t)]
        hm += ["--white-level", a.wl, "-o", "hdr_linear.tif"]
        run_tool("hdr_merge.py", hm, session_dir)
        ran.append("hdr_merge ({} levels, WL {})".format(len(masters), a.wl))
        db = ["hdr_linear.tif", "--rgb", "-o", "final.tif",
              "--tonemap", "reinhard", "--tonemap-white", a.lw] + display_opts(a)
        run_tool("debayer.py", db, session_dir)
        ran.append("debayer (Lw {})".format(a.lw))

    elif kind in ("science", "snap"):
        sci = frames_for(session_dir, cap["file_prefix"], ext)
        if not sci:
            sys.exit("no frames {}frame_*.{}".format(cap["file_prefix"], ext))
        fa = [f.name for f in sci] + ["-o", "single_master.tif"]
        if flat:
            fa += ["--flat"] + [f.name for f in flat]
        dark = frames_for(session_dir, "dark_", ext)          # standalone dark_frame_*
        if dark:
            fa += ["--dark"] + [f.name for f in dark]
            ran.append("dark ({} frames)".format(len(dark)))
        else:
            skipped.append("dark (no standalone dark_ frames)")
        run_tool("frame_average.py", fa, session_dir)
        db = ["single_master.tif", "--rgb", "-o", "final.tif",
              "--assume-linear", a.wl, "--tonemap", "reinhard",
              "--tonemap-white", "1.0"] + display_opts(a)
        run_tool("debayer.py", db, session_dir)
        ran.append("debayer --assume-linear {} (Lw 1.0)".format(a.wl))
    else:
        sys.exit("capture kind {!r} is not processable.".format(kind))

    if not a.ca:
        skipped.append("CA-correct (no --ca)")
    if not a.gains:
        skipped.append("white-balance (no --gains)")

    print("\nStages run:    " + ", ".join(ran))
    print("Stages skipped: " + ", ".join(skipped))
    disp = session_dir / "final_display.tif"
    png = session_dir / "final_display.png"
    print("\nDisplay image: {}{}".format(disp, "  (+ {})".format(png.name) if png.exists() else ""))
    return disp


def archive_raws(session_dir, ext, mode):
    dngs = sorted(session_dir.glob("*.{}".format(ext)))
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
    tarpath = session_dir / "{}_raws.tar".format(session_dir.name)
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
    ap.add_argument("session", help="session folder (contains session.json)")
    ap.add_argument("--kind", choices=["auto", "hdr", "science", "snap"], default="auto")
    ap.add_argument("--index", type=int, default=None, help="process captures[INDEX]")
    ap.add_argument("--wl", default="65520", help="sensor white level / saturation")
    ap.add_argument("--lw", default="2.2", help="Reinhard white point for the HDR path")
    ap.add_argument("--gains", nargs=2, metavar=("RED", "BLUE"), default=None,
                    help="ColourGains white balance (green=1.0)")
    ap.add_argument("--ca", default=None, metavar="CALIB_JSON", help="CA calibration to apply")
    ap.add_argument("--sharpen", default=None, metavar="RADIUS", help="unsharp radius px")
    ap.add_argument("--shadow-deepen", action="store_true")
    ap.add_argument("--raw-ext", default="dng", help="raw frame extension (default dng)")
    ap.add_argument("--archive-raws", dest="archive", action="store_const", const="force",
                    default="prompt", help="tar+remove raws without prompting")
    ap.add_argument("--keep-raws", dest="archive", action="store_const", const="keep",
                    help="never archive raws")
    a = ap.parse_args()

    session_dir = Path(a.session).resolve()
    sj = session_dir / "session.json"
    if not sj.is_file():
        sys.exit("no session.json in {}".format(session_dir))
    session = json.loads(sj.read_text())
    cap = pick_capture(session, a.kind, a.index)
    print("Processing capture #{} kind={} note={!r}".format(
        cap.get("index"), cap.get("kind"), cap.get("note", "")))
    process(session_dir, session, cap, a, a.raw_ext)
    archive_raws(session_dir, a.raw_ext, a.archive)


if __name__ == "__main__":
    main()
