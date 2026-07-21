"""
stacks.py — focus-stack tagging, validation, ordering, and soft-discard.

Shared by capture.py (writes tags, handles declared retakes) and
zstack_process.py (reads tags, orders planes, validates before Zerene).

Everything here is camera-independent and unit-testable. The only thing that
lives outside this module is the physical frame capture in capture.py.

TAG SCHEMA — fields added to a capture entry in session.json:

    stack    str    PARSED    stack membership. Absent  => not part of any
                              stack (the filter). This is what a session
                              folder cannot infer on its own, so it is
                              asserted at capture time, per capture.
    plane    int    PARSED    depth position within the stack. Named 'plane',
                              NOT 'index', because capture.py already uses
                              'index' for a capture's position in captures[].
                              Integer so ordering never falls back to lexical
                              (where plane_10 sorts before plane_2).
    exclude  bool   PARSED    quality veto. Keeps a frame's stack intent on
                              record while dropping it from the built stack —
                              a blurred dud stays documented as "was meant to
                              be here, cut for quality", not silently deleted.
    note     str    IGNORED   free text. capture.py already records this. The
                              stack logic never parses it: prose in a parsed
                              field is exactly where ordering breaks, so
                              unstructured judgement gets a home a human reads
                              and a parser leaves alone.
    sharpness_score  float  PARSED (optional, section 13's post-capture QC)
                              variance-of-Laplacian scored on this capture's
                              own green plane, once, right after the shutter
                              fires (qt_shell.py, via focus.score_capture_
                              sharpness) -- distinct from the live focus
                              aid's own number, which never touches an actual
                              captured frame. None if scoring failed. Purely
                              informational: see sharpness_relative_flag
                              below for how it gets compared against a
                              plane's stack siblings; nothing here ever sets
                              `exclude` automatically -- that stays a human
                              decision, same as every other exclude toggle.

Three of the five are read for filtering/ordering/QC (stack, plane,
sharpness_score); exclude is a fourth parsed field; note is never parsed.
Nothing here is inferred from pixels except sharpness_score itself, which
IS a pixel-derived number -- everything else in this module reads only the
tags a capture already carries.

INVARIANT (live session): at most one non-excluded capture per (stack, plane).
A declared retake enforces this by moving the loser to a hidden discarded
folder rather than leaving two frames on the same plane. That keeps the
duplicate-plane check a hard error with no "unless excluded" exception.
"""

from __future__ import annotations

import json
from pathlib import Path


STACK = "stack"
PLANE = "plane"
EXCLUDE = "exclude"
NOTE = "note"

DISCARDED_DIRNAME = "discarded"   # hidden via a leading dot on disk: .discarded


# --------------------------------------------------------------------------
# Reading tags
# --------------------------------------------------------------------------

def is_tagged(cap: dict) -> bool:
    """A capture is part of a stack iff it carries a non-empty `stack`."""
    return bool(cap.get(STACK))


def is_active(cap: dict) -> bool:
    """Tagged and not vetoed for quality. These are the frames that build."""
    return is_tagged(cap) and not cap.get(EXCLUDE, False)


def plane_of(cap: dict):
    """Integer plane, or None if missing/unparseable. Never guesses."""
    p = cap.get(PLANE)
    if p is None:
        return None
    try:
        return int(p)
    except (TypeError, ValueError):
        return None


def load_session(session_dir) -> dict:
    """Read a session.json. Returns {} if absent (caller decides what that means)."""
    sj = Path(session_dir) / "session.json"
    if not sj.exists():
        return {}
    return json.loads(sj.read_text())


def captures_of(session: dict) -> list:
    return session.get("captures", [])


# --------------------------------------------------------------------------
# Grouping across sessions
# --------------------------------------------------------------------------

def group_by_stack(session_dirs, include_excluded: bool = False):
    """Walk sessions, return {stack_id: [(session_dir, capture), ...]}.

    By default includes only ACTIVE (tagged, non-excluded) captures — the ones
    that build. Pass include_excluded=True to also pull in excluded-but-tagged
    captures; validation needs those to tell a deliberate quality cut from a
    plane that was never tagged at all. Untagged captures are never included."""
    groups: dict[str, list] = {}
    for sd in session_dirs:
        sd = Path(sd)
        for cap in captures_of(load_session(sd)):
            keep = is_active(cap) or (include_excluded and is_tagged(cap))
            if keep:
                groups.setdefault(cap[STACK], []).append((sd, cap))
    return groups


