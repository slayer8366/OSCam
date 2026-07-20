#!/usr/bin/env python3
"""
capture.py - reproducible still / burst / HDR capture for the Raspberry Pi HQ
camera (IMX477) on a Pi 5, via Picamera2 (single held pipeline).

The camera is opened once and held: a live preview runs continuously and
full-res captures happen via an in-place mode switch, so the preview never
tears down and there is no device-busy race.

Probe-then-lock: meter once with auto AE/AWB so they converge on the current
illumination, READ the settled shutter / gain / colour-gain values, then LOCK
them so every frame is photometrically identical and reproducible from the
recorded numbers alone. Locked values persist to ~/imx/profile.json.

SESSION MODEL
  Launching capture.py opens ONE session folder, ~/captures/<timestamp>/, and
  every capture lands in it. 'restart' closes the current folder and opens a new
  one WITHOUT quitting (e.g. a new focal plane for a z-stack); 'q' ends the
  program. An empty session folder is removed on restart/quit.

  One session.json per folder logs the locked settings plus a captures[] list;
  each entry records the settings IN EFFECT at that moment, so a mid-session
  'reprobe' is captured honestly rather than rewritten globally.

  File names are always <prefix>frame_<idx>, which keeps globs collision-free
  ('dark_' never catches 'dark_1_'):
      flat_frame_*                  empty-field flat burst (base exposure)
      science_frame_*               single-exposure specimen burst (base)
      1_frame_* ... 5_frame_*       HDR bracket-burst (level 1 = lowest exposure)
      dark_1_frame_* ... dark_5_frame_*   darks matching the HDR levels
      dark_frame_*                  standalone dark burst (pairs with science)
      snap_frame_*                  quick single frame

COMMANDS  (note = free text after the command, recorded in session.json)
  flat  <note>     empty-field burst                          -> flat_frame_*
  science <note>   single-exposure specimen burst             -> science_frame_*  [offer process]
  hdr   <note>     specimen bracket-burst, then prompts darks  -> N_frame_*, dark_N_frame_*  [offer process]
  dark  <note>     standalone dark burst                       -> dark_frame_*
  s     <note>     quick single frame                          -> snap_frame_*     [offer process]
  reprobe          re-meter exposure mid-session (confirm to adopt)
  restart          close this folder, open a new session folder
  q                quit (confirm if the session has captures)

Every burst command prompts "frames (1-10):", hard-capped at 10 to guard the
card against a fat-fingered count. Re-running a command clears its prior frames
first (confirm, default No). Destructive/irreversible prompts all default to No.

PROCESSING
  After s / science / hdr you can process to a finished display image in one
  keypress; this calls the sibling hdr_from_session.py, which runs the right
  chain (per-level average + flat/dark correction -> hdr_merge -> debayer with
  tone-map / white-balance / CA / sharpen) and can tar the raws afterward.
  Which display stages run is controlled by LAUNCH FLAGS:
      capture.py --wl 65520 --lw 2.2 --gains 1.89 1.59 --ca ca.json --sharpen 1.5
  A missing flag => that stage is skipped. Lw is auto-split (HDR uses --lw,
  single-exposure uses 1.0).
"""
import argparse
import datetime as _dt
import json
import subprocess
import sys
from pathlib import Path

try:
    from . import stacks
except ImportError:            # run directly as a script, not as a package module
    import stacks

try:
    from picamera2 import Picamera2, Preview
    from libcamera import controls
    _HAVE_CAMERA = True
except ImportError:                      # allow importing the session layer for tests
    _HAVE_CAMERA = False

# ----------------------------------------------------------------------------
# Defaults - set once for the rig.
# ----------------------------------------------------------------------------
FULL_RES      = (4056, 3040)
PREVIEW_RES   = (1332, 990)
FULL_MODE_LBL = "4056:3040:12:U"
DENOISE       = "off"
SHARPNESS     = "0"
DEFAULT_BURST = 8
MAX_BURST     = 10                       # hard cap per burst (typo guard)
OUT_ROOT      = Path.home() / "captures"
PROFILE_PATH  = Path.home() / "imx" / "profile.json"
DEFAULT_STOPS = [-2.0, -1.0, 0.0, +1.0, +2.0]
PROCESSOR     = Path(__file__).resolve().parent / "hdr_from_session.py"


