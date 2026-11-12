---
name: run-mochitest
description: Run a Firefox mochitest (browser-chrome, plain, chrome, a11y) locally and get a trustworthy pass/fail, avoiding the display, objdir and harness gotchas that turn environment problems into plausible-looking product failures. Use whenever you are about to invoke `./mach mochitest` or `./mach test` on a Firefox checkout, including a single file, a directory, a repeat/verify run for flakiness, or a pre-commit check.
---

# Run a mochitest

A local mochitest scores the environment as readily as the patch: skipping a step below
produces a *plausible* failure you can build a wrong story on.

## 1. Make sure the objdir contains the code under test

Chrome JS/CSS in the objdir are **symlinks into the source tree**, so edits to an existing
front-end file are live. What is not:

- A **new** test file, or a `browser.toml` / `mochitest.ini` change — `./mach build faster`.
- A **preprocessed include** (`*.inc.xhtml`, and anything else compiled into a chrome file
  rather than symlinked, e.g. `navigator-toolbox.inc.xhtml` into `browser.xhtml`) — the
  objdir keeps the *built* copy, so an edit is invisible until `./mach build faster`. The
  trap is that the test fails on the *old* markup, which reads as the change not working.
- C/C++/Rust changes — a real build (`./mach build binaries`).
- Anything after moving the tree (a branch cut from fresh autoland, a rebase, a pull): the
  built markup and localization files can fall out of step with the source, and most
  window-opening tests then fail with a stackless `uncaught rejection: undefined` that reads
  as a regression. `./mach build faster` first.
- Anything after `./mach artifact install` (or `./mach bootstrap`): it unpacks the downloaded
  build over the objdir, **reverting locally-built front-end JS** to upstream, so always
  `./mach build faster` after. The trap is that this reads as a **pass** — green because your
  change isn't there — so treat a surprising result in *either* direction soon after an
  install as a stale objdir until you've rebuilt.

Reach for `references/objdir-staleness.md` once a run has come back wrong: it picks the refresh
by signature (`Missing chrome or resource URLs: …`, a suite reporting `0 checks`, an exit 0 with
no counts at all) and holds the CLOBBER **mtime** check — the one to use, since diffing the two
files reports a difference on every healthy tree.

A fresh worktree has no objdir: copy a sibling checkout's mozconfig and `./mach build`
(artifact build ~10-15 min). Never edit a source file mid-run; the symlinks are live.

## 2. Pick the display — and never the user's live desktop

Name what the failing config needs — real widget backend? window manager? input seat? — then
take the cheapest rung that supplies it:

| Rung | Supplies | Lacks |
| --- | --- | --- |
| `--headless` | every platform, no display; size modes and their events | the drag service on Linux, APZ, the platform widget backend, desktop-supplied values on Linux |
| an isolated real display | the real backend with nobody contending | on Linux, a WM or an input seat on some rungs; on macOS and Windows, the rung itself |
| the user's live session | backend, WM and seat together | isolation: ask for it first, and a run they touched is unscored |
| a try run | the real backend under isolation on any platform | speed, and anything you'd want a debugger for |

Try `--headless` first and treat a headless-only failure as suspect until checked elsewhere.
For any rung past it, read `references/display-rungs.md` rather than assembling a command: it
says how a test run fails on each rung, and `references/linux-display.md` has the Linux commands. The in-tree `desktop-theme-probing` skill describes the rungs themselves. Confirm the rung was isolated before reading a number off it: a
virtual display server sets only part of the environment the browser consults, so a run can
land on the **user's desktop** while the log reads like a clean virtual display.

A mochitest count only answers *whether* a thing passes; for *why*, probe the browser directly
so the answer doesn't depend on who has focus: the in-tree `desktop-theme-probing` skill's `firefox-probe.py` in either context, and `references/chrome-probe.md` for the recipes that skill lacks and for reading the accessibility tree.

**A probe can erase the bug.** For a style or visibility failure, reading computed style
before the assertion forces a flush that recomputes hover state and hides the cause. Dump
diagnostics only *after* the check fails, using the same call the assertion makes.

When the *test itself* has to drive a content document through `SpecialPowers.spawn`, read
`references/content-task-traps.md` first: `BrowserTestUtils` cannot be imported there at all,
a sandbox `Assert` outlives its task and blames the next test, and a missing
`SimpleTest.promiseFocus` turns into a plausible product failure.

## 3. Don't collide with another run on this host

The mochitest httpd binds a **fixed `127.0.0.1:8888`** host-wide — another worktree is no
protection. A run started while one is live falls back to a random port, and the *second* run
fails in ways that look nothing like infrastructure:

- a test asserting a hardcoded `http://127.0.0.1:8888/…` URL fails on the port mismatch;
- a mock-server test takes its failure branch and asserts a **plausible wrong value** (the
  mock is simply unreachable) — the one that reads as a regression;
- per-file times blow up and tests hit their timeout, from CPU contention rather than a hang.

