# Handoff

For whichever agent picks this project up next. Read this before touching
anything — several things here are non-obvious and have already cost real
debugging time once.

**This file and `CHANGELOG.md` are kept current after every action, not
just at the end of a session** — so if you're resuming mid-session rather
than between sessions, both should still be accurate as of the last thing
that happened. If you add, fix, or change something meaningful, update the
relevant section here and add a `CHANGELOG.md` entry as part of that same
piece of work, not as an afterthought at the end.

## Open right now

`task9-work` has landed — its push was blocked in-session by the auto-mode
permission classifier (see `CHANGELOG.md`'s "Open: task9-work fast-forward
to main, blocked on push permission" entry for that history), the user ran
it directly, and `origin/main`'s tip is now that commit. Closed, not open;
left in for anyone who reads this expecting it to still be pending.

What's actually open, none of it written down anywhere until now:

1. **`frame_average.py`'s saturation-rejection question went from
   theoretical to testable this session.** The investigation (`CHANGELOG.md`'s
   2026-08-03 entries; summarized above under "frame_average.py
   capture-metadata sidecar wiring") already established that the default
   averaging path sums saturated samples unconditionally with no rejection.
   What changed: 160 pre-average raws from the August 3 bracket turned out
   to have survived rather than been discarded, so whether the level-5
   raws actually hard-clip at a single value can now be checked directly
   against real data instead of only reasoned about from the merged
   masters. Not yet checked; whether/how to build saturation rejection is
   still the user's sequencing decision to make (see item 2 in that same
   backlog list above). **Still not checked as of the 2026-08-05 session
   that closed item 7 below** — that session also ran directly on the Pi
   (confirmed via `hostname`, same access as the session that opened this
   item), but stopped at the item-7 commit (`ddb845c`) on the user's
   explicit instruction, not because Pi access was unavailable. Whoever
   picks this up next: access isn't the blocker, sequencing/scope is —
   this is the one item in this list suited to a Pi-connected session
   specifically (real bracket data), the other open items below are not.
2. **Closed (gallery-race staging design).** `correction_status`
   (`raw_discarded`/`raw_discard_reason`/the derived-outputs fields) is
   built strictly after the retention-deletion loop, inside the one
   `process()` function `hdr_from_session.py` shares between every
   caller — the auto-processed staging path (`_auto_process`, snap/
   science/hdr) and the unstaged manual reprocessing wizard/archive
   dialog alike. Deliberately unconditional on staging, not a
   staging-only branch: retention-before-embed holds for every caller,
   so this item closes for all of them, not just a session's first
   capture. Full reasoning, including why the ordering must not live
   inside a staging-specific branch: `CHANGELOG.md`'s 2026-08-05
   "gallery-race staging design" entries. Same session also built the
   staging design itself — capture+processing for snap/science/hdr now
   write into a same-device staging directory
   (`Path.home()/"staging"/<session_ts>`, `provenance.new_staging_dir`)
   and publish the finished set into the session directory one file at a
   time via `os.replace`, not a single directory-level rename (a
   directory rename only succeeds against an empty destination, which
   does not hold once a session holds more than one auto-processed
   capture — the common case, since `self._session` is never reset).
   `dark`/`flat` capture call sites are untouched, never staged.
   `--render-check` clean (exit 0) and both retention paths (Keep RAW
   Images on/off) verified on real `Picamera2Camera` hardware, embed
   matching directory contents in both cases — one real bug (`final.tif`,
   debayer.py's always-written primary output, was never in the publish
   list) found by the on-rig run and fixed; render-check alone would
   never have caught it. Full detail, including two verification-script
   failures along the way (neither in this task's own code): `CHANGELOG.
   md`'s 2026-08-05 "Record build: gallery-race staging design" entry.
3. **Gallery race guard** — parked, pending the user's decision. The gap
   itself is already documented (see "Keep RAW Images narrowed to raws
   only" below): nothing guards a separately-launched `process_wizard.py`
   or the Gallery view from reading a capture's files concurrently with,
   or right after, an auto-process worker thread's deletion. `claude/
   gallery-race-comment-fix` states this as a contract in a `# CAVEAT:`
   comment (see "Gallery race comment corrected to a stated contract"
   below); it does not add the guard itself — that's still this open item.
4. **Stage 3** — hasn't moved. Named in conversation, not detailed here;
   don't assume it's the same thing as the "Part 03" Preferences-dialog
   plan set covered later in this file unless confirmed.
5. **The `--render-check` verification gap from PRs #10/#11/#12 is
   closed.** This session runs directly on the Pi (`hostname` ==
   `raspberrypi`, real `numpy`, real `ssh`) — the first session on this
   repo with that access. `git pull --ff-only` fast-forwarded to
   `95fce3e`, then `python3 qt_shell.py --render-check` ran for the
   first time ever against a merged state: **exit 0, every assertion
   PASS**, including the PR #9-fixed Keep RAW Images block. Full detail:
   `CHANGELOG.md`'s 2026-08-05 "Record: tenth task Part 1" entry.
6. **Check-enumeration report (Part 2) done, nothing fixed.** All 15
   `render_check()` files plus `camera_backend.py`, `imx477.py`,
   `pixel_hash.py` were read in full and actually run on the Pi (not
   just read). Findings, in full, in `CHANGELOG.md`'s 2026-08-05
   "Record: tenth task Part 2" entry — summary: expected-value
   provenance is external-contract for the large majority; a handful of
   lower-stakes internal-threshold checks exist (`calibrate.py`
   `stretch_to_uint8`, `ca_measure.py` `format_offset_table`/
   `poly2_flag`, `measure.py`'s use of `stacks.py`'s `rel_drop=0.5`) but
   none reproduce the Keep-RAW-Images shape exactly; three checks cite
   planning docs (`PLAN_04_green_plane_cache.md`, `PLAN_quick_ruler.md`,
   `PRIORITY_click_mapping_fix.md`) that don't exist anywhere in the
   repo or its git history — unverifiable citations, not known errors;
   several real coverage gaps identified (`ca_lib.py` has no self-check
   at all; `stacks.py`'s `move_frames_to_discarded` isn't exercised by
   any check; `provenance.py` never confirms a recorded path resolves to
   the file it describes).
7. **Closed, 2026-08-05 (tenth task Part 4).** `function_index.py
   --render-check` had been failing on `main` since PR #10/#11/#12 added
   functions to `frame_average.py`/`hdr_merge.py` and a `CAVEAT:`
   comment to `qt_shell.py` (item 3 above) without anyone re-running
   `python3 function_index.py` afterward. Regenerated and re-verified
   (`assert_function_index_current` PASS); `qt_shell.py --render-check`
   independently re-run and confirmed still exit 0. `SWEEP_CHECKS.md`'s
   sensor-sanity section now names the trigger this should run on ("any
   PR that adds/removes/changes a function's signature regenerates
   `FUNCTION_INDEX.md` in that same PR") — enforcing that trigger
   automatically (hook/CI, not memory) is still a gap, not built here.
8. **Part 3 (standardized sweep-check list) closed.** `SWEEP_CHECKS.md`
   added at repo root — five sections (measurement correctness,
   provenance integrity, geometry derivation, retention safety, sensor
   sanity), each entry with what it checks, its contract source (or
   "gap"/"unverifiable citation" if it doesn't have one), and where it
   can run. Read this file first, before composing a check on the spot,
   for anything touching those five areas — that is the whole reason it
   exists. It cross-references item 7 above (`function_index.py`) as its
   own live example of what happens when a check isn't. Full
   intent/build/record-build detail: `CHANGELOG.md`'s 2026-08-05 "tenth
   task Part 3" entries.
8a. **`session.json` correction-status field loss on a second capture in
    one session — found verifying the gallery-race staging design's
    multi-capture publish case, not fixed, documentation only.**
    Symptom: a second capture in one session strips ALL SIX fields
    `_record_correction_status`'s own `cap.update(correction_status)`
    writes from the first capture's entry in `session.json` —
    `flat_correction`, `dark_correction`, `raw_discarded`,
    `derived_outputs_discarded`, `derived_outputs_note`, and (present
    only when `raw_discarded` is true) `raw_discard_reason`. Not just the
    three that happened to be visible in the observed case — all six go
    the same way, since one `cap.update()` call writes them together.
    The first capture's raw files remain present and untouched on disk —
    only the record of them is gone. Mechanism: `_record_correction_
    status` reads `session.json` fresh from disk and patches it in
    place, by design, so it also serves the manual processing wizard's
    non-live sessions (its own docstring says so explicitly).
    `Session.record()`, called by the second capture's `record_capture`,
    appends to `self.captures` in memory — which never learned about
    that disk-side patch — then calls `Session.write()`, which overwrites
    the whole file from that stale in-memory list. Scope: pre-existing,
    untouched by the staging work (none of the three functions below was
    touched by it), fires on the ordinary two-Snap workflow with no
    staging involved at all. Observed in session `2026-08-05_163014`.
    Ranked above item 9: this is silent loss from the provenance record
    itself, not a UI-level drop — `final.tif` still carries its own
    embed (see item 8b below), so once this fires, the TIFF states a
    retention outcome that `session.json` neither corroborates nor
    contradicts for that earlier capture.

    **Line numbers resolve only against this branch
    (`claude/gallery-race-staging-design`) — the staging work shifted
    all three below it, `Session.write` and `_record_correction_status`
    included. Whoever picks this up must know which base they are
    patching against:**

    | Function | This branch (`858260d`) | `main` (`1a2eb45`) |
    |---|---|---|
    | `_record_correction_status` | `qt_shell.py:5584-5608` | `qt_shell.py:5529-5551` |
    | `Session.write` | `provenance.py:269-285` | `provenance.py:219-234` |
    | `Session.record` | `provenance.py:321-328` | `provenance.py:321-328` (coincides on both — offsetting shifts elsewhere in the file, not a signal that this function is unaffected) |

    **A second disk-patch writer exists with the identical clobber
    mechanism: `measure.py`'s `_on_exclude_toggled`.** It reads
    `session.json` fresh from disk and patches one field (`stacks.
    set_exclude`'s exclude flag on a z-stack plane's capture entry) in
    place, by its own docstring explicitly never depending on
    `qt_shell.Session` — same shape as `_record_correction_status`,
    same vulnerability to a later live-Session `write()` that never
    learned of the patch. This is reasoned from the mechanism, not
    reproduced: the z-stack review flow this runs under typically starts
    after a stack's per-plane `Session` objects have already gone out of
    scope, so the realistic collision here looks more like a cross-
    process race (a second window or process still holding that
    directory's `Session` live while `measure.py` patches it) than the
    same-process, same-object sequence observed for the two-Snap case
    above. Not confirmed on the rig or otherwise.
8c. **Design, not scheduled: conflict-detecting `session.json` write —
    the structural fix item 8a's investigation converged on, not
    committed to.** Shape: `Session.write` fingerprints what this object
    last wrote (a hash or mtime of its own prior write); before writing
    again, it compares that fingerprint against what is actually on disk
    now; on a mismatch, it re-reads, re-applies only THIS write's own
    delta against the current disk content, and raises only when that
    delta actually touches a field that changed underneath it (i.e., a
    genuine conflict, not just "disk moved since I last looked"). Why it
    is worth having: it is the only shape under consideration that does
    not depend on enumerating every present and future disk-patch writer
    correctly — `_record_correction_status` and `_on_exclude_toggled`
    today, anything written tomorrow, all covered the same way, because
    the protection lives in the one function everything ultimately
    writes through, not in each caller separately. What blocks it: no
    decided story for what a caller does with the raise. A raise inside
    a capture path is worse than the defect it fixes, because losing a
    whole capture entry (a failed `record()`) outweighs losing a
    correction field (today's actual defect) — that decision, not the
    fingerprinting mechanism itself, is the gate. Note: the narrower,
    cheaper fix considered alongside this one (`_record_correction_
    status` also updating a live in-memory `Session` when one exists for
    the directory being patched) would narrow how often this ever
    triggers, not remove the need for it — it covers only the one known
    call site, in this one process, and does nothing for
    `_on_exclude_toggled` or any writer not yet invented.
8b. **Derived outputs are not per-capture — found in the same
    verification, not fixed, documentation only.** Raw frames are
    indexed per capture (`snap_frame_0000.dng`, `snap_frame_0001.dng`,
    ...), but `final.tif`, `single_master.tif`, and `final_display.*`
    are rewritten in place under fixed names every time a capture is
    processed. A session with N processed captures holds N sets of raws
    but exactly one set of masters/display images, belonging to the most
    recently processed capture, with nothing in the filenames stating
    which capture that is. Pre-existing (fixed output names predate the
    staging work; per-file publish just moves the same fixed names,
    unchanged). Evidence: session `2026-08-05_163014`'s `final.tif` went
    from 25,334,219 bytes at 16:30:28 (after Snap #1) to 25,480,015 bytes
    at 16:30:50 (after Snap #2) — same path, rewritten, not renamed.
9. **Gallery pick-mode silently drops in-progress entries — found during
   the gallery-race staging design work (item 2 above), not fixed, out
   of scope for that task by explicit instruction.** `GalleryWidget`
   lists once at construction (`gallery.py:334`, the only call site of
   `.refresh()` anywhere in the repo) and never refreshes — an already-
   open gallery is a snapshot, not a live view. `GalleryPickDialog`'s
   `selected_paths()` (`gallery.py`) silently filters out any entry whose
   `raw_path` is `None` before returning the selection — so a user who
   picks a tile for a capture whose raw isn't on disk yet (or, for a
   fully-processed-and-retention-discarded capture, ever) and clicks OK
   gets an empty selection with no message, not an error, not a
   "not ready yet." Pre-existing, not introduced by staging — but the
   staging design widens the window in which it's reachable: a capture
   now lists in the gallery (`session.json` gains its capture entry,
   `qt_shell.py`'s `record_capture`) well before its raw frame is
   published into the session directory, whereas before staging that gap
   was only the length of one subprocess call. Needs its own decision
   (an error message on empty-selection OK? disable/gray those tiles?
   the gallery race guard in item 3 above, once built, may be the more
   natural place to fix this from) — not guessed at here.

One number worth a line since the constant alone doesn't explain it:
`hdr_from_session.MERGE_WHITE_LEVEL_DEFAULT` stays `65520`, but the real
August 2026 bracket was actually merged at `--white-level 62100` — the
measured ceiling (~61000, reproduced on a second, older bracket) plus a
margin, landing the cutoff below where the frame5/frame4 ratio starts
departing from 2.00 rather than at it, since that departure is gradual,
not a step (full reasoning: `hdr_from_session.py:41-61`'s comment).

## What this project is

A microscopy capture + calibration + measurement suite for a Raspberry Pi 5
with an IMX477 HQ camera. See `README.md` for the architecture map and the
measurement-integrity invariants (green-plane-only measurement, append-only
calibration, hash-pinned marks) — those are load-bearing design rules, not
suggestions, and nothing in this handoff repeats them. See `PHILOSOPHY.md`
for durable rules about *how* things get verified here (as opposed to what
currently is or was built) — in particular, its rule on what a self-check
actually has to prove before it counts as verification.

## PyQt6 (the UI layer runs on PyQt6, not PyQt5)

The port from PyQt5 to PyQt6 is complete, merged into `main` directly (not
a standing separate branch — `port/pyqt6` no longer exists), and confirmed
on-rig on 2026-08-01, including the `camera_backend.py` binding-selection
fix that had to land alongside it (`QGl6Picamera2`, now also a `# CAVEAT:`
comment at `camera_backend.py:769` and a `FUNCTION_INDEX.md` entry — see
`CHANGELOG.md`'s "generated per-module function index" entries for that
mechanism). Full history — the five things the port actually changed, the
structural Qt-attribute verification, the on-rig deep-verification
readings, and the binding-fix root cause — is in `CHANGELOG.md`'s "PyQt5
to PyQt6 port" (2026-07-29) and "picamera2 Qt binding selection"
(2026-08-01) entries. Two things from that work stay live enough to
repeat here rather than leave buried in a changelog entry:

**Qt6 flag types don't compare equal to raw ints, inconsistently.**
`Qt.MouseButton.LeftButton == 1` is False and
`Qt.KeyboardModifier.NoModifier == 0` is False — both are flag types now,
not int enums. `Qt.Key.Key_Escape == 16777216` is still True, and
`QEvent.Type`/`QDialog.DialogCode`/`QMessageBox.StandardButton` all still
compare equal to their old ints, so the inconsistency itself is the trap.
Every comparison site in the tree was checked at port time and compares
enum to enum; write `if ev.modifiers() == 0:` in new code and you'll get
a silently false branch and no error.

**`QMouseEvent.pos()` is deprecated but deliberately not yet replaced.**
Qt6's `position().x()` returns `float` where Qt5's `.x()` returned `int`,
and `native_point_from_preview_click`/`widget_to_native` both do float
arithmetic, so a float input wouldn't raise — it would silently shift
every click by up to a pixel, a real change to measured values at the
current 4x calibration (1.4084 um/px). `pos()` still returns `QPoint` in
6.11.0 and was kept deliberately; a stage-micrometer check on-rig
(2026-08-01) found sub-0.2px mean error consistent with an int read, not
a truncation offset — evidence for staying on `pos()` for now. When
`pos()` is eventually removed and this has to move to `position()`,
budget a rig re-check, not just a code review.

**Known problems, not fixed by the port and not fixed since** (this is
their canonical location — `CHANGELOG.md` points back here rather than
repeating them):

- `GREEN_PLANE_RES` and `FULL_RES` duplicated across `measure.py`,
  `qt_shell.py`, `gallery.py`, `calibrate.py`
- The live bug at `qt_shell.py:3452` passing the module `GREEN_PLANE_RES`
  constant instead of the camera's own configured size
- The green-plane loader hardcoding `(3040, 4056)` and `(1520, 2028)`
  instead of deriving shapes from the sensor profile
- Missing mono / no-CFA path
- BGGR assumed as the only CFA pattern in `calibrate.py` and
  `ca_measure.py`
- `FULL_MODE_LBL` hardcoded into every `session.json` provenance record
- Open `G_IS_OBJECT` assertion at teardown
- Extracting capture logic out of `qt_shell.py`
- `provenance.py` phase 2 (store-mechanics migration) — see "Current
  state" below for what's actually landed

Line numbers above haven't been re-verified since the port — check before
trusting one.

**Also found during the 2026-08-01 on-rig bench, still open, not caused
by the port** (full readings: `CHANGELOG.md`'s "Record on-rig
confirmation: PyQt5 to PyQt6 port" entry):

1. ROI box jumps inward slightly on resize before moving in the dragged
   direction. Reproduced on pre-port PyQt5 too — pre-existing.
2. Focus aid rebases onto the plane just captured during a Z-stack, so it
   must be reset manually to find the next plane. Pre-existing.
3. Under raised `Xft.dpi` the GL preview viewport doesn't follow a widget
   resize — old size, uncleared framebuffer around it. Not A/B'd against
   pre-port PyQt5, not confirmed pre-existing either way.
4. Possible field-scale gradient at 4x (~1.8 um left to right across the
   field). Suggestive (n=2 per position), not established. Discriminating
   test on record: rotate the slide 180° and re-measure left/right — if
   the gradient follows the slide it's the slide, if it stays with the
   field it's optics or sensor.

One more thing to watch, not on either list, since it comes from Qt
itself and hasn't been characterized on the rig: Qt6 turns high-DPI
scaling on unconditionally (the tree never set `AA_EnableHighDpiScaling`/
`AA_UseHighDpiPixmaps`, so there was nothing to remove). Given this
project's history with compositor output scale on HDMI-A-1, worth a look
if display sizing looks off.

## Current state (as of this handoff)

The build checklist referenced throughout commit messages and code comments
has 12 of 13 sections complete:

| § | What | Status |
|---|------|--------|
| 0–7, 9, 10 | Invariants, seams, pixel hash, stores, wizards, capture GUI, focus aid, canvas/tools, annotations, CA display | ✅ done |
| 8 | Z-stack view (filmstrip + onion-skin) | ✅ done |
| 11 | Export (flat JSON results) | ✅ done |
| 12 | Publication (provenance manifests) | ✅ done |
| 13 | Later items: **post-capture QC** ✅, **objective/config-change invalidation** ✅, **poly2 CA model** ⏳ deferred |

The one open item is the poly2 chromatic-aberration model
(`m(r) = 1 + c1·r² + c2·r⁴`). It was explicitly deferred — `poly2_flag()`
already detects when a CA fit's outer annulus curves away from the fitted
line, but no actual target has shown that curvature yet, and building the
correction model without real evidence to validate against would be
speculative. Don't build it until someone hits it.

That 13-section checklist is a separate, older track from the newer
`BUILD_LIST.md` (planning doc, not checked into the repo) the user is now
working through in dependency order. Progress so far: Tier 1 items 1
(focus aid tick rate + auto-reset on stack tag), 2 (`measure.py`'s stale
tool-status text after a mark commits), 4 (single green-plane extraction
utility — "Extract green plane..." File menu action, wraps `debayer.py
--green` as a subprocess, own `DEBAYER_TOOL`/`default_green_output_path`),
5 (video resolution menu — see the note below; a persisted next-launch
preference, not a live change, and the build list undersold how
non-trivial "wire it up" turned out to be), and 3 (themes — see the note
below; an open-ended, scanned-not-hardcoded system, not a fixed
Dark/Light pair) are all done. That's every Tier 1 item. Tier 3 item 4
(Gallery module) is done — see `gallery.py` below. Tier 3 item 5
(processing wizard overhaul) is done — see `process_wizard.py` below.
Tier 3 item 6 (the z-stack one-click aid, the thing the user actually
asked for) is also done. **Tier 2 (full screen mode with a floating
panel) is now also done** — see the note below. **`provenance.py`
extraction, phase 1 (Tier 3 item 1), is now done**: `OUT_ROOT`,
`PROFILE_PATH`, `load_profile`/`save_profile`, `_dump_meta`,
`new_session_dir`/`new_zstack_root_dir`, `Session`, `record_capture`/
`record_burst`/`record_hdr` all live in `provenance.py`; `qt_shell.py`,
`gallery.py`, `wizard_pages.py`, and `process_wizard.py` all reference
them as `provenance.X` (never a `from provenance import X` — see
`provenance.py`'s own comment on why). All five modules' own
`--render-check` pass. **`provenance.py` phase 2 (store-mechanics
migration for `calibrate.py`/`ca_measure.py`/`annotations.py`) has its
intent recorded but is not yet built** — see this file's own
"Store-mechanics migration" section further down for the design (a new
leaf module, `json_store.py`) and the decided migration order. Both Tier 0 investigations are also now done (see their own
note below) — the second one (CA wizard's live-capture path still
building its own independent camera) is what still gates the Measure-menu
reorg (item 3). **Casual Mode (Tier 3 item 2) is now done** — see the
dedicated section below for the full design and what actually landed;
it depended on `provenance.py` phase 1 above (that's the reason phase 1
was pulled out first) and was built in the order `PLAN_casual_mode.md`
laid out: preference/menu plumbing, then the module skeleton with its
own import-boundary self-check, then single-shot capture, then format
selection, then Burst/HDR. All landed together in one `casual_mode.py`,
each with its own `--render-check` coverage; self-check-verified only —
see the section below for exactly what that does and does not cover.

**A new plan set (2026-07-24) supersedes Casual Mode in full.** See
`PLAN_00_context_and_supersession.md` through `PLAN_05_live_measure_panel.md`
(drafted, not checked into the repo). The design: one application, one window, one layout — every
feature always present, nothing gated by a mode. Provenance moves to
`~/provenance/<timestamp>/` rather than becoming conditional; the only
setting that changes what gets kept is Keep RAW Images. `casual_mode.py`
is superseded in full and **is now deleted** (Part 03, below) — its
capture-and-save logic, format handling, and JPG-first delivery are all
lifted into `qt_shell.py`'s own main capture path. Its `qt_shell.py`
plumbing (`CASUAL_MODE_DEFAULT`, the `"casual_mode"` gui_prefs key, the
Options > Casual Mode action, `main()`'s window-class branch) went first,
as part of Part 01 below. **Parts 01, 02, and 03 are all now done** —
`Camera
Backend.get_capabilities()` is a new abstract method, implemented on both
`FakeCamera` (a small synthetic set; pass `stream_caps=True` at
construction to exercise the stream-key-present rendering path, since the
real driver can't yet) and `Picamera2Camera` (from `sensor_modes`,
translated to plain dicts/lists/strings — never `sensor_modes`' own
`"format"` field, which is a libcamera `PixelFormat` object; `"unpacked"`
is the plain-string field used instead). `video_resolutions` reuses the
same sensor-mode sizes as `capture_resolutions`: Picamera2's main stream
can technically scale to an arbitrary size via the ISP, but there's no
discrete "supported list" for that the way `sensor_modes` gives one for
capture, so the sensor-mode sizes are what's offered — real hardware
information, not a fabricated range. `stream_formats`/
`stream_resolutions` are omitted entirely on `Picamera2Camera` (absent,
not empty): no stream server exists in this backend yet. **`sensor_modes`
enumeration now hardware-verified (2026-07-24, on-rig session):** the real
IMX477's `Picamera2().sensor_modes` was read directly (no Qt/GUI layer
involved) and `get_capabilities()`'s exact size/format translation logic
run against it — 5 discrete sizes ((1332,990) through (4056,3040)) and 3
formats (SRGGB8/10/12) came back, and `"format"` was confirmed to really
be a non-plain PixelFormat-like object (`SRGGB10_CSI2P` etc.) on real
hardware, not just a theoretical risk — `"unpacked"` is genuinely the
right field. **Still open**: calling `get_capabilities()` through the
full `Picamera2Camera` class (which embeds a `QGlPicamera2` GL preview
widget at construction) — that failed on this rig with an EGL
`EGL_BAD_ALLOC` on `eglCreateWindowSurface` (no other process held the
camera at the time), so the class-construction path through the GUI
stack is a separate, still-unverified gap, unrelated to the capability
query logic itself. `FakeCamera.get_capabilities()` has no real-hardware
equivalent to verify against — it's the synthetic path by design.

Also added: `assert_only_camera_backend_imports_picamera2()`, a
structural self-check (runs every `python3 camera_backend.py`) that greps
every other `.py` file in the project for a direct `picamera2`/
`libcamera` import. It found two **pre-existing** violations that predate
this plan and are out of its scope: `wizard_pages.py`'s own
camera-availability probe (`from picamera2 import Picamera2`) and
`test_burst_backend.py`'s direct hardware test — both are carved out as
documented exceptions in the check itself rather than silently ignored or
fixed as a side effect of this part.

**Backlog item (tracked here — `BUILD_LIST.md` is a planning doc, not a
file that exists in this repo, so this is the durable record):** fix the
two violations above. `wizard_pages.py`'s probe is the one that actually
matters — it's a shared UI module reaching into Picamera2 directly, which
is the boundary this plan's "thin adapter" rule is about holding (a test
file doing the same thing, `test_burst_backend.py`, is untidy but lower
stakes). Fix: give `camera_backend.py` a cheap availability-probe
function/classmethod and have `wizard_pages.py` call that instead of
importing `picamera2` itself; revisit `test_burst_backend.py` separately
once that's done.

**Part 01 (Preferences dialog) — now built and committed.** See the
"Preferences dialog" section below for what landed.

**Part 03 (provenance relocation, Keep RAW, auto-processing) — now built
and committed.** See the dedicated section below for the full design.
This is the part that supersedes and deletes `casual_mode.py`.

### Preferences dialog (Preferences-dialog plan set, Part 01)

`qt_shell.py`'s new `PreferencesDialog` (Options > Preferences...)
replaces the old standalone Video resolution/Theme submenus and the
Casual Mode action with one sectioned dialog: Capture and Video Options
(built entirely from `camera.get_capabilities()` — an omitted capability
like `stream_formats` produces no row at all, never an empty/disabled
one), Appearance (Theme, same next-launch shape), and Advanced (Keep RAW
Images, provenance folder location, cache auto-clean). Capture/Video/
Appearance persist only on OK (next-launch settings); Advanced settings
persist immediately on change, independent of OK/Cancel. `CASUAL_MODE_
DEFAULT` and the Options > Casual Mode action are gone from `qt_shell.py`
— `casual_mode.py` itself is untouched, staying until Part 03.

Two things worth flagging that the intent entry didn't call out:
- A new `capture_resolution_kwargs()` (mirroring the existing `video_
  resolution_kwargs()`) wires the Preferences dialog's capture-resolution
  choice through to `Picamera2Camera`'s `full_res` constructor kwarg in
  `main()` — the intent entry only described rendering `get_capabilities()`
  results, not this specific plumbing.
- The Advanced section's controls (Keep RAW Images, provenance folder,
  auto-clean) persist their prefs for real, but nothing reads them yet —
  there is no retention system to gate: that lands in Part 03 (not yet
  drafted, per `PLAN_00_context_and_supersession.md`). Scaffolding, not a
  gap in this part.

Verified: full project `--render-check` sweep (all 16 modules, including
`camera_backend.py`) passes with no regressions; `qt_shell.py`'s own
`--render-check` covers the dialog directly (absent-capability → no
control, present-capability → real control, next-launch-vs-live-apply
persistence split, a stale `"casual_mode"` gui_prefs key degrading
gracefully). Not yet exercised as a live GUI on-rig (this dialog itself,
specifically — see the note above about `QGlPicamera2`'s EGL surface
failure in this environment, which blocks constructing a live
`Picamera2Camera` at all here, not just this dialog).

### Provenance relocation, Keep RAW, and auto-processing (Preferences-dialog plan set, Part 03) — BUILT

Full design in `PLAN_00_context_and_supersession.md` and
`PLAN_03_provenance_relocation_and_keep_raw.md` (drafted, not checked
into the repo), plus `CORRECTION_flat_dark_framing.md` (also not checked
in). This section captures folder-layout and plumbing decisions settled
directly with Brandon that go beyond what those files say, plus what
actually landed — including several real bugs this build surfaced that
the plan files didn't anticipate (see "Bugs this build found and fixed"
below).

**Supersedes `casual_mode.py` in full — the module is now deleted.** Its
capture-and-save logic, format handling, and the `(Exception, SystemExit)`
catch around `hdr_from_session.process()` (`process()` calls `sys.exit()`
on some error paths; `SystemExit` derives from `BaseException`, so a bare
`except Exception` around it lets the worker thread die silently — a real
find from the Casual Mode build, not a hypothetical) were all reused,
lifted into the main capture path, before the module was deleted.

**Folder layout.** Three Preferences > Advanced settings — `provenance_
folder` already exists from Part 01 (default `~/provenance`); this part
adds `capture_folder` (default `~/captures`) and `flat_library_folder`
(default `~/flat`):
- `<capture_folder>/<timestamp>/` — science/hdr/snap raws + processed
  outputs (`final.tif`, `final_display.*`, per-format exports). This is
  today's `OUT_ROOT`-per-session folder, minus the provenance record.
- `<capture_folder>/<timestamp>/dark/` — that session's own dark
  sub-burst, nested underneath it (today it sits flat in the same
  session dir as everything else, distinguished only by file prefix).
- `<capture_folder>/focal/<stack_id>/plane_N/` — z-stack, moved off the
  direct-under-`OUT_ROOT` location `new_zstack_root_dir` uses today.
- `<flat_library_folder>/` — one standing set, replaced outright by each
  new Flat capture (no versioning), reused across every session.
  `hdr_from_session.py`'s "last flat wins" logic changes from scanning
  the *current session's own* `captures` list (today's behavior) to
  reading this one fixed folder — flat is a reusable calibration
  artifact, not a per-session capture.
- `<provenance_folder>/<timestamp>/` — `session.json` + meta sidecars
  ONLY, no image bytes at all. Provenance and images no longer share a
  folder, so a new field on the session record stores the capture dir's
  absolute path (chosen over deriving it from a shared timestamp
  convention, which would silently break if folder-naming ever drifts).
  Every consumer that did `session_dir.glob(...)` assuming images sit
  beside `session.json` now resolves two directories instead of one:
  `hdr_from_session.py` (reads `capture_dir` off the session record it's
  handed, see its own module docstring), `gallery.py` (a new
  `_capture_base_dir`), `qt_shell.py` (a new `_provenance_dir_for`, used
  by `list_sessions`/`load_session_json`/`capture_correction_status`/
  `_run_process_cmd`/`_end_zstack`). `process_wizard.py` turned out to
  need no change of its own — it sources every frame through
  `gallery.capture_frame_paths`, so gallery's own fix already covers it.
  `measure.py` needed the fix too (see "Bugs this build found and fixed"
  below) even though the original plan file didn't name it explicitly —
  its z-stack code (`collect_stack_planes`/`_on_exclude_toggled`) reads
  session.json directly, same as `qt_shell.py`, via its own
  `_provenance_dir_for` (duplicated rather than importing `qt_shell.py`,
  which would drag in the whole Qt capture GUI just for one helper).

**Provenance is always written; Keep RAW Images is the only setting that
changes what survives.** There is no setting that stops a record from
being written — Brandon's framing: invisibility is the product, not the
extinction of provenance. Off means only this capture's own raw frames
are deleted once processing succeeds (`hdr_from_session.py process()`'s
own `a.delete_raw_on_success`, wired from the `keep_raw_images` pref in
`qt_shell.py`'s `_run_process_cmd`, read live at processing time — not
baked into an open session). **The linear master
(`single_master.tif`/`master_*.tif`/`hdr_linear.tif`) is never touched
by this setting** — until 2026-08-03 it was deleted alongside the raws,
a real data-loss bug (a user leaving Keep RAW Images off was consenting
to discard raws, not averaged/merged outputs built from a multi-frame
bracket); see `CHANGELOG.md`'s "Keep RAW Images narrowed to raws only"
entry for the full investigation and fix. The session record states the
raw discard was deliberate: `correction_status["raw_discarded"]` + (when
true) `"raw_discard_reason"`, parsed out of `hdr_from_session.py`'s
`CORRECTION_STATUS_JSON:` stdout line and written onto the capture's own
`session.json` entry by `qt_shell.py`'s `_record_correction_status` — a
later reader (human or agent) can distinguish "the user chose not to
keep these" from "a file is missing"; absence with a recorded reason is
provenance, absence without one looks like corruption. Two new,
unconditional keys make the derived-output side explicit too, rather
than leaving a reader to infer it from `raw_discarded` alone:
`correction_status["derived_outputs_discarded"]` (always `False` today)
and `["derived_outputs_note"]` (why). `measure.py`
fails legibly (not obscurely, and specifically NOT a silent fallback to
the JPG) on a raw-less capture: `load_measurement_plane`'s new
`_raw_discard_reason` checks the owning capture's `raw_discarded` flag
and, if set, names the TRUE reason instead of
`calibrate.resolve_raw_path`'s generic "this suggests the file moved on
its own" wording (which would otherwise misdescribe a deliberate choice
as an anomaly) — never a silent fallback to measuring the JPG, which
`calibrate.resolve_raw_path` already structurally prevented (a `.jpg`
argument resolves to its `.dng` sibling or refuses; it never measures
the JPEG itself). `wizard_pages.py`'s `ImageSourcePage._on_open_existing`
got a matching fix: picking a raw-discarded Gallery entry used to
silently do nothing (`GalleryWidget.selected_paths()` drops any entry
with `raw_path=None`) — it now reports why, with a pointer to the
manual-file escape hatch.

**Auto-processing replaces `_offer_process`'s Yes/No `QMessageBox`** —
the method itself is renamed `_auto_process` (`qt_shell.py`). Snap,
Science, and HDR all process automatically now, matching Casual Mode's
always-functional design — Snap is a genuinely new call site: previously
only science/hdr ever reached `_run_process_cmd` (the comment there was
explicit that frame-averaging a single frame was considered pointless,
but `hdr_from_session.process()` already had a working
`kind in ("science", "snap")` branch, so this was wiring, not new
processing logic). **Bug this surfaced**: `_auto_process` always let
`hdr_from_session.py` default `--raw-ext` to `"dng"`, which is fine on
real hardware (Picamera2Camera always writes `.dng`) but silently broke
processing under the default (no `--camera`) `FakeCamera` backend, which
writes `.tif` — `_auto_process` now detects the real extension via
`capture_correction_status`'s own on-disk glob (the same mechanism the
manual processing wizard already used) rather than assuming a camera
class.

**Flat/Dark correction status must be recorded and displayed as the
named technique, never folded into a generic "processing complete"**
(`CORRECTION_flat_dark_framing.md`). Today `hdr_from_session.py`'s
`process()` builds `ran`/`skipped` lists and only `print()`s them —
nothing persists. This part changes `process()` to return them
structured, so `qt_shell.py` can write `"flat_correction": "applied"` /
`"dark_correction": "skipped (not selected)"` onto the capture's own
entry in `session.json` after processing. A capture with neither
selected states that explicitly — omission reads as "this field
predates the concept," not "the user chose not to." Flat and Dark
selection itself stays exactly as visible in the capture UI as it is
today (the existing checkbox picker at `qt_shell.py` ~line 903) — not
moved into Advanced, not defaulted silently, not collapsed into one
implied on/off.

**Additional export formats, lifted from `casual_mode.py` — but NOT a
literal transplant.** Three placement/scope questions came up mid-build
that the plan files didn't settle, and were resolved directly with
Brandon rather than guessed:
- **Where**: Preferences > Advanced, not a new row of controls next to
  the Capture button — persisted, live-applied (read fresh by
  `_run_process_cmd` at processing time, same as Keep RAW Images), not a
  per-capture UI decision the way Casual Mode's own window had it.
- **JPG-first**: still "always produced, discarded if unchecked" in
  spirit, but the mechanism differs from Casual Mode's literal
  placeholder-then-atomic-swap (which existed because Casual Mode ran a
  synchronous capture→process→deliver flow with a real "before processing
  starts" moment to drop a placeholder into). The main path's processing
  already runs as a background subprocess with no separate staging
  directory; `hdr_from_session.py process()` now converts
  `final_display.png` (or `.tif` if PNG is off) straight into
  `final_display.jpg` via `os.replace` for atomicity once processing
  itself completes, gated on `--export-jpg`.
- **The all-formats-unchecked case**: turned out to be a non-question.
  `final.tif`/`final_display.tif` are `debayer.py`'s own structural
  output of the tonemap step this pipeline always runs (`-o final.tif`
  plus the automatic `_display.tif` sibling `--tonemap` produces) — there
  is no flag to skip them without deeper surgery to `debayer.py` itself,
  a shared tool other paths (`calibrate.py`, `ca_measure.py`) also
  depend on. So TIFF is shown as a checkbox for visual parity with Casual
  Mode's four-checkbox layout, but locked checked and disabled, with a
  tooltip explaining why. PNG is genuinely gatable (`display_opts` only
  passes debayer.py's own `--tonemap-8bit` when `a.export_png`, default
  `True` via `getattr` so a caller that never heard of the flag — i.e.
  `casual_mode.py`'s own `SimpleNamespace`, before it was deleted — kept
  its old unconditional-PNG behavior). JPG and DNG are genuinely new
  (`--export-jpg`, `--export-dng`, `--export-dng-merge`), gated the same
  "produce it or don't, never produce-then-delete" way. DNG's own
  naming — `<file_prefix>raw.<ext>` untouched, or `<file_prefix>raw.tif`
  when Process DNG merge is also checked — follows the same "never a
  mislabeled `.dng` for a merged result" rule Casual Mode's own
  `dng_merge` checkbox used, since a merge produces a derivative and a
  DNG container would mislabel it as raw.
- Audited first (per Brandon's own staged plan for this step):
  `gallery.py`/`process_wizard.py`/`measure.py` turned out to have ZERO
  dependency on `final_display.tif`/`.png` existing at all (gallery only
  ever shows raw+camera-preview-jpg; process_wizard has its own
  independent `<label>_final.tif` pipeline; measure.py never touches a
  display-referred file, by its own green-plane-only invariant) — so no
  changes were needed there, only in `qt_shell.py`'s own render_check
  fixtures, which had assumed unconditional TIFF/PNG.

**Build order** (as actually built, including the parts the original
plan didn't anticipate): `provenance.py`'s new roots + `Session` split →
`qt_shell.py`'s Preferences additions and capture-path re-plumbing
(flat/dark/focal) → `hdr_from_session.py`'s structured return and
flat-library lookup → `gallery.py`/`process_wizard.py` path resolution
→ **[not in the original plan] fix `measure.py`'s own stack code and
`qt_shell.py`'s `_end_zstack`, both still assuming session.json sits
beside the raw frames — see "Bugs this build found and fixed"** → the
auto-process wiring and structured flat/dark recording (**+ the
`--raw-ext` bug fix above**) → Keep RAW deletion + discard recording →
`measure.py`'s legible failure (+ `wizard_pages.py`'s matching Gallery
fix) → audit `final_display.*` consumers, settle the all-unchecked case,
lift casual_mode.py's format handling and JPG-first delivery (**with the
placement/scope decisions above, settled directly with Brandon
mid-build**) → delete `casual_mode.py` and its remaining plumbing → full
`--render-check` sweep → this completion entry.

**Bugs this build found and fixed, not part of the original design:**
1. `qt_shell.py`'s `_end_zstack` passed CAPTURE-side `plane_dirs`
   straight to `_stacks.validate_all`, which reads `session.json` from
   whatever directory it's given — a real regression introduced by this
   same build's own earlier `_provenance_dir_for` work (before this fix
   landed), silently validating nothing and reporting "No issues found"
   even for a real 3-plane stack. Fixed by mapping through
   `_provenance_dir_for` first; a new render_check assertion
   (`"No issues found." in zwin.capture_status.toolTip()`) now actually
   checks `validate_all`'s real output, which no prior check did.
2. `measure.py`'s `collect_stack_planes`/`_on_exclude_toggled` (and, via
   `_stacks.group_by_stack`, `stacks.py`'s own `load_session`) assumed
   `session.json` sits beside the raw frames — pre-dating Part 03
   entirely, not caught by the plan files' consumer list. Would have
   silently found zero stacks once real captures moved to the new
   layout. Fixed with `measure.py`'s own `_provenance_dir_for` (see the
   folder-layout note above) — `stacks.py` itself needed no change,
   since it just reads whatever directory it's handed.
3. `casual_mode.py` called `hdr_from_session.process()` directly (not
   through the CLI) and broke twice as that function's signature changed
   under it during this build: once for the new required `a.flat_root`
   (`processing_args()` gained a `NO_FLAT_ROOT` sentinel — a path that
   can never hold frames, preserving "Casual Mode never flat-corrects"
   without importing `provenance.py`), once for `process()`'s new
   `(disp, correction_status)` tuple return (unpacked and the status
   discarded — Casual Mode has nowhere to persist it). Both fixed before
   the module was deleted, so its own `--render-check` kept passing
   throughout the build, not just at the very end.

**`provenance.py` extraction plan** (read this before writing any of the
code — it resolves a real Python gotcha that has already bitten this repo
twice via a different mechanism):

- Moving out of `qt_shell.py`, verbatim: `OUT_ROOT`, `PROFILE_PATH`,
  `load_profile`/`save_profile`, `_dump_meta`, `new_session_dir`/
  `new_zstack_root_dir`, `class Session`, `record_capture`/`record_burst`/
  `record_hdr`. Confirmed via this session's own Tier 0 investigation that
  `camera_backend.py` has zero session/provenance awareness, so this is a
  clean pull-out of code that already lives in exactly one place.
- **Staying** in `qt_shell.py` (out of phase-1 scope): `list_sessions`,
  `load_session_json`, `processable_captures`, `capture_correction_
  status`, `archive_session_raws`, `build_display_flags` — reading
  `session.json` back out for browsing/processing-prep is a different
  concern from writing new provenance records.
- **The constraint that shapes everything**: `qt_shell.py`'s own
  `render_check()` mutates `OUT_ROOT`/`PROFILE_PATH` as module state
  (isolating test fixtures, and — after two real incidents this session —
  keeping the whole self-check off the real `~/imx/profile.json`).
  Whichever module owns these names is where that mutation has to happen.
  Every consumer (`qt_shell.py`, `gallery.py`, `wizard_pages.py`) must
  reference `provenance.OUT_ROOT`/`provenance.PROFILE_PATH` **by
  attribute** — never `from provenance import OUT_ROOT`, which creates a
  second, independent binding that silently stops tracking the moment
  either side reassigns it. `provenance.py` carries an explicit comment
  on the constants themselves about this, not just here.
- `qt_shell.py`'s ~23 internal call sites (11 production, ~12 in
  `render_check()`) get module-qualified: `Session(` → `provenance.
  Session(`, etc. — matching how `_calibrate.X()`/`_stacks.X()`/
  `_gallery.X()` already work in this file. `gallery.py`'s `OUT_ROOT`
  default and `wizard_pages.py`'s `new_adhoc_dir()` get the same
  treatment (both currently reach `qt_shell.OUT_ROOT`/`qt_shell.
  new_session_dir` via their own private `_lazy_qt_shell()`).
- Test relocation, not just a move: the `render_check()` blocks that
  prove `Session`/`record_*` *mechanics* move into `provenance.py`'s own
  `--render-check`; `qt_shell.py`'s keeps only what's actually GUI
  behavior, calling into `provenance.*` as supporting infrastructure.
- Not in this pass: phase 2 (store-mechanics migration for `calibrate.py`/
  `annotations.py`/`ca_measure.py`) and Casual Mode (item 2) — both
  explicitly depend on this landing first. Phase 2's intent is now
  recorded — see this file's "Store-mechanics migration" section below.

**Tier 0 investigation results** (both now answered):
1. `camera_backend.py` is NOT session-aware — see above.
2. The CA wizard's live-capture path (via the shared `wizard_pages.
   ImageSourcePage`/`_CapturePane`) still constructs its own independent
   `Picamera2Camera()`, confirmed still true. This is exactly why
   `CAWizard` still isn't wired into `qt_shell.py`'s own menu (only
   referenced in comments there). Still gates the Measure-menu reorg
   (Tier 3 item 3) — that reorg needs this fixed first, or needs to ship
   with CA's live-capture path explicitly disabled/flagged.

**Part 04 (green-plane cache) — now built and committed.** See the
dedicated section below (after the Debayer.py follow-up) for the full
design, the real-hardware timing finding that changed a default, and what
landed. Depended on Part 01 for its Advanced-tab controls, per
`PLAN_00_context_and_supersession.md`'s own dependency order.

**Part 05 (live measure panel) — now built.** See the dedicated section
below (after Part 04's own section) for the full design, the real
eventFilter-wiring bug found resuming a session that dropped mid-build,
and the render_check coverage added. Depended on Part 04 (built) for the
cache a committed mark points at. The only part of this plan set that
adds a genuinely new user-facing capability — everything before it was
relocation, configuration, or housekeeping. This closes out the
Preferences-dialog plan set (Parts 01–05), all built.

All five parts of the Preferences-dialog plan set are done — verified with
a full `--render-check` sweep across all 16 modules (`casual_mode.py` stays
deleted; `plane_cache.py`, Part 04's new module, is the 16th). Since then,
two further pieces of work landed, each documented in its own section
further down: a bug fix to Part 02's `get_capabilities()` (see "Fix:
Preferences dialog crash on `get_capabilities()`"), and a new, separate
feature called Live Measuring (see "Live Measuring (quick ruler) — BUILT").
Nothing is currently in progress.

### Debayer.py tonemap/write split (Part 03 follow-up, same day) — BUILT

Part 03 above shipped with TIFF locked checked in Preferences > Advanced's
export-format row — `debayer.py` had no way to skip writing it, so "TIFF
unchecked" wasn't actually possible. Brandon flagged this as a workaround,
not the real "whatever's checked in Preferences is what gets written,
full stop" contract, and asked for an audit-first fix across the whole
raw-processing pipeline (`frame_average.py` → `debayer.py` → `hdr_merge.py`
→ `hdr_from_session.py`), in dependency order, with a consumer-compatibility
check gated before touching `debayer.py` itself.

**Audit findings:** `frame_average.py` and `hdr_merge.py` are both
entirely upstream of tonemap (raw-domain masters in, raw-domain masters
out; `hdr_merge.py`'s own docstring is explicit that it never tone-maps)
— zero dependency on the write-format question, no changes needed in
either. The consumer-compatibility check found `gallery.py`/`measure.py`
have zero dependency on `final_display.tif` existing (gallery only shows
raw+camera-preview-jpg; measure only reads the file's embedded tag to
*refuse* it, never its pixels) — but `process_wizard.py` has its own
independent `debayer.py --tonemap-out` call, a real second caller whose
contract had to survive the split unchanged.

**The split itself.** `debayer.py`'s tonemap block (CA correct → white
balance → Reinhard tonemap → shadow-deepen/CLAHE/sharpen → sRGB OETF)
already produced its result as an in-memory array (`disp`) before writing
anything; the write to TIFF was just the next line. The existing
`--tonemap-8bit` (PNG) flag was *already* correctly decoupled — it wrote
straight from `disp`, never reading the TIFF back off disk — that was the
template. Three write-format flags now hang independently off the one
`disp` computation, none reading another's file:
- `--tonemap-tiff` / `--no-tonemap-tiff` (new; default ON, so every
  existing caller — `process_wizard.py`'s `--tonemap-out`, any direct CLI
  use — keeps working unchanged).
- `--tonemap-8bit` (existing, untouched).
- `--tonemap-jpg` (new) — writes straight from `disp` via PIL, sharing
  the same 8-bit conversion `--tonemap-8bit` already computes when both
  are requested.

**`hdr_from_session.py`:** the JPG export added earlier the same day (a
post-hoc PIL conversion reading `final_display.png`/`.tif` back off disk)
is gone — it would have broken the moment TIFF and PNG were both
unchecked, and was the wrong shape even before that, a disk round-trip
for data that already existed in memory one process ago. Replaced with
`--tonemap-jpg` passed straight through to the `debayer.py` subprocess
call in `display_opts()`. **Also added: real output-existence
validation.** `run_tool()` only ever checked subprocess exit status —
`debayer.py` degrades a missing Pillow to a stderr-only warning with
exit code 0, so a checked PNG/JPG that silently never got written would
have been invisible (`_on_process_finished`'s success path only surfaces
`stdout`). Each requested format is now checked against disk after the
subprocess returns and recorded in the `ran`/`skipped` stage summary
with the real reason if it's missing.

**`qt_shell.py`:** TIFF's checkbox is unlocked — a real `export_format_
tiff` preference, persisted immediately like PNG/JPG/DNG, wired into
`_run_process_cmd` (`--no-export-tiff` when unchecked). Unchecking all
three display formats is now a legitimate choice, not a special case: it
just means no display-referred image gets produced that run (`final.tif`,
the linear RGB measurement master, is unaffected either way — it has no
checkbox, never did).

Verified: manual CLI smoke tests confirmed each new `debayer.py` flag in
isolation (baseline unchanged, `--no-tonemap-tiff --tonemap-jpg` correctly
omits the TIFF and writes only the JPG) before touching any caller. Full
`--render-check` sweep, all 15 modules, including `process_wizard.py`
exercising the real `--tonemap-out` contract through its own subprocess
call — confirms the split didn't disturb that third, independent caller.

### Green-plane cache (Preferences-dialog plan set, Part 04) — BUILT

Full design in `PLAN_00_context_and_supersession.md` and
`PLAN_04_green_plane_cache.md` (drafted, not checked into the repo). New
module: `plane_cache.py` — Qt-free, camera-free, importable from
`qt_shell.py` the same guarded-optional way `stacks`/`calibrate`/`measure`/
`gallery`/`process_wizard` already are (`_plane_cache = None` on a missing
file, degrading rather than crashing).

**Not a performance cache — the substrate a committed measurement points
at**, per the plan's own framing: Part 05's live measure panel will pull a
green plane on first click and measure against it, and once a mark is
committed against that plane, the plane has to keep existing on disk for
as long as the mark does, or the mark is stranded (it still says what it
measured, but nothing can re-derive or verify the number). Keyed by
`pixel_sha256`, never timestamp or sequence: `plane_cache.plane_path(hash)`
resolves a cached plane's filename from the hash alone, no index or
mapping table anywhere — the same key `annotations.json` uses, so
`measure.py` opening a cached plane and this store's marks find each other
automatically.

**Location**: `<provenance_folder>/plane_cache/<pixel_sha256>.tif` — a
subfolder of `provenance.PROVENANCE_ROOT` (Part 03), same "out of sight,
not out of existence" placement as `session.json`'s own sidecars, never
the capture output folder. Read live via `provenance.PROVENANCE_ROOT`
(attribute access, at call time, not import time) — same rule
`provenance.py`'s own `OUT_ROOT`/`PROFILE_PATH` comment documents, and
what lets both `plane_cache.py`'s own `--render-check` and `qt_shell.py`'s
redirect the whole cache to a disposable temp dir just by reassigning that
one attribute.

**Real-hardware finding that changed a default, not just informed one**:
the plan asked for extraction timing to be measured on the Pi 5 rather
than trusted from a size estimate, since Part 05's whole interaction
design assumes the pull-to-cache step is imperceptible on first click.
Driving `Picamera2` directly (the documented on-rig workaround — real
IMX477, `capture_request()` → `save_dng()` → `calibrate.load_mosaic_array`
→ `debayer.extract_green`, the exact chain the app's own capture path
uses), on this rig: `extract_green` itself is negligible (~0.03 ms,
confirming the plan's own claim that this is a slice, not a de-mosaic);
`pixel_sha256` hashing is ~9 ms; an uncompressed `tifffile.imwrite` is
~6 ms — but a **deflate-compressed** write of that same real captured
plane is **~570-600 ms**, almost two orders of magnitude slower, because
real sensor noise compresses far more slowly than the synthetic random
data an estimate would reach for (deflate on synthetic random data of the
same shape/dtype ran in ~90 ms on this same rig — still not representative
of the real cost). Compression does shrink the file meaningfully (~3.9MB
vs ~6.2MB for one plane), but 600 ms is not imperceptible, so
`plane_cache.store_plane` writes **uncompressed**, keeping the whole
extract+hash+store pipeline at ~15 ms end to end. Disk space stays "a few
MB" either way, which the plan's own weight section already accepted as
fine. This is called out with the real numbers in `plane_cache.py`'s own
module docstring so a future reader doesn't quietly flip it back to
deflate without re-measuring against real sensor data first. Separately
worth knowing for Part 05's own design: the DNG capture-and-write step
itself (`save_dng`) measured ~530 ms on its own in this same test — that
dwarfs anything Part 04 adds, and it's an existing cost of the capture
path itself, not something the cache introduces; Part 05 will need to
reckon with it directly (e.g. capturing in-memory rather than a full DNG
round-trip) if a live first-click pull is to feel instant.

**Mechanics** (`plane_cache.py`): `store_plane`/`load_cached_plane` are
atomic (temp file + `os.replace`, same pattern every other store in this
project uses) and idempotent (an already-cached hash is left untouched,
never rewritten). `clean_cache(referenced=None, older_than_days=None)` —
`referenced` defaults to a fresh `annotations.load_annotations().keys()`
read (so a plane that gains a mark since the last clean is automatically
ineligible, no bookkeeping needed); `older_than_days=None` is "Clean cache
now" semantics (every unreferenced plane goes, regardless of age); a
number is "auto-clean" semantics (only unreferenced planes at least that
old by mtime go). Returns
`{"removed", "retained_referenced", "retained_too_new"}` so a caller can
report the real reason a plane survived, not just a single kept/removed
split — per the plan's own "report what a clean actually did" instruction.

**`measure.py` integration — checked early, per the plan's explicit
instruction, not deferred to the end.** It turned out to need zero
adaptation: `measure.load_measurement_plane` already treats a bare
green-shaped TIFF as a pass-through substrate (no re-extraction, no
description-tag requirement beyond "not flagged as a display-referred
derivative"), which is exactly what a cached plane is. `plane_cache.py`'s
own `--render-check` proves the full loop: store a plane → open it through
`measure.load_measurement_plane` → confirm the hash matches the cache key
exactly → save a mark under that hash via `annotations.save_mark` →
confirm `annotations.image_record_for` resolves it straight back. No
index, no mapping table, nothing to keep in sync — the plane and its
marks share one key by construction.

**`qt_shell.py` wiring**: the Advanced tab's "Clean cache now" button
(built in Part 01 as a stub) now calls `plane_cache.clean_cache(
older_than_days=None)` for real and reports the real counts
("removed N, kept M (referenced by a saved measurement)") instead of the
old "no cache to clean yet" placeholder. Auto-clean
(`cache_auto_clean_enabled`/`cache_auto_clean_days`, also Part 01 stubs)
runs once in `main()`, right after the Part 03 folder-layout prefs are
applied to `provenance.PROVENANCE_ROOT` — a settled placement call, not a
silently guessed one: this app has no other recurring background-timer
mechanism to hang periodic housekeeping off, sessions are typically
started and closed rather than left running for days, and startup is
already where every other "apply a persisted setting" step in `main()`
happens. Revisit if a long-running session ever turns out to need a
mid-run re-clean.

Verified: `plane_cache.py`'s own `--render-check` covers hash-only
resolution, atomic write, idempotent store, the live `PROVENANCE_ROOT`
read, the `measure.py`/`annotations.py` integration above, clean-now,
auto-clean's day threshold, and — via two identically-aged twin fixtures
in separate roots, since `clean_cache` has no dry-run mode to test
eligibility without side effects — that gaining a reference after aging
past the threshold flips a plane from prune-eligible to retained.
`qt_shell.py`'s own `--render-check` drives the real "Clean cache now"
button handler end to end (not a hand-fed `referenced` set): a plane with
a real `annotations.save_mark` committed against it survives the click, an
unmarked sibling doesn't, against `annotations.json` redirected to a temp
path for the check's duration (same isolation rule as
`provenance.PROFILE_PATH`/`PROVENANCE_ROOT`, never the real
`~/.zynergy/annotations.json`). Full `--render-check` sweep, all 16
modules, passes. Extraction/store timing above is real-hardware-verified
(see the caveat elsewhere in this file: a headless pass proves internal
consistency, not that it works on the rig — the timing numbers here are
the hardware half, run separately, not inferred from the self-check).

### Live measure panel (Preferences-dialog plan set, Part 05) — BUILT

Full design in `PLAN_00_context_and_supersession.md` and
`PLAN_05_live_measure_panel.md` (drafted, not checked into the repo). The
only part of this plan set that adds a genuinely new user-facing
capability — everything else was relocation, configuration, or
housekeeping.

**Real bug found resuming a dropped session, now fixed**: the bulk of
this part's code (`_LiveMeasureCanvas`, `LiveMeasurePanel`, the
`_live_measure_*` state machine on `FocusPreviewWindow`) had already
landed uncommitted when the prior session dropped, including a fully
written `_live_measure_preview_event` — but it was never actually wired
into `eventFilter`. `self.preview`'s ordinary box-drag (`_press`/`_move`)
would have kept firing on every click while the panel was open, and the
freeze-triggering click would never have happened at all. `eventFilter`
now checks `self._live_measure_active` first and routes to
`_live_measure_preview_event` (which consumes the event unconditionally)
before falling through to the box-drag branches — see
`qt_shell.py`'s `eventFilter` for the fix. This is exactly the kind of
gap `--render-check` coverage exists to catch, which is also why none
existed yet for this part: the build had stopped before verification.

**What it is**: a small floating panel (`Qt.Tool`, native title bar so the
window manager gives it dragging and a close button for free, non-modal so
the live feed underneath stays interactive) with a shape picker (distance/
angle/polygon/ellipse — the same four tools `measure.py` has, same
click-count-per-shape interaction) and a status line. Opened from a new
action in the existing "Measure" menu (`_launch_live_measure`, alongside
the current "Measure..." action) — the plan's own prose says
"Options > Measure," but that was written before this app grew its own
top-level "Measure" menu; putting the new action there instead of a
literal Options submenu is the real, consistent placement, not a deviation
worth relitigating.

**Freeze on first click** (the plan's own load-bearing decision): the
first left-click on `self.preview` while the panel is open and nothing is
frozen yet triggers a real still capture — `camera.capture_still_async`
into a throwaway `tempfile.mkdtemp` directory, no `provenance.Session`, no
`record_capture`, no sidecar. This is deliberately lighter than the main
capture path: a live-measure freeze is not a provenance event, it is
"pull the substrate a click needs right now," and Part 04's own framing
already established that the cached plane — not a session record — is
what has to survive. Once the capture resolves, `measure.
load_measurement_plane` (reused as-is, not touched) extracts the green
plane exactly the way the rest of the app already does; `pixel_hash.
pixel_sha256` + `plane_cache.store_plane` cache it; the temp directory is
deleted (`finally:`) regardless of outcome. The click's own screen
position is converted through the SAME `displayed_rect`/`frac_from_point`
fractional mapping the focus box already uses against `self.preview`,
scaled into `GREEN_PLANE_RES` — this is the "preview-to-sensor" mapping
the plan calls for, and it already exists in this file for a different
feature, so Part 05 reuses it rather than building a second copy. That
converted point becomes the FIRST point of whatever tool is armed, not a
throwaway trigger click — matching the plan's own "I clicked and started
measuring" framing.

**Display swap**: `self.preview` moves from being a direct splitter child
to living inside a small `QStackedLayout` wrapper (added to the splitter
in its place) alongside a new `_LiveMeasureCanvas` (a `QGraphicsView`).
Not frozen: the wrapper shows `self.preview`, live as always. Frozen: the
wrapper shows the canvas instead, with the cached plane as its pixmap
(`calibrate.array_to_qimage(calibrate.stretch_to_uint8(plane))`, the exact
call `measure.py` itself uses to render a plane). Closing the panel swaps
back and restores box-drag on the preview, which is suspended for the
whole time the panel is open (the two features would otherwise fight over
the same clicks on the same widget).

**`_LiveMeasureCanvas` is new, not `measure.MeasureView` reused** — a
deliberate choice, not an oversight. `measure.py` stays untouched, per the
plan (`PLAN_00`'s own "measure.py is not rewritten and not touched"), and
this canvas needs two things `MeasureView` doesn't have: per-mark
`QGraphicsItem` tracking (so a specific mark can be recolored on commit or
removed on delete — `MeasureView`'s own `draw_*` methods discard their
item references immediately) and a THREE-way visual state instead of
`MeasureView`'s two (`PENDING_PEN` while a shape's points are still being
clicked; a new solid-orange pen for a finished-but-uncommitted mark; a
solid-cyan pen — identical color to `measure.py`'s own `MARK_PEN` — once
committed, so a mark looks the same here as it will when `measure.py`
later opens the same plane by hash). The click-sequence logic itself
(2 points for distance, 3 for angle, double-click to finish a 3+-point
polygon or 5+-point ellipse) is the same rule `MeasureView` already
encodes; duplicated as the minimum needed to drive the different rendering
underneath, not because the logic itself changed.

**Marks stay in memory until committed.** Finishing a shape's points
builds the mark object via `annotations.build_distance_mark`/
`build_angle_mark`/`build_polygon_mark`/`build_ellipse_mark` (`measure.
fit_ellipse` for the ellipse fit) — identical calls to what `measure.py`'s
own `commit_mark` makes — but does NOT call `annotations.save_mark` at
that point. It is held in a plain list (`{"mark":, "committed": False,
"items": [...]}`) and drawn with the uncommitted pen. The objective (and
therefore `um_per_px`) is read from the main window's existing `ruler_
objective_combo` and snapshotted onto the entry the moment its points
finish, not re-read later at commit time — same "snapshot at mark time"
discipline `annotations.calibration_ref_for` already documents for its own
`um_per_px` field, applied one step earlier here since commit is a
separate, later action.

**Right-click menu** is exactly the plan's own shape:
```
Commit > Point / All
Delete > Point / All
```
Point hit-tests the nearest finished mark within a fixed view-space pixel
radius (not scene-space, so the grab radius stays constant regardless of
zoom); greyed out on a miss, per the plan. Delete only ever acts on
uncommitted marks — hitting an already-committed mark greys out both
Point actions (Commit has nothing left to do; Delete structurally can't,
the store never deletes). Escape cancels an in-progress, not-yet-finished
click sequence (mirroring this app's existing Escape conventions
elsewhere), kept off the right-click menu entirely so that menu stays
exactly the two actions the plan specifies, no overloading.

**Closing discards.** Every uncommitted mark and its scene items are
dropped; the live preview is restored; the frozen plane and hash are
cleared, so reopening the panel is a genuine blank slate. The plane
already written to `plane_cache` is left on disk (harmless — Part 04's own
`clean_cache` reclaims anything nothing ever referenced), never deleted
here directly.

**The one change outside this file's/`plane_cache.py`'s own territory**:
`FakeCamera` gains an additive `capture_shape=(64, 64)` constructor kwarg
(default matches today's hardcoded shape exactly, so every existing caller
and test is unaffected) so `--render-check` can drive the REAL `capture_
still_async` → `measure.load_measurement_plane` path end to end headlessly
— constructing `FakeCamera(capture_shape=(GREEN_PLANE_RES[1],
GREEN_PLANE_RES[0]))` makes the fake's own captured frame already
green-plane-shaped, so `load_measurement_plane` takes its real
already-extracted-green branch with no stubbing. The full-mosaic-needs-
extraction branch is covered separately, against a synthetic mosaic-shaped
array, the same way `measure.py`'s and `calibrate.py`'s own checks already
cover that branch — not exercised through the live GUI path, since
`FakeCamera` cannot produce a real full-sensor mosaic worth extracting
from (the standing "`FakeCamera` cannot exercise everything" caution
`PLAN_00` itself repeats).

**Verification**: self-check-only, no access to the rig this session —
same honest split `PLAN_00` asks for. `qt_shell.py --render-check`'s new
"Live measure panel" block proves: `native_point_from_preview_click`
scales the real `frac_from_point` fraction into the green plane's actual
resolution (asserted on the real converted values, not just "a mark
exists" — this is the claim the whole freeze design rests on); the
freeze-triggering click is driven through the REAL `eventFilter`, not
called directly, which is what actually caught the wiring bug above and
proves ordinary box-drag is suppressed while the panel is open; freezing
happens exactly once per panel session (a second, stray click routed to
`self.preview` post-freeze is a no-op; the hash stays stable) and a real
`FakeCamera(capture_shape=...)` capture round-trips through
`load_measurement_plane`'s already-extracted-green branch; a finished
2-point shape (built the same way as the freeze's own first point, via
`add_point_programmatic`, the same entry point the canvas's own
`mousePressEvent` uses) holds in memory with the uncommitted pen; Point
hit-test misses empty space and finds a real mark by its own segment
geometry (`live_measure_mark_segments` + `mapFromScene`); commit writes
to a temp-redirected `_calibrate.CALIBRATION_PATH`/`_annotations.
ANNOTATION_PATH` pair (a real `40x` calibration entry, not a hand-fed
`um_per_px`) and flips the pen to the committed color; Delete is a no-op
against a committed mark; closing discards every uncommitted entry,
restores the live preview, and leaves the already-committed mark
untouched in the store. Hardware verification (a real first-click freeze
feeling instant on the rig, per Part 04's own timing caveat about `save_
dng`'s ~530 ms) is explicitly NOT claimed until run on the actual Pi.

**Full screen mode detail worth knowing**: `F11` toggles; the interaction
model (explicit toggle key, not auto-hide-on-idle or an always-visible
translucent overlay — a translucent overlay would permanently obscure
part of the live specimen view) and the menu-bar-hides/Ctrl+Escape-exits
behavior both came from a direct discussion with the user, not a default
assumed here. `FocusPreviewWindow._panel` (the SAME widget instance
docked in `self._splitter` normally) reparents into a lazily-created,
never-destroyed floating `Qt.Tool | Qt.FramelessWindowHint` window
(`self._floating_panel`) on every full-screen entry, and back into the
splitter on every exit — reparenting happens on EVERY toggle, not just
the widget's first construction; an earlier version of this only added
the panel to the floating window's layout inside the `if self.
_floating_panel is None:` one-time-creation block, so a second F11 press
left the panel stranded wherever it last was. If you touch
`_toggle_fullscreen` again, keep the reparenting call outside that
one-time guard. `P` shows/hides the floating panel while full screen
(genuine no-op otherwise); plain `Escape` was deliberately left untouched
(already cancels an armed burst / aborts a batch sequence) — `Ctrl+
Escape` is the exit, a distinct key combination so it never needs
priority ordering against those two existing branches. Not persisted
across a relaunch, on purpose — see `CHANGELOG.md`'s entry for the reason
(disorientation risk of a hidden-chrome launch with no visible way out),
not because this app avoids persisting UI state in general (it doesn't).

**Real on-rig bug, now actually fixed (confirmed live, real camera, real
tablet)**: the live preview (`self.preview`, the real `QGlPicamera2`
widget on-rig) was staying pinned to a small rectangle after `F11`
instead of filling the screen. Two earlier theories here were both
wrong and reverted in turn (an explicit `self.preview.resize(...)` nudge;
then forcing `QT_QPA_PLATFORM=xcb`, which turned out to be harmless but
not the fix -- see `CHANGELOG.md` for both post-mortems). Real root
cause: this rig's display is physically 4096x2160 driven at a
compositor-level 2x output scale (`wlr-randr --output HDMI-A-1 --scale
2`, in `~/.config/labwc/autostart`, user-added to make the UI legible on
the panel). XWayland presents that to Qt as a "logical" 2048x1080 screen
with `devicePixelRatio` 1.0. Ordinary windowed content is fine because
the compositor's normal composited path scales it up 2x, but a *real*
`showFullScreen()` puts the window into the compositor's actual
`xdg_toplevel` fullscreen state, and wlroots-based compositors commonly
fast-path that straight to display scanout, skipping the scale-up --
so a 2048x1080 buffer lands on the 4096x2160 panel covering exactly one
quarter of it.

Fix: `_toggle_fullscreen` no longer calls `showFullScreen()`/
`showNormal()` at all -- it sets `Qt.FramelessWindowHint` and manually
resizes to `QApplication.primaryScreen().geometry()` (restoring the
saved pre-fullscreen geometry + flags on exit), which stays on the
normal composited (correctly-scaled) path since the compositor never
sees a real fullscreen state. `self._is_fullscreen` (+
`self._pre_fullscreen_geometry`) backs this app's own notion of the
state now; `isFullScreen()` stays `False` throughout. If you touch this
again: **do not** add `Qt.WindowStaysOnTopHint` via `setWindowFlags` on
this window after it's shown -- confirmed on-rig to crash (`setWindowFlags`
recreates the window's native handle out from under `self.preview`'s
already-created EGL surface, real XCB `BadDrawable`/`BadWindow` errors).

**Known limitation, not yet fixed**: in this fake-fullscreen mode, the
desktop taskbar (`wf-panel-pi`, a `wlr-layer-shell` surface, always above
ordinary windows by design) stays visible over the bottom edge -- real
fullscreen would raise above it automatically, but this deliberately
isn't real fullscreen anymore (see above). A raw EWMH
`_NET_WM_STATE_ABOVE` `ClientMessage` was tried (delivered successfully
per `XSendEvent`/`XFlush`, but silently ignored by labwc -- `xprop`
never showed `_ABOVE` on the window) and reverted; no trace left in the
code. The one lead not yet tried: labwc's own `ToggleAlwaysOnTop` action
via an `rc.xml` `<windowRule>` keyed off a distinctive window title (see
`CHANGELOG.md`'s entry for the full reasoning) -- needs a one-time edit
outside this repo, so needs the user's go-ahead first.

**Themes detail worth knowing**: the user wants to design a dozen-plus
side-panel aesthetics over time, so this is NOT a fixed theme list — the
Options > Theme menu is built by `discover_themes()` scanning
`themes/<name>/style.qss` under `THEMES_ROOT` (next to `qt_shell.py`).
Adding a new theme is dropping in a folder, nothing else. A theme's own
QSS references its images via `url({{ASSETS}}/file.png)`
(`themes/<name>/assets/`), substituted by `load_theme_stylesheet()` for
that theme's own absolute path — plain QSS `url()` resolves against the
app's working directory otherwise, which would silently break on launch
from anywhere but this exact folder. The side panel itself carries
`objectName("side_panel")` for a theme's own `#side_panel { ... }` rule
to target. Same persisted/next-launch pattern as video resolution, for
consistency (`resolve_theme_qss_path()` degrades a stale/deleted theme
preference to the stock look, never raises in `main()`). One minimal
starter theme ships (`themes/dark/style.qss`, plain colors) just to prove
the pipeline works — the real aesthetics are the user's own to design and
drop in later.

**Casual Mode (BUILD_LIST Tier 3, item 2) — done.** Lives entirely in the
new `casual_mode.py` (`CasualModeWindow`), plus the small preference/menu/
`main()`-branch plumbing in `qt_shell.py` described below. Full design
lives in `PLAN_casual_mode.md` (drafted, not checked into the repo, same
as `BUILD_LIST.md`); this is the durable as-built summary.

Casual Mode is **not** a reduced feature set — it is the *same* capture
behavior (snap, burst frame-averaging, HDR bracket, debayer, tonemap) with
a different file-retention policy: no session folder, no `session.json`,
no `.meta.json` sidecars, no `pixel_sha256`, no `calibration_ref`. Only
the final image survives; intermediates are cleaned up automatically, no
prompt.

*The separation mechanism is by construction, not a flag*: `casual_mode.py`
never imports `provenance.py`'s write functions (`Session`,
`record_capture`, `record_burst`, `record_hdr`, `_dump_meta`,
`new_session_dir`, `new_zstack_root_dir`) — so no code path through it can
write a provenance record, and no future edit can silently reintroduce one
without an import that's visible in review. `casual_mode.py`'s own
`--render-check` asserts this structurally (inspects the module's own
namespace/imports for those names), not just behaviorally.

**The one real wrinkle this surfaced, worth understanding before touching
either module**: `qt_shell.py`'s normal capture path invokes
`hdr_from_session.py` as a **subprocess** CLI (`_run_process_cmd`), and
that CLI's `main()` *requires* a real `session.json` on disk (`sj =
session_dir / "session.json"; if not sj.is_file(): sys.exit(...)`) — so
Casual Mode cannot reuse that entry point without writing exactly the
provenance artifact it exists to avoid. `hdr_from_session.py`'s `process()`
function itself, underneath `main()`, is provenance-free: it takes plain
`session`/`cap` **dicts** (never touches disk for them, never writes
`session.json` or any sidecar) and does the real work (frame averaging,
HDR merge, debayer, tonemap) against a `session_dir` passed in explicitly.
So `casual_mode.py` imports `hdr_from_session.process` directly and hand-
builds the minimal `session`/`cap` dicts `record_capture`/`record_burst`/
`record_hdr` would otherwise have produced — same pipeline, zero
provenance i/o, and the entanglement never reaches disk. This is a
deliberate deviation from the plan's "same pipeline" phrasing being a
literal subprocess call; the underlying image operations and results are
identical either way.

**Output format design deviates from the original plan**, per Brandon's
own call made during the build (the plan's fixed seven-preset list
`dng, png, jpg, tiff, tiff+jpg, dng+jpg, png+jpg` did not resolve cleanly:
a real DNG is a raw Bayer-mosaic container, and Burst/HDR's "same pipeline"
merges multiple raw frames into one TIFF master via `frame_average.py`/
`hdr_merge.py` — there is no valid single DNG for that merged result, and
writing merged data under a `.dng` extension would misrepresent the file's
actual format, directly against this project's honesty-about-derivatives
principle (`publish.py`'s explicit `"NOT a measurement"` labeling is the
same rule applied elsewhere)). Resolved as independent format checkboxes
(DNG / PNG / JPG / TIFF, any nonempty combination — a generalization of
the plan's seven fixed presets, not a subset of them) plus a dedicated
checkbox, enabled only for Burst/HDR, choosing what "DNG" means for a
multi-frame capture: unchecked delivers the first captured frame's own
real, untouched `.dng`; checked delivers the merged raw-domain master
*honestly saved with a `.tif` extension* (never `.dng`), with status text
explaining why the extension differs from what was requested. For a
single Snap this checkbox is moot (one frame IS the result) and stays
disabled.

The JPG-first UX itself is unchanged from the plan: a placeholder JPG
(the camera's own preview JPG, already written at capture time by
`camera_backend.py` — free, no extra encode) lands in the destination
folder immediately, before the debayer/tonemap chain finishes. If JPG
was one of the checked formats, the real processed JPG atomically
replaces that placeholder in place (`os.replace`, temp name first — never
delete-then-write). If JPG was **not** checked, the placeholder is
removed only once every checked format's file is safely on disk. On a
processing failure, the placeholder is always kept and the failure is
reported plainly — never silently presented as a complete result.
FakeCamera never writes a preview JPG of its own (`CaptureResult.preview`
is always `None` there), so the placeholder path has an off-rig fallback
too: a quick PIL-synthesized stand-in straight off the raw frame, purely
so the behavior stays exercisable under `--render-check` without real
hardware — real hardware always takes the free-copy path.

**What `casual_mode.py` actually contains, for whoever touches it next**:

- `assert_no_provenance_import()` is the structural half of the
  guarantee — inspects this module's own namespace for the six
  provenance write-function names, but ALSO for the bare name
  `provenance` itself (stricter than the plan's literal list): binding
  the `provenance` module under any name would leave every write
  function reachable via attribute access with no further import,
  which defeats the guarantee just as completely. `--render-check` calls
  it first, every run. Because of this, the module never reaches for
  `provenance.OUT_ROOT`/`load_profile`/`save_profile` either, even though
  none of those three are on the forbidden-name list — importing the
  module at all to reach them would still trip the guard. `DEFAULT_OUT_ROOT`
  is a plain literal `Path.home() / "photos"`, and exposure is fully
  self-contained: continuous `auto_exposure`/`auto_white_balance` while
  idle (a point-and-shoot default, unlike `FocusPreviewWindow`'s locked/
  reproducible exposure), frozen via `apply_exposure_lock` with whatever
  AE last metered just before each shot (mirrors `_enforce_exposure_lock`'s
  own trick in `qt_shell.py`, reimplemented locally rather than shared),
  resumed after. `~/imx/profile.json` is never touched.
- `run_capture_and_save(camera, kind, out_root, formats, dng_merge, n,
  stops)` is the Qt-free core: capture via `camera_backend.py` directly
  (`capture_still_async` for snap, `capture_burst` for burst,
  `enter_still_mode`/`capture_bracket_phase`(science)/
  `capture_bracket_phase`(dark)/`exit_still_mode` for HDR — the identical
  dance `qt_shell.py`'s own `_run_burst_kind` uses for HDR's two
  phases), stage into `tempfile.mkdtemp(prefix="zynergy_casual_staging_")`,
  hand-build the `session`/`cap` dicts (`snap_cap_dict`/`hdr_cap_dict`)
  `provenance.record_capture`/`record_burst`/`record_hdr` would otherwise
  have produced, call `hdr_from_session.process()` directly, write every
  checked format, delete the staging dir unconditionally (`finally:`).
  Catches `(Exception, SystemExit)` — `hdr_from_session.process()` itself
  calls `sys.exit(...)` on a missing-frames error (inherited from being a
  CLI script's helper function), which is a `SystemExit`, not a plain
  `Exception`; missing that would have hung the GUI's worker thread
  silently on a real processing failure of that specific shape.
- Output filenames all share one collision-avoiding stem from
  `new_output_stem()` (same shape as `provenance.new_session_dir`, for a
  file instead of a directory): `<stem>.tif` (TIFF format, the processed/
  display image), `<stem>.png`, `<stem>.jpg`, and `<stem>_raw.<ext>` for
  DNG — the `_raw` prefix is load-bearing, not decoration: without it, a
  Burst/HDR capture with both "tiff" and "dng+process-merge" checked would
  have written the display TIFF and the raw-domain merged master to the
  exact same `<stem>.tif` path, silently clobbering one with the other.
  Also fixes a real collision that only shows up off-rig: FakeCamera's own
  raw extension is `.tif` too, so an unprocessed "dng" output under
  FakeCamera would otherwise collide with a "tiff" format request at the
  same plain `<stem>.tif` — `_raw` avoids this in both the real (`.dng`)
  and fake (`.tif`) environments, not just the one where it happens to
  look necessary.
- `CasualModeWindow`'s format checkboxes (DNG/PNG/JPG/TIFF, JPG checked by
  default) and the "Process DNG (merge Burst/HDR frames)" checkbox are the
  as-built form of the plan's original seven-preset list — see the
  "Output format design deviates" paragraph above for why, and Brandon's
  own framing that led to it ("give a checkbox option ... to select which
  gets the process, if any, or both"). The process-DNG checkbox disables
  itself whenever the capture kind is Snap (one frame IS the result;
  nothing to choose between).
- `_LivePreviewFallback` is a small, independent duplicate of
  `qt_shell.py`'s own `_FakePreview` (paints `focus_frame()` for
  FakeCamera, which has no `.widget`) — duplicated, not imported, so this
  module has zero dependency on `qt_shell.py` in either direction. The
  only place `qt_shell.py` and `casual_mode.py` touch is `main()`'s own
  lazy, in-branch `import casual_mode`.

**Verification status, stated plainly (per this project's own hard-won
rule that a headless pass proves internal consistency, not that it works
on the rig — video recording passed every headless check while producing
no file at all on real hardware, three separate times)**: every claim
above is **self-check-verified** (`casual_mode.py --render-check`,
including a real end-to-end pass through `CasualModeWindow`'s actual
worker thread and queued completion signal, not just the pure
`run_capture_and_save` function directly) and via the full project
`--render-check` sweep (all 15 modules pass). **Nothing here has been
verified on the real IMX477 rig** — this was built in a non-interactive
session with no hardware access. Real-hardware verification should follow
the documented workaround (drive `Picamera2` directly, see "Real-hardware
testing workaround" below) before this ships as trusted, especially the
places `--render-check` cannot reach on FakeCamera alone: the real
preview-JPG-copy path (`CaptureResult.preview` is always `None` on
FakeCamera, so only the PIL-synthesized fallback has actually run), the
real `.dng`/`.jpg` extension pairing end to end, and `camera.widget`
embedding in `CasualModeWindow.__init__` (untestable off-rig for the same
`QGlPicamera2`/EGL reason `FocusPreviewWindow`'s own embedded preview is,
see "`QGlPicamera2` ... needs a real GL-capable X session" below).

**Video resolution menu detail worth knowing (updated — see "Decouple
video resolution from preview" below for the full story)**:
`camera_backend.py`'s `Picamera2Camera.set_video_resolution()` has never
had a live effect — recording always encodes the preview config's fixed
"main" stream, set once at construction — despite a stale comment in
`__init__` claiming otherwise (it described an abandoned mode-switching
design; `start_recording`'s own history notes are the accurate account,
and the comment itself has since been corrected in place). The
Preferences dialog's "Video resolution (next launch)" combo used to write
a `gui_prefs.json` preference that `main()` fed into
`Picamera2Camera(preview_res=...)` at construction — that coupling caused
a real crash (a non-4:3-ish `preview_res` broke its pairing with the
fixed `LORES_RES`, silently killing focus aid for the rest of the
process) and has been **removed**. The combo is now disabled
(`setEnabled(False)`, with an explanatory tooltip) rather than live — see
"Decouple video resolution from preview" below. **Update**: `preview_res`
is no longer pinned to `PREVIEW_RES` unconditionally — a real, enabled
"Preview resolution (next launch)" setting now governs it (`preview_
resolution_kwargs()`, ROADMAP item 2 REVISED — see this file's own section
below and `CHANGELOG.md`'s matching entry), self-check-verified but NOT
yet on-rig. Naming note, worth repeating since this file's own next
section had to catch it once already: it is "preview_resolution", never
"stream_resolution" — that name is reserved for a different, dormant,
unbuilt feature (a future network streaming server). If you ever want
video resolution (as opposed to preview resolution) to apply live, that
still means tearing down and rebuilding the camera+widget while running;
think hard about it first, given this project's track record with
in-session camera reconfiguration (see `start_recording`'s own
docstring).

It lives entirely in `qt_shell.py`'s `FocusPreviewWindow`:

- `zstack_btn` ("Start Z-Stack" / "End Z-Stack (N planes)") mirrors
  `_toggle_recording`'s own two-state shape exactly: press to start
  (captures plane 0 immediately as part of starting, via
  `_start_zstack` → `_capture_zstack_plane`), press again to end
  (`_end_zstack`). `self._zstack` is `None` when inactive, else
  `{"root": Path, "stack_id": str, "next_plane": int}`.
- The **existing** Capture button/menu action (`_start_capture`) gets a
  branch at its very top: while `self._zstack is not None`, every press
  repurposes to `_capture_zstack_plane()` (next plane) instead of its
  normal untagged-snap behavior — this is the only reading of the build
  list's "one button... each subsequent press... a distinct action (same
  button again, mirroring Record)" that makes "mirroring Record" literally
  true, since Record itself is a pure two-state toggle with nothing in
  between. No second new button was needed.
- Folder layout: `~/captures/zstack_<timestamp>/plane_0/`, `plane_1/`,
  ... — each plane its own real, independent `Session`, via a small,
  backward-compatible `Session.__init__(..., session_dir=None)` extension
  (skips the usual auto-timestamped `new_session_dir` call when an exact
  directory is given). `new_zstack_root_dir` mints the stack's own root +
  `stack_id` (the timestamp itself, reused — no second ID scheme).
- Each plane capture: `camera.capture_burst(dir, "science_", 1)` →
  `record_burst(..., "science", ...)` → `stacks.apply_tag` + `session.
  write()` (the same two calls `_on_tag_stack` already makes manually,
  just automatic) → `_score_capture_sharpness`, on a worker thread with
  its own `zstack_plane_done_signal` (kept separate from `burst_done_
  signal`, which is hardwired to `self._session`/`self._batch_active` —
  both wrong here).
- `_on_zstack_plane_finished`'s success path calls `self.meter.reset_
  field()` — `SPEC_focus_aid_fps_and_stack_reset.md` part 2's requirement
  carried over from the manual `_on_tag_stack` path, per that spec's own
  forward note (this WAS a real gap for one session's worth of time: the
  z-stack aid shipped without it, caught and fixed once the spec was
  re-read against the finished flow). Fires only on a successful plane
  capture+tag, never on the failure branch above it.
- While a stack is active, `capture_kind_combo`/`record_btn` are disabled
  (mirrors Record's own mutual-exclusion of `capture_kind_combo`);
  `capture_btn`/`_capture_action` stay enabled and repurposed.
- Ending the stack runs `stacks.validate_all` over the plane folders and
  shows the result, then offers (never forces, matching `_offer_process`'s
  own precedent) to open `process_wizard.ProcessWizard(out_root=<stack's
  own root folder>)` with every plane pre-selected. Gallery is naturally
  scoped to just this stack's planes because `list_gallery_entries` treats
  `out_root`'s own immediate children as sessions, and the stack root's
  immediate children ARE exactly this stack's `plane_N/` folders — no
  changes were needed in `gallery.py` or `process_wizard.py` for this.

**Testing note for anyone extending this**: the render-check's own z-stack
coverage does NOT bypass the worker-thread/signal machinery the way
`_on_tag_stack`'s test does for the single-shot async path — it drives the
real button handlers and pumps `QApplication.processEvents()` in a loop
until `self._capturing` clears, because `zstack_plane_done_signal` is a
genuinely cross-thread QUEUED connection (worker thread → GUI-thread slot)
that a real event loop would drain automatically but a headless script
must pump itself. If you add another worker-thread-backed z-stack method,
test it the same way, not by calling its completion handler directly.

Four standalone tools, one shared GUI entry point:

- `python3 qt_shell.py [--camera]` — live capture GUI. Has **Calibrate** and
  **Measure** menu items that open `calibrate.py`'s and `measure.py`'s
  windows as separate, non-modal windows (see "Menu integration pattern"
  below). Session/profile management (`Session`, `load_profile`,
  `new_session_dir`, ...) that used to live in a separate `capture.py` is
  now baked directly into this file — `capture.py` was deleted this session
  because it wasn't sensor-specific and had no reason to be its own module.
- `python3 calibrate.py [image] [--objective NAME]` — spatial (µm/px)
  calibration, standalone or via its own wizard (no args).
- `python3 ca_measure.py [target] -o out.json` / `--wizard` — chromatic
  aberration calibration, standalone or via its own wizard.
- `python3 measure.py [image] [--objective NAME]` — the analysis GUI:
  4 measurement tools, z-stack filmstrip with onion-skin, export, publish.
  Reachable from `qt_shell.py`'s Measure menu, or run directly.

`gallery.py` is a fifth, shared (not standalone) module: a capture-browsing
grid widget, thumbnails from the JPG previews already written alongside
every raw capture. Two modes off one `GalleryWidget` — `GalleryPickDialog`
(multi-select-capable; replaced the plain `QFileDialog.getOpenFileName` in
`wizard_pages.py`'s `ImageSourcePage`, `measure.py`'s and `calibrate.py`'s
own `_on_open`) and `GalleryBrowseWindow` (`qt_shell.py`'s new "Browse
captures..." File menu action, just looking, no commit). Whether a capture
already has annotations is checked lazily, in a background `QThread`, only
against the real green-plane substrate (`measure.load_measurement_plane`) —
**never** a display-referred derivative like `final_display.tif`, which is
structurally excluded from `annotations.json` (see `check_measurement_
provenance`) and would silently under-report if hashed instead.

`process_wizard.py` is a sixth shared module, built on `gallery.py`: the
"choose your operations" processing wizard (`ProcessWizard`, a 3-page
`QWizard` — select files via an embedded `GalleryWidget`, pick green/rgb +
optional color-correct gains, run). Reachable from `qt_shell.py`'s new
"Process files..." File menu action, deliberately separate from the older
"Process session..." (`ProcessSessionDialog`/`hdr_from_session.py`), which
stays untouched — that one is still the right tool for a session's own
recorded HDR bracket; this one is for an arbitrary set of Gallery captures
or loose files. It does **not** support HDR-merge grouping from arbitrary
files (see `process_wizard.py`'s own module docstring for why that's a
deliberate cut, not a gap) — if that need ever shows up for real, don't
bolt it onto this wizard's `_OperationsPage` without rereading that
docstring first.

Every module with real logic has a headless self-check:
`python3 <module>.py --render-check`. Run the whole set before trusting
anything:

```bash
for m in pixel_hash annotations export publish calibrate measure ca_measure \
        wizard_pages qt_shell stacks focus gallery process_wizard \
        provenance plane_cache; do
  DISPLAY=:0 python3 $m.py --render-check || echo "FAILED: $m"
done
```

All 16 currently pass (`casual_mode.py` is deleted, Part 03 — no longer in
this list; `plane_cache.py`, Part 04's new green-plane cache module, is
now in it). `stacks.py`, `focus.py`, `plane_cache.py`, and `calibrate.py`'s
own pure functions run fine without PyQt5 or a display; `qt_shell.py`/
`measure.py` have PyQt5-gated checks that print `SKIPPED` (not `FAILED`)
when PyQt5 isn't importable — that's correct, expected behavior, not a bug
to chase. `camera_backend.py` has no `--render-check` flag of its own —
its self-check runs unconditionally on `python3 camera_backend.py` — so
it's not in the loop above; run it separately.

### Fix: Preferences dialog crash on `get_capabilities()` (Part 02 follow-up)

Opening Options > Preferences while the camera was running crashed:
`RuntimeError: Camera must be stopped before configuring`. Root cause:
`Picamera2.sensor_modes` is not a passive lookup — reading it internally
calls `configure()`, which Picamera2 refuses while the camera is running,
and the main window's preview has always already started the camera by
the time a user can open Preferences. Part 02's own hardware verification
missed this because it exercised `get_capabilities()` standalone, against
a `Picamera2()` that was not mid-preview — the translation logic itself
(favoring `"unpacked"` over the raw `PixelFormat` object) was genuinely
confirmed correct; calling it during the camera's actual normal running
state, the only state the real UI ever calls it in, was not.

**Fix, not workaround**: `sensor_modes` describes fixed hardware
capability that cannot change between construction and any later point in
the same process, so it is queried exactly once — in
`Picamera2Camera.__init__`, while construction is still in progress and
before `start()` can possibly have run — and cached on
`self._capabilities`. `get_capabilities()` returns that cached dict on
every later call and never touches `_picam2` again; external contract
(signature, return shape) is unchanged, so `PreferencesDialog` needed no
changes at all. `FakeCamera` got the identical caching shape (eager
`__init__` priming, cache-or-compute) purely for symmetry between the two
backend classes — its own synthetic result never changes regardless.

If you touch either class's `get_capabilities()` again: do NOT add a
stop-query-restart dance around a live call — there is no case that needs
the camera paused just to re-answer a question that hasn't changed since
construction. Also do not assume the returned dict is safe to mutate in
place across callers — it's the same cached object every time now, not a
fresh one per call (see `qt_shell.py`'s `PreferencesDialog.__init__`,
which only ever reads via `.get(...)`, never mutates).

**Verification split, same honest pattern as everywhere else in this
project**: the self-check (`camera_backend.py`, run directly — see above)
proves the caching CONTRACT — a second `get_capabilities()` call returns
the exact same cached object (`is`, not just an equal value), both for
`__init__`'s own eager priming and a forced cold first computation —
against `FakeCamera`, the only one of the two classes constructible
off-rig (`Picamera2Camera.__init__` also builds a real GL preview widget,
on top of needing real hardware). **On-rig confirmation is now done**:
reproduced the original crash first (opened Preferences while the preview
was running, on the real Pi 5 + IMX477, confirmed via photos of the
crash), then confirmed the fix removes it — same sequence, no crash,
Preferences shows the same real capture resolutions/formats Part 02's own
standalone verification already found. Both halves of this fix — the
caching contract (self-check) and the actual crash being gone
(hardware) — are independently verified.

### Live Measuring (quick ruler) — BUILT

Full design in a user-provided `PLAN_quick_ruler.md` (not checked into the
repo). A new, separate feature from Measure/Part 05's own "Live measure
panel" above — do not conflate the two. Live Measuring is a pixel-only
overlay on the LIVE, moving feed: no freeze, no calibration, nothing ever
committed to a store. It deliberately reuses Part 05's own *interaction
shape* (floating `Qt.Tool` panel, shape picker, click-to-place, right-click
menu) but never its substrate — every result is labeled in plain pixels or
degrees (`"143.2 px"`, `"61.9°"`), never a calibrated µm figure, so a
screenshot of this feature can never be mistaken for an actual measurement.

**Where it lives**: its own menu entry, "Live Measuring...", on the SAME
"Measure" menu as "Measure..." and "Live measure..." (Part 05) — a third,
independent tool, always enabled (unlike the other two, it has no
`measure.py`/`annotations.py` dependency that could be missing). Marks draw
straight into the SAME overlay buffer `_tick()`/`_static_overlay_buf()`
already manage for the focus box/ruler — no separate canvas widget, no
`self.preview`/`_preview_stack` swap the way Part 05 needed (there is
nothing to freeze here). Amber while a shape is still being clicked, white
once finished — deliberately neither of Part 05's own two colors
(orange/cyan), so a glance never confuses which of the two live tools is
on screen. **Mutually exclusive with Part 05's live panel, both
directions**: both repurpose `self.preview`'s clicks for their own tool, so
opening either one closes the other first (`_launch_live_measuring`/
`_launch_live_measure` each check the other's `_active` flag).

**Module-boundary rule, enforced structurally, not just by convention**:
Live Measuring must never import or call into `calibrate.py`/
`annotations.py`/`provenance.py`, or reuse Part 05's own
`native_point_from_preview_click` — `assert_live_measuring_has_no_
calibration_dependency()` scans every Live Measuring function/method's own
source for those names, the same way `assert_only_camera_backend_imports_
picamera2()` (`camera_backend.py`) polices the camera-import boundary.

**Picked up this session from a build that had dropped mid-flight — same
pattern as Part 05's own predecessor session, and it hid the same kind of
bug.** The feature's code (pixel-math helpers, `LiveMeasuringPanel`, the
`_live_measuring_*` state machine, overlay drawing, `eventFilter`/
`keyPressEvent` wiring) was already written and looked complete on
inspection. **Two real bugs, found by actually running the self-check
rather than trusting that its presence meant it worked:**
1. `assert_live_measuring_has_no_calibration_dependency()` was defined but
   called from nowhere — not `render_check()`, not anywhere. An assertion
   nobody runs only *looks* like a guard. Now called at the top of
   `render_check()`'s own Live Measuring section, so a future regression
   here is caught by `--render-check`, not left to code review.
2. Once actually run, it failed immediately — on itself.
   `lores_point_from_preview_click`'s own docstring names
   `native_point_from_preview_click` (Part 05's function) to *explain* why
   it's deliberately not reused — and the check's original `word in
   inspect.getsource(...)` scan can't tell a docstring mentioning a
   forbidden name from code actually calling it. Fixed with
   `_source_without_docs_and_comments()`: tokenizes the source and drops
   every `COMMENT`/`STRING` token before scanning. A real reference always
   survives this (it's an attribute access or a call, never a string
   literal), so this removes only the false positive.

**If you touch this feature again**: `_live_measuring_delete_point`/
`_live_measuring_delete_all` were pulled out of
`_live_measuring_context_menu`'s two inline branches specifically so
`render_check()` could drive real deletion without calling the actual,
blocking `QMenu.exec_()` — the same reason Part 05's own commit/delete
(`_live_measure_commit_entry`/`_live_measure_delete_entry`) are already
separate methods rather than living inline in a menu handler. If you add a
third menu action here, give it the same shape rather than inlining new
logic into the menu method itself, or it becomes untestable the same way
these two originally were.

**Verified**: no render_check coverage existed for this feature before
this session — none of the above would have surfaced without writing it.
The new "Live Measuring check" (`qt_shell.py --render-check`) proves: the
module-boundary self-check now genuinely runs clean; the panel opens/reuses
like every other launcher in this file; the mutual-exclusion guard works in
both directions; a real click through the real `eventFilter` converts to
the correct LORES_RES-space point via `frac_from_point` (computed
independently in the test, never a hand-typed literal) and suppresses
ordinary box-drag; distance (2 points)/angle (3 points) auto-finish at
their own count while polygon needs an explicit double-click at or past
its own minimum (and a double-click *before* the minimum is a no-op, not a
short shape); Escape cancels an in-progress shape without touching an
already-finished one; the overlay push actually reaches
`camera.set_overlay` with the focus aid off, proving
`_live_measuring_notify_changed`'s direct-push path is really wired, not
just present; the hit test misses empty space and finds a real mark by its
own segment geometry; Delete Point/All really mutate the mark list;
closing discards every mark and pending point. Full `--render-check` sweep,
all 16 modules: no regressions. **Not yet exercised as a live GUI on-rig**
— same standing limitation as everything else in this file that touches
`self.preview` (see the `QGlPicamera2`/EGL note elsewhere in this file).
Given the three-strikes pattern above ("a self-check must reach the code
the way the application reaches it" — see `PHILOSOPHY.md`), don't treat
this feature as trustworthy on the strength of the self-check alone.
Specifically still unverified on the rig: that a placed mark stays pinned
to its own screen/pixel position and visibly does NOT track the specimen
once the stage moves — the whole design rests on Live Measuring being a
pure screen-space overlay with no re-projection, and `FakeCamera` has no
way to produce a moving feed to exercise that against. Confirm this before
trusting the feature, not just that the panel opens and clicks land where
expected.

**`MeasureWindow` extraction (2026-07-26) — Step 2, recall/review, now
built.** A new, separate migration
(`PLAN_measurewindow_extraction.md`, drafted, not checked into the repo)
breaks `measure.py`'s monolithic `MeasureWindow` apart, extract-then-remove
style: each capability gets a new home while `MeasureWindow` keeps working,
verified independently, the shell deleted only once everything else is
proven (a later step). Step 1 (investigation, already done) confirmed the
plan's five-way capability split against the real code, answered the
Export/Publish UI question (two already-independent dialogs, no
consolidation decision to make), and found wizard-restart
(`_on_restart_wizard`/`restart_requested`/`MeasureWizard`/`_SetupPage`/
`main()`'s wizard loop) dead in the in-app path — `qt_shell.py`'s own
`_launch_measure` never connects `restart_requested`, so clicking "Restart
wizard..." from the Measure menu silently closes the window and does
nothing. Confirmed for full deletion at this migration's shell-removal
step, not fixed (there's nothing to fix once the whole shell goes).

Step 2 (recall/review) was originally scoped read-only; approved as
**editable** after the plan was written. Editable means it needs the same
commit-mark orchestration `MeasureWindow.commit_mark` already has — which
already existed in two independent copies (`MeasureWindow.commit_mark`, and
`qt_shell.py`'s Part-05 Live Measure Panel, which reimplements the same
sequence inline against the same primitives rather than calling
`commit_mark` — the original extraction plan's claim that Part 05 "already
calls into" `commit_mark` was wrong, corrected during step 1). A third copy
was ruled out. **Decision**: extract the orchestration into one shared,
Qt-free module-level function, `commit_measurement()`, that both
`MeasureWindow` and the new `ReviewWindow` call; Part 05's panel is
deliberately **not** migrated to it in this step (it ships, works, and
stays untouched — migrating it is separate future work). This also
satisfies `PHILOSOPHY.md`'s "pure logic is Qt-free" rule, since
`commit_mark` was bound to `self.objective_combo`/`self._plane`/etc.

`commit_measurement()`'s calibration gate is **strict** (unconditional
`if um_per_px is None:`), matching `MeasureWindow`'s pre-extraction
behavior exactly — not the looser gate Part 05's panel uses, which exempts
angle marks (since `build_angle_mark` never uses `um_per_px` — angle is
scale-invariant, so gating it on calibration blocks nothing that needed
blocking). The looser gate is probably the correct end state, but adopting
it now would be a silent behavior change riding on a refactor, and this
step is meant to be behavior-neutral. **This is a decision, not an
oversight, and it has a real consequence to flag**: once `commit_measurement()`
is strict, migrating Part 05's panel to call it later becomes a silent
behavior *regression* for Part 05 (which exempts angles today) unless
whoever does that migration consciously decides to carry the exemption
forward. Direct project precedent for this kind of split: `measure.py`'s
`DEFAULT_CAPTURES_ROOT` hand-duplicates `provenance.OUT_ROOT` on purpose,
with switching to an import left as its own deliberate follow-up rather
than folded into the phase-1 move.

**Built and verified**: `commit_measurement()` lives in `measure.py`'s
pure-logic section (before `_HAVE_QT`), alongside `current_um_per_px`/
`fit_ellipse`/`build_record_defaults` — same shape as `calibrate.py`'s
`build_calibration_entry`. `MeasureWindow.commit_mark` is now a thin
wrapper around it. `ReviewWindow` (new class, same file) is the editable
recall/review capability — reuses `MeasureView` with zero changes to it
(satisfies its existing duck-typed `window_` contract), launches via
`gallery.GalleryPickDialog` exactly the way `MeasureWindow._on_open`
already does, reachable this step via `measure.py --review`. The commit
round trip — a mark committed in Part 05's Live Measure Panel resolving by
`pixel_sha256` in `ReviewWindow` — is now verified end to end, through
Part 05's real click/commit dispatch, not a hand-simulated equivalent; the
assertion lives inside `qt_shell.py --render-check`'s existing "Live
measure panel check" (reuses that check's own frozen plane/hash/committed
mark, no parallel fixture — `qt_shell.py` already depends on `measure.py`,
never the reverse). Full `--render-check` sweep passes across all 16
modules. **Self-check-verified only** — nothing here has been exercised on
real hardware or as a live GUI on-rig yet.

**Real-store pollution, found while building this — now fully resolved
except for the pre-existing entries themselves, which stay by deliberate
choice.** `measure.py`'s own *pre-existing* "mark-commit status-line reset
check" (already in the repo before this session, `BUILD_LIST` Tier 1 item
2's coverage) never redirected `annotations.ANNOTATION_PATH` before
driving real distance/polygon commits — every `measure.py --render-check`
run was writing real marks into the actual `~/.zynergy/annotations.json`.
Same shape of risk as the `PROFILE_PATH` incident elsewhere in this file.

Investigated properly before deciding what to do about it (this needed
verifying, not assuming):
- **The polluted entries are deterministic and identifiable, not
  scattered.** The check's fixture is built once via `np.arange(...) %
  4096` — no randomness, no timestamp — so every run hashes to the exact
  same `pixel_sha256`
  (`45a24e947a87c7817690b7181efb3eea8a3e8279ed8c0a65a1a8752c0bfd9a67`,
  confirmed by direct computation and cross-checked against a real polluted
  store, which held exactly that one key, `8` marks accumulated from
  repeated runs). That hash was never produced by any real capture — it
  resolves to no session, no provenance record. In `annotations.py`'s own
  terms (see `find_orphans`, and `PHILOSOPHY.md`'s orphan-handling section)
  this is precisely an orphan: caller-computed `known_hashes` from any real
  scan of `~/captures/`/`~/provenance/` will never contain it.
- **`~/.zynergy/calibration.json` is unaffected.** Traced the code: the
  calibration-gating block's own `_calibrate.CALIBRATION_PATH` redirect
  (`measure.py`, around the `"40x"` calibration setup) spans the *entire*
  outer `try`, including the status-line check and the later staleness-
  drift test — confirmed by checking every `CALIBRATION_PATH`/`save_calibration`
  call site in the file. On the machine this was verified on,
  `~/.zynergy/calibration.json` doesn't even exist. Only `annotations.json`
  was ever exposed.

**Decision on the existing polluted entries: leave them, don't touch
them.** `PHILOSOPHY.md`'s strict rules are explicit and don't carve out an
exception for this: *"Stores are append-only. Never edit or delete an
existing entry. Never 'clean up' a store."* — "clean up a store" is named
directly, and that's exactly what deleting these would be, however
sympathetic the case (a synthetic, orphaned, deterministic hash that was
never a real measurement) seems. The rule's own doc frames any apparent
need to break it as "a design problem worth raising, not a rule worth
working around," so it's raised here rather than worked around. This
matters concretely for **step 3 (Export)**, which reads the whole store —
its design should surface orphaned entries via the existing
`find_orphans(store, known_hashes)` (evidence, not a silent drop and not a
silent include — the same "evidence, never a gate" pattern this project
already uses everywhere else), not invent new filtering logic, and
absolutely not attempt to prune the store itself as part of building it.

**Forward fix, landed**: the status-line check now redirects
`ANNOTATION_PATH` to its own isolated temp path for its duration, the same
pattern the `commit_measurement()`/`ReviewWindow` checks already use.
Verified directly, not just by re-running the suite: snapshotted the real
store's polluted-key mark count before and after a fresh
`measure.py --render-check` run — unchanged, confirming the fix actually
stops the write rather than merely looking plausible. Full 16-module sweep
still passes with no regressions.

**`MeasureWindow` extraction (2026-07-26) — Step 3, Export and Publish menu
actions, now built.** Relocates
`MeasureWindow._on_export_results`/`_on_publish_package` into dedicated
`qt_shell.py` File-menu actions: Export is store-wide with no dependency on
any open image; Publish, while image-specific, has no open
`MeasureWindow`/`self._plane` to work from once triggered from a menu, so
it picks its own image via `gallery.GalleryPickDialog`. `MeasureWindow`
itself is not deleted this step (extract-first-then-remove discipline
continues; shell removal is a later step).

Two findings reshape this step beyond "just relocate the handlers":
`annotations.find_orphans` has zero production callers today, and a
store-wide Export is its first — the `known_hashes` set it needs will come
from a new `gallery.known_green_hashes(out_root=None)`, unioned with
`plane_cache.list_cached_hashes()` (at the `qt_shell.py` call site, not
inside `gallery.py`) so a plane committed only through Live Measuring's
green-plane cache is never mistaken for a permanent orphan. And Publish's
`calibration_ref` currently reads whatever calibration is *currently*
active for the selected objective rather than the one a mark's microns
were actually computed under — **decided (user, this session) to fix, not
replicate** (Option B+): a new `annotations.stored_calibration_ref` will
return a record's own first-commit `calibration_ref` instead. Checked, not
assumed: no mark carries its own calibration pointer, only a baked-in
`um_per_px`, so this is accurate for the common case but not authoritative
across a mid-record recalibration — a genuine, pre-existing schema gap
this step documents, not closes. See `CHANGELOG.md`'s matching Intent
entry for the full reasoning.

**Built and verified**: `gallery.known_green_hashes(out_root=None)` (next
to `capture_has_annotation`) and `annotations.stored_calibration_ref(
pixel_sha256, store=None)` (next to `calibration_ref_for`) both landed as
designed. `qt_shell.py` gained `Export measurement results...` and
`Publish package...` File-menu actions, following the existing
`_open_X`/`_run_X_cmd`/`_on_X_finished` triad
(`_open_green_extraction`/`_run_green_extract_cmd`/
`_on_green_extract_finished`) and reporting through `_set_capture_status`,
not `measure.py`'s `QMessageBox` convention — both are fire-and-forget
background jobs from a menu, not a synchronous confirmation inside an open
canvas. Export writes the results file first, orphan-scans second (the
write is the deliverable; the scan is comparatively expensive evidence,
never a gate). `capture_scan_ok`/`cache_scan_ok` are tracked
independently; `find_orphans` only runs when BOTH actually completed — a
partial `known_hashes` set produces false-positive orphans exactly as
confidently as an empty one, so partial coverage degrades to
`{"unavailable": "..."}`, the same absent-vs-empty split Part 02 drew for
`get_capabilities()`, never a silent `{"orphans": []}` that could be
mistaken for a clean scan. Publish picks its own image via
`GalleryPickDialog` (mirroring `_open_green_extraction`'s input step), then
builds `calibration_ref` via `stored_calibration_ref` with no objective
picker at all. `measure.py`'s `MeasureWindow._on_publish_package` converges
onto the exact same call, replacing its old currently-active-calibration
lookup — one way this gets built across the whole codebase, not two.

Found and fixed while building, before it ever shipped:
`_run_publish_package_cmd` wrote `green_plane.tif` straight into `out_dir`
without creating it first — harmless when the input actually comes from
`QFileDialog.getExistingDirectory` (which only ever returns an existing
directory), but a real gap for any other caller, including this step's own
`--render-check`, that hands it a directory that doesn't exist yet. Fixed
with a `mkdir(parents=True, exist_ok=True)` before the write, matching
`publish.publish_measurements`'s own defensive `out_dir.mkdir`.

`qt_shell.py --render-check` gained a full pass for both actions, driving
the worker methods directly (bypassing `GalleryPickDialog.exec_`, which
can't run headless): a cache-only plane with a real committed mark proves
NOT an orphan (the direct regression test for the union-of-hashes
finding), a genuinely orphaned record proves reported, `_gallery`/
`_plane_cache` temporarily unavailable proves the write still lands while
orphan evidence reports `"unavailable"` rather than an empty or partial
list, a forced `export.export_measurements` failure proves reported rather
than swallowed, and Publish's manifest is checked against the record's own
stored ref end to end, plus a forced-failure (bad input path) case for
each action. `gallery.py --render-check` and `annotations.py
--render-check` each gained matching direct coverage for the two new
functions. Full `--render-check` sweep passes (all 16 modules), no
regressions. **Self-check-verified only** — nothing in this step has been
exercised on real hardware or as a live GUI on-rig.

### Live measure freeze-on-first-click fix — BUILT

Full diagnosis and plan in a user-provided `PLAN_live_measure_freeze_fix.md`
(not checked into the repo; see `CHANGELOG.md`'s matching Intent/Build
entries for the full plan text and what landed). Fixes a real,
reproducible bug in Part 05's freeze-on-first-click design (see "Live
measure panel" above) — not a new feature.

**Reported symptom**: clicking the live feed with the Live measure panel
open zooms in slightly, doesn't freeze, registers no point, and bricks
every click after that. The zoom is real Picamera2 behavior (the still
capture switches to `full_res`, changing the FoV) — not the bug, just
confirmation the click really does reach `_live_measure_freeze`.

**The bug**: `_on_live_measure_freeze_done` sets
`self._live_measure_frozen = True` before the pixmap/`set_image`/stack-swap
block that can actually fail, and its availability guard checks only
`_measure is None`, never `_calibrate is None` — which is just as
legitimately `None` as `_measure` elsewhere in this file. A `calibrate.py`
import failure makes `array_to_qimage` raise on `None`; the exception
escapes the slot with the flag already `True`, and
`_live_measure_preview_event`'s own `_live_measure_frozen` short-circuit
then swallows every click forever — exactly the reported "first click
zooms, doesn't freeze, then everything is dead" symptom. Separately, the
freeze click's own point was discarded whenever no tool was armed at click
time.

**The fix, as built**: guard `_calibrate is None` the same way
`_measure is None` already is; set `_live_measure_frozen` only after the
swap actually succeeds, restoring the live preview on any failure instead
of leaving the mode bricked; require a tool to be armed before a click can
start a freeze at all (a click with no tool now prompts for one and never
captures); with that guarantee in place, always register the
freeze-triggering click as point 1 — a real behavior change: the first
click both freezes the frame and places the first measurement point, not
just the former; and actually set/clear `self._capturing` during a
freeze, which the code's own docstring already claimed but never did.
Landed exactly as planned, no deviations. `measure.py`, `camera_backend.py`,
`plane_cache.py`, and `pixel_hash.py` are all untouched, as scoped.

**Render-check coverage** (`qt_shell.py --render-check`, appended right
after the existing "Live measure panel" check, five fresh camera/window
fixtures so a failure in one case can't mask a bug in another): a
`_calibrate is None` freeze (frozen stays `False`, live preview stays the
current stack widget, status reports the real reason, `_capturing`
clears, and a later click still completes a real freeze — not bricked); a
forced `set_image` exception (same four postconditions — this is the
direct regression test for the reported bug); the happy path (the
triggering click's own converted coordinate — via
`native_point_from_preview_click`, not a hand-typed literal — lands as the
frozen canvas's sole pending point); no tool armed (a spied
`capture_still_async` is never called, the click is still consumed, and
status prompts for a tool); and the `_capturing` lifecycle on the two
exit paths the first three cases don't already cover — a freeze failure
(the delivered result is itself an `Exception`), a `measure.
load_measurement_plane` failure, and a synchronous `capture_still_async`
raise (before any worker starts). Full `--render-check` sweep passes,
including this file's own 6 new "Live measure freeze-fix" PASS lines.

**Environment gap found while verifying, not fixed (out of scope for this
plan)**: in a genuinely fresh environment — no `~/.zynergy/gui_prefs.json`,
no calibration on record — `--render-check` hangs forever the first time
any `FocusPreviewWindow` gets constructed and pumped, well before this
fix's own new coverage runs. Root cause: `_maybe_show_onboarding_gate`
(checklist §4's first-launch prompt, unrelated to this plan) fires a real
blocking `QMessageBox.question` the first time
`should_show_onboarding_gate` sees both "not shown yet" and "no
calibration exists" — true by construction in a brand-new environment —
and a headless/offscreen run has no way to click it, so the whole process
sits polling forever (confirmed via `py-spy dump`, not guessed: the stack
showed `_maybe_show_onboarding_gate` under `_pump_until_idle`'s
`qtapp.processEvents()`). Worked around for this session by pre-seeding
the real `onboarding_calibration_prompt_shown` pref to `True` before
running the check — environment setup, not a code change, and exactly the
state any machine that has run this app once before would already be in
(which is presumably why this has never surfaced before). Flagging here
rather than silently fixing it: `render_check()`'s own test isolation
already redirects `PROFILE_PATH`/`CALIBRATION_PATH`/`ANNOTATION_PATH` for
exactly this class of problem elsewhere in the file (see "Things that will
bite you," below) — this one spot doesn't, and should get the same
treatment in a dedicated fix, not bundled into this plan's scope. **Now
fixed** — see "Onboarding gate must not block a non-interactive launch"
below. **Closed.**

**Verified**: self-check first (as above), then manually driven against a
real `should_show_onboarding_gate` fresh environment to actually observe
and diagnose the hang above (not merely inferred). **Not yet exercised as
a live GUI on-rig** — same standing limitation as the rest of Part 05; the
plan's own three on-rig checks (tool selected → freeze + point 1 lands
correctly; no tool selected → prompt, no zoom, no capture; a simulated
on-rig freeze failure → feed stays live, next click works) are still
outstanding.

### Onboarding gate must not block a non-interactive launch — BUILT

Full plan in a user-provided `INTENT_onboarding_gate_headless.md` (not
checked into the repo). Follow-up to the environment gap flagged (not
fixed) during the Live Measure freeze fix, above — now closed.
`qt_shell.py` only.

**Problem**: `_maybe_show_onboarding_gate` (~3551) fires via
`QTimer.singleShot(0, ...)` from `MainWindow.__init__` (~2772). On a
genuinely fresh install (no `onboarding_calibration_prompt_shown` pref, no
calibration on record) `should_show_onboarding_gate` correctly returns
True and a real modal `QMessageBox.question` appears — that part is
working as designed. **The defect is that it fires regardless of whether
anything can dismiss it.** With no one at the keyboard — offscreen Qt,
CI, a container, an SSH session with no display — the modal blocks the
event loop forever, with no output and no timeout; this is exactly the
hang `py-spy dump` diagnosed in the freeze-fix session above.

**Scope correction, made explicitly in the intent doc**: an earlier
description of this overstated it as hitting "every fresh rig." On a real
rig with a display, a human clicks the dialog and nothing is broken. The
hang is specific to non-interactive contexts — narrower than first
described, but still the actual blocker for clean-environment testing,
which is the reason to fix it at all.

**Landed exactly as planned, no deviations** (five parts):
1. `should_show_onboarding_gate` gains a third parameter, `interactive`
   (default `True`, so every pre-existing call site keeps its old
   behavior) — returns True only when not-shown AND no-calibration AND
   interactive. Stays the existing pure, Qt-free predicate, fully testable
   without a display.
2. New `_onboarding_session_is_interactive(no_onboarding_flag=False)`
   helper, in one place: non-interactive when `QT_QPA_PLATFORM` is
   `offscreen`/`minimal` (name compared alone, ignoring any
   `:`-separated backend option), when the new opt-out flag is passed, or
   when no live `QApplication` instance exists yet. Reads the *effective*
   `QT_QPA_PLATFORM` live via `os.environ`, never cached — the file's own
   `os.environ.setdefault(..., "xcb")` (~76) is untouched, so an
   explicitly-set value still wins. Errs toward `True` everywhere else, by
   design: an unrecognized platform or a real SSH session with display
   forwarding is left alone, not guessed at, since a missed one-time
   prompt costs nothing (the Calibrate menu action always covers
   "whenever") while wrongly suppressing a real one means a user silently
   never learns they need to calibrate.
3. New `--no-onboarding` CLI flag (`main()`'s argparse) for a scripted
   launch that has a real display but shouldn't be interrupted — threaded
   through a new `no_onboarding` constructor parameter on
   `FocusPreviewWindow` (`self._no_onboarding`, read fresh on every gate
   check, never cached), documented in the module docstring's usage block
   alongside `--render-check`.
4. **The ordering detail most likely to get lost if this had been done
   casually**: `save_pref("onboarding_calibration_prompt_shown", True)`
   still fires *before* the dialog is shown on the interactive path
   (unchanged — a crash mid-dialog must not re-prompt on every later
   launch). Suppression for non-interactivity writes nothing — and this
   fell out for free from `should_show_onboarding_gate`'s own early
   return, since `save_pref` already only ever ran downstream of that
   check; no separate guard needed.
5. The freeze-fix session's pref pre-seeding workaround is no longer
   needed and was never actually code (it was a one-off manual step
   against this sandbox's real `~/.zynergy/gui_prefs.json`, not anything
   checked into `render_check()` itself) — nothing to remove from the
   suite. The "Environment gap found" note above is marked closed.

**Explicit non-goals, honored**: the gate's one-time-ever semantics for
real interactive users are unchanged; `calibrate.py` is untouched — the
CALIBRATION INTEGRATION banner's (~926) "delete these blocks and nothing
else" separability contract still holds (the banner's own removal list is
updated to include the new helper, flag, and constructor parameter); no
other blocking-dialog site was touched in this pass.

**Render-check coverage added**: the predicate's full eight-combination
truth table; the interactivity helper's own branches (offscreen/minimal,
a platform value with backend options after a colon, the opt-out flag,
an unrecognized platform defaulting to interactive, and — checked before
this function constructs its own `QApplication`, the one point in
`render_check()` where this is genuinely true — no live instance yet);
suppression leaving the real pref file completely unwritten (not merely
"no dialog shown" — the assertion that actually matters, since a
regression here is otherwise invisible); the interactive path still
writing the pref before the dialog (via a monkeypatched
`QMessageBox.question`, since a real one would hang this exact check);
and `--no-onboarding` suppressing an otherwise-interactive session.

**Verified**: full `qt_shell.py --render-check` sweep passes (exit 0) on
a genuinely fresh environment — `~/.zynergy/gui_prefs.json` and
`calibration.json` both deleted before the run, no pre-seeding of any
kind. The resulting `gui_prefs.json` (written to by other, unrelated
render-check sections that don't redirect `PREFS_PATH` themselves — a
pre-existing characteristic, out of scope here) has **no**
`onboarding_calibration_prompt_shown` key at all afterward, confirming
suppression held across the *entire* sweep's real `FocusPreviewWindow`
construction, not just the isolated test block. **Not yet exercised as a
live GUI on-rig** — this sandbox has no real display, so the interactive
path (prompt shows exactly once, honors both Yes and No) is verified only
by the monkeypatched render-check case above, not by an actual human
click; that on-rig confirmation is still outstanding, same standing
limitation as the rest of this file's Qt-facing work.

### Live Measure frozen canvas must fit its frame on first freeze — BUILT

Full plan in a user-provided `live_measure_canvas_fit_three_phases.md`
(not checked into the repo). Found during on-rig testing of the
freeze-on-first-click fix — the core behavior is confirmed working (first
click froze the frame and registered a real 14.885 µm distance from that
same click) — `qt_shell.py` only, entirely inside `_LiveMeasureCanvas`.

**Two residual, cosmetic-but-disruptive defects**:
1. The first freeze of a session renders the frozen plane as a small
   thumbnail in a large empty area rather than filling the frame. Closing
   and reopening the Live measure box restores normal appearance; the
   second and every later freeze in the same session are already correct,
   including after moving the stage.
2. The frozen canvas doesn't visually match the live preview — Qt's
   default gray background plus a visible view frame, instead of the live
   feed's own black letterboxing — so the swap reads as a different,
   patchier widget appearing rather than the same frame freezing in place.

**Root cause of (1)**: `_LiveMeasureCanvas.set_image` ends with
`resetTransform()` + `fitInView(..., Qt.KeepAspectRatio)`, but `set_image`
runs from `_on_live_measure_freeze_done` *before*
`_preview_stack_layout.setCurrentWidget(self._live_measure_canvas)` — at
that moment the canvas isn't the stack's current widget yet and has no
real laid-out geometry, so `fitInView` computes against stale/absent
geometry and lands on a much-too-small transform. The class defines no
`resizeEvent`, so the bad fit is never recomputed once real geometry
actually arrives. Later freezes are fine because the canvas already has
real geometry retained from the first time it was ever shown.

**Not a bug — recorded here specifically so it doesn't get "fixed" later**:
- A 174.652 µm reading taken on the mis-fitted first-freeze view is an
  imprecise *click*, not a scale error. Clicks convert through
  `mapToScene`, so scene coordinates — and therefore µm values — are
  independent of the view's current zoom. Case 5 of the render-check
  coverage below exists specifically to lock this in.
- The ~1 second click-to-freeze lag (the real `switch_mode` full-res
  capture) is expected and explicitly accepted by the user, not a defect
  of this plan.
- The frozen plane is greyscale because it's literally the green plane —
  inherent to what gets frozen, not a rendering defect. A future color
  freeze would mean carrying the full RGB still alongside the green plane;
  parked deliberately, out of scope here.

**Landed exactly as planned in three of four steps; the fourth was
confirmed not applicable, not silently skipped**:
1. The fit is factored into one small `_fit_to_view` method (no-ops when
   `_pixmap_item is None`), called from `set_image` (unchanged) and two
   new overrides, `resizeEvent`/`showEvent` — real geometry arriving after
   `set_image` now actually triggers a refit. This is the direct fix.
2. `self._user_zoomed`, set `True` in `wheelEvent`, makes `_fit_to_view` a
   no-op; reset to `False` in `set_image`, since a new frozen plane is a
   fresh view that should fit again.
3. `setBackgroundBrush(QColor("black"))`, `setFrameShape(QFrame.NoFrame)`,
   both scrollbar policies off — matches the live preview's own
   appearance. `QFrame` added to the existing guarded PyQt5 import.
4. **Confirmed not applicable**: checked whether any of the above lands
   inside a block the CALIBRATION INTEGRATION banner (~926) already lists
   for removal — it doesn't. `_LiveMeasureCanvas` is entirely inside Part
   05's own feature, unrelated to that banner's separable calibration
   block. No banner update needed; recorded here so a future reader
   doesn't wonder whether this was missed.

**Where the build diverged from intent — the one real deviation, worth
flagging on its own**: render-check case 5's first draft followed the plan
literally — simulate a click via `mapFromScene`/`mapToScene` at two
different zoom levels and assert the round-tripped scene points (and the
um values built from them) were identical. **It failed, and the failure
was correct, not a bug in the fix**: `QGraphicsView.mapFromScene` rounds
to an integer view pixel, so a "click" at a small (mis-fitted) zoom
genuinely lands less precisely than the same nominal scene point clicked
at a larger zoom — that IS the click-imprecision mechanism phase 1's own
note describes, not a violation of transform-independence. The test was
measuring the wrong thing (click precision, which correctly *does* vary
with zoom) instead of the actual phase-1 claim (that *already-recorded*
scene coordinates measure identically regardless of the canvas's zoom at
measurement time). Rewritten to test that directly: `add_point_
programmatic` stores the exact scene coordinate it's handed, and
`build_distance_mark` run on those stored points is bit-identical across
two different zoom levels, since neither function ever reads
`self.transform()`. Same shape of finding as the onboarding-gate build's
own step 4 (a plan-literal approach revealing something the diagnosis
under-specified) — the value of a three-phase record is exactly that the
build entry can say this rather than quietly conforming to the plan's
literal wording.

**Non-goals, honored**: `set_image`/`setCurrentWidget`'s ordering in
`_on_live_measure_freeze_done` is untouched — the freeze fix's own
load-bearing invariant; no green-plane/color changes; `measure.py`/
`camera_backend.py`/`calibrate.py` untouched; capture latency unchanged.

**Render-check coverage added**: first-show fit (the direct regression
test — `set_image` before any real layout, then a forced resize/show
correctly refits rather than keeping the stale transform); repeat freeze
still fits (guards the already-working path); user zoom survives a
resize; a new `set_image` re-enables auto-fit; and transform-independent
measurement (see the deviation note above for what this actually checks).

**Verified**: full `qt_shell.py --render-check` sweep passes (exit 0),
including all five new "Live measure canvas-fit check PASS" lines.
**Explicitly not done until confirmed on-rig** — self-check cannot prove
any of: first freeze fills the frame at correct scale; the live-to-frozen
swap reads as continuous (no gray flash, no border, no visible relayout);
second and later freezes still correct after moving the stage; wheel-zoom
survives a window resize; closing and reopening the Live measure box
fits correctly on the reopened view. Carried forward to the pending bench
session as its own checklist, not treated as done:
- [ ] First freeze of a fresh session fills the frame at correct scale.
- [ ] Swap from live to frozen reads as continuous — no gray flash, no
      border, no visible relayout.
- [ ] Second and later freezes still correct, including after moving the
      stage.
- [ ] Wheel-zoom on the frozen plane, then resize the window — zoom is
      preserved.
- [ ] Close and reopen the Live measure box — canvas fits correctly on
      the reopened view.

**Store-mechanics migration (`BUILD_LIST` phase 2) — intent recorded, not
yet built.** `calibrate.py`'s calibration store and `ca_measure.py`'s CA
store are the same code twice over (objective-keyed, chronological entry
lists, `entry_id`/`supersedes` chaining, mkdir-then-atomic-write).
`annotations.py`'s store is a different shape — keyed by `pixel_sha256`,
one record per hash with an ever-growing `marks` list, no supersedes chain
at all — and shares only the outer atomic-write mechanic with the other
two. `provenance.py`'s `save_profile`/`load_profile` is a fourth instance
of that same outer mechanic.

**Decision**: two primitives in a new leaf module, `json_store.py` — not
folded into `provenance.py`, even though `provenance.py` already has one
instance of the pattern. `atomic_write_json`/`load_json_or_default` (the
generic mechanic — `provenance.py`, `calibrate.py`, `ca_measure.py`,
`annotations.py`, four consumers) and `append_to_history`/`current_entry`
(the objective-keyed supersedes-chain mechanic, pure/no I/O — `calibrate.py`
and `ca_measure.py` only; `annotations.py` never adopts this half). The
test applied is the one phase 1 already established for `FULL_MODE_LBL`:
single-consumer code tied to `Session.write` belongs in the governor
module, but a primitive with four consumers unrelated to camera sessions
is a utility library, not governor content.

Three constraints baked into the design before any code is written:
- **Path-agnostic, non-negotiable**: every path is a parameter, never a
  constant the leaf module imports or defaults itself — otherwise it
  reintroduces the exact second-binding failure the `provenance.OUT_ROOT`/
  `PROFILE_PATH` by-attribute rule exists to prevent (`render_check()`
  reassigns `PROFILE_PATH` at runtime; that only works because
  `save_profile()` reads the module global at call time).
- **`mkdir` folds into `atomic_write_json`** — all four current call sites
  repeat it immediately before their own write; leaving it outside the
  primitive is a gap a future caller can forget, same class as the
  `green_plane.tif` mkdir bug from the Export/Publish step.
- **`entry_id` generation moves into the leaf** — `uuid` leaves
  `calibrate.py`'s/`ca_measure.py`'s own call sites; saving assigns the id
  now, which stops being visible at the call site, so the primitive's own
  docstring has to say so explicitly.

Import style is hard (not the optional `debayer`/`focus`/`wizard_pages`
guarded pattern) via the same try-relative-then-bare style `ca_lib`
already uses — `json_store.py` is stdlib-only, so this doesn't cost
`calibrate.py` its "runs standalone" property. `json_store.py` gets its
own `--render-check`: the supersedes-chain half is pure and fully covered
with no disk I/O; the atomic-write half gets a real temp-path round trip.
Documented explicitly as a known gap: nothing anywhere in this project's
suite covers concurrent-writer behavior (`save_profile()`'s own docstring
already says so) — the primitive's crash-safety justification stays
unproven by self-check, single-writer only.

**Migration order**: (0) `json_store.py` itself plus re-pointing
`provenance.py`'s `save_profile`/`load_profile` at it, behavior-neutral
and verified before touching any of the three named modules; (1)
`calibrate.py` (most upstream); (2) `ca_measure.py` (identical shape); (3)
`annotations.py` last (smaller change, atomic-write half only). Each
module's existing `--render-check` keeps exercising the real
`save_calibration`/`save_ca_calibration`/`save_mark` call path throughout
— no test relocation needed.

**Not in this pass**: `plane_cache.py`, `measure.py`'s `session.json`
write, and `qt_shell.py`'s `PREFS_PATH` write duplicate the same atomic
pattern but aren't in `BUILD_LIST`'s phase 2 scope. `measure.py`'s
`DEFAULT_CAPTURES_ROOT` (see the `MeasureWindow` extraction step 2 note
above) stays parked too — this series is scoped tightly to the
store-mechanics consolidation alone, since the save paths it touches are
exactly where this project's two real data-loss incidents happened
(`PROFILE_PATH` overwrite, `annotations.json` pollution). Full design and
reasoning in `CHANGELOG.md`'s matching "Intent: Store-mechanics migration"
entry.

### Focus-aid "no real lores frames received" after changing video resolution — decode-failure guard fixed, the resolution bug itself still OPEN

On-rig report: focus aid works fine at the default video resolution, but
picking a different one in Preferences and relaunching produces "no real
lores frames received -- lores stream is not reaching the camera backend,
not a scoring bug" (`qt_shell.py`'s `_readout`). Read-only investigation
found the likely mechanism (below) but a real fix needs on-rig repro data
first — see `CHANGELOG.md`'s matching Intent/Build entries for the guard
fix itself (what's actually landed so far).

**Leading hypothesis, not yet confirmed on real hardware**: `LORES_RES`
(the focus aid's lores stream size) is pinned at construction and never
changes (`camera_backend.py`'s own comment on `Picamera2Camera.__init__`
explains this was deliberate — lores does double duty as the recording
widget's display source too, and the video-resolution menu was scoped to
"the RECORDED file's size", not this). But "Video resolution" in
Preferences only overrides the **main** stream (`video_resolution_kwargs()`,
`qt_shell.py`), and `video_resolutions` itself is deliberately built from
the IMX477's raw, unfiltered sensor modes (`camera_backend.py`'s own
`get_capabilities()`, "do not filter this list to a 'sensible' subset") —
including non-4:3 sizes. Pairing an arbitrary main size against a lores
stream nobody validated against it is a plausible way for libcamera to
accept the config at `configure()` time but then fail to actually deliver
lores frames at capture time.

**Fixed so far (self-check-verified, NOT yet on-rig)**: the diagnostic
pathway itself was broken — `_stash_lores`'s `RuntimeError` guard around
`request.make_array("lores")` couldn't tell "still-mode request, no lores
stream by design" (expected, silent) apart from "lores IS configured but
failing on every frame" (a real defect), so any genuine failure vanished
as silently as the expected case, with zero information about why. Now
`Picamera2Camera.lores_decode_errors`/`last_lores_error` capture the real
count and exception text when a failure isn't the known still-mode race
(see `_lores_error_is_expected`), and `qt_shell.py`'s `_readout` reports
that real text once it exists instead of only ever showing the generic
message. **Extended once more, same day**: the error text alone confirms
a genuine failure but doesn't settle *which* candidate mechanism is
responsible — `lores_config_at_failure` now captures `camera_configuration()`
itself (the ACTIVE config, not `request.config`, which the comment above
already found unreliable for this) once, at the first genuine failure, via
`_summarize_camera_configuration`; `_readout` appends whether `lores` is
present in that config, explicitly, alongside the streams that are. This
is the check that actually distinguishes candidate 1 (`create_preview_
configuration()` silently dropped `lores`) from anything else — a present
`lores` key would rule candidate 1 out entirely, which the error text by
itself never could.

**On-rig run happened (2026-07-26), and it changes the diagnosis**: the
screenshot showed a genuine failure (418 counted, `_lores_error_is_expected`
confirms this is not the still-mode race) at the **default** video
resolution — `gui_prefs.json`'s `video_resolution` was `null`, so
`preview_res` used its own `PREVIEW_RES=(1332,990)` default, no override
in play. Yet the captured active config read `main=640x480` — exactly
`LORES_RES`'s own size, not the `1332x990` actually requested — with
`lores` MISSING, plus an unrequested `raw=4056x3040` present (`_preview_cfg`
as written never asks for a raw plane at all). **This kills the
resolution-pairing hypothesis above outright**: it happens at the default
resolution too, so resolution was never the variable.

Two competing mechanisms remain, needing different fixes:
1. libcamera's own config validation rejects the requested lores
   format/size during negotiation and silently drops it — the RGB888
   vs. YUV420 shakeout `__init__`'s own comment already names as a
   contingency.
2. Streams are reported **positionally**, and `main` itself was the
   stream actually lost, with `lores` surviving and inheriting the
   `main` label in the reported config — under which reformatting lores
   fixes nothing, because lores was never the problem.

Dropping one stream shouldn't resize another, so (2) fits the observed
`main=640x480` better than (1) does; the unexplained `raw` entry doesn't
discriminate between them on its own (Picamera2 configs can carry an
auto-selected raw sensor mode even when not explicitly requested).

**Next, explicitly not done yet**: reproduce on-rig again with the new
diagnostic dump in place (see `CHANGELOG.md`'s matching entry) — two
`camera_configuration()` snapshots, one right after
`create_preview_configuration()` returns and one right after
`configure()` applies it. If `lores` is already absent from the first,
the loss is in Picamera2's own construction, before libcamera negotiation
ever runs (favors nothing formatted yet — a different bug than either
candidate above). If it's present in the first and gone from the second,
libcamera's negotiation is where it's dropped (candidate 1). That result
decides which fix to actually write — do not swap lores to YUV420 before
this discriminates, since candidate 2 would make that change a no-op.
**Also flagged for whoever writes that fix's own intent doc** (not
decided now): `video_resolutions` being built from unfiltered raw sensor
modes may itself be part of the problem — even with lores correctly
reconfigured, some main/lores pairings might be genuinely undeliverable,
in which case the menu shouldn't offer them at all. Whether the eventual
fix reconfigures lores, filters the menu, or both is a call the repro
should inform, not a guess made ahead of it.

### Decouple video resolution from preview — BUILT, verified on-rig

First item off a larger roadmap that came out of the on-rig focus-aid
investigation. Full plan in user-provided
`ROADMAP_resolution_sensor_calibration.md` (item 1) and
`SUPPLEMENT_for_agent_handoff.md` (§1), neither checked into the repo —
see `CHANGELOG.md`'s matching Intent/Build entries for the fuller text.
Built by a separate, since-unreachable session working directly in this
checkout; a different session (this one) took over the uncommitted
working tree, added the one missing piece (the combo's `setEnabled(False)`,
per the amendment below — the other session's draft had only added the
disclosure tooltip), verified it against real hardware, and committed it.

**Bug**: focus aid dies (silently — the `(lores MISSING)` diagnostic, not
a crash) whenever "Video resolution (next launch)" is set to a non-4:3-ish
mode. `video_resolution_kwargs()` (qt_shell.py ~426) feeds that pref
straight into `Picamera2Camera(preview_res=...)`, which becomes the
`main` stream's size. At e.g. 2028×1080 (≈1.88:1), `main`'s aspect no
longer matches the fixed `LORES_RES` (640×480, 4:3), the pairing is
rejected during `create_preview_configuration`, lores is dropped for the
rest of the process, and every `make_array("lores")` raises from then on.

**Correction to the roadmap's own first draft, worth recording so it
isn't rediscovered**: the roadmap's first pass assumed decoupling was
free because "`start_recording()` already draws its video config from
`self._video_res`." Verified against the code and found false —
`start_recording()` (camera_backend.py ~1039) never reads `self._video_
res`; it just `start_encoder()`s whatever the `main` stream already is,
i.e. `preview_res`. Not new information — the "Video resolution menu
detail worth knowing" note earlier in this file already flagged the
`__init__` comment near `self._video_res = preview_res` as stale; the
roadmap's author just didn't cross-reference it. `self._video_res`/
`set_video_resolution()` are dead code, reserved for a future
Record-button rework. So the "Video resolution (next launch)"
preference's *only* real effect today is via `preview_res`, and a naive
decouple would silently turn it into a no-op — recorded video pinned at
`PREVIEW_RES` forever, no error, no indication.

**Decision** (confirmed with the user before starting): do both halves in
one cycle rather than trade a loud bug for a silent one.
1. Remove `video_resolution_kwargs()` and its call site in `main()`'s
   `Picamera2Camera(...)` construction — `preview_res` (and therefore
   `main`, and therefore its pairing with `LORES_RES`) is fixed at
   `PREVIEW_RES` again, unconditionally. This is the actual fix.
2. Keep the "Video resolution (next launch)" combo in the Preferences
   dialog (still populated from `get_capabilities()`, still persists to
   `gui_prefs.json`, in case a future Record-button rework wires it
   through), but **disable it** (`setEnabled(False)`) with a tooltip
   explaining why, rather than leaving it live with just a disclosure
   tooltip. **Amendment, per user feedback before the build started**: an
   enabled combo that still changes, still persists, and still shows the
   user's choice back to them is a false affordance regardless of what
   its tooltip says — the user believes it worked. Disabled-with-tooltip
   ("pending Record-button rework") keeps it discoverable and signals
   it's coming back, without inviting use. `capture_format`/
   `video_format`'s existing "persisted, not yet applied" tooltip idiom
   is for controls that are merely *not wired up yet*, not one that
   *used to work and now doesn't* — this one earns the stronger
   treatment.
3. Correct the stale `__init__` comment at camera_backend.py ~665-676
   that is the actual source of the false premise — it describes the
   Record-button rework's *intended* future behavior as if it were
   current. Left as-is, it would mislead the next reader (agent or
   human) exactly the way it misled this roadmap's first draft.

**Explicitly rejected**: wiring `self._video_res` into `start_recording()`
now, to keep the preference meaningful. Encoding at a size other than
`main` means either a mode switch at record-start or a third stream —
exactly the pairing fragility that produced this bug, and the Record
button's mode-switching history has already caused a pane freeze and an
exposure shift on real hardware (see `start_recording`'s own docstring).
Out of scope for this cycle.

**Consequence to carry forward**: once roadmap item 2 (a stream-resolution
setting) lands, *stream* resolution becomes the real control over
recorded video size, since the encoder always takes whatever `main`
currently is. Video resolution stays a persisted-but-inert preference
until the Record button itself is reworked.

**Real user-visible regression, worth naming explicitly (per the same
user feedback above) — this is more than a control going inert**:
anyone who had already set Video resolution to something other than
`PREVIEW_RES` (e.g. 2028×1080) will find their recorded video silently
drop back to `PREVIEW_RES` (1332×990) the next time they launch after
this lands — not merely "the preference stops responding to new
choices," but "an existing choice silently stops taking effect." Worth
knowing before a recording session, not discovering after one. Not
hypothetical for the rig this was verified on: `gui_prefs.json` there
already had `video_resolution: [2028, 1080]` persisted from before this
fix landed.

**Landed exactly as planned, no deviations**, except the missing
`setEnabled(False)` (see above) added by the session that finished and
verified it. `video_resolution_kwargs()` and its call site are gone;
`preview_res` is unconditionally `PREVIEW_RES` again; the `__init__`
comment at camera_backend.py is corrected; the Preferences combo is
disabled with a tooltip. The dead render-check block that exercised
`video_resolution_kwargs()` directly was removed rather than kept as a
no-op.

**Verified**: full 16-module `--render-check` sweep passes, no
regressions. **On-rig, this session**: with `video_resolution: [2028,
1080]` still persisted (the exact non-4:3 shape that used to break the
pairing — see above, this rig's own `gui_prefs.json` already had it),
`qt_shell.py --camera` was run twice; `camera_configuration()` at both
diagnostic checkpoints in `Picamera2Camera.__init__` showed `main` at the
correct `1332x990` and `lores` present and correctly sized at `640x480`.
The preference can no longer reach the pairing at all, so it can't break
it. The reported bug — focus aid dying on a non-default video-resolution
preference — is fixed. The Preferences dialog's disabled state was not
independently eyeballed on-screen this session (no screenshot taken); the
code path (`setEnabled(False)` unconditionally in `_video_res_combo`'s own
construction) has no branch that could make it otherwise.

### Fix: `Picamera2Camera` construction left the camera in the `sensor_modes` probe's leftover config — BUILT

Root-caused from an on-rig failure log the user supplied directly (not
found by this session): live-measure's freeze capture came back with
`main=640x480@XBGR8888, raw=4056x3040@SBGGR16` and no lores stream at all
(`KeyError`-shaped failure — `lores` genuinely absent from the request).
An earlier theory blamed a main/lores aspect-ratio mismatch; the user
disproved it by reading the log's own libcamera stream-negotiation trace,
which showed a sweep — `(0) 640x480-XBGR8888/sRGB (1) <size>-RAW` — cycling
the raw stream through every sensor mode while main stayed fixed. That
sweep's exact last line matched the failure's config byte for byte,
including the `SBGGR16` format nobody had asked for: the camera was still
sitting in `get_capabilities()`'s own probe configuration, not anything
the app had configured.

**Root cause**: `self._picam2.sensor_modes` (read inside
`get_capabilities()`, cached in `PLAN_fix_capabilities_cache.md`'s fix —
see the "Fix: Preferences dialog crash" entry above) is not a passive
lookup; internally it calls `Picamera2.configure()` once per sensor mode
to enumerate them, sweeping the camera through every mode and leaving it
in whatever the LAST swept mode was. `Picamera2Camera.__init__` called
`self._picam2.configure(self._preview_cfg)` (the real config, with lores)
*first*, then built the `QGlPicamera2` widget against it, and only
afterward — at the very end of `__init__` — called `get_capabilities()`
to prime the cache. That ordering meant the sensor-mode sweep ran last,
silently clobbering the real preview config the widget had already been
built against, and nothing ever re-applied `self._preview_cfg` afterward.
`main` reads 640×480 because that's the probe's own fixed placeholder
size, not anything requested; `lores` is missing because the probe never
asks for one. The Preferences-dialog crash fix (above) introduced the
caching that made this ordering possible — the underlying sensor_modes
sweep behavior isn't new, but eagerly calling it at all inside `__init__`
is what exposed this.

**Fix**: moved the capability probe (`self._capabilities = None` +
`self._capabilities = self.get_capabilities()`) to run immediately after
`self._picam2 = Picamera2()`, before `self._preview_cfg` is even built,
let alone applied. The real `self._picam2.configure(self._preview_cfg)`
call and the `QGlPicamera2` widget construction now both happen strictly
after the sensor-mode sweep has already run and settled, so they're the
last thing to touch the camera's configuration during construction — the
sweep's leftover state never survives past this point. `get_capabilities()`
itself is unchanged; only when it's called moved.

**Not yet fixed, flagged by the user, worth checking next**: a second,
distinct bug — a `G_IS_OBJECT` assertion at the end of the same failure
log, suggesting the probe's own teardown (or something downstream of it)
isn't clean. Different mechanism from this one; this fix does not close
it. An earlier session's report of "the second bug is probably a phantom"
was itself wrong per the user — real, just not yet root-caused.

**Verification**: `camera_backend.py`'s self-check (`FakeCamera`-only)
still passes after the reorder — see "Full `--render-check` sweep" above.
The ordering bug itself is only reachable through `Picamera2Camera`, which
needs a real `Picamera2()` plus a `QGlPicamera2` widget to construct at
all, so this fix is **not yet confirmed on-rig**. No new tooling needed
for that confirmation — Picamera2's own logger already prints the active
stream configuration at construction and on every `configure()` call
(this is literally the log the original failure and the diagnostic sweep
above were both read from); launching the app and reading that existing
output is the whole check:
- [ ] At default `PREVIEW_RES`/`LORES_RES`: confirm the post-construction
      log shows `lores` present at `LORES_RES` and `main` at
      `PREVIEW_RES` — not the probe's leftover config (small placeholder
      main, no lores, an unrequested raw format) — before `start()` is
      even called.
- [ ] **Also test at the non-default video resolution currently
      persisted in prefs, `[2028, 1080]`** — this is the exact
      combination the original failure report came from. Same check:
      `lores` present, `main`/`raw` match `self._preview_cfg`, not the
      probe's leftover state.

### `_resolution_combo()` fallback fix — BUILT

Found in passing while investigating a user-reported roadmap item (a
preview-resolution setting — its own separate section below), not that
work's subject; independent of it and predates it. Applies to every
resolution combo the Preferences dialog builds through this one shared
helper (capture/video/stream today; any future one), not just the control
it happened to be noticed against.

**Defect**: `_resolution_combo()` can only display a value that's also in
the driver-reported list it's built from. A persisted preference outside
that list (a discrete, sensor-mode-derived list — e.g. `video_resolution`
persisted as `[2028, 1080]`, not an actual IMX477 sensor mode) silently
rendered as "Default (current preview)" instead of the true stored value.
Worse: Preferences' OK button unconditionally re-saves every next-launch
combo's `currentData()`, so simply opening Preferences and pressing OK —
regardless of whether that control was touched — could silently overwrite
a real persisted preference with `null`.

**Fix**: `_resolution_combo()` now prepends the persisted value as its own
selectable entry when absent from the reported list, instead of falling
back to "Default". Displays honestly, round-trips through OK unchanged.

**Verified (self-check only, not on-rig)**: render_check persists
`video_resolution` as `[2028, 1080]` (confirmed absent from `FakeCamera`'s
own `video_resolutions`), constructs `PreferencesDialog`, confirms the
disabled Video resolution combo shows `(2028, 1080)`/"2028x1080" rather
than Default, and that OK persists it unchanged. Full 16-module
`--render-check` sweep passes, no regressions.

**On-rig verification NOT done**: confirm against real (non-`FakeCamera`)
sensor-mode data — the disabled Video resolution combo should show its
true persisted value rather than "Default" whenever that value isn't one
of the real reported sensor modes.

### Preview resolution setting (ROADMAP item 2, REVISED) — BUILT, self-check only, NOT yet on-rig

User-provided, twice-revised brief (`ITEM2_preview_resolution_brief.md`,
not checked into the repo). Built directly from the handed-over brief, no
separate intent commit first — this project's usual intent → build →
record convention applied retroactively in the record rather than
manufactured as a commit that never happened (same call as the lores
diagnostic). Full narrative in `CHANGELOG.md`'s matching entry; this
section is the load-bearing summary for a fresh agent. The
`_resolution_combo()` fallback fix directly above is a separate commit —
an unrelated defect found while investigating this brief, not part of it.

**Naming**: the setting is `preview_resolution`
(`preview_resolution_kwargs()`, mirrors `capture_resolution_kwargs()`),
**never** `stream_resolution` — a dormant `stream_resolution` pref already
exists (the `stream_formats`/`stream_resolutions` combo,
~qt_shell.py 1720-1723/1966-1967), reserved for a future network streaming
server this backend doesn't implement. Caught before any code was written;
do not let the two collide in a future session either.

**What it does**: a real, enabled "Preview resolution (next launch)"
control in Preferences, built from `get_capabilities()`'s unfiltered
`video_resolutions` (no aspect filter — deliberate, per the brief's
"build unfiltered, then test, then filter only if the test demands it").
Feeds `preview_res` at `Picamera2Camera` construction, same shape as
`capture_resolution_kwargs`/`full_res`. Per the same reasoning as "Video
resolution menu detail worth knowing" above: since `start_recording()`
always encodes whatever "main" (== `preview_res`) is, **preview resolution
is now the real, live control over both the preview AND recorded video
size** — video resolution stays inert until a future Record-button rework.

**Lores now derives from preview_res's own aspect**, not a fixed 4:3
constant: `camera_backend.derive_lores_res(preview_res, target_pixels=...)`
computes an even-dimensioned lores size at roughly the old `LORES_RES`
pixel count, matching whatever aspect `preview_res` has.
`Picamera2Camera.__init__`'s `lores_res` parameter is now `None`-default
and derives via this function unless explicitly overridden. **This means
`Picamera2Camera`'s lores size is per-instance now, not always the
`LORES_RES` module constant** — a real behavior change every place in
`qt_shell.py` that read the bare constant needed checking against. Fixed:
`FocusPreviewWindow.__init__`'s `_aspect`/`_ov_bufs`, `lores_point_from_
preview_click()` (gained a `lores_res` parameter), `_live_measuring_view_
point()` — all now call the new `camera.lores_resolution()` accessor
(added to `CameraBackend`/`FakeCamera`/`Picamera2Camera`) instead of
reading `LORES_RES` directly. If you add a NEW place that draws into or
converts a click against the live lores frame, it must call
`camera.lores_resolution()` too, not the module constant — a self-check
using only a default-constructed `FakeCamera` cannot catch a regression
here, since its `lores_resolution()` happens to equal `LORES_RES` anyway
(see this project's own `PHILOSOPHY.md` rule on why the render_check
coverage for this uses an explicit non-default override instead).

**Ruler overlay, traced per the brief's own instruction**: tick generation
(`_current_ruler_ticks`) depends only on `GREEN_PLANE_RES` × calibration's
`um_per_px`, never on `preview_res`/lores size; drawing
(`_draw_ruler_ticks_into`) uses the overlay buffer's own actual shape. So
once lores/`_ov_bufs` are correctly sized (above), tick placement is
aspect-independent by construction — no bug found in this path. **What
tracing genuinely could not settle**: whether a non-4:3 `preview_res` on
real IMX477 hardware preserves the same physical field of view (resampled
to different pixel dimensions) or crops it. That's a hardware behavior
question pure code reading cannot answer — on-rig test item below. **This
is worth restating precisely, since it matters for a possible future
filter**: if a wide preview turns out to crop rather than preserve FOV,
that's a real, different argument for restricting the resolution menu —
the user sees less specimen at a wide preview resolution — distinct from
(and better than) the roadmap's original, disproven aspect-pairing theory.
Only the rig can settle which situation is actually true.

**Verified**: full 16-module `--render-check` sweep, no regressions. New
coverage: `derive_lores_res`/`lores_resolution()` (camera_backend.py);
`preview_resolution_kwargs()`; the Preferences dialog's new combo;
`FocusPreviewWindow`'s dynamic `_aspect`/`_ov_bufs`/click round-trip
against a non-default `lores_res` (proves the wiring, not just the
default case).

**On-rig verification explicitly NOT done — needed before this is
trustworthy**:
- [ ] Set Preview resolution to **2028×1080** (non-4:3), confirm focus aid
      still scores and `lores` is present in the active config (the
      brief's own build-order step 2 — the whole point of this change).
- [ ] A normal launch at a 4:3 non-default preview resolution: confirm
      preview, focus aid, AND the ruler all read correctly — this is what
      actually settles the FOV-preservation question above, which code
      tracing alone could not answer, and with it whether a future aspect
      filter is actually warranted (for the FOV reason above, not the
      disproven pairing one).

### Qt environment defaults, platform-conditional — BUILT, CONFIRMED on-rig, 2026-08-02

`qt_shell.py`'s module-level environment-defaults block used to run
unconditionally; it's now gated on `sys.platform.startswith("linux")`
(`qt_shell.py:97-99`). The existing `QT_QPA_PLATFORM=xcb` setdefault
moved inside that gate, and a new `QT_QPA_PLATFORMTHEME=gtk3` setdefault
joined it. Root cause: `qt6-gtk-platformtheme` is installed on the rig,
but labwc doesn't advertise itself in a way Qt maps to `gtk3` on its
own, so `QT_QPA_PLATFORMTHEME` never got a value and Qt fell back to its
own built-in default font — and since this app's layout is entirely
font-metric-driven (no `setFont`/`QFont`/`setPointSize` anywhere), the
whole UI rendered smaller than the rest of the desktop. Both stay
`setdefault`, so a desktop that already exports `QT_QPA_PLATFORMTHEME`
(KDE, for instance) is unaffected, and Mac/Windows never enter the
Linux-only branch at all.

**Confirmed on-rig, 2026-08-02**: `env -u QT_QPA_PLATFORMTHEME python3
qt_shell.py` renders the UI at correct size, with the Linux-gated
setdefault doing the work rather than an exported shell variable. Theme
selection still works; `discover_themes()` unaffected. Real camera path
confirmed separately, not `FakeCamera`: preview streams, focus aid live
and scoring, ROI draws, Reprobe returns sane exposure. **Not confirmed
on macOS or Windows** — the non-Linux branches, where neither setdefault
runs, remain untested.

See `CHANGELOG.md`'s "Qt environment defaults" series for the full
intent/build/record-build/confirmation record, including a correction to
the intent entry's baseline (it was sandbox-measured under Xvfb, not the
rig's — the real gap on the tablet was far larger) and a discovered
palette effect: applying `gtk3` changes more than font metrics, it
changes the app's palette too, because the QSS themes
(`themes/*/style.qss`) only override part of the palette and `gtk3`
fills in the rest on Linux — worth knowing before the macOS/Windows
work, where no platform theme gets set at all and the app's appearance
will be the QSS over whatever those platforms supply underneath.

**Superseded, 2026-08-06: the plain `setdefault` above stopped working
once `/usr/bin/setup_env` (package `raspberrypi-ui-mods`, sourced
unconditionally at session start) started exporting
`QT_QPA_PLATFORMTHEME=qt5ct` ambiently — `setdefault` never overrides an
already-set value, so it silently became a no-op, and `qt5ct` has no Qt6
build on this rig, so Qt6 fell back to its own built-in default font
again with no warning. Two more fixes followed on branch `claude/qt-
platformtheme-plugin-check`, in order:**

1. **2026-08-05, since corrected: clear-only.** `qt_shell.py` gained
   `_qt6_plugin_keys`/`_qt6_platformthemes_dirs` (parse a Qt6
   platformtheme plugin's `.so` for its registered CBOR-encoded "Keys"
   metadata, without importing PyQt6 before `QApplication` — an earlier
   sub-version of `_qt6_platformthemes_dirs` used
   `QLibraryInfo.path()`, but merely importing `PyQt6.QtCore` before
   `QApplication` exists was enough for Qt to snapshot
   `QT_QPA_PLATFORMTHEME` internally, so a later `os.environ` write had
   nothing left to affect — fixed by finding the plugin directory via a
   glob instead) and `_clear_unloadable_platformtheme` (verify the
   ambient value names an installed, loadable plugin; if not, clear it
   so Qt would auto-detect the one that does exist, on the theory that
   clearing alone would trigger that auto-detection). **That theory was
   wrong.** It shipped with an on-rig measurement in the code comment
   (unset = 18.0pt PibotoLt, matching the desktop) that a fresh 2026-08-06
   on-rig re-run could not reproduce (see step 2) — see the
   "unexplained divergence" paragraph below before trusting either
   reading blindly.
2. **2026-08-06: verified-set, confirmed on-rig.** A plain launch (no
   environment manipulation), ambient `QT_QPA_PLATFORMTHEME=qt5ct`
   confirmed present in the shell beforehand, still rendered the broken
   9.0pt "Sans Serif" fallback with the var cleared/unset —
   `QT_DEBUG_PLUGINS=1` showed why: Qt's factory loader finds
   `libqgtk3.so` on disk (`"Got keys from plugin meta data ... gtk3"`)
   but never calls `create()` on it, because Qt only auto-instantiates
   an available-but-unnamed theme plugin when `XDG_CURRENT_DESKTOP`
   matches a short internal list Qt ships
   (`QGenericUnixTheme`'s desktop-name heuristics), and this rig's value
   (`labwc:wlroots`, from `XDG_SESSION_DESKTOP=LXDE-pi-labwc`,
   `XDG_SESSION_TYPE=wayland`, session wrapper `lightdm` → `labwc`) is
   not on it. `_clear_unloadable_platformtheme` was rewritten as
   `_ensure_loadable_platformtheme`: same verified-plugin-existence
   check as before, but now it explicitly **sets**
   `QT_QPA_PLATFORMTHEME=gtk3` when the current value is missing or
   unloadable — never a blind hardcode, only ever after independently
   confirming (same CBOR parsing) that a plugin actually registers the
   `gtk3` key. **Confirmed on-rig, 2026-08-06, plain launch, no
   environment manipulation**: `PibotoLt 18.0`, matching the rest of the
   desktop — the acceptance test this whole fix is judged against.
   `qt_shell.py --render-check` re-run clean, no regressions.

**Divergence explained, 2026-08-06 (follow-up session).** The
"`QT_QPA_PLATFORMTHEME` unset = 18.0pt PibotoLt" reading and the later
"9.0pt Sans Serif" reading for the nominally same `env -u
QT_QPA_PLATFORMTHEME` command were both captured by the *same* prior
session (transcript
`~/.claude-agent2/projects/-home-bwann83-imx/8d430360-...jsonl`), in the
*same shell, same boot* — no reboot between them. It was never about the
reboot, the desktop session, or `XDG_*` hints. It was about which code
`import qt_shell` ran: the first (18.0pt) reading, at that transcript's
line 1593, ran against **old `main`-branch code** (this branch's `git
checkout -b` happens later, at line 1636) — old `main` had
`os.environ.setdefault("QT_QPA_PLATFORMTHEME", "gtk3")`. `env -u`
genuinely stripped the var from that process's environment before Python
started, so `setdefault` had nothing blocking it and set `gtk3`
successfully (the transcript's own script even printed `"after import:
gtk3"`). This was never Qt auto-detecting anything. Once this branch's
first fix attempt rewrote the function to *only clear* an unloadable
value, the identical shell-level `env -u` test no longer went through
any `setdefault("gtk3")` call — clearing an already-absent var is a
no-op — so Qt fell back to its own built-in default, giving the 9.0pt
reading. Directly reproduced read-only, without switching branches:
running the literal old `setdefault(..., "gtk3")` line under `env -u
QT_QPA_PLATFORMTHEME` reproduces `18.0pt PibotoLt` exactly; stripping
`QT_QPA_PLATFORMTHEME`/`XDG_CURRENT_DESKTOP`/`XDG_SESSION_DESKTOP`
together with no `qt_shell` import at all still gives `9.0pt Sans
Serif` — confirming Qt itself never auto-detects `gtk3` here under any
tested condition, only an explicit `QT_QPA_PLATFORMTHEME=gtk3` (however
it gets set) works. The fix on this branch as of the
`_ensure_loadable_platformtheme` rewrite above already does the right
thing — it explicitly sets `gtk3` (verified present) rather than only
clearing, which is exactly the mechanism that produced `18.0pt` both
times it ever worked.

### Keep RAW Images narrowed to raws only — BUILT (data-loss fix), self-check only

Own branch: `claude/keep-raw-images-scope-fix`, off `main` (fourth
sibling alongside `claude/hdr-merge-verification-w7sb22`, `claude/frame-
average-sidecar-wiring`, and `claude/white-level-constant-consolidation`
— not stacked on any of them). Full investigation and the build are in
`CHANGELOG.md`'s 2026-08-03 "Keep RAW Images narrowed to raws only"
entry; see also the correction to the "Provenance relocation, Keep RAW,
and auto-processing" section above, which described the old (buggy)
behavior as the design.

**The bug**: "Keep RAW Images" off deleted `master_N.tif`/
`hdr_linear.tif`/`single_master.tif` — the averaged/merged
intermediates — not only the raw frames the setting's name promises. A
user leaving it off was consenting to discard raws, not derived outputs
built from a multi-frame bracket. Real data loss: those intermediates
are not re-derivable without re-shooting once the raws behind them are
also gone.

**Two adjacent findings from the investigation, NOT fixed (out of
scope)**: `archive_raws()` (a different, separately-named feature,
"Archive raws") globs by raw extension — off-rig (`--raw-ext tif`) it
would also match every processed `.tif` output in the same directory,
since raws and outputs share an extension there off-rig. Currently
unreachable via the GUI (`qt_shell.py` always passes `--keep-raws`);
only reachable via a direct CLI run. Nothing in the code guards a
separately-launched `process_wizard.py` or Gallery view from reading a
capture's files concurrently with, or right after, an auto-process
worker thread's deletion — narrow, real, not addressed here.

**The fix**: `hdr_from_session.py:process()`'s deletion loop no longer
includes `master_files` — only `raw_files` are ever deleted by this
setting. `correction_status` (persisted onto the capture's own
`session.json` entry) now carries two new unconditional keys,
`derived_outputs_discarded` (always `False`) and `derived_outputs_note`,
matching `frame_average.py`'s/`hdr_merge.py`'s explicit-value-plus-note
provenance convention — so a reader never has to infer "were
intermediates kept?" from `raw_discarded` alone. `raw_discard_reason`'s
text no longer falsely claims the master was discarded too.
**Deliberately not added**: any new setting for discarding derived
outputs — if disk pressure ever makes that wanted, it needs its own
explicitly-named control and its own decision.

**Verification, stated honestly**: `python3 -m py_compile` passes for
both files. `hdr_from_session.py` was statically checked for real (the
old buggy deletion expression is gone, the new fields exist, the false
reason-text clause is gone) — no live functional exercise was run, and
none was attempted even with placeholder/non-image files, since this
task's own "no synthetic data" instruction was read as covering any
fabricated on-disk stand-in given the task is specifically about file-
deletion safety. `qt_shell.py`'s `render_check()` Keep RAW Images block
was corrected to assert the new behavior but not run — no PyQt6/numpy
in this sandbox, the same constraint every task on this repo has hit.
No existing user data was touched, migrated, or deleted by this work.

### hdr_merge.py provenance-integrity fixes (six defects) — BUILT, self-check only, NOT yet run on real hardware

Own branch: `claude/hdr-merge-verification-w7sb22`. `hdr_merge.py` is now
`__version__ = "1.1"` (was `"1.0"`) — six defects found by hand-auditing a
real 5-frame bracket run's embedded provenance JSON against the actual
bracket data (on the Pi only, not in this checkout). Full reasoning,
measured baseline, and the six-defect list are in `CHANGELOG.md`'s
2026-08-03 intent/build entries. Merge math is untouched by all six.

**What changed, briefly** (see `CHANGELOG.md` for the why): `metadata=None`
added to the `imwrite` call plus a new `_assert_single_description_tag()`
that re-opens the written file and hard-fails if TIFF tag 270
(ImageDescription) isn't exactly one — fixes a real duplicate-tag bug, not
just the symptom. `-o`'s value is now resolved to an absolute path before
being recorded in its own provenance, instead of the raw possibly-stale
CLI string. Five new optional CLI flags — `--white-level-source`,
`--analogue-gain`, `--black-note`, `--channel-layout {mosaic,mono}`,
`--cfa-pattern` — record operator-supplied context that the file's own
bytes structurally can't prove (mosaic vs. mono, why a black level of 0.0
is real vs. never-implemented, what gain a white-level cutoff is only
valid for); every one is `null` in the provenance JSON when omitted,
never silently guessed or omitted from the record.

**The capture-metadata propagation gap (defect 6) is real, in-repo, and
deliberately NOT fixed here.** `camera_backend.py` and `provenance.py`
already capture and persist `AnalogueGain`/`ExposureTime` per frame into
each capture's own `.meta.json` sidecar (`record_capture`/`record_burst`/
`record_hdr`) — confirmed by reading the code, not assumed. The actual
gap is `frame_average.py`: its own provenance dict (`frame_average.py`
~321-412) has no gain/sensor-mode/capture-time fields at all and never
reads those sidecars. `hdr_merge.py` now has the read side ready
(`try_read_embedded_capture_meta()`, looking for `analogue_gain`/
`sensor_mode`/`capture_time_utc` in a master's own embedded JSON) so the
day `frame_average.py` starts writing those three keys, every exposure
record in `hdr_merge.py`'s output picks them up with zero further change
here. **Backlog item, not scoped or started:** teach `frame_average.py`
to read the per-frame `.meta.json` sidecars its own inputs came from and
stamp `analogue_gain`/`sensor_mode`/`capture_time_utc` into its own output
provenance under those exact key names.

**Verification, stated honestly**: `python3 -m py_compile hdr_merge.py`
passes. No real bracket exists in this checkout (the 5 masters live only
on the Pi) and no synthetic bracket was fabricated to exercise this fix —
a passing synthetic run would only prove the code agrees with the numbers
used to derive the fix, and a fabricated bracket in a captures-shaped
path is itself a provenance contamination risk. `numpy`/`tifffile` aren't
even installed in this checkout, so a runtime smoke test wasn't possible
here regardless. Real verification — the merge actually running, the
saturation-rejected count going nonzero, the real embedded JSON, tag 270
confirmed against a real file — is the user's to run on the Pi.

### white_level defaults consolidated + sigma-clip/raw-retention/UTC-anchor investigation — BUILT (constant only), self-check only

Own branch: `claude/white-level-constant-consolidation`, off `main` (a
third sibling alongside `claude/hdr-merge-verification-w7sb22` and
`claude/frame-average-sidecar-wiring`, not stacked on either). Full
investigation and the build itself are in `CHANGELOG.md`'s 2026-08-03
entries; three findings worth keeping visible here:

1. **`frame_average.py`'s `--sigma-clip` can reject genuine unclipped
   samples and keep a clipped cluster as "the population"** — confirmed
   against the actual formula (single-iteration mean/sd over ALL frames,
   never refined) and verified numerically. Defaults OFF (`None`), and
   no caller in this repo ever passes it — inactive in the real pipeline
   today. Not fixed (out of scope for that task; no saturation rejection
   was added to `frame_average.py`).
2. **`hdr_from_session.py`'s "Keep RAW Images" off deletes `master_N.tif`
   too, not just the raws** (`hdr_from_session.py:283-303`,
   `a.delete_raw_on_success`) — whether a given bracket's masters/raws
   still exist depends entirely on that session's own setting, which is
   Pi-side state invisible from this repo.
3. **A monotonic→UTC anchor for `capture_time_utc` needs a paired
   reading taken once (near where the camera starts) and carried forward
   via `session.json`, with the actual `SensorTimestamp` conversion done
   upstream in `provenance.py` at sidecar-write time** — design only, not
   built; `frame_average.py`'s existing `--sidecar-dir` wiring already
   picks up a real `capture_time_utc` the moment a sidecar carries one.

**Built**: `hdr_from_session.MERGE_WHITE_LEVEL_DEFAULT = 65520` (one
definition, with the comment recording it's a container-range assumption
vs. the real ~61000 measured ceiling at an unrecorded gain — see
`hdr_merge.py`'s `white_level_gain_dependency` field). `qt_shell.py`
imports it via a new guarded import (matching the existing
`_process_wizard`/`_plane_cache` pattern) instead of keeping its own
independent `65520` literal. `process_wizard.py`'s unrelated
`DEFAULT_WHITE_LEVEL` (feeds `debayer.py --assume-linear`, a different
codepath) is untouched. Verified for real, not just `py_compile`:
`hdr_from_session.py` has no non-stdlib dependencies, so it was actually
imported in this sandbox and its constant/`--wl` default confirmed to
resolve and stringify identically to the old literal.

### frame_average.py capture-metadata sidecar wiring — BUILT, self-check only, NOT yet run on real hardware

Own branch: `claude/frame-average-sidecar-wiring`, off `main` (a sibling
to the finished `claude/hdr-merge-verification-w7sb22`, not stacked on
it). `frame_average.py` is now `__version__ = "2.2"` (was `"2.1"`). Full
investigation (the `--white-level 65520` caller locations, whether
`frame_average.py` averages saturated samples, and the exact sidecar
naming/keys) and the build itself are in `CHANGELOG.md`'s 2026-08-03
entries.

**New `--sidecar-dir DIR` flag** carries `analogue_gain`/`sensor_mode`/
`capture_time_utc` from the science burst's own `.meta.json` sidecars
(`provenance.py`'s naming: `<sidecar_dir>/<raw_frame_stem>.meta.json`)
into this tool's own output provenance, under the exact key names
`hdr_merge.py`'s existing `try_read_embedded_capture_meta()` already
reads back out of a master. `frame_average.py` does not import
`provenance.py` — it only replicates the naming, staying usable with any
camera that writes TIFF frames. `None` by default, so every existing
invocation is byte-for-byte unaffected.

**Two fields are null by design, not by bug, and stay that way until
something upstream of this file changes:** `sensor_mode` is not a field
libcamera's per-frame `capture_metadata()`/`get_metadata()` ever carries
on either the real (`camera_backend.py:1205`) or fake (`camera_backend.
py:498-508`) backend — confirmed by reading both, not assumed — so this
is a `camera_backend.py` gap, not something `frame_average.py` reading
harder can fix. `capture_time_utc` is null because the sidecar's own
`SensorTimestamp` is a monotonic hardware clock with no recorded epoch,
not wall-clock UTC; mapping it to a field named `..._utc` would be a
fabricated value, so it isn't done. `analogue_gain` is the one field that
actually resolves today when a real sidecar directory is given.

**Disagreement across a burst's frames is recorded, never silently
resolved to the first value** — `aggregate_capture_field()` returns
`None` plus every distinct value it saw, in frame order, whenever a
burst's sidecars don't agree. A missing sidecar (frame has none) simply
doesn't vote; it is not treated as a disagreement of its own.

**Backlog items surfaced by this investigation, not started here:**
1. `qt_shell.py:5744` and `hdr_from_session.py:362` independently
   hardcode `--wl`/white-level default `65520` — two copies of the same
   magic number, no shared constant. Not touched (out of scope; only
   `frame_average.py` was in scope for this task).
2. `frame_average.py`'s default averaging path has no saturation
   awareness at all — every frame's raw value is summed unconditionally,
   and `dtype_max()` only knows the container max (65535 for uint16),
   never the sensor's real white level. `--sigma-clip` is a statistical
   outlier rejection, not a saturation rejection, and only incidentally
   catches a clipped frame. No per-frame maximum or saturation count is
   recorded anywhere. This is consistent with (not proven to be) the
   smearing mechanism behind the `hdr_merge.py` white-level fix
   (`claude/hdr-merge-verification-w7sb22`) — averaging a clipped sample
   with unclipped ones lands the mean between them, a soft rolloff
   rather than a hard cutoff. **Deliberately not fixed in this pass**:
   changing the averaging stage would invalidate the knee measurement
   that fix's white_level was derived from, and the sequencing of that
   change belongs to the user.
3. `camera_backend.py` records no per-frame sensor-mode identity at all
   (see above) — needed before `sensor_mode` can ever be non-null
   anywhere downstream.

**Verification, stated honestly**: `python3 -m py_compile frame_average.py`
passes. No real bracket or sidecar data exists in this checkout (the Pi
is unreachable) and none was fabricated. `aggregate_capture_field()`'s
three branches (agree / disagree / absent) were exercised against
hand-built dicts in a throwaway process — logic-only, no files, no real
sidecar or capture data. Real verification (a real `--sidecar-dir`
resolving against real sidecars, a real disagreeing burst reported
correctly) is the user's to run on the Pi.

### Stale help text fixed, orphaned preview .jpgs cleaned up — BUILT (docs + narrow cleanup), self-check only

Own branch: `claude/keep-raw-images-scope-fix-cleanup`, off `main` (fifth
sibling alongside `claude/hdr-merge-verification-w7sb22`, `claude/frame-
average-sidecar-wiring`, `claude/white-level-constant-consolidation`, and
`claude/keep-raw-images-scope-fix` — all four now merged into `main`; this
one lands last, via PR, after a local rebase onto the resulting `main`).
Full investigation and the build are in `CHANGELOG.md`'s 2026-08-03
entries.

**Branch-sequencing dependency, resolved**: this section originally
warned that this branch's `--delete-raw-on-success` help text describes
a narrower contract than this branch's own (untouched, out-of-scope)
`process()` code delivered on its own. That gap is closed:
`claude/keep-raw-images-scope-fix` landed first (see the section above),
and this branch was rebased onto the resulting `main` before merging, so
the code this branch's own commits sit on top of already deletes only
`raw_files`, never `master_files` — the docs and the code agree.

**Audit finding (item 1, not fixed here, code untouched)**:
`qt_shell.py:_open_gallery_browser`'s own comment claims opening it
"cannot race a capture in progress" because it's modal — this conflates
a modal dialog (blocks other Qt actions) with a background worker thread
(auto-process's deletion, deliberately NOT blocked by the Qt event
loop). `_open_processing_wizard`, right next to it, correctly guards
with `self._capturing`; Gallery doesn't. Real, reported, not built.

**Built**: the deletion loop's `raw_files` iteration now also checks
each raw's own `.with_suffix(".jpg")` sibling (the preview
`Picamera2Camera._save_still_request` writes per frame; `FakeCamera`
never produces one) and unlinks it alongside the raw — scoped to exactly
the raws this run selected, never a second glob, never touching
`master_files`. Fixes a real, silent leftover: nothing else in the
codebase ever cleaned these up.

**Deferred, design only, per instruction**: whether `final.tif` can ever
record its own retention state (two designs written up in `CHANGELOG.md`
— defer-the-embed vs. record-the-decision-before-deletion, with the
tradeoff that the second one embeds intent rather than a confirmed
outcome), and a guard against Gallery/`process_wizard.py` reading a
capture's files while an auto-process worker thread is mid-deletion. Both
decisions are the user's.

### Gallery race comment corrected to a stated contract — BUILT (comment only), self-check only

Own branch: `claude/gallery-race-comment-fix`, off the updated `main`
(after `claude/keep-raw-images-scope-fix` landed via PR #9). Full
investigation and the build are in `CHANGELOG.md`'s 2026-08-03 entries.

**The fix**: `_open_gallery_browser`'s own comment used to claim it
"cannot race a capture in progress" because it's modal — a real reasoning
error, not just stale prose: a modal dialog blocks other Qt *GUI*
actions, not a background *worker thread*, and auto-process's deletion
runs on a worker thread specifically so it does NOT block the Qt event
loop. Replaced with a `# CAVEAT:` stating the actual situation:
unguarded, can race the worker thread's own deletion loop (raws + their
preview `.jpg`s), TOCTOU on listing-then-open. No guard added — that
decision (`_open_processing_wizard`'s existing coarse `self._capturing`
check, reused, vs. a finer per-capture check against the already-tracked
`self._last_process_session_dir`/`_last_process_index`) is the user's.

**Also reported, design only, not built**: a re-tested, lower cost
estimate for embedding a confirmed retention fact into `final.tif`
before deletion (`debayer.py`'s inputs never touch raws, so reordering
it after deletion is cheaper than originally estimated — only DNG
export's non-merge path needs a raw file, and it can simply stay ahead
of deletion); a third design (a supersede-after-the-fact record, keyed
per capture, using the same append-only pattern `calibrate.py`'s
calibration store already proves out) with its own real costs
(no per-capture key exists yet; the generalized store module isn't built
yet either) and a real discoverability gap if it lands as a sidecar
rather than in the file. All three compared on whether the artifact's
own embedded claim can ever be false — see `CHANGELOG.md` for the full
comparison. None of this is built; the choice among them is the user's.



## Things that will bite you if you don't know them

**`qt_shell.py`'s `render_check()` now monkeypatches `PROFILE_PATH` for
its ENTIRE duration, and that's load-bearing — don't remove it.**
`save_profile()` writes the SHARED, single `~/imx/profile.json` (real
hardware exposure/gain/WB data), and `FocusPreviewWindow.__init__` calls
it via a probe-and-save fallback whenever `load_profile()` doesn't find a
profile — which happens on every `FocusPreviewWindow` construction in the
self-check if `PROFILE_PATH` isn't redirected first. Real hardware
profile data got silently overwritten with fake `FakeCamera`-probed
values TWICE this session: first explained by two overlapping
`--render-check` processes racing a read against a write (fixed by making
`save_profile()` itself atomic — temp file + `os.replace`, matching every
other store here), then a SECOND time, sequential, no concurrency
involved, that never reproduced reliably enough to pin to a specific
cause. Given that, the fix is no longer "be careful running things
concurrently" — `render_check()`'s very first lines now redirect
`PROFILE_PATH` to a temp file for the whole function, restored at the very
end, so no `FocusPreviewWindow` built anywhere in the self-check, now or
in the future, can touch the real file at all. If you add a new
Qt-gated check that constructs a `FocusPreviewWindow`, you get this for
free; just don't reach for the real `PROFILE_PATH` directly inside one.

**A render-check that HANGS (not fails) right after loading an image is
almost certainly a blocking `QMessageBox` with no one to click it.**
`_load_image`-style methods across this project catch a load failure and
call `QMessageBox.warning(...)`, which is a real modal `.exec_()` — fine in
the real app, but in a headless `--render-check` run there is no user to
dismiss it, so the process just sits there forever instead of failing
loudly. Hit this for real writing `measure.py`'s mark-commit status-line
test: it reused `green_path`, a fixture file an EARLIER check in the same
`render_check()` had already `unlink()`ed in its own `finally:` block, so
the reload raised, and the resulting warning dialog hung the test with no
error message at all — `timeout <n> python3 -u foo.py --render-check` (the
`-u` matters; stdout is block-buffered when not a tty, so without it you
see nothing before the timeout kills it) is what actually revealed where
it stopped. Lesson: give any UI-driving render-check test its own
self-contained fixture files, never reuse another check's, even one that
looks like it should still be sitting there.

**Circular import chain — `wizard_pages.py`'s qt_shell import MUST be
lazy.** The load order is `qt_shell.py → calibrate.py → wizard_pages.py`
(both `qt_shell` and `calibrate` import `wizard_pages` at module level, for
`ImageSourcePage`). If `wizard_pages.py` ever imports `qt_shell` at module
level too (for `new_session_dir` or the overlay-render helpers), the cycle
closes and one of the three fails to import depending on which one Python
loads first. Both of `wizard_pages.py`'s reasons to reach into `qt_shell`
are deferred into a lazy `_lazy_qt_shell()` helper, called only at actual
use time, never at import time. If you add a new cross-reference between
any of `{qt_shell, calibrate, measure, ca_measure, wizard_pages}`, check the
import graph before assuming a top-level import is safe.

**Same rule applies to `gallery.py`, one level further.** `qt_shell.py`,
`measure.py`, and `calibrate.py` all reach into `gallery.py` (for
`GalleryBrowseWindow`/`GalleryPickDialog`); `qt_shell.py` does it as a
top-level guarded import (safe — `gallery.py`'s own top level only pulls in
`stacks`/`annotations`/`pixel_hash`, none of which import anything back),
but `wizard_pages.py`, `measure.py`, and `calibrate.py`'s own `_on_open`
methods import `gallery` *lazily, inside the method*, not at module top
level — those three are exactly the modules `gallery.py` itself needs
(`measure.load_measurement_plane`, for the annotation check), so a
top-level import in either direction would close a new cycle. If you touch
`gallery.py`'s imports, keep `qt_shell`/`measure` lazy inside
`capture_has_annotation`/`_lazy_qt_shell`/`_lazy_measure`, same shape as
`wizard_pages.py`'s existing `_lazy_qt_shell`.

`process_wizard.py` sits one level further out and needs no lazy trick of
its own: it top-level imports `gallery` and `hdr_from_session` (neither
imports `process_wizard` back, and `hdr_from_session.py` needs no PyQt5 at
all), and only ever reaches `qt_shell.OUT_ROOT` through `gallery`'s own
already-lazy `_lazy_qt_shell()` (inside `new_output_dir`, at call time, not
at import time). `qt_shell.py` imports `process_wizard` at its own top
level the same safe way it already does `gallery`/`measure`. If
`process_wizard.py` ever needs something from `qt_shell.py` directly
(rather than through `gallery`), make that lazy too, same reasoning.

**`QGlPicamera2` (the embedded live-preview widget) needs a real
GL-capable X session.** It fails with `EGLError: EGL_BAD_ALLOC` when
constructed from a plain exec/tool shell, even with `DISPLAY` correctly set
to a real, running desktop session (`:0`) — this environment's shell just
doesn't have the GL/DRM access a real logged-in session would. This is an
**environment limitation of the exec shell, not a code bug** — the widget
itself was hardware-verified working in earlier sessions. Everything else
in `qt_shell.py` (menus, dialogs, non-GL windows, `FocusPreviewWindow`
itself minus the embedded preview) renders fine under `DISPLAY=:0` — you
just can't construct `Picamera2Camera` (which builds the widget in its
`__init__`) from here.

**Real-hardware testing workaround: drive `Picamera2` directly, skip the
widget.** When you need genuine sensor data (not `FakeCamera`'s synthetic
frames) to prove something works, don't try to construct
`camera_backend.Picamera2Camera`. Instead build `Picamera2` directly with
the same `create_preview_configuration`/`create_still_configuration` calls
`Picamera2Camera.__init__` uses, and drive `switch_mode` +
`capture_request` yourself — this gets you real DNGs off the actual IMX477
without needing the GL widget. Every "verified beyond render-check on real
hardware" claim in the git log this session was done this way. Search the
git log (`git log --all --grep="real hardware"`) for worked examples if
you need to do this again.

**The `calib/` directory at the repo root is the user's own real specimen
data**, not test fixtures — real `.dng` captures and session folders from
actual microscopy sessions (dates predate this session). Never touch,
move, or delete it. It's untracked in git (large binaries) and that's
correct — don't `git add` it.

**Central store paths** (all under `~/.zynergy/` except profile, which sits
in the repo root because the repo happens to live at `~/imx`; the
folder-location ones — provenance/capture/flat — are each a Preferences >
Advanced pref, defaults shown here, see Part 03's folder-layout note above
for the full split):
- `~/.zynergy/calibration.json` — spatial (µm/px), keyed by objective
- `~/.zynergy/ca_calibration.json` — chromatic aberration, keyed by objective
- `~/.zynergy/annotations.json` — measurement marks, keyed by `pixel_sha256`
- `~/imx/profile.json` — camera exposure/gain/WB (this repo IS `~/imx`)
- `~/provenance/<timestamp>/` — `session.json` + `.meta.json` sidecars only
  (Part 03) — no image bytes; `~/provenance/plane_cache/<pixel_sha256>.tif`
  — the green-plane cache (Part 04), a subfolder of this same root
- `~/captures/<timestamp>/` — session image bytes only, no `session.json`
  (Part 03); `~/captures/focal/<stack_id>/plane_N/` — z-stack captures;
  `~/captures/adhoc/` — ad hoc wizard-shot images (not full sessions)
- `~/flat/` — the standing flat-field library, replaced outright by each
  new Flat capture (Part 03), not a per-session capture

All three JSON stores are append-only with a `supersedes` chain — **never**
edit or delete an existing entry when you need a store operation; add a new
one. Every store function that writes does so atomically (temp file, then
`os.replace`).

**Menu integration pattern** (`qt_shell.py`'s Calibrate/Measure menus): both
follow the identical shape — a guarded top-level import
(`try: from . import X as _x / except ImportError: ... / _x = None`), one
menu with one action, the action disabled with a tooltip if the import
failed, and a `_launch_x()` method that reuses an already-open window
(`raise_()` + `activateWindow()`) rather than spawning a duplicate on a
repeat trigger. If you add a fifth tool that needs its own window, copy
this shape exactly — it's proven and tested (see `_launch_calibrate` /
`_launch_measure`).

**`README.md` was refreshed** (`cd6e566`) to match current state — the
stale `zstack_process.py`/standalone-`capture.py` references are gone, and
it now documents `ca_measure.py`, the Calibrate/Measure menus, z-stack
review, and post-capture QC. If you make an architecturally-visible change
(new tool, new menu, a file removed or renamed), update `README.md` in the
same commit rather than letting it drift again.

### PRIORITY: preview-to-green-plane click mapping is wrong — BUILT, CONFIRMED on-rig 2026-08-01

Full brief in a user-provided
`PRIORITY_click_mapping_fix.md` (not checked into the repo), plus a
mid-turn clarification from the user on the sensor-profile module's naming
(folded in below). **This is a measurement-accuracy defect, confirmed
on-rig, and outranks every remaining roadmap item** — it supersedes the
roadmap's ordering the same way the freeze-on-first-click bug did earlier.

**The defect.** A stage micrometer in view: the live preview shows 19
divisions across the frame; the frozen plane shows 27 divisions across the
same frame (~1.42x wider field), plus the circular field stop and
vignetted corners the preview never shows. Confirmed consequence: the
freeze-triggering click registers point 1 at a visibly different place on
the frozen plane than where it was actually clicked.

**Root cause.** `native_point_from_preview_click(px, py, disp_rect,
green_plane_res)` (`qt_shell.py`) converts a preview-widget click into
green-plane coordinates via one letterboxing-aware fraction. That's only
correct if the preview stream and the green plane cover the same field of
view — they don't. The preview comes from `preview_res` (default
1332x990); the green plane comes from the still config built on `full_res`
(4056x3040). Those are two different IMX477 sensor modes with two
different crop rectangles read off the sensor array — 1332x990 is a
cropped mode, not a binned-down version of the same full-array view
4056x3040 reads. Expected FOV ratio for this pairing is roughly 1.52
against the on-rig-measured ~1.42 (division-counting at a frame edge is
imprecise, so that gap isn't alarming) — **neither number is the
calibration source; the fix derives the correction from the sensor's own
reported crop geometry, never a hand-counted ratio.**

**Scope**: point 1 of every measurement is affected (the only point that
crosses between the live preview and the frozen plane); points 2+ are
already correct (clicks on the frozen canvas, `mapToScene`, no cross-view
conversion). Error is zero at frame centre and grows toward the edges.
Pre-existing, not introduced by the freeze-on-first-click fix — but that
fix made the inaccurate path mandatory (before it, a click with no tool
armed was discarded, so the bad conversion was avoidable).

**Interim workaround — no longer needed, kept here for context only.**
Until this was confirmed on-rig, the standing advice was: freeze with the
click, press **Escape** to cancel the in-progress shape, then place both
points on the frozen canvas, avoiding the cross-view conversion entirely.
The fix below was confirmed on-rig 2026-08-01 (see the Verification note
at the end of this section) — the workaround is obsolete and should not
be followed; point 1 of a fresh measurement is correct as the tool
normally works.

**Also worth recording**: any measurement already committed whose first
point came from a freeze click placed off-centre carries this error.
Those results predate the fix and should not be treated as equivalent to
ones taken after it — no way to retroactively correct them, since the
click's own screen position wasn't recorded, only its (wrong) converted
coordinate.

**Landed exactly as planned, no deviations** (promotes roadmap item 3, the
sensor-profile module, from an architectural tidy-up to a prerequisite for
measuring correctly):

1. **New `imx477.py`** (sensor profile module, driver layer, alongside
   `camera_backend.py`). Exposes, for a given output size, the crop
   rectangle that mode reads from the full sensor array — origin AND
   extent (`(x, y, w, h)` in full-array pixel units), not a scale factor,
   since a scale factor can't express an off-centre crop (exactly the bug
   above). A static table is the off-rig/`--render-check` fixture only;
   on-rig, `Picamera2().sensor_modes`' own `crop_limits` is authoritative
   and must be read from the SAME cached sweep `get_capabilities()`
   already primes at construction (see the "sensor_modes is not a passive
   lookup" entries elsewhere in this file) — never a second sweep.
   `FakeCamera` needs a plausible implementation too, or the self-check
   can't run end to end.

   **Naming, per the user's own instruction, given mid-brief**: the module
   name must match `Picamera2().camera_properties['Model']` EXACTLY (e.g.
   `"imx477"`), not a name chosen for readability. That buys a direct
   lookup with no separate mapping table to drift from what the hardware
   reports: `camera_backend.py` resolves a sensor to its profile module by
   importing the exact string the hardware itself names, restricted to a
   same-named `.py` file sitting next to `camera_backend.py` (never an
   unrelated same-named package elsewhere on `sys.path`). An unrecognised
   sensor fails as a missing module named after the real sensor model,
   never a silent fallback to IMX477 geometry for hardware this project
   has never seen. This is also why `camera_backend.py` itself stays fully
   sensor-agnostic — it never hardcodes the string `"imx477"` anywhere in
   its own dispatch logic, only `FakeCamera` does (deliberately: it's a
   stand-in for THIS project's real rig, and its `get_capabilities()`
   already returns real IMX477 mode sizes).

   **`PHILOSOPHY.md`'s rule itself was rewritten, not just reasoned
   around.** The old wording ("`camera_backend.py` is the only file in
   this project that may know what an IMX477 is") had a property worth
   keeping even though it was outgrown here: it was checkable, by a plain
   grep. Just arguing that `imx477.py` doesn't really violate the spirit
   of that sentence would have thrown that property away — the next
   reader would hit the stale wording, see `imx477.py` sitting right next
   to `camera_backend.py`, and could reasonably "fix" it by folding the
   module back in, undoing the modularity on the authority of a document
   that no longer described the code. Caught on review (by the user, not
   by this session) before it could cause exactly that. Fix: the rule now
   reads "sensor-specific knowledge lives in sensor-named modules matching
   the hardware-reported model; those modules may be imported only by
   `camera_backend.py`, which itself contains no sensor-specific
   constants" — and a NEW structural self-check,
   `assert_only_camera_backend_imports_sensor_profiles`
   (`camera_backend.py`), makes that checkable again: it discovers
   sensor-profile modules by shape (exposing `FULL_ARRAY_SIZE`/
   `crop_for_size`, imx477.py's own contract — never a maintained name
   list, so a future `imx519.py` is covered the moment it exists) and
   greps every OTHER file for a direct import of one, the same style as
   the pre-existing `assert_only_camera_backend_imports_picamera2`. Both
   checks run from `camera_backend.py`'s own self-check block. The
   Picamera2/libcamera half of the original rule is unchanged.

2. **`CameraBackend` gains three new methods** — `preview_resolution()`
   and `capture_resolution()` (the ACTUAL configured `(w, h)` for the live
   preview stream and the still-capture path; needed so the fix is general
   across arbitrary preview resolutions once item 2, the user-settable
   preview_res, is on-rig — never a hardcoded 1332x990 correction) and
   `sensor_crop_for_size(size)` (the `(x, y, w, h)` crop rectangle for
   any of this backend's own advertised sizes). `FakeCamera` implements
   all three plausibly (delegating the crop lookup straight to
   `imx477.crop_for_size`, since its `get_capabilities()` already reports
   real IMX477 sizes); `Picamera2Camera` implements them from cached
   sensor data.

3. **Fix the conversion** (`qt_shell.py`): `native_point_from_preview_click`
   keeps its name (the Live Measuring boundary check at
   `assert_live_measuring_has_no_calibration_dependency` already forbids
   that exact name in the unrelated pixel-only feature, and that guard
   should keep working unchanged) but its body becomes the full chain: (1)
   widget point -> fraction of the preview stream (existing
   `frac_from_point`, unchanged), (2) fraction -> full-sensor-array pixel
   coordinate, via the PREVIEW mode's own crop rectangle, (3) sensor
   coordinate -> green-plane pixel coordinate, via the STILL mode's crop
   rectangle. Stays a pure function taking both crop rectangles as
   arguments — Qt-free, camera-free, testable in `--render-check` with no
   hardware, matching how this file already separates decisions from Qt.
   The one production call site (`_live_measure_preview_event`) sources
   the two crop rectangles from `self.camera.sensor_crop_for_size(...)`
   fed by the new `preview_resolution()`/`capture_resolution()` accessors,
   never a module-level constant.

4. **The interim-behaviour question the brief raised** ("stop
   auto-registering point 1 until the fix lands, so the user places both
   points on the frozen canvas") **does not apply** — the real fix (steps
   1-3 above) lands in this same piece of work, so there is no gap for
   that flip-flop to bridge.

**Static crop table, `imx477.py`** (off-rig fallback / fixture only — full
array `(0, 0, 4056, 3040)`, the 5 real discrete sizes this project's own
on-rig `sensor_modes` read already confirmed, see the "Camera capability
query: `sensor_modes` hardware-verified" entry above): full-FOV binned
`2028x1520` and full-FOV unbinned `4056x3040` both read the whole array;
the 16:9-cropped `2028x1080`/`4056x2160` pair reads a centred
`(0, 440, 4056, 2160)` crop (binned/unbinned versions of the same window);
`1332x990` reads a centred `(696, 530, 2664, 1980)` crop — derived from
this mode's own 2x2-binning arithmetic and centring, **not** independently
confirmed against a real `crop_limits` read. Cross-check: that derivation
implies a preview/still FOV ratio of 4056/2664 ≈ 1.523, matching this same
brief's own "expected ratio roughly 1.52" note almost exactly — reasonable
corroboration, but on-rig confirmation (reading real `crop_limits`
directly) should still replace this table's role as anything but a
fallback/fixture the moment hardware is available.

**Verification**: `--render-check` proves the conversion chain as a pure
function against several crop-rectangle pairs, including a real off-centre
crop (imx477's own 1332x990-vs-4056x3040 pairing, via a real
`FakeCamera.sensor_crop_for_size`, not a hand-fabricated rectangle) and the
identity case (same crop for both views -> result matches the OLD
single-fraction formula exactly, byte-for-byte, proving the fix provably
doesn't change the already-correct case). Also new: `imx477.py`'s own
self-check (crop-table internal consistency — every entry inside
`FULL_ARRAY_SIZE`, aspect-preserving — an unknown-size failure, and the
FOV-ratio cross-check against this brief's own ~1.52 expectation);
`camera_backend.py`'s self-check gained coverage for
`preview_resolution()`/`capture_resolution()`/`sensor_crop_for_size()`
being general across a non-default preview/full pairing (not hardcoded to
1332x990/4056x3040), and for `_resolve_sensor_profile` resolving `"imx477"`
by exact name while rejecting an unrecognised, wrongly-cased
(`"Imx477"`), or unsafe (`"../imx477"`, a shell-injection-shaped string)
model rather than silently falling back to IMX477 geometry. Full project
`--render-check` sweep run and passing (exit 0): `imx477.py`,
`camera_backend.py`, `qt_shell.py`, and every other module with its own
`--render-check` (17 total, including `measure.py`, `calibrate.py`,
`gallery.py`, `plane_cache.py`, `provenance.py`, `annotations.py`,
`ca_measure.py`, `export.py`, `focus.py`, `process_wizard.py`,
`publish.py`, `stacks.py`, `wizard_pages.py` — `test_burst_backend.py`
skipped, hardware-only by design).

**Confirmed on-rig, 2026-08-01.** The self-check above proves internal
consistency; the real test — captured as part of the same bench session
that confirmed the PyQt6 port — is in `CHANGELOG.md`'s "Record on-rig
confirmation: PyQt5 to PyQt6 port" entry, item 1: freeze-and-click tested
at capture 4056x3040 against preview 1332x990 (the pairing that actually
exercises this fix's crop-aware conversion), with a 40x specimen
measurement landing within 1% of an independently calibrated 4x
measurement scaled by ten. Still worth re-running at a second preview
resolution once item 2 (the user-settable `preview_res`) is itself
on-rig, and worth cross-checking the static crop table above against a
real `crop_limits` read specifically (the on-rig result confirms the
*conversion formula* works; it doesn't by itself confirm every entry in
the off-rig fallback table matches a directly-read `crop_limits`, since
real hardware runs through `Picamera2Camera`, not the static table). The
interim workaround above no longer applies.

## Design conventions worth knowing before you add anything

- **Evidence, never a gate.** `poly2_flag`, `sharpness_relative_flag`,
  `calibration_staleness` all follow the same rule: detect and surface a
  problem, but never auto-block or auto-correct. A stale calibration still
  works: the user decides whether to re-measure. A soft z-stack plane still
  displays: the user decides whether to exclude it. If you're tempted to
  add an automatic decision on top of a detector like these, don't —
  that's a deliberate, repeated design choice, not an oversight.
- **Absence is not evidence of a mismatch.** `calibration_staleness` skips
  any field an entry never recorded (an older entry predating a given
  field) rather than flagging it — same principle as above, applied to
  missing data specifically.
- **Pure logic is Qt-free and camera-free**, always in a form
  `--render-check` can exercise with no hardware and (mostly) no PyQt6.
  GUI code is a thin wrapper that calls into it. If you're writing
  something that isn't obviously GUI wiring, it almost certainly belongs
  in the Qt-free section of whichever file, not inline in a widget method.
- **One session (folder) contributes one z-stack plane.** A stack spans
  *across* session folders, tagged via `stacks.apply_tag`/`find_tagged`,
  never assembled from one session's own `captures` list. This tripped up
  an earlier version of `measure.py`'s `_load_stack` badly (it looked for
  a nonexistent `"base"` field and assumed one session held a whole
  stack) — the fix is `stacks.group_by_stack` + `stacks.ordered_planes`
  across multiple session dirs. If z-stack code looks like it's reading
  one session's captures for multiple planes, that's the bug recurring.
- **`--render-check` coverage is the definition of done.** Every commit
  message in the git log that says "Build §N" also says which new
  `--render-check` assertions back it up. Don't consider new logic
  finished without a corresponding self-check; don't trust a GUI method
  without at least a scripted (if not pixel-verified) exercise of it.
- **Self-check scratch paths are `tempfile`-based, not hardcoded.** Every
  `--render-check`/self-check harness in this project (`qt_shell.py`,
  `calibrate.py`, `provenance.py`, `measure.py`, `annotations.py`,
  `camera_backend.py`, `ca_measure.py`, `plane_cache.py`) builds its
  scratch directories with `tempfile.mkdtemp()` and its rare stable-named
  paths (metadata strings that are never actually written to disk) with
  `tempfile.gettempdir()` — never a hardcoded `/tmp/...` literal. If
  you're adding a new harness path, follow that pattern rather than
  writing `/tmp/whatever` directly: a fixed POSIX path doesn't resolve on
  Windows/macOS and collides with another user's on a shared machine.
  Match the surrounding block's cleanup discipline too — a `mkdtemp()`'d
  directory is a fresh, uniquely-named one every run, so it has to be
  `shutil.rmtree()`'d somewhere in the same function, or it just
  accumulates on disk run after run instead of being overwritten in
  place the way a fixed name used to be.

## Recommended first move

Run the full `--render-check` sweep (command above) to confirm the
baseline still holds, then read the last handful of commit messages
(`git log -15 --stat`) for the specific reasoning behind the most recent
changes — they're written to be self-contained explanations, not just
summaries.
