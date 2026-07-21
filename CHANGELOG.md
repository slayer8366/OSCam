# Changelog

Curated, most recent first. Grouped by logical change, not a raw commit
dump — each entry names the commit(s) it corresponds to for traceability.
See `HANDOFF.md` for what a fresh agent needs to know before working here;
this file is the historical record of what happened and why.

## 2026-07-21

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
