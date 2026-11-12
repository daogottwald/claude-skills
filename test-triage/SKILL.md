---
name: test-triage
description: For a change, commit, or commit range, find the tests that exercise the touched code, run them, and return a compact pass/fail verdict with failure summaries and the likely fix location — keeping the large test output in the subagent's context. Use when delegating "does this change break tests / which test and why", after a commit rewrite to catch stranded tests, or to check a diff before committing.
---

# Test triage

Find the tests a change touches, run them, and report a **compact** result. Built to be
delegated: the verbose test logs stay in this context, only the verdict returns.

## Return compact — the whole point

Report only: per test file, pass/fail; for each failure, the assertion + `file:line` and
a one-line cause; whether it's a new regression vs baseline; and the likely fix location
(which source file / commit). Do **not** paste full test logs, environment dumps, or raw
command output into your final message — that defeats the reason you were delegated to.

## 1. Find the affected tests

- **By naming convention**: a module `Foo.mjs` / `Foo.sys.mjs` is usually covered by
  `test_Foo*.js` under a nearby `tests/` directory.
- **By symbol**: grep the touched symbols in the component's test directories (restrict
  the path — the repo is huge) to catch tests that exercise them indirectly.
- **By manifest**: the test manifest (`xpcshell.toml` / `browser.toml` / `mochitest.ini`)
  lists the tests for a directory.
- For a commit or range, take the union of tests across every touched source file.

## 2. Run them — output to a file, never into your context

Redirect the (large, slow) output to `artifacts/` and read only what you need; never pipe
`./mach test` through `tail`/`grep`/`head` (you'd have to re-run to see more):

```
mkdir -p artifacts
./mach test <files> --headless > artifacts/triage.log 2>&1; echo "exit=$?"
rg -n 'TEST-UNEXPECTED|Test FAIL|Passed:|Failed:|Assert' artifacts/triage.log
```

Front-end-only changes need no build; otherwise build first per the repo's conventions.

For a Firefox mochitest, follow the **run-mochitest** skill for the run itself — it covers
choosing a display that isolates the run from the user's desktop (and the Wayland leak that
makes `xvfb-run` only look isolated), keeping the objdir in sync with the code under test,
port collisions with a concurrent run, capping a wedge, and the repeat/chaos flags for
judging whether a failure is intermittent. Its §6 also owns two facts that decide whether a
directory run means anything: **a test timeout aborts the rest of its manifest**, so the run
covers a fraction of the directory while still reporting a normal `SUITE_END`, and **a
failure inside a long segment is not comparable to an isolated baseline**, because a
preceding file's leaked window makes later files fail on their own assertions. Count what
actually ran before reading a directory run as coverage.

## 3. Judge differentially, not absolutely

Many suites carry known env/flaky failures. What this section owns is the cross-suite
comparison; `run-mochitest` §6 owns judging a single run's own segments. Establish the
baseline failure set (run at the pre-change state, or the current tip) and report only
**new** failures beyond it as regressions — at the same iteration count as the suspect run,
or you're comparing nothing.
A test red across a rewrite may be stranded in a commit you didn't touch — re-run the
affected files across the whole range, not just the tip.

For a **transport or pref flip**, the baseline is the same tree with the flip off, and
pref-on-fails/pref-off-passes is the whole scope rule. Whether a failure predates the
change says where to look for the cause, not whether it is yours: a test that fails on
both settings belongs to someone else, and a per-test `skip-if` on the variant is for a
test that genuinely cannot run under it, never for one that merely failed before.

## 4. Report

A scannable list: which files passed/failed, each failure's assertion + `file:line` +
one-line cause, new-regression-vs-baseline, and the most likely fix location. Nothing more.