# --------------------------------------------------------------------------
# Validation — the payoff of a structured schema
# --------------------------------------------------------------------------

class Issue:
    """A single validation finding. level is 'error' or 'warning'."""
    def __init__(self, level: str, stack: str, message: str):
        self.level = level
        self.stack = stack
        self.message = message

    def __repr__(self):
        return "[{}] {}: {}".format(self.level.upper(), self.stack, self.message)


def validate_stack(stack_id: str, members: list) -> list:
    """Check one stack. members should include BOTH active and excluded tagged
    captures (call group_by_stack(..., include_excluded=True)), because an
    excluded plane is documented, not missing, and must not raise a gap error.

    Findings:
      * duplicate plane   — two ACTIVE frames on one depth            (error)
      * missing plane     — a hole in min..max absent from BOTH the   (error)
                            active and excluded records: a plane that
                            was likely captured but never tagged
      * no parseable plane — tagged for the stack but no depth         (warning)
      * single-member     — a one-plane active stack                  (warning)
    A deliberate quality cut (excluded) fills its slot, so it never reads as a
    gap; that is the whole point of keeping exclude on record. Clean -> [].
    """
    issues = []
    active_planes, excluded_planes, missing = [], [], 0
    for _sd, cap in members:
        p = plane_of(cap)
        excl = cap.get(EXCLUDE, False)
        if p is None:
            if not excl:
                missing += 1
        elif excl:
            excluded_planes.append(p)
        else:
            active_planes.append(p)

    if missing:
        issues.append(Issue("warning", stack_id,
            "{} active capture(s) tagged for this stack have no parseable "
            "'plane' and will be skipped in ordering".format(missing)))

    # duplicates — active-vs-active only. An active frame coexisting with an
    # excluded frame on the same plane is fine: the excluded one is history.
    seen = {}
    for p in active_planes:
        seen[p] = seen.get(p, 0) + 1
    for p in sorted(k for k, n in seen.items() if n > 1):
        issues.append(Issue("error", stack_id,
            "plane {} claimed by {} active captures (expected 1). A retake "
            "should have moved the loser to discarded".format(p, seen[p])))

    # gaps — a plane is missing only if absent from BOTH active and excluded.
    accounted = set(active_planes) | set(excluded_planes)
    if active_planes:
        lo, hi = min(active_planes), max(active_planes)
        gaps = [p for p in range(lo, hi + 1) if p not in accounted]
        if gaps:
            issues.append(Issue("error", stack_id,
                "missing plane(s) {} in the range {}..{} — a plane may have "
                "been captured but never tagged".format(gaps, lo, hi)))

    # singleton (count active planes only)
    if len(active_planes) == 1 and not any(i.level == "error" for i in issues):
        issues.append(Issue("warning", stack_id,
            "stack has a single active plane ({}); a one-plane stack is "
            "usually a tagging slip".format(active_planes[0])))

    return issues


def validate_all(session_dirs) -> list:
    """Validate every stack found across the given sessions. Pulls excluded
    frames in so deliberate cuts are not mistaken for missing planes."""
    groups = group_by_stack(session_dirs, include_excluded=True)
    issues = []
    for stack_id, members in sorted(groups.items()):
        issues.extend(validate_stack(stack_id, members))
    return issues


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------

def ordered_planes(members: list) -> list:
    """Return active members with a parseable plane, sorted by plane ascending.
    Members without a plane are dropped here (validate_stack warns about them
    separately). Ordering is by the integer plane only — folder/name order is
    never trusted, which is the whole reason plane is an int.
    """
    withp = [(plane_of(cap), sd, cap) for sd, cap in members if plane_of(cap) is not None]
    withp.sort(key=lambda t: t[0])
    return [(sd, cap) for _p, sd, cap in withp]


def output_name(stack_id: str, plane: int, suffix: str = "_final.tif") -> str:
    """Zerene-ready filename that states plate and depth without a legend:
    T4, plane 3  ->  'T4_03_final.tif'. Inherits your existing stack notation
    instead of a generic 'plane_003'."""
    return "{}_{:02d}{}".format(stack_id, int(plane), suffix)


# --------------------------------------------------------------------------
# Tagging (capture-side bookkeeping; no camera)
# --------------------------------------------------------------------------