# ----------------------------------------------------------------------------
# Profile persistence
# ----------------------------------------------------------------------------
def load_profile():
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    return None


def save_profile(locked):
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(locked, indent=2))


# ----------------------------------------------------------------------------
# Camera: probe / lock / exposure settle
# ----------------------------------------------------------------------------
def probe(picam2, settle_s=2.5, timeout_s=8.0):
    import time
    print("[probe] metering with auto AE/AWB ...")
    picam2.set_controls({"AeEnable": True, "AwbEnable": True})
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if picam2.capture_metadata().get("AeLocked", False):
            break
    time.sleep(settle_s)
    md = picam2.capture_metadata()
    cg = md.get("ColourGains", (1.0, 1.0))
    locked = {
        "shutter_us":    int(round(md.get("ExposureTime", 0))),
        "analogue_gain": round(float(md.get("AnalogueGain", 1.0)), 4),
        "awb_red_gain":  round(float(cg[0]), 4),
        "awb_blue_gain": round(float(cg[1]), 4),
    }
    print("[probe] locked: shutter={}us gain={} awbgains={},{}".format(
        locked["shutter_us"], locked["analogue_gain"],
        locked["awb_red_gain"], locked["awb_blue_gain"]))
    return locked


def apply_lock(picam2, locked):
    picam2.set_controls({
        "AeEnable": False,
        "AwbEnable": False,
        "ExposureTime": locked["shutter_us"],
        "AnalogueGain": locked["analogue_gain"],
        "ColourGains": (locked["awb_red_gain"], locked["awb_blue_gain"]),
        "Sharpness": 0.0,
        "NoiseReductionMode": controls.draft.NoiseReductionModeEnum.Off,
    })


def wait_for_exposure(picam2, target_us, tol=0.05, max_frames=12):
    actual = 0
    for _ in range(max_frames):
        actual = picam2.capture_metadata().get("ExposureTime", 0)
        if target_us and abs(actual - target_us) <= tol * target_us:
            break
    return actual


# ----------------------------------------------------------------------------
# Disk writes
# ----------------------------------------------------------------------------
def _dump_meta(path, md):
    def _j(o):
        try:
            json.dumps(o); return o
        except TypeError:
            return str(o)
    path.write_text(json.dumps({k: _j(v) for k, v in md.items()}, indent=2))


def _save_request(request, session_dir, idx, prefix=""):
    stem = "{}frame_{:04d}".format(prefix, idx)
    try:
        request.save("main", str(session_dir / (stem + ".jpg")))
        request.save_dng(str(session_dir / (stem + ".dng")))
        md = request.get_metadata()
    finally:
        request.release()
    _dump_meta(session_dir / (stem + ".meta.json"), md)
    print("    wrote {}.dng / .jpg / .meta.json".format(stem))


# ----------------------------------------------------------------------------
# Session state (no camera dependency: unit-testable)
# ----------------------------------------------------------------------------
def new_session_dir(root):
    root = Path(root)
    ts = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    d = root / ts
    n = 1
    while d.exists():                    # avoid collision within the same second
        d = root / "{}_{}".format(ts, n); n += 1
    d.mkdir(parents=True)
    return d.name, d


