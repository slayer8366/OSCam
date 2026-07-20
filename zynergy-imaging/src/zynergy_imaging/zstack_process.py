#!/usr/bin/env python3
"""
zstack_process.py — build one or more focus stacks from tagged capture sessions,
ready for Zerene Stacker.

Model: one science capture per session (capture.py clears a prior science shot on
re-shoot), so one session contributes one plane. Each session's science capture
is tagged in capture.py with `tag <stack> <plane>`; this script reads those tags,
validates the stacks, processes each plane through hdr_from_session, and names the
output <stack>_<plane>_final.tif so the plate and depth are legible without a key.

Validation runs FIRST and across ALL sessions, because a missing or duplicated
plane only shows up when the sessions are seen together — capture.py tags one
session at a time and cannot detect a plane that another session already claims.

Usage:
  zstack-process ~/captures/2026-07-01_*            # process every tagged stack
  zstack-process ~/captures/* --stack T4            # only stack T4
  zstack-process ~/captures/* --out-dir ~/zerene/T4 --gains 1.89 1.59 --ca ca.json
  zstack-process ~/captures/* --check              # validate only, process nothing
  zstack-process ~/captures/* --master             # stack the linear masters, not
                                                   # the tonemapped display images
"""

import argparse
import glob
import os
import sys
import subprocess
from pathlib import Path

try:
    from . import stacks
except ImportError:
    import stacks

SCRIPTS = Path(__file__).resolve().parent
HDR_FROM_SESSION = SCRIPTS / "hdr_from_session.py"


def resolve_sessions(patterns):
    """Expand args into session folders, handling absolute paths and ~ (the old
    Path().glob fallback raised on absolute patterns and never expanded ~)."""
    out = []
    for item in patterns:
        p = Path(item).expanduser()
        if p.is_dir():
            out.append(p)
            continue
        matches = glob.glob(os.path.expanduser(item))
        out.extend(Path(m) for m in matches if Path(m).is_dir())
    # de-dup, keep order
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r); uniq.append(p)
    return uniq


def hdr_args(a):
    extra = ["--kind", a.kind]
    if a.wl:            extra += ["--wl", str(a.wl)]
    if a.lw:            extra += ["--lw", str(a.lw)]
    if a.gains:         extra += ["--gains", str(a.gains[0]), str(a.gains[1])]
    if a.ca:            extra += ["--ca", str(a.ca)]
    if a.sharpen:       extra += ["--sharpen", str(a.sharpen)]
    if a.shadow_deepen: extra += ["--shadow-deepen"]
    if a.archive_raws:  extra += ["--archive-raws"]
    return extra


def process_plane(session_dir, stack_id, plane, a):
    """Run hdr_from_session on one session, then move its output to the stack
    folder as <stack>_<plane>_final.tif. Returns the destination path or None."""
    cmd = [sys.executable, str(HDR_FROM_SESSION), str(session_dir)] + hdr_args(a)
    print("\n=== {} plane {}  ({}) ===".format(stack_id, plane, session_dir.name))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("  FAILED:\n" + (result.stderr or "").rstrip())
        return None
    tail = [ln for ln in result.stdout.strip().split("\n") if ln][-3:]
    print("  " + "\n  ".join(tail))

    produced = session_dir / ("final.tif" if a.master else "final_display.tif")
    if not produced.exists():
        print("  WARNING: expected {} not found; nothing moved.".format(produced.name))
        return None
    out_dir = Path(a.out_dir).expanduser() if a.out_dir else session_dir.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / stacks.output_name(stack_id, plane)
    if produced.resolve() != dest.resolve():
        if dest.exists():
            dest.unlink()
        produced.rename(dest)
    print("  -> {}".format(dest))
    return dest


def report_issues(issues):
    errors = [i for i in issues if i.level == "error"]
    for i in issues:
        print("  " + repr(i))
    return errors


def main():
    ap = argparse.ArgumentParser(description="Build tagged focus stacks for Zerene.")
    ap.add_argument("sessions", nargs="+", help="session folders or glob(s)")
    ap.add_argument("--stack", default=None, help="only build this stack id")
    ap.add_argument("--out-dir", default=None,
                    help="where the <stack>_<plane>_final.tif files land "
                         "(default: the sessions' parent folder)")
    ap.add_argument("--master", action="store_true",
                    help="stack the linear measurement masters (final.tif) instead "
                         "of the tonemapped display images (final_display.tif)")
    ap.add_argument("--check", action="store_true",
                    help="validate the stacks and stop; process nothing")
    ap.add_argument("--force", action="store_true",
                    help="process even if validation reports errors")
    # hdr_from_session passthrough
    ap.add_argument("--kind", choices=["auto", "hdr", "science", "snap"], default="science")
    ap.add_argument("--wl", default="65520")
    ap.add_argument("--lw", default="2.2")
    ap.add_argument("--gains", nargs=2, metavar=("RED", "BLUE"), default=None)
    ap.add_argument("--ca", default=None, help="CA calibration JSON")
    ap.add_argument("--sharpen", default=None, metavar="RADIUS")
    ap.add_argument("--shadow-deepen", action="store_true")
    ap.add_argument("--archive-raws", action="store_true")
    a = ap.parse_args()

    sessions = resolve_sessions(a.sessions)
    if not sessions:
        sys.exit("No session folders found.")
    print("Scanning {} session folder(s).".format(len(sessions)))

    # Validate across ALL sessions first (excluded frames included, so a
    # deliberate quality cut is not mistaken for a missing plane).
    issues = stacks.validate_all(sessions)
    groups = stacks.group_by_stack(sessions)              # active-only, for building
    if a.stack:
        groups = {a.stack: groups.get(a.stack, [])}
        issues = [i for i in issues if i.stack == a.stack]

    if not groups or all(not m for m in groups.values()):
        sys.exit("No tagged, active captures found"
                 + (" for stack {!r}.".format(a.stack) if a.stack else "."))

    print("\nStacks found: " + ", ".join(
        "{} ({} planes)".format(s, len(stacks.ordered_planes(m)))
        for s, m in sorted(groups.items()) if m))

    print("\nValidation:")
    errors = report_issues(issues) if issues else (print("  clean") or [])

    if a.check:
        print("\n(--check) validation only; nothing processed.")
        return
    if errors and not a.force:
        sys.exit("\nRefusing to build with {} error(s). Fix the tags, or pass "
                 "--force to build anyway.".format(len(errors)))

    total_ok = 0
    for stack_id, members in sorted(groups.items()):
        planes = stacks.ordered_planes(members)
        for session_dir, cap in planes:
            if process_plane(session_dir, stack_id, stacks.plane_of(cap), a):
                total_ok += 1

    print("\nDone. {} plane(s) written.".format(total_ok))
    print("Ready for Zerene: 'Align & Stack All' with PMax or DMap.")
    if not a.master:
        print("Note: stacked the DISPLAY renders (final_display.tif). Use --master "
              "to stack linear measurement masters instead.")


if __name__ == "__main__":
    main()