def find_holder(captures: list, stack_id: str, plane: int):
    """Return the ACTIVE capture currently occupying (stack, plane), or None.
    Used by a declared retake to know whose slot is being contested."""
    for cap in captures:
        if is_active(cap) and cap.get(STACK) == stack_id and plane_of(cap) == plane:
            return cap
    return None


def slot_taken(captures: list, stack_id: str, plane: int) -> bool:
    return find_holder(captures, stack_id, plane) is not None


def apply_tag(captures: list, position: int, stack_id: str, plane: int) -> dict:
    """Tag the capture at list position `position` with (stack, plane).

    Refuses (raises ValueError) if that (stack, plane) is already held by a
    different active capture. Duplicates are meant to arrive only through a
    declared retake, never through a plain tag — so a mistyped plane becomes a
    refusal here, not a silent overwrite. That is what keeps tagging typo-safe.
    """
    if not (0 <= position < len(captures)):
        raise IndexError("capture position {} out of range".format(position))
    holder = find_holder(captures, stack_id, plane)
    if holder is not None and holder is not captures[position]:
        raise ValueError(
            "plane {} of stack {!r} is already held by capture index {}. "
            "Use retake to contest it.".format(plane, stack_id, holder.get("index")))
    cap = captures[position]
    cap[STACK] = stack_id
    cap[PLANE] = int(plane)
    cap.pop(EXCLUDE, None)   # tagging a frame clears any prior veto
    return cap


def find_tagged(captures: list, stack_id: str, plane: int):
    """Return the capture tagged (stack_id, plane), ACTIVE OR EXCLUDED, or
    None. Unlike find_holder (active-only, used to check a retake collision),
    this is used to locate a capture for QC review regardless of its current
    exclude status -- an excluded plane must still be findable, or a human
    could never toggle it back."""
    for cap in captures:
        if is_tagged(cap) and cap.get(STACK) == stack_id and plane_of(cap) == plane:
            return cap
    return None


# --------------------------------------------------------------------------
# Post-capture QC (section 13): a recorded sharpness_score is evidence, never
# a gate -- set_exclude is the one thing that actually changes what a stack
# builds from, and it is always a deliberate, reversible human action.
# --------------------------------------------------------------------------

def set_exclude(cap: dict, excluded: bool) -> dict:
    """Toggle the quality veto on ONE capture, in place. excluded=False
    clears the field entirely rather than writing `exclude: false` --
    is_active's own check (`not cap.get(EXCLUDE, False)`) already treats
    absent and False identically, so this keeps a never-excluded capture's
    record free of a flag that was never actually set."""
    if excluded:
        cap[EXCLUDE] = True
    else:
        cap.pop(EXCLUDE, None)
    return cap


def sharpness_relative_flag(score, best_score, rel_drop: float = 0.5):
    """Whether `score` is soft enough, relative to the best score seen
    elsewhere in its own stack, to be worth a human's attention -- evidence
    only, same "recorded honestly, never a gate" rule ca_measure.py's own
    poly2_flag already follows for CA curvature. Returns None (no verdict,
    not a crash) if either score is missing or best_score is not positive --
    a plane that was never scored, or a stack with no scores at all yet,
    has nothing meaningful to compare against."""
    if score is None or best_score is None or best_score <= 0:
        return None
    return score < rel_drop * best_score


def discarded_dir(session_dir) -> Path:
    """Hidden per-session discard folder: <session>/.discarded"""
    return Path(session_dir) / ("." + DISCARDED_DIRNAME)


def move_frames_to_discarded(session_dir, file_prefix: str,
                             exts=("dng", "tif", "tiff", "png", "jpg")) -> list:
    """Soft-discard: move every frame whose name starts with file_prefix into
    the hidden .discarded folder. Nothing is deleted; the frames (and the
    capture's tags in session.json) survive so the user decides their fate
    later. Returns the list of destination paths moved.

    Consistent with the pipeline's read-only-source ethos: a discarded frame is
    set aside, never destroyed.
    """
    session_dir = Path(session_dir)
    dest = discarded_dir(session_dir)
    moved = []
    for f in sorted(session_dir.iterdir()):
        if not f.is_file():
            continue
        if f.name.startswith(file_prefix) and f.suffix.lower().lstrip(".") in exts:
            dest.mkdir(exist_ok=True)
            target = dest / f.name
            f.rename(target)
            moved.append(target)
    return moved