class Session:
    def __init__(self, root, locked, display_flags):
        self.root = Path(root)
        self.locked = dict(locked)
        self.display_flags = list(display_flags)
        self.ts, self.dir = new_session_dir(root)
        self.captures = []
        self.write()

    def write(self):
        payload = {
            "session_timestamp": self.ts,
            "tool": "picamera2",
            "mode": FULL_MODE_LBL,
            "denoise": DENOISE,
            "sharpness": SHARPNESS,
            "display_flags": self.display_flags,
            "captures": self.captures,
        }
        (self.dir / "session.json").write_text(json.dumps(payload, indent=2))

    def existing(self, prefixes):
        """Files already present for any of these prefixes."""
        hits = []
        for p in prefixes:
            hits += list(self.dir.glob("{}frame_*".format(p)))
        return hits

    def clear(self, prefixes, kinds):
        """Remove files for these prefixes and any capture entries of these kinds.
        Returns the number of files removed."""
        removed = 0
        for f in self.existing(prefixes):
            f.unlink(); removed += 1
        self.captures = [c for c in self.captures if c.get("kind") not in kinds]
        self._reindex()
        self.write()
        return removed

    def _reindex(self):
        for i, c in enumerate(self.captures):
            c["index"] = i

    def record(self, entry):
        entry["timestamp"] = _dt.datetime.now().astimezone().isoformat()
        entry["locked_settings"] = dict(self.locked)     # snapshot in effect now
        self.captures.append(entry)
        self._reindex()
        self.write()
        return entry["index"]

    def has_captures(self):
        return len(self.captures) > 0

    def close(self):
        """Remove the folder iff nothing was captured (only session.json on disk).
        If unexpected files are present, keep the folder to avoid data loss."""
        if self.has_captures():
            return False
        others = [p for p in self.dir.iterdir() if p.name != "session.json"]
        if others:
            return False
        (self.dir / "session.json").unlink()
        self.dir.rmdir()
        return True


# ----------------------------------------------------------------------------
# Small interactive helpers (testable with monkeypatched input)
# ----------------------------------------------------------------------------
def ask_frames(cap=MAX_BURST):
    raw = input("  frames (1-{}): ".format(cap)).strip()
    try:
        n = int(raw)
    except ValueError:
        print("  not a number - using 1."); return 1
    if n < 1:
        print("  min 1."); return 1
    if n > cap:
        print("  capped at {}.".format(cap)); return cap
    return n


def yes_no(prompt):
    """Default No. Non-interactive stdin => No (never blocks a script)."""
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    return input(prompt).strip().lower() in ("y", "yes")


# ----------------------------------------------------------------------------
# Camera capture primitives
# ----------------------------------------------------------------------------
def do_burst(picam2, still_cfg, preview_cfg, locked, n, session_dir, prefix):
    base = locked["shutter_us"]
    picam2.switch_mode(still_cfg)
    try:
        picam2.set_controls({"ExposureTime": base})
        actual = wait_for_exposure(picam2, base)
        for i in range(n):
            _save_request(picam2.capture_request(), session_dir, i, prefix=prefix)
    finally:
        picam2.switch_mode(preview_cfg)
    return actual


def bracket_burst_phase(picam2, base_us, ordered_stops, n, session_dir, name_prefix):
    """n frames at EACH exposure level (still mode already active). Files named
    <name_prefix><level>_frame_<idx>. Returns per-level records with actuals."""
    levels = []
    for level, ev in enumerate(ordered_stops, start=1):
        target = int(round(base_us * (2.0 ** ev)))
        picam2.set_controls({"ExposureTime": target})
        actual = wait_for_exposure(picam2, target)
        prefix = "{}{}_".format(name_prefix, level)
        print("  level {} (ev {:+g}): {}us x{} [{}*]".format(level, ev, actual, n, prefix))
        for i in range(n):
            _save_request(picam2.capture_request(), session_dir, i, prefix=prefix)
        levels.append({"level": level, "ev": ev, "file_prefix": prefix,
                       "requested_us": target, "actual_us": actual,
                       "actual_s": (actual / 1e6) if actual else None,
                       "frame_count": n})
    return levels


# ----------------------------------------------------------------------------
# Command handlers
# ----------------------------------------------------------------------------
def _guard_reshoot(session, prefixes, kinds, label):
    hits = session.existing(prefixes)
    if hits and not yes_no("  Clear {} existing {} frame(s) and re-shoot? [y/N] "
                           .format(len(hits), label)):
        print("  kept - command cancelled.")
        return False
    if hits:
        session.clear(prefixes, kinds)
    return True


