"""mock_mono.py - a MOCK sensor fixture, not a real camera and not a
dispatchable driver (no crop_for_size -- see mock_oddgreen.py's own
docstring for why a test fixture is exempt from the "only camera_backend.py
imports a profile module" rule).

Exists only to give the multi-sensor audit's Tasks 2-5 a concrete
regression target: a MONOCHROME sensor with no colour filter array at all.
CFA_PRESENT = False is read by NOTHING in this project today -- neither
camera_backend.get_capabilities(), nor debayer.py, nor measure.py's plane-
loading dispatch. That absence is exactly the gap this fixture exists to
prove, not paper over. See test_sensor_generality.py.
"""

FULL_ARRAY_SIZE = (1920, 1080)
CFA_PRESENT = False   # read by nothing today -- that's the point
