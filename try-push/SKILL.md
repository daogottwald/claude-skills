---
name: try-push
description: Push a Firefox (mozilla-central) patch to try and read the result truthfully — select the right tasks (mach try auto by default, a fuzzy query when you need a named suite), avoid the over-selection/artifact-build/test-path/DONTBUILD traps that silently make a run meaningless, then verify with the bundled try-status.py. Use when asked to push to try, schedule CI for a patch, check on a try run, find out why a try job failed, or whether a given test passed in CI.
---

# Firefox try pushes

Two halves: selecting the right tasks, and reading the result without being lied
to. Both halves have traps that produce a confidently green or confidently red
answer that means nothing.

## Selecting tasks

**Default to `./mach try auto`.** It computes the task set from the changed files
with the same optimizer autoland uses, so it answers "will this stick when it
lands" and there is no query to get wrong — which is the failure mode that
actually bites (see the dry-run gate below). It takes `-m`,
`--artifact`/`--no-artifact`, and `--tasks-regex-exclude` if the computed set
needs bounding.

Note that the dry-run gate below **does not apply to `auto`**: its selection is
computed by the decision task, not locally, so `--no-push` prints only strategy
flags (`disable-pgo`, `use-artifact-builds`) and an empty task list. There is
nothing to count before pushing — what protects you is that there is no query to
under-exclude, and `--tasks-regex-exclude` if you need a ceiling. Read the real
size from `try-status.py` once the decision task has expanded the graph.

```
./mach try auto -m "<message>"
```

Reach for an explicit `fuzzy` query only when you need something `auto` won't
guarantee: a *named* suite you must see run (a test you know is affected, or one
that fails locally and you need CI's verdict on), `--rebuild N` to measure an
intermittent, or a single platform for a platform-specific repro. Both are worth
running for a broad refactor — `auto` for landing confidence, `fuzzy` for the
suites you care about most.

```
./mach try fuzzy --full [--no-artifact] [--rebuild N] \
  -q "<query>" [-q "<query2>"] -m "<message>" <paths>
```

- `--full` searches the whole task graph, not just the default-visible subset.
  Without it many test tasks are unselectable.
- Multiple `-q` are **unioned**, so build one query per platform family rather
  than one unreadable query.
- fzf query syntax inside a `-q`: space-separated terms are ANDed, `'foo` means
  exact-match `foo`, `!foo` excludes. **Paste the whole exclusion set every time**
  rather than assembling it from memory:
  `!asan !tsan !ccov !devedition !shippable !artifact` plus `!wayland` on
  linux2404. `asan`/`tsan`/`ccov` are expensive and slow, while `devedition` and
  `shippable` are the same tests again on build variants a patch review never needs — and
  those two are the easiest to leave out by accident, because nothing in the task name hints
  that they duplicate coverage. Example:
  `-q "'linux2404 'mochitest-browser-chrome !asan !tsan !ccov !devedition !shippable !artifact !wayland"`.
- `--rebuild N` runs each selected task N times. This is how you measure
  intermittents; N=6 is a normal choice, N=18 for a stubborn one. **`--full`
  caps it at 3** (`tools/tryselect/task_config.py`), and the only notice is a
  `warning:` line in the submission output. Before a measuring push, dry-run the
  same query without `--full`: when the tasks you need are still selected, drop
  `--full` and the dry-run JSON shows the full `rebuild` count. Check the warning
  or the dry run before pushing, not the job count afterwards.
- `-m "<message>"` is the only reliable handle on the push afterwards. Always
  pass a distinctive one; you will search on it later. If the push aborts on
  "Could not contact BMO", append `SKIP_BMO_CHECK` to the message.
- `<paths>` restrict which tests run.

### Gate every fuzzy push on a dry run - you cannot take it back

For a `fuzzy` query, `--no-push` prints the selection as JSON instead of
submitting. Count it, and look at the platforms, *before* pushing (with `auto`
there is nothing to count, see above):

```
./mach try fuzzy --full --no-push -q "<query>" -m "dry run" 2>/dev/null > /tmp/dry.json
python3 - <<'EOF'
import json, collections
raw = open('/tmp/dry.json').read()
d = json.loads(raw[raw.index('{'):])
tasks = d.get("parameters", d).get("try_task_config", d).get("tasks", [])
print("selected tasks:", len(tasks))
for k, v in collections.Counter(t.split('/')[0] for t in tasks).most_common(10):
    print(f"  {v:4}  {k}")
EOF
```

This is not optional politeness. **An agent session cannot cancel a try push**:
there is usually no `taskcluster` CLI and no Taskcluster credentials, and
`mach try` has no cancel subcommand, so the only remedy is asking the user to hit
"Cancel All" in Treeherder. A wrong selection is therefore irreversible from your
side, and it burns shared CI capacity for hours.

Calibrate against the intent: one test suite on one platform family, debug+opt, is
**tens** of tasks (a browser-chrome + devtools-chrome selection on linux2404 is
~37). Hundreds means the query let in variants you did not ask for -
`tsan`, `devedition` and `shippable` each re-run the same tests on another
platform, which is how one forgotten `!` turns 37 tasks into a four-digit job
count. Note the push's *job* count exceeds the selected-task count (builds, lint
and dependencies expand it), so compare selections to selections.