def do_flat(picam2, cfgs, session, note):
    if not _guard_reshoot(session, ["flat_"], {"flat"}, "flat"):
        return
    n = ask_frames()
    print("  [flat] empty field, illuminator ON, ~60-70% and unclipped.")
    actual = do_burst(picam2, cfgs[0], cfgs[1], session.locked, n, session.dir, "flat_")
    session.record({"kind": "flat", "note": note, "file_prefix": "flat_",
                    "frame_count": n, "requested_us": session.locked["shutter_us"],
                    "actual_us": actual, "actual_s": (actual / 1e6) if actual else None})
    print("  [flat] {} frames.".format(n))


def do_tag(session, arg):
    """tag <stack> <plane> — attach stack membership and depth to this session's
    science capture. One science capture per session, so this tags that capture.
    Cross-session duplicate/gap detection happens at batch time (zstack-process
    can see all sessions; this command can only see the current one).
    """
    parts = arg.split()
    if len(parts) != 2:
        print("  usage: tag <stack> <plane>   e.g.  tag T4 3")
        return
    stack_id, plane_str = parts[0], parts[1]
    try:
        plane = int(plane_str)
    except ValueError:
        print("  plane must be an integer (got {!r}).".format(plane_str))
        return
    sci = [i for i, c in enumerate(session.captures) if c.get("kind") == "science"]
    if not sci:
        print("  no science capture in this session yet — capture one first, then tag.")
        return
    pos = sci[-1]
    try:
        stacks.apply_tag(session.captures, pos, stack_id, plane)
    except ValueError as e:
        print("  " + str(e))
        return
    session.write()
    print("  [tag] science capture -> stack {!r}, plane {}  (output will be {})"
          .format(stack_id, plane, stacks.output_name(stack_id, plane)))


def do_science(picam2, cfgs, session, note):
    if not _guard_reshoot(session, ["science_"], {"science"}, "science"):
        return
    n = ask_frames()
    actual = do_burst(picam2, cfgs[0], cfgs[1], session.locked, n, session.dir, "science_")
    idx = session.record({"kind": "science", "note": note, "file_prefix": "science_",
                          "frame_count": n, "requested_us": session.locked["shutter_us"],
                          "actual_us": actual, "actual_s": (actual / 1e6) if actual else None})
    print("  [science] {} frames.".format(n))
    offer_process(session, "science", idx)


def do_hdr(picam2, cfgs, session, note):
    ordered = sorted(DEFAULT_STOPS)
    sci_pre = ["{}_".format(i) for i in range(1, len(ordered) + 1)]
    dark_pre = ["dark_{}_".format(i) for i in range(1, len(ordered) + 1)]
    if not _guard_reshoot(session, sci_pre + dark_pre, {"hdr"}, "HDR"):
        return
    print("  [hdr] specimen bracket ({} levels).".format(len(ordered)))
    n = ask_frames()
    picam2.switch_mode(cfgs[0])
    try:
        sci_levels = bracket_burst_phase(picam2, session.locked["shutter_us"],
                                         ordered, n, session.dir, "")
        print("  [hdr] kill the illuminator + block ambient for darks.")
        nd = ask_frames()
        dark_levels = bracket_burst_phase(picam2, session.locked["shutter_us"],
                                          ordered, nd, session.dir, "dark_")
    finally:
        picam2.set_controls({"ExposureTime": session.locked["shutter_us"]})
        picam2.switch_mode(cfgs[1])
    idx = session.record({"kind": "hdr", "note": note,
                          "levels": sci_levels, "dark_levels": dark_levels})
    print("  [hdr] {} science + {} dark frames across {} levels."
          .format(n * len(ordered), nd * len(ordered), len(ordered)))
    offer_process(session, "hdr", idx)


