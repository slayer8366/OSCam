# Zynergy

**Scientific microscopy capture and analysis suite for the Raspberry Pi**

Live camera preview, guided capture (single frames, flat/dark calibration
sequences, HDR bracketing, video), spatial and chromatic-aberration
calibration, z-stack review, and a real measurement GUI, all built around
one rule: a number reported by this software can always be traced back to
the raw sensor data and the exact calibration that produced it.

## Measurement integrity — read this first

This is the actual design center of the project, not a footnote.

- **Measurements are made on the green plane** (one de-mosaiced green
  channel, half the sensor's resolution each axis) **or on a linear
  master. Never on a display-referred derivative** — sharpening,
  tonemapping, and CLAHE all move apparent edge positions, so anything
  that has been through them is disqualified as a measurement surface,
  not just discouraged.

- **The live preview is for aiming, not measuring.** Overlays (the focus
  box, the XY ruler) are a vector layer composited on top of the video
  feed. They never touch a capturable pixel.

- **Calibration is append-only.** Redoing a calibration adds a new entry
  and chains it to the one it supersedes; nothing already saved is ever
  edited or deleted. Every entry carries full provenance: objective,
  reduction lens, target type, focus score at capture, and which CFA
  pattern / green channel was used. This applies identically to both
  calibration stores — spatial (µm/px) and chromatic aberration.

- **Every measurement is hash-pinned.** Marks are keyed to the
  `pixel_sha256` of the exact green-plane image they were made on, and
  each one records precisely which calibration entry was in force at the
  time. A published number states which image and which calibration it
  came from, not just the figure.

- **Evidence, never a gate.** Several checks in this project — a stale
  calibration (objective or rig config changed since it was recorded), a
  soft z-stack plane, unusual chromatic-aberration curvature — are
  detected and surfaced, but never auto-block or auto-correct anything.
  A human decides what to do with the evidence. Nothing in this suite
  silently fixes or silently hides a problem.

- **All camera-bound operations sit behind one thin adapter**
  (`camera_backend.py`). Everything else — calibration, measurement,
  processing, export — can be developed and tested with no camera
  attached.

## Hardware

- Raspberry Pi 5
- IMX477 HQ camera
- 0.5x reduction lens (fixed; recorded in every calibration entry)
- Argon NEO 5 M.2 NVMe expansion board + Fikwot FN501 Pro NVMe SSD
- SD card repurposed as an archive volume
- Labwc compositor

## Software

- Python 3, PyQt5, Picamera2, NumPy, tifffile
- `FfmpegOutput` / ffmpeg (video recording, `.mp4` container)

## Quick start

Live capture GUI, real camera:
```bash
python3 qt_shell.py --camera
```

Live capture GUI, no camera attached (development/testing, uses a
simulated camera backend):
```bash
python3 qt_shell.py
```

Spatial calibration (µm/px), standalone or via its own wizard:
```bash
python3 calibrate.py [image] [--objective NAME]
```

Chromatic aberration calibration, standalone or via its own wizard:
```bash
python3 ca_measure.py [target] -o out.json [--plot out.png]
python3 ca_measure.py --wizard
```

Analysis GUI — measurement tools, z-stack review, export, publish:
```bash
python3 measure.py [image] [--objective NAME]
```

`qt_shell.py` also reaches `calibrate.py` and `measure.py` directly from
its own **Calibrate** and **Measure** menus, each opening the real tool as
a separate window rather than requiring a second terminal. (Chromatic
aberration calibration currently runs standalone only — see *Known
limitations* below.)

Every module with real logic has a headless self-check. Run the whole
set before trusting anything:
```bash
for m in pixel_hash annotations export publish calibrate measure ca_measure \
        wizard_pages qt_shell stacks focus; do
  DISPLAY=:0 python3 $m.py --render-check || echo "FAILED: $m"
done
```
`qt_shell.py` and `measure.py` have PyQt5-gated checks that print
`SKIPPED` rather than `FAILED` when PyQt5 isn't importable — that's
expected, not a bug to chase.

## Architecture

**`qt_shell.py`** is the live capture GUI and the project's main entry
point: focus aid, exposure panel, capture and burst/HDR walkthroughs
(arm-then-fire on the Capture button, a real worker thread underneath),
z-stack plane tagging, the XY ruler overlay, video recording, and the
Calibrate and Measure menus. Session and profile management (what used
to be a separate `capture.py`) lives here now — that logic wasn't
sensor-specific and had no reason to be its own module.

**`camera_backend.py`** is the only thing that talks to the camera. An
abstract interface, a `FakeCamera` for headless development, and a
`Picamera2Camera` for the real hardware. Nothing else in the project
imports Picamera2 directly.

**`calibrate.py`**, **`ca_measure.py`**, **`annotations.py`**, and
**`measure.py`** are the measurement chain: calibrate a spatial scale and
a chromatic-aberration correction per objective, store marks against a
hashed image, then view and measure them in a proper canvas GUI —
distance, angle, polygon, and a real ellipse fit for round spores
(length, width, area, and Q ratio from one fitted shape). `measure.py`
also reviews z-stacks (a lit/dimmed filmstrip, an onion-skin overlay of
neighboring planes) and can export or publish results directly.

**`debayer.py`**, **`frame_average.py`**, **`hdr_merge.py`**, and
**`hdr_from_session.py`** are the processing chain from a raw capture
session to a measurement-ready green-plane master or a tonemapped
display image, with every output explicitly tagged for whether it's fit
for measurement.

**`stacks.py`** groups and processes multi-plane focus stacks — a stack
spans *across* session folders via tags, never assembled from one
session's own captures. **`focus.py`** scores sharpness (Laplacian
variance) for the live focus aid, as recorded evidence on a calibration
entry, and as a post-capture QC score on each stack plane. **`pixel_hash.py`**
is the identity function everything else keys measurements to.

