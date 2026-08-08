# Sweep checks

A fixed, pre-written list of the checks this repo should run as a
standard event on every code change — never composed on the fly. A
check written in the moment takes its expected value from the change
that prompted it; that is exactly how the Keep RAW Images deletion bug
got enshrined as correct behavior (`qt_shell.py`'s `render_check()`
asserted the buggy deletion as the expected outcome — see
`CHANGELOG.md`'s 2026-08-03 "Keep RAW Images narrowed to raws only" and
2026-08-05 "tenth task Part 2" entries). This file exists so the next
check on a given topic is looked up, not reinvented.

Each entry below records: what it checks, what external contract
supplies its expected value (or "observed behavior" / "unverifiable
citation" if it doesn't have one), where it can run, and whether it is
implemented today or is a gap. **Marking something a gap is the point,
not a shortfall** — a list claiming coverage it doesn't have is worse
than a short one. Nothing in this file was implemented as part of
writing it; gaps stay gaps until someone deliberately closes them as
their own piece of work, with its own intent/build/record-build
sequence.

Baseline and full reasoning: `CHANGELOG.md`'s 2026-08-05 "Record
intent: tenth task Part 3" entry. Full per-file findings this list was
seeded from: the same date's "tenth task Part 1"/"Part 2" entries.

Run convention for implemented checks: `python3 <module>.py
--render-check` (most modules) or bare `python3 <module>.py`
(`annotations.py`, `camera_backend.py`, `imx477.py`, `pixel_hash.py`).
All need `numpy`; none need real hardware except where noted.

---

## 1. Measurement correctness

| Check | Expected-value source | Where it runs | Status |
|---|---|---|---|
| Calibration survives a capture-resolution change | — | Sandbox or Pi (pure math, no I/O) | **Gap.** `ca_lib.py`'s `adapt_center` (the function this question is actually about — scales a measured optical centre from `calib_shape` to `image_shape`) has no `if __name__` block, no self-check of any kind. |
| Measurement reads the green plane, not the debayered image | External contract — BGGR Bayer layout; cross-checked against `debayer.py`'s own `extract_green` | Sandbox or Pi (numpy only) | Implemented — `calibrate.py render_check()` (`load_green_plane`), `measure.py render_check()` (`load_measurement_plane` + pixel-hash consistency). Caveat (Part 2): the "expected" value in both is computed by calling `debayer.extract_green` with the same constants the code under test uses — proves the wrapper doesn't alter debayer.py's result, would not catch both sides agreeing on a wrong constant. |
| Preview and still agree on field of view | Unverifiable citation — both cite `PRIORITY_click_mapping_fix.md`, which does not exist anywhere in the repo or its git history | Sandbox or Pi (`FakeCamera`, no hardware) | Implemented — `camera_backend.py`'s self-check (sensor crop geometry block) and `imx477.py`'s self-check (FOV-ratio cross-check, `4056x3040` vs `1332x990` ≈1.52x). Both currently pass; the contract they claim just can't be independently verified from inside the repo today. |
| An independent re-derivation (any scratch script that recomputes a pipeline stage and diffs it against the file on disk) reproduces the *code's actual floating-point operation order*, not just its algebra | Observed behavior — this session's own `measure_bracket_full_q1q6.py` Q2 residual, `CHANGELOG.md`'s 2026-08-06 "full Q1-Q6 chain" entry | Sandbox or Pi (numpy only) | **Gap.** No standing check enforces this; caught by hand, not by tooling. A first re-derivation used direct division (`mean_sci - mean_dark`, then `rint`/clip) and produced a plausible, structured, nonzero residual (~19-20 px/bracket, always off by exactly 1 ADU at an exact `x.5` tie) that read like a real property of the data. It was an artifact of using `mean / dmax` where `frame_average.py`'s `average_burst()` actually uses `mean * (1.0/dmax)` — a precomputed-reciprocal multiply, not a direct division; the two round differently at exact IEEE754 ties. `algebra == code` is not a safe assumption for any bit-exact re-derivation claim — reproduce the operation order, or state explicitly that only the algebra, not the arithmetic, was checked. |
| White level derives from the sensor profile's own bit depth, never from container width | External contract — `PHILOSOPHY.md`'s sensor-profile rule, same clause the geometry checks below cite, extended to a different constant | Sandbox or Pi (`FakeCamera`/no hardware for the substitution half; the driver-config read that confirmed `BIT_DEPTH=12` needed the real camera once, off the standing check) | Implemented — `hdr_merge.py`'s own first `render_check()` (positive-form substitution: a synthetic `camera_backend.BIT_DEPTH`, reassigned before the call, must change `merge()`'s own `white_level=None` fallback, reached through `merge()`'s real call path, not a direct attribute read — and the real profile's own derived value must still equal today's `65520` exactly). Covers: the one fallback this session touched. Does NOT cover: whether `BIT_DEPTH=12` is itself correct in any absolute sense (driver-confirmed via the still-capture config's own requested format, `SRGGB12_CSI2P`, not independently proven against the PiSP `COMP1` transport's own undocumented internals — see `CHANGELOG.md`'s own intent entry for the full evidence chain and what's observed vs inferred); a wrong `BIT_DEPTH` would derive a wrong-but-internally-consistent white level and this check would still pass. `camera_backend.py`'s `assert_no_hardcoded_bit_depth_or_white_level_above_driver_layer` is the companion scan (same shape as the geometry rows below, single-`NUMBER`-token form, two documented false-positive exceptions — `annotations.py`'s hash-slice index and `ca_measure.py`'s CA radial-bin count, both coincidental collisions with `12`). |
| The per-frame saturation mask marks exactly the raw samples that hit the sensor profile's own white level, and follows the profile if it changes | Observed behavior (a synthetic frame with known clipped positions) plus the same substitution contract as the white-level row above | Sandbox or Pi (`camera_backend` import only, no camera construction — nothing in this chain constructs a camera at all) | Implemented — `frame_average.py`'s own first `render_check()`. Position-exact on a synthetic frame with known clipped photosites (not a count — a mask that is always full or always empty passes a count-based check), plus an all-clipped and an all-clean frame exercised separately (the two cases a position-exact check on a single mixed frame still would not catch), plus the same profile-substitution requirement as the white-level row (`camera_backend.BIT_DEPTH` reassigned before the call, marked set must change, reached through `write_clipped_mask`'s own real call path). Covers: the packed-bit read/write round trip and the threshold's own derivation. Does NOT cover: whether the mask is byte-correct against a real raw frame's own true clip pattern beyond the one real-bracket comparison recorded in `CHANGELOG.md` (an exact match to an independent, out-of-tree investigation's own figure — corroboration, not proof, and the two could still both be wrong the same way, since both ultimately read the same raw bytes); the persisted `.satmask.npz` files themselves are still never read back by anything in production — nothing does, that remains write-side only. The clip *detection* the mask writes is now also consumed, live, by the row below — a different consumer of the same underlying test, not a reader of the persisted files. |
| `average_burst` excludes a clipped raw sample from its own per-photosite average, dividing by the count of samples actually included there, and records how many were excluded | Observed behavior (synthetic frames with known clipped positions at known burst-relative indices, not just known spatial positions) | Sandbox or Pi (`camera_backend` import only, no camera construction) | Implemented — `frame_average.py`'s own `render_check()` (same function, same synthetic-frame block as the mask row above). Position-exact across four photosites with three distinct clean-counts (0, 1, and 2 excluded out of 4 frames — not just "some vs none," so a bug that applies one divisor to the whole frame cannot pass by coincidence): a clean photosite is bit-identical to today's unconditional average (same reciprocal-multiply operation order the code itself uses, not a fresh division — see this file's own §1 "operation order" row for why that distinction matters here); a partially-clipped photosite's mean is independently cross-checked against the raw sum of only its own clean samples; an all-clipped photosite is confirmed to hold the `white_level` sentinel, confirmed distinct from both `0` and the container ceiling `65535`, and its excluded-count is confirmed to equal the frame count; `info["exclusion"]`'s aggregate fields (`partially_clipped_photosites`, `fully_clipped_photosites`, `total_excluded_samples`) are cross-checked against the same ground truth independently, not just returned and trusted. Built before the fix and watched fail first (`ValueError: not enough values to unpack` against the pre-change 2-tuple return — `CHANGELOG.md`'s own build entry has the pasted output). Covers: the accumulator/divisor/sentinel arithmetic and the excluded-count record's correctness against a known-clipped synthetic burst. Does NOT cover: whether the all-clipped sentinel is actually distinguishable from a genuine photosite reading exactly `white_level` without also reading the excluded-count record — by design, it is not, and the check does not pretend otherwise; whether `hdr_merge.py` (unmodified this session) or any other consumer correctly interprets the sentinel or the excluded-count sibling file — nothing consumes either yet; whether the printed stage-summary line or the `_excluded_count.tif` sibling file (both `main()`-level, CLI plumbing around the tested function) are wired correctly — self-check only exercises `average_burst` directly, not the CLI; see `CHANGELOG.md`'s own build-record entry for the separate, real-bracket, real-CLI verification that covers that half. |

## 2. Provenance integrity

| Check | Expected-value source | Where it runs | Status |
|---|---|---|---|
| Exactly one description tag per written TIFF | External contract — TIFF tag-270 semantics (two entries in one IFD is reader-dependent/invalid) | Pi only (real bracket TIFFs; needs `numpy`+`tifffile`, importable in sandbox but no fixture data) | Implemented, on the real production path (`hdr_merge.py`'s `_assert_single_description_tag`, called from `main()` after every real `tifffile.imwrite`) — but **zero automated-check coverage**: no `render_check`, no test file, not part of the documented 15-module sweep. Only exercised by a real CLI run or a real `hdr_from_session.py` subprocess call. |
| Recorded values match the artifact they describe | External contract — documented Part 03 capture/provenance-dir split | Sandbox or Pi (no numpy needed — `provenance.py` imports none; pure stdlib) | Implemented, partially — `provenance.py render_check()` confirms sidecars/`session.json` land in the *correct directory* and that fields like `capture_dir` are *recorded*. |
| Recorded output paths resolve to the file they're embedded in | — | Sandbox or Pi | **Gap.** No check re-opens a file at a path recorded in `session.json` (or a manifest) and confirms it is the file that entry describes. Named explicitly in Part 2; nothing in the repo does this today. |
| Publish/export manifests agree with each other (results.json vs. manifest.json counts, hash-sliced package contents) | External contract — "build checklist §11/§12" | Sandbox or Pi (numpy only) | Implemented — `export.py render_check()`, `publish.py render_check()`. |

## 3. Geometry derivation

| Check | Expected-value source | Where it runs | Status |
|---|---|---|---|
| No hardcoded sensor dimension above the driver layer | External contract — `PHILOSOPHY.md`'s sensor-profile rule | Sandbox or Pi (source-inspection only, no hardware) | **Corrected (Stage 3 sequence 1):** this row previously cited `assert_only_camera_backend_imports_sensor_profiles` as evidence and was marked Implemented on that basis. That function verifies no module *imports* a sensor-profile module — real, and does exactly what it claims — but an import check does not test for a hardcoded dimension; `GREEN_PLANE_RES`/`FULL_RES` literals were present above the driver layer while it passed clean. Import boundary: still Implemented, same function, unchanged claim. Hardcoded dimensions: now genuinely Implemented — `camera_backend.py`'s `assert_no_hardcoded_sensor_dimension_above_driver_layer` (tokenizes every non-driver `.py` file's own production region — see `_production_region_source` — for a literal matching a dimension `_sensor_profile_dimension_pairs` derives live from the loaded profile, either axis order, plus integer halves). |
| Shape predicates derive from the sensor profile, not a maintained list | External contract — same rule, "discovered by shape, not a maintained list" | Sandbox or Pi | Implemented — same function as above; also `imx477.py`'s `_resolve_sensor_profile` exact-name-match check (unrecognized/wrongly-cased/unsafe model strings fail loudly). |
| Crop table is internally consistent (within `FULL_ARRAY_SIZE`, aspect-preserving) | External contract — physical sensor geometry (`FULL_ARRAY_SIZE`) | Sandbox or Pi (no hardware) | Implemented — `imx477.py`'s self-check, "crop table internal-consistency". |
| No other module imports `picamera2`/`libcamera` directly | External contract — `PHILOSOPHY.md`'s camera-import boundary | Sandbox or Pi | Implemented — `camera_backend.py`'s `assert_only_camera_backend_imports_picamera2`. |

## 4. Retention safety

| Check | Expected-value source | Where it runs | Status |
|---|---|---|---|
| Keep RAW Images off deletes only this capture's own raw frames, never the merged/averaged master or the final result | External contract — the setting's own name/label ("Keep RAW Images", not "Keep Intermediates") | Pi (needs a real `_auto_process` run through `FakeCamera` + a real `frame_average.py`/`hdr_from_session.py` subprocess; sandbox-runnable if numpy+PyQt6 present) | Implemented, and the **fixed instance** of the one known contract-vs-observed bug in this repo (`qt_shell.py render_check()`'s Keep RAW Images block; see `CHANGELOG.md` 2026-08-03/2026-08-05). |
| `plane_cache.clean_cache` never removes a referenced plane, regardless of age | In-file documented contract — `clean_cache`'s own docstring | Sandbox or Pi (numpy+tifffile only) | Implemented — three checks (clean-now, auto-clean by age, same-age reference-vs-no-reference eligibility flip). Untested edge: all three pass an explicit `root=`; none exercises the `root=None` default-scoping path against a tree holding non-cache files (scoping is structural via `_resolve_root`, not demonstrated end-to-end). |
| `stacks.move_frames_to_discarded` moves only the intended capture's frames, never a differently-tagged capture sharing a prefix | — | Sandbox or Pi (pure stdlib, no numpy) | **Gap.** Function is defined (`stacks.py`); `render_check()` never calls it. No assertion either way on prefix-collision safety. |
| No writer's default output filename appears in any deletion list | — | Sandbox or Pi | **Gap.** Nothing in the repo checks this as a standing, generalized invariant. The Keep RAW Images fix corrected one specific instance by hand (`master_files` removed from the delete set); no check exists that would catch a *future* writer's default output name being added to a deletion list by mistake. |
| `archive_raws()`'s glob doesn't sweep up processed outputs sharing the raw extension | — | Pi (real files, real extensions) | **Gap, explicitly out of scope for this list per direct instruction** — not investigated further here. Known risk already on record: off-rig (`--raw-ext tif`) `archive_raws()`'s `*.<raw-ext>` glob would also match every processed `.tif` output in the directory (`CHANGELOG.md`, 2026-08-03 Keep RAW Images investigation, item 1). Currently unreachable via the GUI (`qt_shell.py` always passes `--keep-raws`), only via direct CLI with `--archive-raws`. |
| A staging directory left behind by a crashed or interrupted publish is never auto-deleted | In-file documented contract — the gallery-race staging design's own crash-cleanup decision (`CHANGELOG.md`'s 2026-08-05 entries; `provenance.new_staging_dir` never removes an existing directory, it only ever creates one) | Sandbox or Pi (source-inspection: confirm no startup or capture-path code unlinks anything under `STAGING_ROOT`) | **Gap.** The policy — leave in place, log, no auto-delete at startup, since that would be unreviewed deletion of capture data — is followed in the code that exists today (nothing anywhere deletes a staging directory), but there is no `render_check` assertion enforcing it and no logging/reporting mechanism for an orphan when one appears. Two real orphaned staging directories exist on this Pi right now from a verification-script crash (`~/staging/2026-08-05_145415`/`_145438`) — left in place per the policy, not evidence the policy is enforced by code. |

## 5. Sensor sanity — contract traceability, meta-level

Every entry above carries its own contract-vs-observed classification
forward from `CHANGELOG.md`'s 2026-08-05 "tenth task Part 2" catalog,
not re-derived here. Three checks in this repo cite a planning
document as their external contract, and none of those three documents
exist anywhere in the repo or its git history — flagged here so they
are never silently treated as verified just because they're on this
list:

- `plane_cache.py`'s cache-plus-annotations integration check cites
  `PLAN_04_green_plane_cache.md`.
- `qt_shell.py`'s `assert_live_measuring_has_no_calibration_dependency`
  (a real, structural, source-inspection check — it does run, and does
  catch what it claims to) cites `PLAN_quick_ruler.md`.
- `camera_backend.py`'s sensor-crop-geometry check and `imx477.py`'s
  FOV-ratio check (both listed under §1/§3 above) cite
  `PRIORITY_click_mapping_fix.md`.

None of these three are known to be wrong — Part 2 found no evidence
their expected values are incorrect, only that the spec they claim to
match can't be independently checked from inside this repo. Treat as
"unverifiable citation," a distinct status from both "external
contract" and "observed behavior," until the cited document either
turns up or gets committed.

**A second, live example of this file's own reason for existing:**
`function_index.py --render-check` was failing on `main`
(`FUNCTION_INDEX.md` stale against real PR #10/#11/#12 additions —
`CHANGELOG.md`'s 2026-08-05 "tenth task Part 2" entry has the full
diff) until it was regenerated and re-verified this session
(`CHANGELOG.md`'s 2026-08-05 "tenth task Part 4" entry). Its presence
here is the argument for this file: a real, standing check that nobody
re-ran after a merge is precisely the failure mode a fixed, standard
sweep — run every time, not composed when someone remembers to — is
meant to catch before it goes stale silently again.

| Check | Expected-value source | Where it runs | Status |
|---|---|---|---|
| `FUNCTION_INDEX.md` matches the real per-module function set | Observed behavior — `function_index.py`'s own regeneration is authoritative; no external contract | Sandbox or Pi (`python3 function_index.py --render-check`, no hardware) | Implemented (`assert_function_index_current`), but its trigger is unenforced — nothing runs `python3 function_index.py` automatically when a function is added, removed, or has its signature changed. **Trigger:** any PR that adds, removes, or changes the signature of a module-level function regenerates and commits `FUNCTION_INDEX.md` as part of that same PR, not a later cleanup pass. This is exactly how it went stale after PR #10/#11/#12 — three PRs each added functions, none regenerated the index, and the drift wasn't caught until this list's own Part 2 sweep ran the check directly. Enforcing the trigger itself (a pre-commit hook or CI step, as opposed to remembering to run it) is not built — still a gap.
