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
working through in dependency order. Progress so far: Tier 1 item 1 (focus
aid tick rate + auto-reset on stack tag) is done. Tier 3 item 4 (Gallery
module) is done — see `gallery.py` below. Next up per that list: Tier 3
item 5 (processing wizard overhaul, choose-your-operations flow, now
unblocked since Gallery exists), then the z-stack one-click aid (Tier 3
item 6) the user actually wants, which hands off to the wizard once it
exists.

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

Every module with real logic has a headless self-check:
`python3 <module>.py --render-check`. Run the whole set before trusting
anything:

```bash
for m in pixel_hash annotations export publish calibrate measure ca_measure \
        wizard_pages qt_shell stacks focus gallery; do
  DISPLAY=:0 python3 $m.py --render-check || echo "FAILED: $m"
done
```

All 11 currently pass (some — `stacks.py`, `focus.py` — only gained a
`--render-check` this session; they didn't have one before). `stacks.py`
and `focus.py` and `calibrate.py`'s new pure functions run fine without
PyQt5 or a display; `qt_shell.py`/`measure.py` have PyQt5-gated checks that
print `SKIPPED` (not `FAILED`) when PyQt5 isn't importable — that's
correct, expected behavior, not a bug to chase.

## Things that will bite you if you don't know them

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
