# Changelog

Curated, most recent first. Grouped by logical change, not a raw commit
dump — each entry names the commit(s) it corresponds to for traceability.
See `HANDOFF.md` for what a fresh agent needs to know before working here;
this file is the historical record of what happened and why.

## 2026-07-21

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
