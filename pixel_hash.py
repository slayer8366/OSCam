"""pixel_hash.py - the content hash that is the identity for the measurement chain.

A mark, a calibration reference, a published figure: all of them bind to an image
by the hash of its DECODED pixels, not its path, so the link survives a chaotic
filesystem. This is the pixel_sha256 from the checklist, and the whole annotation
and provenance design rests on its two properties: the same pixels hash the same
on any machine, and anything that is not the same pixels hashes differently.

Kept separate from the file hash (sha256_file, used for the provenance chain where
the artifact genuinely is the file). Here the artifact is the measurement, so the
key is the pixels.
"""
import hashlib
import sys

import numpy as np

__version__ = "1.0"


def pixel_sha256(array):
    """Content hash of a decoded pixel array.

    Folds three things into one sha256, in a canonical form:
      * the pixel bytes, normalized to little-endian (a no-op on the Pi and x86,
        but it means the same values hash the same on any machine),
      * the dtype (so a uint16 array and an int16 array of the same bits differ),
      * the shape (so the same values at a different shape differ; the raw bytes
        alone are shape-blind).

    Hash the array AS DECODED from the stored file, never a freshly recomputed
    one. A deflate-TIFF decode is deterministic and re-hashes identically every
    time, floats included; a recomputed array is not stable across numpy versions
    or summation order, and would orphan a mark for no real reason.
    """
    a = np.ascontiguousarray(array)
    bo = a.dtype.byteorder
    # '<' little, '>' big, '=' native, '|' single-byte (order not applicable)
    big = (bo == ">") or (bo == "=" and sys.byteorder == "big")
    # byteswap on a big-endian array leaves its raw bytes in little-endian order;
    # this sidesteps astype, which can skip the swap because numpy treats '<u2'
    # and '>u2' as equal dtypes.
    data = a.byteswap().tobytes() if big else a.tobytes()
    dtype_str = a.dtype.newbyteorder("<").str          # e.g. '<u2', '<f4', '|u1'

    h = hashlib.sha256()
    header = "{}:{}:{}".format(dtype_str, a.ndim,
                               "x".join(str(n) for n in a.shape)).encode("ascii")
    h.update(header)
    h.update(b"\x00")                                  # separate header from data
    h.update(data)
    return h.hexdigest()


def hash_tiff(path):
    """Load a stored TIFF and hash its decoded pixel array. The intended entry
    point for a saved green plane: you fingerprint the pixels of a file on disk,
    deterministically."""
    import tifffile
    return pixel_sha256(tifffile.imread(str(path)))


if __name__ == "__main__":
    import os
    import tempfile
    import tifffile

    rng = np.random.default_rng(0)

    # 1) round-trip: hash an array, write a deflate TIFF, reload, re-hash -> same.
    #    This is the load-bearing guarantee: a stored green plane re-hashes to its
    #    record every time, so a mark made today still resolves next week.
    green = rng.integers(0, 65536, size=(2028, 1520), dtype=np.uint16)
    h_mem = pixel_sha256(green)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "green.tif")
        tifffile.imwrite(p, green, compression="deflate")
        h_disk = hash_tiff(p)
    assert h_mem == h_disk, "round-trip through TIFF changed the hash"
    print("round-trip stable:     ", h_mem[:16], "==", h_disk[:16])

    # 2) shape sensitivity: identical bytes, different shape -> different hash.
    flat = rng.integers(0, 256, size=(1024,), dtype=np.uint8)
    h_a = pixel_sha256(flat.reshape(32, 32))
    h_b = pixel_sha256(flat.reshape(16, 64))
    assert h_a != h_b, "different shapes collided"
    print("shape distinguished:   ", h_a[:16], "!=", h_b[:16])

    # 3) dtype sensitivity: same raw bits, uint16 vs int16 -> different hash.
    bits = rng.integers(0, 65536, size=(200,), dtype=np.uint16)
    h_u = pixel_sha256(bits)
    h_i = pixel_sha256(bits.view(np.int16))            # same bytes, reinterpreted
    assert h_u != h_i, "uint16 and int16 of the same bits collided"
    print("dtype distinguished:   ", h_u[:16], "!=", h_i[:16])

    # 4) endianness invariance: same VALUES, opposite byte order -> same hash.
    vals = rng.integers(0, 65536, size=(50, 40), dtype=np.uint16)
    h_le = pixel_sha256(vals.astype("<u2"))
    h_be = pixel_sha256(vals.astype(">u2"))
    assert h_le == h_be, "endianness changed the hash"
    print("endianness invariant:  ", h_le[:16], "==", h_be[:16])

    print("pixel-hash self-check PASS")
