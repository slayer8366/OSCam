# Zynergy — design philosophy and working rules

For a Claude agent picking this project up without prior context. This is
not an architecture map (that's `README.md`) or a state-of-play summary
(that's `HANDOFF.md`). This is the *why*: the reasoning behind the rules,
which of them are absolute, which are open to judgment, and how to tell
the difference when you hit a case nobody anticipated.

Read this before your first meaningful change. Several of these rules
exist because they were violated once already, and the resulting bug cost
real hours to find.

---

## What this project actually is

A microscopy capture and analysis suite on a Raspberry Pi 5 with an
IMX477 sensor, used for real mycology work — spore morphometry,
measurements that get reported. It is not a camera app that happens to
be pointed at a microscope. The distinction matters constantly:

**A number this software reports must be traceable back to the raw
sensor data and to the exact calibration that produced it.** Everything
below follows from that single requirement. When a rule seems fussy or a
shortcut seems harmless, this is the sentence to test it against.

---

## The provenance model

Provenance here means: for any reported measurement, you can answer
*which pixels*, *which calibration*, *under what conditions*, without
guessing and without trusting that nothing changed in between.

Four mechanisms carry it. They are not independent — each covers a gap
the others can't.

### 1. Hash-pinned identity

Measurements are keyed to `pixel_sha256` — a hash of the actual pixel
data of the exact image plane the measurement was made on. Not a
filename, not a path, not a timestamp. Files get renamed, moved,
reprocessed, and re-exported; the pixels either are or aren't the same
pixels.

The practical consequence: if an image is reprocessed and its pixels
change, its hash changes, and its old annotations no longer resolve to
it. That is *correct behavior*, not a bug to design around. The
annotation store surfaces such records as orphans rather than silently
re-attaching them to something that looks similar.

### 2. Append-only stores with supersedes chains

Three JSON stores — spatial calibration, chromatic-aberration
calibration, annotations — are append-only. Recalibrating doesn't
overwrite the old entry; it writes a new one carrying a `supersedes`
pointer to the one it replaces.

Why it has to work this way: a measurement taken last month was taken
under last month's calibration. If recalibrating silently overwrote
that, every historical measurement would quietly re-interpret itself
against a number it was never computed with. The chain means the old
entry still exists, still says what it said, and the measurement that
referenced it still resolves correctly.

Every store write is atomic — temp file, then `os.replace` — so a crash
mid-write can't leave a truncated store.

### 3. Recorded conditions, not just results

A calibration entry doesn't just store µm/px. It stores the objective,
the reduction lens, the target type, a focus score from the actual
frame it was measured on, and which CFA pattern and green channel were
used. A measurement mark doesn't just store a distance — it stores the
raw click coordinates *and* which calibration entry was in force when
it was made.

The test for whether something belongs in a provenance record: could
someone later need to know this to judge whether the number is
trustworthy? If yes, record it.

### 4. Substrate discipline

Measurements happen on the green plane (one de-mosaiced green channel)
or on a linear master. **Never** on a display-referred derivative —
anything that has been through sharpening, tonemapping, or CLAHE.

This is not aesthetic preference. Those operations move apparent edge
positions. A measurement taken on a tonemapped image is measuring where
the tonemapper put the edge, not where the specimen boundary is. The
codebase enforces this structurally: `debayer.py` tags such outputs
`"kind": "display-referred derivative (NOT a measurement)"`, and
`measure.py`'s `check_measurement_provenance()` refuses to load anything
carrying that flag.

A real bug from this project's history, worth internalizing: a feature
once checked "does this capture have annotations?" by hashing the
session's `final_display.tif`. That file is structurally excluded from
the annotation store by the rule above — so the check would have
reported "never annotated" for every capture, including ones with real
measurements on them. It looked reasonable, would have run without
error, and would have been quietly wrong. **When you need an image's
identity for provenance purposes, resolve it to the actual measurement
substrate, not to whatever file is most convenient to hash.**

---

## Strict rules — do not break these

These are load-bearing. If one seems to be blocking you, you've found a
design problem worth raising, not a rule worth working around.

**Measurement substrate is green plane or linear master, always.** See
above. There is no acceptable exception, including "just for a preview"
or "just for this one check."

**Stores are append-only.** Never edit or delete an existing entry.
Never "clean up" a store. If a stored value is wrong, the fix is a new
entry that supersedes it.

**Overlays never touch capturable pixels.** The focus box, the XY ruler,
measurement marks — all composite over the live feed or the displayed
image as a separate layer. The image data itself is never drawn into.

**Camera-bound operations stay behind `camera_backend.py`.** The camera
adapter is deliberately thin and provenance-unaware. Session creation,
sidecar writing, and hashing all live one layer up. This separation is
what makes every other module testable with no hardware attached — and
it's currently intact, which was confirmed rather than assumed. Don't
be the change that erodes it.

Stated more strictly, because a later decision hardened it, then revised
again when `PRIORITY_click_mapping_fix.md` outgrew the original wording:
`camera_backend.py` is a *driver*. Sensor-specific knowledge — crop
geometry, mode tables, anything that would need to change for a different
sensor — lives in a sensor-named module matching the hardware's own
reported model exactly (e.g. `imx477.py`, named to match
`Picamera2().camera_properties['Model']`), never as constants scattered
elsewhere and never encoded in `camera_backend.py` itself. Those
sensor-profile modules may be imported ONLY by `camera_backend.py`, which
carries no sensor-specific constants of its own and resolves the right
one at runtime by the hardware's own reported name, never a hardcoded
mapping table. `camera_backend.py` is also still the only file in this
project that may import Picamera2/libcamera directly, or let a
libcamera-typed value cross the seam — that half is unchanged. Every
other module must run unchanged against a different sensor with a
different driver (and a different sensor-profile module) dropped in its
place. Capability enumeration is a driver-implemented query returning
generic structures; no Picamera2 or libcamera type may cross that
boundary.

Both halves stay checkable, not just asserted in prose — that property is
the reason this rule can be trusted rather than quietly outgrown again.
`camera_backend.py`'s own self-check runs
`assert_only_camera_backend_imports_picamera2` (no other file may import
`picamera2`/`libcamera`) and `assert_only_camera_backend_imports_sensor_
profiles` (no other file may import a sensor-profile module — discovered
by shape, a module exposing `FULL_ARRAY_SIZE`/`crop_for_size`, not a
maintained list, so a future `imx519.py` is covered the moment it exists).
Add a second sensor by dropping in its own profile module next to
`imx477.py` and teaching `camera_backend.py` to resolve it by name; if
you ever find yourself importing a sensor-profile module from anywhere
else, that is exactly the design problem this section exists to catch,
not a rule to route around.

**Pure logic is Qt-free and camera-free.** Anything that isn't obviously
GUI wiring belongs in a module-level, testable section, not inline in a
widget method. Every module has a `--render-check` self-test that runs
with no camera and (mostly) no PyQt5. This isn't a testing preference;
it's what makes the measurement logic verifiable at all.

**`--render-check` coverage is the definition of done.** New logic
without a corresponding self-check assertion isn't finished. And assert
the thing that actually matters, not its proxy — "the output file
exists" is a much weaker claim than "the output file carries the
provenance block it's supposed to."

**One session folder contributes one z-stack plane.** A stack spans
*across* session folders via tags; it is never assembled from a single
session's own capture list. This one has already been violated once and
caused a real bug. If z-stack code looks like it's reading one session's
captures for multiple planes, that's the same bug recurring.

**The `calib/` directory is real specimen data.** Not test fixtures.
Never modify, move, or delete it.

---

## Flexible — judgment applies

These are conventions with good reasons, not invariants. Deviating is
fine when you can articulate why.

**Module organization.** `capture.py` was folded into `qt_shell.py` when
it turned out not to be sensor-specific. Provenance code was pulled back
*out* of `qt_shell.py` into its own module because it predated that file
and never belonged inside it. Files move when the reasoning changes.
What doesn't move is the *layering* — nothing that reaches into the
camera, nothing that makes pure logic depend on Qt.

**UI shape and interaction details.** Button placement, menu grouping,
keyboard bindings, whether a panel floats or docks. These get decided by
what feels right in use, and get revised freely. A UI change was reverted
in this project simply because the original looked better.

**Scope of any given feature.** Shipping a working subset and marking the
rest as deliberately deferred is normal and encouraged here. Several
features exist in exactly that state right now — visible, disabled,
documented as to why.

**Which stage of a pipeline to expose.** Whether a processing wizard
offers six operations or three is a product decision, not a correctness
one.

---

## Working principles that have earned their place

**Evidence, never a gate.** Several checks detect problems — a stale
calibration, a soft z-stack plane, unusual CA curvature. Every one of
them *surfaces* the finding and lets a human decide. None auto-corrects,
auto-excludes, or blocks. If you're tempted to add an automatic decision
on top of a detector, don't. Surfacing a problem and deciding what to do
about it are different jobs, and this project deliberately only does the
first one automatically.

**Absence is not evidence of a mismatch.** When checking whether
something has drifted, skip fields an older record never captured rather
than flagging them. A record that predates a field isn't inconsistent
with it.

Absence in a record you are *writing today* is a different matter. If
code knows perfectly well that a correction was skipped, or that raws
were deliberately discarded, it should say so. A record that omits what
it knew is indistinguishable later from one that predates the concept
entirely. Absence with a recorded reason is provenance; absence without
one is a gap that looks like corruption.

**Don't build ahead of evidence.** A chromatic-aberration correction
model is specced, understood, and deliberately unbuilt — because no real
target has yet exhibited the curvature it would correct. Building it
would mean validating it against nothing. When a feature's correctness
can only be judged against real data you don't have yet, wait.

**Additive over replacing.** Video recording sits alongside still
capture. A general processing wizard sits alongside the existing
session-based one. Proven paths keep working; new capability arrives
beside them. This has consistently been the lower-risk choice here.

**One uniform path beats a special case.** A single-frame group still
goes through frame-averaging rather than short-circuiting to a copy —
one code path, one honest provenance record, no separately-behaving
branch that nobody thought to test.

**Report honestly, never silently swallow.** A failed item in a batch
gets recorded and the batch continues. A failure never becomes a silent
success, and a partial result never gets presented as a complete one.

**Separable features carry removal instructions.** Larger optional
features are bounded by banner comments naming exactly what to delete to
remove them. If you build something optional, do the same.

---

## Verification culture

Three things have repeatedly proven true here, all of them the hard way.

**Headless checks and real hardware behavior diverge.** A full
`--render-check` pass proves internal consistency. It does not prove the
feature works on the rig. Video recording passed every headless check
while producing no file at all on real hardware, three separate times,
for two different reasons. Always distinguish "self-check passes" from
"verified on hardware," and say which one you actually did.

**Reason from evidence, not from plausibility.** That same video bug
survived five hypothesis-driven fix attempts, each of which sounded
correct. What actually solved it was reading the library's own
documentation and running a minimal reproduction that isolated the
failing case. The lesson, written down at the time and worth repeating:
**when two consecutive fixes fail on the same symptom, stop reasoning
and go get real data.**

Corollary: know what your environment can and cannot tell you. An agent
working through a sandbox with stale file copies will confidently
describe code that no longer exists. Confirm before asserting, and say
plainly when you're inferring rather than observing.

**A self-check must reach the code the way the application reaches it.**

This is the newest of the three and the one that has slipped through
most often. Three separate times, a check confirmed a mechanism worked
in isolation while the path a real user takes stayed broken:

1. The live measure panel's `_live_measure_preview_event` was fully
   written but never wired into `eventFilter`. The self-check called
   the handler directly, so it passed. Every click on the preview would
   have fallen through to ordinary box-drag. The entire freeze path was
   dead code, unreachable from the UI.
2. `get_capabilities()` was hardware-verified standalone, against a
   camera that was not mid-preview. It crashed the moment a real user
   opened Preferences, because `sensor_modes` internally calls
   `configure()`, which raises on a running camera. The only state the
   function is ever called in from the UI was the one state never
   tested.
3. Live Measuring's structural self-check was written but never called
   from anywhere. Once wired in, it failed immediately.

The rule that follows: drive the event through the real dispatch path,
call the function in the state the application actually calls it in, and
confirm the check itself is reachable and runs. A handler existing and a
handler being wired in are different claims, and only the second one
matters to a user.

Two corollaries earned alongside it:

*Confirm the failure before confirming the fix.* When fixing a bug seen
on the rig, reproduce it first, then verify it is gone. A check that
never exercised the failing sequence can pass for the wrong reason.

*Assert on structure, not on source text.* Live Measuring's check
tripped a false positive because it scanned source text and caught a
docstring explaining what the module deliberately does not do. Inspect
namespaces, imports, and types instead. Text scanning will keep breaking
on comments that describe the very thing being forbidden.

---

## Documentation as a first-class artifact

`HANDOFF.md` and `CHANGELOG.md` are the project's living memory, kept
current as work happens rather than reconstructed afterward. They exist
because this project is worked on by multiple agents and instances with
no shared context, and the repo is the only thing all of them can see.

Entries are written in two phases, not one. Before a change, an entry
states the intent to make it. After the change lands, that entry is
edited or followed up to state what actually happened. A record written
only after the fact loses the part worth having: what was expected, and
whether it held.

Write them for someone who wasn't there. Record *why*, not just *what* —
a decision without its reasoning becomes an arbitrary constraint that
someone later removes because it looked pointless.

Record what didn't work, too. Several of this project's most useful notes
are about dead ends, so nobody spends hours rediscovering them.

---

## If you're unsure

Ask, and be specific about what you're unsure of. The build order in this
project has repeatedly been shaped by someone stopping to flag an
ambiguity rather than picking an interpretation and proceeding. A
question costs one exchange; a wrong assumption baked into working code
costs considerably more.

And when you do flag something, flag it *before* building on top of it,
not after. Nearly every expensive problem in this project's history was
cheap to fix at the point it was first noticeable.
