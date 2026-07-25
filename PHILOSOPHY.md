# Philosophy

Durable rules about *how* this project is built and verified — not what it
does (`README.md`), what's currently true (`HANDOFF.md`), or what happened
(`CHANGELOG.md`). Read this when you're deciding how to verify something,
not just what to build. Short on purpose: each entry earns its place by
having actually cost something once.

## A self-check must reach the code the way the application reaches it

Three real bugs shipped past a passing self-check, in three different
parts of this project, each because the check proved a mechanism works in
isolation while the actual integration point — the wiring, the calling
context, the check's own blind spot — stayed untested:

- **Live measure panel (Part 05)**: `_live_measure_preview_event` was
  fully written and correct on its own, but never wired into
  `eventFilter` — every real click on the live feed would have kept
  falling through to ordinary box-drag, and the freeze this whole feature
  depends on would never have triggered. Invisible because no
  render_check coverage existed yet to call the handler through
  `eventFilter` — the gap in coverage is what let the wiring gap survive.
- **`get_capabilities()` (Part 02)**: verified standalone, against a
  `Picamera2()` that was never mid-preview. `sensor_modes` secretly calls
  `configure()`, which raises if the camera is already running — the only
  state the real UI ever calls it in. The standalone check proved the
  value-translation logic; it never proved the calling context, which is
  where the crash actually lived.
- **Live Measuring's own module-boundary check**: defined, but never
  called from anywhere — an assertion nobody runs, which only *looks*
  like a guard. Once wired in and actually run, it failed on itself: a
  docstring naming a forbidden identifier to explain why it was
  deliberately *not* reused tripped the same naive text scan meant to
  catch a real violation, because the scan couldn't tell a docstring
  mention from a real code reference.

The common shape: a check that is real, and passes, and proves nothing
about the one thing that actually broke. When you add a self-check for a
handler, a cache, or a structural guard:

- **Drive it through the same entry point the application uses** — the
  real `eventFilter`, the real calling state (mid-preview, not
  freshly-constructed), the real invocation site — not a shortcut that
  happens to be easier to call from a script.
- **Before trusting an existing self-check that "looks thorough," grep
  for where it's actually called.** A guard that is only ever *defined*
  is not a guard yet.
- **A structural scan over source text is not the same as reasoning
  about it.** If a check greps/scans raw source for a forbidden name, it
  will flag a comment or docstring that merely *mentions* that name —
  strip comments and string literals before scanning (see
  `qt_shell.py`'s `_source_without_docs_and_comments`), or the check will
  eventually fail on its own honest documentation.
