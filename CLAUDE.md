# CLAUDE.md

Operating contract for any agent executing work in this repository.

This file is deliberately short and deliberately carries **no state**.
State lives in `HANDOFF.md`. Reasoning lives in `PHILOSOPHY.md`. If this
file ever starts describing what is currently being built, delete that
part. Two files describing the same present will drift, and the one you
happen to read will be the stale one.

---

## What this project is

A microscopy capture and analysis suite on a Raspberry Pi 5 with an
IMX477 sensor, used for real mycology work. Spore morphometry.
Measurements that get reported.

A number this software reports must be traceable back to the raw sensor
data and to the exact calibration that produced it. Every rule below
follows from that sentence. Test shortcuts against it.

---

## Establish where you are running, before anything else

Sessions run in one of two places and the difference decides what you
are able to verify:

**On the Pi** (hostname `raspberrypi`): numpy, picamera2, a live
`DISPLAY=:0`, real capture data on disk, and the instrument itself. You
can launch the application, drive it, and measure real behaviour.

**In a cloud sandbox**: none of the above, and no ssh route to the Pi.
You can read code, reason about it, and write. You cannot verify
anything that touches hardware or real data.

Check which you are in and say so in your first substantive message. If
a check cannot run where you are, say that plainly and ask. Do not
simulate it and do not silently skip it. A verification that was
quietly skipped is worse than one that was never claimed, because the
record shows a gate that did not run.

---

## Read before your first change, in this order

1. `PHILOSOPHY.md` — governing document. Finish it before acting. Not
   skim, finish. Several of its rules exist because they were violated
   once and the bug cost real hours.
2. `HANDOFF.md` — what is true right now.
3. `CHANGELOG.md` — the tail, and specifically any intent entry with no
   matching build entry. That is unfinished work someone recorded.
4. `README.md` — architecture map.
5. `GLOSSARY.md` — vocabulary. Cheap to read, expensive to reconstruct.

Reading only this file is not sufficient preparation. This file is the
contract. `PHILOSOPHY.md` is the reasoning, and the reasoning is what
tells you what to do in the cases nobody wrote down.

---

## Preflight, every session, before the first edit

```bash
git rev-parse --short HEAD
git branch --show-current
git status --porcelain
git log --oneline -15
git log -p -3 -- CHANGELOG.md
git worktree list
```

Report the HEAD hash **and the branch** back to the operator in your
first substantive message. It costs one line and it is the only cheap
proof that you and the operator are looking at the same tree.

The branch matters more than it looks. The operator runs the instrument
from the working tree you are editing, so whatever branch you leave
checked out is what his camera runs next time he launches it. Say where
you left it when you finish.

`git worktree list` matters for the same reason. More than one checkout
of this repository has existed on the machine at once, and at least one
stale one has genuinely been launched from by accident.

If your file view and git disagree, your file view is wrong. An agent
working through a stale sandbox will describe code that no longer exists,
confidently, at length, and the description will be internally consistent
the entire time.

---

## Label every claim

Two distinctions, both load-bearing, neither optional.

**Observed or inferred.** If you ran the command and read the output, it
is observed. If it follows from something you read, it is inferred. Say
which. "Confirm before asserting" is the rule; saying plainly that you
are inferring is the fallback when you cannot confirm.

**Fixed or confirmed.** A change that compiles, runs, and passes
`--render-check` is *fixed*. A change watched behaving correctly against
the instrument is *confirmed*. Track them separately. Never let one
sentence report both. Most of the value of this project's method sits in
that refusal.

When you are running on the Pi you *can* move something from fixed to
confirmed, and you should: launch the application, drive the real path,
and report what the instrument actually did. That is the cheap half of
the evidence gate and there is no reason to hand it back.

What you cannot do is judge how something looks. Anything whose pass
condition is visual belongs to the operator's eye. Measure it, report
the numbers, and ask. "The UI renders at the correct size" is not a
finding you can make. "The application font is 18.0pt PibotoLt and the
desktop's is the same" is.

Say which kind of verification you ran. A green `--render-check` and a
watched run on the rig are different claims and only one of them is
about the instrument.

---

## The three-phase commit convention

Non-negotiable. Stated in full in `PHILOSOPHY.md`; the operational
summary:

1. **Intent entry** in `CHANGELOG.md`, in its own commit, containing
   nothing else, committed before any other file is touched. It carries a
   measured baseline: a count where the work can be counted, otherwise the
   scope in a form the build record can be checked against (files it will
   touch, behaviours it will change, what it will deliberately leave
   alone). A baseline that cannot be checked against the finished work is
   not a baseline.
2. **Build** the thing the entry describes.
3. **Build entry** recording what was actually built, any deviation and
   why it was necessary, and anything found along the way marked
   `DISCOVERED:`. A discovery that is a durable fact about a line of code
   also gets a `# CAVEAT:` comment on that line.

Each phase is its own commit. Three entries landing in one commit prove
nothing about which one actually came first, and the commit boundary is
what makes the ordering checkable rather than asserted.

Where the work *is* the outcome, such as recording a measurement already
taken, there is no plan to diverge from. That is a single entry with no
intent phase.

Fix the action to match the record, rather than the record to match the
action.

No retroactive recording. If you built before recording intent, undo only
the building. Keep every record, including the one showing the false
start. The undo penalty is on code, never on the record.

An entry, once written, is never modified. A correction is a new entry
that supersedes it. Abandoning a plan is a recorded act, not a violation:
a new intent entry stating what it supersedes and why.

Verify append-only after each phase:

```bash
git diff <base> HEAD -- CHANGELOG.md | grep '^-' | grep -v '^---'
```

An insertions-only diff is not sufficient evidence on its own. A
superseding entry has previously consumed the superseded entry's header
line and left its body orphaned under the new title, and the diff looked
clean throughout. Check that every pre-existing entry is still present,
byte-identical, and still its own entry with its own header.

---

## Write permissions

| Path | Who writes | Rule |
|---|---|---|
| `CHANGELOG.md` | execution agent only | append only, never edited |
| `HANDOFF.md` | execution agent only | updated in place, present tense |
| `calib/` | nobody | real specimen data, never test fixtures |
| `profile.json` | nobody | live rig drift, never committed |
| source tree | execution agent | see boundaries below |

Planning and review agents propose. They do not write to the repo. If you
are reading this as part of an execution session, you are the one holding
the pen, and the entries are yours to write in the right order.

Scratch analysis scripts live **outside** the repo tree. Do not leave
tooling in the working directory that the changelog does not account for.

---

## Operational rules

Small, unglamorous, and each one already responsible for lost time.

**Push only when asked.** Commit freely. Pushing is the operator's call.

**No interactive choice widgets.** They have repeatedly failed to render
in the operator's client. Ask in plain text, state the default you would
take and why, then stop.

**Stub every modal before driving the GUI unattended.** Three separate
verification runs have hung on a real `QMessageBox.exec()` waiting for a
click that was never coming, one of them for over two hours. The
operator is often not at the rig, where a blocked run and a slow run
look identical. Enumerate the modal entry points your path can reach and
stub them before you start.

**Run checks in the foreground and paste real output with the exit
code.** A background invocation that captures no output is not a result.
"Completed" reports that the shell exited, not what it exited with.

**Record the commit a measurement ran against.** Not just the number,
and not just the environment. A measurement taken against code that is
being edited around it is not reproducible unless the tree state is
captured with it. Two contradictory on-rig readings of the same quantity
once cost a full session to reconcile, and the only reason it was hard
is that neither reading recorded which version of the function it ran
through.

**A step taken as a guess is recorded as a guess.** Otherwise the next
reader treats it as a finding and reasons across it. A troubleshooting
reboot once entered the record as if it were a boundary, and everyone
downstream believed it.

**When you rely on a prior verification, check what it was run against
and whether that still holds.** Verified results expire without failing.
A path can resolve to a same-named file from a different session; a
"merges cleanly" check goes stale the moment another branch lands.
Neither errors.

**If a permission block or a missing decision stops you, commit what you
have and write the open question into the branch** so it survives the
session.

---

## Boundaries that break quietly

Each of these is stated with its reasoning in `PHILOSOPHY.md`. Listed
here with the accidental-violation shape, because the accidents are what
you are actually at risk of.

**Measurement substrate is green plane or linear master.** The accident:
needing an image's identity for a provenance purpose and hashing whatever
file is nearest to hand. A display-referred derivative is structurally
excluded from the annotation store, so hashing one produces a check that
runs cleanly and answers wrongly every time. Resolve to the actual
measurement substrate.

**Stores are append-only.** The accident: a cleanup pass. There is no
such thing as cleaning up a store here. A wrong value is superseded, not
edited.

