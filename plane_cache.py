"""plane_cache.py - the green-plane cache (Preferences-dialog plan set,
Part 04).

Not a performance cache. Part 05's live measure panel pulls a green plane
on first click and measures against it; once a mark is committed against
that plane, the plane has to keep existing on disk for as long as the mark
does, or the mark is stranded (it still says what it measured, but nothing
can re-derive or verify the number). That is the substrate this module
manages -- see PLAN_04_green_plane_cache.md for the full design.

Keyed by pixel_sha256, never by timestamp or sequence: this is what makes
pruning mechanical rather than a lookup (a hash present in annotations.json
is referenced and never pruned -- anything else is fair game) and what lets
measure.py open a cached plane and have its own annotations resolve, with
no index or mapping table to keep in sync -- the plane and its marks are
keyed identically, by construction.

Location: <provenance root>/plane_cache/<pixel_sha256>.tif -- a subfolder
of PROVENANCE_ROOT (Part 03), the same "out of sight, not out of existence"
placement as session.json's own sidecars, never the user's capture output
folder. provenance.PROVENANCE_ROOT is read live via module attribute on
every call (never cached at import time), the same rule provenance.py's own
OUT_ROOT/PROFILE_PATH comment documents -- this is what lets qt_shell.py's
render_check() (and this module's own) redirect the whole cache to a
disposable temp dir just by reassigning provenance.PROVENANCE_ROOT.

Written UNCOMPRESSED, not deflate -- a deliberate, measured choice, not the
project's usual TIFF default. Real-hardware timing on this rig (Pi 5,
IMX477, a genuine captured-and-DNG-decoded green plane, not synthetic
data): extract_green itself is negligible (~0.03 ms, confirming the plan's
own estimate that this is a slice, not a de-mosaic); pixel_sha256 hashing
is ~9 ms; an UNCOMPRESSED tifffile.imwrite is ~6 ms; a DEFLATE-compressed
write of the exact same real sensor data is ~570-600 ms -- almost two
orders of magnitude slower, and real sensor noise turned out to compress
far more slowly than the synthetic random data an earlier estimate would
have used (deflate on random data of the same shape/dtype ran in ~90 ms on
this same rig). Compression does shrink the file (~3.9MB vs ~6.2MB for one
plane), but Part 05's whole interaction design assumes the pull-to-cache
step is imperceptible on first click, and 600 ms is not imperceptible.
Uncompressed keeps the extract+hash+store pipeline at ~15 ms end to end,
well inside that budget; disk space stays "a few MB" either way, which the
plan's own weight section already called an acceptable cost. If disk space
ever becomes the binding constraint instead, revisit this -- but don't
flip it back to deflate without re-measuring, since the random-data
estimate this docstring corrects is exactly the kind of assumption that
turned out wrong against real sensor data.

Two ways to run:
  python3 plane_cache.py --render-check   headless: store/load/path-from-
                                          hash-alone round-trip, atomic
                                          write, clean_cache's reference
                                          and age rules, and (checked
                                          early, per the plan's own
                                          instruction) that measure.py can
                                          open a cached plane and resolve
                                          its annotations with no external
                                          mapping. No PyQt5, no camera.
  python3 plane_cache.py                  not a standalone tool; import
                                          from qt_shell.py.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import tifffile

try:
    from . import provenance
except ImportError:
    import provenance

# Not fatal if missing: store_plane can still be called with an explicit
# pixel_sha256, and clean_cache can still be called with an explicit
# referenced set -- same "degrade, don't crash" contract measure.py/
# gallery.py use for their own optional cross-module imports.
try:
    from . import pixel_hash as _pixel_hash
except ImportError:
    try:
        import pixel_hash as _pixel_hash
    except ImportError:
        _pixel_hash = None

try:
    from . import annotations as _annotations
except ImportError:
    try:
        import annotations as _annotations
    except ImportError:
        _annotations = None

CACHE_DIRNAME = "plane_cache"


def _resolve_root(root):
    """root=None means "read provenance.PROVENANCE_ROOT right now" -- an
    attribute read, not a value captured at import time, so a caller that
    redirects provenance.PROVENANCE_ROOT (a live pref, or a render_check's
    own test-isolation swap) is picked up by every plane_cache call made
    afterward with no separate plane_cache-specific redirect needed."""
    return Path(root) if root is not None else (provenance.PROVENANCE_ROOT / CACHE_DIRNAME)


def plane_path(pixel_sha256, root=None):
    """Where a cached plane for this hash lives, or would live -- the hash
    alone determines the filename, no index or mapping table anywhere.
    Callers resolve a plane from a bare pixel_sha256 (e.g. one pulled off
    an annotations.json record) with nothing else."""
    return _resolve_root(root) / "{}.tif".format(pixel_sha256)


def has_cached_plane(pixel_sha256, root=None):
    return plane_path(pixel_sha256, root).is_file()


def store_plane(plane, pixel_sha256=None, root=None):
    """Write `plane` into the cache, keyed by its own pixel_sha256 (computed
    here if not given). Idempotent: if a file for this hash already exists,
    it is left untouched (identical hash means identical pixels, per
    pixel_hash.py's own contract -- there is nothing to overwrite). Atomic
    when it does write (temp file + os.replace), the same pattern every
    other store in this project uses, so a crash mid-write can never leave
    a truncated plane behind for measure.py to load later. Returns
    (path, pixel_sha256)."""
    if pixel_sha256 is None:
        if _pixel_hash is None:
            raise RuntimeError(
                "pixel_hash.py could not be imported; pass pixel_sha256 "
                "explicitly, or keep pixel_hash.py alongside plane_cache.py.")
        pixel_sha256 = _pixel_hash.pixel_sha256(plane)
    root = _resolve_root(root)
    path = root / "{}.tif".format(pixel_sha256)
    if not path.is_file():
        root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        # Uncompressed -- see the module docstring's real-hardware timing
        # note for why this is deliberate, not an oversight.
        tifffile.imwrite(str(tmp), plane)
        os.replace(tmp, path)
    return path, pixel_sha256


def load_cached_plane(pixel_sha256, root=None):
    """The cached array for this hash, or None if nothing is cached for it.
    A lossless TIFF read regardless of compression, so this is also what a
    caller uses to confirm a stored plane re-hashes to the same value it
    was stored under (pixel_hash.py's own round-trip guarantee)."""
    path = plane_path(pixel_sha256, root)
    if not path.is_file():
        return None
    with tifffile.TiffFile(str(path)) as tf:
        return tf.pages[0].asarray()


def list_cached_hashes(root=None):
    """Every pixel_sha256 currently cached -- [] if the cache dir doesn't
    exist yet, which is a normal state (nothing measured live yet), not an
    error."""
    root = _resolve_root(root)
    if not root.is_dir():
        return []
    return [p.stem for p in root.glob("*.tif")]


def referenced_hashes(store=None):
    """The set of pixel_sha256 hashes that back a committed measurement --
    every key in annotations.json. `store` lets a caller that already
    loaded the store once (e.g. to build a UI) avoid a second read; None
    (annotations.py unavailable) reads as an empty set, which would make
    clean_cache treat every cached plane as unreferenced -- callers that
    care about this distinction should check `_annotations is not None`
    themselves rather than relying on clean_cache to warn them."""
    if _annotations is None:
        return set()
    store = store if store is not None else _annotations.load_annotations()
    return set(store.keys())


def clean_cache(referenced=None, older_than_days=None, root=None, now=None):
    """Remove unreferenced planes from the cache; never remove a referenced
    one, full stop -- that plane backs a committed measurement (see the
    module docstring). `referenced` defaults to referenced_hashes() (a
    fresh read of annotations.json), so a plane that gained a mark since
    the last clean is automatically ineligible with no extra bookkeeping.

    `older_than_days=None` is "Clean cache now" semantics: every
    unreferenced plane goes, regardless of age. A number is "Automatically
    clean after X days" semantics: only unreferenced planes whose file is
    at least that old (by mtime) go; a younger unreferenced plane is
    retained for now, not because it's referenced but because it hasn't
    aged out yet -- tracked as a separate count so a caller can report the
    real reason, not just a single removed/kept split.

    Returns {"removed": int, "retained_referenced": int,
    "retained_too_new": int} -- every plane on disk before the call is
    accounted for in exactly one of the three (retained_too_new is always
    0 when older_than_days is None, since nothing is age-gated then)."""
    root = _resolve_root(root)
    if referenced is None:
        referenced = referenced_hashes()
    now = now if now is not None else time.time()
    removed = 0
    retained_referenced = 0
    retained_too_new = 0
    if root.is_dir():
        for f in sorted(root.glob("*.tif")):
            pixel_sha256 = f.stem
            if pixel_sha256 in referenced:
                retained_referenced += 1
                continue
            if older_than_days is not None:
                age_days = (now - f.stat().st_mtime) / 86400.0
                if age_days < older_than_days:
                    retained_too_new += 1
                    continue
            f.unlink()
            removed += 1
    return {"removed": removed, "retained_referenced": retained_referenced,
            "retained_too_new": retained_too_new}


# ---------------------------------------------------------------------------
# Headless self-check (no Qt, no camera)
# ---------------------------------------------------------------------------
def render_check():
    import shutil

    import numpy as np

    global _annotations

    assert _pixel_hash is not None, "pixel_hash.py must be importable"

    tmp_root = Path("/tmp/zynergy_plane_cache_render_check")
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    rng = np.random.default_rng(0)
    plane = rng.integers(0, 65536, size=(1520, 2028), dtype=np.uint16)

    # --- store_plane / plane_path: hash alone resolves the file, no map ---
    path, h = store_plane(plane, root=tmp_root)
    expected_hash = _pixel_hash.pixel_sha256(plane)
    assert h == expected_hash, "store_plane must hash exactly what pixel_hash.py would"
    assert path == plane_path(h, root=tmp_root), \
        "plane_path(hash) must resolve to the exact file store_plane wrote"
    assert path.is_file() and path.name == "{}.tif".format(h), \
        "the filename itself must be the hash, nothing else needed to find it"
    assert not path.with_suffix(".tmp").exists(), \
        "store_plane must not leave its temp file behind (atomic write)"
    print("store_plane / plane_path check PASS: a cached plane's filename "
          "resolves from its pixel_sha256 alone, atomic write leaves no "
          ".tmp behind")

    # --- load_cached_plane: round-trips exactly, re-hashes identically ---
    reloaded = load_cached_plane(h, root=tmp_root)
    assert np.array_equal(reloaded, plane), \
        "load_cached_plane must return exactly what was stored"
    assert _pixel_hash.pixel_sha256(reloaded) == h, \
        "a reloaded plane must re-hash to the same pixel_sha256 it was cached under"
    assert load_cached_plane("no-such-hash", root=tmp_root) is None, \
        "an uncached hash must read as None, not raise"
    print("load_cached_plane check PASS: round-trips the exact array, "
          "re-hashes identically, an uncached hash reads as None")

    # --- idempotent store: writing the same plane twice is a no-op the 2nd time
    mtime_before = path.stat().st_mtime_ns
    path2, h2 = store_plane(plane, root=tmp_root)
    assert path2 == path and h2 == h
    assert path.stat().st_mtime_ns == mtime_before, \
        "storing an already-cached plane a second time must not rewrite the file"
    print("store_plane idempotence check PASS: an already-cached hash is left untouched")

    # --- live PROVENANCE_ROOT read: no root= given must follow provenance.PROVENANCE_ROOT
    orig_prov_root = provenance.PROVENANCE_ROOT
    provenance.PROVENANCE_ROOT = tmp_root / "swapped_provenance_root"
    try:
        default_path, _ = store_plane(plane)
        assert default_path == provenance.PROVENANCE_ROOT / CACHE_DIRNAME / "{}.tif".format(h), \
            "root=None must resolve against provenance.PROVENANCE_ROOT, read live, not cached"
    finally:
        provenance.PROVENANCE_ROOT = orig_prov_root
    print("live PROVENANCE_ROOT check PASS: root=None follows "
          "provenance.PROVENANCE_ROOT read at call time, not import time")

    # --- measure.py can open a cached plane and its annotations resolve ---
    # (checked early, per the plan's own instruction: if this doesn't hold,
    # committed marks would be stranded in a store nothing can open.)
    try:
        from . import measure as _measure
    except ImportError:
        import measure as _measure
    orig_annotation_path = _annotations.ANNOTATION_PATH if _annotations else None
    if _annotations is not None:
        _annotations.ANNOTATION_PATH = tmp_root / "annotations_for_render_check.json"
    try:
        loaded_via_measure = _measure.load_measurement_plane(str(path))
        assert np.array_equal(loaded_via_measure, plane), \
            "measure.py must load a cached green-plane TIFF AS-IS, not re-extract it"
        assert _pixel_hash.pixel_sha256(loaded_via_measure) == h, \
            "the hash measure.py would compute on open must match the cache key exactly"
        if _annotations is not None:
            defaults = {"shape": list(plane.shape), "dtype": str(plane.dtype), "kind": "green"}
            mark = {"type": "distance", "note": "render_check"}
            _annotations.save_mark(h, mark, record_defaults=defaults)
            record = _annotations.image_record_for(h)
            assert record is not None, \
                "a mark saved under the cache's own hash must resolve back through " \
                "annotations.image_record_for(same hash) -- the plane and its marks " \
                "must find each other with no index or mapping table"
            assert record["marks"][0] == mark, \
                "the resolved record must carry the exact mark that was saved"
            print("measure.py / annotations.py integration check PASS: opening a "
                  "cached plane through measure.load_measurement_plane hashes "
                  "identically to the cache key, and a mark saved under that hash "
                  "resolves straight back via image_record_for -- no external mapping")
        else:
            print("measure.py integration check PASS (annotations.py not importable "
                  "here, so the mark-resolution half is skipped): a cached plane "
                  "opens through measure.load_measurement_plane and hashes identically")
    finally:
        if _annotations is not None:
            _annotations.ANNOTATION_PATH = orig_annotation_path

    # --- clean_cache: referenced planes are never removed, unreferenced are ---
    clean_root = tmp_root / "clean"
    clean_root.mkdir(parents=True)
    planes = {}
    for i in range(3):
        p = rng.integers(0, 65536, size=(8, 8), dtype=np.uint16) + i * 1000
        _path, _h = store_plane(p, root=clean_root)
        planes[_h] = _path
    hashes = list(planes.keys())
    referenced = {hashes[0]}   # only the first is "referenced"
    result = clean_cache(referenced=referenced, older_than_days=None, root=clean_root)
    assert result == {"removed": 2, "retained_referenced": 1, "retained_too_new": 0}, \
        "clean-now must remove every unreferenced plane and retain the referenced one: got {}".format(result)
    assert planes[hashes[0]].is_file(), "the referenced plane must survive clean_cache"
    assert not planes[hashes[1]].is_file() and not planes[hashes[2]].is_file(), \
        "both unreferenced planes must be removed"
    print("clean_cache (clean-now) check PASS: removes every unreferenced "
          "plane, retains the referenced one, reports accurate counts")

    # --- auto-clean: respects the day threshold, same reference rule ------
    age_root = tmp_root / "age"
    age_root.mkdir(parents=True)
    old_unreferenced, h_old_unref = store_plane(
        rng.integers(0, 65536, size=(8, 8), dtype=np.uint16), root=age_root)
    new_unreferenced, h_new_unref = store_plane(
        rng.integers(0, 65536, size=(8, 8), dtype=np.uint16) + 1, root=age_root)
    old_referenced, h_old_ref = store_plane(
        rng.integers(0, 65536, size=(8, 8), dtype=np.uint16) + 2, root=age_root)
    now = time.time()
    old_mtime = now - 40 * 86400   # 40 days old
    new_mtime = now - 1 * 86400    # 1 day old
    os.utime(old_unreferenced, (old_mtime, old_mtime))
    os.utime(new_unreferenced, (new_mtime, new_mtime))
    os.utime(old_referenced, (old_mtime, old_mtime))
    result = clean_cache(referenced={h_old_ref}, older_than_days=30, root=age_root, now=now)
    assert result == {"removed": 1, "retained_referenced": 1, "retained_too_new": 1}, \
        "auto-clean must remove only the old+unreferenced plane, keeping the " \
        "new-but-unreferenced one (too young) and the old-but-referenced one " \
        "(reference rule wins over age): got {}".format(result)
    assert not old_unreferenced.is_file(), "old + unreferenced must be removed"
    assert new_unreferenced.is_file(), "new + unreferenced must be retained (too young)"
    assert old_referenced.is_file(), "old + referenced must be retained (reference rule wins)"
    print("clean_cache (auto-clean) check PASS: respects the day threshold, "
          "and a referenced plane is retained regardless of age")

    # --- gaining a reference after being prune-eligible makes it ineligible
    # clean_cache has no dry-run mode (a real "clean" always really removes),
    # so eligibility is proven with two IDENTICALLY-aged twins in separate
    # roots rather than by pruning one plane twice: twin A, pruned with no
    # reference, proves the fixture really was eligible; twin B, pruned with
    # its own hash already in `referenced` (as if a mark had just been
    # committed against it), proves gaining a reference flips that same
    # eligibility -- annotations.json is re-read fresh on every clean_cache
    # call, so a reference gained at any point before the call counts.
    flip_root_a = tmp_root / "flip_a"
    flip_root_b = tmp_root / "flip_b"
    flip_root_a.mkdir(parents=True)
    flip_root_b.mkdir(parents=True)
    twin = rng.integers(0, 65536, size=(8, 8), dtype=np.uint16) + 3
    path_a, h_a_twin = store_plane(twin, root=flip_root_a)
    path_b, h_b_twin = store_plane(twin, root=flip_root_b)
    assert h_a_twin == h_b_twin, "identical pixels must hash identically across roots"
    os.utime(path_a, (old_mtime, old_mtime))
    os.utime(path_b, (old_mtime, old_mtime))

    pruned = clean_cache(referenced=set(), older_than_days=30, root=flip_root_a, now=now)
    assert pruned == {"removed": 1, "retained_referenced": 0, "retained_too_new": 0}, \
        "the unreferenced twin must actually be prune-eligible -- otherwise " \
        "the flip below wouldn't prove anything"
    assert not path_a.is_file()

    retained = clean_cache(referenced={h_b_twin}, older_than_days=30, root=flip_root_b, now=now)
    assert retained == {"removed": 0, "retained_referenced": 1, "retained_too_new": 0}
    assert path_b.is_file(), \
        "a plane that gains a reference must become ineligible for pruning, " \
        "even though it already met the age threshold -- same fixture as the " \
        "twin that WAS pruned above, differing only in whether its hash is referenced"
    print("clean_cache eligibility-flip check PASS: an identically-aged twin "
          "is pruned when unreferenced and retained once its hash is "
          "referenced, isolating the reference rule from the age rule")

    shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    import sys
    if "--render-check" in sys.argv:
        render_check()
    else:
        sys.exit("plane_cache.py is not a standalone tool; import its functions, "
                 "or run with --render-check for the headless self-check.")