**`ca_lib.py`** and **`ca_measure.py`** handle chromatic aberration
calibration and measurement, a separate correction from spatial
calibration, with its own append-only, supersedes-chained store.

**`export.py`** and **`publish.py`** close the loop: export flattens the
annotation store into a results file (every record carries its
`calibration_ref`); publish assembles a full package (green plane,
results, a manifest naming the provenance chain, and optional
display/marked-up derivatives, each explicitly labeled `"NOT a
measurement"` if they carry burned-in marks).

**`wizard_pages.py`** is a shared "pick an existing image or shoot a new
one" page used by `calibrate.py`'s, `ca_measure.py`'s, and `measure.py`'s
wizards.

**`test_burst_backend.py`** covers the burst capture path specifically.

## Data locations

All three JSON stores are append-only with a `supersedes` chain — never
edited or deleted, only added to.

| Path | Contents |
|---|---|
| `~/.zynergy/calibration.json` | Spatial (µm/px), keyed by objective |
| `~/.zynergy/ca_calibration.json` | Chromatic aberration, keyed by objective |
| `~/.zynergy/annotations.json` | Measurement marks, keyed by `pixel_sha256` |
| `~/imx/profile.json` | Camera exposure/gain/white-balance profile |
| `~/captures/<timestamp>/` | Session folders (`session.json` + raw frames) |
| `~/captures/adhoc/` | Ad hoc wizard-shot images (not full sessions) |

## Testing

Nothing here should be trusted on claims alone. Every module with real
logic ships a self-check, and the project's own discipline is to run
`py_compile` plus the self-check before calling anything done, then
confirm the actual behavior on real hardware separately — headless
checks and on-rig behavior have diverged before, and both matter.

## Known limitations / open items

- Chromatic aberration calibration (`ca_measure.py`'s `CAWizard`) isn't
  yet reachable from `qt_shell.py`'s own menu, unlike spatial calibration
  and measurement. Its live-capture path builds its own camera instance
  independent of the one the main window already holds, which needs
  resolving before that path can be wired in safely.
- The poly2 chromatic-aberration correction model (`m(r) = 1 + c1·r² +
  c2·r⁴`) is deliberately deferred. The detector (`poly2_flag`) exists
  and works, but no real target has shown that curvature yet, and
  building the correction without real evidence to validate against
  would be speculative.
- `measure.py`: after committing a mark, the tool-status line doesn't
  reset (a polygon commit still shows "double-click to finish").
  Cosmetic, not a data issue.
- Video recording resolution isn't yet user-adjustable at record time;
  it always encodes at the live preview's resolution. The setter for
  this exists and is documented but not wired to a menu yet.
- Recorded video is documentation/review quality (compressed H.264),
  deliberately separate from the measurement path. A raw,
  measurement-grade capture mode is a distinct possible future feature,
  not a replacement for this one.

## License

_[ fill in — not yet specified ]_