def render_check():
    # find_tagged: locates a capture regardless of active/excluded status,
    # unlike find_holder (which is deliberately active-only, for retake
    # collision checks). Built via apply_tag + set_exclude, the same two
    # functions a real capture would go through.
    captures = [{"index": 0}, {"index": 1}]
    apply_tag(captures, 0, "T1", 1)
    assert find_tagged(captures, "T1", 1) is captures[0]
    assert find_tagged(captures, "T1", 99) is None, \
        "a plane that was never tagged should not resolve to anything"
    assert find_holder(captures, "T1", 1) is captures[0], \
        "an active tagged capture should still resolve via find_holder too"

    set_exclude(captures[0], True)
    assert captures[0][EXCLUDE] is True
    assert not is_active(captures[0]), "an excluded capture must not read as active"
    assert find_holder(captures, "T1", 1) is None, \
        "find_holder must NOT resolve an excluded capture (retakes should be " \
        "able to reclaim its slot)"
    assert find_tagged(captures, "T1", 1) is captures[0], \
        "find_tagged MUST still resolve an excluded capture -- otherwise a " \
        "human could never review or un-exclude it"

    set_exclude(captures[0], False)
    assert EXCLUDE not in captures[0], \
        "clearing exclude should remove the key entirely, not just set it False"
    assert is_active(captures[0])
    print("find_tagged / set_exclude check PASS: locates a capture regardless "
          "of exclude status (unlike find_holder, active-only on purpose), "
          "clearing exclude removes the key rather than leaving a stale False")

    # sharpness_relative_flag: evidence only, never raises, honest about
    # what it can't judge (missing scores, a degenerate all-zero stack).
    assert sharpness_relative_flag(50.0, 200.0, rel_drop=0.5) is True, \
        "well below half the stack's best should flag"
    assert sharpness_relative_flag(150.0, 200.0, rel_drop=0.5) is False, \
        "comfortably above half the stack's best should not flag"
    assert sharpness_relative_flag(None, 200.0) is None, "no score -> no verdict"
    assert sharpness_relative_flag(50.0, None) is None, "no stack best -> no verdict"
    assert sharpness_relative_flag(50.0, 0.0) is None, \
        "a degenerate (zero) best score has nothing meaningful to compare against"
    print("sharpness_relative_flag check PASS: flags a real relative drop, stays "
          "quiet on a comfortable score, returns None (never raises) when either "
          "input can't support a verdict")

    # Integration: group_by_stack's include_excluded=True must surface an
    # excluded-but-tagged capture alongside active ones, ordered_planes must
    # still place it correctly by its own plane tag -- this is exactly what
    # measure.py's collect_stack_planes relies on to show excluded planes in
    # the filmstrip rather than hiding them outright.
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    sessions = []
    for i, (plane, excluded) in enumerate([(1, False), (2, True), (3, False)]):
        sd = tmp / "s{}".format(i)
        sd.mkdir()
        cap = {"index": 0, "kind": "science"}
        apply_tag([cap], 0, "T9", plane)
        if excluded:
            set_exclude(cap, True)
        (sd / "session.json").write_text(json.dumps({"captures": [cap]}))
        sessions.append(sd)

    groups_active_only = group_by_stack(sessions)
    assert len(groups_active_only["T9"]) == 2, \
        "default group_by_stack should surface only the 2 active planes"

    groups_all = group_by_stack(sessions, include_excluded=True)
    assert len(groups_all["T9"]) == 3, \
        "include_excluded=True should surface all 3 tagged planes"
    ordered = ordered_planes(groups_all["T9"])
    assert [plane_of(cap) for _sd, cap in ordered] == [1, 2, 3], \
        "ordered_planes must place the excluded plane 2 correctly by its " \
        "own tag, not drop it or push it to the end"
    excluded_flags = [is_active(cap) for _sd, cap in ordered]
    assert excluded_flags == [True, False, True], \
        "plane 2's excluded status must survive the group_by_stack -> " \
        "ordered_planes round-trip"
    print("group_by_stack/ordered_planes + exclude integration check PASS: "
          "an excluded-but-tagged plane is invisible by default, surfaces "
          "with include_excluded=True, and orders correctly alongside active "
          "planes rather than being dropped or misplaced")

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("stacks.py is not a standalone tool; import its functions, "
                 "or run with --render-check for the headless self-check.")
