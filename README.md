# Zynergy

**Scientific microscopy capture and analysis suite for the Raspberry Pi**

Live camera preview, guided capture (single frames, flat/dark calibration
sequences, HDR bracketing, video), spatial calibration, and a real
measurement GUI, all built around one rule: a number reported by this
software can always be traced back to the raw sensor data and the exact
calibration that produced it.

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
  pattern / green channel was used.

- **Every measurement is hash-pinned.** Marks are keyed to the
  `pixel_sha256` of the exact green-plane image they were made on, and
  each one records precisely which calibration entry was in force at the
  time. A published number states which image and which calibration it
  came from, not just the figure.

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

Spatial calibration (creates or redoes a calibration entry for one
objective):
```bash
python3 calibrate.py [image] [--objective NAME]
```

Analysis GUI (measure on an already-calibrated image):
```bash
python3 measure.py [image] [--objective NAME]
```

Every module below that touches meaningful logic has its own headless
self-check, runnable with no camera and (mostly) no PyQt5:
```bash
python3 <module>.py --render-check
python3 camera_backend.py        # self-check runs directly
python3 annotations.py           # self-check runs directly
```

## Architecture

**`qt_shell.py`** is the live capture GUI: focus aid, exposure panel,
capture and burst/HDR walkthroughs (arm-then-fire on the Capture button,
a real worker thread underneath), the XY ruler overlay, the calibration
menu, and video recording. It also carries what used to be a separate
`capture.py` — session and profile management now live inside it.

**`camera_backend.py`** is the only thing that talks to the camera. An
abstract interface, a `FakeCamera` for headless development, and a
`Picamera2Camera` for the real hardware. Nothing else in the project
imports Picamera2 directly.

**`calibrate.py`**, **`annotations.py`**, and **`measure.py`** are the
measurement chain: calibrate a spatial scale per objective, store marks
against a hashed image, view and measure them in a proper canvas GUI
(distance, angle, polygon, and a real ellipse fit for round spores —
length, width, area, and Q ratio from one fitted shape).

**`debayer.py`**, **`frame_average.py`**, **`hdr_merge.py`**, and
**`hdr_from_session.py`** are the processing chain from a raw capture
session to a measurement-ready green-plane master or a tonemapped
display image, with every output explicitly tagged for whether it's fit
for measurement.

**`stacks.py`** and **`zstack_process.py`** group and process
multi-plane focus stacks. **`focus.py`** scores sharpness (Laplacian
variance) for both the live focus aid and as recorded evidence on a
calibration entry. **`pixel_hash.py`** is the identity function
everything else keys measurements to.

**`ca_lib.py`** and **`ca_measure.py`** handle chromatic aberration
calibration and measurement, a separate correction from spatial
calibration.

**`export.py`** and **`publish.py`** close the loop: export flattens the
annotation store into a results file (every record carries its
`calibration_ref`); publish assembles a full package (green plane,
results, a manifest naming the provenance chain, and optional
display/marked-up derivatives, each explicitly labeled `"NOT a
measurement"` if they carry burned-in marks).

**`wizard_pages.py`** is a shared "pick an existing image or shoot a new
one" page used by both `calibrate.py`'s and `measure.py`'s wizards.

**`test_burst_backend.py`** covers the burst capture path specifically.

## Testing

Nothing here should be trusted on claims alone. Every module with real
logic ships a self-check, and the project's own discipline is to run
`py_compile` plus the self-check before calling anything done, then
confirm the actual behavior on real hardware separately — headless
checks and on-rig behavior have diverged before, and both matter.

## Known limitations / open items

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
