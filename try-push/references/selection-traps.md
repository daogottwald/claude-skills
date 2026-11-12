# Selection traps, in full

Each of these makes a push return a confidently green or confidently red answer that means
nothing. `try-push` lists the rule for each one; this file has the reasoning, the log
signatures and the measurements behind them, which is what you want when a push has already
come back wrong.

## Artifact builds silently skip compilation

If the local mozconfig has `--enable-artifact-builds`, `mach try` prints:

```
Artifact builds enabled, pass --no-artifact to disable
```

and the push builds **artifacts** - it downloads a prebuilt binary instead of
compiling your patch. A C/C++/Rust change is then never compiled and the entire
run is meaningless while looking perfectly green. For any C/C++/Rust change pass
`--no-artifact` (or push from a full-build worktree).

Separately, exclude `-artifact` **test** platforms from the selection (`!artifact`
in the query) for the same reason.

Note that `auto` inherits the mozconfig setting too - its strategy flags come back with
`use-artifact-builds: true` - so a front-end-only push you never passed `--artifact` to is
still an artifact push, and hits the trap below.

An **`.idl` change is the trap inside the trap**, because the rest of the patch can be
pure JS. An artifact build cannot regenerate xpt from a local idl, so a constant the
patch adds reads `undefined` at runtime: `flags |= undefined` no-ops, nothing throws,
and the assertions score the *unforced* behavior while reporting green. Push any branch
touching a `.idl` with `--no-artifact`, and name the xpcshell directories that exercise
the interface, which a browser-chrome selection will not reach.

The same holds for a local artifact build, however often you rebuild: the XPT comes down
with the downloaded binaries. A `.sys.mjs` implementation destructuring the new name off
`Ci` gets `undefined` too, so `flags & FLAG` is `0` and the tests fail exactly as though
the JS were wrong. Before debugging a JS-looking failure on a branch that touches a
`.idl`, probe the constant from `<objdir>/dist/bin` (libxul will not load without
`LD_LIBRARY_PATH=.`):

```
LD_LIBRARY_PATH=. ./xpcshell -e 'print(Ci.nsIURIFixup.FIXUP_FLAG_FORCE_KEYWORD_LOOKUP)'
```

For a local verdict without a full build, substitute the literal value on **both** sides,
the `Ci.` read at the call site and the `} = Ci.nsIFoo;` destructure in the
implementation, run the tests, then revert.

## An artifact build needs an indexed build near the base

`--artifact` resolves each platform's build by walking back up to **50 pushheads** from the
base on the base's own tree, and `recurse_artifact` busts the build task - its test chunks then
go `unscheduled` - when nothing is found. The index namespace it searches depends on the build
type:

- **opt** -> `gecko.v2.<tree>.shippable.revision.<rev>.firefox.<platform>-opt`
- **debug** -> `gecko.v2.<tree>.revision.<rev>.firefox.<platform>-debug`

So the plain `revision.…-opt` namespace existing proves nothing for an opt artifact build; check
the `shippable` one. How stale a tree's newest is, per platform:

```
curl -s "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.autoland.shippable.latest.firefox.win32-opt"
```

Measured on autoland: `macosx64` and `win64` shippable are current (every push), while **`win32`
shippable is roughly weekly** - far outside the 50-pushhead window. So from an autoland base
`build-win32/opt` reliably busts, while `build-win32/debug`, `build-macosx64/opt` and
`build-macosx64/debug` succeed. mozilla-central carries all of them current, so an opt-on-win32
artifact push wants a central base or a real non-artifact build (~40 min). Re-measure rather than
trusting the weekly figure. Local git SHAs work fine in these index lookups, so a git-based
checkout needs no hg revision to check one.

Grep the busted build's log for its own signature rather than reading the mozharness `FATAL`
above it, which only says `'mach build -v' did not run successfully`:

```
Tried 50 pushheads, no built artifacts found.
gmake[3]: *** [Makefile:76: recurse_artifact] Error 1
```

**A second, unrelated-looking task fails from the same cause: `fuzzing-simple` on linux64/opt,
with `FileNotFoundError: ./js`.** An artifact build ships no JS shell for the fuzzer to run, so it
fails alongside the busted build on any artifact push and reads as a separate problem. Neither is
attributable to the patch.

Don't rebase onto a fresh autoland tip to get closer to an indexed build - the tip's own builds
usually aren't indexed yet. And weigh the re-push before making one: a busted opt build costs that
platform's opt test chunks, but its debug build and chunks still run, so for a change that
compiles nothing the platform is still covered and a `--no-artifact` re-push buys no signal.

### An artifact push can test the base's front-end on older binaries

The artifact lookup takes the nearest indexed build at or below the base, and for opt that is often a push or two older than the base itself. When a commit between the two changed C++, Rust or a vendored crate together with the JS that calls it, every test runs the base's JS against binaries without the native half, even for a patch that is pure JS. Measured 2026-10-14 (try push 2068615): the base sat five minutes after bug 2076174's WebIDL `getMenuItems()` and just after an application-services vendor; linux64-opt took its binaries from a push 9 minutes older and win64/macosx64-aarch64-opt from one 35 minutes older, while linux64-debug resolved to the base itself and showed none of it. The signatures, spread across unrelated suites on opt platforms only:

- `TypeError: <element>.<newMethod> is not a function` for a method the base's C++ just added;
- `UniFFIScaffolding.callSync: LowerRustArgs: Incorrect argument length`, `Engine store failed to initialize`, `SearchService failed while it was initializing`;
- `PROCESS-CRASH | … ABORT: file moz-src:///toolkit/components/uniffi-js/js/UniFFI.sys.mjs`;
- Glean `testGetValue()` returning `null` in Marionette tests, so `len(events)` raises a `TypeError`.

An uncaught rejection from the skew fails whatever test is running, so the patch's own test can go red with every check of its own passing; read its `TEST-PASS` lines before counting it. Confirm by grepping each build log for `ArtifactFind` and comparing the revision it names with the base. Judge from the debug jobs, which resolve to the base more often, or re-push with `--no-artifact` for a clean opt signal.

### A local artifact build that finds nothing: dead mirror SHAs

A local `mach build` or `mach artifact install` looks up each of HEAD's last 500 ancestors in
the index on two candidate trees, and fails with `Tried 1000 pushheads, no built artifacts
found`. The github mirror (`github.com/mozilla-firefox/firefox`) regenerates autoland history,
so a branch rebased onto `origin/autoland` before a regeneration sits on same-content,
different-SHA copies of upstream commits, and every lookup 404s. Neither `mach artifact
install` nor a clobber helps, since nothing about the objdir is wrong. Query the index
directly instead of re-running the build; a live SHA answers 200 and a dead one 404:

```
curl -s -o /dev/null -w '%{http_code}' \
  https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.<tree>.revision.<sha>.firefox.linux64-opt
```

with `<tree>` as `mozilla-central` or `autoland`. An empty `git branch -r --contains
<ancestor>` for an obviously-upstream commit (an l10n bump) is the same signal.

To fix it, find the branch's commits in `origin/autoland` by subject, confirm
`git diff --stat HEAD <twin>` is empty, then `git reset --keep <twin>`. Only the hashes
change, and the artifact build works at once. When the branch must not move (a stack already
up for review), pin the build instead: `MOZ_ARTIFACT_REVISION=<twin sha> ./mach build`, taking
the twin of the stack's *base* (`git log origin/main --grep '<base subject>'`), not
`origin/main`'s tip. A tip two days newer installs a binary with a uniffi ABI that disagrees
with the tree's JS bindings, and every mochitest dies in `UniFFIScaffolding.callSync`. A bare
`mach artifact install <sha>` plus `mach build faster` is not enough on a fresh objdir: it
never builds `dist/xpi-stage/specialpowers`, so every browser-chrome run reports `invalid
setup: missing extension` and `Ran 0 checks`.

## A test file path prunes the whole suite

Passing a test *file* to `mach try` drops every task in that suite: the manifest
match requires the path to be a prefix of a manifest's directory. Pass the
**directory** instead.

```
# wrong - prunes the entire suite
./mach try fuzzy --full -q "..." browser/components/urlbar/tests/browser/browser_foo.js
# right
./mach try fuzzy --full -q "..." browser/components/urlbar/tests/browser
```

A path mistake shows up in `try-push`'s dry-run count as a suspiciously small
selection, which is the other half of why that gate is worth the 30 seconds.

The prefix match cuts the other way too, usefully: a parent directory sweeps in any
sibling subdirectory sharing its prefix, so `browser/components/urlbar/tests` reaches
`tests/browser-newtab` without naming it.

## Any try-level scoping collapses a job to one chunk

`target_tasks.py` drops every chunk but `-1` when `try_task_config` carries
`MOZHARNESS_TEST_PATHS` or `MOZHARNESS_TEST_TAG`. So a variant that carries its own tag
in its task definition must be pushed with **no paths and no `--tag`** - otherwise the
scoping you add collapses the job you wanted spread across chunks. It also means the
one-chunk runtimes you read on try are that collapse and not production timings; don't
call a slow chunk a regression.

A collapsed job can outrun the 60-minute max run time. The task then aborts with
`[taskcluster:error] Task aborted - max run time exceeded`, which Treeherder reports as
`testfailed` with no test-level errors, and every test after the abort point went unrun.
A duration near 60 minutes with zero assertion failures means truncated coverage, not a
regression, so check the job's duration before triaging a bare `testfailed`; macOS hits the
cap first. Paths are per push: positional paths apply to every `-q` in the push, so a
directory that needs its own chunk needs its own push.

## A subsuite runs in a task no path or tag reaches

A directory marked `subsuite = "a11y"` runs in `mochitest-browser-a11y`, a different
task from `mochitest-browser-chrome`, so a browser-chrome push cannot reach it however
the paths are written - it needs a push of its own. Tagging those tests does not help
either: a task that declares no `variants:` has no variant job for the tag to land in.

## A default flip needs the push to find the tests that assume the old default

A test that waits for the pre-flip UI does not turn the gate on for itself, so it passes
locally and on every push that lacks the flip. Only a push *carrying* the flip finds
them, and the first stale wait in a file times out everything after it, so one bad wait
reads as a whole file of failures. Expect them in test roots the patch never touches,
and give each one an explicit pre-flip pref in its own setup.

## DONTBUILD strips the whole graph

Never put `DONTBUILD` (or `CLOSED TREE`) in the `-m` message. The Gecko decision
task scans the message and on `DONTBUILD` removes every task from the graph. The
decision task itself succeeds, Treeherder shows green, and no builds or tests
were ever scheduled.

