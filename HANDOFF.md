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

## What this project is

A microscopy capture + calibration + measurement suite for a Raspberry Pi 5
with an IMX477 HQ camera. See `README.md` for the architecture map and the
measurement-integrity invariants (green-plane-only measurement, append-only
calibration, hash-pinned marks) — those are load-bearing design rules, not
suggestions, and nothing in this handoff repeats them. See `PHILOSOPHY.md`
for durable rules about *how* things get verified here (as opposed to what
currently is or was built) — in particular, its rule on what a self-check
actually has to prove before it counts as verification.

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
`--render-check` pass. Both Tier 0 investigations are also now done (see their own
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
extinction of provenance. Off means raw frames + the linear master
(`single_master.tif`/`master_*.tif`/`hdr_linear.tif`) are deleted once
processing succeeds (`hdr_from_session.py process()`'s own
`a.delete_raw_on_success`, wired from the `keep_raw_images` pref in
`qt_shell.py`'s `_run_process_cmd`, read live at processing time — not
baked into an open session), and the session record states the discard
was deliberate: `correction_status["raw_discarded"]` + (when true)
`"raw_discard_reason"`, parsed out of `hdr_from_session.py`'s
`CORRECTION_STATUS_JSON:` stdout line and written onto the capture's own
`session.json` entry by `qt_shell.py`'s `_record_correction_status` — a
later reader (human or agent) can distinguish "the user chose not to
keep these" from "a file is missing"; absence with a recorded reason is
provenance, absence without one looks like corruption. `measure.py`
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
  explicitly depend on this landing first.

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

**Video resolution menu detail worth knowing**: `camera_backend.py`'s
`Picamera2Camera.set_video_resolution()` has never had a live effect —
recording always encodes the preview config's fixed "main" stream, set
once at construction — despite a stale comment in `__init__` claiming
otherwise (it describes an abandoned mode-switching design;
`start_recording`'s own history notes are the accurate account). The new
Options > "Video resolution" menu in `qt_shell.py` writes a `gui_prefs.json`
preference via the new `video_resolution_kwargs()` helper, which `main()`
reads at camera-construction time (`Picamera2Camera(**video_resolution_
kwargs(...))`) — takes effect next launch, not immediately, and the
status text says so. If you ever want this to apply live, that means
tearing down and rebuilding the camera+widget while running; think hard
about it first, given this project's track record with in-session camera
reconfiguration (see `start_recording`'s own docstring).

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
all, so this fix is **not yet confirmed on-rig** — carried forward as an
open item: re-run the same live-measure freeze that produced the original
failure log and confirm `lores` is present and `main`/`raw` match
`self._preview_cfg` (not the probe's leftover config) immediately after
construction, before `start()` is even called.

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
  `--render-check` can exercise with no hardware and (mostly) no PyQt5.
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

## Recommended first move

Run the full `--render-check` sweep (command above) to confirm the
baseline still holds, then read the last handful of commit messages
(`git log -15 --stat`) for the specific reasoning behind the most recent
changes — they're written to be self-contained explanations, not just
summaries.