def do_dark(picam2, cfgs, session, note):
    if not _guard_reshoot(session, ["dark_"], {"dark"}, "standalone dark"):
        return
    n = ask_frames()
    print("  [dark] illuminator OFF, no ambient leak (verify the raw floor).")
    actual = do_burst(picam2, cfgs[0], cfgs[1], session.locked, n, session.dir, "dark_")
    session.record({"kind": "dark", "note": note, "file_prefix": "dark_",
                    "frame_count": n, "requested_us": session.locked["shutter_us"],
                    "actual_us": actual, "actual_s": (actual / 1e6) if actual else None})
    print("  [dark] {} frames.".format(n))


def do_snap(picam2, cfgs, session, note):
    if not _guard_reshoot(session, ["snap_"], {"snap"}, "snap"):
        return
    actual = do_burst(picam2, cfgs[0], cfgs[1], session.locked, 1, session.dir, "snap_")
    idx = session.record({"kind": "snap", "note": note, "file_prefix": "snap_",
                          "frame_count": 1, "requested_us": session.locked["shutter_us"],
                          "actual_us": actual, "actual_s": (actual / 1e6) if actual else None})
    print("  [snap] 1 frame.")
    offer_process(session, "snap", idx)


def do_reprobe(picam2, preview_cfg, session):
    old = dict(session.locked)
    picam2.switch_mode(preview_cfg)
    new = probe(picam2)
    print("  old: shutter={}us gain={}  ->  new: shutter={}us gain={}".format(
        old["shutter_us"], old["analogue_gain"], new["shutter_us"], new["analogue_gain"]))
    if yes_no("  Adopt new exposure for subsequent captures? [y/N] "):
        session.locked = new
        apply_lock(picam2, new)
        save_profile(new)
        print("  adopted (and saved to profile).")
    else:
        apply_lock(picam2, old)
        print("  kept previous exposure.")


def do_restart(session):
    """Close the current folder (removing it if empty) and open a new one,
    carrying the locked settings forward. Returns the new Session."""
    if session.has_captures() and not yes_no(
            "  Start a NEW session folder? Current ({}, {} captures) is kept. [y/N] "
            .format(session.dir.name, len(session.captures))):
        print("  continuing current session.")
        return session
    if session.close():
        print("  (removed empty {})".format(session.dir.name))
    nxt = Session(session.root, session.locked, session.display_flags)
    print("  new session: {}".format(nxt.dir))
    return nxt


def offer_process(session, kind, index):
    if not yes_no("  Process capture #{} to a display image now? [y/N] ".format(index)):
        return
    if not PROCESSOR.exists():
        print("  (hdr_from_session.py not found beside capture.py; skipped.)")
        return
    cmd = [sys.executable, str(PROCESSOR), str(session.dir),
           "--kind", kind, "--index", str(index)] + session.display_flags
    print("  -> processing ...")
    subprocess.run(cmd)


# ----------------------------------------------------------------------------
# Launch flags -> display-flag list passed through to hdr_from_session.py
# ----------------------------------------------------------------------------
def build_display_flags(args):
    flags = ["--wl", str(args.wl), "--lw", str(args.lw)]
    if args.gains:
        flags += ["--gains", str(args.gains[0]), str(args.gains[1])]
    if args.ca:
        flags += ["--ca", str(Path(args.ca).resolve())]     # absolutise: processor runs in the session dir
    if args.sharpen is not None:
        flags += ["--sharpen", str(args.sharpen)]
    if args.shadow_deepen:
        flags += ["--shadow-deepen"]
    if args.archive_raws:
        flags += ["--archive-raws"]
    return flags