**Overlays never touch capturable pixels.** The accident: compositing an
annotation into the image data because it was simpler than a layer.

**Camera-bound operations stay behind `camera_backend.py`.** It is a
driver. Sensor-specific knowledge means full array size, crop geometry
and mode tables, CFA pattern including the no-CFA case, and sensor bit
depth. All of it lives in a sensor-named profile module matching the
hardware's own reported model, importable only by `camera_backend.py`.
White level derives from bit depth, never from container width. No
Picamera2 or libcamera type crosses the seam. The accident: adding a
field to a sensor profile without growing the shape predicate that
discovers profiles, at which point the guard keeps passing while covering
less than it claims. Nothing fails. The boundary just stops being
enforced for new profiles.

**The driver interface must stay satisfiable by a non-libcamera backend.**
Micro-Manager is property-based, has `snapImage`/`getImage`, and has no
sensor-mode crop concept at all. Any method whose contract cannot be met
honestly by such a device is a capability to enumerate, not a required
method to fake. A degenerate but truthful answer is allowed. A fabricated
one never is. `sensor_crop_for_size` returning `(0, 0, w, h)` is the model.

**One session folder contributes one z-stack plane.** A stack spans
*across* session folders via tags. If z-stack code looks like it is
reading one session's captures for multiple planes, that is a previous
bug recurring, not a new one.

**Pure logic is Qt-free and camera-free.** Module-level and testable, not
inline in a widget method.

**`--render-check` coverage is the definition of done.** New logic
without a corresponding assertion is unfinished. Assert the thing that
matters rather than its proxy: "the file exists" is a much weaker claim
than "the file carries the provenance block it is supposed to."

---

## Writing a self-check

Four rules, each of which was learned by a check passing while the user
path stayed broken.

1. **Reach the code the way the application reaches it.** Drive the event
   through real dispatch. Calling a handler directly will pass against a
   handler that was never wired into `eventFilter` at all.
2. **Call the function in the state the application calls it in.** A
   function hardware-verified standalone crashed the moment a user opened
   the dialog that calls it, because the camera was mid-preview and the
   standalone test never was.
3. **Confirm the check itself is reachable and runs.** A structural
   self-check was written, never called from anywhere, and failed
   immediately once wired in.
4. **Assert on structure, not source text.** Inspect namespaces, imports,
   types. Text scanning trips on docstrings that describe the very thing
   being forbidden.

And: confirm the failure before confirming the fix. Reproduce the bug,
then verify it is gone. A check that never exercised the failing sequence
can pass for the wrong reason.

A fifth, learned more recently and the same shape as the others: **run
the check in the environment the software actually runs in, not a
cleaned one.** An acceptance test once passed because it launched with
the very environment variable stripped that broke the feature in normal
use. It validated the mechanism in isolation and never touched the
question it existed to answer. The feature shipped inert and stayed that
way for days.

Tempfile discipline applies to every self-check harness. Nothing writes
into the repo tree.

---

## Guards

```bash
python camera_backend.py --render-check
```

Runs `assert_only_camera_backend_imports_picamera2` (no other file may
import `picamera2` or `libcamera`) and
`assert_only_camera_backend_imports_sensor_profiles` (no other file may
import a sensor-profile module). The second discovers profiles by shape
rather than by a maintained list, so a future `imx519.py` is covered the
moment it exists.

`SWEEP_CHECKS.md` holds the standing check list. Read it, and add to it
when you find a class of defect that nothing would have caught.

Run these before claiming anything is finished. A green run is *fixed*.
On the Pi, follow it with a real run against the instrument before
calling anything *confirmed*.

---

## Two other rules with teeth

**When two consecutive fixes fail on the same symptom, stop reasoning and
go get real data.** One bug in this project's history survived five
hypothesis-driven fixes, each of which sounded correct. What solved it
was reading the library's own documentation and building a minimal
reproduction that isolated the failing case.

**Evidence, never a gate.** Detectors surface findings and let a human
decide. Nothing here auto-corrects, auto-excludes, or blocks. If you find
yourself adding an automatic decision on top of a detector, stop.
Surfacing a problem and deciding what to do about it are different jobs,
and this project deliberately automates only the first.

---

## If you are unsure

Ask, and be specific about what you are unsure of. Flag it *before*
building on top of it. A question costs one exchange. A wrong assumption
baked into working code costs considerably more, and nearly every
expensive problem in this project's history was cheap to fix at the point
it first became noticeable.
