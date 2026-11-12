# Harness flags and known-broken assertions

The flags past a bare invocation, each with the way it silently makes a run mean something
other than what you asked, plus how to mark an assertion you know is broken.
`run-mochitest`'s §4 holds the invocation itself and the log digester.

Flags worth knowing:

- **`--setpref x=y` loses to a manifest `prefs = [...]`**, which is applied after the flag: the
  run goes green while testing the *opposite* of what you asked, and quoting doesn't help. Edit
  the manifest for the run, or confirm from the log that the pref-dependent branch ran.
- **`--run-failures true`** runs a test marked `skip-if = ["true"]`, and runs it *under that
  manifest only*, so that manifest's prefs genuinely apply — the way to exercise a file listed
  in two manifests where the interesting one skips it. Such tests are usually `expected: fail`,
  so read the counts, not the exit code. Corollary: for a file in several manifests, "the suite
  is green" can mean only one configuration was ever exercised. **And a test that writes a
  file overwrites its own output once per manifest** — a screenshot or dump keyed only on the
  run label silently compares the *last* configuration to run, with nothing in the log saying
  the earlier ones happened. Put the distinguishing pref in the filename. The urlbar
  directories are where this bites: `browser.toml` plus `browser-nova.toml` and
  `browser-proton.toml`, so a file added to the base manifest ran twice (once per variant
  manifest) — count `TEST_START` lines for the file rather than assuming one run.
- `--repeat N` (same browser), `--verify` (four TV modes), `MOZ_CHAOSMODE=0xfb` for flakiness —
  run the same iteration count against the pre-change baseline. **`--repeat` cannot resample
  anything captured once per browser launch**: a test reading startup data (e.g.
  `startupRecorder.data.frames`) replays identical bytes on every repeat, giving a perfectly
  stable count that measures nothing. Sample that with separate `mach mochitest` invocations.
- **`--log-raw` to read todo text — and get the filter right.** The default formatter swallows
  todo messages, so a `Todo: N` count with no way to see *which* assertion is one flag away.
  In mozlog, `expected` is present only when the result was **unexpected**, so a todo is a bare
  `status: "FAIL"` with no `expected` key, and its text is in `message` (`subtest` is null).
  Filtering on `expected == "PASS"` therefore matches nothing and prints a confident zero for
  every run — a unanimous result, which `run-mochitest`'s §6 says to distrust rather than believe.

- **`--start-at <dir>/<file>` to resume a directory run past a timeout.** A test timeout aborts
  the rest of its manifest: the harness kills the browser, abandons the remaining files in that
  `browser.toml`, and moves to the next manifest — then reports a normal `SUITE_END`, so nothing
  in the summary says "170 files never started". Resume at the file *after* the last one that
  started, and repeat until the tail of the manifest is reached; a handful of focus-dependent
  tests can each cost a segment. `run-mochitest`'s §6 holds the count that tells you it happened.

**Marking a known-broken assertion: reach for `todo_is`, not a manifest `fail-if`.** `fail-if`
sets a *file-level* expectation, so **any** failure anywhere in the file satisfies it and the
run still reports green — the rest of the file silently stops being a test. Measured on one
file: sabotaging a genuinely-passing assertion gave exit 0 and `Failed 0` under
`fail-if = ["true"]`, and exit 1 with `Failed 1` once the same file used `todo_is` for its one
known-broken assertion instead. The log is also honest — `EXPECTED-FAIL` and a `Todo 1` count,
rather than `fail-if`'s inverted `PASS <name> - "x" == ""` on a comparison that plainly failed.
`todo`, `todo_is` and `todo_isnot` are browser-chrome globals, and **`SpecialPowers.spawn`'s
sandbox exposes `todo`/`todo_is` too**, so an assertion inside a content task can be marked in
place without routing the value back to the parent. Either way the fix signal is the same: when
the bug lands, the todo starts passing and the harness reports an unexpected pass. Name the bug
in a `TODO(bug NNNNNN)` comment beside it. Reserve `fail-if` for a file that genuinely cannot
run meaningfully at all.

## The static tests

The static tests live in `browser/base/content/test/browser-static/`, not `static/`.
`browser_all_files_referenced.js` refuses an unpackaged objdir build with "This test
requires a packaged build, run 'mach package' and then use --appname=dist": run
`./mach package`, then pass `--appname=dist`, and it launches `<objdir>/dist/firefox/`.
Under `--headless`, `browser_parsable_css.js` always fails on missing
`moz-icon://stock/dialog-*` icons referenced from `global.css` and `sanitizeDialog_v2.css`,
because headless has no GTK stock icons; baseline it rather than read it as a regression.
