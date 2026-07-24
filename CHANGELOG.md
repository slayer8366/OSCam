# Changelog

Curated, most recent first. Grouped by logical change, not a raw commit
dump — each entry names the commit(s) it corresponds to for traceability.
See `HANDOFF.md` for what a fresh agent needs to know before working here;
this file is the historical record of what happened and why.

## 2026-07-24

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