### Traps that make a push meaningless

Every one of these has produced a run that looked green and tested nothing. The rule is here;
the log signature and the measurement behind each is in `references/selection-traps.md`.

- **An artifact build compiles nothing.** If the mozconfig has `--enable-artifact-builds`,
  `mach try` says so and the push downloads a prebuilt binary — and `auto` inherits the
  setting too. Pass `--no-artifact` for any C/C++/Rust change, and for any branch touching a
  `.idl`, where the rest of the patch can be pure JS and a constant it adds silently reads
  `undefined`. Exclude `-artifact` test platforms from the query for the same reason.
- **An `--artifact` push needs an indexed build within 50 pushheads of the base**, or the
  build task busts and its test chunks go `unscheduled`. opt resolves through the `shippable`
  index namespace and debug does not, and win32 shippable is built only about weekly.
- **An artifact push can run the base's JS on binaries a push or two older**, when the base
  sits just after a commit pairing native and JS changes: tree-wide `… is not a function`,
  UniFFI `LowerRustArgs` and `Engine store failed to initialize` on opt only, failing even a
  pure-JS patch's own test through uncaught rejections. Compare each build log's `ArtifactFind`
  revision with the base before reading any failure.
- **A test *file* path prunes every task in that suite** — the manifest match wants the path
  to be a prefix of a manifest's *directory*. Pass the directory.
- **Any try-level scoping collapses a job to one chunk.** `MOZHARNESS_TEST_PATHS` or
  `MOZHARNESS_TEST_TAG` in `try_task_config` drops every chunk but `-1`, so a variant that
  carries its own tag must be pushed with no paths and no `--tag`.
- **A subsuite runs in a task no path or tag reaches** — a directory marked
  `subsuite = "a11y"` runs in `mochitest-browser-a11y`, and needs a push of its own.
- **A default flip needs a push *carrying* the flip** to find the tests that assume the old
  default; expect them in test roots the patch never touches.
- **`msix` and `standalone` exceed the run-time cap** and are easy to leave out of a
  negation list. The msix xpcshell job runs the harness's own self-test suite rather than
  the paths you asked for, which reads as ~39 unrelated failures.
- **`DONTBUILD` (or `CLOSED TREE`) in the `-m` message strips every task from the graph.**
  The decision task itself succeeds and Treeherder shows green.

### Adding or changing a test variant

A variant is defined in `taskcluster/test_configs/variants.yml` and attached per suite in
`taskcluster/kinds/<suite>/kind.yml`. `taskcluster/docs/kinds/test.md` documents the
schema and composites; six things it does not say:

- **A variant needs a `mozinfo` flag to be skippable.** The `mozinfo:` key is what makes
  `skip-if = <flag>` work as a per-test escape hatch. Without one there is no way to
  exempt a test that genuinely cannot run under the variant.
- **Scope the run with a test tag, not a path.** `MOZHARNESS_TEST_TAG` in the variant's
  `worker.env` restricts it to manifests carrying that tag, and because it lives in the
  *task definition* the run escapes the one-chunk collapse that a `try_task_config` tag
  causes. Check the tag's real reach before relying on it — a tag added for one directory
  gets widened later.
- **`expiration` is mandatory and is a standing obligation.** A date is a patch to extend;
  `"never"` needs director approval. `urlbar-ipc` carries `2027-06-01`.
- **A tier override interacts with the composites.** `verify_dependency_tiers` rejects an
  outright `replace: tier: 1` on a variant that also composes with the ccov and asan
  platforms, since a tier-1 task cannot depend on their tier-2 builds. Drop the override
  and take the platform default instead. Tier is a Treeherder visibility level: tier 3 is
  behind a hidden-by-default filter, tier 2 is in the default and sheriff views so
  failures get filed and tracked, and tier 1 adds the obligation to back failing patches
  out — so an intermittent at tier 1 costs a backout, not just noise.
- **A task label uses the variant's Treeherder symbol, not its name.** `urlbar-ipc` shows up
  in labels as `uipc` (`M-uipc`, `M-swr-uipc`), so a query token of `urlbar-ipc` matches
  nothing.
- **The default fuzzy set follows the variant's run-on-projects.** Opt `uipc` tasks need no
  `--full`, because run-on-projects schedules `urlbar-ipc` for opt on autoland and
  mozilla-central. Debug `uipc` runs on try only and needs `--full`, which caps `--rebuild`
  at 3; with no debug results on autoland or central, a debug-only failure needs its own
  baseline push at the base.
- **`auto` picks a variant's tasks one platform at a time, not as a set.** For a
  comment-only urlbar JS change (2026-10-02), it scheduled the Linux `swr-uipc` task and
  neither the Windows nor the macOS `uipc` one. To reproduce a variant's full coverage,
  select its labels with `fuzzy`.

### Platform naming

- Linux 22.04: the Wayland variants carry `wayland` in the task name.
- Linux 24.04: **x11 is the default and is not named**, so select it with
  `!wayland`. There is no `x11` string to match on.

## Verifying and reading results

Use the bundled script. It answers the four recurring questions and cites a URL
or push id for every claim.

```
python3 ~/.claude/skills/try-push/scripts/try-status.py \
  <subject-substring | revision | landoCommitID> \
  [--since 4h] [--test NAME] [--push-id N] [--json]
```

Abbreviated below as `try-status.py`; always invoke it by that full path — a bare
`scripts/try-status.py` resolves against the checkout, where it doesn't exist.

```
try-status.py "Bug 2063202 - report the minimized"   # subject search
try-status.py 8d366a6e34ac                            # by revision
try-status.py 8d366a6e34ac --test browser_foo.js      # did this test pass
try-status.py --push-id 2001655 --json                # machine-readable
```

It reports: whether the push exists (revision, push id, time, author); whether
it is complete (job counts by state and by result, platform coverage); what
failed, grouped per test **file** and deduped across chunks and `--rebuild`
repeats (`x18/18` = failed all 18 times that task ran); and for `--test`, that
test's `TEST-PASS` / `TEST-UNEXPECTED-*` lines read out of a real job log.

Exit status 1 means it could not resolve a push - it never guesses one.

### `xN/N` counts tasks, so the failure may be test-verify

`x6/6` is six failing *tasks*, not browser-chrome chunk 6. Read the job names out
of the jobs API before assuming a chunk: test-verify runs the test **files the
push modifies**, so touching a test file schedules it, and a green browser-chrome
run says nothing about it. Check that the comparison push scheduled TV at all - a
push with no `test-verify-N` job cannot establish that a TV failure is new, and
`--test` reads only failing logs, so its "did not run" is not an answer either.

