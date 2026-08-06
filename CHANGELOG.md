# Changelog

Curated, most recent first. Grouped by logical change, not a raw commit
dump — each entry names the commit(s) it corresponds to for traceability.
See `HANDOFF.md` for what a fresh agent needs to know before working here;
this file is the historical record of what happened and why.

## 2026-08-05

### Record: two findings filed from the multi-capture verification (session.json correction-status field loss, derived outputs not per-capture)

Documentation only, no code touched — the branch is verified on the rig
(the two-Snap-in-one-session test, `CHANGELOG.md`'s own "Record build:
gallery-race staging design" entry above) and this session's own
instruction was to keep it that way, since any source change forces a
re-run. Work-is-the-outcome form, not intent/build: the work was the
verification already done in the prior session; this just files what it
found. Both items recorded in `HANDOFF.md` in place, ranked above
existing item 9, neither existing item renumbered or edited (verified:
`git diff` against `HANDOFF.md` shows only insertions).

**Item A — `session.json` correction-status field loss.** A second
capture in one session strips the first capture's
`raw_discarded`/`flat_correction`/`dark_correction` fields from
`session.json`; the first capture's raw files are untouched on disk,
only the record of them is gone. `_record_correction_status`
(`qt_shell.py:5584-5608`) reads `session.json` fresh from disk and
patches it in place, by design, so it also serves the manual processing
wizard's non-live sessions. `Session.record()` (`provenance.py:321-328`)
appends to the in-memory `captures` list, which never learned of that
disk-side patch, and `Session.write()` (`provenance.py:269-285`) then
overwrites the whole file from that stale list. Pre-existing, untouched
by the staging work — neither of the three functions above was touched
by it — and fires on the ordinary two-Snap workflow, no staging
involved. Observed in session `2026-08-05_163014`. Filed ahead of
existing item 9 because this is silent loss from the provenance record
itself, not a UI-level drop: `final.tif` still carries its own retention
embed (item B below), so once this fires, the TIFF states an outcome
`session.json` no longer corroborates or contradicts for that earlier
capture.

**Item B — derived outputs are not per-capture.** Raw frames are indexed
per capture (`snap_frame_0000.dng`, `snap_frame_0001.dng`, ...), but
`final.tif`/`single_master.tif`/`final_display.*` are rewritten in place
under fixed names on every processed capture. A session with N processed
captures holds N sets of raws and exactly one set of masters/display
images, belonging to the most recently processed capture, with nothing
in the filenames stating which. Pre-existing — the fixed output names
predate the staging work; per-file publish just moves the same fixed
names, unchanged. Evidence: session `2026-08-05_163014`'s `final.tif`
went from 25,334,219 bytes at 16:30:28 (after Snap #1) to 25,480,015
bytes at 16:30:50 (after Snap #2) — same path, rewritten in place, not
renamed.

No fix proposed for either item, per instruction. HANDOFF.md item 2
(retention-embed design) stays closed — neither finding reopens it, both
are separate, newly-filed gaps.

### Correction: SWEEP_CHECKS.md's orphaned-staging-directory line was never added

Supersedes, does not edit: the "Record intent"/"Record build: gallery-
race staging design" entries below stay exactly as written — checked
directly (`grep -c SWEEP_CHECKS.md` against the committed intent entry:
0 hits), and neither entry actually asserts in text that
`SWEEP_CHECKS.md` gained a line. The false claim was mine, made in
conversation rather than in this file: reporting on the crash-cleanup
decision from two tasks prior ("leave orphaned staging directories in
place, log them, add a line to `SWEEP_CHECKS.md`, no auto-delete"), I
said "implementing as given" and later said "I committed to adding this
in the intent/build entries" — read naturally as claiming it shipped.
It did not. `grep -i staging SWEEP_CHECKS.md` returned nothing before
this entry, on a Pi-verified check.

**Actual state, corrected here:** `SWEEP_CHECKS.md`'s "Retention safety"
table gains one row, marked **Gap** (per this file's own convention —
marking something a gap is the point, not a shortfall): a staging
directory left behind by a crashed or interrupted publish is never
auto-deleted, per the stated policy, but nothing in the repo enforces
or checks that policy — no `render_check` assertion, no logging. Two
real examples exist on this Pi right now from an earlier verification-
script crash (`~/staging/2026-08-05_145415`/`_145438`) and were left in
place per the policy, which is evidence the *policy* was followed
manually, not that it is enforced by code.

### Record build: gallery-race staging design (per-file publish, not directory rename)

Compares the built result against the recorded intent and reports
verification honestly, including what verification itself got wrong
first.

**`python3 qt_shell.py --render-check`: exit 0, every assertion PASS**,
run directly in the foreground (not backgrounded — a backgrounded
invocation captures no output to read, same caution as the tenth task's
own Part 4 entry above), twice: once right after the `Build` commit, once
again after the on-rig fix described below, to confirm the fix didn't
regress it. Both runs clean, including the `auto-processing`/`Keep RAW
Images`/`export-format` blocks and the `science2_` second-capture-in-one-
session sub-test.

**On-rig hardware verification, real `Picamera2Camera`, both retention
paths, driven through the actual staging-aware call sites
(`win._run_burst_kind`/`win._auto_process`), not a mock:**

The first attempt hung for 2h11m — `ps`/`strace` showed every thread
parked in `futex_wait` with 2 seconds of real CPU time used across the
whole run. Root cause was in the throwaway verification script, not this
task's code changes: `_run_burst_kind` is documented as meant to run on a
background thread while the Qt event loop keeps pumping on the main
thread (`_fire_armed_burst`'s own `threading.Thread`, the only way the
real app ever calls it) — the script called it synchronously on the main
thread instead, with the event loop idle, deadlocking something in the
camera backend that needed it serviced concurrently. Killed, camera
device confirmed freed (`fuser`), script rewritten to mirror
`_fire_armed_burst`'s own threading pattern exactly plus a hard `timeout`
wrapper so a repeat could not hang silently again.

The second attempt aborted (`ValueError: read of closed file` in
picamera2's own `QGlPicamera2.handle_requests`, a background thread
reading a notification pipe closed by the first camera's teardown) —
running two `Picamera2Camera`/`FocusPreviewWindow` pairs sequentially in
one process hit a picamera2 preview-widget teardown-ordering issue, not
this task's code either; the real app itself only ever constructs one
camera per process lifetime. Split into one process per retention path
instead, matching that.

**The third attempt is what actually verified the design, and caught a
real bug in it.** Keep RAW Images ON first: session directory ended up
with `final_display.jpg/png/tif`, `science_frame_0000/0001.dng/.jpg`, and
`single_master.tif` — but staging was NOT empty afterward, `final.tif`
(debayer.py's primary `-o` output, the RGB measurement master, always
written, no checkbox) was left behind, published nowhere. Root cause:
`final.tif` was referenced only as a bare string literal handed to the
debayer.py subprocess call in both the `hdr`/`science`-`snap` branches of
`process()`, never captured as a Python `Path` anywhere else in the
function — the publish loop, built from `master_files`/`disp`/`png`/
`jpg`/`dng_dest`, had no way to know it existed. Fixed: `final_tif =
capture_dir / "final.tif"` named right beside `disp`/`png`/`jpg`, added
to the publish list (`hdr_from_session.py`, own commit, message has the
full detail). Re-ran both retention paths after the fix:

- **Keep RAW Images ON** (session `2026-08-05_145832`): session directory
  = `final.tif`, `final_display.jpg/png/tif`, `science_frame_0000/0001.
  dng/.jpg`, `single_master.tif`. Staging = empty. Embed: `raw_discarded
  = False`. Matches — raws present, embed says kept.
- **Keep RAW Images OFF** (session `2026-08-05_150021`): session
  directory = `final.tif`, `final_display.jpg/png/tif`, `single_master.
  tif` — no raw `.dng`/`.jpg`. Staging = empty. Embed: `raw_discarded =
  True`, `raw_discard_reason` stated. Matches — raws absent, embed says
  discarded, master survives regardless as designed.

Pass condition (embed matches directory) held for both, on real
hardware, after the fix. These two real sessions were left on disk under
the real `~/captures`/`~/provenance`/`~/staging` (not cleaned up) —
they're genuine verification evidence, not synthetic data, and harmless
to leave; the user can remove them at their discretion.

**Verification-hygiene note for whoever reads this next:** two of these
three attempts failed before the design was ever actually exercised, and
both failures were reported here rather than quietly retried and
forgotten — the point of running on real hardware is exactly to catch
what a self-check with `FakeCamera` cannot (`FakeCamera` never runs the
real `Picamera2Camera`/`QGlPicamera2` teardown paths, and `render_check`
never actually inspects a real `final.tif` disk artifact against a real
retention setting the way this on-rig run did — that gap is precisely
how the `final.tif` bug survived every render-check pass above). No
result here was assumed from render-check alone.

### Record intent: gallery-race staging design (per-file publish, not directory rename)

Own branch off `main`: `claude/gallery-race-staging-design`. Runs directly
on the Pi (`hostname` == `raspberrypi`).

**What this is.** Capture and processing for the auto-processed kinds
(`snap`/`science`/`hdr`'s science phase — the three that reach
`_auto_process`/`_run_process_cmd`) write into a same-device staging
directory (`Path.home()/"staging"/<session_ts>`) instead of the session
directory directly. Retention (Keep RAW Images off) deletes from staging.
Once processing succeeds, the finished set is published into the session
directory one file at a time via `os.replace` — not a single
`os.replace(staging_dir, session_dir)` directory-level rename.

**Why not a directory rename, despite that being the original brief.**
Investigated and reported before writing code, per the user's own
correction: a directory-level `os.replace` only succeeds when the
destination is empty (POSIX requires it), which only holds for a
session's *first* auto-processed capture — `self._session` is never
reset (`qt_shell.py:2543`/`4360`) and re-Snap/reshoot routinely adds a
second, third, ... capture into the same session directory
(`_snap_counter`, `_reshoot_guard`), so a directory-rename design would
have protected almost nothing in practice. The actual hazard the gallery
CAVEAT (`qt_shell.py:5461-5475`) names is *deletion* under a stale
listed path — and staging already removes every delete from ever
touching the session directory, since retention (step 5) runs entirely
in staging. Once nothing is ever unlinked from a directory the gallery
can see, publish atomicity stops being the load-bearing property; a
per-file `os.replace` — the repo's own existing seven-site pattern
(`provenance.py`/`qt_shell.py`/`calibrate.py`/`ca_measure.py`/
`annotations.py`/`measure.py`/`plane_cache.py`, all tmp-write-then-
`os.replace`), applied to more files rather than a directory operation
with no precedent in this codebase — gets the same protection for every
capture in a session, not just the first, with no `ENOTEMPTY`
constraint, no placeholder needed (the session directory is never
emptied out from under itself, so nothing needs to keep it non-empty),
and no Windows limitation (per-file `os.replace` is atomic there too,
unlike a directory replace). Cost, stated plainly rather than hidden:
the published set no longer appears at once — worst case a gallery
already open mid-publish sees fewer files than it eventually will, all
of which exist and open correctly. Given the gallery lists once at
construction and never refreshes (`gallery.py:334`, the only call site
of `.refresh()` in the repo) and `_first_frame_paths` takes the first
`dng`/`tif` it finds and stops, this reduction in protection is real but
narrow, and is the user's accepted tradeoff, not mine.

**`capture_dir` is a promise during staging, not a description — stated
as a contract, not fixed.** For the whole staging window (from
`record_capture` right after the shutter through the per-file publish
loop completing), `session.json`'s `capture_dir` field continues to name
the *final* session directory (provenance never moves — this file was
already always written that way, `provenance.py:231`), while the actual
bytes sit in staging. Any reader that resolves a path through
`capture_dir` during that window — `gallery.py`'s `_first_frame_paths`
included — gets a path to a file that does not exist yet. This is the
same defect class as HANDOFF item 2 (retention-embed design: a stored
description written before the fact it describes is actually true), at
smaller scope, and it is being accepted rather than engineered around.
A `# CAVEAT:` comment goes at `hdr_from_session.py`'s `capture_dir`
derivation saying the same thing in place.

**Two more "resolves its path from wherever frames currently are" bugs
found by tracing this through, fixed before proceeding rather than
guessed past:**
1. `hdr_from_session.py`'s dark-frame lookup (`dark_dir = capture_dir /
   "dark"`) would silently stop finding dark correction frames once
   `capture_dir` is redirected to staging — dark captures are a
   separate, never-staged call site (`qt_shell.py`'s `_run_burst_kind`,
   `target_dir = session.dir / "dark"`, untouched by this work), so dark
   frames physically live under the real session directory even during
   staging. Fixed by decoupling `dark_dir` from the (now overridable)
   `capture_dir` — it always resolves from `session["capture_dir"]`,
   never from the `--capture-dir` override.
2. `qt_shell.py`'s `capture_correction_status` detects the on-disk raw
   extension (`dng` vs `tif`) by globbing `session_dir` for the just-
   captured frame — during staging that frame is not in `session_dir`
   yet, so this would have silently fallen back to its `"dng"` default
   every time (happens to be harmless on real Picamera2 hardware, wrong
   for `FakeCamera`'s `.tif` output — a real, if narrow, silent-wrong
   path). Fixed with an explicit `own_frames_dir=None` parameter
   (defaults to `session_dir`, so every other caller of this function is
   unaffected) that `_auto_process` now passes as the staging directory.

**`--capture-dir` validated, not trusted.** Per the earlier round's own
agreed shape: if `--capture-dir` is given and the directory does not
exist, or contains no frames matching `frames_for()`'s own glob for this
capture, `hdr_from_session.py` fails loudly with the path in the
message rather than falling back to `session["capture_dir"]` — a silent
fallback would mean quietly processing the wrong directory, which is
exactly the failure this flag exists to prevent. `--capture-dir` and
`--publish-dir` are cross-validated to be given together.

**Scope.** `dark`/`flat` capture call sites are untouched (never staged
— `flat` already writes straight to the standing `FLAT_ROOT` library,
`dark` to `session.dir/"dark"`, neither reaches `_auto_process`). The
gallery race guard itself, the gallery's never-refreshing entry list,
Stage 3, and `white_level`/`frame_average.py`/sigma-clip behavior are
untouched, per instruction.

**This makes the retention-before-embed ordering (`correction_status`,
including `raw_discarded`, is constructed strictly after the deletion
loop, `hdr_from_session.py:339` through `~362`) the resolution of
HANDOFF.md open item 2** — deliberately as a property of the one shared
`process()` function used by both the staged auto-process path and the
unstaged manual-reprocess/archive-wizard path, not a staging-only
branch, so the item closes for every caller, not just the first capture
of a session. See the matching "Build" entry below for what was
actually verified.

### Build: tenth task Part 4 — regenerate `FUNCTION_INDEX.md`, close item 7

Ran on the Pi (`hostname` == `raspberrypi`, real `numpy`), the same
session that closed Parts 1-3. Mechanical fix: `python3
function_index.py` regenerated `FUNCTION_INDEX.md` against the actual
PR #10/#11/#12 additions (`frame_average.py` gained
`read_sidecar_meta`/`aggregate_capture_field`/`capture_meta_for_science`;
`hdr_merge.py` gained `try_read_embedded_capture_meta` and a widened
`merge()` signature; `qt_shell.py` gained the `CAVEAT:` comment on its
unguarded gallery-race function). `python3 function_index.py
--render-check` now passes (`assert_function_index_current` included).
Also re-ran `DISPLAY=:0 python3 qt_shell.py --render-check` directly
(not just trusted from Part 1's commit message) — still exit 0, all
assertions PASS, confirming that result still holds. Run as a direct,
synchronous terminal command, not a backgrounded task invocation — the
distinction matters because a backgrounded invocation of this same
command captured no output to read, so only the direct terminal run
produced anything to actually verify against. The check completing
either way isn't the same as the result being read; this is the first
time in this repo that a post-PR-#10/#11/#12 `--render-check` result
was both run *and* read, covering the merge plus the `FUNCTION_INDEX.md`
regeneration above.

**What this failure mode reveals, per the user's own read:** the check
existed and worked, but nothing invalidates the index at the event that
actually stales it — a PR adding a function. `SWEEP_CHECKS.md`'s
sensor-sanity section gained a formal entry for this, naming the
trigger explicitly ("any PR that adds, removes, or changes the
signature of a module-level function regenerates and commits
`FUNCTION_INDEX.md` as part of that same PR") and marking the trigger's
*enforcement* (a hook or CI step, as opposed to remembering) as a
still-open gap — the mechanical fix here doesn't close that part.

`HANDOFF.md` item 7 updated to closed below.

### Record build: tenth task Part 3 — standing sweep-check list

Built to the intent recorded below. No deviation: `SWEEP_CHECKS.md` is
the only file added, all five sections landed exactly as scoped (three
implemented-with-caveats items, five gaps, three unverifiable-citation
flags, one live-failure cross-reference to `function_index.py`), and no
check named as a gap was implemented — verified by re-reading the
committed file against the intent entry section by section.

**Against the counted baseline:** every named entry from the intent
(`ca_lib.adapt_center`'s missing coverage; the green-plane/debayer
delegation caveat; the FOV-ratio unverifiable citation; the description-
tag/provenance-path/retention gaps; the sensor-sanity meta-section)
appears in the built file, none dropped, none added beyond what the
intent scoped plus the one explicit cross-reference the intent itself
called for (the `function_index.py` live failure, used as the file's
own closing argument for why it exists).

**Verification, stated honestly:** `SWEEP_CHECKS.md` is prose/tables,
no executable content — verified by rereading it against `CHANGELOG.md`'s
Part 1/Part 2 entries it was seeded from (every cited file/line/function
name traced back to something actually read or actually run this
session, not invented at write time) and against the intent entry
(scope match, confirmed above). Not re-run against the repo a second
time beyond what Parts 1+2 already executed — this file makes no new
claims about the code that weren't already grounded there.

`HANDOFF.md`'s open items updated below: item 8 (Part 3) closed; item 7
(`function_index.py` stale) stays open, now cross-referenced from
`SWEEP_CHECKS.md` itself as the file's own live example.

### Record intent: tenth task Part 3 — standing sweep-check list

Own step, per the three-phase convention (`PHILOSOPHY.md`), following
directly from Parts 1+2 above (their findings are the baseline this
scope is drawn from — grep-only groundwork already superseded by the
verified catalog those entries hold).

**What gets built.** One new file, `SWEEP_CHECKS.md`, at repo root
(matching the existing all-caps `.md` naming convention —
`HANDOFF.md`/`CHANGELOG.md`/`PHILOSOPHY.md`/`README.md`/
`FUNCTION_INDEX.md`). No `.py` file, no test harness, no code — a fixed,
pre-written list a human or a future session reads and runs down, not
composed fresh each time. Per direct instruction, this list itself does
not implement any missing check it finds — it records the gap.

**Baseline scope, stated so the build can be checked against it.** Five
sections, one per seed category, each expanded into concrete entries
drawn only from what Parts 1+2 already verified exists in this repo
(no new grepping/reading invented at build time — that would be
composing checks in the moment, the exact failure mode this file
exists to prevent):

1. **Measurement correctness** — calibration surviving a capture-
   resolution change (`ca_lib.adapt_center` — flagged in Part 2 as
   having zero self-check coverage, a real gap, not implemented); green-
   plane-only measurement (`measure.load_measurement_plane`,
   `calibrate.load_green_plane` — both have real render_check coverage,
   contract = BGGR Bayer layout / cross-checked against `debayer.py`);
   preview/still field-of-view agreement (`camera_backend.py`'s sensor
   crop geometry check + `imx477.py`'s FOV-ratio cross-check — both
   real, contract partly cites the missing `PRIORITY_click_mapping_fix.md`,
   noted as unverifiable-but-not-known-wrong per Part 2).
2. **Provenance integrity** — one description tag per TIFF
   (`hdr_merge._assert_single_description_tag` — real, on the
   production path, zero automated-test coverage, per Part 2); recorded
   values matching their artifact (`provenance.py`'s sidecar/session.json
   location checks — real but partial, per Part 2's gap note: directory
   placement is checked, round-trip path resolution is not); recorded
   output paths resolving to the file they're embedded in (**gap**,
   named explicitly in Part 2, nothing in the repo checks this today).
3. **Geometry derivation** — no hardcoded sensor dimension above the
   driver layer / `assert_only_camera_backend_imports_sensor_profiles`
   (real, `camera_backend.py`, contract = `PHILOSOPHY.md`'s sensor-
   profile rule); shape predicates deriving from the sensor profile
   (`imx477.crop_for_size` internal-consistency + `_resolve_sensor_profile`
   exact-name-match checks — both real).
4. **Retention safety** — no deletion path removing more than its name
   covers (`qt_shell.py`'s Keep RAW Images block — real, and the fixed
   instance of the one known contract-vs-observed bug); `plane_cache
   .clean_cache` never removing a referenced plane (real, three checks);
   `stacks.move_frames_to_discarded` prefix-match safety (**gap**, named
   in Part 2 — defined, never exercised); no writer's default output
   filename appearing in any deletion list (**gap** — nothing in the
   repo checks this as a standing invariant; the Keep RAW Images fix
   corrected one specific instance by hand, this generalizes it into a
   check no one has written).
5. **Sensor sanity** — a meta-entry: every check above gets its
   contract-vs-observed classification carried over verbatim from Part
   2's catalog, not re-derived, plus the three dangling-citation checks
   (`plane_cache.py`, `qt_shell.py`'s Live Measuring boundary check,
   `camera_backend.py`/`imx477.py`) flagged as unverifiable-contract
   rather than silently upgraded to "external contract" now that they're
   being written into a standing list.

**What this deliberately leaves alone**, per direct instruction:
`frame_average.py`'s averaging behavior, the deletion path
(`hdr_from_session.py:process()`), and `archive_raws()` — not read for
new findings, not touched, not added to the list beyond what Parts 1+2
already found in passing (the `archive_raws()` off-rig `--raw-ext tif`
risk from the 2026-08-03 Keep RAW Images investigation, already on
record there, not re-investigated here).

**Checkable against the finished work:** the build record should list
exactly these five sections with the entries above (or explain any
deviation), confirm `SWEEP_CHECKS.md` is the only file touched, and
confirm no check gets implemented as part of writing the list.

### Record: tenth task Part 1 — Pi render-check verification gap closed

Picks up the handoff below ("Open: tenth task..."). This session runs
directly on the Pi (`hostname` reports `raspberrypi`, `numpy 1.24.2`
importable, real `ssh` present) — the opposite environment from every
prior session on this repo, none of which had a path to the Pi. Stated
per the task's own instruction that this fact is itself an input to
Part 2 below.

`cd ~/imx && git pull --ff-only origin main` fast-forwarded
`7c1696b..95fce3e` (9 commits: PRs #10, #11, #12 — CHANGELOG.md,
HANDOFF.md, PHILOSOPHY.md, `frame_average.py`, `hdr_from_session.py`,
`qt_shell.py`). Working tree had a locally-modified `profile.json` and
an untracked `calib/` (real specimen/rig data per `PHILOSOPHY.md`) —
neither conflicts with the fast-forward and neither was touched, added,
or committed.

`python3 qt_shell.py --render-check` then run for the first time ever
against a merged state on this repo. **Result: exit 0, every assertion
PASS** — overlay/box/bar/letterbox geometry, XY ruler, onboarding gate,
slider maps, capability-driven capture/preview resolution kwargs,
lores-derived FocusPreviewWindow state, theming, capture-lock,
processing-wizard helpers, dark-nesting, `archive_session_raws`,
z-stack tagging and the full z-stack aid flow, full-screen mode, the
Preferences dialog (both parts), Clean Cache Now, green-plane
extraction, Export, Publish, capture-sharpness scoring, auto-processing,
**Keep RAW Images** (the PR #9-fixed check — master/final now correctly
survive, raw frames alone are deleted), export-format flags, Live
Measuring (launch, commit round-trip, all five freeze-fix cases, all
five canvas-fit cases), and the full Live Measuring module-boundary
check. No failure, so per the task's own instruction there is nothing
to stop on — three merges' worth of unrun gate ran clean on its first
real pass. Full raw output retained in this session's transcript, not
reproduced verbatim here; the check-name list above is exhaustive
(nothing PASS'd was omitted).

### Record: tenth task Part 2 — check enumeration (report only, nothing fixed)

Per the handoff's Part 2 spec. `grep -l "def render_check" *.py` (rerun,
matches the prior session's ungrounded groundwork): 15 files. Read every
one of those 15 `render_check()` functions in full, plus
`camera_backend.py`'s self-check (an `if __name__ == "__main__":` block,
not a `def render_check` — resolves the prior session's noted count
discrepancy: it exists, is real, and is exercised the same way, just
under a different invocation convention, `python3 camera_backend.py`
with no flag), `function_index.py`'s `assert_function_index_current`,
`qt_shell.py`'s `assert_live_measuring_has_no_calibration_dependency`,
`hdr_merge.py`'s `_assert_single_description_tag`, and
`test_burst_backend.py`. Every one of these except `test_burst_backend.py`
(needs a real camera + `.show()`'d Qt widget — not run, to avoid
operating hardware for a report-only task) was actually executed on the
Pi just now, not just read — findings below are grounded in real exit
codes, not static reading alone.

**(a) Expected-value provenance.** The overwhelming majority trace to an
external contract: cross-module behavior another file's real code
defines (e.g. `calibrate.py`'s green-plane check reuses `debayer.py`'s
own `extract_green`; `export.py`'s schema cites "build checklist §11";
`publish.py` cites "§12"; `plane_cache.py`/`stacks.py`/`provenance.py`
check documented store/split contracts), a physical/math standard
(Pythagorean distance, circle/ellipse area, BGGR Bayer layout), or a UI
label (`measure.py`'s status-line strings, `wizard_pages.py`'s
`snap_frame_<idx>` convention). The one **known** instance of a check
encoding observed behavior as correct — `qt_shell.py`'s Keep RAW Images
block, confirmed by reading the 2026-08-03 entry and the current code
side by side — is now fixed (the assertion flipped from "master must be
deleted" to "master must survive," matching the setting's own name/
label, not the old buggy output). **A few more of the same shape exist,
all lower-stakes:**
  - `calibrate.py`'s `stretch_to_uint8` check (~line 1015) and
    `ca_measure.py`'s `format_offset_table`/`poly2_flag` checks
    (~lines 835, 856) assert against arbitrary internal thresholds with
    no cited external spec — display-only / evidence-only paths, so an
    error here can't corrupt a measurement, only a display or an
    advisory flag.
  - `measure.py`'s z-stack flagged-plane check exercises
    `stacks.sharpness_relative_flag`'s real `rel_drop=0.5` default
    ("half of best") — a genuine cross-module check, not circular — but
    the `0.5` constant itself has no cited spec anywhere in the repo.
  - `measure.py`'s green-plane-extraction check (and the pixel-hash
    check built on it) computes its own "expected" value by calling
    `debayer.extract_green` with the *same* `DEFAULT_CFA_PATTERN`/
    `DEFAULT_GREEN_WHICH` constants `load_measurement_plane` itself
    uses — proves the wrapper doesn't alter debayer.py's result, but
    would not catch both call sites agreeing on a wrong constant. One
    level removed from the Keep-RAW-Images shape, not the same bug.
  - `annotations.py`'s `stored_calibration_ref` check derives part of
    its own expectation by calling `calibration_ref_for` (the function
    whose *relationship* to `stored_calibration_ref` is under test) —
    flagged as a relational, not absolute, oracle; looks intentional and
    documented, not accidental.
  No other instance of the exact Keep-RAW-Images shape (assert whatever
  the code under test currently outputs, call it correct) was found.

  **Dangling contract citations, a distinct finding from the above:**
  several checks cite a planning doc as their external contract —
  `plane_cache.py` cites `PLAN_04_green_plane_cache.md`, `qt_shell.py`'s
  `assert_live_measuring_has_no_calibration_dependency` cites
  `PLAN_quick_ruler.md`, `camera_backend.py`/`imx477.py` cite
  `PRIORITY_click_mapping_fix.md` — **none of these three files exist
  anywhere in the repo, in any worktree, or in git history**
  (`git log --all --diff-filter=A` for all three: no hits). The
  citations may be accurate (docs shared outside the repo, e.g. in
  conversation) but are not independently verifiable from inside it as
  things stand. Not the same failure mode as Keep RAW Images (nothing
  here is wrong), but it means "external contract" for these specific
  checks is currently an unverifiable claim, not a checkable one.

**(b) Where each can run.** All 15 `render_check()` files plus
`camera_backend.py`, `imx477.py`, and `pixel_hash.py` (18 files total)
were run just now on the Pi — **all 17 passed**; **`function_index.py`
`--render-check` failed, exit 1** (see below — a real, current, live
failure, discovered and reported, not fixed). All 18 need only `numpy`
(all import it directly or transitively) — none touch real hardware,
all use `FakeCamera`/synthetic data/tempdirs. `measure.py`'s Qt-gated
sub-checks (marks-commit UI, ReviewWindow, z-stack UI) need a
`QApplication` but not `.show()`/a real display — offscreen-capable, so
sandbox-runnable given PyQt6 (this repo's actual sandboxes to date have
had neither `numpy` nor PyQt6). `test_burst_backend.py` needs a real
`Picamera2Camera` and a real `.show()`'d Qt event loop — Pi-only,
hardware-only, not run this session. `hdr_merge.py`'s
`_assert_single_description_tag` runs on every real production
`hdr_merge.py`/`hdr_from_session.py` invocation (real code path, not a
test) but has **zero automated-check coverage** — no render_check, no
test file, invisible to the documented 15-module sweep; needs real (or
realistically-shaped) bracket TIFFs to exercise. `py_compile`, cited
throughout the CHANGELOG as "the only check possible" in numpy-less
sandbox sessions, is **not an implemented gate** — no CI config,
Makefile, or git hook in the repo runs it automatically (checked:
no `.github/`, no `Makefile`, no non-sample hooks in `.git/hooks/`); it
is a manual habit narrated in commit messages, and it only proves the
file parses, nothing about behavior.

**Live finding, not fixed:** `python3 function_index.py --render-check`
**fails right now on this main**, exit 1: `FUNCTION_INDEX.md` is stale
against the current source. `assert_function_index_current` (the
freshness check itself is sound — external contract, byte-diff against
a real committed artifact) reports `frame_average.py` gained
`read_sidecar_meta`/`aggregate_capture_field`/`capture_meta_for_science`
and `hdr_merge.py` gained `try_read_embedded_capture_meta`/
`_assert_single_description_tag` plus a changed `merge()` signature —
all real PR #10/#11/#12 additions — and `qt_shell.py` gained the
Gallery-race `CAVEAT:` comment (`HANDOFF.md` open item 3) — none
reflected in the committed `FUNCTION_INDEX.md`, because no one ran
`python3 function_index.py` (no flag, regenerates for real) after those
merges. This is a second, independent instance of exactly this task's
own theme — a real check, part of the documented sweep, silently unrun
after merges — found by actually running the enumeration rather than
just reading it. **Not fixed here** (Part 2 is report-only); `HANDOFF.md`
records it as open below.

**Coverage gaps, not bugs — flagged per the task's "mark gaps as gaps"
instruction:**
  - `ca_lib.py` (chromatic-aberration math: `radial_warp`,
    `apply_ca_correction`, `adapt_center` — the function calibration's
    resolution-change survival depends on) has **no self-check of any
    kind**, no `if __name__`, nothing.
  - `frame_average.py`, `hdr_from_session.py`, `debayer.py` have no
    `render_check`/self-test of their own — only indirectly exercised
    where another module's `render_check` shells out to them as a real
    subprocess (`process_wizard.py`'s does, for `frame_average.py`).
  - `stacks.py`'s `move_frames_to_discarded` (the retention/deletion-
    adjacent prefix-move) is defined but **never exercised** by
    `render_check` — no assertion either way on whether a prefix match
    could sweep up a differently-tagged capture sharing a prefix.
  - `plane_cache.py`'s `clean_cache` checks all pass an explicit `root=`
    temp dir; none exercises the `root=None` default-scoping path
    against a realistic tree holding non-cache files — scoping is
    structural (`_resolve_root` always appends `plane_cache/`), so this
    is an untested assumption, not a demonstrated bug.
  - `provenance.py`'s checks confirm sidecars/`session.json` land in the
    *correct directory* and that `capture_dir` gets *recorded*, but none
    re-opens a file at a recorded path to confirm it is the file the
    entry describes — the "recorded path resolves to the file it's
    embedded in" half of provenance integrity is untested.

Nothing in this Part was fixed, deleted, or modified — `frame_average.py`,
the deletion path, and `archive_raws()` were not touched, per instruction.

### Open: tenth task (Pi verification-gap run + standardized sweep list), blocked on Pi access from this session

Given as two parts under the three-phase convention (intent commit,
build commit, record-build commit): close the `--render-check`
verification gap left by PRs #10/#11/#12, then start a standardized
sweep-check list. Neither part was started. This entry hands both off;
it is not an intent commit for either.

**Why nothing was built.** Part 1 requires running, on the Pi:
`cd ~/imx && git pull --ff-only origin main && python3 qt_shell.py
--render-check`, and reporting the result in full — stopping rather
than fixing if it fails, since PRs #10, #11, and #12 all merged with
`qt_shell.py --render-check` unrun (this sandbox has no `numpy`, true
of every PR against this repo so far, so the gate has never once run
against a merged state). This session has no path to the Pi: no `ssh`
binary installed, no `~/.ssh` config or `known_hosts`, no VPN/tailscale,
no Pi hostname or IP documented anywhere in this repo, and the only
registered remote environment (`list_environments`) is this same cloud
sandbox, not the Pi — checked, not assumed. Per direct instruction, this
is handed to a new session with real Pi access rather than worked around
from here.

**Part 1, exact task for that session:** run the two commands above,
report the full output. If it does not pass, stop there and report —
do not attempt a fix in the same pass. Real capture data lives on the
Pi's disk; do not delete, overwrite, or move any existing file while
doing this.

**Part 2, exact task, report only, nothing fixed:** enumerate every
automated check in the repo — every individual assertion inside every
module's `render_check()`, every `py_compile` gate, any self-test, any
assertion helper like `hdr_merge.py`'s `_assert_single_description_tag`.
For each: (a) whether its expected value comes from an external
contract (a spec, a UI label, `PHILOSOPHY.md`, a physical standard) or
from observed behavior — the `render_check` assertion that encoded the
Keep RAW Images deletion bug as correct (see `CHANGELOG.md`'s 2026-08-03
"Keep RAW Images narrowed to raws only" entry) is the known instance of
the second kind; report honestly whether others exist, don't assume
there's only the one; (b) where it can actually execute — sandbox, Pi,
or both — and what it needs that the sandbox lacks (`numpy`, hardware,
capture data).

*Grep-only groundwork done this session, not verified or classified —
treat as a starting point, not a catalog:* `grep -l "def render_check"
*.py` finds it in exactly 15 files: `annotations.py`, `ca_measure.py`,
`calibrate.py`, `export.py`, `focus.py`, `function_index.py`,
`gallery.py`, `measure.py`, `plane_cache.py`, `process_wizard.py`,
`provenance.py`, `publish.py`, `qt_shell.py`, `stacks.py`,
`wizard_pages.py`. An earlier entry (2026-08-01 port work) describes "a
full project `--render-check` sweep (all 16 modules, including
`camera_backend.py`)" — `camera_backend.py` did NOT turn up in this
grep, so either that count is off by one or `camera_backend.py`'s
self-check is wired some other way; it does have its own assertion
functions (`_assert_plain_types`, `assert_only_camera_backend_imports_
picamera2`, `assert_only_camera_backend_imports_sensor_profiles`, all
referenced in `PHILOSOPHY.md`). Not resolved here — a real finding for
Part 2 to run down, not assumed either way. Two more assertion helpers
spotted by grep, not yet read in full: `function_index.py`'s
`assert_function_index_current` and `qt_shell.py`'s `assert_live_
measuring_has_no_calibration_dependency`. One self-test sits outside the
`render_check` convention entirely — `test_burst_backend.py` — not
opened this session; what it tests and what it needs to run (`numpy`,
hardware, or neither) is unknown.

**Part 3, exact task:** create a new standing file holding a fixed,
pre-written set of sweep checks, run as a standard event on every code
change, never composed in the moment — a check written on the spot
takes its expected value from the change that prompted it, which is
exactly how the deletion bug got enshrined as correct in the first
place. Each entry records: what it checks, what external contract
supplies its expected value, where it can run (sandbox / Pi / both), and
whether it's currently implemented or a gap. Seed categories to expand
into concrete entries from what the repo actually contains: measurement
correctness (calibration survives a capture-resolution change;
measurement reads the green plane, not the debayered image; preview and
still agree on field of view), provenance integrity (exactly one
description tag per written TIFF; recorded values match the artifact
they describe; recorded output paths resolve to the file they're
embedded in), geometry derivation (no hardcoded sensor dimension above
the driver layer; shape predicates derive from the sensor profile,
matching the `assert_only_camera_backend_imports_sensor_profiles`
convention `PHILOSOPHY.md` already documents), retention safety (no
deletion path removes something its name doesn't cover; no writer's
default output filename appears in any deletion list), sensor sanity
(every check's expected value traces to a contract, not to observed
behavior). Mark gaps as gaps — a list claiming coverage it doesn't have
is worse than a short one. Report the intent baseline as scope, not a
count. Do not implement any missing check found while building this
list. Do not touch `frame_average.py`'s averaging behavior, the
deletion path, or `archive_raws()` while doing any of this.

**What the next session should do first, per the three-phase
convention:** write its own "Record intent" entry — recording intent
before the build begins is itself the rule (`PHILOSOPHY.md`), and this
entry is a handoff, not that commit. This entry's own groundwork
(the 15-file grep, the assertion-helper list, the `camera_backend.py`
count discrepancy) should be verified, not trusted, before being relied
on in that entry.

## 2026-08-04

### Open: task9-work fast-forward to main, blocked on push permission

`task9-work` (this branch) carries the build below plus the
`claude/white-level-constant-consolidation` merge (`119cefc`). Per the
user's explicit instruction, it lands the same way every prior
PHILOSOPHY.md-touching branch has: a direct fast-forward push to `main`,
no PR, no merge commit — the same form used for the PyQt6 port (see
`CHANGELOG.md`'s and `HANDOFF.md`'s port entries). Confirmed safe:
`git fetch origin main && git merge-base --is-ancestor origin/main
task9-work` printed "fast-forward safe" as of this entry.

The push itself (`git push origin task9-work:main`, run from
`/home/bwann83/imx/.claude/worktrees/land-hdr-merge-verification`) is
blocked by the session's own auto-mode permission classifier, not by
git — it refuses `git push` to `main` regardless of the user having
already approved it in conversation. **Default, if no one revisits
this**: the user runs the push directly from a shell outside this
session, since that's the one path the classifier can't intercept.
Whoever picks this up next: check `git log origin/main -1` against
`task9-work`'s tip (`005ff56` as of this entry) — if they already
match, this is stale and the push already happened.

Two bookkeeping items for after it lands, from the user directly:
`claude/white-level-constant-consolidation` is already merged in via
`119cefc` (see below) — retired, don't attempt to land it again.
`claude/frame-average-sidecar-wiring`,
`claude/keep-raw-images-scope-fix-cleanup`, and
`claude/gallery-race-comment-fix` are still outstanding and don't touch
`PHILOSOPHY.md`, so they land under the other form (PR, not direct push)
once this push clears.

### Record build: append-with-positional-note conflict resolution, landed and generalized

Built to the intent recorded below, across two sessions (the second
resuming after the first hung mid-merge). No deviation in the resolution
method itself; one scope note below.

**Part 1, landing the conflict.** `claude/white-level-constant-
consolidation` merged as `119cefc`. `CHANGELOG.md`'s conflict resolved
exactly as planned: the incoming branch's two entries appended verbatim
to the end of the `## 2026-08-03` section, prefaced by the positional
note describing where they originally sat and stating their numbered
list's self-containment. `HANDOFF.md`'s conflict was, as predicted by
inspection, a plain single-region append and needed no positional note.
The two auto-merged code files were checked (`py_compile` clean,
`MERGE_WHITE_LEVEL_DEFAULT` reads back correctly) before the merge
commit.

**Part 2, generalizing the rule.** `PHILOSOPHY.md`'s existing two-form
conflict-resolution text (interleaving, superseding — from `claude/
philosophy-conflict-resolution-rule`, still unlanded as a branch but
folded forward here as the intent specified) carries forward unrestated
once, immediately followed by a new third-form paragraph,
append-with-positional-note, describing the method part 1 just used:
preserves content while abandoning position, records that position as
prose naming an anchor rather than an index, and states a list's
self-containment rather than renumbering it to show the boundary.

**Against the baseline the intent stated:** `git diff 031bbe6 HEAD --
CHANGELOG.md | grep '^-' | grep -v '^---'` is empty — zero pre-existing
lines lost. `PHILOSOPHY.md`'s two existing forms are present exactly
once, byte-identical to `claude/philosophy-conflict-resolution-rule`'s
own wording, not rewritten to accommodate the third. Full `qt_shell.py
--render-check` sweep: exit 0, no failures.

**Scope note, the one deviation from a literal reading of the intent:**
the intent said this task's entire deliverable is "two `CHANGELOG.md`
entries and one `PHILOSOPHY.md` addition" — that undercounted by one; a
`CHANGELOG.md` entry describing the build (this one) is also required by
this project's own intent/build convention, which the intent entry
itself follows for every other piece of work in this file. Recording it
here rather than treating it as silently implied.

### Record intent: append-with-positional-note conflict resolution, landed and generalized

Own work directly against `main` (not a feature branch — this task's
entire deliverable is documentation-shaped: two `CHANGELOG.md` entries
and one `PHILOSOPHY.md` addition, plus landing one already-built
branch). No code changes planned outside `CHANGELOG.md` and
`PHILOSOPHY.md`; the Pi is not touched.

**Background.** Landing `claude/white-level-constant-consolidation`
produces a `CHANGELOG.md` merge conflict that a prior session correctly
flagged and left unresolved rather than risk corrupting it: the
conflict splits into two disjoint regions because
`claude/keep-raw-images-scope-fix`'s and
`claude/white-level-constant-consolidation`'s "Record intent" entries
coincidentally share one boilerplate heading, `**Investigation, reported
before any code change, per direct instruction:**`. Git's 3-way merge
locks onto that identical line as a synchronization point and
interleaves the two entries' numbered investigation lists around it;
naively resolving each region independently would misattribute
`white-level`'s own numbered list to a heading that isn't its own, and
sandwich the entire unrelated `hdr-merge-verification` entry between the
two halves of what should be one continuous section. That is exactly
the case `PHILOSOPHY.md`'s never-edit rule exists to catch — the
resolution needs to be a deliberate act, not a merge strategy.

**Plan, part 1 — resolve the flagged conflict.** Land
`claude/white-level-constant-consolidation`'s `CHANGELOG.md` content by
appending it, in full and unaltered, to the end of the existing
`## 2026-08-03` section, prefaced by a positional note (ordinary text,
not a merge instruction) recording where it originally sat: the top of
the document, on its own branch, directly preceding `## 2026-08-02`'s
"Record build: HANDOFF.md restructure, part 1" entry (the most recent
entry that existed when that branch forked from `main`), before its two
siblings independently opened the same date heading and landed first.
The note also states explicitly that the entry's own numbered
investigation list is self-contained to it — not a continuation of, and
not continued by, any list elsewhere in the document — since relabeling
the list to make that visually obvious would itself be an edit to
existing entry text, which this task's own instruction rules out.
`HANDOFF.md`'s conflict is a plain, single-region append (verified by
inspection, not assumed) and is resolved normally, with no positional
note — it describes the present, not a history, so there is no
position to preserve.

**Plan, part 2 — generalize the method.** `claude/philosophy-conflict-
resolution-rule` (not yet landed, not one of the "other three outstanding
branches" this task is told to leave alone) added a `PHILOSOPHY.md` rule
covering two conflict-resolution forms: interleaving (preserves
everything in place, not an edit) and superseding (replaces a claim with
a new entry pointing at the old one). This task folds that rule's
existing two-form text forward as-is and adds a third form,
append-with-positional-note, matching the method used in part 1: it
preserves content while explicitly abandoning position and recording
that position as text, for conflicts about arrangement rather than
content. Its pass condition is stated in the same terms as the existing
rule (every pre-existing entry present, byte-identical, still its own
entry) plus the two requirements part 1 above needed in practice: name
an anchor, not an index, and state a list's self-containment rather than
renumber it.

**Verification planned:** `git diff <pre-merge-main> HEAD -- CHANGELOG.md
| grep '^-' | grep -v '^---'` must be empty — proof that no pre-existing
entry lost a byte. `PHILOSOPHY.md`'s two existing forms carry forward
unrestated once and are not rewritten to accommodate the third.

## 2026-08-03

### Record build: stale help text fixed, orphaned preview .jpgs cleaned up

Built to the intent recorded below. No deviation.

**Item 4.** `--delete-raw-on-success`'s `help=` string (the named target)
now reads "delete this capture's own raw frames (and their preview
.jpgs) only... never the averaged/merged intermediates
(master_N.tif/hdr_linear.tif/single_master.tif)...". As the intent
specified, `process()`'s own docstring, its inline comment above the
deletion loop, and the runtime `correction_status["raw_discard_reason"]`
string were all deliberately left untouched — they describe this
branch's actual current behavior (deletion scope unchanged, per
instruction), and changing them would have made them false here
specifically. The cross-branch dependency this creates (this branch's
docs now describe a contract only the sibling `claude/keep-raw-images-
scope-fix` branch's code fulfills) is unchanged from what the intent
already flagged.

**Item 5.** The deletion loop's single `for f in raw_files +
master_files` became two loops: `raw_files` (each raw's own
`.with_suffix(".jpg")` sibling checked and unlinked alongside it) and
`master_files` (unchanged, verbatim). Net files deleted when Keep RAW
Images is off and no preview `.jpg`s exist (e.g. `FakeCamera`, or a
session with no real hardware previews) is identical to before; on real
hardware, each raw's own preview now goes with it.

**Verification, as honestly as the intent asked for:** `python3 -m
py_compile` passes. `hdr_from_session.py` has no non-stdlib dependencies
and `--help` was actually run, confirming the corrected help text end to
end, not just present in source. The jpg-cleanup logic was reviewed
statically (confirmed `.with_suffix(".jpg")` correctly maps e.g.
`1_frame_0000.dng` -> `1_frame_0000.jpg`, matching the real filename
convention `Picamera2Camera._save_still_request` writes, and that the
check-then-unlink is scoped strictly to `raw_files`, never
`master_files`) — no placeholder files were created to exercise it live,
consistent with this same session's own standard from the immediately
preceding task. No existing user data was touched, migrated, or deleted.
No re-run, no reported numbers — real verification is the user's, on the
Pi.

### Record intent: stale help text fixed, orphaned preview .jpgs cleaned up

Own branch off `main`: `claude/keep-raw-images-scope-fix-cleanup`. Fifth
sibling to `claude/hdr-merge-verification-w7sb22`, `claude/frame-average-
sidecar-wiring`, `claude/white-level-constant-consolidation`, and
`claude/keep-raw-images-scope-fix` (all pushed, none merged) — not
stacked on any of them. Repo-only: the Pi is unreachable, no verification
runs, no synthetic data, no existing user data touched.

**Branch-sequencing note, surfaced before any change was made:** the
stale `--delete-raw-on-success` help text this task names was already
fixed on the sibling `claude/keep-raw-images-scope-fix` branch, as part
of that task's own deletion-scope narrowing. Because every task branches
fresh off `main` and none of these branches are merged yet, this branch
does not have that fix either, so the help text genuinely is stale here
too. Fixing only the help text on THIS branch, without that branch's
actual code change, means: if this branch merges alone, the docs would
promise raws-only deletion while the code here still deletes
`master_files` too — a worse mismatch than today's (today both are at
least consistently wrong). These two branches need to land together, not
this one alone. Proceeding per direct instruction, with this flagged.


**Investigation, reported before any code change, per direct
instruction:**

1. **Audit for other behaviour-derived records.** Checked every real
   (non-`render_check`-scratch) deletion site against its own contract:
   `provenance.Session.clear()`/`close()` match their docstrings exactly;
   `archive_session_raws` mirrors `hdr_from_session.py`'s own
   `archive_raws()` correctly; the flat-library replace-on-capture
   matches its documented "one standing set, replaced outright" design;
   `plane_cache.clean_cache()` is contract-first, already backed by real
   hardware measurement. No other `help=` string in the repo shows the
   same drift as `--delete-raw-on-success` did. **One further, real
   finding**: `qt_shell.py:_open_gallery_browser`'s own comment claims
   "it is modal... so it cannot race a capture in progress either way" —
   this conflates a modal GUI dialog (blocks other Qt actions) with a
   background worker thread (auto-process's deletion, which is
   deliberately NOT blocked by the Qt event loop, that being the whole
   point of running it on a worker thread). `_open_processing_wizard`,
   right next to it, correctly guards with `if self._capturing: return`;
   `_open_gallery_browser` doesn't, and its comment asserts a safety
   property the code doesn't actually deliver — same category as the two
   named in this task: written to match a belief about the code, not
   checked against the real concurrency contract. Beyond this, **no**
   broader pattern found — not manufactured, and the audit was scoped to
   deletion/retention-adjacent code, not an exhaustive line-by-line pass.
2. **Why the deliverable can't self-report retention (design only).**
   Design A (defer the embed until retention is settled): reorder
   `process()` so deletion runs before `debayer.py`, which then embeds a
   confirmed fact. Real conflict: DNG export's non-merge path needs an
   actual raw file (`raw_files[0]`) present at export time, so deletion
   can't simply move earlier without restructuring that feature too;
   also requires teaching `debayer.py` (a second real caller,
   `process_wizard.py`, which never deletes anything) a new retention
   concept. Design B (write the decision before deletion, deletion
   honours it): cheap, no reordering — `a.delete_raw_on_success` is
   already known before any subprocess runs. But it embeds intent, not a
   confirmed outcome — if deletion later fails or is skipped, the file's
   own claim would be wrong, a smaller instance of the exact "recorded
   from expectation, not fact" pattern item 1 audits for. A naming
   refinement (`raw_deletion_planned` vs. a `_confirmed` field reserved
   for `session.json`) reduces but doesn't remove that risk. Decision is
   explicitly the user's, not made here.
3. **The read-during-deletion race (design only).** Concrete sequence:
   auto-process starts on a worker thread, `self._capturing` stays
   `True` for its whole duration; `_open_processing_wizard` is correctly
   gated and can't race it; `_open_gallery_browser` is not gated at all,
   so opening Gallery mid-process can list a file and then fail to open
   it moments later (TOCTOU) if the worker thread's deletion lands in
   between — extends to preview `.jpg`s after item 5. Guard sketched, not
   built: simplest is the same `if self._capturing: return` check
   `_open_processing_wizard` already uses (coarse — blocks browsing any
   session while any capture anywhere is processing); a finer guard
   would compare against the already-tracked
   `self._last_process_session_dir`/`_last_process_index` and only
   block/warn on the specific in-flight item.

**Plan for items 4-5 (the only code change in this task):**
4. Fix `--delete-raw-on-success`'s `help=` string (the exact location
   named) to describe the corrected contract (raws + their preview
   `.jpg`s only, never the averaged/merged intermediates). Deliberately
   NOT touched: `process()`'s own docstring and its inline comment above
   the deletion loop, and the runtime `correction_status["raw_discard_
   reason"]` string — all three describe what this branch's code
   *actually does right now* (still `raw_files + master_files`, deletion
   scope untouched per instruction), so changing them would make them
   false on this branch specifically, which is worse than leaving them
   accurate-but-pending-the-other-fix.
5. `frames_for()` only globs the raw extension, so each raw frame's own
   preview `.jpg` (written only by `Picamera2Camera`; `FakeCamera` never
   produces one) is never cleaned up by any existing path and
   accumulates on every real-hardware capture regardless of the setting.
   The deletion loop's `raw_files` iteration gains a same-stem
   `.with_suffix(".jpg")` check-and-unlink, scoped exactly to the raw
   files this run selected (not a second glob) — removed when the raw
   is, kept when the raw is kept, matching the setting's own name.
   `master_files`' own deletion loop is untouched (split into its own
   loop so the jpg-cleanup can't accidentally apply to non-raw files).

**Baseline:** `hdr_from_session.py` (this branch, off `main`) is 431
lines; `--delete-raw-on-success`'s help string still reads "raw frames +
linear master"; no preview-`.jpg` cleanup exists anywhere in the repo.

**Verification, stated honestly:** no real bracket/session data exists
in this checkout, none fabricated, no existing user data touched.
`python3 -m py_compile` plus real argument-parsing (`--help`) will be
used to confirm the help text change, since `hdr_from_session.py` has no
non-stdlib dependencies. The jpg-cleanup logic itself will be verified by
static/source-level review only (confirming the code derives the sibling
path and unlinks it conditionally) — consistent with this same session's
own prior call, on the immediately preceding task, not to create even
placeholder on-disk files to exercise deletion-safety code; no reason to
apply a different standard here. `qt_shell.py`'s own `_open_gallery_
browser` finding is reported only — no code changed there, per
instruction (design only for items 2-3). No re-run, no reported numbers
— real verification is the user's, on the Pi.


**Positional note.** The entries directly below (`Record build: Gallery
race comment corrected to a stated contract` and `Record intent: Gallery
race comment corrected to a stated contract`) are appended here, in full
and unaltered, from `claude/gallery-race-comment-fix`. On that branch
they sat at the top of this same `## 2026-08-03` section, directly above
`claude/keep-raw-images-scope-fix-cleanup`'s entries (already landed,
above) — both branches' "Record intent" entries happen to share the
identical boilerplate heading `**Investigation, reported before any code
change, per direct instruction:**`, the same collision this section's
own earlier positional note (above `### Record build: Keep RAW Images
narrowed to raws only`) already describes and resolves the same way.
Each entry's own numbered investigation list is self-contained to it —
not a continuation of, and not continued by, the other.

### Record build: Gallery race comment corrected to a stated contract

Built to the intent recorded below. No deviation.

`_open_gallery_browser`'s comment (`qt_shell.py:5445`) no longer claims
"it is modal... so it cannot race a capture in progress" — replaced with
a `# CAVEAT:` block (PHILOSOPHY.md's convention for a durable fact about
a specific line) stating plainly: unguarded against `self._capturing`,
can race the auto-process worker thread's own deletion loop (raws +
their preview `.jpg`s, as of the now-merged `claude/keep-raw-images-
scope-fix`/`-cleanup` work), TOCTOU on listing-then-open, being modal
only blocks other GUI actions and does not block a background worker
thread by design. Points to this entry for the full concurrency contract
rather than restating it inline. No guard added — per instruction, that
choice (coarse `self._capturing` reuse vs. a finer per-capture check,
both already on record) is the user's.

**Verification, as honestly as the intent asked for:** `python3 -m
py_compile qt_shell.py` passes — the only check possible here, no
PyQt6/numpy in this sandbox for a live Qt exercise. No real bracket/
session data exists in this checkout, none fabricated, no existing user
data touched. No re-run, no reported numbers — real verification is the
user's, on the Pi.

### Record intent: Gallery race comment corrected to a stated contract

Own branch off the updated `main`: `claude/gallery-race-comment-fix`.
`main` now includes `claude/keep-raw-images-scope-fix` (merged via PR #9,
commit `031bbe6`); `claude/keep-raw-images-scope-fix-cleanup` was rebased
onto the result and force-pushed — see this session's own branch-handling
report (delivered before this task's investigation began) for the full
resolution, including how the `CHANGELOG.md`/`HANDOFF.md`/`hdr_from_
session.py` conflicts were combined rather than picked from one side.
Repo-only: the Pi is unreachable, no verification runs, no synthetic
data, no existing user data touched.


**Investigation, reported before any code change, per direct
instruction:**

1. **Design A cost, re-tested.** Walked `process()` on the current
   (deletion-scope-fixed) code: `debayer.py`'s input is always
   `hdr_linear.tif`/`single_master.tif` (a merged/averaged file), never a
   raw or a preview `.jpg` — confirmed by reading both `db = [...]`
   construction sites directly (lines 181, 209). The only step anywhere
   in `process()` reading a raw file's *content* is DNG export's non-
   merge path, `src = raw_files[0]` (line 272), which already runs after
   `debayer.py` today with no dependency on `debayer.py`'s own output.
   Deletion itself needs raw *paths* only, already known well before any
   of this. **Revised cost estimate**: under `DNG export -> delete raws
   -> debayer -> embed confirmed retention`, nothing between DNG export
   and `debayer` reads a raw or preview jpg — the reordering is a cheap
   step-sequence swap (DNG export and `debayer` trade relative position;
   the format-existence-validation block, which reads `debayer.py`'s own
   outputs, moves with it), no restructuring of any subprocess-invoking
   block's own logic. What still stands from the original estimate:
   `debayer.py` needs a new flag/field to accept and embed the confirmed
   retention fact, and `process_wizard.py` (a second real caller, never
   deletes anything) needs a sensible default for it.
2. **Design C, evaluated.** The referenced primitive is `calibrate.py`'s
   calibration store (`save_calibration`/`current_calibration`):
   objective-keyed, append-only list per key, each entry carrying its own
   `entry_id` + a `supersedes` pointer to the prior entry, current state
   always the list's last element. Real and working, but does not fit
   this use case unmodified: its key ("objective") is small, stable, and
   GUI-selected; a retention record needs a key scoped to one specific
   capture, and nothing in `hdr_from_session.py` computes one today (no
   `pixel_sha256` of `final.tif` anywhere in this file, unlike the
   green-plane/`plane_cache.py` side of the app). It also lives in its
   own standalone JSON file, never amended onto an image file — matching
   "sidecar," not "amended into the deliverable." The generalized version
   (`json_store.py`, HANDOFF's own "provenance.py phase 2") isn't built
   yet, so using this pattern now means building that generalization or
   accepting a second, parallel implementation. Cost: define a stable
   per-capture key (cheapest: reuse session timestamp + capture index,
   already on hand; no new hashing required). No reordering of
   `process()` at all — this writes after deletion, exactly where
   `correction_status` already gets written today.
   **Discovered sub-cost**: "amended into the deliverable" and "sidecar"
   are not equally cheap. Amending `final.tif` itself means reopening and
   fully rewriting the TIFF after `debayer.py` already wrote it
   (`tifffile` has no incremental-append story) — changes the file's own
   bytes/hash after the fact and reopens a race window of exactly the
   kind this whole investigation is about. Sidecar avoids this.
   **Discoverability**: a reader with only `final.tif` cannot find a
   sidecar record unless `final.tif`'s own embedded JSON (written once,
   at original write time) carries a stable pointer to the key, and even
   then needs filesystem access the file won't carry with it once it
   leaves the project's own directory tree (e.g. via `publish.py`'s own
   package model).
   **Three designs, compared on one axis — can the artifact's own claim
   ever be false?** Design A (embed after deletion): true by construction
   at write time, only stale from something out-of-band afterward.
   Design B (embed before deletion, deletion honours it): can be false
   the moment deletion fails, is interrupted, or is skipped, even under
   otherwise-normal operation — a real, structural gap. Design C
   (sidecar, written after deletion): the artifact's own bytes never
   carry a retention claim at all, so nothing there can ever be false;
   the external record inherits Design A's "true at write time" property
   but has the discoverability weakness above, and — being append-only —
   a later-discovered inconsistency can be appended as a correcting
   entry, something neither A's nor B's baked-in file claim can ever do
   once written. Design only, no implementation, no decision made.
3. **The Gallery contract, stated.** `_open_gallery_browser`
   (`qt_shell.py:5445`) must not permit a read (listing or open) of any
   capture's files while that specific capture's own `process()` is in
   flight on the auto-process/manual-process worker thread, because that
   worker thread's own last step deletes files (raws, and now their
   preview `.jpg`s) as part of the same run. A guard would have to hold
   for the entire duration from when that worker thread starts until
   `_on_process_finished` resets state, not a single point-in-time check
   when Gallery happens to open — the race window is the whole processing
   span, not an instant — evaluated either coarsely (any processing at
   all) or against the specific in-flight capture, matching the coarse-
   vs-fine choice already on record from the prior task. Design only, no
   guard built.

**Plan for item 4 (the only code change in this task):** replace
`_open_gallery_browser`'s incorrect "it is modal... so it cannot race a
capture in progress" reasoning with an accurate `# CAVEAT:` comment
(PHILOSOPHY.md's convention for a durable fact about a specific line)
stating: unguarded, can race the auto-process worker thread's deletion
loop, TOCTOU on listing-then-open. No guard added — the coarse-versus-
fine choice is the user's.

**Baseline:** `qt_shell.py:_open_gallery_browser`'s comment currently
reads "Independent of self._capturing -- it only reads the filesystem,
and it is modal (exec()) like Process/Archive above, so it cannot race a
capture in progress either way" — no `# CAVEAT:` marker present.

**Verification, stated honestly:** no real bracket/session data exists in
this checkout, none fabricated, no existing user data touched.
`python3 -m py_compile qt_shell.py` is the only check possible — this
sandbox has no PyQt6/numpy for a live Qt exercise, same constraint every
task on this repo has hit. No re-run, no reported numbers — real
verification is the user's, on the Pi.



### Record build: Keep RAW Images narrowed to raws only

Built to the intent recorded below. No deviation.

**Against the counted baseline** (deletion loop `for f in raw_files +
master_files`): `hdr_from_session.py:process()`'s deletion loop is now
`for f in raw_files` — `master_files` stays defined (still used a few
lines earlier for the DNG-merge export) but is structurally excluded
from deletion, not just conditionally skipped. The surrounding doc-
comment and `--delete-raw-on-success`'s CLI help text are corrected to
state the real, narrowed scope, including the "if disk pressure is ever
a real problem, that needs its own named setting" note the intent
specified.

`correction_status` keeps `raw_discarded`/`raw_discard_reason` (now
finally accurate) with the reason text's false "and the linear master"
clause removed, and gains two unconditional new keys —
`derived_outputs_discarded` (always `False`) and `derived_outputs_note`
(a fixed explanatory string) — matching `frame_average.py`'s/
`hdr_merge.py`'s explicit-value-plus-note convention exactly, so a
`session.json` reader never has to infer "were intermediates kept?" from
`raw_discarded` alone.

`qt_shell.py`'s `render_check()` Keep RAW Images block (~7476-7516) is
corrected to match: the `single_master.tif`-must-be-deleted assertion is
flipped to must-survive; new assertions cover the corrected reason text
and both new `derived_outputs_*` fields; the block's own comment and
final print statement restate the corrected behavior instead of the old
bug.

**Verification, as honestly as the intent asked for:** `python3 -m
py_compile` passes for both files. `hdr_from_session.py` has no non-
stdlib dependencies and was statically checked for real: confirmed the
old `raw_files + master_files` deletion expression is gone, the new
`for f in raw_files:` loop is present, both new provenance keys exist,
and the false "and the linear master" string no longer appears anywhere
in the file. A live functional exercise (creating placeholder files and
running the real deletion loop against them) was deliberately not done,
even with empty/non-image files — this task's own repeated "no synthetic
data" instruction was read as covering any fabricated on-disk stand-in
for what this code touches, not just realistic bracket pixel data, given
the task is specifically about file-deletion safety. `qt_shell.py`'s
`render_check()` edits were reviewed by inspection only, not run — this
sandbox has no PyQt6/numpy for a live Qt self-check either way, same
constraint every task on this repo has hit. No existing user data was
touched, migrated, or deleted by this work — it is a code-only change.
No re-run, no reported numbers — real verification is the user's, on the
Pi.

### Record intent: Keep RAW Images narrowed to raws only

Own branch off `main`: `claude/keep-raw-images-scope-fix`. Fourth sibling
to `claude/hdr-merge-verification-w7sb22`, `claude/frame-average-sidecar-
wiring`, and `claude/white-level-constant-consolidation` (all pushed and
done) — not stacked on any of them. Repo-only: the Pi holding `~/captures`
is unreachable, no verification runs, no synthetic data. No existing user
data is touched or migrated — this is a code-only fix.

**Background.** "Keep RAW Images" off, found during the frame-average
investigation, deletes `master_N.tif` (and `hdr_linear.tif`/
`single_master.tif`) — the averaged/merged intermediates — not only the
raw frames the setting names. A user leaving it off is consenting to
discard raws, not averaged masters built from a multi-frame bracket.

**Investigation, reported before any code change, per direct
instruction:**

1. **Full deletion path.** One call site, `hdr_from_session.py:process()`
   lines 297-303, gated on `getattr(a, "delete_raw_on_success", False)`.
   Deletes `raw_files + master_files`: `raw_files` is every individual
   raw capture (`<level>_frame_NNNN.<ext>`, HDR: all levels' science +
   dark; science/snap: sci + dark); `master_files` is the averaged/merged
   intermediates — HDR: `master_1.tif`..`master_N.tif` + `hdr_linear.tif`;
   science/snap: `single_master.tif`. NOT touched: `.meta.json` sidecars,
   `session.json`, the flat library, `final.tif`/`final_display.*`, DNG/
   JPG exports. Also not touched (separate, minor, not this bug): per-
   frame preview `.jpg`s, orphaned regardless of this setting since
   `frames_for()` only globs the raw extension. **Adjacent, NOT fixed
   here**: `archive_raws()` (`hdr_from_session.py:338-371`, a different
   named feature, "Archive raws") globs `*.<raw-ext>` — off-rig
   (`--raw-ext tif`) this would also match every processed `.tif` output
   in the directory, since raws and outputs share an extension there.
   Unreachable via the GUI today (`qt_shell.py:5105` always passes
   `--keep-raws`); only via a direct CLI run with `--archive-raws`/`y`
   plus `--raw-ext tif`. Different setting, out of scope, reported only.
2. **When it fires.** Per capture, synchronously, the last step inside
   `process()`, after every subprocess that capture's own run needed has
   already finished (blocking calls) — safe within one run. Cross-
   consumer risk is real but narrow: no guard found preventing Gallery or
   an independently-launched `process_wizard.py` from reading the same
   files concurrently with, or after, an auto-process worker thread's
   deletion; the only guard (`self._capturing`) covers re-entrant
   capture/process on the same window. A second `process()` attempt on a
   capture whose raws are already gone fails loudly (`sys.exit`), never
   silently — partial mitigation only.
3. **Label/docs.** Checkbox text (`qt_shell.py:1870-1871`): "Keep RAW
   Images (applies to captures from now on, not retroactively)". Pref
   key `keep_raw_images`, default `True`, no tooltip on the checkbox
   itself. The ONLY existing prose describing its scope is a neighboring
   checkbox's tooltip (`qt_shell.py:1910-1913`, DNG export): "governs the
   session's own working raw frames" — the app's own docs already
   describe raws-only; the code disagreed with its own docs, not the
   other way round. `hdr_from_session.py`'s own `--delete-raw-on-success`
   CLI help (lines 395-398) is honest about the current bug ("+ linear
   master").
4. **Is deletion recorded?** Yes, but only via the GUI flow:
   `correction_status["raw_discarded"]`/`["raw_discard_reason"]`
   (`process()` 311-317) printed as `CORRECTION_STATUS_JSON:`, parsed and
   merged onto the capture's `session.json` entry by `qt_shell.py:
   _record_correction_status` (5517-5541, `cap.update(...)`). A direct
   manual CLI run prints the same line but nothing persists it. Today's
   reason text already honestly says "raw frames and the linear master
   were deleted" — the record isn't silently wrong, the setting's NAME
   is what disagrees with it. Nothing is ever recorded inside `final.tif`/
   `final_display.*`'s own embedded provenance — `debayer.py` writes
   them BEFORE the deletion step runs, so they structurally can't know.
   **Found**: `qt_shell.py`'s own `render_check()` (~7494-7534) currently
   asserts the buggy behavior as correct (`assert not (kr_session.dir /
   "single_master.tif").exists(), "Keep RAW Images off must delete the
   linear master too"`) — must flip as part of this fix or it fails (or
   keeps enshrining the bug).

**Plan for items 5-6 (the only code change in this task):**
5. `process()`'s deletion loop changes from `for f in raw_files +
   master_files` to `for f in raw_files` — `master_files` stays defined
   (still used for the DNG-merge export a few lines earlier) but is
   removed from the delete set entirely. `--delete-raw-on-success`'s help
   text and the surrounding doc-comment are corrected to match. No new
   setting for derived-output deletion is added — that decision is the
   user's, not built here.
6. `correction_status` keeps `raw_discarded`/`raw_discard_reason`
   (finally accurate once scope narrows) but the reason text drops "and
   the linear master"; two new keys, matching `frame_average.py`/
   `hdr_merge.py`'s explicit-value-plus-note convention (e.g.
   `white_level_source`, `black_note`): `derived_outputs_discarded`
   (always `False` post-fix — explicit, never omitted) and
   `derived_outputs_note` (a fixed explanatory string: Keep RAW Images
   only ever discards this capture's own raw frames; averaged/merged
   intermediates are retained regardless of this setting). `qt_shell.py`'s
   `render_check()` assertions are corrected to match the new behavior
   (single_master.tif must now SURVIVE Keep RAW off; new fields
   asserted) — edited for correctness by inspection, not run, since this
   sandbox has no PyQt6/numpy for a live Qt check.

**Baseline:** `hdr_from_session.py` is 431 lines (pre-white_level-
consolidation baseline; this branch is off `main`, not stacked on that
work). Deletion set structurally shrinks by exactly `len(master_files)`
per call: HDR = master_1..N.tif + hdr_linear.tif (N+1 fewer files
deleted); science/snap = single_master.tif (1 fewer file deleted).

**Verification, stated honestly:** no real bracket/session data exists in
this checkout, none fabricated, no existing user data touched. No
re-run, no reported numbers — real verification is the user's, on the
Pi.

### Record build: hdr_merge.py provenance-integrity fixes (six defects)

Built to the intent recorded below. No deviation in scope or approach;
one open question the intent left explicit is now answered by reading
code rather than assumed, recorded as `DISCOVERED:` below.

**Against the counted baseline (382 lines, `__version__ "1.0"`):**
`hdr_merge.py` is now 488 lines (+106), `__version__ "1.1"`. Merge math
(`E = sum_i w_i*(v_i/white - black)/t_i / sum_i w_i`) is byte-for-byte
unchanged — checked against the diff, not just asserted.

1. **white_level**: no code default changed (the script's own fallback
   was always the honest `dtype_max`, never `65520` — that number only
   ever arrived from a caller's `--white-level` flag). The fix here is
   documentary: new `--white-level-source` records how a value was
   determined (`null` if omitted), and a new `white_level_gain_dependency`
   field states, whenever `analogue_gain` is `null`, that the value is
   only valid for this bracket's gain. Setting `--white-level 64200` for
   the real bracket is an invocation-time action on the Pi, not a code
   change, matching what the intent already said it would be.
2. `metadata=None` added to the `imwrite` call. `_assert_single_
   description_tag()` re-opens the just-written file and hard-fails if
   TIFF tag 270 isn't exactly one — proves the fix against the actual
   bytes on disk, not just against the code that wrote them.
3. `args.output` is now resolved to an absolute path
   (`Path(args.output).resolve()`) before being recorded, rather than the
   raw CLI string (which defaults to `"hdr_linear.tif"` if `-o` is
   omitted and doesn't track where the file is later moved to).
4. New `--channel-layout {mosaic,mono}` / `--cfa-pattern` flags feed
   `geometry.channel_layout` / `geometry.cfa_pattern`, both `null` unless
   the caller states them — deliberately not defaulted to `"mosaic"` even
   though that's this tool's typical input, since the file's own tags
   structurally can't prove which one it is; that was the defect.
5. New `--black-note` flag feeds a `black_note` field, `null` unless
   supplied.
6. **DISCOVERED**: the intent entry left open whether defect 6's
   propagation gap belongs to a script inside this repo or a Pi-only
   acquisition script outside it. Answered by reading the actual code:
   `camera_backend.py` and `provenance.py` (both in this repo) already
   capture and persist `AnalogueGain`/`ExposureTime` per frame into each
   capture's own `.meta.json` sidecar (`record_capture`/`record_burst`/
   `record_hdr`) — the acquisition side is real, in-repo, and already
   working. The actual gap is `frame_average.py` (also in this repo): its
   own provenance dict (`frame_average.py` ~321-412) has no gain/sensor-
   mode/capture-time fields at all and never reads those sidecars. So the
   propagation fix is a real, buildable, in-repo change — but per
   instruction it stays the user's to make, not built in this pass. New
   `try_read_embedded_capture_meta()` gives `hdr_merge.py` the read side
   ready now: it looks for `analogue_gain`/`sensor_mode`/
   `capture_time_utc` in a master's own embedded JSON and records `null`
   per-key when absent, never omitted — the day `frame_average.py` starts
   writing those three keys, every exposure record here picks them up
   automatically with no further change to this file.

**Verification, as honestly as the intent asked for:** `python3 -m
py_compile hdr_merge.py` passes — the only check possible here, since
`numpy`/`tifffile` aren't installed in this checkout (a runtime smoke
test wasn't attempted regardless; the intent already ruled out
fabricating bracket data to exercise this). Real verification — the
merge actually running, the saturation-rejected count going nonzero, the
real embedded JSON, tag 270 confirmed against a real file — is left
entirely to the user's own run on the Pi, not attempted or reported here.

### Record intent: hdr_merge.py provenance-integrity fixes (six defects)

Own branch off `main`: `claude/hdr-merge-verification-w7sb22`. Prompted by
a hand audit of a real 5-frame exposure bracket run (`master_1..5.tif`,
uint16, 12.49ms doubling to 199.85ms) merged on the Pi to
`~/captures/final.tif`, cross-checked against the embedded provenance JSON
and against a companion `camera_backend.py` audit running concurrently on
a separate, deliberately read-only branch in this same repo. That audit
raised two process points, both accepted: (1) no synthetic bracket is
being fabricated to exercise this fix — the real masters exist only on
the Pi, and a synthetic run would only prove the code agrees with the
numbers used to derive the fix, which is not evidence; a passing synthetic
merge dropped into a captures-shaped path would also be a provenance
contamination risk in its own right. (2) this stays off the audit branch
— same repo, different task — so the audit branch doesn't accumulate
unrelated changes.

**Six defects, in priority order, each against a measured baseline —**
merge math (`E = sum_i w_i*(v_i/white - black)/t_i / sum_i w_i`) is
explicitly unchanged by all six:

1. **`white_level` wrong; saturation rejection never fires.** This run
   used `white_level=65520.0`, `sat_frac=0.95` → cutoff 62244. Measured
   directly off the bracket: the frame5/frame4 median ratio holds at 2.00
   through a frame4 value of 30500, then breaks — 1.932 over
   30500-32500, falling monotonically to 1.37 by 46500. The true ceiling
   is ~61000 in frame5, below the 62244 cutoff, so no sample is ever
   rejected and roughly 48% of the frame merges biased-low samples at
   real weight. Fix is an invocation-time value (`--white-level 64200`,
   landing the cutoff at 61000) — not a new hardcoded default, since it's
   only valid for this bracket's analogue gain, which capture metadata
   can't currently recover (see defect 6).
2. **Two ImageDescription tags (TIFF code 270) written to one IFD.**
   `tifffile.imwrite` writes both the explicit `description=` provenance
   JSON and its own default `metadata={}` shape JSON into tag 270 —
   two tags, undefined resolution order per reader, provenance silently
   droppable.
3. **`prov["output"]["path"]` isn't normalized.** It records whatever
   string `-o` happened to be at write time (default `"hdr_linear.tif"`
   if omitted) with no resolution to where the bytes actually landed —
   this run's file sits at `~/captures/final.tif` but its own embedded
   record still names `hdr_linear.tif`.
4. **`geometry.channels: 1` doesn't say mosaic vs. mono.**
   `PhotometricInterpretation` is MINISBLACK with no CFA tag, so nothing
   in the file distinguishes a raw Bayer mosaic from a true mono/already-
   extracted plane.
5. **`black: 0.0` can't distinguish "verified no pedestal" from "pedestal
   handling never implemented."** 0.0 is correct for this run (frame1 min
   is 297, far below where a surviving 12-bit pedestal would leave a
   floor) but the field reads identically either way.
6. **Capture settings don't propagate.** Exposure records carry
   `t_source` but never gain, sensor mode, or real per-frame capture
   time — upstream masters (`frame_average.py` output) don't embed
   capture metadata yet, so `hdr_merge.py` has structurally no way to
   record what gain a bracket was shot at, even in principle. **Scope
   check in progress**: whether the propagation fix belongs in this repo
   (`frame_average.py`) or points at a Pi-only acquisition script outside
   it is being confirmed before this defect's plan is finalized below.

**Baseline:** `hdr_merge.py` is 382 lines, `__version__ = "1.0"`, zero
code changes yet on this branch.

**Plan:** bump `__version__` to `"1.1"` so a file produced by the patched
script is structurally distinguishable from the `v1.0` master already on
disk (worth confirming separately, outside this fix, whether the checked-
in copy this session inherited is actually the same version that produced
today's `final.tif` — flagged back to the camera_backend audit rather than
assumed). Add `try_read_embedded_capture_meta()` alongside the existing
`try_read_embedded_exposure()`; five new optional CLI flags
(`--white-level-source`, `--analogue-gain`, `--black-note`,
`--channel-layout {mosaic,mono}`, `--cfa-pattern`) that record
operator-supplied context, explicit `null` when omitted, never a silent
guess; `metadata=None` on the `imwrite` call plus a post-write assertion
that exactly one tag 270 exists; resolve `-o` to an absolute path before
recording it. Only `hdr_merge.py` is touched by this pass — defect 6's
write side (wherever it turns out to live) is not built here regardless
of where the scope check above lands; at most this pass documents exactly
what fields/shape `hdr_merge.py` is ready to read.

**Verification, stated honestly up front:** no real bracket exists in
this checkout — the five masters live only on the Pi — so this pass can
only be self-check/code-review verified here (`py_compile` plus a
throwaway smoke test against synthetic arrays in the session scratchpad,
never written under any captures-shaped path, offered only as evidence
the code runs, not that the fix is numerically correct). Real numbers
(above-norm-point count, saturation-rejected count, the real embedded
JSON, tag-270 count) come from a real run on the Pi, done separately by
the user — not attempted or reported from this session.

**Positional note.** The two entries directly below (`Record build:
white_level defaults consolidated to one constant` and `Record intent:
white_level defaults consolidated to one constant`) are appended here,
in full and unaltered, from `claude/white-level-constant-consolidation`.
On that branch they sat at the top of the document, directly preceding
`## 2026-08-02`'s "Record build: HANDOFF.md restructure, part 1" entry
— the most recent entry that existed when the branch forked from
`main`, before its siblings `claude/hdr-merge-verification-w7sb22` and
`claude/keep-raw-images-scope-fix` independently opened this same
`## 2026-08-03` date heading and landed first. The entries' own numbered
investigation list, below, is self-contained to the "Record intent"
entry it appears in — not a continuation of, and not continued by, any
other numbered list in this section.

### Record build: white_level defaults consolidated to one constant

Built to the intent recorded below. No deviation.

**Against the counted baseline** (two independent literals, two Python
types — string `"65520"` in `hdr_from_session.py`, bare int `65520` in
`qt_shell.py`): `hdr_from_session.py` gains `MERGE_WHITE_LEVEL_DEFAULT =
65520` (plain int) right after `__version__`, with the comment the intent
specified (container-range assumption; the August 2026 bracket's real
~61000 ceiling at an unrecorded gain; why that number isn't promoted to
a new default). Its own `--wl` argparse now defaults to the constant
instead of the string literal. `qt_shell.py` gains a guarded import of
`hdr_from_session` (mirroring the existing `_process_wizard`/
`_plane_cache` pattern exactly: nested `try/except ImportError`,
`None` if the sibling file is missing), and its own `--wl` default
becomes `_hdr_from_session.MERGE_WHITE_LEVEL_DEFAULT if _hdr_from_session
else 65520` — the `else 65520` is the documented "sibling script
physically absent" fallback the intent called for, not a second source
of truth for the normal case. `process_wizard.py` untouched, as planned.

**A future grep for `65520` now shows exactly what the intent asked
for**: one real definition (`hdr_from_session.py`'s
`MERGE_WHITE_LEVEL_DEFAULT`), one reference to it, one degrade-path
fallback that's commented as exactly that, and `process_wizard.py`'s own
`DEFAULT_WHITE_LEVEL = 65520.0` sitting apart, unrelated, undisturbed.

**Verification, as honestly as the intent asked for:** `python3 -m
py_compile` passes for both files. `hdr_from_session.py` has zero non-
stdlib dependencies, so — unlike `qt_shell.py`, which needs PyQt6/numpy
this sandbox doesn't have — it could actually be imported and exercised
for real: `import hdr_from_session; hdr_from_session.
MERGE_WHITE_LEVEL_DEFAULT` reads back `65520` (int), `str()` of it is
`"65520"` (byte-identical to the old literal, confirming no downstream
formatting change), and `python3 hdr_from_session.py --help` shows the
`--wl` flag wired correctly end to end. No real bracket/session data
exists in this checkout, none fabricated; no re-run, no reported numbers
— real pipeline verification is the user's to run on the Pi.

### Record intent: white_level defaults consolidated to one constant

Own branch off `main`: `claude/white-level-constant-consolidation`.
Sibling to `claude/hdr-merge-verification-w7sb22` and `claude/frame-
average-sidecar-wiring` (both pushed and done) — not stacked on either.
No bracket/captures data is reachable from this session; none was
fabricated. `frame_average.py`'s averaging behavior is untouched.

**Investigation, reported before any code change, per direct
instruction:**

1. **Can `--sigma-clip` discard unsaturated samples in favor of
   saturated ones?** Yes, structurally possible, confirmed against the
   actual formula (`frame_average.py:242-277`): `mean`/`sd` are computed
   once over ALL frames (single iteration, never refined — matches the
   docstring), so a majority-clipped pixel (identical, zero-spread
   clipped values pulling the pooled mean toward them) can make the
   minority unclipped samples the higher-deviation ones and get them
   rejected instead. Verified numerically (illustrative arithmetic only,
   3 identical 64200s + 58000/59000, not synthetic capture data, no
   files written): at `K=1` the two genuine unclipped samples are
   rejected and the clipped cluster survives; at `K=2`/`K=3` nothing is
   rejected in this scenario. Parameter- and pixel-mix-dependent, not
   guaranteed. **`--sigma-clip` defaults to `None` (off)**, and no
   caller in this repo (`hdr_from_session.py:process()`, the only real
   invocation site) ever passes it — inactive in the actual pipeline
   today, reachable only via a manual direct CLI call.
2. **Are pre-average raws retained?** Yes by default, conditionally.
   Per-level frames: `<level>_frame_NNNN.<ext>` (prefix_template `""`,
   confirmed at `qt_shell.py:4893-4894`), count set by the GUI's own
   `armed["n"]` at capture time (not a fixed constant). `frame_average.py`
   never deletes its inputs. But `hdr_from_session.py:process()` (lines
   283-303) does, when "Keep RAW Images" is off
   (`a.delete_raw_on_success`): every raw frame AND every `master_N.tif`
   plus `hdr_linear.tif` are unlinked once processing succeeds — only
   `final.tif`/`final_display.*`/exports survive. Whether the real
   bracket's masters/raws still exist depends on that session's own
   Keep-RAW setting, which is Pi-side state not visible from this repo.
3. **Monotonic→UTC anchor design** (not implemented): take a paired
   `datetime.now(timezone.utc)` + monotonic (believed `CLOCK_BOOTTIME`,
   to be confirmed on-rig) reading close to where the camera actually
   starts; carry it forward as new top-level `session.json` fields; do
   the `SensorTimestamp` conversion once, upstream, in `provenance.py` at
   sidecar-write time, stamping a real `capture_time_utc` directly into
   each `.meta.json`. `frame_average.py`'s existing `--sidecar-dir`
   wiring would then pick it up automatically via
   `aggregate_capture_field()` with no further change there.

**Plan for item 4 (the only code change in this task):** a new
`MERGE_WHITE_LEVEL_DEFAULT` constant, defined once in `hdr_from_session.py`
(the actual owner of `--wl` and the direct caller of `hdr_merge.py
--white-level`), carrying a comment recording both that `65520` is a
container-range assumption (not a measured sensor value) and that the
one real measurement on record — the August 2026 bracket's frame5/frame4
ratio-break finding — put the true ceiling at ~61000, at an analogue gain
that went unrecorded (`hdr_merge.py`'s own `white_level_gain_dependency`
field). `qt_shell.py:5744` imports it via this file's own established
guarded-import pattern (matching `_process_wizard`/`_plane_cache`:
nested `try/except ImportError`, degrading to a literal fallback — the
existing "sibling script physically missing" case, already otherwise
handled by `_run_process_cmd`'s own `PROCESSOR.exists()` check).
`hdr_from_session.py`'s own `--wl` argparse default switches from the
string literal `"65520"` to this constant; the constant's value (a plain
int, `65520`) stringifies identically (`str(65520) == "65520"`), so
every downstream consumer (the `hdr_merge.py`/`debayer.py` subprocess
calls, the printed stage-summary lines) is byte-for-byte unaffected.
**Not touched**: `process_wizard.py`'s own `DEFAULT_WHITE_LEVEL = 65520.0`
— explicitly out of scope (different codepath, feeds `debayer.py
--assume-linear` only) — and the value itself is not changed to `61000`
anywhere; that number is only valid for one bracket's unrecorded gain,
and hardcoding it as a new blanket default would repeat the exact
mistake this constant's own history already is one instance of.

**Baseline:** `hdr_from_session.py` is 431 lines, `qt_shell.py`'s `--wl`
default is a bare int `65520`, `hdr_from_session.py`'s is the string
`"65520"` — two independent literals, two different Python types, same
numeric value, zero shared source today.

**Verification, stated honestly:** no real bracket/session data exists
in this checkout, none fabricated. `hdr_from_session.py` has no
non-stdlib dependencies, so it can actually be imported and exercised
directly in this sandbox (unlike `qt_shell.py`, which needs PyQt6/numpy
this environment doesn't have) — `python3 -c "import hdr_from_session"`
plus reading back the constant is real verification, not just
`py_compile`. `qt_shell.py` gets `py_compile` only. No re-run, no
reported numbers — real pipeline verification happens on the Pi.

**Positional note.** The two entries directly below (`Record build:
frame_average.py capture-metadata sidecar wiring` and `Record intent:
frame_average.py capture-metadata sidecar wiring`) are appended here, in
full and unaltered, from `claude/frame-average-sidecar-wiring`. On that
branch they sat at the top of the document, directly following this same
`## 2026-08-03` heading — the branch was cut before
`claude/hdr-merge-verification-w7sb22` and `claude/keep-raw-images-
scope-fix` independently opened it and landed first, and before
`claude/white-level-constant-consolidation` (itself already appended
above under its own positional note) landed after them. The entries'
own numbered investigation list, below, is self-contained to the
"Record intent" entry it appears in — not a continuation of, and not
continued by, any other numbered list in this section.

### Record build: frame_average.py capture-metadata sidecar wiring

Built to the intent recorded below. No deviation.

**Against the counted baseline (431 lines, `__version__ "2.1"`):**
`frame_average.py` is now 525 lines (+94), `__version__ "2.2"`.

New: `read_sidecar_meta(sidecar_dir, frame_path)` (locates and parses a
frame's `.meta.json` sidecar by name only, `{}` on anything missing/
unreadable, never raises); `aggregate_capture_field(sidecars, raw_key,
caster)` (single agreed value, or `None` + a note listing every value
seen when the burst disagrees, or `None` + a not-present note when no
sidecar carries the key — three genuinely different outcomes, three
different notes, never collapsed into one generic null); `capture_meta_
for_science()` (wires the two above against the science burst
specifically, returns the three `hdr_merge.py`-shaped keys plus a `note`
sub-object explaining every one). New `--sidecar-dir` flag, `None` by
default (matching every existing invocation's behavior exactly —
`analogue_gain`/`sensor_mode`/`capture_time_utc` all `null` with `"not
present in any input frame's sidecar"` unless a caller opts in). Wired
into `main()` right after `prov["science"]`/`prov["geometry"]` are set,
so the new top-level `analogue_gain`, `sensor_mode`, `capture_time_utc`,
`capture_metadata_note` keys sit next to the run's other top-level
context, matching where `hdr_merge.py` puts its own equivalent fields.

**Verification, as honestly as the intent asked for:** `python3 -m
py_compile frame_average.py` passes. `numpy`/`tifffile` aren't installed
in this checkout, so no full run was possible regardless; a standalone,
file-free re-implementation of `aggregate_capture_field`'s three branches
(agree / disagree / absent) was exercised against hand-built dicts held
only in a throwaway Python process — not sidecar files, not bracket data,
nothing written to disk — to confirm the aggregation rule itself before
trusting it in the real function. Real verification (does a real sidecar
directory actually resolve, does a real disagreeing burst get reported
right) is the user's to run on the Pi, not attempted or reported here.

### Record intent: frame_average.py capture-metadata sidecar wiring

Own branch off `main`: `claude/frame-average-sidecar-wiring`. Separate
from, and not stacked on, `claude/hdr-merge-verification-w7sb22` (pushed
and done) — same repo, different task. No bracket data or captures
directory is reachable from this session (they live on the Pi only); no
synthetic data was fabricated. `hdr_merge.py` is explicitly NOT modified
by this task.

**Investigation, done and reported before any code change, per direct
instruction:**

1. **The `--white-level 65520` caller.** Two, both duplicated, independent
   argparse defaults: `qt_shell.py:5744` (`--wl` default `65520`, the
   GUI's own `main()`) and `hdr_from_session.py:362` (`--wl` default
   `"65520"`, its own standalone default). Both forward through
   `hdr_from_session.py:178` (`hm += ["--white-level", a.wl, ...]`) into
   `hdr_merge.py`. `process_wizard.py:61`'s `DEFAULT_WHITE_LEVEL = 65520.0`
   is the same number but an unrelated codepath (feeds `debayer.py
   --assume-linear` only — confirmed via grep, zero references to
   `hdr_merge`/`hdr_from_session` in that file). No Makefile/shell
   script/config anywhere in the repo carries this constant.
2. **Whether `frame_average.py` averages saturated samples.** By default,
   yes, unconditionally — `average_burst()` with no `--sigma-clip` sums
   every frame's raw value with no proximity-to-ceiling check;
   `dtype_max()` only knows the container max (65535 for uint16), with no
   concept of the sensor's real white level. `--sigma-clip` rejects
   statistical outliers relative to the burst's own mean, which is not
   the same thing as saturation rejection and only incidentally catches
   a clipped frame. No per-frame maximum or saturation count is recorded
   anywhere; the only clip field (`prov["clipping"]`) is computed on the
   post-average, post-correction result against the *container* ceiling
   (65535), not the sensor's true one, so it would essentially never fire
   on real data regardless of per-frame clipping. This is **consistent
   with** (not proof of) the smearing mechanism described in the prior
   task's finding (a 2.00x ratio holding to frame4=30500, breaking to
   1.932, falling to 1.37 by 46500, no pileup, ~48% above the break) —
   averaging a clipped sample with unclipped ones lands the mean between
   them, a soft rolloff rather than a hard cutoff. Not asserted as the
   confirmed cause; no real bracket was available to check directly.
3. **Sidecar wiring gap.** Filename pattern `<prov_dir>/<raw_stem>.
   meta.json` (`provenance.record_capture`/`record_burst`/`record_hdr`).
   JSON keys confirmed identical on both backends (`camera_backend.py`
   `request.get_metadata()` at line 1205, `_fake_metadata()` at
   498-508, both via `_dump_meta`): `ExposureTime`, `AnalogueGain`,
   `DigitalGain`, `ColourGains`, `SensorTimestamp` — real libcamera
   casing. Two gaps that are NOT this task's to fix: `sensor_mode` does
   not exist in this metadata on either backend at all (a
   `camera_backend.py` gap); `SensorTimestamp` is a monotonic hardware
   clock with no recorded epoch, not wall-clock UTC — mapping it directly
   to a field named `capture_time_utc` would be a fabricated value, not a
   real one. `frame_average.py` itself has zero sidecar awareness today
   (confirmed by reading it in full) and, per its own docstring, is a
   generic tool that doesn't import `provenance.py`.

**Plan for the wiring (item 4 only — item 5, saturation rejection, is
explicitly NOT built in this task; see below):** a new `--sidecar-dir DIR`
flag on `frame_average.py`, applied to the science burst's own frames
(the burst that becomes the HDR-level master `hdr_merge.py` reads).
`frame_average.py` stays decoupled from `provenance.py` (no import) but
replicates its known `<stem>.meta.json` naming as a documented contract.
A new aggregation helper reads `AnalogueGain` across the science burst's
sidecars: if every frame that has a sidecar agrees, record that single
value; if they disagree, record the disagreement itself (every observed
value), never silently the first one seen. `sensor_mode` and
`capture_time_utc` are recorded `null` with an explanatory note for the
structural reasons above — not a wiring bug, a real absence. Output key
names match `hdr_merge.py`'s existing `try_read_embedded_capture_meta()`
exactly (`analogue_gain`, `sensor_mode`, `capture_time_utc`, top-level in
the provenance dict) so nothing else has to change once this lands.

**Explicitly deferred, per direct instruction:** item 5 (saturation
rejection in `frame_average.py`) is not built here even though item 2's
finding shows the current code is consistent with averaging saturated
samples. Changing the averaging stage would invalidate the knee
measurement the current `hdr_merge.py` white_level fix was derived from;
that sequencing decision belongs to the user, not this session. This
pass only reports what such a fix would look like (reject/clamp/weight
samples relative to a real white level, ideally the same operator-
supplied one `hdr_merge.py` now accepts, rather than the container max
`dtype_max()` uses today) and what it would change about the existing
masters (a full recompute — the current masters embed the unconditional-
average smear, not a decision point stored separately from the pixels).

**Baseline:** `frame_average.py` is 431 lines, `__version__ = "2.1"`, zero
code changes yet on this branch.

**Verification, stated honestly up front:** no real bracket or sidecar
data exists in this checkout (the Pi is unreachable), so this pass is
self-check/code-review only. No synthetic sidecar or bracket data will be
fabricated to exercise the new flag. No re-run, no reported numbers —
verification happens on the Pi, by the user, separately.


## 2026-08-02

### Record build: HANDOFF.md restructure, part 1 — fix the confirmed-stale sections

Built to the intent recorded below, no deviation.

**Against the counted baseline (3100 lines / section 1 289 lines / section
2 200 lines):** `HANDOFF.md` is now 2899 lines (-201). Section 1 (the
former "READ FIRST: the Qt6 port is on a branch" heading) is now 83 lines
under a new heading, "PyQt6 (the UI layer runs on PyQt6, not PyQt5)"
(-206 lines). Section 2 (the click-mapping fix's status) stayed 200 lines
— three surgical edits (title, the interim-workaround paragraph, the
closing on-rig-status paragraph), not a rewrite, so the length didn't
move. Line 3061's stray `PyQt5` is now `PyQt6`, confirmed the only
in-scope occurrence (`grep -n "PyQt5" HANDOFF.md` before editing showed
the other five at lines 1523-1525/2132/2769, all inside the untouched
"Part N" narrative, exactly as the intent's baseline said).

**What was preserved versus compressed, checked against the plan:**

- The 9-item out-of-scope/known-problems list: preserved verbatim, "for
  the port" framing dropped, still the section CHANGELOG's own 2026-07-29
  entry points back to.
- The 4-item on-rig-bench backlog: compressed from full paragraphs (each
  with a **Reproduced on `main`**/**Not A/B'd** qualifier and, for item 4,
  the full numeric table) to one-line-each, since the full readings are
  verbatim in `CHANGELOG.md`'s on-rig confirmation entry already. The
  qualifiers themselves were kept (pre-existing / not confirmed either
  way / not established) since those are the load-bearing classification,
  not just color.
- The flag-comparison gotcha and `pos()`-vs-`position()` decision: kept
  prominent, condensed from ~85 combined lines to ~30, folded directly
  into the new short section rather than kept as their own headings.
- Everything else in the original 289 lines (the port's own mechanical
  changes — enum-scoping counts, `exec_()` counts — the verification-state
  bookkeeping, the full binding-fix narrative): dropped to pointers.
  Checked against `CHANGELOG.md` before dropping, not assumed: the enum/
  `exec_()`/`ev.pos()` breakdown is in the 2026-07-29 "Build: PyQt5 to
  PyQt6 port" entry in more detail than `HANDOFF.md` carried; the
  verification-state and light/deep-verification lists are in the
  2026-08-01 "Record on-rig confirmation" entry with the actual readings
  `HANDOFF.md` only pointed at; the binding-fix root cause and fix are in
  the 2026-08-01 "picamera2 Qt binding selection" entries and now also
  live as a `# CAVEAT:` comment in `camera_backend.py:769` itself
  (harvested into `FUNCTION_INDEX.md`) — triply covered, safe to compress
  to a pointer.

**DISCOVERED, while re-checking the click-mapping section's on-rig
status**: the section's own "Static crop table" paragraph makes a claim
narrower than "the fix works" — that the table's per-mode crop rectangles
were derived by arithmetic, "not independently confirmed against a real
`crop_limits` read." The 2026-08-01 on-rig entry confirms the *conversion
formula* end to end (a real specimen measurement agreeing across two
independently calibrated objectives), which necessarily ran through
`Picamera2Camera`'s real `crop_limits`, not the static fallback table —
but nothing in that entry explicitly logs the raw `crop_limits` value
against the table's derived numbers for a side-by-side check. The
correction written into `HANDOFF.md` says what's actually confirmed (the
fix works on-rig) without overclaiming the narrower, unconfirmed point
(the fallback table's exact numbers were never explicitly cross-printed).
Not a new problem — a precision fix to what the record now claims.

**Acceptance, verified:**

- Full `--render-check` sweep, all 17 modules (the 16 pre-existing plus
  `function_index`), exit 0 — proves "documentation only" rather than
  just asserting it, same discipline `PHILOSOPHY.md`'s own three-phase
  entries hold themselves to.
- No stale `port/pyqt6`-as-a-live-branch claim remains: `grep -n
  "port/pyqt6" HANDOFF.md` returns exactly one line, the new section's
  own "no longer exists" sentence.
- No present-tense "this project is PyQt5" claim remains (`grep -n "is
  PyQt5" HANDOFF.md` returns nothing).
- The two still-open click-mapping caveats (second preview resolution
  untested, static table not cross-printed against a real `crop_limits`
  read) are stated as open, not glossed over by the correction.

**Out of scope, confirmed untouched:** the ~2250-line "Part N — BUILT"
narrative (lines 240-2494 in the current file) — zero lines changed
there, verified by the commit diff itself (`+90/-291`, all of it inside
the two sections named above; `git diff --stat` on the build commit shows
one file, `HANDOFF.md`, nothing else). That compression is its own piece,
gated on a section-by-section `CHANGELOG.md` coverage check, not started
here.

### Record intent: HANDOFF.md restructure, part 1 — fix the confirmed-stale sections

Own branch off `main`: `claude/handoff-restructure-part1`. Prompted by a
status audit of `HANDOFF.md` (3100 lines) run against current code state,
`git log`, and `CHANGELOG.md` — not a scheduled task, a review finding.

**Problem, three confirmed-stale items, verified against current state
rather than assumed:**

1. **Lines 25-313 (289 lines), "READ FIRST: the Qt6 port is on a branch
   (`port/pyqt6`)".** Says "If you are on `main`, this project is PyQt5."
   False today: `qt_shell.py` imports PyQt6 on `main` (`grep -n "from
   PyQt" qt_shell.py`), `camera_backend.py:769` already carries the
   `QGl6Picamera2` binding fix, and `git log --oneline main` shows the
   port's build/record-build/on-rig-confirmation commits and the binding
   fix's own series already in `main`'s linear history — not a merge
   commit from a separate branch, direct history. `port/pyqt6` does not
   exist in `git branch -a`. The whole section describes a bifurcated
   state that stopped being true a while ago.
2. **Lines 2847-3046 (200 lines), "PRIORITY: preview-to-green-plane click
   mapping is wrong — BUILT".** Says "On-rig verification is explicitly
   NOT done by this session... keep using the interim workaround" (press
   Escape, place both measurement points manually on the frozen canvas).
   `CHANGELOG.md`'s 2026-08-01 "Record on-rig confirmation: PyQt5 to PyQt6
   port" entry confirms this exact fix on-rig with real stage-micrometer
   and specimen readings (40x measurement within 1% of the 4x
   calibration). Telling a reader to keep using a workaround for an
   already-fixed bug is actively misleading, not merely outdated.
3. **Line 3061**, "Design conventions worth knowing": "(mostly) no
   PyQt5" — the codebase is PyQt6 throughout now (see item 1).

**Coverage check, since compressing without one is exactly the "trust the
history is elsewhere" gap this task's own review flagged:** cross-checked
what's actually duplicated in `CHANGELOG.md` before deciding what's safe
to compress versus what must be preserved.

- The flag-comparison gotcha (`Qt.MouseButton.LeftButton == 1` is False,
  etc.) — present verbatim in `CHANGELOG.md:1400-1401` ("Build: PyQt5 to
  PyQt6 port"). Safe to compress in `HANDOFF.md`, though it stays
  prominent (it's exactly the kind of thing a future agent writing new
  event-handling code needs to see before, not after, hitting it).
- The `pos()`-vs-`position()` deferred decision — present in the same
  entry. Same treatment: compress, keep visible, since it's a live,
  unresolved future decision, not closed history.
- The on-rig-bench backlog (ROI box jump, focus-aid Z-stack rebase, GL
  viewport DPI-resize gap, field-scale gradient) — present **verbatim**,
  word for word, in `CHANGELOG.md`'s "Record on-rig confirmation" entry.
  Safe to compress to a short list; these are still-open bugs, so they
  stay listed, just not re-narrated in full.
- **The 9-item out-of-scope/known-problems list (`GREEN_PLANE_RES`
  duplication, the `qt_shell.py:3452` bug, hardcoded green-plane shapes,
  missing mono/no-CFA path, BGGR assumption, `FULL_MODE_LBL`, the open
  `G_IS_OBJECT` assertion, capture-logic extraction, `provenance.py`
  phase 2) is NOT duplicated in `CHANGELOG.md`.** `CHANGELOG.md:1353`
  ("Record intent: PyQt5 to PyQt6 port") says outright: "the out-of-scope
  list is the one handed over in the port brief and it is reproduced in
  `HANDOFF.md` under the port section" — `HANDOFF.md` is the canonical
  copy CHANGELOG itself points to. This list is preserved, not deleted,
  just reframed without the now-meaningless "for the port" framing (the
  port is done; these are just still-open problems).
- **A correction to this session's own earlier read of a fourth item:**
  the audit initially flagged `wizard_pages.py`/`test_burst_backend.py`
  importing `picamera2` directly as an unresolved boundary violation.
  Checked against `camera_backend.py:1524-1547`
  (`assert_only_camera_backend_imports_picamera2`) directly: both are a
  documented, hardcoded exception set (`exceptions = {"wizard_pages.py",
  "test_burst_backend.py"}`), reasoned in the function's own docstring.
  The guard is honest, not holed — not a live bug, so it is dropped from
  this piece's scope entirely, not carried forward as a finding. Worth
  noting for whoever looks at this later: that exception set is
  maintained by hand, unlike its sibling
  `_sensor_profile_module_names` (same file), which discovers
  sensor-profile modules by shape specifically so nothing has to be
  remembered. Whether the two `picamera2` exceptions can be brought
  inside the boundary the same way — `wizard_pages.py`'s probe in
  particular, since `get_capabilities()` may now be able to answer it —
  is worth a look eventually. Not decided or scoped here.

**Plan.** Rewrite lines 25-313 into a short current-state section: port
status in a few sentences (merged, on-rig confirmed, pointer to
`CHANGELOG.md`), the two still-relevant Qt6 gotchas condensed but kept
visible, the 9-item known-problems list preserved with the "for the port"
framing dropped, the 4-item bench backlog compressed to a short list.
Surgically fix the two stale status paragraphs in lines 2847-3046 (the
"BUILT" title and the two "not yet on-rig" paragraphs) without touching
the rest of that section's technical detail, which is accurate. Fix line
3061's PyQt5 mention.

**Scope, deliberately split from the larger restructure question, per
direct instruction:** this piece is items 1-3 above (the confirmed-stale
material) plus surfacing item 4's genuinely-open content correctly (minus
the picamera2-boundary claim, corrected above). Explicitly NOT in scope:
the ~2250-line "Part N — BUILT" narrative in lines 446-2700 (~15
sections) — compressing those needs its own coverage check, section by
section, against `CHANGELOG.md`, which is real judgment work for its own
piece, not something to fold into this one. That is exactly the same
shape of unbounded excursion the function-index work's own instructions
warned against for `HANDOFF.md`'s "things that will bite you" section —
the parallel holds here too, and the fix is the same: split the work,
don't rush the part that needs a count.

**Baseline, measured before any file was touched:** `HANDOFF.md` is 3100
lines. Section 1 (lines 25-313) is 289 lines. Section 2 (lines 2847-3046)
is 200 lines. One `PyQt5` mention at line 3061 in scope; five other
`PyQt5` mentions elsewhere in the file (lines 1523-1525, 2132, 2769) are
inside the out-of-scope "Part N" narrative, describing historically
accurate PyQt5-era state at the time they were written, and are left
alone.

### Record build: generated per-module function index, with a freshness guard

Built to the intent recorded in `6402a4b`, from the clean tree the note
below it confirmed. No deviation from the plan: `function_index.py`, one
new file, walks the AST and harvests `# CAVEAT:` comments exactly as
described; `FUNCTION_INDEX.md` is its output; `README.md`'s sweep loop
gained `function_index`.

**Against the counted baseline (23 modules / 274 top-level defs / 1
`# CAVEAT:`):** 24 modules now (`function_index.py` counts itself, as
expected — it walks the tree it's part of), 284 top-level functions/classes
(`grep -c '^- `' FUNCTION_INDEX.md`; +10, all functions, all in
`function_index.py` itself — `_discover_modules`, `_top_level_nodes`,
`_signature_lines`, `_harvest_caveats`, `_owner_for_line`, `_render_module`,
`generate_index_text`, `assert_function_index_current`, `main`,
`render_check`), 5 `# CAVEAT:` comments (1 pre-existing in
`camera_backend.py`, 4 newly seeded in `function_index.py` — within the
plan's three-to-five range, all at the one code file this work touches, as
scoped).

**The two deliberate decisions, confirmed by actually running them, not
asserted:**

1. **Guard placement.** `python3 function_index.py --render-check` fails
   with a real, readable diff when `FUNCTION_INDEX.md` doesn't exist yet
   (run before the file was generated) and passes once it does. Reachability
   beyond the standalone tool was confirmed by running the actual documented
   sweep from the now-updated `README.md` — all 17 modules, `function_index`
   included, exit 0, `assert_function_index_current PASS` printed as part
   of that run, not a separate invocation.
2. **Determinism.** Confirmed two ways: `render_check()`'s own within-process
   double-generation-and-diff, and the stronger cross-process test — two
   separate `python3 function_index.py` processes, the second forced to
   `PYTHONHASHSEED=random`, output files byte-identical
   (`e1d3db1f0428ef939a31181c206eae87` both times).

**The guard-fails-on-drift acceptance criterion, demonstrated and
reverted, not asserted:** appended `def _demo_undocumented_function(x):
return x` to `pixel_hash.py`, ran `python3 function_index.py
--render-check` — failed with `AssertionError`, exit 1, diff showing
exactly the one added line. `git checkout -- pixel_hash.py` reverted it;
re-ran the same command — passed, exit 0. Never committed in the broken
state.

**DISCOVERED: this sandbox was missing `numpy`, `tifffile`, `PyQt6`, and
`Pillow`, and the system libraries `qt_shell.py`'s Qt-gated checks need**
(`libegl1`, `libegl-mesa0`, `libxcb-cursor0`, `libxkbcommon-x11-0`,
`libxcb-icccm4`, `libxcb-keysyms1`, `libxcb-shape0`, plus `xvfb-run` — no
real display here) — the same recurring, environment-only gap this
project's own tempfile-sweep entries above already document, not a fact
about this change. Installed all of it to run the real documented sweep
rather than accept a degraded one; without it, 15 of the other 16 modules
would have failed on import before ever reaching `function_index`'s own
check, which would have proven nothing about reachability.

**Acceptance, verified:**

- Generator is deterministic: confirmed above, both within-process and
  cross-process with a different hash seed.
- Every module in the tree appears: `module coverage check PASS: all 24
  modules appear as their own heading`, part of `render_check()` itself.
- The guard fails when a function is added without regenerating:
  demonstrated and reverted above.
- All modules with `--render-check` pass, exit 0: all 17 in the updated
  `README.md` sweep, run for real under `xvfb-run` where Qt-gated,
  `qt_shell.py` and `measure.py` showing 48 and 13 real `PASS` lines with
  zero `SKIPPED`, not a degraded run.

Out of scope, confirmed untouched: no line of `HANDOFF.md`'s "things that
will bite you" section was converted to a `# CAVEAT:` comment. Five exist
now — the mechanism is built and seeded; the rest accumulates as code is
worked on, per the intent's explicit scope.

### Record note: `6402a4b` itself was written after build activity, corrected per `PHILOSOPHY.md`'s own remedy

Single entry, no intent phase — this records a correction to process, not
new feature work. `6402a4b` (below) is not edited, per the append-only
rule; this entry stands beside it.

**The defect.** `6402a4b`'s own commit diff touches only `CHANGELOG.md`,
so it looked correctly ordered by commit boundaries alone. It wasn't. By
the time it was written, `function_index.py` had already been rebuilt from
scratch, run repeatedly, used to generate `FUNCTION_INDEX.md`, exercised
to demonstrate the freshness guard both failing and passing, and
`README.md`'s sweep loop had already been edited — all of it sitting
uncommitted on disk. `6402a4b` describes a plan for work already done, not
work about to start. `PHILOSOPHY.md` requires intent recorded "before the
build begins," which is a stronger claim than "before the build is
committed," and this failed the stronger one.

**Remedy applied, verbatim from `PHILOSOPHY.md`:** *"If intent wasn't
recorded before the build... undo only the building that was done and
start over — keep every record, including the one that shows the false
start."* `function_index.py` and `FUNCTION_INDEX.md` were deleted and
`README.md`'s edit reverted (`git checkout`), leaving a clean working tree
with only the three CHANGELOG commits (`efb4215`, `0d41d65`, `6402a4b`) on
top of this branch's prior history — verified with `git status --short`
returning nothing. `6402a4b` is kept, unedited, as the record of what was
believed and planned; this entry is the correction, not a replacement.

**What actually changes going forward:** nothing about `6402a4b`'s
content is disputed — problem, baseline, plan, scope, and the two
deliberate decisions all still stand as the current intent. The build
starts now, for real, from the clean tree this entry confirms, with
nothing pre-built to describe in hindsight.

### Record intent, redone: generated per-module function index, with a freshness guard

**Supersedes `efb4215`** ("Record intent: generated per-module function
index, with a freshness guard"), per direct instruction after a build-order
concern was raised against it. The concern itself — that the intent commit
had been preceded by build work, inverting the three-phase convention — was
checked against actual git history in the note entry below (`0d41d65`) and
not found: `efb4215` touched `CHANGELOG.md` alone and landed before
`function_index.py` existed on disk. Nothing in `efb4215`'s own content —
problem, baseline, plan, scope, or the two deliberate decisions — is
disputed on the merits. It is superseded anyway, so the redo the concern
prompted has its own clean, current intent entry rather than leaving that
concern attached only to a side note next to an entry it doesn't touch.
`efb4215` stays visible below, unedited, showing what was recorded and
verified.

**Problem.** The tree is 21,884 lines across 23 modules with no
navigational index. An agent picking up a task knows the *concern* ("where
is the click-to-native-pixel mapping done?"), not the *identifier*
(`native_point_from_preview_click`), so it greps blind — repeatedly, per
this project's own session history. The pending overhaul is about to
relocate a large amount of this code (module boundaries shifting per the
"Module organization" note in `PHILOSOPHY.md`), which is exactly when a
navigational aid earns its cost and exactly when a hand-maintained one
would rot fastest.

**Why generated, not written by hand.** A hand-maintained index drifts the
first time someone adds a function and forgets it, and a drifted index is
worse than none — it gets trusted, and a wrong answer costs more than no
answer. This is the same reasoning `camera_backend.py`'s
`assert_only_camera_backend_imports_sensor_profiles` already applies to
sensor-profile discovery: it finds profile modules by shape (exposing
`FULL_ARRAY_SIZE`/`crop_for_size`), not from a maintained list, so a future
`imx519.py` is covered the moment it exists. Same principle here: derived
beats maintained. The index generator walks the AST; nobody edits the
output by hand.

**Why grouped by module, not alphabetical.** Lookup by name assumes you
already know the name — the exact thing an agent grepping blind does not
have. A single alphabetical list of all ~274 top-level functions/classes
across 22k lines would interleave `calibrate.py` and `qt_shell.py` with no
surrounding context, which is no better than the blind grep it's meant to
replace. Grouping by module keeps a function next to its siblings, where
the surrounding names themselves carry meaning.

**Baseline, re-stated unchanged from `efb4215`** (measured before any file
was touched; re-verified true here since nothing about the tree changed
between the two intent entries):

| Metric | Count |
|---|---|
| Modules (`*.py` in repo root) | 23 |
| Top-level functions | 259 |
| Top-level classes | 15 |
| Top-level functions + classes | 274 |
| `# CAVEAT:` comments (`grep -rn`, code only) | 1 |

The single `# CAVEAT:` is `camera_backend.py`'s binding-fix comment from
the picamera2 Qt-binding-selection work (currently at line 763, not the
769 recalled from memory going in — line numbers drift, this baseline
trusts the re-measurement, not the recollection). Three other files
(`HANDOFF.md`, `PHILOSOPHY.md`, this file) match the same grep because they
discuss the marker convention in prose; none of those are code comments and
none are in scope for harvesting.

**Plan.** A new module, `function_index.py`, alongside the other 23. It
walks each module's AST, collects top-level `FunctionDef`/`AsyncFunctionDef`/
`ClassDef` nodes (not nested defs — terse, one line per signature via
`ast.unparse` on a body-stripped clone, not a hand-rolled formatter), and
writes `FUNCTION_INDEX.md` grouped by module, modules sorted alphabetically
by filename. Separately, it scans each module's raw source text for
`# CAVEAT:` comments (AST discards comments, so this half is necessarily
text-based, not structural — the one place this generator works the way
the harvested convention itself, a plain source comment, requires) and
attaches each one to whichever top-level node's line range contains it,
falling back to a module-level bucket for anything outside every top-level
node's range.

**The two decisions this task asked to be made deliberately, made here:**

1. **Where the guard runs.** `assert_function_index_current()` (named in
   the existing `assert_*` idiom this project already uses for structural,
   tree-wide checks) lives in `function_index.py`'s own `render_check()`,
   reached the same way as every other module's: `python3 function_index.py
   --render-check`. This is not a new testing mechanism — it's the
   project's own established idiom, applied to a new module the same way
   it's applied to the other 16 that carry it. What makes it actually
   reachable rather than merely present: `function_index.py` is added to
   `README.md`'s documented sweep loop (the `for m in ...
   --render-check` block), so it joins the checklist this project already
   runs after every change, not a check that exists in isolation. The
   build record below confirms this by actually running it, both alone and
   as part of that sweep — not by asserting the function exists.
2. **Determinism.** Two runs on an unchanged tree must produce identical
   bytes, checked two ways: within `render_check()` (generate twice in the
   same process, diff), and — the stronger test, since the same-process
   check can't catch a bug that only shows up with a different
   `PYTHONHASHSEED` — as two separate `python3 function_index.py`
   invocations in the build record, each its own process, diffed on disk.
   The concrete trap this guards against: CPython randomizes string hash
   seeds per process by default, so a bare `set()` of strings iterated
   directly into output would look deterministic in every same-process
   test and then flap between unrelated process runs for a reason nobody
   would think to check. The generator sorts explicitly everywhere order
   is observable — modules by filename, caveats by line number — and never
   iterates a `set()` into output. A guard that fails spuriously on an
   unchanged tree gets disabled by the next agent who hits it, which is
   worse than not having it.

**Scope.** New file `function_index.py`; new generated file
`FUNCTION_INDEX.md`; `README.md`'s self-check sweep loop gains
`function_index` (17 modules, up from the list's current 11 — that list
was already stale against the real 16-module sweep this project's own
`CHANGELOG.md` entries describe elsewhere, and is corrected to 17 rather
than left at a number that was already wrong before this change). Nothing
else. Explicitly out of scope: migrating `HANDOFF.md`'s "things that will
bite you" section (~345 lines) into `# CAVEAT:` comments — that is exactly
the unbounded excursion this task's own instructions warn against. This
build seeds three to five `# CAVEAT:` comments at sites already being
touched (inside `function_index.py` itself — the only code file this work
touches) and lets the rest accumulate as code is worked on, not converted
in one pass.

### Record note: build-order concern raised against `efb4215`, checked

Single entry, no intent phase — this records a check against already-landed
work, not new work of its own, per the convention's outcome-only carve-out.
Per the project's own append-only rule, `efb4215` (the intent entry below)
is not edited; this entry stands beside it instead.

A review raised a concern that the intent commit below had been preceded
by build work — the three-phase convention requires the CHANGELOG intent
entry to be committed "before any other file is touched," and building
first would invert that.

**Checked against the actual git history, not assumed either way:**
`git log` and `git status` at the time of the check showed exactly one new
commit on this branch, `efb4215` (CHANGELOG.md only, the intent entry
below), and a single untracked, uncommitted file, `function_index.py` —
no prior commit touching that file, and nothing else modified. `efb4215`'s
own diff (`git show --stat`) confirms it touches `CHANGELOG.md` alone. By
the repository's history, the intent commit landed before the build file
existed on disk, and no build file was ever committed ahead of it.

**Action taken regardless, per direct instruction:** the uncommitted
`function_index.py` draft that predated this note is discarded outright,
not reused. The build phase starts fresh from this point, on top of
`efb4215` and this note, so there is no ambiguity left about what was
written before what was recorded.

### Record intent: generated per-module function index, with a freshness guard

Own branch off `main`: `claude/function-index-generator-avl3i0`. (This
branch already carries prior unrelated landed work — the tempfile sweep and
Qt environment-defaults series recorded above — from earlier sessions; this
entry starts a new, independent intent/build/record-build series on top of
it, not a continuation of either.)

**Problem.** The tree is 21,884 lines across 23 modules with no
navigational index. An agent picking up a task knows the *concern* ("where
is the click-to-native-pixel mapping done?"), not the *identifier*
(`native_point_from_preview_click`), so it greps blind — repeatedly, per
this project's own session history. The pending overhaul is about to
relocate a large amount of this code (module boundaries shifting per the
"Module organization" note in `PHILOSOPHY.md`), which is exactly when a
navigational aid earns its cost and exactly when a hand-maintained one
would rot fastest.

**Why generated, not written by hand.** A hand-maintained index drifts the
first time someone adds a function and forgets it, and a drifted index is
worse than none — it gets trusted, and a wrong answer costs more than no
answer. This is the same reasoning `camera_backend.py`'s
`assert_only_camera_backend_imports_sensor_profiles` already applies to
sensor-profile discovery: it finds profile modules by shape (exposing
`FULL_ARRAY_SIZE`/`crop_for_size`), not from a maintained list, so a future
`imx519.py` is covered the moment it exists. Same principle here: derived
beats maintained. The index generator walks the AST; nobody edits the
output by hand.

**Why grouped by module, not alphabetical.** Lookup by name assumes you
already know the name — the exact thing an agent grepping blind does not
have. A single alphabetical list of all ~274 top-level functions/classes
across 22k lines would interleave `calibrate.py` and `qt_shell.py` with no
surrounding context, which is no better than the blind grep it's meant to
replace. Grouping by module keeps a function next to its siblings, where
the surrounding names themselves carry meaning.

**Baseline, measured on this tree before any file was touched** (ad hoc
`ast`-walk over `sorted(Path(".").glob("*.py"))`, not the generator itself,
which does not exist yet):

| Metric | Count |
|---|---|
| Modules (`*.py` in repo root) | 23 |
| Top-level functions | 259 |
| Top-level classes | 15 |
| Top-level functions + classes | 274 |
| `# CAVEAT:` comments (`grep -rn`, code only) | 1 |

The single `# CAVEAT:` is `camera_backend.py`'s binding-fix comment from
the picamera2 Qt-binding-selection work (currently at line 763, not the
769 recalled from memory going in — line numbers drift, this baseline
trusts the re-measurement, not the recollection). Three other files
(`HANDOFF.md`, `PHILOSOPHY.md`, this file) match the same grep because they
discuss the marker convention in prose; none of those are code comments and
none are in scope for harvesting.

**Plan.** A new module, `function_index.py`, alongside the other 23. It
walks each module's AST, collects top-level `FunctionDef`/`AsyncFunctionDef`/
`ClassDef` nodes (not nested defs — terse, one line per signature via
`ast.unparse` on a body-stripped clone, not a hand-rolled formatter), and
writes `FUNCTION_INDEX.md` grouped by module, modules sorted alphabetically
by filename. Separately, it scans each module's raw source text for
`# CAVEAT:` comments (AST discards comments, so this half is necessarily
text-based, not structural — the one place this generator works the way
the harvested convention itself, a plain source comment, requires) and
attaches each one to whichever top-level node's line range contains it,
falling back to a module-level bucket for anything outside every top-level
node's range.

**The two decisions this task asked to be made deliberately, made here:**

1. **Where the guard runs.** `assert_function_index_current()` (named in
   the existing `assert_*` idiom this project already uses for structural,
   tree-wide checks) lives in `function_index.py`'s own `render_check()`,
   reached the same way as every other module's: `python3 function_index.py
   --render-check`. This is not a new testing mechanism — it's the
   project's own established idiom, applied to a new module the same way
   it's applied to the other 16 that carry it. What makes it actually
   reachable rather than merely present: `function_index.py` is added to
   `README.md`'s documented sweep loop (the `for m in ...
   --render-check` block), so it joins the checklist this project already
   runs after every change, not a check that exists in isolation. The
   build record below confirms this by actually running it, both alone and
   as part of that sweep — not by asserting the function exists.
2. **Determinism.** Two runs on an unchanged tree must produce identical
   bytes, checked two ways: within `render_check()` (generate twice in the
   same process, diff), and — the stronger test, since the same-process
   check can't catch a bug that only shows up with a different
   `PYTHONHASHSEED` — as two separate `python3 function_index.py`
   invocations in the build record, each its own process, diffed on disk.
   The concrete trap this guards against: CPython randomizes string hash
   seeds per process by default, so a bare `set()` of strings iterated
   directly into output would look deterministic in every same-process
   test and then flap between unrelated process runs for a reason nobody
   would think to check. The generator sorts explicitly everywhere order
   is observable — modules by filename, caveats by line number — and never
   iterates a `set()` into output. A guard that fails spuriously on an
   unchanged tree gets disabled by the next agent who hits it, which is
   worse than not having it.

**Scope.** New file `function_index.py`; new generated file
`FUNCTION_INDEX.md`; `README.md`'s self-check sweep loop gains
`function_index` (17 modules, up from the list's current 11 — that list
was already stale against the real 16-module sweep this project's own
`CHANGELOG.md` entries describe elsewhere, and is corrected to 17 rather
than left at a number that was already wrong before this change). Nothing
else. Explicitly out of scope: migrating `HANDOFF.md`'s "things that will
bite you" section (~345 lines) into `# CAVEAT:` comments — that is exactly
the unbounded excursion this task's own instructions warn against. This
build seeds three to five `# CAVEAT:` comments at sites already being
touched (inside `function_index.py` itself — the only code file this work
touches) and lets the rest accumulate as code is worked on, not converted
in one pass.

### Record verification: tempfile sweep, self-check harnesses — PASS-line parity across all eight modules

Single entry, no intent phase — this checks already-built, already-landed
code (the intent/build/record-build series below), not new work, per the
convention's own carve-out for outcome-only work.

**What was compared.** The record-build entry below ran a byte-for-byte
PASS-line diff against the pre-edit tree for `qt_shell.py` only. Exit 0
and zero `SKIPPED` across all eight modules proved the checks still
pass; it did not prove they still assert the same things — a converted
temp path or an added cleanup call could in principle sit next to a
quietly loosened assertion and still exit clean. That gap is closed here
for the remaining seven: `calibrate.py`, `provenance.py`, `measure.py`,
`annotations.py`, `camera_backend.py`, `ca_measure.py`, `plane_cache.py`.

**Method.** Each module's `--render-check` (`camera_backend.py`: plain
run, it takes no flag) captured on the pre-edit tree (`184ae2e`, checked
out into its own worktree) and on this branch, same session, same
environment: **`xvfb-run` plus this sandbox's installed `libegl1`,
`libegl-mesa0`, `libxcb-cursor0`, `libxkbcommon-x11-0`, `libxcb-icccm4`,
`libxcb-keysyms1`, `libxcb-shape0`** — the same set the record-build
entry below names, not a bare rig. `measure.py` specifically needs this
set to construct a real `QApplication` rather than silently skip its
Qt-gated checks; the other six modules don't touch Qt in their self-check
and ran without `xvfb-run`. `... PASS` lines extracted from both logs and
diffed; `SKIPPED` lines diffed the same way.

**Result: identical on all seven, both PASS lines and SKIPPED lines
(empty on both, every module).** `calibrate.py` 9, `provenance.py` 9,
`annotations.py` 7, `camera_backend.py` 16, `ca_measure.py` 7,
`plane_cache.py` 8, `measure.py` 13 — line-for-line matches, not just
matching counts. `measure.py` mattered most going in: cleanup was added
inside its ~250-line calibration-gating block (one `tmp_dir` shared
across the calibration store, two nested annotation-store swaps, and two
standalone image files) — exactly the size of block where a changed
assertion hides behind a green result. It didn't happen here. Combined
with `qt_shell.py`'s own diff in the record-build entry below, all eight
modules now carry this same confirmation.

**What this establishes, and what it doesn't.** The sweep is justified
by a claim about Windows: that the application would likely launch there
while every self-check failed outright, since none of these eight
modules could resolve a hardcoded POSIX `/tmp` path. What landed removes
that specific, known blocker — `tempfile.mkdtemp()`/`tempfile.
gettempdir()` are documented cross-platform, and nothing in this sweep
is Linux-conditional. **It does not establish that these harnesses
actually pass on Windows or macOS.** Nobody has run either. Worth
stating here, not only as a caveat attached to this entry, since
"removes a known blocker" and "confirmed portable" read as the same
claim once a changelog entry is skimmed rather than read in full, and
only the first one is true of this work.

**Cleanup deviation, already on record below, re-confirmed here.** The
record-build entry already states plainly that converting to
`mkdtemp()` without adding cleanup would have turned several sites'
previously-bounded leak (one fixed name, overwritten each run) into an
unbounded one (a fresh unique name every run, nothing to find the old
one), so cleanup was added alongside the conversion at every site that
lacked it — a necessary deviation from a literal reading of "just swap
the path," not scope creep. Re-confirmed independently in this session:
cleared `/tmp` of every `zynergy_*` entry, ran all eight modules back to
back from a clean slate, swept again — zero entries left, the same
result the record-build entry reports, reproduced rather than assumed
still true.

### Record build: tempfile sweep, self-check harnesses

Built to the intent recorded below, no scope change — every site named
in the intent's baseline table is converted, no production path touched,
no check's assertion weakened.

**Against the counted baseline: 68 sites before, 68 converted, 0
remaining.**

| File | Sites |
|---|---|
| `qt_shell.py` | 24 |
| `calibrate.py` | 11 |
| `provenance.py` | 10 |
| `measure.py` | 10 |
| `annotations.py` | 5 |
| `camera_backend.py` | 4 |
| `ca_measure.py` | 3 |
| `plane_cache.py` | 1 |

Split by which tool: `tempfile.mkdtemp()` for every directory that gets
created and written into (the large majority); `tempfile.gettempdir()`
for the handful of fake source-image paths that are only ever recorded
as a string in a JSON entry and never touch disk (`calibrate.py`'s
`fake_40x.dng`/`fake_100x.dng`/`fake_40x_redo.dng`, `ca_measure.py`'s
`fake_ca_target.tif`, `measure.py`'s and `qt_shell.py`'s `fake.dng`
sites) and one deliberately-missing path
(`qt_shell.py`'s `archive_session_raws` no-such-dir case, which must
keep genuinely not existing). Where a check compared a recorded
`source_image` against a hardcoded literal string (`calibrate.py`'s
40x/100x round-trip, the one case this came up), the assertion now
compares against the same variable that built the path instead of a
second hardcoded copy — the claim being checked (the path survives the
round-trip unchanged) is identical either way.

**DISCOVERED: a large fraction of these sites had no cleanup at all
before this sweep, independent of the `/tmp` literal problem.** Not
anticipated in the intent, which only named the portability problem.
Found while deciding, per the build step's own instruction, whether each
harness cleans up after itself:

- `camera_backend.py`'s four sites (`capture_still`/`capture_still_async`
  sharing one dir, plus `burst_dir`/`vid_dir`) never removed anything —
  no `shutil` import, no cleanup of any kind. Now share one `mkdtemp`
  root, `shutil.rmtree`'d in one call at the end of the `__main__` block
  (a one-shot script -- if an earlier assertion fails the process ends
  anyway, so this is placed at the end rather than in a `try`/`finally`,
  matching the same reasoning `qt_shell.py`'s own `PROFILE_PATH` comment
  already gives for the same tradeoff).
- `qt_shell.py`'s function-wide `PROFILE_PATH`/`PROVENANCE_ROOT`/
  `OUT_ROOT`/`FLAT_ROOT` swap (used for the entire ~2700-line
  `render_check()`) was restored (the module-attribute pointer) but
  never had its directory removed. Previously this was a fixed name,
  pre-emptively `rmtree`'d at the *start* of the next run, so at most one
  leftover directory existed on disk between runs. `mkdtemp` breaks that
  bound — a fresh name every run means nothing later finds the old one —
  so this sweep adds real cleanup at the same point the pointer already
  gets restored, rather than trade a portability fix for an unbounded
  leak.
- `annotations.py`'s three temp-path blocks (the main store, the
  `calibration_ref_for` block, its nested `stored_calibration_ref`
  block) all restored their module-attribute pointer in `finally` but
  never removed the directory either. Same fix: `shutil.rmtree` added
  alongside each existing pointer restore.
- Several `qt_shell.py` `PREFS_PATH` swaps (the plain Preferences dialog
  check, its part-2 stream-capability check) had the same gap. Same fix.
- `measure.py`'s calibration-gating block reused one `tmp_dir` across
  ~250 lines (calibration store, two nested annotation-store swaps, two
  standalone image files) and restored `CALIBRATION_PATH` in `finally`
  but never removed `tmp_dir` itself. Same fix, one `shutil.rmtree` in
  that same `finally`.

Every site that already had its own cleanup (`plane_cache.py`,
`calibrate.py`'s `resolve_raw_path`/calibration-store blocks,
`provenance.py`'s nine-directory final sweep, `ca_measure.py`, most of
`qt_shell.py`'s scoped blocks) keeps that cleanup, now `rmtree`ing a
`mkdtemp`'d path instead of a fixed one — no change to *whether* it
cleans up, only to *what* it's cleaning.

**DISCOVERED: this sandbox needed several system libraries and a
display server to run the Qt-gated halves of `qt_shell.py`'s and
`measure.py`'s own `render_check()` to completion** — none a fact about
this project's own code. Missing `libEGL.so.1` made `PyQt6.QtWidgets`
itself fail to import (silently, via this project's own `except
ImportError` guard around the top-of-file Qt import), which meant
`_HAVE_QT` read `False` and an *unguarded* `QApplication.instance()`
call at `qt_shell.py`'s line ~5963 raised `NameError` — reproduced
identically on the pre-edit tree first, confirming this is an
environment gap, not a regression (same discipline as the Qt
environment-defaults build entry's own Pillow discovery). Installed
`libegl1`, `libegl-mesa0`, `libxcb-cursor0`, `libxkbcommon-x11-0`,
`libxcb-icccm4`, `libxcb-keysyms1`, `libxcb-shape0`, and ran under
`xvfb-run` (no real display in this sandbox). Nothing about the tempfile
sweep required any of this; it was required to *observe* the sweep's
own Qt-gated checks running rather than being silently SKIPPED.

**Acceptance, verified:**

- Zero hardcoded `/tmp` literals remain anywhere in the tree (`grep -rl
  "/tmp"` over every file, not just `.py`, matches nothing but this
  changelog's own prose).
- All eight modules' self-check passes, exit 0, from a clean `/tmp`:
  `plane_cache.py`, `calibrate.py`, `annotations.py`, `provenance.py`,
  `ca_measure.py`, `camera_backend.py` directly; `measure.py` and
  `qt_shell.py` under `xvfb-run` (both construct a real `QApplication`).
  Zero `SKIPPED` lines across all eight — confirmed by diffing
  `qt_shell.py`'s 48 `... check PASS` lines against a run of the
  pre-edit tree in the same environment: identical set, identical order,
  byte-for-byte on every line prefix.
- **Repeated runs leave no directories behind.** Cleared `/tmp` of every
  `zynergy_*` entry, ran all eight modules back to back (six standalone,
  `measure.py` and `qt_shell.py` under `xvfb-run`), and swept `/tmp`
  again: zero `zynergy_*` entries remained. Contrast with the pre-edit
  tree exercised earlier in this same session, which left both directory
  and file leftovers with fixed names after a normal successful run —
  the DISCOVERED gaps above, empirically confirmed, not just reasoned
  about.
- **Windows and macOS remain untested.** Nobody has run any of these
  eight modules there. `tempfile.mkdtemp()`/`gettempdir()` are
  documented cross-platform, and nothing in this sweep is
  Linux-conditional the way the Qt environment-defaults work was, but
  that is a claim about the API, not a claim this was actually run on
  either platform — it wasn't, and isn't described as verified there.

### Record intent: tempfile sweep, self-check harnesses

Own branch off `main`, after the Qt environment defaults work landed.

**Problem.** Production paths are already portable — they use
`Path.home()`. The self-check harnesses are not: every one of them
hardcodes a POSIX `/tmp` path. Net effect is that the application is
more portable than its own test suite. On Windows the app would likely
launch fine while every `--render-check` failed outright (no `/tmp` to
resolve), which reads as catastrophe and is not one — it says nothing
about whether the app itself works there.

**Baseline, re-measured, not trusted from any prior clone** (`grep -rn
"/tmp" --include="*.py" .`, then per-file counts via `grep -c`):

| File | Sites |
|---|---|
| `qt_shell.py` | 24 |
| `calibrate.py` | 11 |
| `provenance.py` | 10 |
| `measure.py` | 10 |
| `annotations.py` | 5 |
| `camera_backend.py` | 4 |
| `ca_measure.py` | 3 |
| `plane_cache.py` | 1 |
| **Total** | **68** |

No other file in the tree (any extension, not just `.py`) contains the
literal `/tmp` — confirmed with an unrestricted `grep -rl`, not just the
`.py` glob above.

**Load-bearing claim confirmed before building on it: every site is
harness-only, none are in a production path.** For the seven modules
with a `render_check()` function (`plane_cache.py`, `calibrate.py`,
`annotations.py`, `provenance.py`, `ca_measure.py`, `qt_shell.py`,
`measure.py`), every `/tmp` site was traced to a line number strictly
between that function's `def render_check():` and the next top-level
`def` in the file (verified with `awk '/^def /{print NR}'` bracketing,
not by eyeballing) — none sit outside it. `camera_backend.py` has no
`render_check()`; its four sites are module-level statements inside `if
__name__ == "__main__":`, guarded by that same self-check-only
condition, just not wrapped in a named function — noted here so the
build step doesn't apply `render_check()`-shaped reasoning to it by
habit. This holds: nothing above is reachable from an import of any of
these eight modules, only from running one directly (with
`--render-check` where the module gates on it, unconditionally for
`camera_backend.py`). No STOP condition triggered.

**Plan.** Replace every hardcoded `/tmp` literal with
`tempfile.mkdtemp()` for a directory that gets created and written into,
and `tempfile.gettempdir()` for a path where a stable, predictable name
is genuinely wanted — chiefly the handful of fake source-image paths
(`/tmp/fake.dng` and siblings) that are never written to disk, only
recorded as a string and compared against later; there is no directory
to create there and no multi-user collision is possible against a path
nothing ever touches. `mkdtemp` is the default everywhere else: a fixed
name in a shared, world-writable `/tmp` is its own bug on a multi-user
machine independent of the Windows/macOS portability problem this sweep
is actually about, and `mkdtemp` costs nothing a fixed name doesn't
already pay for in setup.

**Scope.** The eight files named in the baseline table, harness code
only — `render_check()` in seven of them, the `if __name__ ==
"__main__":` self-check block in `camera_backend.py`. No production
path. No behaviour change to what any check asserts: where a check
currently compares against a literal `/tmp/...` string (`calibrate.py`'s
`source_image` round-trip is the one place this happens), the assertion
moves to comparing against the same variable that built the path, not a
second hardcoded literal — same claim checked, portable either way.
Cleanup behavior (whether a harness removes what it creates) and the
`camera_backend.py` module-level shape are both decided explicitly in
the build step, not left as defaults.

### Record correction: the intent entry's baseline was sandbox-measured, not the rig's — plus a discovered palette effect

**Supersedes `73537fa`** ("Record intent: Qt environment defaults,
platform-conditional") on one point only: its baseline table's
provenance. Nothing else in that entry is disputed. `73537fa` already
flagged this in its own text — "this sandbox, not the rig — no gtk3
desktop session or dbus here, so this demonstrates the mechanism, not
the rig's exact numbers" — but that caveat lives inside an entry that
can never be edited, and a caveat is easy to lose once later entries
pile up on top of it. This entry exists so the point has its own record
rather than surviving only as a sentence inside someone else's context.

To restate it plainly: the 9.0pt "Sans Serif" (no gtk3 theme) versus
10.0pt "Sans" (with the gtk3 theme) baseline in `73537fa` was measured
under Xvfb, in a sandbox with no labwc session, no dbus, and no real
gtk3 install. It demonstrates that the `setdefault` mechanism has an
effect; it is not a measurement of the rig. The real difference measured
on the tablet was far larger than the sandbox's ~11% gap.

**DISCOVERED: applying the gtk3 platform theme changes the
application's palette, not only its font metrics.** With no platform
theme selected, the app now renders in the system's light palette, where
it previously showed a darker default. Selecting a theme in-app restores
the intended appearance, so this is a consequence of the fix rather than
a defect in it — the intent entry named font metrics as the problem, and
the outcome also touched appearance, which is why it belongs in the
record rather than going unmentioned because it wasn't what was asked
for.

Durable fact worth carrying into the three-platform work: the QSS themes
(`themes/*/style.qss`) override only part of the palette. `gtk3` fills
in the rest on Linux, which is why the appearance shifted the moment the
platform theme started loading. On macOS and Windows, where this
platform-conditional change never sets a platform theme at all, the
app's appearance will be the QSS over whatever palette those platforms
themselves supply underneath — not the same underlying default this fix
now produces on Linux.

### Record on-rig confirmation: Qt environment defaults, platform-conditional

**Confirmed on-rig.** Single record, not an intent/build series — the
platform-conditional environment defaults are already committed (intent
`73537fa`, build `6e12e39`/`8061187`, confirmation-carve-out entries
`d68085e`/`e6636a2` on the file's other read sites); this is the outcome
record for the acceptance criterion the intent entry named, now met on
the actual hardware rather than in a sandbox.

**Command run:** `env -u QT_QPA_PLATFORMTHEME python3 qt_shell.py`

- The UI renders at correct size with `QT_QPA_PLATFORMTHEME` unset in the
  ambient shell before launch. Nothing exported it, so the size Qt lays
  out with comes from the Linux-gated
  `os.environ.setdefault("QT_QPA_PLATFORMTHEME", "gtk3")` at
  `qt_shell.py:99` actually taking effect on this machine — not from a
  shell variable standing in for it, which is what every prior check
  (self-check, sandbox import) had to rely on instead.
- Theme selection still works afterward; `discover_themes()` is
  unaffected by the environment-defaults change, as expected — it reads
  `themes/` on disk and has no dependency on `QT_QPA_PLATFORM`/
  `QT_QPA_PLATFORMTHEME`.
- **Real camera path confirmed separately, same session, not
  `FakeCamera`:** preview streams, focus aid is live and scores, ROI
  draws, Reprobe returns sane exposure. This is hardware behavior, not
  something the platform-conditional change itself touches, but it
  confirms the rig is in its normal working state under the new
  environment defaults, not merely that a window opened.

**What remains unverified: the non-Linux branches.** The
`sys.platform.startswith("linux")` gate around both setdefaults
(`qt_shell.py:97-99`) has now run on this rig and in the sandbox — both
Linux. Nobody has run this on macOS or Windows, and nothing above should
be read as covering them; the code path that skips both setdefaults on
those platforms is still unexercised.

### Record confirmation, revised: other `QT_QPA_PLATFORM` sites in `qt_shell.py`

**Supersedes `d68085e`** ("Record confirmation: other `QT_QPA_PLATFORM`
sites in `qt_shell.py` don't assume line 83/98 ran"). That entry's
verdict was right — nothing breaks — but it stated the conclusion
without the per-site reasoning that makes it durable, didn't name the
shape a future unsafe site would take, and blurred "verified" across
two different things that shouldn't have been blurred. This is a
correction to an outcome entry, not new work, so it's a new entry per
the convention, not an edit to the old one — the old entry stays
visible above/below wherever it lands in the file, showing what was
recorded the first time.

**Also worth flagging on its own: this is the confirmation carve-out's
first actual use** — "where the work is the outcome, a single entry
with no intent phase" — so getting its shape right here is worth the
extra pass; it's the entry most likely to be pattern-matched against
later.

**Per-site reasoning, not just the verdict:**

- `_onboarding_session_is_interactive` (~line 1135) is safe **by
  construction, not by luck**. It reads
  `os.environ.get("QT_QPA_PLATFORM", "")` — the `""` default stands for
  "no signal," and the function's own design already treats "no signal"
  as fall-through to erring interactive. That was true before this
  change too; the platform-conditional setdefault didn't make this site
  safe, it was already written to not need an ambient value.
- The three self-check blocks (~5965, ~6067, ~6102) are safe because of
  their `finally`, not merely because they currently work. Each saves
  whatever `QT_QPA_PLATFORM` is right now, **directly assigns**
  (`os.environ["QT_QPA_PLATFORM"] = "xcb"`, never `setdefault`) its own
  test values, and restores the original — or pops the key entirely if
  there wasn't one — in `finally`, unconditionally. The `finally` is
  what makes this self-contained rather than order-dependent.
- The docstring at ~3883 is prose describing
  `_maybe_show_onboarding_gate`; it isn't a read site and has no
  behavior to be safe or unsafe.

**What would NOT be safe — the shape to check a new site against:** code
that does `os.environ["QT_QPA_PLATFORM"]` (a bare read with no default,
raising `KeyError` on absence) or that branches on `== "xcb"` as if the
variable is always present. Either would have worked by accident while
the setdefault ran unconditionally and would break the moment it
doesn't — which is now, on non-Linux. That is the actual regression this
platform-conditional change could have introduced elsewhere in the file,
and it's what the search above was checking for.

**The `--render-check` evidence is real but partial, and the two halves
don't cover the same claim.** This sandbox is Linux, so
`sys.platform.startswith("linux")` is true here and the block at line
97-99 runs — the passing assertions (`_onboarding_session_is_interactive
check PASS` ×2, `Onboarding gate ... check PASS` ×3) confirm these
readers behave correctly **with the block having run**, which is the
Linux case. They exercise nothing about the case where the block never
runs, because nothing in this environment can produce that case — there
is no non-Linux `sys.platform` to test against here. The per-site
reasoning above is what covers the non-Linux case (each site's own live
default or self-contained save/assign/restore, argued from the code,
not from an execution); the test coverage does not extend there and
isn't described as if it does.

### Record confirmation: other `QT_QPA_PLATFORM` sites in `qt_shell.py` don't assume line 83/98 ran

Single entry, no intent phase — this confirms already-built code rather
than building anything, per the convention's own carve-out for outcome-
only work. Prompted by a review question after the platform-conditional
change landed (previous entry, below): does anything else in the file
read `QT_QPA_PLATFORM` expecting the module-level `setdefault` to have
already set it, now that the setdefault is Linux-only?

Checked every site (`grep -n "QT_QPA_PLATFORM" qt_shell.py`):

- `_onboarding_session_is_interactive` (~line 1135) reads
  `os.environ.get("QT_QPA_PLATFORM", "")` live, defaulting to `""`.
  Absent (non-Linux, or Linux with the var genuinely unset) resolves to
  `""`, which isn't `"offscreen"`/`"minimal"`, so it falls through to
  the function's own documented "errs toward interactive" behavior. No
  dependency on line 83/98 having run.
- The docstring at ~line 3883 (`_maybe_show_onboarding_gate`) is prose
  describing that function, not a read site itself.
- The three self-check blocks (~5965, ~6067, ~6102) each save whatever
  `QT_QPA_PLATFORM` currently is, **directly assign** their own test
  values (`os.environ["QT_QPA_PLATFORM"] = "xcb"`, `"offscreen"`, etc.
  — not `setdefault`), and restore the original in `finally`. Fully
  self-contained regardless of what ran at import time.

Nothing reads `QT_QPA_PLATFORMTHEME` anywhere in the file except the two
lines that set it (line 89-99 comment block and the setdefault itself,
same grep).

**Verified, not just reasoned through:** `python3 qt_shell.py
--render-check`'s log shows the exact assertions at all of the above
sites executing and passing against the edited tree —
`_onboarding_session_is_interactive check PASS` (both variants) and the
three `Onboarding gate ... check PASS` lines. No code change was needed;
nothing here is a defect, so nothing was fixed and no `# CAVEAT:` was
added — none of these sites is fragile or non-obvious in a way a future
reader would trip over, they were simply confirmed unaffected.

### Record build: Qt environment defaults, platform-conditional

Built exactly to the intent recorded below, no correction needed.

**Against the plan:** `qt_shell.py`'s environment-defaults block, still
at line 83 as recorded (verified on open, unchanged). The existing
`QT_QPA_PLATFORM=xcb` setdefault and a new `QT_QPA_PLATFORMTHEME=gtk3`
setdefault both now sit under `if sys.platform.startswith("linux"):`.
`sys` was already imported at line 62 — no new import needed, matching
the intent's expectation that it might be. The comment above the block
was rewritten in the same commit: a new lead sentence states both lines
are Linux-only and why, the original XWayland/nested-native-window
paragraph is unchanged prose, and a new paragraph carries the gtk3
rationale — line 71's "still wins" claim stays true of both setdefaults
now, not just the one it originally described. Scope held: `qt_shell.py`
only in the build commit, nothing else touched.

**DISCOVERED: no matching HANDOFF.md backlog line existed to remove.**
The intent's record-build step said to "remove the QPA platform default
from the backlog, since it is now done," but grepping `HANDOFF.md` for
`gtk3`, `platformtheme`, `font`, and the general backlog/known-limitation
sections found no prior entry describing this issue under any wording —
it was never logged as a tracked backlog item in this repo. There is
nothing to delete, so nothing was deleted. What HANDOFF gets instead is
the thing it's actually for: a new note under "Things that will bite you
if you don't know them" recording the fix, the root cause, and that it
is self-check-verified only, not yet confirmed on-rig. This is a fact
about the repo's documentation state, not about a line of code, so it
gets no `# CAVEAT:` — the discovery is recorded here and in HANDOFF
itself, which is where a future agent would actually look.

**DISCOVERED: this sandbox was missing `numpy`, `tifffile`, `PyQt6`, and
`Pillow`**, none of which are a fact about any line of this project's own
code — a from-scratch container without the project's runtime installed.
Installed all four to run the self-checks and the baseline/acceptance
measurements; nothing about the fix required them. `Pillow`'s absence is
already self-documented in `hdr_from_session.py`'s own error message
("Pillow missing for PNG/JPG"), so no new `# CAVEAT:` was warranted
there either — the existing message already says exactly this.

**Acceptance:**

- `python3 camera_backend.py --render-check` and all 16 modules'
  `--render-check`, including `qt_shell.py`'s, pass, exit 0 (`qt_shell.py`
  needed `Pillow` installed first, per the discovery above; confirmed the
  same assertion fails identically on the pre-edit tree, so it isn't a
  regression from this change).
- **The actual test, run and passing:** with `QT_QPA_PLATFORM` and
  `QT_QPA_PLATFORMTHEME` both unset in the ambient environment, importing
  `qt_shell` (which runs the new module-level block) and then
  constructing a `QApplication` reports platform `xcb`,
  `QT_QPA_PLATFORMTHEME` now `gtk3` in `os.environ`, and font `Sans 10.0`
  — matching the "variable set manually" baseline exactly (`Sans 10.0`),
  not the no-theme baseline (`Sans Serif 9.0`). A sanity check the other
  direction confirms `setdefault` doesn't override an explicit value:
  forcing `QT_QPA_PLATFORMTHEME=""` before import leaves it empty after
  import and reproduces the no-theme font exactly.
- **This exercises the mechanism, not the rig.** It's an import +
  `QApplication` construction in this sandbox (no dbus, no gtk3 session,
  no labwc), not a full interactive launch and not the actual hardware —
  nobody has run this on Mac, Windows, or the rig itself. The Linux
  branch's logic is verified; the two platform branches are not, and
  are not described as verified anywhere above.

### Record intent: Qt environment defaults, platform-conditional

First change built under the intent/build/record-build convention with
the convention itself already present in `PHILOSOPHY.md`. Own branch off
`main`.

**Problem.** `qt6-gtk-platformtheme` 6.4.2 is installed on the rig, but
Qt never loads it: labwc does not advertise itself in a way Qt maps to
`gtk3`, so `QT_QPA_PLATFORMTHEME` never gets a value and Qt falls back to
its own built-in default font. Qt lays out every widget from font
metrics, so the whole app renders smaller than the desktop's own UI on
the same screen at the same resolution. The app sets no font anywhere —
no `setFont`, no `QFont`, no `setPointSize` — and `themes/dark/style.qss`
touches only borders, radius and padding, so the undersized rendering is
entirely inherited from the platform default, and there is nothing in
the layout itself to enlarge. Hardcoding sizes would be the wrong fix and
would break Mac and Windows, where the native default is different again
and correct on its own.

**Baseline, measured before any file was touched (this sandbox, not the
rig — no gtk3 desktop session or dbus here, so this demonstrates the
mechanism, not the rig's exact numbers):**

| Condition | Font | Point size |
|---|---|---|
| `QT_QPA_PLATFORM=xcb`, no theme set | Sans Serif | 9.0 |
| same, plus `QT_QPA_PLATFORMTHEME=gtk3` | Sans | 10.0 |

`sys.platform`: `linux`. `XDG_CURRENT_DESKTOP`: unset. `QT_QPA_PLATFORMTHEME`:
unset in the ambient environment before either command above set it
manually. The gap between 9.0 and 10.0 pt (about 11%) is what the missing
theme costs, and it is what the build record below compares against.

**Plan.** Two changes, both at the environment-defaults block currently
at `qt_shell.py:83` (verify on open — line numbers drift, don't trust a
number recorded ahead of the edit):

1. The existing `os.environ.setdefault("QT_QPA_PLATFORM", "xcb")` becomes
   Linux-only. The XWayland-over-Wayland reasoning in the comment above
   it is Linux-specific and still correct there; it is meaningless on Mac
   and Windows, where Qt should be left to pick its own platform.
2. A new Linux-only
   `os.environ.setdefault("QT_QPA_PLATFORMTHEME", "gtk3")`.

Both stay `setdefault`, deliberately — an explicitly set variable still
wins, which is what makes a blanket `gtk3` default safe: a desktop that
cares, KDE for instance, already exports `QT_QPA_PLATFORMTHEME` itself,
so this only fills the gap where nothing set it.

**Scope.** `qt_shell.py` only in this step's build; this entry and a
`HANDOFF.md` update land in the record-build step. No other file, and
nothing on the out-of-scope list for this change.

### Record build: three-phase convention in PHILOSOPHY.md

Built exactly to the intent recorded below, no correction needed.

**Against the stated scope:** `CHANGELOG.md` touched first (this
series), then `PHILOSOPHY.md`, then `CHANGELOG.md` again, in that order
— nothing else. The two-phase paragraph at (then-)356-360 is gone, not
duplicated alongside a new version: `grep -n "edited" PHILOSOPHY.md`
now returns exactly one line, "correction or an outcome is superseded by
a later entry, never edited into the old one," which is the new rule
denying the thing the old paragraph licensed, not a second copy of the
old wording. The convention landed split across the two sections named
in the intent — the enforceable half under "Strict rules" (new rule
after the `calib/` directory rule), the pointer plus the HANDOFF-specific
carve-out under "Documentation as a first-class artifact." Every other
section is byte-for-byte unchanged, and no executable file was touched.

**Against the measured baseline: 382 lines before, 433 after — 56
insertions, 5 deletions, +51 net.** That is what stating the convention
in full, in this file's own voice, actually costs: the file was short
enough to read start to finish at 382 lines, and it still is at 433, but
that margin is smaller now and is worth watching on the next addition.

**Every clause in the source wording made it in.** Cross-checked line by
line against the plan: never-modified, intent-supersedes-intent with
reason and visible old entry, redo's `supersedes` pointer, the
applies-to-divergent-outcomes scope with the single-entry carve-out for
outcome-only work, per-phase commits with the intent-commit-first
ordering and why that's what makes it checkable, the measured-baseline
requirement with its count-or-scope split, build-then-record-then-fix-
the-code-not-the-record, `DISCOVERED:`/`# CAVEAT:` marking, and no-
retroactive-recording. Nothing was dropped, so there is nothing to flag
here.

**Acceptance, checked after the build:** `python3 camera_backend.py
--render-check` exits 0 — full self-check sweep unchanged, which is what
"documentation only" means in practice here, not just in the commit
message. Both halves are independently reachable: the "Strict rules"
addition never refers out to the documentation section to complete
itself, so an agent reading only that section gets the whole enforceable
convention.

### Record intent: three-phase convention in PHILOSOPHY.md

Documentation only. Branches off `philosophy/sensor-driver-boundary`
(`1d1eda2`/`492c5bd`/`d51d1f8`, plus `49db921`), not `main` — that branch
is unmerged and touches the same file, and there is no reason for two
independent edits to `PHILOSOPHY.md` to conflict with each other.

This project has been running an intent/build/record-build convention in
practice for a while — every entry above this one is evidence of it — but
`PHILOSOPHY.md` still only states the two-phase version, and states it in
a way that is now actively wrong: "that entry is edited or followed up"
licenses editing a landed entry, which the append-only stores rule two
sections up already forbids for JSON and which practice has never
actually done to a CHANGELOG entry either. The gap is between what the
file says and what has been true the whole time. This entry closes it by
writing down the convention this series is itself following, split across
two sections: the enforceable half under "Strict rules," and a pointer to
it under "Documentation as a first-class artifact" replacing the
paragraph that contradicts it.

**Scope, stated the way a documentation change has to be, since there is
no line-count-style number to give it:**

- Files touched, in this order: `CHANGELOG.md` (this entry), then
  `PHILOSOPHY.md` (the build), then `CHANGELOG.md` again (the build
  record). No other file.
- Replaced: the two-phase paragraph at `PHILOSOPHY.md` lines 356-360
  ("Entries are written in two phases... edited or followed up...").
  Exactly that paragraph, not the section around it.
- Added: the convention itself, split across the two sections named
  above.
- Left alone, deliberately: every other section of `PHILOSOPHY.md`, and
  every executable file in the tree.

**Measured baseline: `PHILOSOPHY.md` is 382 lines before this build.**
The build record below states the line count after, so the cost of
spelling the convention out in full is visible as a number, not just
asserted.

**Acceptance for the build:** no executable file modified —
`python3 camera_backend.py --render-check` exits 0 before and after,
which is how "documentation only" gets proven rather than claimed. The
file states the never-modified rule once, not twice — `grep -n "edited"
PHILOSOPHY.md` should find nothing once the build lands. Both halves
must be independently reachable: an agent reading only "Strict rules"
gets the complete enforceable convention without needing the
documentation section.

## 2026-08-01

### Record on-rig confirmation: PyQt5 to PyQt6 port

**Confirmed on-rig. All four deep-verification items pass. No port
defect was found.** This is a single record, not an intent/build/record
series — there is no build here, the port and its binding fix are
already committed; this is the outcome record for work already landed.

1. **Live Measure freeze and crop-aware conversion.** Clicks freeze and
   register point 1 on the clicked feature. Tested at capture 4056x3040
   and preview 1332x990 — the configuration that actually exercises the
   1.5225 crop conversion. A specimen measurement at 40x read 22.658 um
   over 162.5 px (0.1394 um/px), within 1% of the 4x calibration of
   1.4084 scaled by ten — two independently calibrated objectives
   agreeing.

2. **Measure tool at 4x** against a 1 DIV = 0.1mm stage micrometer. This
   is the item that mattered, because the port's only non-formulaic
   change was `ev.x()`/`ev.y()` to `ev.pos().x()`/`ev.pos().y()`, and the
   risk was a systematic offset of roughly one preview pixel, about
   1.4 um at 4x.

   Full span: 2607.345 um over 1851.3 px -> 1.40839 um/px against a
   stored 1.4084. Confirms the calibration is applied intact, though it
   is arithmetic rather than an independent check.

   Single division, the sensitive form of the test, since a fixed pixel
   error is diluted by a long span but not by a short one:

   | Position | Sample 1 | Sample 2 | Mean |
   |---|---|---|---|
   | left | 100.774 | 98.828 | 99.80 |
   | centre | 99.777 | 100.714 | 100.25 |
   | right | 101.675 | 101.607 | 101.64 |

   Mean error across all six samples is under 0.2 px, and individual
   samples straddle the true 100 um in **both** directions. A truncation
   offset is constant and unidirectional; this is not that. The `pos()`
   over `position()` decision (see `HANDOFF.md`) is vindicated by
   physical ground truth, which is stronger evidence than agreement with
   `main` would have been.

3. **Z-stacking.** Start Z-Stack works, ROI resets after each capture,
   post-stack processing offer appears. That last one is the first
   hardware confirmation of the `ProcessWizard.exec()` path, and
   therefore of the `_FakeWizard.exec_` to `exec` rename, since nothing
   else in the session opens the wizard.

4. **Focus aid.** `F` enables and disables. Score and percentage both
   respond across the focus range and reach 100%. ROI is draggable,
   exercising the `ev.pos()` sites at `qt_shell.py:5620` and `:5622`.

Light list also confirmed: app launches under labwc, preview streams,
capture writes files, dialogs open, menus populate.

Also confirms the picamera2 Qt binding fix (recorded below, this same
date) — the branch would not have launched at all on-rig without it.
That fix is now permanent rather than provisional.

**Backlog found during the bench — not fixed, recorded only:**

1. ROI box jumps inward slightly on resize before moving in the dragged
   direction, making enlarging feel like it fights you. **Reproduced on
   `main` under PyQt5 — pre-existing, not a port regression.** Suspected
   anchor/hit-test logic rather than coordinate handling.
2. Focus aid rebases onto the plane just captured during a Z-stack, so
   the just-captured plane reads as peak and the aid must be reset
   manually to find the next plane. **Reproduced on `main` —
   pre-existing.** Related idea from the same session: auto-enable focus
   assist when a Z-stack starts, if not already on. Both are really the
   same question — the aid does not know a stack is in progress.
3. Under raised `Xft.dpi` the UI scales correctly but the GL preview
   viewport does not follow the widget: the frame renders at its old
   size anchored bottom-left, with an uncleared framebuffer around it,
   and window resize or fullscreen toggle does not correct it. **Not
   A/B'd against `main` — unclassified, not confirmed pre-existing.**
   Possibly the same defect as the old quarter-screen fullscreen bug at
   compositor scale=2 (closed as an OS-level issue) by a different route.
4. Possible field-scale gradient at 4x, roughly 1.8 um left to right
   across the field (left 99.80, centre 100.25, right 101.64 from item 2
   above). With n=2 per position and within-position scatter up to
   1.9 um, this is suggestive rather than established. Monotonic rather
   than radially symmetric, so it points at tilt rather than objective
   distortion. **Not a port defect**: a truncation offset would be
   constant across the field; this varies with position. Deliberately
   deferred. Discriminating test on record for later: rotate the slide
   180 and re-measure left and right — if the gradient follows the slide
   it's the slide, if it stays with the field it's optics or sensor.

### Build: picamera2 Qt binding selection — self-check only, NOT yet on-rig

Built to the intent recorded below it, with no correction — the fix was
exactly the one line planned. `QGl6Picamera2` was confirmed present in
the rig's installed picamera2
(`/usr/lib/python3/dist-packages/picamera2/previews/qt.py`), resolved via
the same `__getattr__` table that maps `QGlPicamera2` to PyQt5, so the
underscore-private fallback (`_get_qglpicamera2`/`_QT_BINDING`) was not
needed.

`camera_backend.py:764` (now `:769`) is now `from picamera2.previews.qt
import QGl6Picamera2 as QGlPicamera2`. Aliased so the construction at
line 882 and the comments at 754, 808, 881, 1267, 1281 all keep referring
to `QGlPicamera2` unchanged. The stale `# ON-RIG: confirm this import
path` comment above it is replaced with a `# CAVEAT:` explaining the
binding coupling, so a future import-tidying pass does not read the
6-suffix as a typo, revert it, and reproduce the abort. `# CAVEAT:` is a
new marker convention — the first seed for the function-index generator
to harvest later.

**`grep -c "QGl6Picamera2" camera_backend.py` returns 2, not the 1 the
plan's acceptance criterion named.** The caveat comment itself, which the
plan explicitly said not to skip or simplify away, spells out `"QGl6Picamera2
-> PyQt6"` in prose — that line matches too. Not a defect in the fix; the
plan's own acceptance check didn't account for its own caveat text
containing the string. The import line is the only place the name is
actually used as code.

**All 16 modules with `--render-check` still pass, exit 0** — `pixel_hash
annotations export publish calibrate measure ca_measure wizard_pages
qt_shell stacks focus gallery process_wizard provenance camera_backend
plane_cache`. As expected and as the plan itself said: these run against
`FakeCamera` and never import this path, so this proves nothing about the
fix, only that nothing else broke.

**Real acceptance is on-rig** — app launches, preview streams — and is
not something this task could self-check. Not yet run.

**Recording note, per the plan.** Until this, `port/pyqt6` differed from
`main` by the port's three commits and nothing else, which is what made
bench comparisons attributable. That property is now weakened by exactly
this one line + comment; whoever compares builds next needs to know this
diff is here and is not part of the port. The port itself was never at
fault — the deep-verification list in `HANDOFF.md` is unchanged and still
only tests `ev.pos()` and the enum scoping, the two non-formulaic things
the port actually changed.

### Record intent: picamera2 Qt binding selection

Branch `port/pyqt6`, not `main` — `main` is still PyQt5 and this change
would break it. Own intent/build/record-build series, separate from the
port's three commits, so a bench failure can be attributed to the port or
to this independently.

**The failure, on-rig, at startup:**

```
QWidget: Must construct a QApplication before a QWidget
Aborted
```

libcamera is fine up to that point — every sensor mode enumerates,
imx477 comes up, `create_preview_configuration()` and `configure()` both
return. It dies constructing the preview widget.

**Root cause.** `camera_backend.py:764` imports `QGlPicamera2` from
`picamera2.previews.qt`. That module resolves widget class names lazily
through a module-level `__getattr__`, and the class name *is* the Qt
binding selector — there is no auto-detection and no environment
variable:

| Name | Binding |
|---|---|
| `QGlPicamera2` | PyQt5 |
| `QGl6Picamera2` | PyQt6 |
| `QPicamera2` | PyQt5 |
| `Q6Picamera2` | PyQt6 |

Plain `QGlPicamera2` always means PyQt5. Under a PyQt6 `QApplication`
that builds a PyQt5 C++ widget, which asks Qt5 whether an application
exists, gets null — Qt5 and Qt6 are separately loaded C++ libraries and
cannot see each other's application object — and aborts. **Not a port
defect.** Nothing in the enum-scoping or `ev.pos()` diff touches this;
it's a picamera2 API detail that only surfaces once the app is actually
PyQt6.

**Not the fix:** uninstalling `python3-pyqt5`. `main` still needs it, and
benching the new build against the old one on the same rig is why the
port is on a branch at all. The binding has to be selected in code so
both builds coexist.

**Planned fix, one line:** alias the import,
`from picamera2.previews.qt import QGl6Picamera2 as QGlPicamera2`, so the
construction at line 882 and the comments at 754, 808, 881, 1267, 1281
keep referring to `QGlPicamera2` unchanged. `QGl6Picamera2` will be
verified present on the rig's installed picamera2 before the alias is
written; if absent, the fallback is the underscore-private factory
(`_get_qglpicamera2`/`_QT_BINDING`) since those are confirmed present but
may move between releases, so the named alias is preferred when it
exists.

**What this cannot self-check.** `--render-check` runs against
`FakeCamera` and never imports this path. A green sweep will prove
nothing about this fix, only that nothing else broke. The real check is
on-rig: the app launches and the preview streams.

## 2026-07-29

### Build: PyQt5 to PyQt6 port — self-check only, NOT yet on-rig

Branch `port/pyqt6`. Built to the intent recorded below it, with one
correction to that intent: it said the port changes three things. It
changes five. The two the intent did not anticipate are the interesting
ones.

**The three formulaic categories, as scoped:** 153 enum scopings, 31
`exec_()` to `exec()` (two of them `QMenu.exec_(pos)` call sites, not
bare `exec_()`), and `QActionGroup` moving from `QtWidgets` to `QtGui`.
The `preexec_fn` mention in the ffmpeg comment is a subprocess kwarg, not
Qt, and was correctly not swept up — which is the argument for having
matched `.exec_()` specifically instead of the bare token `exec_`.

**Fourth: `_FakeWizard.exec_` had to become `exec`.** It is the test
double `render_check` substitutes for `ProcessWizard` in the z-stack
section. Its production caller is `wiz.exec()` now, so a double still
offering `exec_` would have failed with an AttributeError. Loud, but it
would have failed. The dict key `"exec_called"` is a label rather than an
API name and was left alone.

**Fifth, and the one worth the entry: `ev.x()` and `ev.y()` are removed
in Qt6.** 10 sites over 5 lines, in `calibrate.py` and `qt_shell.py`.

Static analysis did not find this, and no amount of it would have. Both
resolvers written for this port work on `Class.MEMBER` lookups — one
asking whether a bare enum name still resolves, one asking whether every
Qt attribute in the tree exists on real PyQt6. An instance method call on
an event object is invisible to both. What found it was
`qt_shell.py --render-check`, dying in `_live_measure_preview_event` on
the crop-aware click-mapping path — the path confirmed on-rig only
recently, and the first item on the deep verification list.

The lesson is not that the static passes were wasted; they cleared 153
sites with zero ambiguity and confirmed every import. The lesson is that
they were **necessary and not sufficient**, and that this project's habit
of embedding real self-checks in the modules is what covered the gap.

**`pos()` and not `position()`, deliberately.** Qt6 offers
`position().x()` as the modern replacement and it is the wrong choice
here:

- Qt5's `ev.x()` returned `int`
- `position().x()` returns `float`
- `native_point_from_preview_click` and `widget_to_native` both do float
  arithmetic, so a float argument does **not** raise

That combination is the bad one. It would not have failed; it would have
moved every click by up to a pixel. At the current 4x calibration of
1.4084 um/px that is a real change in measured values, arriving inside a
port whose whole promise was that it changes nothing. `pos()` still
returns `QPoint` in 6.11.0, verified by probe, so the values handed to
the geometry functions are bit-for-bit what Qt5 handed them.

`pos()` is deprecated and will eventually go. Recorded in `HANDOFF.md` as
open with a known cause and a decision deliberately not made: choosing
`position()` means choosing float coordinates, and that wants the stage
micrometer, not a code review.

**Zero line drift.** All 13 files have the same line count they have on
`main`; the commit is 205 insertions against 205 deletions. Every
line-number reference in `HANDOFF.md`, in this file, and in the code
comments still points where it did. The out-of-scope `GREEN_PLANE_RES`
bug is still at `qt_shell.py:3452`, the same line the port brief cited.
This cost one deliberate re-edit: a comment correction had grown to
eleven lines and was cut back to two, because nine lines of drift in
`qt_shell.py` would have quietly invalidated every line reference below
it in two documents. Reasoning goes in the docs; the code keeps the
pointer stable.

**Two comments, handled differently, for the same reason.** The blanket
`PyQt5` to `PyQt6` swap ran through prose as well as imports, and two of
its rewrites were not automatically safe:

- The `QWidget.screen()` comment explained the code was avoiding a Qt
  5.14+ API "for broader PyQt5 compatibility." Rewritten mechanically it
  claimed broader *PyQt6* compatibility, which is meaningless when 5.14
  is not a floor anyone can be below. Corrected by hand.
  `QApplication.primaryScreen()` itself needs no migration; this project
  never used the removed `QDesktopWidget`.
- The `findData` comment recorded an empirical finding: `findData` fails
  to match an equal-but-distinct runtime-built tuple, which is why
  `_index_for_data` scans with `==` instead. Reattributing that finding
  to PyQt6 without testing would have been asserting something nobody
  checked. It was tested: `combo.findData(probe)` returns -1 while
  `combo.itemData(1) == probe` is True, under PyQt6 6.11.0. The quirk
  survives, so the workaround stays justified and the comment is now
  true of the library it names.

**Out-of-scope list: nothing touched.** Verified rather than asserted —
zero lines in the diff mention `GREEN_PLANE_RES`, `FULL_RES`,
`FULL_MODE_LBL`, `G_IS_OBJECT`, `BGGR`, or the hardcoded `(3040, 4056)` /
`(1520, 2028)` green-plane shapes.

**Four of the port brief's expected breakages are absent from this tree**,
checked rather than assumed: no `QAction` import, no `QShortcut`, no
`QRegExp`, no `QDesktopWidget`, and neither High-DPI attribute. The brief
flagged the last two as needing care given this project's compositor and
display-scaling history. There was nothing to remove, so that history is
not in play in this diff. Qt6 does turn scaling on unconditionally, which
is a real difference on the tablet display over HDMI, but it arrives from
Qt and not from an edit here, and it cannot be characterized without the
rig.

**What was proved.** No `PyQt5` import anywhere, and the token in no
comment or string either. Every file compiles. Every Qt attribute
resolves against real PyQt6 6.11.0, including every scoped enum path and
every imported name against its module. No event-object method call in
the tree is missing from the PyQt6 event classes. All 12 modules with a
`--render-check` pass headless under the offscreen platform, exit 0, with
48 PASS lines in `qt_shell.py` alone including the live-measure panel,
freeze-fix cases 1-5, canvas-fit cases 1-5, and the Live Measuring click
conversion.

**What was not proved.** Any of it, on hardware. `FakeCamera`, no
libcamera, no sensor, offscreen QPA. Per `PHILOSOPHY.md` this is a
self-check and not verification. The light and deep verification lists,
and the reason all four deep items drop to unconfirmed, are in
`HANDOFF.md` under the port section.


### Record intent: PyQt5 to PyQt6 port

Branch `port/pyqt6`, deliberately not main. Everything currently marked
confirmed on-rig in the UI layer drops to unconfirmed the moment this
lands, and the old build has to stay benchable against the new one during
the deep verification session. That is the reason for the branch, not
caution about the diff size.

**Scope is the port and nothing else.** It is a port, not a rewrite. No
restructuring, no renaming, no fixing things that are visible while
passing through. The out-of-scope list is the one handed over in the port
brief and it is reproduced in `HANDOFF.md` under the port section, because
several items on it are things any agent working in these files will want
to fix and must not.

**Why now.** Qt5 is past end-of-life for open-source users, so Qt6 is
where platform and Wayland fixes land. Sequencing it ahead of the pending
sensor-geometry work means the same modules are not edited twice, and
everything written after this point is built against the new API.

**Method.** The enum-scoping rewrite is not being done from a hand-written
mapping table. PyQt6 6.11.0 was installed and introspected, and every
unscoped `Class.MEMBER` in the tree was resolved by asking the real
library two questions in order: does the bare name still resolve on that
class, and if not, which nested enum actually holds it. A name that
resolved in more than one nested enum, or in none, was to be reported
rather than guessed at. Neither case occurred. A hand-written table was
the available alternative and was rejected because a wrong entry in it
would produce a plausible-looking rewrite that fails at runtime on a rig,
which is the most expensive place in this project to find a mistake.

**Measured scope, before any edit** (13 files carry `PyQt5`, 18,536 of the
project's ~22k lines):

| Category | Count |
|---|---|
| Enum scopings | 153 |
| `exec_()` to `exec()` | 31 across 6 files |
| Module relocations | 1 (`QActionGroup`, `QtWidgets` to `QtGui`, qt_shell.py:366) |
| Ambiguous or unresolved names | 0 |

`qt_shell.py` holds 119 of the 153 scopings. `Qt.LeftButton` alone
accounts for 22, `QEvent.MouseButtonPress` for 13, `Qt.NoModifier` for 12.

**Four of the breakages the port brief warned about do not exist in this
tree**, checked rather than assumed: no `QAction` import, no `QShortcut`,
no `QRegExp`, no `QDesktopWidget`, and no `AA_EnableHighDpiScaling` or
`AA_UseHighDpiPixmaps`. The brief flagged the last two as needing care
given this project's compositor and display-scaling history. There is
nothing to remove, so that history is not in play here. Qt6 turns scaling
on unconditionally, which is a real behavioural difference from Qt5 on the
tablet display over HDMI, but it arrives from Qt rather than from an edit
in this diff, and it cannot be characterized without the rig.

**Two silent-failure risks were probed and cleared, and they are the
reason to write this down rather than trust the loud failures.** Qt6
enums mostly compare equal to their old integer values, but not all of
them do. `Qt.MouseButton.LeftButton == 1` is False and
`Qt.KeyboardModifier.NoModifier == 0` is False, because those two are flag
types rather than int enums. Code comparing `ev.button()` or
`ev.modifiers()` against an integer literal would therefore go quietly
false rather than raising. Every comparison site in this tree was
inspected: all five `button()` comparisons and the single `modifiers()`
site compare enum to enum, so none are affected. This is recorded because
the next person to write `== 0` against a modifier here will not get an
error.

`QMouseEvent.pos()` is deprecated in Qt6 but still present in 6.11.0 and
still returns `QPoint`, verified by probe, so the four call sites are left
alone. `globalPos()` is genuinely gone, and is not used anywhere in the
tree. The 5-argument `QMouseEvent` and 3-argument `QKeyEvent`
constructors that the embedded self-check suite depends on both still
work in 6.11.0, also verified by probe.

**What this port cannot prove.** No rig here, and no picamera2 or
libcamera. Verification available on this branch is limited to: no
`PyQt5` import surviving anywhere, every file compiling, every Qt
attribute in the tree resolving against real PyQt6, and whichever of the
embedded self-checks run headless under the offscreen platform. That last
one is a genuine check of behaviour and not just of syntax, but it is not
the rig, and per `PHILOSOPHY.md` it does not count as verification. The
four deep-verification items are handed over unconfirmed. See the port
section in `HANDOFF.md` for the split lists.

## 2026-07-28

### Build: Preview resolution setting (ROADMAP item 2, REVISED) — self-check only, NOT yet on-rig

User-provided, twice-revised brief (`ITEM2_preview_resolution_brief.md`,
not checked into the repo — supersedes item 2 as originally written in
`ROADMAP_resolution_sensor_calibration.md`). Built directly from the
handed-over brief, no separate intent commit first — same call as the
lores diagnostic: this project's usual intent → build → record convention
is applied retroactively in this record, not manufactured as a commit that
never happened.

The roadmap's original item 2 argued for filtering the resolution menu to
4:3-ish modes, on the theory that a non-4:3 main stream can't be paired
with the fixed 4:3 `LORES_RES` without libcamera dropping lores. **That
theory is dead** — the failure it was inferred from turned out to be the
sensor-mode probe's leftover config (this file's own entry below),
unrelated to aspect. No filtering was built on the strength of a disproven
theory.

**Naming catch before any code was written**: the brief's first draft
called the new setting `stream_resolution`. A dormant `stream_resolution`
pref already exists in this file (the `stream_formats`/`stream_resolutions`
combo, ~qt_shell.py 1720-1723/1966-1967), reserved for a future network
streaming server this backend doesn't implement yet
(`Picamera2Camera.get_capabilities()` deliberately omits those keys — "no
stream server exists in this backend yet"). Using the same name for a
second, unrelated concept (this setting governs `preview_res`, not a
streaming server) would have collided in `gui_prefs.json` and in the
Preferences dialog's own labels. Flagged to the user before writing any
code; the brief was revised to `preview_resolution` throughout (dialog
label "Preview resolution (next launch)"), confirmed before building.

**What landed**, the four pieces belonging to this setting (a fifth piece
from the same investigation — the `_resolution_combo()` fallback fix — is
its own separate commit/entry above: an unrelated defect found in passing,
affecting every resolution combo, not specific to this setting):

1. **The setting itself.** `preview_resolution_kwargs()` (qt_shell.py,
   mirrors `capture_resolution_kwargs()` exactly): `None` → `{}` (camera's
   own `PREVIEW_RES` default applies), else `{"preview_res": (w, h)}`.
   Wired into `main()`'s `Picamera2Camera(...)` construction alongside
   `capture_resolution_kwargs`. A real, enabled "Preview resolution (next
   launch)" combo in Preferences, built from `get_capabilities()`'s
   `video_resolutions` (the same unfiltered list the disabled Video
   resolution combo already uses) — no aspect filter, per the brief's
   explicit "build unfiltered, then test, then filter only if the test
   demands it."

2. **Lores derives from main's aspect.** `camera_backend.py` gains
   `derive_lores_res(preview_res, target_pixels=LORES_RES[0]*LORES_RES[1])`:
   a pure function computing an even-dimensioned lores size matching
   `preview_res`'s own aspect at roughly `LORES_RES`'s pixel count, instead
   of the old fixed `(640, 480)` "4:3 like the sensor" constant.
   `Picamera2Camera.__init__`'s `lores_res` parameter defaults to `None`
   now (was `LORES_RES`) and derives via this function when not explicitly
   overridden, closing the pairing-mismatch class of failure regardless of
   whether it was ever real here.

   This is a real architectural change, not just a constructor default:
   `Picamera2Camera`'s lores size is now per-instance, not always the
   `LORES_RES` module constant. Every place in `qt_shell.py` that drew into
   or converted clicks against the live lores frame by reading the bare
   `LORES_RES` constant would have silently gone wrong for a non-default
   preview resolution otherwise. Traced every such site (a full inventory,
   not a guess) and fixed the ones that needed it: `FocusPreviewWindow.
   __init__`'s `_aspect` and `_ov_bufs` allocation, `lores_point_from_
   preview_click()` (gained a `lores_res` parameter, default `LORES_RES`
   for callers with no live camera), and `_live_measuring_view_point()` —
   all now read `self.camera.lores_resolution()`, a new accessor added to
   `CameraBackend`/`FakeCamera`/`Picamera2Camera` (previously private,
   asymmetric state: `Picamera2Camera` had `self._lores_res`, `FakeCamera`
   had `self._w`/`self._h` and no attribute of that name at all). Render-
   check fixtures using a default-constructed `FakeCamera` are correctly
   unaffected (its `lores_resolution()` still equals `LORES_RES`) — the new
   coverage proves the dynamic wiring with an explicit non-default
   `lores_res` override instead, per this project's own rule (`PHILOSOPHY.
   md`) that a self-check exercising only the default case can't tell
   "wired to the instance" apart from "still silently reading the
   constant."

3. **Ruler overlay, traced per the brief's explicit instruction ("trace it
   before building, not after")**: `_current_ruler_ticks()` computes
   `fov_width_um`/`fov_height_um` from `GREEN_PLANE_RES` (the full-res
   green plane) × calibration's stored `um_per_px` — entirely independent
   of `preview_res`/lores size. `_draw_ruler_ticks_into()` then draws each
   tick at `frac * ov.shape[...]`, i.e. against whatever the overlay
   buffer's OWN actual shape is. So once `_ov_bufs` is correctly sized to
   the derived lores size (item 2 above), tick placement is aspect-
   independent by construction — no code fix needed here beyond that.
   **What tracing could NOT settle, flagged rather than assumed**: whether
   a non-4:3 `preview_res` on real IMX477 hardware actually preserves the
   same physical field of view (just resampled to different pixel
   dimensions) or crops it — a hardware behavior question, not something
   pure code reading can answer. **Worth calling out explicitly**: this is
   a genuinely different, and better, argument for an aspect filter than
   the roadmap's original theory (disproven above) — if a wide preview
   crops rather than preserves FOV, a wide preview shows the user *less
   specimen*, which is a real reason to restrict the menu, not a pairing
   mechanic. Only the rig can answer it; left for the on-rig test below.

4. **Consequence recorded** (per the brief's own instruction): once this
   lands, *preview* resolution is the real control over both live preview
   AND recorded video size, since `start_recording()` always encodes
   whatever "main" (== `preview_res`) currently is — same mechanism the
   video-resolution decoupling entry below already established, just with
   a real, enabled setting driving it now instead of an inert one.

**Verified (self-check only, NOT yet on-rig)**: full 16-module
`--render-check` sweep passes, no regressions. New coverage: `derive_lores_
res` across several preview_res aspects (even-dimensioned, aspect-matched,
including the actual `PREVIEW_RES` default and a wide `2028x1080` case);
`lores_resolution()` on both backends; `preview_resolution_kwargs()`
mirroring `capture_resolution_kwargs()`'s own coverage; the Preferences
dialog's new Preview resolution combo (built from `video_resolutions`,
enabled, persists on OK); `FocusPreviewWindow`'s `_aspect`/`_ov_bufs`/click-
round-trip against a non-default `lores_res`.

**On-rig verification explicitly NOT done this session** — needed before
this can be trusted: set Preview resolution to **2028×1080** (non-4:3) and
confirm focus aid still scores and lores is present in the active config
(the brief's own build-order step 2); a normal launch at a 4:3 non-default
preview resolution confirming preview, focus aid, AND the ruler all read
correctly (settles the FOV-preservation question item 3 above flagged as
unresolvable from code alone, and with it, whether a real aspect filter is
actually warranted — for the FOV reason above, not the disproven pairing
one).

### Fix: `_resolution_combo()` silently misrepresented a persisted value absent from `get_capabilities()`

Found in passing while investigating a user-reported roadmap item (a
preview-resolution setting, its own separate entry above), not the subject
of that work — this defect is independent of it and predates it. Applies
to every resolution combo the Preferences dialog builds through this one
shared helper (capture/video/stream today; any future one), not just the
control it happened to be noticed against.

**Defect**: `_resolution_combo()` can only ever display a value that's
also present in the driver-reported list it's built from. A persisted
preference outside that list (a discrete, sensor-mode-derived list — e.g.
`video_resolution` persisted as `[2028, 1080]`, which isn't an actual
IMX477 sensor mode) silently rendered as "Default (current preview)"
instead of the true stored value. Worse: since Preferences' OK button
unconditionally re-saves every next-launch combo's `currentData()`, this
meant simply opening Preferences and pressing OK — for any reason,
touching that control or not — could silently overwrite a real persisted
preference with `null`.

**Fix**: `_resolution_combo()` now prepends the persisted value as its own
selectable entry when it isn't already in the reported list, instead of
falling back to "Default". It displays honestly and round-trips through
OK unchanged.

**Verified (self-check only, not yet on-rig)**: new render_check coverage
persists `video_resolution` as `[2028, 1080]` (confirmed absent from
`FakeCamera`'s own `video_resolutions`), constructs `PreferencesDialog`,
and confirms the disabled Video resolution combo shows `(2028, 1080)`/
"2028x1080" rather than Default, and that pressing OK persists it
unchanged. Full 16-module `--render-check` sweep passes, no regressions.

### Fix: PHILOSOPHY.md's sensor-profile rule had gone stale (and uncheckable)

Follow-up correction, caught on review of the click-mapping fix below,
not found by this session on its own. The rule as written ("`camera_
backend.py` is the only file in this project that may know what an
IMX477 is") had a property worth keeping even though `imx477.py` had
already outgrown it: it was checkable by a plain grep. Reasoning past the
stale wording without updating it would have left a document that
disagreed with the code it's supposed to govern — the next reader could
reasonably "fix" the disagreement by folding `imx477.py` back into
`camera_backend.py`, undoing the modularity on the authority of a rule
nobody had corrected.

**Rule rewritten** (`PHILOSOPHY.md`): sensor-specific knowledge lives in
sensor-named modules matching the hardware-reported model; those modules
may be imported only by `camera_backend.py`, which itself carries no
sensor-specific constants and dispatches by the hardware's own reported
name. The Picamera2/libcamera half of the original rule is unchanged.

**Made checkable again** (`camera_backend.py`): new
`assert_only_camera_backend_imports_sensor_profiles`, run from the
self-check block alongside the pre-existing
`assert_only_camera_backend_imports_picamera2`. Discovers sensor-profile
modules by shape (`FULL_ARRAY_SIZE` + `crop_for_size`, `imx477.py`'s own
contract) rather than a maintained name list, so a future `imx519.py`
imported straight from `qt_shell.py` would fail this check the moment it
exists. Verified the check actually catches a violation, not just passes
vacuously (a throwaway sibling file importing `imx477` directly, deleted
after confirming the assertion fired).

Verified: `python3 camera_backend.py` self-check passes with the new
assertion included.

### Build: preview-to-green-plane click mapping fix — landed as planned

Builds the intent recorded in the entry below, exactly as planned, no
deviations. New `imx477.py` (driver layer): `FULL_ARRAY_SIZE` +
`crop_for_size(size)`, a static crop-rectangle table for the 5 real
IMX477 modes this project's own on-rig `sensor_modes` read already
confirmed (off-rig fallback / `--render-check` fixture only — on-rig, a
live `sensor_modes` `crop_limits` read is authoritative), plus a
self-check (internal consistency, unknown-size failure, and a
cross-check against the brief's own "~1.52 expected FOV ratio" note,
which came back 1.5225).

`camera_backend.py`: `CameraBackend` gains `preview_resolution()`,
`capture_resolution()`, and `sensor_crop_for_size(size)`. `FakeCamera`
implements all three (delegating crop lookup to `imx477` directly, since
its `get_capabilities()` already reports real IMX477 sizes) and gained
`preview_res`/`full_res` constructor kwargs for render_check coverage of
a non-default pairing. `Picamera2Camera` resolves its sensor-profile
module from the hardware's own `camera_properties['Model']` string (new
`_resolve_sensor_profile`: exact-name import, restricted to a same-named
`.py` file next to `camera_backend.py`, never a same-named package
elsewhere on `sys.path`; an unrecognised model raises, naming the real
sensor) — `camera_backend.py` itself never hardcodes `"imx477"`. Per-mode
crop rectangles are cached from the same `sensor_modes` read
`get_capabilities()` already primes, never a second sweep.

`qt_shell.py`: `native_point_from_preview_click` keeps its name (the Live
Measuring boundary check already forbids it in the unrelated pixel-only
feature) but its body is now the full three-step chain instead of one
letterboxing-aware fraction. The one production call site
(`_live_measure_preview_event`) sources both crop rectangles from
`self.camera.sensor_crop_for_size()`, fed by the new accessors — never a
`GREEN_PLANE_RES`/`PREVIEW_RES` module constant. Every render_check call
site updated; new coverage proves the identity case matches the OLD
formula exactly and a real off-centre crop pair converts through both
rectangles and lands somewhere genuinely different.

**Verified**: `python3 imx477.py`, `camera_backend.py`, and `qt_shell.py
--render-check` all pass, plus a full sweep of every other module with
its own `--render-check` (17 total). **On-rig verification is explicitly
NOT done** — no hardware access this session; the stage-micrometer test
the brief specifies is still outstanding, so the interim workaround
(freeze, Escape, place both points on the frozen canvas) stays in effect
until someone runs it. See `HANDOFF.md`'s matching entry for the full
verification list and the interim workaround.

### Intent: preview-to-green-plane click mapping fix (promotes roadmap item 3)

Recording intent before building, per this repo's two-phase documentation
rule. Full brief in a user-provided `PRIORITY_click_mapping_fix.md` (not
checked into the repo). **Measurement-accuracy defect, confirmed on-rig,
outranking the rest of the roadmap**: a stage micrometer shows 19
divisions in the live preview but 27 in the frozen plane (~1.42x wider
field) — the freeze-triggering click's point 1 lands at a different place
on the frozen plane than where it was actually clicked. Points 2+ are
unaffected (frozen-canvas clicks, no cross-view conversion).

**Root cause**: `native_point_from_preview_click` (`qt_shell.py`) converts
a preview click to green-plane coordinates via one letterboxing-aware
fraction, correct only if the preview and the green plane share a field of
view. They don't — `preview_res` (1332x990) and `full_res` (4056x3040) are
different IMX477 sensor modes with different crop rectangles read off the
array, and the smaller mode is a genuine crop, not a binned-down full view.

**The plan**: a new `imx477.py` sensor-profile module (driver layer,
alongside `camera_backend.py`) exposing each mode's own crop rectangle
(origin + extent, never a scale factor — an off-centre crop can't be
expressed as a ratio); three new `CameraBackend` methods
(`preview_resolution`/`capture_resolution`/`sensor_crop_for_size`, with a
plausible `FakeCamera` implementation); `native_point_from_preview_click`'s
body replaced with the full three-step chain (fraction -> sensor
coordinate via the preview mode's crop -> green-plane coordinate via the
still mode's crop), staying a pure, Qt-free function. Full reasoning,
including the user's own mid-brief instruction that the profile module's
name must match `Picamera2().camera_properties['Model']` EXACTLY (a direct
lookup with no mapping table to drift from reality, so an unrecognised
sensor fails loudly by name instead of silently reusing IMX477 geometry),
lives in `HANDOFF.md`'s matching entry — including how this squares with
`PHILOSOPHY.md`'s "only `camera_backend.py` may know what an IMX477 is"
rule.

**Interim workaround for the user until this lands**: freeze with the
click, press Escape to cancel the in-progress shape, then place both
points on the frozen canvas.

No code changed in this commit — `HANDOFF.md`/`CHANGELOG.md` only. See the
matching Build entry once it lands.

## 2026-07-27

### Fix: `Picamera2Camera` construction order left the camera in the `sensor_modes` probe's leftover config

Root-caused from a user-supplied on-rig failure log, not found by this
session. A live-measure freeze capture was coming back as
`main=640x480@XBGR8888, raw=4056x3040@SBGGR16` with `lores` entirely
missing — an earlier main/lores aspect-ratio theory was disproved by the
user reading the log's own libcamera stream-negotiation sweep, whose exact
last line matched the failure byte for byte, including the unrecognized
`SBGGR16` format.

**Root cause**: `get_capabilities()` reads `self._picam2.sensor_modes`,
which is not a passive lookup — internally it calls `Picamera2.configure()`
once per sensor mode to enumerate them, sweeping the camera through every
mode and leaving it sitting in whichever mode was swept last (no lores
stream, since the probe never asks for one). `Picamera2Camera.__init__`
applied the real `self._preview_cfg` (with lores) and built the
`QGlPicamera2` widget against it *before* calling `get_capabilities()` to
prime its cache at the very end of construction — so the sweep ran last,
silently clobbering the real config nothing ever re-applied afterward.

**Fix**: `camera_backend.py` — moved the capability probe to run
immediately after `Picamera2()` construction, before `self._preview_cfg`
is even built. `self._picam2.configure(self._preview_cfg)` and the
`QGlPicamera2` widget construction now happen strictly after the
sensor-mode sweep has already settled, so they're the last thing to touch
the camera's config during `__init__`. `get_capabilities()` itself is
unchanged — only its call site moved.

**Not fixed here**: a second, distinct bug the same failure log surfaces —
a `G_IS_OBJECT` assertion at teardown, a different mechanism from this
ordering bug. Flagged in `HANDOFF.md` as a follow-up, not addressed by
this change.

**Verification**: `camera_backend.py`'s `FakeCamera`-only self-check still
passes. The ordering bug is only reachable through `Picamera2Camera`
(needs real hardware plus a `QGlPicamera2` widget), so this fix is not yet
confirmed on-rig — see `HANDOFF.md`'s own entry for the exact re-test
procedure to run against the failure log this was diagnosed from.

### Build: Decouple video resolution from preview

Builds the intent recorded in the two entries below (Intent, then its
amendment) — landed exactly as planned, no deviations. Built by a
separate, since-unreachable session working directly in this checkout;
this entry (and the on-rig verification below) is from a different
session that took over the same uncommitted working tree, confirmed the
code against real hardware, and committed it.

`qt_shell.py`: removed `video_resolution_kwargs()` and its call site in
`main()`'s `Picamera2Camera(...)` construction — `preview_res` now always
uses its own `PREVIEW_RES` default, so its pairing with the fixed
`LORES_RES` in `create_preview_configuration` can no longer fail and
silently drop the lores stream (the direct fix for the reported focus-aid
bug). Rewrote the now-stale comment block above the removed function and
`capture_resolution_kwargs`'s docstring. Removed the dead render-check
block that exercised `video_resolution_kwargs()` directly. Preferences
dialog: `_video_res_combo` is `setEnabled(False)` with an explanatory
tooltip, per the amendment below — still populated from
`get_capabilities()`, still persists to `gui_prefs.json` against a future
Record-button rework.

`camera_backend.py`: corrected the stale `__init__` comment above
`self._video_res = preview_res` — the actual source of the false premise
the roadmap's first draft took as fact — to state plainly that
`self._video_res`/`set_video_resolution()` are dead code today. Left the
`DIAGNOSTIC` `camera_configuration()` dump prints (from the still-open
lores-at-default investigation a few entries below) untouched — unrelated
bug, still needs its own on-rig repro.

**Verified on real hardware, on this rig, in this session**: `gui_prefs.json`
still had `video_resolution: [2028, 1080]` persisted from before this fix
(non-4:3, the exact shape that used to break the pairing). Ran
`qt_shell.py --camera` twice with that preference still in place;
`camera_configuration()` at both diagnostic checkpoints (right after
`create_preview_configuration()` and right after `configure()`) showed
`main` at the correct `1332x990` and `lores` present and correctly sized
at `640x480` — the preference is no longer read, so it can no longer
break the pairing. Confirms the reported bug (the one this session's user
was actually seeing — a non-default video-resolution preference killing
focus aid) is fixed. Full 16-module `--render-check` sweep also passes, no
regressions.

**Does not touch or resolve** the separate `main=640x480`/lores-missing
anomaly documented in this file's 2026-07-26 entries and in `HANDOFF.md` —
that failure was observed via a different mechanism (a genuine decode
failure caught mid-preview, not a rejected pairing at construction) and
remains open; this session did not attempt to reproduce it and isn't
claiming it's resolved.

### Intent: Decouple video resolution from preview

Recording intent before building, per this repo's two-phase documentation
rule. First item off a larger roadmap that came out of the on-rig
focus-aid investigation, slotted against what was already queued. Full
plan in user-provided `ROADMAP_resolution_sensor_calibration.md` (item 1)
and `SUPPLEMENT_for_agent_handoff.md` (§1), neither checked into the
repo.

**Reported bug**: focus aid dies (silently — the `(lores MISSING)`
diagnostic, not a crash) whenever the "Video resolution (next launch)"
preference is set to a non-4:3-ish mode. `video_resolution_kwargs()`
(qt_shell.py ~426) feeds that preference straight into
`Picamera2Camera(preview_res=...)`, which becomes the `main` stream's
size. At e.g. 2028×1080 (≈1.88:1), `main`'s aspect no longer matches the
fixed `LORES_RES` (640×480, 4:3, "like the sensor" per its own comment),
`create_preview_configuration` rejects the pairing, lores is dropped for
the life of the process, and every `make_array("lores")` raises from then
on.

**Correction made to the roadmap itself during this investigation, worth
recording in full so it isn't rediscovered**: the roadmap's first draft
read "video resolution feeds `start_recording()`'s video config only —
which it already does via `self._video_res`," and proposed decoupling
`video_resolution_kwargs()` from `preview_res` as a clean, side-effect-free
fix on that basis. That premise came from a comment in
`Picamera2Camera.__init__` (camera_backend.py ~665-676) describing the
Record button's *intended* future design, not the code as it exists.
Verified against the actual implementation and found false:
`start_recording()` (camera_backend.py ~1039) never reads `self._video_
res` at all — it calls `start_encoder(encoder, output, name="main")`,
encoding whatever the `main` stream already is. `main`'s size is
`preview_res`, fixed once at construction. So the "Video resolution (next
launch)" preference's *only* real effect today is `preview_res` → `main`
stream size → recorded file size; `self._video_res`/
`set_video_resolution()` are dead code, reserved for a Record-button
rework that hasn't happened yet (their own docstring already says so).
This is not newly discovered information on its own — `HANDOFF.md`'s
existing "Video resolution menu detail worth knowing" note already
flagged the `__init__` comment as stale — the roadmap's author simply
didn't cross-reference it before writing item 1. Decoupling exactly as
first specified would have fixed the lores crash while silently turning a
live, user-facing preference into a no-op: recorded video pinned at
`PREVIEW_RES` forever, with no error and no indication. This project
consistently treats a quiet failure as worse than a loud one (cf. the
blanket-except audit, the absent-vs-empty distinction in Export, this
whole lores investigation) — trading one for the other was rejected.

**Revised plan, agreed with the user before any code changes**, doing
both halves in one cycle:
1. Remove `video_resolution_kwargs()` and its call site in `main()`'s
   `Picamera2Camera(...)` construction entirely. `preview_res` reverts to
   its own fixed default (`PREVIEW_RES`) unconditionally — the direct fix
   for the lores crash, and the reason a stream-resolution setting
   (roadmap item 2) is needed before wide/non-4:3 modes can be offered
   again at all.
2. Keep the "Video resolution (next launch)" combo in the Preferences
   dialog — still populated from `get_capabilities()`, still persists to
   `gui_prefs.json` against a future Record-button rework — but
   **disable it** (`setEnabled(False)`) with an explanatory tooltip.
   **Amendment, per user feedback before the build started**: a live,
   enabled combo that still changes, persists, and shows the user's
   choice back to them is a false affordance no matter what its tooltip
   says — the user believes their choice took effect. Disabled with
   "pending Record-button rework" in the tooltip stays discoverable and
   signals it's coming back, without inviting use — a stronger
   treatment than `capture_format`/`video_format`'s existing "persisted,
   not yet applied" tooltip idiom, reserved for controls that are merely
   not wired up yet, not one that used to work and now doesn't.
3. Correct the stale `__init__` comment at camera_backend.py ~665-676 —
   the actual source of the false premise above — so it states the true
   current behavior instead of the Record button's intended future one.

**Explicitly rejected**: wiring `self._video_res` into `start_recording()`
now, which would have kept the preference meaningful. Encoding at a size
other than `main` means either a mode switch at record-start or a third
stream — precisely the pairing fragility that caused this bug — and the
Record button's mode-switching history has already produced a pane freeze
and an exposure shift on real hardware (`start_recording`'s own docstring
history notes). Out of scope for this cycle.

**Consequence, recorded so it's found rather than rediscovered**: once
roadmap item 2 (a stream-resolution setting) lands, *stream* resolution
becomes the real control over recorded video size, since the encoder
always takes whatever `main` is. Video resolution stays persisted but
inert until the Record button itself is reworked.

**Real user-visible regression, called out in its own right (per the
same user feedback)**: this is more than a control going inert. Anyone
who had already set Video resolution to something other than
`PREVIEW_RES` (e.g. 2028×1080) will find their recorded video silently
drop back to `PREVIEW_RES` (1332×990) the next time they launch after
this lands — an existing setting silently stops taking effect, not just
a control that stops responding to new choices. Worth knowing before a
recording session, not discovering after one.

**Verification plan**: on-rig — set video resolution to a non-4:3 mode
(e.g. 2028×1080), confirm focus aid still scores; that single check is
the whole bug. No new pure render-check logic is expected: the removed
function's only behavior was gluing a persisted preference to a
hardware-only constructor argument that `FakeCamera` doesn't even accept
a parameter for. The existing 16-module `--render-check` sweep must still
pass with no regressions.

No code has changed for this yet — see the matching Build entry once it
lands.

## 2026-07-26

### Fix: focus-aid readout label no longer clips mid-word

The lores decode-failure diagnostic text (commit below) got truncated
mid-word on the actual tablet screen during the on-rig run that produced
it — cost a full rig run before the cut-off tail (`(lores M...`) could be
read at all. Cause: `qt_shell.py`'s `self.readout` `QLabel` never called
`setWordWrap(True)`, unlike every other status label in the file
(`capture_status`, `ruler_status`, the wizard note labels), and sits in a
fixed-width (`panel.setMinimumWidth(250)`) splitter panel that won't grow
to fit it — so Qt just clips wherever the pixel width runs out.

**Fix**: `qt_shell.py`. One line: `self.readout.setWordWrap(True)`.
Full `--render-check` sweep still passes, no regressions.

### Diagnostic: two-point `camera_configuration()` dump in `Picamera2Camera.__init__`, to localize the lores-drop

Follow-up to the commit below, same on-rig sitting. That run's screenshot
confirmed a genuine lores decode failure (418 counted, not the still-mode
race — `_lores_error_is_expected` rules that out) even at the **default**
video resolution (`gui_prefs.json`'s `video_resolution` was `null`, so
`preview_res` used its own `PREVIEW_RES=(1332,990)` default — no
override in play). Yet the captured active config read `main=640x480` —
exactly `LORES_RES`'s own size, not the `1332x990` actually requested —
with `lores` MISSING and an unrequested `raw=4056x3040` present. That
kills the resolution-pairing hypothesis the commit below's own `HANDOFF.md`
section had named — it happens at the default resolution too. Two
mechanisms remain, needing different fixes: libcamera's negotiation
silently dropping lores during `configure()` (favors the RGB888/YUV420
shakeout `__init__`'s own comment already names), versus streams being
reported positionally and `main` itself being the stream actually lost,
with `lores` surviving under the `main` label (under which reformatting
lores would fix nothing). Dropping one stream shouldn't resize another,
so the observed `main=640x480` fits the second theory better, but nothing
here proves it yet.

**Fix**: `camera_backend.py`. Two `print(..., file=sys.stderr)` calls,
both routed through the existing `_summarize_camera_configuration` helper
(already safe/plain — no raw libcamera objects held or printed) — one
immediately after `create_preview_configuration()` returns, one
immediately after `configure()` applies it. If `lores` is already absent
from the first dump, the loss happens in Picamera2's own construction,
before libcamera negotiation ever runs. If it's present in the first and
gone from the second, libcamera's negotiation is where it's dropped.
Temporary/diagnostic only, not part of the class's real behavior — no
self-check changes, since off-rig `FakeCamera`/self-checks never
construct `Picamera2Camera` at all (confirmed: full self-check and
`--render-check` sweeps both still pass unchanged).

**Not yet run on-rig.**

### Fix: lores decode-failure diagnostic now captures the active `camera_configuration()`, not just the error text

Follow-up to the commit below (`f4af4fd`), same sitting, before the on-rig
trip that commit's own build note called for. That commit recorded
`last_lores_error`'s exception text and a count, which confirms a genuine
decode failure is happening but not *why* — candidate 1 (the leading
hypothesis, below) claims specifically that `create_preview_configuration()`
silently drops the `lores` stream from the config during its own
internal validation, which means the config actually in effect never has
one. Proving that needs the config inspected at the moment of failure,
not inferred from an error string alone — a check flagged before the
error string was mistaken for sufficient evidence on its own.

**Fix**: `camera_backend.py`. `Picamera2Camera` gains `lores_config_at_failure`,
captured once (not every failing frame — the active config can't change
again without a fresh `switch_mode`/`configure()`, which `_stash_lores`
never triggers, so repeating this on a hot per-frame callback across
potentially hundreds of failures would be pure overhead) via a new pure
`_summarize_camera_configuration(cfg)` helper: pulls only `size`/`format`
out of `main`/`lores`/`raw`, since the real dict carries libcamera objects
(`Transform`, `ColorSpace`) unsafe to hold or print. The capture call
itself is wrapped separately from `make_array` — a `camera_configuration()`
failure of its own must not turn into a crash on the preview thread.
`qt_shell.py`'s `_readout` gains a matching pure `format_lores_config_summary`
and now appends "`-- active config: streams: ... (lores PRESENT/MISSING)`"
to the decode-failure message, stating the one fact candidate 1 turns on
explicitly rather than leaving it for the reader to infer from a raw dict.

**Render-check coverage added**: `camera_backend.py`'s self-check extends
the existing `_stash_lores` stub test with a fake `_picam2.camera_configuration()`
call-counter, proving the still-mode race pays nothing for a dump it
doesn't need, a genuine failure captures the config exactly once (a
second failure does not re-dump it), and a `camera_configuration()`
failure of its own is caught rather than propagating. `qt_shell.py`'s
self-check extends its own `_readout` round trip with both an
uncaptured-config case and a real main+raw (no lores) config, asserting
the exact rendered text. Both pure helpers also get standalone tests.
Full 16-module `--render-check` sweep passes, no regressions.

**Not yet exercised on-rig at all** — self-check-only, same as the commit
below.

### Build: `_stash_lores`'s `RuntimeError` guard now distinguishes an expected still-mode race from a real lores decode failure

Builds the intent recorded immediately below — landed exactly as planned.
Follow-up to an on-rig report: focus aid works at the default video
resolution but fails with "no real lores frames received" after changing
it. Root-cause investigation (read-only, no fix yet at that point) found
the message itself is honest but uninformative — `camera_backend.py`'s
`_stash_lores` (the `post_callback` that decodes the lores stream every
camera frame) has always had a blanket `except RuntimeError: return`
around `request.make_array("lores")`, written for one specific case (a
still-mode request racing this callback, which legitimately carries no
lores stream) but silently swallowing every other `RuntimeError` too —
including a lores stream that's configured but genuinely failing to
decode on every frame, which is exactly what a bad main/lores size
pairing (the resolution bug's own leading hypothesis) would look like.
That conflation is a real defect on its own terms, independent of whether
the resolution bug turns out to be an aspect-ratio mismatch or an ISP
downscale-ratio ceiling: either way, the guard was turning a hard
configuration failure into a silent, generic, 2-second-delayed diagnostic
with the underlying error thrown away.

**Fix**: `camera_backend.py`. `Picamera2Camera` gains two new diagnostic
attributes alongside the existing `lores_frames_received` —
`lores_decode_errors` (a count) and `last_lores_error` (the most recent
exception's own `str()`). A new pure module-level helper,
`_lores_error_is_expected(suspend_lores: bool) -> bool`, formalizes the
classification: `_stash_lores`'s except-block re-checks `self._suspend_lores`
*at the point of failure* (not trusted from the earlier guard alone, since
the real race is `_suspend_lores` flipping true concurrently between that
earlier check and `make_array` actually raising) — true means the known,
silent still-mode race (unchanged behavior, still swallowed with no
record), false means a genuine failure that's now recorded rather than
discarded.

`qt_shell.py`'s `_readout` (the tick that surfaces this to the user) now
reports the real error once one exists: `lores_decode_errors > 0` produces
"lores stream configured but failing to decode (N time(s)): <the actual
exception text>" instead of the generic "no real lores frames received"
message, which is now reserved for the genuinely different case of
`post_callback` never reaching the backend at all (both counters stay at
0 — a distinct failure mode this fix does not touch).

**Render-check coverage added**: `camera_backend.py`'s own self-check
drives the real bound `_stash_lores` method (not a reimplementation)
against a minimal stand-in self/request object, since `Picamera2Camera`
itself can't be constructed off-rig — a successful decode still increments
`lores_frames_received` exactly as before; a still-mode race (the stub
request flips `_suspend_lores` true as a side effect of its own
`make_array`, modeling the real timing) stays silent with nothing
recorded; a genuine failure (never suspended) records both the count and
the exact exception text. `qt_shell.py`'s own render-check drives the
real `_readout` method on a real `FocusPreviewWindow`/`FakeCamera` pair
(the other half of the seam — proving `_readout` actually reads and
formats what `_stash_lores` records, not just that the classification
logic itself is sound in isolation), covering both the unchanged generic
message and the new specific one. Full 16-module `--render-check` sweep
passes, no regressions.

**Explicitly not done in this pass, by design**: this fix makes the real
underlying error visible; it does not attempt to fix the resolution bug
itself. The next step is reproducing the failure on-rig with the widened
logging in place, so the real fix (which likely means reconfiguring the
lores stream alongside the main stream when video resolution changes,
currently pinned at construction — see `camera_backend.py`'s own comment
on `LORES_RES`) gets designed against an actual libcamera error string
instead of a guess. **Not yet exercised on-rig at all** — self-check-only
until the user reproduces the original symptom with this build in place.

### Intent: `_stash_lores`'s `RuntimeError` guard conflates two different failures

Recording intent before building, per this repo's two-phase documentation
rule, though the build (above) landed in the same sitting. Triggered by
an on-rig report: "focus aid works at full video resolution, but fails
with 'no real lores frames received' after changing resolution — lores
stream is not reaching the camera backend, not a scoring bug."

**Decision (user, this session): fix the guard for its own sake first,
verify on real hardware second, only then design the resolution fix
itself** — rather than guessing at the resolution bug's exact mechanism
(aspect-ratio mismatch vs. an ISP downscale-ratio ceiling) and shipping a
fix blind. The two candidate mechanisms need genuinely different lores
sizing strategies (matching main's aspect ratio vs. scaling within a
downscale limit), so picking one without a real repro risks a fix that
works at one resolution and fails at the next.

**Plan**: `camera_backend.py` only. Add `lores_decode_errors`/
`last_lores_error` next to the existing `lores_frames_received`. Extract
the guard's classification into a pure, module-level
`_lores_error_is_expected(suspend_lores)` so it's testable without
hardware (`Picamera2Camera` can't be constructed off-rig at all).
`_stash_lores`'s except-block re-checks `_suspend_lores` at the moment of
failure rather than trusting the earlier pre-`try` check, since the
documented race is exactly `_suspend_lores` flipping concurrently in that
window. `qt_shell.py`'s `_readout` surfaces the recorded error text once
one exists, instead of only ever showing the generic message.

**Non-goals**: no change to the lores stream's actual configuration, size,
or when it's rebuilt — `LORES_RES` stays pinned exactly as before. No
attempt to fix the resolution bug itself in this pass.

**Render-check coverage planned**: `camera_backend.py`'s self-check drives
the real `_stash_lores` bound method (a minimal stand-in self/request, not
a hardware-backed `Picamera2Camera`) through a successful decode, the
still-mode race, and a genuine failure. `qt_shell.py`'s self-check drives
the real `_readout` method on a real `FocusPreviewWindow`/`FakeCamera`
pair, covering both message branches — per this project's own rule
(PHILOSOPHY.md), testing only the pure classifier in isolation would leave
the actual wiring unverified, the same blind spot three earlier bugs here
all shared.

**Verification, planned**: self-check first (this session), then the user
reproduces the original on-rig symptom with this build in place, so the
real fix downstream gets designed against the real captured error text.

### Build: fit frozen Live Measure canvas to its frame, match preview letterboxing

Builds the intent recorded in the prior commit — landed exactly as
planned in three of four steps; the fourth (CALIBRATION INTEGRATION
banner update) was confirmed not applicable, not silently skipped:
`_LiveMeasureCanvas` isn't part of that banner's removal list at all.
`qt_shell.py` only, entirely inside `_LiveMeasureCanvas`.

`_fit_to_view` factors the fit out, called from `set_image` (unchanged)
plus new `resizeEvent`/`showEvent` overrides — this is the direct fix
for the reported first-freeze thumbnail: `set_image` runs before the
stack-layout swap gives the canvas real geometry, so the old code's
inline `fitInView` call computed against stale/absent geometry once and
never got a chance to recompute. `self._user_zoomed` (set in
`wheelEvent`, cleared in `set_image`) stops the new auto-refit from
fighting a manual zoom. `setBackgroundBrush(QColor("black"))` +
`setFrameShape(QFrame.NoFrame)` + both scrollbar policies off match the
live preview's own appearance. `QFrame` added to the existing guarded
PyQt5 import.

**Where the build diverged from intent**: render-check case 5's first
draft followed the plan literally — simulate a click via `mapFromScene`/
`mapToScene` at two zoom levels and expect identical round-tripped
points. It failed, correctly: `mapFromScene` rounds to an integer view
pixel, so a "click" at a small (mis-fitted) zoom is genuinely less
precise than the same nominal point at a larger zoom — real click
imprecision, not a transform-independence bug. The test was checking
click precision (which correctly varies with zoom), not the actual
phase-1 claim (that already-recorded scene coordinates measure
identically regardless of zoom at measurement time). Rewritten to test
that directly: `add_point_programmatic` stores the exact scene
coordinate it's handed, and `build_distance_mark` on those stored points
is identical across zoom levels, since neither reads the view
transform.

**Verification**: full `qt_shell.py --render-check` sweep passes (exit
0), including all five new "Live measure canvas-fit check PASS" lines.
Not yet exercised on-rig — carried forward as its own checklist (first
freeze fills the frame; swap reads as continuous; later freezes and
wheel-zoom-across-resize still behave; reopening the box re-fits
correctly).

### Intent: Live Measure frozen canvas must fit its frame on first freeze

Recording intent before building, per this repo's two-phase documentation
rule. Full plan in a user-provided `live_measure_canvas_fit_three_phases.md`
(not checked into the repo). Found during on-rig testing of the
freeze-on-first-click fix — the core behavior is confirmed working (first
click froze the frame and registered a real 14.885 µm distance from that
same click).

**Two residual, cosmetic-but-disruptive defects**: (1) the first freeze of
a session renders as a small thumbnail in a large empty area rather than
filling the frame — closing/reopening the Live measure box restores
normal appearance, and the second and every later freeze in the same
session are already correct, including after moving the stage; (2) the
frozen canvas shows Qt's default gray background plus a visible view
frame instead of matching the live feed's own black letterboxing, so the
swap reads as a different, patchier widget appearing rather than the same
frame freezing in place.

**Root cause of (1)**: `_LiveMeasureCanvas.set_image` calls
`resetTransform()` + `fitInView(...)`, but runs from
`_on_live_measure_freeze_done` *before* the stack layout swap makes this
canvas the current widget — at that moment it has no real laid-out
geometry, so `fitInView` computes against stale/absent geometry and lands
on a much-too-small transform. No `resizeEvent` exists to recompute once
real geometry arrives. Later freezes are fine because the canvas retains
real geometry from the first time it was ever shown.

**Not a bug — recorded so it doesn't get "fixed" later**: a 174.652 µm
reading taken on the mis-fitted view is an imprecise *click*, not a scale
error — clicks convert through `mapToScene`, so scene coordinates (and
therefore µm values) don't depend on view zoom; render-check case 5 below
locks this in directly. The ~1s click-to-freeze lag (the real full-res
capture) is expected and explicitly accepted by the user. The frozen
plane is greyscale because it's literally the green plane — inherent, not
a rendering defect; a future color freeze is a separate, parked change.

**Plan**: factor the fit into one `_fit_to_view` method, called from
`set_image` (unchanged) plus new `resizeEvent`/`showEvent` overrides so
real geometry arriving after `set_image` actually triggers a refit. A new
`self._user_zoomed` flag (set in `wheelEvent`, cleared in `set_image`)
stops the auto-refit from fighting a manual zoom. Match the live preview's
appearance: black background brush, no frame, scrollbars off. Update the
CALIBRATION INTEGRATION banner's removal list only if applicable (it
isn't — this is entirely inside Part 05's own `_LiveMeasureCanvas`).

**Non-goals**: do not reorder `set_image`/`setCurrentWidget` in
`_on_live_measure_freeze_done` — that ordering is the freeze fix's own
load-bearing invariant; fix the fit inside the canvas class instead. No
green-plane/color changes, no touching `measure.py`/`camera_backend.py`/
`calibrate.py`, no change to capture latency.

**Render-check coverage planned**: first-show fit (the direct regression
test); repeat freeze still fits; user zoom survives a resize; a new
`set_image` re-enables auto-fit; transform-independence of measurement
values (locks in the "not a bug" finding).

### Build: Onboarding gate must not block a non-interactive launch

Builds the intent recorded in the prior commit — landed exactly as
planned, no deviations. `qt_shell.py` only: `should_show_onboarding_gate`
gains a third parameter, `interactive` (default `True`, every pre-existing
call site unaffected). New `_onboarding_session_is_interactive(
no_onboarding_flag=False)` helper folds all three non-interactivity
signals into one place — `offscreen`/`minimal` `QT_QPA_PLATFORM` (name
compared alone, ignoring any `:`-separated backend option), the new
opt-out flag, or no live `QApplication` instance — and errs toward `True`
everywhere else, so an unrecognized platform or a real SSH-with-
forwarding session is never wrongly suppressed. New `--no-onboarding` CLI
flag, threaded through a new `no_onboarding` constructor parameter on
`FocusPreviewWindow`, read fresh on every gate check.

The load-bearing ordering detail: `save_pref(
"onboarding_calibration_prompt_shown", True)` still fires before the
dialog on the interactive path (crash-mid-dialog safety, unchanged), but
suppression writes nothing at all — this fell out for free from
`should_show_onboarding_gate`'s own early return, since `save_pref` was
already only ever downstream of that check; no separate guard needed to
get this right. CALIBRATION INTEGRATION banner's removal list updated to
include the new helper, flag, and constructor parameter.

Render-check coverage added: the predicate's full 8-combination truth
table; the interactivity helper's platform-name/opt-out/no-QApplication
branches (including the one point in `render_check()` where "no live
QApplication instance yet" is genuinely true, checked before this
function constructs its own); suppression leaving the real pref file
completely unwritten — the assertion that actually matters, since this
regressing is otherwise invisible (nothing fails loudly, a user just
quietly loses their one-time prompt); the interactive path still writing
the pref before the dialog (a monkeypatched `QMessageBox.question`, since
a real one would hang this exact check); and `--no-onboarding` suppressing
an otherwise-interactive session.

**Verification**: full `qt_shell.py --render-check` sweep passes with
exit 0 on a genuinely fresh environment — `~/.zynergy/gui_prefs.json` and
`calibration.json` both deleted before the run, no pre-seeding of any
kind (this used to hang forever). The resulting `gui_prefs.json`
(written to by other, unrelated render-check sections that don't
redirect `PREFS_PATH` themselves) has no
`onboarding_calibration_prompt_shown` key at all afterward, confirming
suppression held across the entire sweep's real window construction, not
just the isolated new test block. Not yet exercised as a live GUI on-rig
— this sandbox has no real display, so the interactive path is verified
only via the monkeypatched render-check case, not an actual human click.

### Intent: Onboarding gate must not block a non-interactive launch

Recording intent before building, per this repo's two-phase documentation
rule. Full plan in a user-provided `INTENT_onboarding_gate_headless.md`
(not checked into the repo). Follow-up to the environment gap flagged
(not fixed) during the Live Measure freeze fix, above: `_maybe_show_
onboarding_gate` fires a real blocking `QMessageBox.question` on a
genuinely fresh install, which is correct when a human is at the keyboard
but hangs the process forever when nothing can dismiss it (offscreen Qt,
CI, containers, no-display SSH) — this is the hang `py-spy dump` diagnosed
in that session.

**Scope correction from the original finding**: not "every fresh rig" —
on real hardware with a display, the dialog appears and a human clicks
it, which is intended behavior. Narrower than first described, but still
the actual blocker for clean-environment testing.

**Plan**: give `should_show_onboarding_gate` a third parameter,
`interactive` (default `True`, existing call sites and the CALIBRATION
INTEGRATION banner's removal instructions unaffected), true only when
not-shown AND no-calibration AND interactive — stays the existing pure,
Qt-free predicate. Add a small helper that detects a non-interactive
session (`offscreen`/`minimal` `QT_QPA_PLATFORM`, a new `--no-onboarding`
flag, or no live `QApplication`/display) — reads the *effective*
`QT_QPA_PLATFORM` rather than assuming the default, since the file
already lets an explicitly-set value win via its own
`os.environ.setdefault(..., "xcb")`. Add `--no-onboarding` to `main()`'s
argparse for a scripted launch with a real display that shouldn't be
interrupted. **The detail most likely to get lost if this is done
casually**: `save_pref("onboarding_calibration_prompt_shown", True)`
currently fires before the dialog, correctly, so a crash mid-dialog
doesn't re-prompt forever — that ordering stays for the interactive path,
but suppression for non-interactivity must NOT write the pref; "nobody's
here" is not "asked and answered," and writing it would silently burn the
user's real one-time prompt. Once built, remove the freeze-fix session's
pref pre-seeding workaround from render-check setup and mark that
HANDOFF.md note closed.

**Non-goals**: no weakening of the gate's one-time-ever semantics for
interactive users; `calibrate.py` stays untouched (the CALIBRATION
INTEGRATION banner's separability contract must still hold); no other
blocking-dialog site gets touched in this pass.

**Render-check coverage planned**: the predicate's full 8-combination
truth table; suppression leaves the pref file untouched and constructs no
dialog; the interactive path still writes the pref before the dialog
(ordering regression guard); `--no-onboarding` suppresses an otherwise-
interactive session; and the real proof — the whole sweep completing on a
fresh container with no `gui_prefs.json` and no pre-seeding step.

### Build: Live measure freeze-on-first-click fix

Builds the intent recorded in the prior commit — landed exactly as
planned, no deviations. `qt_shell.py` only:
`_on_live_measure_freeze_done` now guards `_calibrate is None` alongside
`_measure is None`; sets `_live_measure_frozen = True` only after the
pixmap/`set_image`/stack-swap block succeeds, restoring the live preview
and leaving the mode retryable on any failure there instead of bricking
it (the actual fix for the reported freeze-forever bug);
`_live_measure_preview_event` now requires an armed tool before starting
a freeze at all, prompting instead when none is armed; with that
guarantee, the freeze-triggering click is always registered as the
tool's first point; and `self._capturing` is now actually set while a
freeze capture is in flight and cleared on every exit path, matching what
`_live_measure_freeze`'s own docstring already claimed.

Render-check coverage added, five fresh camera/window fixtures per case
so none can mask another: a `_calibrate is None` freeze (fails clean, not
bricked); `set_image` raising (the direct regression test for the
reported bug); the happy path (freeze-triggering click's own converted
coordinate, via `native_point_from_preview_click`, lands as the frozen
canvas's first point); no tool armed (no capture at all, click still
consumed, status prompts for a tool); and the `_capturing` lifecycle on
the freeze-failure/load-failure/synchronous-raise paths not already
covered by the first three cases.

**Verification**: full `qt_shell.py --render-check` sweep passes,
including the six new PASS lines this fix adds. Found (not fixed, out of
scope for this plan) while getting that sweep to run at all in a genuinely
fresh environment: `_maybe_show_onboarding_gate`'s real, blocking
`QMessageBox.question` — an unrelated, pre-existing first-launch prompt —
fires the first time any `FocusPreviewWindow` is constructed and pumped
when no calibration is on record and the prompt has never been shown,
which is unconditionally true in a brand-new environment; headless/
offscreen has no way to click it, so the whole self-check hangs forever
(confirmed with `py-spy dump`, not guessed). Worked around here by
pre-seeding the real `onboarding_calibration_prompt_shown` pref before
running the check (environment setup, not a code change) — flagged in
`HANDOFF.md` as a real gap in `render_check()`'s own test isolation,
alongside the `PROFILE_PATH`/`CALIBRATION_PATH`/`ANNOTATION_PATH`
redirects it already does elsewhere, rather than silently patched as a
side effect of this fix. Not yet exercised as a live GUI on-rig — this
fix's own on-rig checks (tool selected → freeze + point 1 lands correctly;
no tool selected → prompt, no zoom, no capture; a simulated on-rig freeze
failure → feed stays live, next click works) remain outstanding.

### Intent: Live measure freeze-on-first-click fix

Recording intent before building, per this repo's two-phase documentation
rule. Full diagnosis and plan in a user-provided
`PLAN_live_measure_freeze_fix.md` (not checked into the repo). This is a
bug fix to Part 05's freeze-on-first-click design (see "Live measure
panel" in `HANDOFF.md`), not a new feature.

**Reported symptom**: clicking the live feed with the Live measure panel
open zooms in slightly, doesn't freeze, registers no measurement point,
and every click after that does nothing at all. The zoom is real and
correct (`Picamera2Camera.capture_still_async` switches to `full_res` for
the still, changing the FoV) — it confirms the click really does reach
`_live_measure_freeze` and a capture really does start. The bug is
entirely downstream, in the completion handler.

**Diagnosis**: `_on_live_measure_freeze_done` sets
`self._live_measure_frozen = True` *before* building the pixmap, calling
`set_image`, and swapping `_preview_stack_layout` to the frozen canvas —
any of which can raise. Its availability guard only checks
`_measure is None`, never `_calibrate is None`, even though `_calibrate`
is exactly as legitimately `None` as `_measure` (see the Calibrate
action's own disabled-when-`None` guard elsewhere in this file). If
`calibrate.py` failed to import, `array_to_qimage` raises `AttributeError`
on `None`; the enclosing `try` has only a `finally` (tmp-dir cleanup), so
the exception escapes the slot with `_live_measure_frozen` already `True`.
From then on, `_live_measure_preview_event`'s own
`if self._live_measure_frozen: return True` swallows every subsequent
click, permanently — the exact reported symptom set, including the
no-recovery part. Secondary defect, same handler: the freeze click's own
converted coordinates were discarded whenever no tool was armed at click
time (`pending_pt is not None and self._live_measure_tool is not None`),
silently dropping the click that triggered the freeze.

**Required behavior change** (explicit in the plan, not just a bug fix):
the first click on the live feed must do both things — freeze the frame
*and* register that same click as point 1 of the measurement. A user
clicking a spore edge expects that edge to be point 1, not a second click
on the frozen plane.

**Plan** (`qt_shell.py` only — `measure.py`, `camera_backend.py`,
`plane_cache.py`, `pixel_hash.py` untouched):
1. Guard `_calibrate is None` alongside the existing `_measure is None`
   check, same message style ("Live measure unavailable" /
   "calibrate.py not importable").
2. Restructure the success path so `_live_measure_frozen = True` is the
   *last* thing set — only after the pixmap/set_image/swap block
   succeeds. On failure: status set, frame stays live (swap back to
   `self.preview`), pending point discarded, mode left retryable, never
   bricked.
3. Require an armed tool before `_live_measure_preview_event` will start a
   freeze at all — a click with no tool prompts for one
   (`_live_measure_tool_hint(None)`) and is still consumed, but never
   triggers a capture. This is what makes step 4 unconditional.
4. With (3) guaranteeing a tool is always armed when a freeze starts,
   always register the freeze-triggering click as the tool's first point
   (`pending_pt is not None` alone, no longer gated on a tool also being
   set) — defensive-only fallback if the tool somehow clears mid-capture.
5. Unrelated but cheap, found in the same code: `_live_measure_freeze`'s
   own docstring claims it shares `self._capturing` with the normal
   capture path as a busy-guard, but the code only ever *read* that flag,
   never set it — the claimed mutual exclusion didn't actually hold.
   Set it when a freeze capture launches, clear it on every exit path
   (success, freeze failure, load failure, the new step-2 failure path,
   and a synchronous `capture_still_async` raise).

**Render-check coverage planned**: a `_calibrate is None` freeze (fails
clean, not bricked); `set_image` raising (the direct regression case for
the reported bug); the happy-path point registration (freeze + point 1 in
one click); no tool selected (no capture at all, click still consumed,
status prompts for a tool); the `_capturing` lifecycle across all of the
above plus a synchronous `capture_still_async` raise.

**Verification, planned**: self-check first, then on-rig — click with a
tool selected (frame freezes and point 1 lands where clicked); click with
no tool selected (status prompts, no zoom, no capture); a simulated freeze
failure on-rig (feed stays live, next click works). Render-check alone
cannot prove any of this; it is not done until exercised live.
### Build: `MeasureWindow` extraction, step 3 — Export and Publish menu actions

Builds the intent recorded in the prior commit.

**`gallery.known_green_hashes(out_root=None)`** (next to
`capture_has_annotation`) walks `list_gallery_entries`, decodes each
capture's real green plane via `measure.load_measurement_plane`, and
returns the set of hashes — a decode failure for one entry is skipped, not
fatal to the scan, same defensive contract as `capture_has_annotation`.
`gallery.py --render-check` gained matching coverage: two real capture
sessions, hashes cross-checked against independently computed values, plus
an `out_root=None` defaults-to-`provenance.OUT_ROOT` check.

**`annotations.stored_calibration_ref(pixel_sha256, store=None)`** (next to
`calibration_ref_for`) returns a record's own first-commit
`calibration_ref`, `None` if no record exists. `annotations.py
--render-check` gained the explicit, testable proof this function's
premise rests on: save a mark under one calibration entry, confirm
`stored_calibration_ref` matches `calibration_ref_for` at that point,
recalibrate the same objective, confirm `calibration_ref_for` now reflects
the new entry while `stored_calibration_ref` stays pinned to the original.

**`qt_shell.py`** gained two new File-menu actions, `Export measurement
results...` and `Publish package...`, right after `Extract green
plane...`, following the existing `_open_X`/`_run_X_cmd`/`_on_X_finished`
triad (`_open_green_extraction`/`_run_green_extract_cmd`/
`_on_green_extract_finished`) and two new `pyqtSignal(object)`s for the
worker-thread hand-off. Both report through `_set_capture_status`,
matching "Extract green plane..." — not `measure.py`'s `QMessageBox`
convention, since these are fire-and-forget background jobs from a menu,
not a synchronous confirmation inside an open canvas.

Export writes the results file first, then scans for orphan evidence
second — the write is the deliverable, the scan is comparatively expensive
and is evidence, never a gate, so a slow or failing scan degrades to
"missing evidence in the report," never to a delayed or blocked write.
`capture_scan_ok`/`cache_scan_ok` are tracked independently; `find_orphans`
only runs when BOTH scans actually completed — a partial `known_hashes`
set produces false-positive orphans exactly as confidently as an empty
one, so partial coverage is treated the same as no coverage
(`{"unavailable": "..."}`) rather than surfaced with a caveat, never a
silent `{"orphans": []}` that could be mistaken for a clean scan (same
absent-vs-empty split Part 02 drew for `get_capabilities()`).

Publish picks its own image via `GalleryPickDialog` (mirroring
`_open_green_extraction`'s input step), then builds `calibration_ref` via
`stored_calibration_ref` — Option B+, no objective picker, no UI.
`measure.py`'s `MeasureWindow._on_publish_package` converges onto the
exact same call (`_pixel_hash.pixel_sha256(self._plane)` +
`_annotations.stored_calibration_ref`), replacing its old
currently-active-calibration lookup — one way this gets built across the
whole codebase, not two.

`qt_shell.py --render-check` gained a full pass for both actions, driving
the worker methods directly (bypassing `GalleryPickDialog.exec_`, which
can't run headless, same reason the existing green-extraction check does
this): a cache-only plane with a real committed mark proves NOT an orphan
(the direct regression test for the union-of-hashes finding), a genuinely
orphaned record proves reported, `_gallery`/`_plane_cache` temporarily
unavailable proves the write still lands while orphan evidence reports
`"unavailable"` rather than an empty or partial list, a forced
`export.export_measurements` failure proves reported rather than
swallowed, and Publish's manifest is checked against the record's own
stored ref end to end, plus a forced-failure (bad input path) case.

Found and fixed while building, before it ever shipped:
`_run_publish_package_cmd` wrote `green_plane.tif` straight into `out_dir`
without creating it first — harmless when the input actually comes from
`QFileDialog.getExistingDirectory` (which only ever returns an existing
directory), but a real gap for any other caller (this step's own
render-check included) that hands it a directory that doesn't exist yet.
Fixed with a `mkdir(parents=True, exist_ok=True)` before the write,
matching `publish.publish_measurements`'s own defensive `out_dir.mkdir`.

Full `--render-check` sweep passes (`gallery`, `annotations`, `pixel_hash`,
`export`, `publish`, `calibrate`, `measure`, `ca_measure`, `wizard_pages`,
`qt_shell`, `stacks`, `focus`, `plane_cache`, `provenance`, and
`process_wizard` all green), no regressions. **Self-check-verified only**
— nothing in this step has been exercised on real hardware or as a live
GUI on-rig.

### Intent: `MeasureWindow` extraction, step 3 — Export and Publish menu actions

Recording intent before building, per the project's two-phase documentation
rule. Full design is the user-approved step-3 plan (not checked into the
repo, same standing as `PLAN_measurewindow_extraction.md` itself),
continuing step 2's extract-then-remove migration.

Relocates `MeasureWindow._on_export_results`/`_on_publish_package`
(`measure.py`) into their own `qt_shell.py` File-menu actions: Export needs
no open image at all (store-wide); Publish needs an image but has no open
`MeasureWindow`/`self._plane` to source one from once triggered from a
menu, so it picks its own via `gallery.GalleryPickDialog`. `MeasureWindow`
is not deleted this step (shell removal is a later step).

Investigation surfaced two things that reshape this step beyond a straight
relocation:

**Orphan evidence is a real dependency, not an afterthought.**
`annotations.find_orphans` exists and is tested but has zero production
callers anywhere in the repo — a store-wide Export is its first real use.
The `known_hashes` set it needs has no existing source: a new
`gallery.known_green_hashes(out_root=None)` is required. It must also
union with `plane_cache.list_cached_hashes()`, caught before
implementation — Part 05's Live Measure Panel freezes a live plane and
commits marks against its hash, and that plane lives only in the
green-plane cache (Part 04), never as a capture session with a JPG
preview. A `known_hashes` set built from a capture-root scan alone would
report every mark ever committed through Live Measuring — the primary
calibrated measuring workflow — as a permanent orphan on every export.
Unioned at the `qt_shell.py` call site (which already imports both
`gallery` and `plane_cache`), not inside `gallery.py`, which has no
existing dependency on `plane_cache.py` and shouldn't gain one just for
this.

**Publish's `calibration_ref` has a pre-existing correctness gap.**
`MeasureWindow._on_publish_package` derives it from whatever calibration
is *currently* active for the selected objective, not from
`record["calibration_ref"]` (set once, at a record's first commit, never
touched again) — if the same objective is recalibrated after marks were
made, publishing later reports a manifest claiming those marks were
computed under a calibration they never used. **Decision, made by the
user this session: fix this (Option B+), not replicate it.** Distinct from
step 2's strict-gate call (over-restrictive, but never wrote anything
false) — this writes a manifest asserting a calibration relationship that
isn't true, in the one place a number leaves the system and becomes
something someone cites. `PHILOSOPHY.md`'s own framing — recalibration
must never let historical measurements "quietly re-interpret themselves
against a number they were never computed with" — applies directly. A new
`annotations.stored_calibration_ref(pixel_sha256, store=None)` will
return the record's own first-commit ref instead. Checked, not assumed:
no mark builder stores a per-mark calibration pointer (only a baked-in
`um_per_px`), so this is accurate for the common case (all of an image's
marks made in one calibration epoch) but not authoritative across a
mid-record recalibration — a genuine, pre-existing schema gap this step
will document, not close (closing it means touching
`commit_measurement()`'s mark schema, out of scope here). An unset stored
ref already degrades correctly today (`publish.py`'s own "no calibration
on record" note) — nothing to fix there.

See `HANDOFF.md`'s `MeasureWindow` extraction section for the full step-3
account.

### Intent: Store-mechanics migration (`BUILD_LIST` phase 2 — `calibrate.py` / `ca_measure.py` / `annotations.py`)

Recording intent before building, per the project's two-phase documentation
rule. This is `BUILD_LIST`'s own phase 2 (store-mechanics migration for
`calibrate.py`/`annotations.py`/`ca_measure.py`), named and deliberately
deferred when `provenance.py`'s phase 1 landed — see this file's own
"`provenance.py` extraction, phase 1" entry and its "not in this pass" note.

**Read all three modules' real store code before drafting**, rather than
speccing the split from the phase-1 description alone — worth doing given
what it found. `calibrate.py`'s calibration store (`load_calibrations`/
`current_calibration`/`save_calibration`) and `ca_measure.py`'s CA store
(`load_ca_calibrations`/`current_ca_calibration`/`save_ca_calibration`) are
the same code twice over: an objective-keyed dict of chronological entry
lists, `entry_id`/`supersedes` chaining, mkdir-then-atomic-write.
`annotations.py`'s store is a genuinely different shape: keyed by
`pixel_sha256`, one record per hash holding an ever-growing `marks` list,
no `entry_id`/`supersedes` concept at all — marks accumulate, they never
supersede each other. It shares only the outer atomic-write mechanic with
the other two. `provenance.py`'s own `save_profile`/`load_profile` (see
the 2026-07-21 "`save_profile()` to write atomically" fix) is a fourth
instance of that same outer mechanic.

**Decision: two primitives, in a new leaf module**, not folded into
`provenance.py` despite `provenance.py` already holding one instance of the
pattern. The test applied is the same one that justified moving
`FULL_MODE_LBL`/`DENOISE`/`SHARPNESS` into `provenance.py` during phase 1:
single-consumer code tied to `Session.write` belongs in the governor
module; a primitive with four consumers (`provenance.py`, `calibrate.py`,
`ca_measure.py`, `annotations.py`) that have nothing to do with camera
sessions is a utility library, not governor content. New module:
`json_store.py` — matches the project's existing small-leaf-module naming
(`pixel_hash.py`, `ca_lib.py`, `debayer.py`, `focus.py`).

Two primitives:
- `atomic_write_json(path, data)` / `load_json_or_default(path, default)`
  — the generic mechanic, all four consumers.
- `append_to_history(store, key, entry)` / `current_entry(store, key)` —
  the objective-keyed supersedes-chain mechanic, pure (no I/O),
  `calibrate.py` and `ca_measure.py` only. `annotations.py` never adopts
  this half; its append model has no supersedes chain to give it.

**Path-agnostic by design, non-negotiable.** The leaf module takes every
path as a parameter and never imports, holds, or defaults a path constant
of its own. Same by-attribute discipline `provenance.py`'s own
`OUT_ROOT`/`PROFILE_PATH` comment already documents: `qt_shell.py`'s
`render_check()` reassigns `provenance.PROFILE_PATH` at runtime for test
isolation, and that only keeps working today because `save_profile()`
reads the name from module globals at call time, not a captured default. A
path constant living in the leaf module would reintroduce exactly the
second-binding failure mode the attribute rule exists to prevent.

**`mkdir` folds into `atomic_write_json`.** All four current call sites
repeat `PATH.parent.mkdir(parents=True, exist_ok=True)` immediately before
their own write — a fourth duplicated line, and leaving it outside the
primitive means a future caller can forget it, the same class of gap as
the `green_plane.tif` mkdir bug from the Export/Publish step.

**`entry_id` generation moves into the leaf.** `append_to_history` assigns
`entry_id`/`supersedes` itself; `uuid` leaves `calibrate.py`'s and
`ca_measure.py`'s own call sites. Flagged explicitly since this stops
being visible at the call site: whoever reads `save_calibration`/
`save_ca_calibration` after this migration needs the primitive's own
docstring, not the call site, to learn that saving assigns the id.

**Import style: hard, not guarded.** `json_store.py` is stdlib-only (no
PyQt5, no numpy) and safe to hard-import everywhere via the same
try-relative-then-bare pattern `ca_measure.py`'s own `ca_lib` import
already uses (package-vs-script resolution only, not an optionality guard
like the existing `debayer`/`focus`/`wizard_pages` imports). This
preserves `calibrate.py`'s "runs standalone" property — a tiny leaf util
isn't the capture-session machinery its own module docstring means by
that claim.

**`json_store.py` gets its own `--render-check`, wired into the sweep.**
`append_to_history`/`current_entry` are pure and fully testable with no
disk I/O; `atomic_write_json`/`load_json_or_default` get a real temp-path
round trip (missing file → default, corrupt file → default, no leftover
`.tmp` after a write). Noted explicitly in the module docstring:
concurrent-writer behavior is still not covered by anything, per
`save_profile()`'s own docstring caveat about the two-process race that
once corrupted the real profile.json — the primitive's core justification
(crash-safety) remains unproven by the suite; only the single-writer path
is verified.

**Migration order**, decided deliberately rather than left implicit:
0. `json_store.py` itself, plus `provenance.py`'s `save_profile`/
   `load_profile` re-pointed at it (zero behavior change — `provenance.py
   --render-check` must still pass unmodified). Done as its own
   trivially-verifiable step before any of the three named modules move,
   so the primitive's design isn't entangled with the first real
   migration.
1. `calibrate.py` — most upstream; both `annotations.py` and
   `ca_measure.py` already import from it.
2. `ca_measure.py` — identical store shape to `calibrate.py`, low risk.
3. `annotations.py` last — only ever adopts the atomic-write half, not
   the supersedes-chain half, a genuinely smaller change.

Each module's own existing `--render-check` block keeps testing through
its real call path (`save_calibration`, `save_ca_calibration`,
`save_mark`) — this migration changes what's behind those functions, not
the tests that exercise them, so no test relocation is needed at any of
these three steps.

**Not in this pass**: `plane_cache.py`, `measure.py`'s `session.json`
write, and `qt_shell.py`'s `PREFS_PATH` write all use the identical atomic
temp-file/`os.replace` pattern too, but none are named in `BUILD_LIST`'s
phase 2 scope — left untouched, flagged here rather than silently ignored.
Also not in this pass: `measure.py`'s `DEFAULT_CAPTURES_ROOT` (hand-
duplicates `provenance.OUT_ROOT`, flagged as its own open follow-up during
`MeasureWindow` extraction step 2) — explicitly parked, to keep this
series' risk scoped to the store-mechanics consolidation alone. The save
paths this series touches are exactly where the two real data-loss
incidents in this project's history happened (the `PROFILE_PATH`
overwrite, the `annotations.json` pollution), so it carries no unrelated
changes.

See `HANDOFF.md`'s new "Store-mechanics migration" section for the full
account.

### Fix: real-store pollution in `measure.py`'s pre-existing status-line check

Follow-up to the "found, not fixed" note in the Build entry below, pushed
on because of what it would have meant for step 3 (Export): that step
reads the *entire* annotation store with no dependency on any open
image, so building it before this was settled meant the first real export
would have included whatever the polluted entries left behind.

Investigated properly rather than assuming: the status-line check's
fixture plane is built via `np.arange(...) % 4096` — deterministic, no
randomness or timestamp — so every affected run hashes to the exact same
`pixel_sha256`
(`45a24e947a87c7817690b7181efb3eea8a3e8279ed8c0a65a1a8752c0bfd9a67`,
confirmed by direct computation, cross-checked against a real polluted
store holding exactly that one key). That hash was never produced by any
real capture — an orphan in `annotations.py`'s own sense (see
`find_orphans`), not scattered, not ambiguous. Also traced every
`CALIBRATION_PATH`/`save_calibration` call site in `measure.py`: the
calibration-gating block's own redirect already spans this entire section,
so `~/.zynergy/calibration.json` was never exposed by this — confirmed
against a real machine, where that file doesn't even exist.

**Decision, not a cleanup**: the entries this already wrote to any real
`~/.zynergy/annotations.json` are left untouched. `PHILOSOPHY.md`'s strict
rules are explicit — "Never edit or delete an existing entry. Never 'clean
up' a store." — and name that exact operation. Sympathetic as the case is
(synthetic, deterministic, never a real measurement), the rule doesn't
carve out an exception for it, and the doc's own framing is that an
apparent need to break a strict rule is a design problem to raise, not
work around. Raised here: step 3's Export design should surface orphaned
entries via the existing `find_orphans(store, known_hashes)` — evidence,
not a silent drop and not a silent include, the same pattern every other
detector in this project already follows — rather than inventing new
filtering logic or, worse, pruning the store as part of building it.

**The forward fix**: the status-line check now redirects
`ANNOTATION_PATH` to its own isolated temp path for its duration, same
pattern as the checks added in the prior commit. Verified directly, not
just by re-running the suite — snapshotted a real store's polluted-key
mark count before and after a fresh `--render-check` run: unchanged,
confirming the write actually stopped rather than the fix merely looking
right. Full 16-module sweep still passes, no regressions.

### Build: `MeasureWindow` extraction, step 2 — recall/review (editable) + commit-orchestration extraction

Builds the intent recorded in the prior commit.

**`commit_measurement(plane, pixel_sha256, objective, tool, points)`**
(`measure.py`, pure-logic section, before the `_HAVE_QT` guard) is the
extracted orchestration: resolves calibration, builds the mark for `tool`,
saves it, returns `{"mark": mark, "record": record}` — `record` is
`annotations.save_mark`'s own return value (`store[pixel_sha256]`), not a
second `image_record_for` re-read, which is what `MeasureWindow.commit_mark`
did before this change (a small, justified cleanup — every existing caller
of `save_mark` in the repo already discarded that return value). Raises the
new `CalibrationMissing(ValueError)` for the strict gate, or `ValueError`
unchanged from `build_*_mark`/`fit_ellipse` for degenerate input — neither
caught internally, same shape as `calibrate.py`'s `build_calibration_entry`.
`MeasureWindow.commit_mark` is now a thin thirteen-line wrapper: pull plain
values from its own widgets, call in, catch the two exception types, do
GUI-only follow-up (draw, labels). `MeasureView` needed zero changes.

**`ReviewWindow`** (`measure.py`, new class beside `MeasureWindow`) is the
new recall/review capability, editable per this session's approval:
open an image, see its existing marks, place new ones with the same four
tools. Deliberately smaller than `MeasureWindow` — no filmstrip/z-stack/
export/publish/wizard-restart, out of scope for this step or being deleted
outright. Reuses `MeasureView` completely unmodified by presenting the same
duck-typed contract (`active_tool`, `commit_mark`, `on_point_added`,
`_reset_tool_hint`) `MeasureView` already expects from its `window_`.
Launches via `gallery.GalleryPickDialog`, byte-for-byte the same pattern
`MeasureWindow._on_open` already uses — nothing in `gallery.py` changed.
Reachable this step via an additive `measure.py --review` CLI flag; a
permanent menu entry point is a later step's decision.

**Commit round trip, verified end to end for the first time**: a mark
committed through Part 05's Live Measure Panel — via its real click
dispatch and real `_live_measure_commit_entry`, not a hand-simulated
equivalent — now resolves by `pixel_sha256` in `ReviewWindow`, exact mark
match. This was PLAN_measurewindow_extraction.md's single most important
unverified claim; the new assertion lives inside `qt_shell.py`'s existing
"Live measure panel check" (reusing its already-frozen plane, hash, and
committed mark, not a parallel fixture), since `qt_shell.py` already
depends on `measure.py` and the reverse import direction is one this
codebase deliberately avoids (see `measure.py`'s own comment on why it
doesn't import `qt_shell.py`). `measure.py`'s own `--render-check` gained
matching coverage: `commit_measurement()` exercised directly for all four
tools (including proving the strict gate blocks angle, not just the other
three), and `ReviewWindow`'s own load/commit/recall cycle against a
temp-redirected annotations store.

**Found, not fixed — flagging per this project's honesty convention**:
while adding `commit_measurement()`'s own isolated-store checks, noticed
that `measure.py`'s *pre-existing* "mark-commit status-line reset check"
(`BUILD_LIST Tier 1 item 2`'s coverage, already in the repo before this
session) never redirects `annotations.ANNOTATION_PATH` — every
`measure.py --render-check` run has been committing real distance/polygon
marks to the actual `~/.zynergy/annotations.json` all along, the same
shape of real-data-clobbered-by-a-self-check risk `HANDOFF.md` already
documents for `PROFILE_PATH`. Out of scope for this step (unrelated
pre-existing test, not touched by this extraction), so left as-is rather
than fixed opportunistically — but worth a dedicated fix before it causes
the same kind of confusion the `PROFILE_PATH` incident did. **Investigated
and fixed in the next commit** — see the `Fix:` entry above for the
identifiability findings, the append-only decision on the entries this had
already written, and the redirect itself.

Full `--render-check` sweep passes across all 16 modules, no regressions.
**Self-check-verified only** — nothing in this step has been exercised on
real hardware or as a live GUI on-rig; same standing caveat as everything
else in `HANDOFF.md` that hasn't been separately hardware-verified.

### Intent: `MeasureWindow` extraction, step 2 — recall/review (editable) + commit-orchestration extraction

Recording intent before building, per the project's two-phase
documentation rule. Full design in `PLAN_measurewindow_extraction.md`
(user-drafted, not checked into the repo) plus this session's own step-1
investigation and step-2 design pass.

Step 1 (investigation) confirmed the extraction plan's five-way capability
split against the real code, and corrected one claim in it: Part 05's Live
Measure Panel does not actually call `MeasureWindow.commit_mark` as the
plan asserted — it independently reimplements the same sequence against
the same underlying primitives. Two copies of the commit orchestration
exist today, not one shared path with one dependent. Also found
wizard-restart dead in the in-app Measure-menu path (`qt_shell.py`'s
`_launch_measure` never connects `MeasureWindow.restart_requested`), and
answered the plan's open Export/Publish question: they're already two
independent, self-contained dialogs, no shared-vs-split decision to make.

Step 2 was originally scoped read-only; approved as **editable** this
session, which is what makes the commit-orchestration extraction necessary
now rather than later — an editable viewer needs to commit marks, and
writing that fresh would have made a third copy.

**Decision**: extract the orchestration into `commit_measurement()`, a new
Qt-free, module-level function in `measure.py`, called by both
`MeasureWindow` (rewritten) and a new `ReviewWindow`. Its calibration gate
stays **strict** — matching `MeasureWindow`'s current behavior exactly,
including blocking angle marks without calibration even though angle is
scale-invariant and doesn't need `um_per_px` at all. Part 05's panel
exempts angle from this gate in its own separate copy; adopting that here
would be a silent behavior change riding on a refactor, so it's deferred.
Flagged explicitly for whoever later migrates Part 05 to call this
function: that migration will need to *decide* whether to carry the
exemption forward, not discover after the fact that it silently vanished.

Part 05's panel is explicitly **not** touched or migrated in this step —
it ships, works, and stays exactly as-is; migrating it to
`commit_measurement()` is separate future work.

See `HANDOFF.md`'s new `MeasureWindow` extraction section for the full
account.

## 2026-07-25

### Fix: Preferences dialog crash on `get_capabilities()`

Bug fix to landed code (Part 02's capability query), not a new plan set —
full design in a user-provided `PLAN_fix_capabilities_cache.md` (not
checked into the repo). One function changes shape, in each backend class.

**Root cause**: opening Options > Preferences while the camera is running
crashed with `RuntimeError: Camera must be stopped before configuring`.
`Picamera2.sensor_modes` is not a passive lookup — reading it internally
calls `configure()`, which Picamera2 refuses while the camera is running.
By the time a user opens Preferences, the main window's preview has
already started the camera, so a live `get_capabilities()` call in that
ordering always fails. This slipped past Part 02's own hardware
verification because that verification exercised `get_capabilities()`
standalone, against a `Picamera2()` not mid-preview — the translation
logic itself was genuinely confirmed correct; calling it during the
camera's actual normal running state (the only state the real UI ever
calls it in) was not.

**The fix**: `sensor_modes` describes fixed hardware capability — it
cannot change between camera construction and any later point in the same
process, so there is nothing to gain by re-querying live and real risk in
doing so (this crash). `Picamera2Camera.__init__` now queries
`get_capabilities()` once, itself, while construction is still in
progress and before `start()` can possibly have been called by the GUI,
and caches the result on `self._capabilities`. `get_capabilities()`
itself now returns the cached dict on every later call, never touching
`_picam2` again — same external contract (signature, return shape)
throughout, so `PreferencesDialog` needed zero changes (confirmed, per
the plan's own instruction that a needed change there would mean the
cached shape had drifted from the live one — it hadn't).
`FakeCamera.get_capabilities()` gets the identical caching shape (eager
`__init__` priming, cache-or-compute on call) for consistency, even
though its synthetic result never changes anyway — kept both classes'
behavior symmetric per the plan.

**Verification**: `camera_backend.py`'s self-check gained a cache-identity
assertion — a second `get_capabilities()` call returns the exact same
cached object (`is`, not just an equal value) for both `__init__`'s own
eager priming and a forced cold first computation, proving the cached
branch actually runs rather than merely producing a value that happens to
look right twice. `Picamera2Camera` itself cannot be constructed off-rig
at all (no hardware, and its `__init__` also builds a real GL preview
widget), so confirming its `sensor_modes` is genuinely read only once, and
that the original crash is actually gone, needed the rig — now done: the
original crash was reproduced first (Preferences opened while the preview
was running, on the real Pi 5 + IMX477, photographed), then the fix
confirmed to remove it, same sequence, no crash, same real capture
resolutions/formats Part 02's own standalone verification already found.
Full `--render-check` sweep across all 16 `--render-check`-bearing modules
plus `camera_backend.py`'s own self-check: all pass.

### Build: Live Measuring (quick ruler)

A new, separate feature — full design in a user-provided
`PLAN_quick_ruler.md` (not checked into the repo) — not to be confused with
Measure/Part 05's own "Live measure panel" above, though it deliberately
borrows that feature's interaction shape. Live Measuring is a pixel-only
overlay on the LIVE, moving feed: no freeze, no calibration, nothing ever
committed to a store. A floating panel (Options > Measure > "Live
Measuring...", alongside the two existing actions) offers the same four
shape tools (distance/angle/polygon/ellipse), but every result reads in
plain pixels (or degrees) — `"143.2 px"`, never a calibrated µm figure —
so a screenshot of it can never be mistaken for a measurement. Marks draw
straight into the existing focus-box overlay buffer (no separate canvas
widget); closing the panel discards everything, since nothing here was
ever durable in the first place.

**Module-boundary rule, enforced structurally**: Live Measuring must never
import or call into `calibrate.py`/`annotations.py`/`provenance.py`, or
reuse Part 05's own preview-to-sensor conversion
(`native_point_from_preview_click`) — `assert_live_measuring_has_no_
calibration_dependency()` scans every Live Measuring function/method's own
source for those names, the same way `assert_only_camera_backend_imports_
picamera2()` polices the camera-import boundary.

**Picked up this session from a build that had dropped mid-flight** (same
pattern as Part 05's own session before it): the feature's code — the
pixel-math helpers, `LiveMeasuringPanel`, the `_live_measuring_*` state
machine on `FocusPreviewWindow`, the overlay-drawing and signature-folding
changes, the `eventFilter`/`keyPressEvent` wiring — was already written and
looked complete by inspection. It was not. **Two real bugs found and
fixed, both in the structural self-check itself, neither previously run
even once:**
1. `assert_live_measuring_has_no_calibration_dependency()` was defined but
   never called from anywhere — not from `render_check()`, not from
   anywhere else. An assertion nobody runs is not a guard, it only reads
   like one. It is now called at the top of `render_check()`'s own new
   Live Measuring section.
2. Once actually run, it immediately failed — on itself.
   `lores_point_from_preview_click`'s own docstring *explains*, by name,
   why it deliberately does NOT reuse `native_point_from_preview_click`
   (Part 05's function) — and the self-check's naive `word in
   inspect.getsource(...)` scan can't distinguish a docstring mentioning a
   forbidden name from code actually calling it. Fixed with a new
   `_source_without_docs_and_comments()` helper (tokenizes the source and
   drops every `COMMENT`/`STRING` token before scanning) — a real
   reference always survives this strip, since it's an attribute access or
   a call, never a string literal, so this only removes the false
   positive, not real coverage.

No render_check coverage existed for this feature at all before this
session; none of the above would have surfaced without writing it. The new
"Live Measuring check" block (`qt_shell.py --render-check`) proves: the
module-boundary self-check now genuinely runs clean; the panel opens/reuses
like every other launcher in this file; opening either Live Measuring or
Measure's own live panel (Part 05) closes the other first, in both
directions (both repurpose `self.preview`'s clicks); a real click routed
through the real `eventFilter` converts to the correct LORES_RES-space
point via `frac_from_point` (computed independently in the test, not a
hand-typed literal) and suppresses ordinary box-drag; distance (2 points)
and angle (3 points) auto-finish at their own count while polygon needs an
explicit double-click at or past its own minimum (and a double-click
*before* the minimum is a no-op, not a short shape); Escape cancels an
in-progress shape without touching an already-finished one; the overlay
push actually reaches `camera.set_overlay` with the focus aid off (proving
`_live_measuring_notify_changed`'s direct-push path is really wired, not
just present); the hit test misses empty space and finds a real mark by
its own segment geometry; `_live_measuring_delete_point`/`_delete_all`
(pulled out of the context-menu handler specifically so this could be
tested without driving the actual, blocking `QMenu.exec_` — the same
reason Part 05's own commit/delete are already separate methods) really
mutate the mark list; closing discards every mark and pending point. Full
`--render-check` sweep, all 16 modules: no regressions.

Three bugs in a row now that a passing self-check proved a mechanism works
in isolation while the actual integration point stayed broken (this one;
Part 05's orphaned `eventFilter` wiring; Part 02's `get_capabilities()`
crash) — written up as a durable rule in a new `PHILOSOPHY.md`, rather than
left as three separate one-off HANDOFF.md notes. Rig verification for this
feature is explicitly still open, and specifically needs to confirm marks
stay pinned to their own screen position and visibly do NOT track the
specimen once the stage moves — `FakeCamera` cannot produce a moving feed
to exercise that claim, and it's the one this whole feature's design rests
on.

### Build: Live measure panel (Preferences-dialog plan set, Part 05)

Builds the intent recorded below. Full `--render-check` sweep passes
across all 16 modules. This closes out the Preferences-dialog plan set
(Parts 01-05), all built. See `HANDOFF.md`'s own Part 05 section for the
complete account — this entry summarizes.

**Landed as designed:** `LiveMeasurePanel` (floating `Qt.Tool`, shape
picker + status line) and `_LiveMeasureCanvas` (a `QGraphicsView`, kept
separate from `measure.py`'s `MeasureView` per the plan) on
`FocusPreviewWindow`, opened from a new "Live measure..." action on the
existing "Measure" menu. First left-click on the live feed freezes it via
a real `capture_still_async` into a throwaway temp dir (no provenance
record); `measure.load_measurement_plane` + `pixel_hash.pixel_sha256` +
`plane_cache.store_plane` cache the green plane, keyed by its own hash,
before the temp dir is discarded. The click's own position converts
through the existing `frac_from_point`/`displayed_rect` preview-to-sensor
mapping (`native_point_from_preview_click`, new) and becomes the armed
tool's first point. Marks build via `annotations.py`'s existing
`build_*_mark` calls but stay in memory (three-state pen: in-progress,
uncommitted-orange, committed-cyan matching `measure.py`'s own `MARK_PEN`)
until an explicit right-click Commit; Delete only ever acts on an
uncommitted mark. Closing discards every uncommitted entry and restores
the live preview; a plane already written to `plane_cache` is left for
`clean_cache` to reclaim later, never deleted here directly. `FakeCamera`
gained the additive `capture_shape` kwarg the intent entry below
describes.

**Real bug found resuming a session that dropped mid-build:** the bulk of
this part's code — including a fully written `_live_measure_preview_event`
— had already landed uncommitted before the drop, but it was never wired
into `eventFilter`. Every click on the live feed while the panel was open
would have kept falling through to ordinary box-drag (`_press`/`_move`)
instead of triggering a freeze; the freeze path was entirely dead code
until this session added the two-line routing check at the top of
`eventFilter`. No render_check coverage existed yet for this part either
— the build had stopped before verification, which is exactly the gap
that coverage exists to catch. Both are fixed together in this entry: the
wiring, and the new render_check block (`qt_shell.py`) proving the click's
own coordinates convert to the plane's real native pixel coordinates,
freezing happens exactly once per panel session, commit writes to a real
temp-redirected `annotations.json` keyed to the frozen plane's actual
hash, Point hit-test misses/hits correctly, Delete never touches a
committed mark, and closing discards the right things and nothing more.

Hardware verification (a real first-click freeze feeling instant on the
rig) is explicitly NOT claimed — self-check-only this session, per the
same honest split every other part of this plan set has used.

### Intent: Live measure panel (Preferences-dialog plan set, Part 05)

Recording intent before building, per the project's two-phase
documentation rule. Full design in `PLAN_00_context_and_supersession.md`
and `PLAN_05_live_measure_panel.md` (drafted, not checked into the repo).
Depends on Part 04 (built) for the cache a committed mark points at. The
only part of this plan set that adds a genuinely new user-facing
capability — everything before it was relocation, configuration, or
housekeeping.

Plan: a small floating panel (shape picker: distance/angle/polygon/
ellipse, plus status), opened from a new action in the existing "Measure"
menu — the plan's own prose says "Options > Measure," written before this
app grew its own top-level "Measure" menu; the real, consistent placement
is alongside the existing "Measure..." action there, not a literal
Options submenu. The first left-click on the live feed freezes it: a real
`camera.capture_still_async` into a throwaway temp dir (no session, no
provenance record at all — this path never touches `provenance.py`),
`measure.load_measurement_plane` (reused, unmodified) extracts the green
plane, `pixel_hash.pixel_sha256` + `plane_cache.store_plane` cache it, and
the temp dir is deleted immediately after — only the cached plane
persists. The click's own position (converted through the same
preview-to-sensor fraction mapping the focus box already uses) becomes
the first point of whatever shape tool is armed, not a discarded trigger
click. All measurement after that happens on the frozen plane via a new
`_LiveMeasureCanvas`, not `measure.py`'s own `MeasureView` — kept separate
(measure.py stays untouched, per the plan) because this canvas needs two
things `MeasureView` doesn't: per-mark item tracking for the right-click
Delete/Commit menu, and a distinct visual state for uncommitted marks. A
finished shape builds a mark via `annotations.py`'s existing
`build_*_mark`/`measure.fit_ellipse` — identical math to `measure.py`'s
own commit path — but holds it in memory rather than writing it, until
the user explicitly commits.

Objective/calibration reuses the main window's existing
`ruler_objective_combo` (no separate picker in the small panel),
snapshotted per-mark at the moment its points finish, not re-read at
commit time.

The one code change outside `qt_shell.py`'s/`plane_cache.py`'s own
territory: `FakeCamera` gains an additive `capture_shape` constructor
kwarg (default unchanged) so `--render-check` can drive the real
`capture_still_async` → `load_measurement_plane` path headlessly, instead
of stubbing extraction out.

## 2026-07-24

### Build: Green-plane cache (Preferences-dialog plan set, Part 04)

Builds the intent recorded below. Full `--render-check` sweep passes
across all 16 modules (`plane_cache.py`, new this entry, brings the
15-module Part 03 baseline back up by one). See `HANDOFF.md`'s own Part 04
section for the complete account — this entry summarizes.

**Landed as designed:** `plane_cache.py` — `store_plane`/`load_cached_plane`
keyed by `pixel_sha256` alone (no index, no mapping table), atomic writes,
idempotent stores; `clean_cache(referenced=None, older_than_days=None)`
defaulting `referenced` to a fresh `annotations.json` read (so a plane
that gains a mark since the last clean is automatically ineligible) and
reporting `{removed, retained_referenced, retained_too_new}` so a caller
can say why a plane survived, not just that it did. Lives at
`<provenance_folder>/plane_cache/`, read live off
`provenance.PROVENANCE_ROOT` the same attribute-access way `OUT_ROOT`/
`PROFILE_PATH` already are. Preferences > Advanced's "Clean cache now"
button and "Automatically clean after N days" checkbox (both built as
stubs in Part 01) are now real: the button calls `clean_cache` and reports
real counts; auto-clean runs once at `main()` startup, alongside the
other Part 03 folder-layout prefs.

**Checked early, per the plan's explicit instruction:** whether
`measure.py` can open a cached plane and have its annotations resolve.
Needed zero adaptation — `measure.load_measurement_plane` already passes
a bare green-shaped TIFF through as-is, which is exactly what a cached
plane is. `plane_cache.py`'s own `--render-check` proves the full loop
(store → open via `measure.load_measurement_plane` → hash matches the
cache key → `annotations.save_mark` under that hash → resolves back via
`image_record_for`), and `qt_shell.py`'s own check drives the real button
handler against a real committed mark, not a hand-fed referenced set.

**A real-hardware finding changed a default, not just informed one.** The
plan asked for extraction timing to be measured on the Pi 5 rather than
trusted from a size estimate. Driving `Picamera2` directly (the
documented on-rig workaround) through the app's own real capture chain:
`extract_green` is negligible (~0.03 ms); hashing is ~9 ms; an
uncompressed TIFF write is ~6 ms — but a deflate-compressed write of that
same real captured plane is ~570-600 ms, nearly two orders of magnitude
slower, because real sensor noise compresses far more slowly than
synthetic random data of the same shape/dtype (~90 ms on the same rig).
600 ms is not the "imperceptible on first click" Part 05's design assumes,
so `store_plane` writes uncompressed by default — a deliberate, measured
departure from this project's usual deflate-TIFF convention, documented
with the real numbers in `plane_cache.py`'s own module docstring so it
isn't quietly reverted without re-measuring. Also worth carrying into Part
05: the DNG capture-and-write step itself measured ~530 ms on its own in
the same test, dwarfing anything the cache adds — an existing cost of the
capture path, not something Part 04 introduces, but one Part 05 will need
to reckon with directly for a first-click pull to feel instant.

### Intent: Green-plane cache (Preferences-dialog plan set, Part 04)

Recording intent before building, per the project's two-phase
documentation rule. Full design in `PLAN_00_context_and_supersession.md`
and `PLAN_04_green_plane_cache.md` (drafted, not checked into the repo).
Depends on Part 01 (built) for the Advanced-tab controls it wires up;
blocks Part 05 (live measure panel, undrafted), which needs this cache to
have somewhere to pull a plane into and point a committed mark at.

Plan: a new `plane_cache.py`, keyed by `pixel_sha256` so pruning is
mechanical (referenced in `annotations.json` == never pruned) and so
`measure.py` can open a cached plane with its marks resolving via no
external index. Location under the Part 03 provenance root, not the
capture output folder. Two controls, already stubbed in Part 01's
Preferences > Advanced: "Clean cache now" (immediate) and "Automatically
clean after N days" (off by default). Extraction timing to be measured on
real hardware before trusting the plan's own size-based estimate, since
Part 05's interaction design assumes the pull is fast enough to be
imperceptible on first click — report a real number, not an assumption.

### Build: Debayer.py tonemap/write split (Part 03 follow-up)

Part 03 (below) shipped with TIFF locked checked in Preferences >
Advanced's export-format row, because `debayer.py` had no way to skip
writing it. Brandon flagged this as a workaround rather than the real
"whatever's checked is what gets written, full stop" contract, and asked
for an audit-first fix across `frame_average.py` → `debayer.py` →
`hdr_merge.py` → `hdr_from_session.py`, with a consumer-compatibility
check gated before touching `debayer.py`. See `HANDOFF.md`'s matching
section for the full account.

`frame_average.py`/`hdr_merge.py` needed no changes (both entirely
upstream of tonemap). The consumer check found `gallery.py`/`measure.py`
have zero dependency on `final_display.tif`, but `process_wizard.py` has
its own independent `debayer.py --tonemap-out` call that had to keep
working. `debayer.py`'s tonemap computation and TIFF-writing were fused;
split so the in-memory tone-mapped result now feeds three independent
write-format flags (`--tonemap-tiff`/`--no-tonemap-tiff` new, default on;
`--tonemap-8bit` existing; `--tonemap-jpg` new), none reading another's
file off disk. `hdr_from_session.py`'s post-hoc PIL-based JPG conversion
(added earlier the same day, and already the wrong shape) is gone,
replaced by passing `--tonemap-jpg` straight to the subprocess call; a
new output-existence check now also catches a silently-failed PNG/JPG
write (e.g. missing Pillow) that exit-status checking alone would have
missed. `qt_shell.py`'s TIFF checkbox is unlocked, a real persisted
preference like PNG/JPG/DNG.

Full `--render-check` sweep, all 15 modules, passes — including
`process_wizard.py`, which exercises the real `--tonemap-out` contract
through its own subprocess call.

### Build: Provenance relocation, Keep RAW, and auto-processing (Preferences-dialog plan set, Part 03)

Builds the intent recorded below. Full `--render-check` sweep passes
across all 15 remaining modules (`casual_mode.py` is deleted as part of
this entry, down from the prior 16-module baseline). See `HANDOFF.md`'s
own Part 03 section for the complete account — this entry summarizes.

**Landed as designed:** the capture/provenance folder split
(`OUT_ROOT`/`PROVENANCE_ROOT`/`FLAT_ROOT`, dark nested under
`<capture>/dark/`, z-stack moved to `<capture>/focal/<stack_id>/plane_N/`)
in `provenance.py`; auto-processing with no Yes/No gate, including Snap
as a new call site, in `qt_shell.py`'s renamed `_auto_process`; structured
flat/dark correction status parsed from `hdr_from_session.py`'s
`CORRECTION_STATUS_JSON:` stdout line and written onto each capture's own
`session.json` entry; Keep RAW Images off deleting raw frames + the
linear master once processing succeeds and recording the discard as
deliberate (`raw_discarded` + `raw_discard_reason`); `measure.py` failing
legibly (never a silent JPG fallback) on a raw-discarded capture, naming
the TRUE reason instead of `calibrate.resolve_raw_path`'s generic "file
moved on its own" wording; `casual_mode.py`'s format handling (DNG/PNG/
JPG/TIFF + Process DNG merge) and JPG-first delivery lifted into the main
capture path, then the module deleted.

**Departed from the plan files, settled directly with Brandon mid-build:**
the lifted format controls live in Preferences > Advanced (persisted,
live-applied at processing time), not a per-capture control row; TIFF is
shown as a checkbox for visual parity but is locked checked and disabled,
since `final.tif`/`final_display.tif` are `debayer.py`'s own structural
output of the tonemap step this pipeline always runs, not a separately
gatable export; PNG/JPG/DNG are the three genuinely optional formats,
gated so an unchecked format is never produced at all (never
produced-then-deleted). An audit of `gallery.py`/`process_wizard.py`/
`measure.py` (done before writing any gating code, at Brandon's explicit
direction) found zero dependency on `final_display.tif`/`.png` existing
in any of the three, so none of them needed changes.

**Real bugs found and fixed, not anticipated by the plan files:**
1. `qt_shell.py`'s `_end_zstack` passed capture-side plane directories
   straight to `_stacks.validate_all`, which reads `session.json` from
   whatever directory it's given — a regression from this same build's
   own earlier provenance-split work, silently validating nothing and
   reporting "No issues found" even for a real stack. Fixed by mapping
   through `_provenance_dir_for` first, with a new render_check
   assertion that actually inspects `validate_all`'s output (nothing
   previously did).
2. `measure.py`'s z-stack code (`collect_stack_planes`/
   `_on_exclude_toggled`) and, via it, `stacks.py`'s `load_session`
   assumed `session.json` sits beside the raw frames — pre-existing
   coupling the plan files' consumer list didn't name. Would have
   silently found zero stacks under the new layout. Fixed with
   `measure.py`'s own `_provenance_dir_for` (duplicated from
   `qt_shell.py`'s rather than importing the whole Qt capture GUI for
   one helper); `stacks.py` itself needed no change.
3. `_auto_process` let `hdr_from_session.py` default `--raw-ext` to
   `"dng"`, correct on real hardware but silently broken under the
   default (no `--camera`) `FakeCamera` backend, which writes `.tif`.
   Fixed by detecting the real extension via `capture_correction_status`,
   the same mechanism the manual processing wizard already used.
4. `casual_mode.py` called `hdr_from_session.process()` directly and
   broke twice as that function's signature changed under it during this
   build (a new required `a.flat_root`, then a new tuple return) —
   fixed both times before the module was deleted, so its own
   `--render-check` kept passing throughout rather than only at the end.

`wizard_pages.py`'s `ImageSourcePage._on_open_existing` also got a
related fix: picking a raw-discarded Gallery entry used to silently do
nothing (`GalleryWidget.selected_paths()` drops any entry with
`raw_path=None`); it now reports why and points at the manual-file
escape hatch.

### Intent: Provenance relocation, Keep RAW, and auto-processing (Preferences-dialog plan set, Part 03)

Recording the intent to build Part 03 before any code exists, per this
project's two-phase rule. Full design in `PLAN_00_context_and_
supersession.md` and `PLAN_03_provenance_relocation_and_keep_raw.md`
(drafted, not checked into the repo), plus `CORRECTION_flat_dark_
framing.md` (also not checked in), which corrects how Flat/Dark
correction status must be recorded and displayed — as the named
technique that ran or was skipped, never folded into a generic
"processing complete." This entry also captures folder-layout and
plumbing decisions settled in conversation that go beyond the plan
files' own text.

**Supersedes `casual_mode.py` in full** (see Plan 00) — its capture-and-
save logic, format handling, and `(Exception, SystemExit)` catch around
`hdr_from_session.process()` are reused, lifted into the main capture
path; the module itself is deleted at the end of this part, not before.

**Folder layout** (three Preferences > Advanced settings; `provenance_
folder` already exists from Part 01, adding `capture_folder` (default
`~/captures`) and `flat_library_folder` (default `~/flat`)):
- `<capture_folder>/<timestamp>/` — science/hdr/snap raws + processed
  outputs (final.tif, final_display.*, per-format exports).
- `<capture_folder>/<timestamp>/dark/` — that session's own dark
  sub-burst, nested under it.
- `<capture_folder>/focal/<stack_id>/plane_N/` — z-stack, moved off the
  direct-under-`OUT_ROOT` location it uses today.
- `<flat_library_folder>/` — one standing set, replaced outright by each
  new Flat capture, reused across every session. `hdr_from_session.py`'s
  "last flat wins" changes from scanning the current session's own
  `captures` list to reading this one fixed folder.
- `<provenance_folder>/<timestamp>/` — `session.json` + meta sidecars
  only, no image bytes. A new field on the session record stores the
  capture dir's absolute path, since provenance and images no longer
  share a folder.

**Provenance is always written; only Keep RAW Images gates what
survives.** Off means raws + the linear master (`single_master.tif`/
`master_*.tif`/`hdr_linear.tif`) are deleted once processing succeeds,
and the session record states the discard was deliberate — a later
reader must be able to tell "user chose not to keep these" from "a file
is missing," never leave that as a silent omission. `measure.py` gets a
distinct, plain-language error for a raw-less capture, and must never
silently fall back to the JPG display derivative (structurally excluded
from `annotations.json` already, for the same reason).

**Auto-processing replaces `_offer_process`'s Yes/No `QMessageBox`.**
Snap, Science, and HDR all process automatically now — snap is a new
call site, since today only science/hdr ever reach `_run_process_cmd`
(a single frame never went through frame-averaging/debayer via this
path before). `hdr_from_session.py`'s `process()` stops just `print()`ing
its `ran`/`skipped` stage lists and returns them structured, so `qt_
shell.py` can persist `"flat_correction": "applied"` / `"dark_
correction": "skipped (not selected)"` onto the capture's own `session.
json` entry — per the correction doc, this must name the technique, not
report a generic "processing" status, and a capture with neither
selected states that explicitly rather than omitting the fields. Flat
and Dark selection stays exactly as visible in the capture UI as it is
today — not moved to Advanced, not collapsed into one implied toggle.

**Not yet built, deliberately no code changed for this entry** —
verification and documentation only. Build order: `provenance.py`'s new
roots + Session split, `qt_shell.py`'s Preferences additions and capture
path re-plumbing, `hdr_from_session.py`'s structured return and
flat-library lookup, the auto-process wiring, Keep RAW deletion,
`measure.py`'s legible failure, `gallery.py`/`process_wizard.py` path
resolution, then lifting casual_mode.py's format checkboxes + JPG-first
delivery before deleting it. A completion entry follows once this lands,
noting anything that deviated.

### Camera capability query: `sensor_modes` hardware-verified (Preferences-dialog plan set, Part 02 follow-up)

Follow-up to the Part 02 completion entry below, which shipped
self-check-verified only and flagged the `Picamera2Camera.get_
capabilities()` `sensor_modes` enumeration as unconfirmed against the
real IMX477. Closed that gap on a session that turned out to have real
rig access: `Picamera2().sensor_modes` read directly (no Qt/GUI layer
involved, so no dependency on the GUI issue noted below) and
`get_capabilities()`'s exact size/format-translation logic run against
the result. Real numbers: 5 discrete sizes ((1332,990), (2028,1080),
(2028,1520), (4056,2160), (4056,3040)) and 3 formats (SRGGB8/10/12).
Also confirms, on real hardware rather than by reading Picamera2's
source, that `sensor_modes`' `"format"` field really is a non-plain
libcamera-typed object (`SRGGB10_CSI2P` etc., not a string) — the exact
thing Part 02's `"unpacked"`-not-`"format"` choice was guarding against.

**Still not verified**: calling `get_capabilities()` through the full
`Picamera2Camera` class construction, which embeds a `QGlPicamera2` GL
preview widget — that failed on this rig with `EGLError: EGL_BAD_ALLOC`
on `eglCreateWindowSurface` (confirmed no other process held the camera
at the time, so this is an EGL/display environment issue, not resource
contention). That's a separate, still-open gap in exercising this class
through its normal construction path, orthogonal to the capability-query
logic itself, which is now confirmed correct against real data. No code
changed for this entry — verification and documentation only. Full
project `--render-check` sweep (all 16 modules) re-run and still passes.

### Preferences dialog: build complete (Preferences-dialog plan set, Part 01)

`5158ff8` (intent, recorded retrospectively — see that entry below for
why), plus this entry's commit.

`PreferencesDialog` lands in `qt_shell.py` as designed in the intent
entry: one sectioned dialog (Capture and Video Options / Appearance /
Advanced) replacing the standalone Video resolution and Theme submenus
and the Casual Mode action. Capture and Video Options is built entirely
from `camera.get_capabilities()` — a capability the driver omits (e.g.
`stream_formats` on `Picamera2Camera` today) produces no row at all, not
an empty or disabled one. Capture/Video/Appearance settings persist only
on OK (next-launch, same as the menus they replace); Advanced settings
(Keep RAW Images, provenance folder location, cache auto-clean) persist
immediately on change, independent of OK/Cancel. `CASUAL_MODE_DEFAULT`
and the Options > Casual Mode action are removed from `qt_shell.py`;
`casual_mode.py` itself is untouched, staying until Part 03.

Two things the intent entry didn't call out, surfaced during the build:

- A new `capture_resolution_kwargs()` (mirroring the existing `video_
  resolution_kwargs()`) wires the dialog's capture-resolution choice
  through to `Picamera2Camera`'s `full_res` constructor kwarg in
  `main()` — the intent entry described rendering `get_capabilities()`
  results but not this specific plumbing back to camera construction.
- The Advanced section's controls persist their prefs for real, but
  nothing reads them yet — there is no retention system to gate. That's
  scaffolding ahead of Part 03 (not yet drafted), not a gap in this part.

**Verified**: full project `--render-check` sweep (all 16 modules,
including `camera_backend.py`) passes with no regressions.
`qt_shell.py`'s own check covers the dialog directly: an omitted
capability produces no control, a present one produces a real control,
next-launch settings persist only on OK, Advanced settings persist
immediately and survive Cancel, and a stale `"casual_mode"` gui_prefs key
(left over from the superseded build) degrades gracefully rather than
raising. **Not yet verified**: this dialog as a live GUI on-rig,
specifically — blocked by the same `QGlPicamera2` EGL surface failure
noted in the Part 02 follow-up above, which prevents constructing a live
`Picamera2Camera` at all in this environment, not something specific to
this dialog.

**Process note**: this project's two-phase rule wants an intent entry
before any code exists. Here, Part 01's intent entry was written
*after* the code already existed (see that entry's own text below for
why) — this completion entry follows normally, landing alongside the
code's first commit, but the ordering that produced it was retrospective
rather than sequential. Recording that honestly here rather than
smoothing it over.

### Intent: camera capability query (Preferences-dialog plan set, Part 02)

Recording the intent to build `camera_backend.py`'s capability query
before any code, per this project's two-phase documentation rule. Full
design in `PLAN_02_camera_capability_query.md` (drafted, not checked into
the repo; see `PLAN_00_context_and_supersession.md` for how this plan set
relates to the rest of the project). Condensed version now in
`HANDOFF.md`'s own "Current state" section, since that's what a fresh
agent reads first.

This is Part 02 of a five-part plan superseding Casual Mode (2026-07-23
entries below) with a single always-on window: one layout, provenance
always written (relocated rather than made conditional), and exactly one
retention setting (Keep RAW Images). Part 02 has no dependency on the
rest of the set and is being built first, sequentially ahead of Part 01
(Preferences dialog), which renders its results — the interface shape
alone, not its implementation.

Adds `get_capabilities()` to `CameraBackend`: a generic capability query
(`capture_resolutions`, `capture_formats`, `video_resolutions`,
`video_formats`, plus `stream_formats`/`stream_resolutions` only where
the driver actually reports them — absent means absent, not empty) so
the future Preferences dialog is populated from what the hardware
actually offers rather than a hardcoded list. Enforces the plan's
stricter reading of this project's existing "thin adapter" framing
(README.md's "All camera-bound operations sit behind one thin adapter"):
`camera_backend.py` becomes the only file allowed to know what Picamera2
or an IMX477 is; every other module must run unchanged against a
different sensor with a different driver in its place.

Build order: agree the interface shape → implement on `FakeCamera` (a
small, clearly-synthetic set, including a way to exercise the
stream-format-present path even though the real driver doesn't have one
yet) → implement on `Picamera2Camera` from `sensor_modes`/
`camera_controls`, translated to plain dicts/lists/strings/numbers → a
structural self-check that no other module imports `picamera2`/
`libcamera` directly. A completion entry follows once the build lands,
noting anything that deviated and why.

### Camera capability query: build complete (Preferences-dialog plan set, Part 02)

`baa8745` (intent), plus this entry's commit.

`CameraBackend.get_capabilities()` lands as designed in the intent entry
above, with the interface shape unchanged from the plan's sketch:
`capture_resolutions`, `capture_formats`, `video_resolutions`,
`video_formats` always present; `stream_formats`/`stream_resolutions`
present only where the driver actually has them to report.

`FakeCamera.get_capabilities()` returns a small, clearly-synthetic set
and, by default, omits the stream keys — matching `Picamera2Camera`'s
real current behavior (no stream server exists in this backend). A new
`stream_caps=True` constructor flag makes it populate both stream keys
instead, so the Preferences dialog's present-key rendering path has
something to exercise off-rig even though the real driver can't drive it
yet.

`Picamera2Camera.get_capabilities()` reads `self._picam2.sensor_modes`
and translates every value to a plain primitive before it crosses the
seam. One deviation worth flagging: `sensor_modes` entries carry both a
`"format"` field (a libcamera `PixelFormat` object — never read) and an
`"unpacked"` field (already a plain string, e.g. `"SRGGB12"`) — the plan
didn't specify which to use, and `"unpacked"` is the only one that
doesn't leak a Picamera2 type, so `capture_formats` is built from that.
`video_resolutions` reuses the same discrete sensor-mode sizes as
`capture_resolutions` rather than trying to enumerate the ISP's
continuous main-stream scaling range as a list — a design choice, not
an oversight (see `HANDOFF.md`'s note on this part for the reasoning).

Added `assert_only_camera_backend_imports_picamera2()`: a grep-style
structural self-check, run every `python3 camera_backend.py`, scanning
every other `.py` file in the project for a direct `picamera2`/
`libcamera` import. It surfaced two **pre-existing** violations —
`wizard_pages.py`'s camera-availability probe and `test_burst_backend.py`'s
direct hardware test, both predating this plan set — which are carved
out as documented, named exceptions in the check itself rather than
silently ignored or "fixed" as an unplanned side effect of this part.
Fixing them (routing the availability probe through `camera_backend.py`
instead) is a separate, explicitly out-of-scope concern for whoever picks
it up next.

Self-check-verified only. The full `--render-check` sweep (all 15
modules) plus `python3 camera_backend.py` all pass with no regressions.
**Not hardware-verified**: `Picamera2Camera.get_capabilities()`'s
`sensor_modes` enumeration has not been run on the rig, so its actual
`capture_resolutions`/`capture_formats` values for the IMX477 are
unconfirmed — a headless pass proves the interface holds, not that the
reported numbers are right, per this plan set's own standing caution.

### Intent: Preferences dialog (Preferences-dialog plan set, Part 01)

Recording the intent to build the Preferences dialog — retrospectively.
This project's two-phase rule wants an intent entry before any code
exists; here it's being written after Part 01 was already built, in the
same working pass as Part 02 above. Recording that honestly rather than
backdating this entry to look sequential: the code sits in the working
tree, uncommitted, as of this entry. A completion entry follows once it
lands as its own commit, noting anything that deviated.

Full design in `PLAN_01_preferences_dialog.md` (drafted, not checked into
the repo). Part 01 renders Part 02's `get_capabilities()` results in one
sectioned dialog (Capture and Video Options / Appearance / Advanced),
replacing the standalone Video resolution, Theme, and Casual Mode menu
entries — Casual Mode's `qt_shell.py` plumbing (`CASUAL_MODE_DEFAULT`,
the `"casual_mode"` pref, the menu action, `main()`'s window-class
branch) goes with it, though `casual_mode.py` itself stays until Part 03.

## 2026-07-23

### Intent: Casual Mode (BUILD_LIST Tier 3, item 2)

Recording the intent to build Casual Mode before any code, per this
project's two-phase documentation rule. Full design in
`PLAN_casual_mode.md` (drafted, not checked into the repo); condensed
version now in `HANDOFF.md`'s own dedicated section, since that's what a
fresh agent reads first. Depends on `provenance.py` phase 1
(2026-07-22 entry below) — that extraction is the reason Casual Mode is
buildable at all: "no provenance" becomes *a module this path never
imports*, not a flag threaded through capture code.

Casual Mode is the same capture behavior (snap, burst frame-averaging,
HDR bracket, debayer, tonemap) with a different retention policy: no
session folder, no `session.json`, no sidecars, no `pixel_sha256`, no
`calibration_ref` — only the final image survives, intermediates cleaned
up automatically. The separation is structural: `casual_mode.py` never
imports `provenance.py`'s write functions, asserted by the module's own
`--render-check`, not just tested behaviorally.

Two things resolved during design that the plan itself flagged as open:

1. **Provenance entanglement in the "existing pipeline."** `qt_shell.py`'s
   normal capture path calls `hdr_from_session.py` as a subprocess, and
   that CLI's `main()` requires a real `session.json` on disk to run at
   all. `hdr_from_session.py`'s own `process()` function, underneath
   `main()`, has no such requirement — it takes plain `session`/`cap`
   dicts and writes nothing provenance-related. `casual_mode.py` will
   import `process()` directly and hand-build those dicts in memory,
   never touching `session.json`. Same image operations, no CLI
   entanglement.
2. **What "dng" means for a merged Burst/HDR result.** A real DNG is a
   raw Bayer-mosaic container; Burst/HDR's frame-averaged/HDR-merged
   result is a single TIFF master with no valid DNG to land in, and
   writing it under a `.dng` extension would misrepresent the file (this
   project's own rule against mislabeling derivatives — see `publish.py`'s
   `"NOT a measurement"` labeling). Brandon's call: drop the plan's fixed
   seven-preset format list in favor of independent format checkboxes
   (DNG/PNG/JPG/TIFF, any combination) plus a dedicated checkbox, active
   only for Burst/HDR, for what DNG means there — first raw frame
   untouched (default) or the merged master honestly saved as `.tif`
   instead of `.dng`. JPG-first UX (placeholder JPG immediate, atomic
   replace, honest failure) is unchanged from the plan.

Build order (each step lands with its own `--render-check` before the
next begins): preference/menu plumbing → module skeleton with the
import-boundary self-check → single-shot capture with cleanup → format
checkboxes + JPG-first → Burst/HDR. A completion entry will follow once
the build lands, noting anything else that deviated and why.

### Casual Mode: build complete

All five steps landed in one pass: `gui_prefs.json`'s `"casual_mode"` key
(default ON, `CASUAL_MODE_DEFAULT`) + the Options > Casual Mode checkable
action + `main()`'s branch in `qt_shell.py`; the new `casual_mode.py`
module (`CasualModeWindow`, `run_capture_and_save`,
`assert_no_provenance_import`) covering the skeleton, single-shot
capture, format checkboxes + JPG-first atomic replacement, and Burst/HDR.
See `HANDOFF.md`'s Casual Mode section for the full as-built account;
summarizing what deviated from the intent entry above and why:

- **Format UI**: the plan's fixed seven-preset list became four
  independent checkboxes (DNG/PNG/JPG/TIFF, any nonempty combination)
  plus a "Process DNG (merge Burst/HDR frames)" checkbox, per Brandon's
  own framing during the build ("give a checkbox option ... to select
  which gets the process, if any, or both"). Unprocessed DNG delivers the
  first raw frame untouched; processed delivers the pipeline's own
  already-computed raw-domain master (`single_master.tif`/
  `hdr_linear.tif`), honestly renamed `<stem>_raw.tif` — never a
  mislabeled `.dng`. This also fixed a real filename collision that the
  original one-extension-per-format naming would have hit: a Burst/HDR
  capture with both "tiff" and merged-"dng" checked would otherwise write
  two different images to the same `<stem>.tif`.
- **Processing entry point**: `casual_mode.py` imports
  `hdr_from_session.process()` directly rather than shelling out to
  `hdr_from_session.py` the way `qt_shell.py`'s own `_run_process_cmd`
  does — that CLI's `main()` requires a real `session.json` on disk,
  which is exactly the artifact Casual Mode exists to avoid writing.
  `process()` itself needed no changes; it already took plain dicts and
  wrote nothing provenance-related.
- One thing the build surfaced that the intent entry didn't call out:
  `process()` calls `sys.exit(...)` directly on a couple of its own
  error paths (missing frame files), which raises `SystemExit`, not
  `Exception` — `run_capture_and_save`'s honest-failure handler catches
  `(Exception, SystemExit)` explicitly for this reason; missing it would
  have hung the capture thread silently on that specific failure shape.

**Verification: self-check only, not hardware-verified.** Full project
`--render-check` sweep (15 modules, including `provenance.py` and
`casual_mode.py`, both newly added to the sweep list in `HANDOFF.md`)
passes, and `casual_mode.py`'s own check drives a real end-to-end capture
through `CasualModeWindow`'s actual worker thread and queued completion
signal (not just the underlying pure function). None of this has run on
the real IMX477 rig — this was built in a non-interactive session with no
hardware access. `CaptureResult.preview` is always `None` on FakeCamera,
so only the PIL-synthesized placeholder-JPG fallback has actually
executed; the real free-copy path, the real `.dng`/`.jpg` pairing, and
`camera.widget` embedding in `CasualModeWindow` all still need the
documented on-rig workaround (drive `Picamera2` directly) before this
ships as trusted.

## 2026-07-22

### Known limitation (not fixed): full-screen mode doesn't cover the desktop taskbar

Reported after the quarter-screen fix below was confirmed working: the
live image now fills the real screen correctly, but the labwc taskbar
(`wf-panel-pi`) stays visible over the bottom edge, confirmed via a photo
of the actual tablet.

Root cause: `wf-panel-pi` is a `wlr-layer-shell` surface, which lives in
its own compositor layer ABOVE ordinary windows by design -- real
`showFullScreen()` gets special compositor handling that raises above
that layer automatically, but `_toggle_fullscreen` deliberately avoids
real fullscreen now (see the fix below), so this window has no automatic
way to get that same stacking.

Two ways tried to raise above it anyway, both dead ends on this rig
(labwc 0.8.4): `Qt.WindowStaysOnTopHint` via `setWindowFlags` forces Qt
to recreate the window's native handle out from under `self.preview`'s
already-created EGL surface -- confirmed on-rig as a real crash (XCB
`BadDrawable`/`BadWindow`, preview went black, needed a fresh process).
A raw EWMH `_NET_WM_STATE_ABOVE` `ClientMessage` sent by hand (`ctypes` +
`libX11`, its own separate Xlib connection, so it never touches Qt's own
native window) was confirmed delivered (`XSendEvent`/`XFlush` both
succeeded) but silently ignored -- `xprop` on the window afterward showed
only `_NET_WM_STATE_FOCUSED`, never `_ABOVE`; labwc doesn't honor that
request at runtime on this version. Both attempts were reverted; no
trace of either is left in `qt_shell.py`.

One real lead left unexplored: labwc's own `ToggleAlwaysOnTop` action
(`labwc-actions(5)`), reachable from an `rc.xml` `<windowRule>` keyed off
a distinctive window title set via `setWindowTitle()` (a plain X11
property change, not a `setWindowFlags`-style recreation, so it shouldn't
carry the same crash risk) -- untried because it requires a one-time edit
to this rig's own `~/.config/labwc/rc.xml`, outside this repo, and needs
the user's buy-in first.

### Fixed (for real this time, confirmed live on-rig): full-screen preview stuck at a quarter of the screen

Third attempt at the full-screen preview bug below. The second attempt's
`xcb` switch was itself confirmed correct and harmless (this session ran
the actual app against the real IMX477 camera on the rig itself: after
`_toggle_fullscreen`, the Qt widget geometry, the real X11 window
(`xwininfo`), and the EGL window surface (`eglQuerySurface`) all reported
the full 2048x1080 screen size, exactly as they should) — but the user's
own follow-up photo showed the live image still confined to a small
rectangle, so the native-window/platform-plugin theory itself was wrong.

Real root cause: this rig's display is a physically 4096x2160 panel
driven at a compositor-level 2x output scale (`wlr-randr --output
HDMI-A-1 --scale 2`, in `~/.config/labwc/autostart`, added by the user to
make the UI physically legible on the panel). XWayland presents that to
X11/Qt clients as a "logical" 2048x1080 screen with `devicePixelRatio`
1.0 — Qt has no idea the real panel is 2x bigger. Ordinary windowed
content is fine because the compositor's normal composited render path
applies that 2x scale-up when painting the window. But a *real*
`showFullScreen()` puts the window into the compositor's actual
`xdg_toplevel` fullscreen state, and wlroots-based compositors (labwc
included) commonly fast-path a fullscreen surface straight to the
display's scanout hardware, bypassing the normal composited scale-up
pass entirely — so the client's own 2048x1080 buffer (correct in its own
logical terms) lands on the real 4096x2160 panel covering exactly one
quarter of it, unscaled. Confirmed by screenshotting (`grim`) the actual
rig mid-fullscreen: solid black/no visible content, consistent with the
direct-scanout path not going through the normal compositing grim's
`wlr-screencopy` capture relies on.

Fix: `_toggle_fullscreen` no longer calls `showFullScreen()`/
`showNormal()` at all. It now sets `Qt.FramelessWindowHint` and manually
resizes the window to `QApplication.primaryScreen().geometry()` (and
restores the saved pre-fullscreen geometry + flags on exit) — visually
identical to the user, but the compositor never sees an `xdg_toplevel`
fullscreen state, so it stays on the ordinary composited (and therefore
correctly 2x-scaled) render path. `FocusPreviewWindow._is_fullscreen`
(plus `_pre_fullscreen_geometry`) now backs this app's own notion of the
state, since `isFullScreen()` stays `False` the whole time. Re-verified
live on-rig after the fix, real camera running: `grim` now captures the
actual specimen image filling the panel (thin aspect-ratio letterbox
bars only), not a quarter-screen rectangle or black.

### Fixed: full-screen preview stuck at its old windowed-mode size on-rig

Reported live from the actual tablet (photo evidence): entering full
screen resized the window itself but the live camera preview stayed
pinned to a small rectangle at its old size/position instead of filling
the screen.

First attempt: theorized `self.preview` (the real `QGlPicamera2` widget
on-rig, a `WA_NativeWindow`/`WA_PaintOnScreen` widget painting through its
own native window rather than Qt's backing store) just needed an explicit
nudge, and had `_toggle_fullscreen` schedule `self.preview.resize(self.
_splitter.size())` via `QTimer.singleShot(0, ...)`. Reported back as
having *zero* effect on-rig — wrong theory, reverted (including the
render-check assertion added for it).

Real root cause, found by actually checking this rig's Qt platform
(`QApplication().platformName()` → `"wayland"`, running under `labwc`):
nested native child windows (exactly what `self.preview` is) are a
documented, real limitation of Qt5's native Wayland platform plugin —
their underlying surface does not reliably follow the widget's own
Qt-side geometry once the top-level window's own state changes out from
under them. Confirmed this really was it: the preview's own `resizeEvent`
and `glViewport` call WERE firing correctly with the new size (so the
first fix's theory about Qt-side resize not happening was itself wrong),
but the actual visible native surface stayed stuck regardless — a
Wayland-compositor-level disconnect, not anything `qt_shell.py`'s own
code could paper over. Fix is environmental: `qt_shell.py` now does
`os.environ.setdefault("QT_QPA_PLATFORM", "xcb")` before PyQt5 resolves a
platform, routing through the already-running XWayland instead, where
real X11 child subwindows don't have this limitation. `setdefault` so an
explicit `QT_QPA_PLATFORM` in the environment still wins. Needs on-rig
confirmation (this environment has no way to visually verify a real
fullscreen Wayland/X11 compositor transition).

### `provenance.py` extraction, phase 1 (BUILD_LIST Tier 3, item 1)

Built the plan recorded in the prior commit. This turn's own Tier 0
investigation confirmed `camera_backend.py` has zero session/provenance
awareness — the original thin-adapter design intent held — so this was a
clean pull-out, not a rewrite: `OUT_ROOT`, `PROFILE_PATH`, `load_profile`/
`save_profile`, `_dump_meta`, `new_session_dir`/`new_zstack_root_dir`,
`class Session`, and `record_capture`/`record_burst`/`record_hdr` moved
out of `qt_shell.py` into a new `provenance.py`, verbatim. Unblocks
Casual Mode (item 2) and the store-mechanics migration (phase 2, item 7).

The one real hazard: `render_check()` mutates `OUT_ROOT`/`PROFILE_PATH`
as module state to isolate its own test fixtures (and, after two real
incidents this session, to keep the whole self-check off the real
`~/imx/profile.json`). Every consumer references `provenance.OUT_ROOT`/
`provenance.PROFILE_PATH` by attribute, never `from provenance import
OUT_ROOT`, which would create a second binding that silently stops
tracking the moment either side reassigns it — `provenance.py` carries an
explicit comment on the constants themselves saying so, not just this
note. `list_sessions`/`load_session_json`/`processable_captures`/
`capture_correction_status`/`archive_session_raws`/`build_display_flags`
stayed in `qt_shell.py` — reading `session.json` back out for browsing is
a different concern from writing new provenance records, and the build
list's own phase-1 scope doesn't cover them.

`gallery.py` and `process_wizard.py`'s `OUT_ROOT` defaults, and
`wizard_pages.py`'s `new_adhoc_dir`, all went through the same lazy
`_lazy_qt_shell()` indirection `qt_shell.OUT_ROOT`/`qt_shell.
new_session_dir` used to resolve through before this move — reworked to
reference `provenance.OUT_ROOT`/`provenance.new_session_dir` directly
instead (a plain top-level import, safe since `provenance.py` sits at the
base of the import graph and imports nothing back into any of these
three files, unlike the real `qt_shell.py` cycle `_lazy_qt_shell()` still
exists to break). Left unfixed, `gallery.py`'s and `process_wizard.py`'s
`OUT_ROOT` defaults would have raised `AttributeError` the first time
either ran with no explicit `out_root` argument, since `qt_shell.py` no
longer defines that name.

Caught one real bug the move itself introduced: `qt_shell.py`'s own
`render_check()` had three internal call sites (`Session(`, `record_burst(`
×2) that never got module-qualified to `provenance.Session`/`provenance.
record_burst` when everything else in the file did — `qt_shell.py
--render-check` was crashing with a `NameError` before this fix. All five
touched modules' own `--render-check` (`provenance.py`, `qt_shell.py`,
`gallery.py`, `wizard_pages.py`, `process_wizard.py`) pass clean now.

### Added full screen mode with a floating panel (BUILD_LIST Tier 2)

The build list flagged this as blocked on a design decision (auto-hide on
idle vs. an explicit toggle key vs. an always-visible translucent
overlay). Discussed it with the user: explicit toggle key, deliberately —
a translucent overlay would permanently obscure part of the live
specimen view, which matters more here than in a typical app, since this
is a tool used to visually judge focus/color/contrast. They also asked
for the menu bar to hide during full screen, with a way back out.

`F11` (or View > "Full screen") toggles; the SAME `_panel` widget instance
(now stored as `self._panel`, not just a local in `__init__`) reparents
between the normal-mode `QSplitter` and a lazily-created, never-destroyed
floating `Qt.Tool | Qt.FramelessWindowHint` window on entry/exit, so no
control's state — a slider position, a combo selection — is ever lost by
the move. Hidden by default on entry (explicit toggle, not auto-shown —
maximizing the preview is the whole point); `P` shows/hides it while full
screen, and is a genuine no-op otherwise (not `Tab`, which is Qt's own
widget-focus-traversal key — repurposing it would have silently broken
keyboard navigation through the sliders and combos). `Ctrl+Escape` exits
— plain `Escape` already does real work in this app (cancel an armed
burst, abort a batch sequence) and wasn't overloaded with a third
meaning; being a distinct key combination, it needs no priority ordering
against those two existing branches. `closeEvent`'s `panel_width` save
now guards on the panel actually being a splitter child, since mid-float
`self._splitter.sizes()` no longer describes it.

Deliberately not persisted across a relaunch (unlike `panel_width`, the
ruler toggle, or the focus-aid-at-startup preference, which all do
persist once set) — launching straight into full screen with the menu
bar already hidden, with no visible reminder that F11/Ctrl+Escape is the
way out, could genuinely be disorienting in a way a remembered panel
width never is.

New render-check coverage drives the real toggle methods and real
`QKeyEvent`s (not bypassed): entering hides the menu bar and reparents
the real panel out of the splitter; a full second entry/exit cycle
confirms the reparenting actually repeats (this caught a real bug — the
panel was only ever added to the floating window's layout on its first
construction, so a second F11 press left it stranded); Ctrl+Escape only
exits once an armed burst no longer claims Escape first; `P` is a true
no-op outside full screen. Also manually smoke-tested end to end under
`QT_QPA_PLATFORM=offscreen`.

**Also**: `render_check()` now monkeypatches `PROFILE_PATH` for its ENTIRE
duration (not just the sub-blocks that already did), after real hardware
profile data got silently overwritten a second time despite the earlier
atomic-write fix — the second occurrence didn't reproduce reliably enough
to pin to a specific trigger, so this is the belt-and-suspenders fix: no
`FocusPreviewWindow` constructed anywhere in the self-check, now or in the
future, can ever touch the real file again.

## 2026-07-21

### Carried the focus-meter auto-reset over to the z-stack aid (SPEC_focus_aid_fps_and_stack_reset.md part 2)

The original spec (implemented earlier this session, `ccc00fb`) called
`self.meter.reset_field()` on a successful manual stack-plane tag
(`_on_tag_stack`) and explicitly flagged that this requirement would carry
over to "whatever action ends up being 'this plane is locked in'" once a
one-click z-stack flow existed. That flow (`_capture_zstack_plane`/
`_on_zstack_plane_finished`) was built later in the session without this
carrying over — a real gap the spec's own forward note anticipated
exactly. Fixed: `_on_zstack_plane_finished`'s success path now resets the
field, same reasoning as the manual tag ("last plane's peak/settle is
stale history, not a real reading for this one"); the failure branch
(`isinstance(result, Exception)`) already returns before reaching it, so a
failed capture/tag still can't wipe unrelated focus history.

Extended the z-stack aid's own render-check coverage (rather than adding
a separate test) to assert the same three-way contract `_on_tag_stack`'s
own test already proves: fires once per successful plane (checked after
plane 0's auto-capture and again after two more Capture presses), and
does not fire on a simulated tag failure (`stacks.apply_tag` monkeypatched
to raise) — run last in the sequence, after the folder-layout/tagging
assertions, since the simulated failure deliberately leaves a stray
untagged plane folder behind that would otherwise break the "exactly
plane_0/1/2" check.

### Added an extensible themes system (BUILD_LIST Tier 1, item 3)

Built deliberately open-ended rather than a fixed Dark/Light pair: the user
plans to design a dozen-plus side-panel aesthetics over time and wants the
code to never need touching again to add one. New `themes/<name>/style.qss`
contract, scanned dynamically by `discover_themes()` — dropping in a new
theme folder is the entire integration step. Optional
`themes/<name>/assets/` for images; a theme's own QSS references them via
`url({{ASSETS}}/file.png)`, substituted by `load_theme_stylesheet()` for
that theme's own absolute assets path at load time (plain QSS `url()`
paths resolve against the app's working directory, not the stylesheet's
own location, which would silently break image references the moment the
app is launched from anywhere else — the placeholder is what keeps a
theme folder portable).

`qt_shell.py`'s side panel (the capture/exposure controls column) now
carries `objectName("side_panel")`, so a theme's QSS has something precise
to target with `#side_panel { ... }`. New Options > Theme submenu, built
from whatever's actually discovered (`Default` always present even with
zero themes designed yet), same persisted/next-launch pattern as the
video resolution menu (`resolve_theme_qss_path()` degrades a stale or
deleted theme preference to the stock look rather than raising in
`main()`). Shipped one minimal starter theme (`themes/dark/style.qss`,
plain colors, no image assets) purely to prove the pipeline end to end —
the actual dozen-plus aesthetics are the user's own design work, not
something built here.

New render-check coverage: `discover_themes` against a real folder tree
(ignoring files and folders that aren't themes), `{{ASSETS}}` substitution
correctness, `resolve_theme_qss_path`'s graceful degradation on a missing
theme, plus a real `FocusPreviewWindow` check that the Theme menu reflects
what's actually on disk and persists a choice correctly. Manually
smoke-tested under `QT_QPA_PLATFORM=offscreen`: the shipped `dark` theme
discovered, loaded, applied via `app.setStyleSheet`, and the `side_panel`
object name confirmed present on the real widget.

### Fixed `save_profile()` to write atomically (data-loss hazard, not a build list item)

Discovered while wrapping up the green-plane extraction work: `git diff`
showed `profile.json` — real hardware exposure/gain/WB data — had been
silently overwritten with fake `FakeCamera`-probed values. Root cause:
`save_profile()` was the one store writer in `qt_shell.py` still using a
direct `PROFILE_PATH.write_text(...)`, not the temp-file-then-`os.replace`
pattern `save_pref`/`save_calibration`/`save_mark` all already use.
`FocusPreviewWindow.__init__` falls back to probing and saving a fresh
profile whenever `load_profile()` doesn't find one; two overlapping
`--render-check` processes (run while debugging the `measure.py` hang
earlier this session) racing a non-atomic write against a read is the
leading explanation, though not caught in the act — a single clean run
could not reproduce it. Fixed to the same atomic pattern regardless; real
data was restored via `git checkout -- profile.json` before committing
anything (confirmed against `git log`/`git show HEAD` first). Not a build
list item, but real data loss from a real gap in this exact codebase,
directly triggered by this session's own testing — worth fixing on sight
rather than filing away.

### Added the single green-plane extraction utility (BUILD_LIST Tier 1, item 4)

A new "Extract green plane..." File menu action in `qt_shell.py`, exactly
as small as the build list said it'd be: `debayer.py --green` already does
the real work (zero-interpolation green extraction, provenance-stamped
output), so this is a menu action wrapping it — pick a source via the
Gallery pick dialog, pick a destination via a save-file dialog defaulting
to `debayer.py`'s own CLI naming convention (new Qt-free
`default_green_output_path()`, so a file this menu writes has the
identical name someone would get running `debayer.py --green` by hand on
the same input), then run it as a subprocess on a worker thread — same
`subprocess.run(..., stdin=subprocess.DEVNULL)` shape `_run_process_cmd`
already uses for `hdr_from_session.py`, new `DEBAYER_TOOL` constant
alongside the existing `PROCESSOR` one. Its own signal/handler pair
(`green_extract_done_signal`/`_on_green_extract_finished`), not a reuse of
`_on_process_finished`, which offers to archive a session's raws on
success — meaningless here, since this action has no session involved at
all.

New render-check coverage: `default_green_output_path` against
`debayer.py`'s own default naming formula, plus a real end-to-end
`debayer.py --green` subprocess call (driven through the real worker
thread + queued signal, `processEvents()`-pumped the same way the z-stack
aid's own coverage proved that mechanism works) asserting the output file
exists with real `debayer.py` provenance (`"software": "debayer.py"`,
`"transform": "single_green_extraction"`), plus a failure case (bad input
path) reporting instead of hanging or being silently swallowed.

### Added the video resolution menu (BUILD_LIST Tier 1, item 5)

The build list undersold this one: `camera_backend.py`'s
`set_video_resolution()` already validated input, but its own docstring is
explicit that it currently has **no live effect** — `start_recording()`
always encodes the preview config's fixed "main" stream, built once at
camera construction. A comment in `Picamera2Camera.__init__` claiming the
video config is rebuilt fresh per-recording is stale, describing an earlier
mode-switching design `start_recording`'s own history notes say was tried
and abandoned (it froze the preview pane and shifted exposure on every
switch). So wiring a menu straight to the setter would have produced a
menu that looks functional but silently changes nothing.

Asked the user how to handle it given this project's own repeated,
documented aversion to live camera reconfiguration risk: chose a persisted
preference over a live rebuild. New `qt_shell.py` Options > "Video
resolution" submenu (Default / 1080p / 2K, mutually exclusive via
`QActionGroup`, same shape `save_pref`/`load_pref` already use for
`panel_width` and the focus-aid startup options) writes `gui_prefs.json`;
`main()` reads it via the new `video_resolution_kwargs()` (Qt-free, so this
wiring is testable without a real camera) and passes `preview_res=` to
`Picamera2Camera()` at construction. Explicitly does **not** apply live —
the status text says "takes effect on the next launch" rather than
silently implying it already worked, the same honesty standard
"processing unavailable"/"gallery unavailable" already hold elsewhere.

New render-check coverage: `video_resolution_kwargs` in isolation (no
preference means no kwarg at all, not a hardcoded default), plus a real
`FocusPreviewWindow` check that a fresh window's menu reflects whatever
preference is already on disk, the three presets are mutually exclusive,
choosing one persists it immediately and updates the status text/tooltip,
and choosing Default clears the preference entirely rather than saving a
placeholder value.

### Fixed `measure.py`'s stale tool-status text after a mark commits (BUILD_LIST Tier 1, item 2)

After a mark committed, `point_status` kept showing its pre-commit text —
a polygon commit still read "double-click to finish (3+ needed)" — because
`mousePressEvent`'s auto-commit path (distance/angle) and
`mouseDoubleClickEvent`'s commit path (polygon/ellipse) both called
`_clear_pending()` but never reset the status line, unlike `_cancel_pending()`
(right-click), which already did via `on_point_added([])`. Fixed with a new
`MeasureWindow._reset_tool_hint()` (the same text `_on_tool_toggled` already
shows when a tool is first picked — "ready for the next mark" should look
identical to "just picked this tool"), called from both commit sites and
refactored into `_on_tool_toggled` itself instead of its own inline
`setText` call.

New render-check coverage drives the real `mousePressEvent`/
`mouseDoubleClickEvent` handlers with synthetic `QMouseEvent`s against a
real loaded image and calibration (not a reimplementation of the fix),
covering both the auto-commit path and the double-click path. Tracked down
one real gotcha along the way, worth knowing if you add more UI-driving
render-check coverage here: the fixture image this reused (`green_path`
from the `load_measurement_plane` check earlier in the same function) gets
`unlink()`ed in that earlier check's own `finally:` block, so loading it
again later raised, and `_load_image`'s `except Exception` branch called
`QMessageBox.warning(...).exec_()` — a real modal dialog with nothing to
click it, which hangs a headless test forever rather than failing loudly.
Fixed by writing a fresh, self-contained fixture file instead of reusing
another check's already-cleaned-up path. If a render-check ever seems to
hang (not just fail) right after loading an image, check for exactly this.

### Added the z-stack one-click aid (BUILD_LIST Tier 3, item 6)

The feature the user actually asked for; `gallery.py` and `process_wizard.py`
(below) were built first because the build list gates this one on both.
Full design and file/line grounding in `HANDOFF.md`.

A new `zstack_btn` in `qt_shell.py`'s `FocusPreviewWindow`, mirroring
`_toggle_recording`'s own two-state pattern exactly: press "Start Z-Stack"
to begin (captures plane 0 immediately as part of starting), press "End
Z-Stack" to finish. The existing Capture button/menu action is repurposed
while a stack is active — each press captures and auto-tags the next plane
— rather than a second new button; this is the only reading of the build
list's own wording ("one button... each subsequent press... a distinct
action, same button again, mirroring Record") that makes "mirroring
Record" literally true, since Record itself is a pure two-state toggle.

Nested per-plane sessions under `~/captures/zstack_<timestamp>/plane_N/`
(a small, backward-compatible `Session.__init__(..., session_dir=None)`
extension), each tagged via `stacks.apply_tag` — the same two calls
`_on_tag_stack` already makes manually, just automatic. `capture_kind_
combo`/`record_btn` are disabled while a stack is active (mirrors Record's
own mutual exclusion). Ending the stack runs `stacks.validate_all` over the
plane folders, shows the result, then offers (never forces, matching
`_offer_process`'s own precedent) to hand off to `process_wizard.
ProcessWizard`, scoped to the stack's own root folder so its embedded
Gallery naturally shows — and pre-selects — only this stack's own planes,
with no changes needed in either `gallery.py` or `process_wizard.py`.

New `qt_shell.py --render-check` coverage drives the real button handlers
end to end (`_start_zstack`, two repurposed `_start_capture` presses,
`_end_zstack`) through the real worker thread and real queued cross-thread
signal — pumping `QApplication.processEvents()` rather than bypassing the
async path the way `_on_tag_stack`'s own test does, since this is exactly
the mechanism worth proving actually works. Covers: start/end guards
refusing mid-capture, plane 0 auto-captured on start, folder layout and
per-plane tagging, the process-offer's Yes/No gate (including that Yes
scopes the wizard to the stack's own root, never the global `OUT_ROOT`,
with every plane pre-selected), and a regression check that a plain
Capture press with no active stack is completely unaffected. Also manually
smoke-tested end to end under `QT_QPA_PLATFORM=offscreen`, clicking the
real buttons.

### Added `process_wizard.py`: choose-your-operations processing wizard

New module (BUILD_LIST Tier 3, item 5), built on top of `gallery.py`
(item 4, previous entry below) — the file-selection foundation it needed.
The next step after this is the z-stack one-click aid the user actually
wants, which will hand its finished planes off to this wizard.

A separate, additional path from the existing "Process session..."
(`ProcessSessionDialog` + `hdr_from_session.py`), kept exactly as it was —
that flow is right for a session's own recorded HDR bracket. This one is
for the more general case: any set of Gallery captures or loose files, each
run through the same pipeline shape (`frame_average.py`, always, even a
1-frame group — one uniform path, one honest provenance record, not a
special-cased pass-through — then one `debayer.py` call, `--green` for a
measurement plane or `--rgb --tonemap reinhard` for a display image, with
optional `--colour-gains`). Reuses `hdr_from_session.py`'s own `run_tool`
subprocess helper rather than reimplementing it, wrapped so a failed
`sys.exit` in one group becomes a recorded error instead of aborting the
rest of a batch. Output is named via `stacks.output_name()` when a group
came from a stack-tagged capture, `<label>_final.tif` otherwise. New "Process
files..." File menu action in `qt_shell.py`, distinct from "Process
session...".

Deliberately not built: HDR-merge grouping from arbitrary picked files —
the build list names it as a pipeline stage, but building a real grouping
UI (partition N files into exposure levels, enter each level's exposure
time) would mean a second, riskier way to do something the existing
session/kind path already does correctly for real HDR brackets, and
neither the z-stack aid nor "process some loose files" needs it. Flagged
as a deliberate cut, not an oversight.

`gallery.py` gained a small, additive extension for this: `GalleryEntry`
now carries `file_prefix` and `stack_id`/`stack_plane` (previously
collapsed into a display-only `stack_tag` string) instead of discarding
them, plus a new `capture_frame_paths(entry)` that resolves a capture's
**whole** burst — every frame_average.py needs, not just the frame 0 the
three existing single-image "Open..." callers only ever needed.
`GalleryWidget.selected_entries()` / `GalleryPickDialog.selected_entries()`
expose the full entries for callers (this wizard) that need that context;
the four existing call sites only ever used `selected_paths()`, so this
carries no risk of breaking them (confirmed, not assumed, before touching
the struct).

New `process_wizard.py --render-check`, run only after `gallery.py
--render-check` passed in isolation on the extended fixture first, per the
plan's own ordering (a foundation change gets proven before anything is
built on it, not folded into one pass at the end): real
`frame_average.py`/`debayer.py` subprocess round-trips in both green and
rgb mode, asserting the intermediate master genuinely carries
`frame_average.py`'s own provenance (including for the 1-frame case — not
a special-cased copy that happens to look right), correct output naming
for both a tagged and an untagged group, and a deliberately-broken group
reporting an error without aborting the rest of the batch. Manually
smoke-tested end to end under `QT_QPA_PLATFORM=offscreen`: wizard
construction, a real Gallery selection turned into groups, and a real
pipeline run producing the correctly-named output file.

### Added `gallery.py`: shared capture-browsing grid, pick and browse modes

New module (BUILD_LIST Tier 3, item 4), built as the next unblocked step
toward a z-stack one-click aid: the wizard it hands off to (item 5) needs
this first as its file-selection foundation, per the build list.

Thumbnails come from the JPG previews every real capture already writes
alongside its raw `.dng`/`.tif` — no raw decode to populate the grid, so
opening the gallery on a large captures tree is instant. One `GalleryWidget`
drives two Qt wrappers: `GalleryPickDialog` (multi-select-capable, an OK/
Cancel dialog with a "Choose file manually..." escape hatch back to a plain
file dialog) and `GalleryBrowseWindow` (no commit button, just looking).
`GalleryPickDialog` replaces the bare `QFileDialog.getOpenFileName` calls in
`wizard_pages.py`'s `ImageSourcePage._on_open_existing`, `measure.py`'s
`_on_open`, and `calibrate.py`'s `_on_open` — three sites, same swap, same
`_load_image`/`_try_validate` handling afterward. `qt_shell.py` gets a new
"Browse captures..." File menu action opening `GalleryBrowseWindow`.

Whether a capture already has annotations is intentionally a separate, lazy
check (`capture_has_annotation`), not part of the cheap listing: annotations
are keyed by the green-plane hash, never a display-referred one (`debayer.py`
tags its tonemapped output `"NOT a measurement"`, and `measure.py`'s
`check_measurement_provenance` refuses to measure on it), so answering this
honestly means decoding the raw mosaic via `measure.load_measurement_plane`
— the same substrate `measure.py` itself measures on — not hashing whatever
small file happens to already sit in the session folder. `GalleryWidget`
only ever runs this in a background `QThread`, filling in each tile's
"annotated" marker after the grid already shows the cheap data, so a big
folder tree is never gated on raw decodes nobody asked to see yet.

New `gallery.py --render-check`: `list_gallery_entries` reports the right
kind/timestamp/stack-tag with no raw decode performed; `capture_has_
annotation` correctly distinguishes a green plane whose hash was seeded
into a temp annotations store from an unannotated sibling. Also manually
smoke-tested under `QT_QPA_PLATFORM=offscreen`: both dialogs construct and
populate against a real temp captures tree with PyQt5.

### Focus aid: restored tick rate to ~30fps, auto-reset on stack-plane tag

Two changes to the live focus aid's state machine (`qt_shell.py`), per
`SPEC_focus_aid_fps_and_stack_reset.md`:

- `FocusPreviewWindow`'s `tick_ms` default was `100` (10fps) — a workaround
  for Wayfire's compositing overhead. Now that the project runs under
  Labwc (noticeably smoother per the user), that workaround no longer
  applies. Restored to `33` (~30fps): smooth enough per the user's own
  report, without burning extra CPU on lores-decode frequency for no
  visible gain. No other call site overrode the default, so this was a
  one-line change.
- `_on_tag_stack` now calls `self.meter.reset_field()` immediately after a
  **successful** tag (right after `self._session.write()`), so a z-stack
  session's per-plane refocus-and-confirm loop no longer needs a manual
  "Reset field (R)" between planes. Fires only on that one path — a
  refused tag (blank ID, `(stack, plane)` collision) or any other capture
  (plain snap, flat/science/HDR/dark, burst) leaves focus history alone,
  since none of those call `reset_field()`.

New `--render-check` coverage bundled into the existing PyQt5-gated
`_on_tag_stack` check: a successful tag triggers exactly one
`reset_field()` call; a blank-ID refusal, a collision refusal, and an
untagged capture each trigger zero.

### Refreshed README.md
`cd6e566`

Brought the README in line with everything that had shipped but was never
reflected in it: removed references to `zstack_process.py` and the
standalone `capture.py` (both deleted earlier — see below), added
`ca_measure.py`'s CA calibration and its own supersedes-chained store, the
`qt_shell.py` Calibrate/Measure menu integration, z-stack review and
post-capture QC in `measure.py`, export/publish, the "evidence never a
gate" design rule, and a new data-locations table. Known limitations now
note the CA wizard isn't yet reachable from `qt_shell.py`'s own menu
(separate-camera-instance conflict, unresolved) and the deferred poly2 CA
model.

### Added `HANDOFF.md` and this changelog
`d5e44cd`

First versions of both docs, covering everything through the Measure-menu
entry below. **Going forward, both are updated after every action in a
session, not just at a session's end** — so picking this project up
mid-session, not just between sessions, should still find both current.

### Added `qt_shell.py`'s Measure menu
`ab21eb1`

A "Measure" menu, mirroring the existing "Calibrate" menu exactly: guarded
import of `measure.py`, one action ("Measure...") disabled with a tooltip
if unavailable, and `_launch_measure()` opening `measure.MeasureWindow` as
a separate window — pre-filled from the ruler's own objective combo,
reusing an already-open window rather than duplicating it on a repeat
trigger. `measure.py` never touches the camera, so no resource conflict
with the live preview.

Verified with a live `QApplication`: menu action renders and is enabled,
the real window opens with the correct pre-filled objective and shows the
real calibration status pulled from the live store (confirmed via
screenshot), and re-triggering raises the existing window instead of
spawning a second one. New permanent `--render-check` coverage in
`qt_shell.py` (bundled with the existing PyQt5-gated checks).

## 2026-07-20

### Build checklist §13: objective/config-change invalidation
`e4de59a`

Every spatial and CA calibration entry now records its own reduction lens
and CFA/green-which config. `calibrate.calibration_staleness(entry)`
compares an entry's recorded config against the live rig config and
returns human-readable mismatch reasons (empty = fresh) — evidence only,
same "recorded honestly, never a gate" rule as `poly2_flag`: a stale
calibration still works, a human decides whether to re-measure. Wired into
all three "current calibration" status displays (`CalibrationWindow`, both
wizards' setup pages) and `measure.py`'s tool-gating status, all via one
shared `format_staleness_suffix()` so the wording is identical everywhere.

`ca_measure.py`'s `build_ca_calibration_entry` now records
`reduction_lens`/`cfa_pattern` (no `measurement_plane` nesting, no
`green_which` — CA operates on demosaiced RGB, not a green sub-plane), so
the same staleness check reads a CA entry identically to a spatial one,
with no special-casing.

Verified against this rig's **real, already-calibrated store**
(4×/10×/40×/100×): all four currently read as fresh, and a simulated
reduction-lens drift correctly flags all four with the exact right reason.
Also verified the real `CalibrationWindow` GUI against a copy of the real
store with one entry deliberately drifted — only that entry flags, the
live store file was never touched.

### Build checklist §13: post-capture QC (sharpness score + exclude toggle)
`76039ac`

- `focus.score_capture_sharpness()`: the same variance-of-Laplacian metric
  the live focus aid uses, computed once on an actual captured green frame
  instead of the live lores stream (no smoothing, no bar — meaningless for
  a single static post-capture number). Converted `focus.py`'s bare demo
  script to the project's `--render-check` dispatch (it never had one).
- `stacks.py` gained `find_tagged()` (locate a capture regardless of
  active/excluded status — unlike `find_holder`, which is deliberately
  active-only for retake-collision checks), `set_exclude()`, and
  `sharpness_relative_flag()` (whether a plane is soft relative to its own
  stack's best — evidence only). First-ever `--render-check` for this file.
- `qt_shell.py`: scoring hooked into the science-capture path only (flat/
  dark are calibration frames, never stack planes); a scoring failure
  records `None` rather than raising into the capture flow.
- `measure.py`: `collect_stack_planes` now surfaces excluded planes
  (marked, not hidden) so a human can actually see and reverse a cut —
  the filmstrip shows each plane's score, a softness flag, and an
  Include/Exclude toggle that writes straight to that plane's own
  `session.json`.

Verified on the real IMX477 beyond render-check: shot 3 frames, scored
them for real, tagged them via the real GUI action, loaded the stack
through the real filmstrip, and toggled exclude on a real plane in both
directions — confirmed each change round-trips through the actual
`session.json` and is visible to a fresh directory scan.

### Added a "Tag as stack plane" GUI action
`8ab840c`

Found while testing §8's z-stack view against real hardware: there was no
way to tag a capture into a z-stack from the GUI at all — `stacks.py` was
never imported by `qt_shell.py`, and the old `capture.py` CLI's
`tag <stack> <plane>` command was never ported over when its logic got
baked into `qt_shell.py` (see the bake-in entry below). Added a
"Capture → Tag as stack plane..." menu action: tags the session's most
recent science capture, refuses blank IDs and `(stack, plane)` collisions
cleanly, auto-increments the plane default across successive tags in one
sitting, persists immediately. New permanent `--render-check` coverage
(PyQt5-gated, since it needs a real window).

Verified end-to-end on real hardware: shot a real 3-plane stack, tagged
each plane through this exact new action (not `stacks.apply_tag` called
directly), then loaded the result through `measure.py`'s real z-stack view
— 3 real planes, 3 distinct `pixel_sha256` values, confirming the whole
capture-to-measurement chain works without a Python console in the middle.

### Review pass: fixed six real defects
`7bc204b`

A self-review of the session's own prior work (prompted by "any luck on
the test?" / "run the test suite"), catching real bugs before they shipped
further:

1. **`qt_shell.py`'s `build_display_flags`** had drifted from the original
   `capture.py` semantics it was supposed to preserve: `--ca` needed
   absolutising (the processor runs inside the session dir, where a
   relative path breaks), `--archive-raws` had been dropped, and
   `--sharpen` used truthiness instead of `is not None` (silently
   swallowing an explicit `--sharpen 0`).
2. **`measure.py`'s z-stack `_load_stack` was unreachable and broken**: it
   was never called by anything, read a `"base"` key no capture entry has
   ever had, and assembled a stack from one session's captures when the
   real model is one-session-one-plane. Rebuilt on `stacks.py`'s actual
   API (`group_by_stack` + `ordered_planes`), with a new "Open stack..."
   button and stack picker.
3. **The filmstrip didn't actually work**: thumbnails were unscaled
   full-res pixmaps used as icons, inactive-plane dimming used
   `opacity: 0.5` in a stylesheet (not a real Qt property — silently
   ignored), and the active highlight never moved off plane 0.
4. **The publish button was a stub** ("coming soon" dialog) with two
   latent crashes (formatting a `None` µm/px, slicing a `None` hash). Now
   genuinely writes `green_plane.tif` + calls `publish_measurements`.
5. **`publish.py`'s own package was internally inconsistent**: the
   manifest counted one image's marks while `results.json` dumped the
   *entire* annotation store. Fixed by slicing the store to the published
   image's own hash before export.
6. **`ca_measure.py`'s review-page plot leaked one temp dir per render** —
   now reuses a single per-page directory across Back→redo→Next cycles.

All fixes verified via the full `--render-check` suite plus an offscreen
Qt smoke test of the z-stack/onion-skin path.

### Baked `capture.py`'s session/profile logic into `qt_shell.py`
`fb26a8e`

`capture.py` had been deleted earlier in the session (see below) but its
`Session`/`load_profile`/`save_profile`/`new_session_dir` layer is generic
workflow code (session folders, metadata sidecars, profile persistence),
not IMX477-specific — it belongs with the GUI that's its only remaining
caller, not with `camera_backend.py` (reserved for genuinely
sensor-specific code). `wizard_pages.py`'s `new_adhoc_dir` now calls
`qt_shell.new_session_dir` via a **lazy** import (see `HANDOFF.md`'s
circular-import note for why this can't be a top-level import). Removed
the now-dead "capture.py not importable" fallback branches this created —
every check `qt_shell.py`'s own `render_check` gates on `Session` now runs
unconditionally, since `Session` can no longer be `None`.

### Build checklist §12: Publication packages
`c671157`

`publish.py`: `create_publication_manifest()` documents the reproducibility
chain (green plane hash → calibration → results); `publish_measurements()`
assembles the package directory (`green_plane.tif` + `results.json` +
`manifest.json`). Display derivatives are explicitly marked
`kind="display"`/`"NOT a measurement"`, sourced back to the green hash.

### Build checklist §11: Export
`40974ef`

`export.py`: flattens the central annotation store into a flat JSON
results view, one record per measurement, each carrying its
`pixel_sha256`, exact `calibration_ref` (objective + entry_id + um_per_px
snapshot), mark type/coordinates, and computed values.

### Build checklist §8: Z-stack view
`b6ffc78` (tolerance follow-up: `938fbff`)

`measure.py` gained a filmstrip widget (thumbnails, active lit/inactive
dimmed) and an onion-skin toggle (faint neighbor planes composited behind
the active one). Marks bind to the active plane's own `pixel_sha256` only
— ghosted neighbors are display, never the measurement. (This initial
version of `_load_stack` had real bugs, caught and fixed in the later
review-pass entry above.)

### Build checklist §4 (CA half): CA calibration wizard
`d8fcabb`

Refactored `ca_measure.py`'s inline CLI math into pure, reusable functions
(`fit_lateral_ca`, `format_offset_table`, `render_ca_plot`), added
`poly2_flag()` (evidence-only detection of outer-annulus curvature — no
correction model built, deferred pending real evidence), a central
supersedes-chained CA calibration store mirroring `calibrate.py`'s own,
and a 3-page `CAWizard` (setup → shared image-source page → review with
poly2 flag + export). CLI behavior (stdout, `-o` JSON, `--plot` PNG)
unchanged throughout the refactor.

### Removed `zynergy-imaging/` and `capture.py`
`1b93c68`, `94765b7`, `34c7086` (+ upstream duplicate cleanup in `6362eae`,
`9eaf8b0`)

Removed at the user's request as unnecessary. `capture.py`'s useful logic
was later baked into `qt_shell.py` (see above) rather than lost outright.

### Added paged setup wizards to `calibrate.py` and `measure.py`
`d39fbf5`

Build checklist §4: onboarding/redo wizards for spatial calibration and
measurement, sharing `wizard_pages.ImageSourcePage` (pick an existing
image or shoot a new one with a live focus box/bar) across both tools.
Includes a fix for a `QGlPicamera2` teardown race (a background thread
could read a closed fd after `camera.stop()`) — `_CapturePane.stop()` now
calls the widget's own `cleanup()` explicitly, idempotently, before
detaching it.

### Removed WebUI
`0c1db15`

Kept as local-only convenience files, out of the tracked repo.

---

*Earlier history (initial commit `c488168`): the original Zynergy imaging
pipeline and measurement GUI — camera backend, debayer/HDR/frame-averaging
processing chain, annotation store, pixel hash, spatial calibration, and
the first version of the measurement GUI. Not itemized here; see
`git log c488168` for that baseline if needed.*
