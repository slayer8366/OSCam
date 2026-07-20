# zynergy-imaging

Microscopy capture + photon-faithful image pipeline (Zynergy, LLC).

## Install (Raspberry Pi or any machine)

    cd zynergy-imaging
    pip install -e .            # base: numpy + tifffile only
    pip install -e ".[all]"     # + scipy, scikit-image, pillow, matplotlib

Camera capture also needs the Pi system stack (not a pip package):

    sudo apt install python3-picamera2

The processing tools (frame-average, hdr-merge, debayer, ca-measure,
hdr-from-session) run without the camera stack, so you can process on any
machine.

## Commands

| command            | module                          |
|--------------------|---------------------------------|
| capture            | zynergy_imaging.capture         |
| hdr-from-session   | zynergy_imaging.hdr_from_session|
| frame-average      | zynergy_imaging.frame_average   |
| hdr-merge          | zynergy_imaging.hdr_merge       |
| debayer            | zynergy_imaging.debayer         |
| ca-measure         | zynergy_imaging.ca_measure      |

All run from any directory once installed.

## Notes

- Provenance stays **embedded** in each output's TIFF ImageDescription tag
  (the working model in these scripts). prov.py's sidecar model was left out
  by design.
- `ca_lib.py` and the six command modules live in `src/zynergy_imaging/`.
- hdr-from-session orchestrates frame-average -> hdr-merge -> debayer by
  calling the sibling .py files inside the installed package, so that flow is
  unchanged.