Its four steps are different execution modes. Step 1 repeats the file 10x in one
browser, step 2 runs it 5x in a fresh browser each time, and steps 3 and 4 repeat
those under `MOZ_CHAOSMODE=0xfb`. Which one failed is in the job's
`public/test_info/mochitest-*-test<N>_errorsummary.log` artifact. Step 3
reproduces locally as:

```
MOZ_CHAOSMODE=0xfb ./mach mochitest --repeat 10 --run-until-failure <file>
```

A stack that touches a test file makes TV run it for the first time, so a TV
failure reads as caused by the stack when it predates it. Baseline it: revert the
range in the working tree, rebuild, and run the same command at the push's base.

### Polling cadence

Two checks with different questions.

**First check at ~3 minutes: did anything get scheduled?** Lando takes a couple
of minutes to create the push, so **two** outputs are transient in the first
minutes, not just one: `only the decision task exists so far` once the push
exists, and `PUSH NONE FOUND` before it does. Treating the second as terminal is
the easy mistake - a check at 30 seconds reports `NONE FOUND` for a push that
turns up fine a minute later. Wait out both, and only past ~5-10 minutes read it
as broken (a `DONTBUILD` in the message, a test-file path that pruned every task,
a query that matched nothing, or Lando never pushing at all). Catching that in
the first minutes is what makes a re-push cheap instead of an hour wasted; don't
settle into a long poll before seeing a job count above 1.

**But `NONE FOUND` is never enough to re-push on, however long it has run.**
Lando's queue depth is not bounded by those minutes: one `auto` submission
observed 2026-09-10 took **32 minutes** to appear in the push API at all, having
reported `NONE FOUND` across a 25-minute poll and every lookup by subject,
revision and landoCommitID, over all authors. Re-submitting on that evidence
produced a duplicate push, and a push cannot be cancelled from a session — only
the user can, in Treeherder. So treat a long `NONE FOUND` as a reason to *ask*
rather than to act: report the wait, say a duplicate is the cost of guessing
wrong, and let the user choose. Only the git-to-hg abort below, or Lando
reporting something other than `LANDED`, identifies a submission that will never
arrive.

A wait loop has to exit on *either* transient state clearing, and its first
sleep comes before its first check:

```
for i in $(seq 1 20); do
  sleep 45
  out=$(python3 ~/.claude/skills/try-push/scripts/try-status.py "<subject>" --since 2h 2>&1)
  echo "$out" | grep -qiE 'NONE FOUND|only the decision task' || { echo "$out"; break; }
done
```

**Don't assume an `auto` push is unsearchable by subject.** It has been reported that
`auto` replaces the push comment's first line with `Tasks automatically selected.`,
which would leave your `-m` text out of what Treeherder indexes — but a Lando-submitted
`auto` push observed 2026-08-27 kept the `-m` text as the comment's first line and
resolved fine by subject. So try the subject first, and fall back to author lookup
(`try-status.py` filters by your Lando email by default, so pass no target) if it misses
rather than skipping straight to it.

**For an `auto` push, the transient window is longer than for `fuzzy`.** The graph is
computed *by* the decision task, so `only the decision task exists so far` is the normal
state for the first several minutes — one observed push sat there past 7 minutes before
expanding. Key the poll on the `push_id` once you have it, and don't read that state as
broken until well past the ~5-10 minutes that applies to a locally-computed selection.

**Then poll every ~10 minutes, reading the partial results.** Don't treat the run
as opaque until the last chunk lands: the script groups per-file failures over
the jobs that have *completed*, so a failure in an early-finishing xpcshell chunk
is actionable while browser-chrome is still going. Sleep first and then check
when you already know the current state. Nothing is gained by a shorter interval
- a run takes tens of minutes to hours, so a 4-minute loop is a dozen API
round-trips that tell you nothing.

To be told when the run ends, run the wait as a background task keyed on the
verdict, not on job counts:

```
for i in $(seq 1 30); do
  sleep 600
  out=$(python3 ~/.claude/skills/try-push/scripts/try-status.py --push-id <id> 2>&1)
  echo "$out" | grep -q "IN PROGRESS" || break
done; echo "$out"
```

A failed build or toolchain task leaves every task depending on it `unscheduled`
for good, since Taskcluster schedules a task only after all its dependencies
succeed. The script counts those as done once nothing is pending or running and
every unscheduled job traces back to a failed task, and
prints `COMPLETE (N job(s) never scheduled: a dependency failed)` with the failed
task behind each. A hand-rolled count of `unscheduled` as still to come never
reaches zero on such a push, and the loop sleeps out its whole budget. In a push's
first minutes every build is `unscheduled` behind toolchain, fetch and docker-image
tasks that Treeherder doesn't list; with no failed task behind them, the script
reports those as IN PROGRESS.

### Don't trust any signal but the jobs API

`mach try` printing "try submission success", the `landoCommitID` URL it prints, and Lando's
own `status: "LANDED"` all prove only that **Lando accepted the request** — each of them
appears in full when no push, no decision task and no jobs exist, which is the single most
common false confidence here. `treeherder-cli --json` returns only *failing* jobs, so it
cannot say whether a push finished, and Treeherder's `push/health/` counts disagree with the
jobs API. Resolve a push by matching your `-m` text, never by taking "the newest one", and
never by pasting a bare number into `?id=`: a landoCommitID and a push id are numerically
indistinguishable and their id spaces overlap.

Two rules once the failures are in. `x1/6` is an intermittent and `x6/6` is your patch. And
**before blaming your patch for a deterministic failure, search Bugzilla for the bare test
name** — a test broken on the tree gets a `Perma …` bug, so searching `Intermittent <name>`
misses exactly the failures that are not yours.

A push tests the tree at its **base**, and a base a few days old can sit inside the window
between a landing and its backout, where Bugzilla has nothing because the backout came within
the hour (observed 2026-09-20: a base 14 minutes inside such a window failed two unrelated
tests 13/13 and 5/5 on every platform, and central passed the same chunks two days later).
Before building a local baseline, list the reverts after the base,
`git log --oneline <base>..origin/main | grep -i revert`, and compare the exact failing chunks
against central:

```
scripts/th-test-failures.py try --push-id <id> <test-substring>...         # test -> failing chunks
scripts/th-test-failures.py mozilla-central -n 3 --jobs <job_type_name>...  # those chunks on central
```

Chunk names compare only when the push adds or removes no test file in a manifest that runs
before the test, since chunking follows manifest runtimes.

`references/reading-results.md` carries each of those in full, the Treeherder endpoints for
going direct, and the retry double-count that inflates a hand-rolled denominator. It also
covers the green job that hides a retried failure, grepping every job's log with
`~/.claude/skills/try-push/scripts/try-grep-logs`, mapping autoland revisions, and a job pending for days.

## When a push exists but never gets a decision task

A push can show in Treeherder with your commits attached and sit at `completed: 0,
pending: 0, running: 0` indefinitely, with no `taskgraph.decision` task in the
Taskcluster index for its revision (observed 2026-09-18, push 2040702, still empty
after four hours). Decide by comparison, not by waiting: list the neighbouring pushes
in the same hour with their `push/<id>/status/` counts. When every neighbour got jobs
within minutes and yours alone is at zero past ~30 minutes, the decision task was never
created and will not be, so a re-push carries no duplicate risk. The bare
`landoCommitID` lookup prints a candidate list matched by time; take your revision from
there rather than `--push-id`, whose id space is different.

## When a submission is accepted but no push appears

If `mach try` succeeded and no push ever shows up, and re-submitting on a fresh
autoland base aborts with:

```
Could not determine the equivalent base commit for <sha> in hg for firefox.
Please try again later.
```

that is Lando's git-to-hg mapping failing, not your patch. Retrying does not
reliably help and rebasing onto an older base may not either. Raise it with
`#lando` rather than pushing at it repeatedly.
