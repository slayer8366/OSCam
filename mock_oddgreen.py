"""mock_oddgreen.py - a MOCK sensor fixture, not a real camera and not a
dispatchable driver (no crop_for_size, so this deliberately does not satisfy
camera_backend.py's sensor-profile-module contract or trip
assert_only_camera_backend_imports_sensor_profiles -- see that function's
own docstring for why only a REAL driver needs to avoid being imported
elsewhere; a test fixture is not one).

Exists only to give the multi-sensor audit's Tasks 2-5 a concrete
regression target: a Bayer sensor whose full-array resolution is ODD in
both axes, so the green plane debayer.extract_green() actually produces is
CEIL-sized, not the FLOOR-sized (FULL_RES[0] // 2, FULL_RES[1] // 2) every
one of measure.py's/qt_shell.py's GREEN_PLANE_RES-style constants assumes.
A real, correctly-extracted green-plane file from a sensor shaped like this
is rejected outright by measure.load_measurement_plane's shape-based
dispatch today. See test_sensor_generality.py.
"""

FULL_ARRAY_SIZE = (4001, 3001)   # odd both axes, deliberately
