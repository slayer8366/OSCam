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
suggestions, and nothing in this handoff repeats them.

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
`PLAN_00_context_and_supersession.md` through `PLAN_02_camera_capability_
query.md` (drafted, not checked into the repo; Parts 03-05 — provenance
relocation/Keep RAW, green-plane cache, live measure panel — not yet
drafted). The design: one application, one window, one layout — every
feature always present, nothing gated by a mode. Provenance moves to
`~/provenance/<timestamp>/` rather than becoming conditional; the only
setting that changes what gets kept is Keep RAW Images. `casual_mode.py`
and its `qt_shell.py` plumbing (`CASUAL_MODE_DEFAULT`, the `"casual_mode"`
gui_prefs key, the Options > Casual Mode action, `main()`'s window-class
branch) are superseded but **not yet deleted** — that happens in Part 03,
which hasn't been drafted, so `casual_mode.py` stays in place and working
for now. Building the two parts that have no dependency on the rest
first, sequentially: **Part 02 (camera capability query) — intent
recorded, build starting.** Adds a generic `get_capabilities()` to
`CameraBackend`, stricter than this project's existing "thin adapter"
framing (README.md's "All camera-bound operations sit behind one thin
adapter"): `camera_backend.py` becomes the only file allowed to know what
Picamera2 or an IMX477 is. Part 01 (Preferences dialog, which renders
Part 02's results) follows once Part 02 lands.

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

If you're picking this up mid-build: check `git log` and this section
against what's actually in the repo — this describes the plan, not
necessarily what has landed yet.

Nothing else is currently in progress. Casual Mode (above) is done.

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
        provenance casual_mode; do
  DISPLAY=:0 python3 $m.py --render-check || echo "FAILED: $m"
done
```

All 15 currently pass (some — `stacks.py`, `focus.py` — only gained a
`--render-check` this session; they didn't have one before). `stacks.py`
and `focus.py` and `calibrate.py`'s new pure functions run fine without
PyQt5 or a display; `qt_shell.py`/`measure.py`/`casual_mode.py` have
PyQt5-gated checks that print `SKIPPED` (not `FAILED`) when PyQt5 isn't
importable — that's correct, expected behavior, not a bug to chase.

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
in the repo root because the repo happens to live at `~/imx`):
- `~/.zynergy/calibration.json` — spatial (µm/px), keyed by objective
- `~/.zynergy/ca_calibration.json` — chromatic aberration, keyed by objective
- `~/.zynergy/annotations.json` — measurement marks, keyed by `pixel_sha256`
- `~/imx/profile.json` — camera exposure/gain/WB (this repo IS `~/imx`)
- `~/captures/<timestamp>/` — session folders (`session.json` + raw frames)
- `~/captures/adhoc/` — ad hoc wizard-shot images (not full sessions)

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