def parse_args():
    ap = argparse.ArgumentParser(description="Reproducible IMX477 preview + capture.")
    ap.add_argument("--out", type=Path, default=OUT_ROOT)
    ap.add_argument("--reprobe", action="store_true",
                    help="re-meter AE/AWB at launch and overwrite the saved profile")
    ap.add_argument("--shutter", type=int, default=None)
    ap.add_argument("--gain", type=float, default=None)
    ap.add_argument("--awbgains", default=None, metavar="R,B")
    ap.add_argument("--preview", choices=["qtgl", "qt", "drm", "null"], default="qtgl")
    # display params forwarded to hdr_from_session.py on a process offer
    ap.add_argument("--wl", default=65520, help="sensor white level for processing")
    ap.add_argument("--lw", default=2.2, help="Reinhard white point for the HDR path")
    ap.add_argument("--gains", nargs=2, metavar=("RED", "BLUE"), default=None,
                    help="ColourGains white balance for processing")
    ap.add_argument("--ca", default=None, metavar="CALIB_JSON")
    ap.add_argument("--sharpen", default=None, metavar="RADIUS")
    ap.add_argument("--shadow-deepen", action="store_true")
    ap.add_argument("--archive-raws", action="store_true",
                    help="tar+remove raws after a process offer (no prompt)")
    return ap.parse_args()


def resolve_lock(args, picam2):
    if args.shutter is not None and args.gain is not None:
        r, b = (args.awbgains.split(",") if args.awbgains else ("1.0", "1.0"))
        locked = {"shutter_us": args.shutter, "analogue_gain": round(args.gain, 4),
                  "awb_red_gain": round(float(r), 4), "awb_blue_gain": round(float(b), 4)}
        print("[lock] explicit."); return locked, "explicit"
    if not args.reprobe:
        prof = load_profile()
        if prof is not None:
            print("[profile] loaded {}".format(PROFILE_PATH)); return prof, "profile"
    locked = probe(picam2)
    save_profile(locked)
    print("[profile] saved to {}".format(PROFILE_PATH))
    return locked, "probe"


MENU = ("commands: flat  science  hdr  dark  s  tag  reprobe  restart  q"
        "   (frames capped at {})".format(MAX_BURST))


def main():
    args = parse_args()
    if not _HAVE_CAMERA:
        sys.exit("picamera2 not available - this must run on the Pi.")
    picam2 = Picamera2()
    preview_cfg = picam2.create_preview_configuration(main={"size": PREVIEW_RES}, buffer_count=4)
    still_cfg = picam2.create_still_configuration(main={"size": FULL_RES},
                                                  raw={"size": FULL_RES}, buffer_count=2)
    picam2.configure(preview_cfg)
    backend = {"qtgl": Preview.QTGL, "qt": Preview.QT,
               "drm": Preview.DRM, "null": Preview.NULL}[args.preview]
    picam2.start_preview(backend)
    picam2.start()

    locked, source = resolve_lock(args, picam2)
    apply_lock(picam2, locked)
    display_flags = build_display_flags(args)
    cfgs = (still_cfg, preview_cfg)

    session = Session(args.out, locked, display_flags)
    print("\n[session] {}  (source: {})".format(session.dir, source))
    print(MENU)

    try:
        while True:
            try:
                line = input("\ncapture> ").strip()
            except EOFError:
                line = "q"
            if not line:
                continue
            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            note = parts[1] if len(parts) > 1 else ""

            if cmd in ("q", "quit"):
                if session.has_captures() and not yes_no(
                        "  End session ({} captures)? [y/N] ".format(len(session.captures))):
                    continue
                if session.close():
                    print("  (removed empty {})".format(session.dir.name))
                break
            elif cmd == "flat":
                do_flat(picam2, cfgs, session, note)
            elif cmd == "science":
                do_science(picam2, cfgs, session, note)
            elif cmd == "hdr":
                do_hdr(picam2, cfgs, session, note)
            elif cmd == "dark":
                do_dark(picam2, cfgs, session, note)
            elif cmd == "s":
                do_snap(picam2, cfgs, session, note)
            elif cmd == "reprobe":
                do_reprobe(picam2, preview_cfg, session)
            elif cmd == "tag":
                do_tag(session, note)
            elif cmd == "restart":
                session = do_restart(session)
                print(MENU)
            else:
                print("  ? unknown command.  " + MENU)
    finally:
        picam2.stop()
        picam2.stop_preview()
        picam2.close()
    print("[done]")


if __name__ == "__main__":
    main()
