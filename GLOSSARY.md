# GLOSSARY.md

Vocabulary used across `PHILOSOPHY.md`, `HANDOFF.md`, `CHANGELOG.md`, and
the code. A fresh agent can reconstruct most of this from context, but it
will take several turns and it will get one or two of them subtly wrong.
Cheaper to read it.

Terms are grouped by how well they are anchored. The last section is
explicitly unverified and is meant to be closed rather than carried
forever.

---

## Provenance

**pixel_sha256** — hash of the actual pixel data of the exact image plane
a measurement was made on. Measurements are keyed to this, not to a
filename, path, or timestamp. Files get renamed, moved, reprocessed and
re-exported. The pixels either are or are not the same pixels.

**Orphan** — an annotation record whose `pixel_sha256` no longer resolves
to any known image. Usually because the image was reprocessed and its
pixels changed. This is correct behaviour, not a bug to design around.
The store surfaces orphans rather than silently re-attaching them to
something that looks similar.

**Supersedes chain** — the mechanism that makes the three JSON stores
(spatial calibration, chromatic-aberration calibration, annotations)
append-only. Recalibrating writes a new entry carrying a `supersedes`
pointer to the one it replaces. The old entry still exists, still says
what it said, and a measurement referencing it still resolves correctly.
Without this, recalibrating would silently re-interpret every historical
measurement against a number it was never computed with.

**Atomic store write** — temp file in the same parent directory, then
`os.replace`. A crash mid-write cannot leave a truncated store. This is
the house pattern and it is hand-duplicated at each site rather than
factored into a helper. Note that it is a convention rather than a
universal fact about the codebase: `session.json`'s writers were the
exception until recently, and a new writer will not inherit it
automatically.

**Recorded conditions** — a calibration entry stores more than µm/px: the
objective, the reduction lens, the target type, a focus score from the
frame it was measured on, and which CFA pattern and green channel were
used. A mark stores the raw click coordinates and which calibration entry
was in force. The test for inclusion: could someone later need this to
judge whether the number is trustworthy?

**Absence with a recorded reason** — provenance. **Absence without one** —
a gap indistinguishable later from corruption. If today's code knows a
correction was skipped or raws were discarded, it says so. Older records
that predate a field are skipped, not flagged; a record that predates a
field is not inconsistent with it.

**Promise versus description** — a record can state something that is not
true yet and becomes true only if a later step runs. That is a promise,
and it is a defect wherever a reader cannot tell the difference. The
general fix is ordering: perform the action, then write the record that
describes it. Where a promise is unavoidable it is documented as one at
the point it is written, rather than left to look like a description.

---

## Substrate

**Green plane** — one de-mosaiced green channel. A valid measurement
substrate.

**Linear master** — the other valid measurement substrate.

**Display-referred derivative** — anything through sharpening,
tonemapping, or CLAHE. `debayer.py` tags such outputs
`"kind": "display-referred derivative (NOT a measurement)"` and
`measure.py`'s `check_measurement_provenance()` refuses to load anything
carrying that flag. Those operations move apparent edge positions, so a
measurement taken on one is measuring where the tonemapper put the edge
rather than where the specimen boundary is.

**Overlay** — a layer composited over the live feed or displayed image.
Focus box, XY ruler, measurement marks. Never drawn into the image data.
The generalizing test from the EGD document: are the pixels being
displayed the sensor's pixels? An overlay sits on top and stays visibly an
addition, so it passes. A filter replaces them, so it fails. A focus bar
is fine. A green cast is not, unless the user knowingly reached for it.

---

## Sessions and stacks

**Session folder** — one capture session. Contributes exactly one z-stack
plane. Note that a session folder accumulates more than one *capture*:
the live session object persists for the whole application run, so every
Snap and every reshoot lands in the same folder.

**Z-stack** — spans *across* session folders via tags. Never assembled
from a single session's own capture list. This has been violated once
already and caused a real bug.