Check before running, and before trusting a suspicious result:

```
~/.claude/skills/run-mochitest/scripts/port-check.sh
```

It names who holds the port, truncated: `mach` runs the harness under a `multiprocessing`
forkserver whose children carry their entire `sys_path` on the command line, so one bare
`pgrep -af` dumps 24 KB and costs more context than the run it guards. If another run holds
the port, **don't kill it** — wait it out with `port-check.sh --wait`.

## 4. Invoke it

**Run it in the foreground and block until it exits — above all when you were *delegated* the
run.** No `&`, no background task, no early return: issue the command, wait for the exit code,
digest the log, *then* report. Reporting "the run is underway" carries no result, leaves the job
running orphaned, and makes the delegator wait on it a second time. A slow run is not a reason
to detach.

If the shell must come back (a main-thread run you want to poll), block on a sentinel rather
than guessing at progress — for the *waiter*, not a licence to report early:

```
(./mach mochitest ... > artifacts/run.log 2>&1; echo "exit=$?" > artifacts/run-done) &
until [ -f artifacts/run-done ]; do sleep 20; done
```

Redirect to `artifacts/`; never pipe a slow run through `tail`/`grep`/`head`, or you'll re-run
it for a different slice. Then digest the log rather than grepping it repeatedly:

```
python3 ~/.claude/skills/run-mochitest/scripts/mochitest-summary.py artifacts/mochitest.log
```

It prints a verdict, the counts, failures grouped per file, and the last file to start — every
line citing a log line number, so anything surprising is one `Read` away. It checks harness
signatures *first* and returns `infra-error` when it finds one (`Xvfb failed to start`, app
timed out with no output, a crash, a stale objdir, a web server that wouldn't bind, `Ran 0
checks`), because those otherwise present as ordinary failures — a run that never started reads
as 100% failed. It accepts several logs at once, which is how to compare a run against its
baseline. Counts alone never settle a run: `Passed: 0 Failed: 0` with a nonzero exit is an
environment problem, not a clean sheet.

Two flags routinely make a green run meaningless.
**`--setpref x=y` loses to a manifest `prefs = [...]`**, so the run passes while testing the
*opposite* of what you asked; and a manifest **`fail-if`** is a *file-level* expectation, so any
failure anywhere in the file satisfies it and the rest of the file silently stops being a test
(use `todo_is` on the one assertion instead). `references/harness-flags.md` has both in full,
along with `--run-failures`, `--repeat`/`--verify`/`MOZ_CHAOSMODE`, and `--log-raw` for reading
todo text — consult it before reaching for any of them, and before running the `browser-static` tests.

## 5. Cap a run that can wedge

`--timeout N` does **not** shorten a genuinely wedged run: a hang still burns the harness's
~300s, longer when the app itself is wedged ("application timed out after 370.0 seconds with no
output"). Only an outer shell `timeout` caps it, calibrated to roughly twice the *passing*
duration — a browser-chrome file is often ~10-15s green, so ~30s is ample, and a generous cap
costs minutes per attempt:

```
timeout 30 ./mach mochitest --headless <path> > artifacts/mochitest.log 2>&1
```

To locate a wedge, read the log's last `add_task` / `Entering` line and work forward: a test
with an unbounded wait (`BrowserTestUtils.waitForEvent` without a timeout,
`promiseDocumentFlushed`) times out with no assertion logged, so the hang is among the awaits
since the last logged one.

## 6. Judge differentially

**A test timeout aborts the rest of its manifest, so a directory run silently covers a
fraction of it** — with a normal `SUITE_END` and nothing saying "170 files never started". Count
what ran (`grep -oP 'TEST_START: \K.*' log | sed 's|.*/||' | sort -u | wc -l`) against the
manifest before reading any directory run as coverage; `references/harness-flags.md` has the
`--start-at` resume for working past the offender.

**A failure inside a long segment is not comparable to an isolated baseline.** Browser
lifetime and preceding tests differ, and one file's leaked popup or window makes later files
fail on their own assertions — so "failed in the suite, passes alone at baseline" reads as a
regression when it is cascade damage. Compare like with like: re-run the file alone *with*
the patch too. If both isolated runs agree, the segment failure belongs to whatever ran
before it.

Many suites carry known environment or intermittent failures, and some checkouts carry
long-standing local ones. Before blaming a patch, get the baseline: `git stash` (or run at the
pre-change commit) and run the same command. For a front-end file, the objdir symlink gives a
baseline without moving HEAD or touching the stash, which other worktrees share: repoint
`<objdir>/dist/bin/chrome/.../<file>` at a temp copy of `git show HEAD~1:<path>`, run, then
restore the link and check it with `readlink` before reporting. The chrome URL stays the same,
so this works even when the parent version had a different filename. Report only failures *beyond* baseline as
regressions, distrust unanimous results (everything failed, or zero output, usually means the
run never happened), and don't conclude from 2-vs-2 samples.
