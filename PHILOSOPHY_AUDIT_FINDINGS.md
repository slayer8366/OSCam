# PHILOSOPHY.md rule-binding audit — durable findings

The full audit (all 16 rules in "Strict rules" and "Working principles",
Q1-Q5) lives on branch `claude/philosophy-rule-audit-4sjk4i` at commit
`4909821`, as a single `CHANGELOG.md` entry. That branch is being deleted
and will not merge. This file records only the four findings from it worth
keeping past that deletion — not the rest of the audit's inventory or its
other conclusions, none of which have been accepted. Do not treat this file
as a substitute for reading the full audit while it still exists; it is a
narrower, permanent record of four specific things the audit found.

This file is not a `CHANGELOG.md` entry and does not follow that file's
intent/build convention — the audit itself is the outcome being recorded,
same as the audit's own single entry was. `CHANGELOG.md` is untouched by
this branch: no entries edited, none added, no reorganization.

---

## Finding 1: the substrate rule's binding is a blocklist, not an allowlist

PHILOSOPHY.md's measurement-substrate rule is bound by
`measure.py`'s `check_measurement_provenance()` (`measure.py:175-184`),
which reads a file's embedded `"kind"` tag and refuses anything marked
`"display-referred derivative (NOT a measurement)"` — a tag `debayer.py`
sets at `debayer.py:701` on its own tonemapped output.

That check proves one direction only: a file *tagged* display-referred gets
refused. It does not independently prove that a file which *reaches*
`measure.py` untagged really is green-plane or linear-master data — only
that nothing marked it otherwise. A code path that produces display-referred
output without setting the tag would pass this check unchallenged.

That is structurally the same gap as the incident the rule itself cites —
a hypothetical feature checking "does this capture have annotations?" by
hashing `final_display.tif` and getting a false negative because the
correct exclusion mechanism was never consulted. Here, the exclusion
mechanism (the tag) is real and used correctly by every path that goes
through `debayer.py`'s own tonemap step, but a path producing
display-referred pixels by any other means — a future export path, a
manual PIL/CLAHE step outside `debayer.py`, an image reconstructed from
saved bytes without carrying the original description JSON forward — would
not be caught. The rule's binding covers the paths that were built with it
in mind; it does not structurally rule out one that wasn't.

**This is a live defect in a load-bearing binding, not a hypothetical.**
Not fixed here — the task that found it was an audit, and fixing it wasn't
in scope. Recorded as an open item in `HANDOFF.md` (see below) rather than
left to live only inside a `CHANGELOG.md` entry on a branch about to be
deleted.

## Finding 2: the CHANGELOG convention's binding and incident live in different documents

PHILOSOPHY.md's rule 9 (a `CHANGELOG.md` entry, once written, is never
modified) carries an explicit, strong binding in PHILOSOPHY.md itself —
the intent-commit-is-its-own-commit rule, checkable via `git log -p
CHANGELOG.md`. The incident that motivates its merge-conflict-resolution
sub-form — a superseding entry that consumed the superseded entry's own
header line, leaving its body orphaned under the new title, with a diff
that looked clean throughout — is not in PHILOSOPHY.md. It's in
`CLAUDE.md`'s "Verify append-only after each phase" section, worded almost
identically to what a search for it in PHILOSOPHY.md fails to find.

An agent told to read PHILOSOPHY.md first — which is what PHILOSOPHY.md
itself instructs — gets the binding with no story attached to it. The
incident is real and recorded; it just isn't reachable from the document
whose own rule it justifies, unless that agent also reads `CLAUDE.md` in
full before acting on the CHANGELOG convention.

## Finding 3: rules 4 and 7 point at incidents without narrating them — both recovered

**Rule 4** (camera-bound operations stay behind `camera_backend.py`) says
only that the rule was "revised again when `PRIORITY_click_mapping_fix.md`
outgrew the original wording." The actual incident is commit `0639d4e`,
"Fix: PHILOSOPHY.md's sensor-profile rule had gone stale (and
uncheckable)" — the rule as originally written named `imx477.py`
specifically ("the only file... that may know what an IMX477 is"), which
had already been outgrown by the code it was supposed to govern;
`assert_only_camera_backend_imports_sensor_profiles`
(`camera_backend.py:1570`) was added in that same commit, verified at the
time against a throwaway sibling file confirmed to trip the assertion
before being deleted.

**Rule 7** (one session folder contributes one z-stack plane) says only
"this one has already been violated once and caused a real bug." The
actual incident is commit `7bc204b`, "Review pass: fixed six real
defects" — `measure.py`'s z-stack `_load_stack` was unreachable and
broken, assembling a stack from one session's own captures, which is
exactly the shape this rule forbids.

Both incidents are real, dated, and hash-bearing. PHILOSOPHY.md just
doesn't tell either story — it points at them and stops.

## Finding 4: the substrate rule's incident is a near-miss, not a shipped defect — unlike rules 4 and 7's

PHILOSOPHY.md's own incident for the substrate rule — a feature that
"once checked... by hashing the session's `final_display.tif`" — traces
to commit `36ab34f` (where the prose was written, with no accompanying
bug-fix diff in that commit) and is corroborated by commit `eda9b5c`
("Add gallery.py"), which reasons through the identical trap by name
while building the real `capture_has_annotation` check, and chooses the
green-plane hash instead.

`eda9b5c` shows the mistake being reasoned through and avoided *during
design* — not a bug that shipped, was observed misbehaving, and was then
fixed. Rules 4 and 7's incidents (`0639d4e`, `7bc204b`, finding 3 above)
are the other kind: real defects that existed in landed code and were
caught and corrected. A near-miss and a shipped-and-fixed defect both
support a rule, but they are not the same kind of evidence, and
PHILOSOPHY.md's own text ("a feature once checked... would have reported...
and would have been quietly wrong") reads as though it were the latter. It
is worth knowing which one it actually is before treating it as equivalent
weight to rules 4 and 7's incidents.

---

None of the four findings above have been acted on beyond finding 1's
`HANDOFF.md` entry, and none of the audit's broader conclusions (which
rules are bindable, which are unbindable, the "suspects" list) are recorded
here or accepted. This file exists so these four specific things survive
the deletion of `claude/philosophy-rule-audit-4sjk4i`; it is not a mandate
to fix, rewrite, or otherwise act on any of them.