**calib/** — real specimen data. Not test fixtures. Never modified,
moved, or deleted.

**Science capture** — a plain non-HDR capture with no Flat dependency.
Flat and Dark corrections are optional techniques applied deliberately,
not steps a science capture assumes.

---

## Boundary

**camera_backend.py** — the driver. The only file that may import
Picamera2 or libcamera, or let a libcamera-typed value cross the seam.
Deliberately thin and provenance-unaware. Session creation, sidecar
writing, and hashing all live one layer up. This separation is what makes
every other module testable with no hardware attached.

**Sensor-profile module** — e.g. `imx477.py`, named to match
`Picamera2().camera_properties['Model']` exactly. Holds full array size,
crop geometry and mode tables, CFA pattern (including no CFA at all), and
sensor bit depth. Importable only by `camera_backend.py`, which resolves
the right one at runtime by the hardware's own reported name rather than
a hardcoded mapping table. A mono sensor is a sensor, not an exception to
be special-cased above the seam.

**Shape predicate** — how the import guard discovers sensor-profile
modules: a module exposing `FULL_ARRAY_SIZE` / `crop_for_size`, not a
maintained list. The profile contract and the predicate change together.
Grow one without the other and the guard keeps passing while covering
less than it claims.

**Capability enumeration** — a driver-implemented query returning generic
structures. The alternative to requiring methods a non-libcamera backend
cannot honestly implement.

**Degenerate but truthful** — the allowed answer shape for a backend that
genuinely lacks a concept. `sensor_crop_for_size` returns `(0, 0, w, h)`
on a camera with no mode cropping; `native_point_from_preview_click`
degenerates to identity. A fabricated answer is never allowed.

---

## Method

**EGD (Evidence-Gated Development)** — no task advances state until
evidence external to the code confirms it. The authority to close a task
sits outside the codebase, and that is the point rather than an
inconvenience.

**Fixed** — compiles, runs, passes `--render-check`. **Confirmed** —
watched behaving correctly against the instrument. Tracked separately,
never collapsed into each other. An agent running on the Pi can produce
both; an agent in a sandbox can produce only the first, and should say
so rather than implying otherwise.

**--render-check** — every module's self-test, running with no camera and
mostly no PyQt6. Coverage is the definition of done. It proves internal
consistency and says nothing about the rig.

**Measurement provenance** — a measurement is reproducible only if what
it ran against is recorded with it: the environment *and* the commit. Two
contradictory on-rig readings of the same quantity once cost a session to
reconcile, and the reason it was hard is that neither recorded which
version of the code it ran through while the code was being edited around
it.

**Guess versus finding** — a step taken speculatively during
troubleshooting is recorded as speculative. Otherwise the next reader
treats it as established and reasons across it.

**The blind spot** — anything with no evidence source is invisible to the
method. Performance regressions, structural debt, abstractions that have
gone quietly wrong. No instrument fails because a driver layer got
tangled, so the gates report green the whole time these get worse. Needs
a calendar-triggered mechanism, because the defining feature of these
problems is that nothing triggers.

**Start conditions are dates, not judgments** — any recurring practice
triggered on "once we're done with X" does not start. Overhauls taper
off. Nobody rings a bell.

**Verified reliance** — the intended relationship between operator and
agent, as distinct from trust. Trust means extending credit past what you
can check. What is worth protecting is not the trust, it is the low cost
of catching an error.

---

## Documents

**PHILOSOPHY.md** — the *why*. Governing document. Finish it before
acting.

**HANDOFF.md** — present state, updated in place. Carries no
intent/build convention, because there is nothing for a description of
the present to supersede. Which is also why intent records cannot live
here: a file rewritten in place cannot hold a record that has to stay
exactly as written.

**CHANGELOG.md** — append-only history. Never modified. Intent entry,
then build, then build entry. Each phase its own commit; the intent
commit contains the changelog entry and nothing else. That commit
boundary is what makes the ordering checkable rather than asserted.

**SWEEP_CHECKS.md** — the standing check list. Where a class of defect
goes once you have found one instance of it and want the next one caught.

**FUNCTION_INDEX.md** — generated function index with a freshness guard.
Regenerate it when functions are added or renamed.

**DISCOVERED:** — marker in a build entry for something found along the
way. **# CAVEAT:** — the matching inline comment when the discovery is a
durable fact about a specific line.

---

## Unverified, to be closed

The following terms come from working context rather than from a document
read against the repository. **This section is a task, not a permanent
disclaimer.** Any agent with the repository available should confirm each
against `HANDOFF.md` or the code, promote what holds into the sections
above, correct what has moved, and delete what no longer exists. Leaving
it marked unverified forever is how it stops being read.

- **commit_measurement()** — Qt-free shared orchestration path for
  committing a measurement.
- **calibration_ref** — the stored per-record calibration pointer set at
  first-commit time. Publish must use this rather than the currently
  active calibration.
- **find_orphans()** — orphan surfacing entry point.
- **known_hashes** — for orphan detection, understood to be the union of
  capture hashes and cached-plane hashes.
- **~/provenance/** — provenance written always, kept out of the user's
  way. Single user-facing retention control is a "Keep RAW Images"
  checkbox in Preferences.
- **plane_cache** — on-disk cache of measurement planes under the
  provenance root.
- **MeasureWindow / MeasureWizard / ReviewWindow** — the measurement UI
  surfaces mid-extraction. Check `HANDOFF.md` for which currently exist.
- **Staging and publish** — the pattern where capture and processing
  write into a same-device staging directory and the finished set is
  published into the session directory file by file with `os.replace`.
  Retention is applied inside staging, so nothing is ever deleted from a
  directory the gallery can see. Confirm the current shape in
  `HANDOFF.md` before relying on it.
- **MMCore / pymmcore** — Micro-Manager integration target. A feature,
  not a workaround, and presented that way.
