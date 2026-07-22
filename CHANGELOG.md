# Changelog

Curated, most recent first. Grouped by logical change, not a raw commit
dump — each entry names the commit(s) it corresponds to for traceability.
See `HANDOFF.md` for what a fresh agent needs to know before working here;
this file is the historical record of what happened and why.

## 2026-07-22

### Planned: `provenance.py` extraction, phase 1 (BUILD_LIST Tier 3, item 1)

Plan approved, not yet built (see `HANDOFF.md`'s note for the full design).
This turn's own Tier 0 investigation confirmed `camera_backend.py` has
zero session/provenance awareness — the original thin-adapter design
intent held — so this is a clean pull-out, not a rewrite: `OUT_ROOT`,
`PROFILE_PATH`, `load_profile`/`save_profile`, `_dump_meta`,
`new_session_dir`/`new_zstack_root_dir`, `class Session`, and
`record_capture`/`record_burst`/`record_hdr` move out of `qt_shell.py`
into a new `provenance.py`. Unblocks Casual Mode (item 2) and the store-
mechanics migration (phase 2, item 7).

The one real hazard: `render_check()` mutates `OUT_ROOT`/`PROFILE_PATH`
as module state to isolate its own test fixtures (and, after two real
incidents this session, to keep the whole self-check off the real
`~/imx/profile.json`). Every consumer — `qt_shell.py` itself, `gallery.py`,
`wizard_pages.py` — must reference `provenance.OUT_ROOT`/`provenance.
PROFILE_PATH` by attribute, never `from provenance import OUT_ROOT`,
which would create a second binding that silently stops tracking the
moment either side reassigns it. `provenance.py` will carry an explicit
comment on the constants themselves saying so, not just this note.
`list_sessions`/`load_session_json`/`processable_captures`/
`capture_correction_status`/`archive_session_raws`/`build_display_flags`
stay in `qt_shell.py` — reading `session.json` back out for browsing is a
different concern from writing new provenance records, and the build
list's own phase-1 scope doesn't cover them.

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
