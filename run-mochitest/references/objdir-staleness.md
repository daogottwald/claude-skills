# Diagnosing a stale objdir

Which refresh a given failure signature wants. `run-mochitest`'s §1 holds the pre-flight
CLOBBER check and the everyday cases; reach here when a run crashes with `Missing chrome or
resource URLs:`, reports `0 checks`, exits 0 with no counts at all, aborts installing an
add-on, passes surprisingly soon after an artifact install, or when `./mach build faster`
itself fails. Each of those is a *different* stale-objdir cause, and the
wrong refresh partially fixes it — which reads as progress and costs another round. The in-tree `desktop-theme-probing` skill's `references/build-freshness.md` covers the same CLOBBER, uniffi and `moz-src` signatures for a probe launch; this file adds the cases a test run hits.

## Which refresh a signature wants

- Anything after `./mach artifact install` (or `./mach bootstrap`): it unpacks the downloaded
  build over the objdir, **reverting locally-built front-end JS** to upstream. Always
  `./mach build faster` after. The trap is that this reads as a **pass** — green because your
  change isn't there — so treat a surprising result in *either* direction soon after an
  install as a stale objdir until you've rebuilt. The reverse also holds: `LowerRustArgs:
  Incorrect argument length for uniffi_…` or `Missing chrome or resource URLs: …` mean the
  objdir is stale, not that the patch is broken — but which refresh it wants depends on the
  cause, so work through the cases below rather than reaching for an artifact install.
- **Built chrome out of step with the tree: a Fluent id the markup names and nothing defines.**
  Preprocessed markup (`browser.xhtml` and its `*.inc.xhtml` includes) and packaged `.ftl`
  files reach the objdir as built copies, not symlinks, so after the tree moves (a branch cut
  from fresh autoland, a rebase, a pull) or another branch's build touches the objdir, the two
  can disagree. The log shows `[fluent] Missing message in locale en-US: <id>` during browser
  startup, then three bare `JavaScript error: , line 0: uncaught exception: undefined` lines
  each time a window opens, and most window-opening tests fail with `uncaught rejection:
  undefined` and no stack. A single file can pass while a directory run fails, which reads as
  cascade damage. Run `./mach build faster`; both lines disappear. Don't try to place the id
  first: in the case that produced this entry it was in neither the tree's source nor its
  history, because the stale built markup came from a different source state.
  `mochitest-summary.py` reports this as `stale objdir (missing Fluent string)`.
- **The mochitest harness itself, when the objdir predates a harness file being added
  upstream — and only `./mach build` stages it.** `chrome://mochikit/content/` resolves
  inside a *local build product* staged at `obj-*/dist/xpi-stage/mochijar/` from
  `testing/mochitest/moz.build`, and `./mach build faster` does not regenerate
  `dist/xpi-stage/` (nor do `install-tests`, `build testing/mochitest` or `build-backend`).
  `browser-test.js` loads those files unconditionally, so one missing file MOZ_CRASHes the
  parent through `CheckForBrokenChromeURL` and **every browser-chrome test in that objdir
  reports `0 checks`** — an infra error that looks nothing like a stale objdir. The tell is a
  `Missing chrome or resource URLs: chrome://mochikit/content/tests/SimpleTest/<file>.js`
  line; confirm it's patch-independent by running any unrelated browser-chrome file, then run
  plain `./mach build` (fast even on an artifact config). Copying the file into the staging
  dir by hand works for one run and leaves the next one to rediscover it.
- **A `moz-src:///` product module added upstream while the objdir predates it — here
  `./mach build faster` is enough.** Same `Missing chrome or resource URLs:` signature and the
  same `CheckForBrokenChromeURL` crash reporting `0 checks` as the harness case above, so read
  the URL in the crash to tell them apart: this one names a product file
  (`moz-src:///browser/modules/<Foo>.sys.mjs`), not a `chrome://mochikit/` one. An artifact
  refresh does not help, and plain `./mach build` works but costs far more than it needs to.
  The trigger is the objdir's age against the commit that added the module, not how far behind
  upstream it is — a freshly-built objdir hundreds of commits back is fine, an untouched one
  two days old is not.
- **A module *moved* to `moz-src:///` upstream on an artifact build — only `./mach artifact
  install` fixes it.** The crash names the module's old URL (`Missing chrome or resource URLs:
  resource://gre/modules/URIFixup.sys.mjs`, after bug 2076786 moved it), because the downloaded
  binary still references the old path while the tree no longer packages it there. `./mach
  build faster` and a plain `./mach build` leave it (the latter finished in 25s without
  fetching anything); `./mach artifact install` then `./mach build faster` cleared it. Tell
  it from the added-module case by the URL: the old `resource://` path, not a `moz-src:///` one.
- **The checkout moved since its objdir was built — the harness aborts with `Could not
  install Add-on: ERROR_CORRUPT_FILE`.** `runApp` zips `dist/xpi-stage/specialpowers` and
  `mochijar` into temp xpis and installs them over Marionette, so one dangling symlink in
  either staged dir fails the zip; the run ends at `Automation Error: Received unexpected
  exception while running application` with `Ran 0 checks`, before a single test. Those dirs
  hold **absolute** symlinks into the source tree, and no build repoints them — `config.status`
  picks up the new `topsrcdir` while the links keep the old one, so `./mach build faster`
  reports success and the harness keeps failing. Two things mislead: `unzip -t` on the
  committed `dist/xpi-stage/specialpowers@mozilla.org.xpi` passes, because the harness zips the
  *directory* rather than that file; and the count reads as a total wipeout rather than a run
  that never started. Confirm with `find obj-*/dist/xpi-stage -xtype l | head` — if the targets
  name a path the checkout no longer lives at, rewrite each link's prefix to the current one
  (thousands of them, so script it). Dangling `_tests` links are usually deleted scratch test
  files and harmless.
- **The artifact binary predates the rebased tree — every run dies before its first test with
  `Could not install Add-on: ERROR_FILE_ACCESS`.** The browser crashes about a second after
  Marionette starts (a minidump, no crash reason in the log), so the harness's add-on install
  is what reports it. The same test fails identically against the parent commit's files, and a
  bare probe launch still starts fine. Check `platform_buildid` in the mozversion lines
  against the tree's base date; `./mach artifact install` then `./mach build faster` fixes it.
- **An artifact install stripped `dist/bin/components`, and the httpd never starts.** The
  unpack replaces `dist/bin` wholesale, taking with it the files the build's own install
  manifests put there — among them `httpd.sys.mjs`, which `netwerk/test/httpserver` installs
  via `EXTRA_COMPONENTS`. `runtests.py` points the `resource://httpd-server` substitution at
  `<utilityPath>/components`, so with that directory gone the harness logs `Missing chrome or
  resource URL: resource://httpd-server/httpd.sys.mjs` and then `Timed out while waiting for
  server startup` about 90 seconds later. Nothing names the objdir, and the run reads as the
  web server being broken. A second plain `./mach build` re-runs the install manifests and
  restores it — `./mach build faster` does not. Confirm with
  `ls <objdir>/dist/bin/components/httpd.sys.mjs`.
- **An upstream `CLOBBER` bump — the run exits 0 having tested nothing.** After pulling or
  rebasing onto new upstream commits, `./mach mochitest` can print "The build system was
  unable to install tests because the CLOBBER file has been updated" and **exit 0 with no
  counts at all**, so a caller reading the exit code alone records a pass for a suite that
  never started (`mochitest-summary.py` catches it: `VERDICT: unknown — no counts in the
  log`). `touch <objdir>/CLOBBER` is safe when your patch adds no test files and changes no
  test manifest, since an edit to an existing test file reaches the objdir through its
  symlink. Otherwise `./mach clobber && ./mach build`, but read the tree's mozconfig first:
  a clobber throws away a compiled build that takes an hour or more to redo, so ask the user
  before clobbering any tree that doesn't have `--enable-artifact-builds` active. Note `./mach build` may then finish
  suspiciously fast and leave `_tests/` looking nearly empty — that is fine, `mach mochitest`
  installs the tests it needs at run time, so smoke-test one file rather than diagnosing the
  empty tree.
- **Compare CLOBBER *mtimes* before believing any stale-objdir diagnosis — never their
  contents.** A CLOBBER bump does not always announce itself: it also presents as a startup
  **crash** with `Missing chrome or resource URLs: …` — the same signature listed above as
  wanting an artifact refresh. An `artifact install` then *partially* fixes it (the first
  missing URL goes away, a different one appears, e.g. a `chrome://mochikit/…` harness file),
  which reads as progress and costs another round. The one-second check tells the two apart:

  ```
  [ CLOBBER -nt <objdir>/CLOBBER ] && echo "clobber needed" || echo "objdir is current"
  ```

  `mozbuild`'s `clobber_needed()` compares `getmtime(src)` against `getmtime(obj)`, and the
  objdir's CLOBBER is a **0-byte marker** that mozbuild touches, not a copy of the source
  file. So `diff CLOBBER <objdir>/CLOBBER` reports a difference on every healthy tree — it
  reads as a stale objdir every time, which is worse than not checking, since it sends you
  into a needless `clobber && build`.

## `build faster` itself fails on crash-ping metrics

Signature: `./mach build faster` (and `./mach build`) stops at `toolkit/crashreporter/crashping/.deps/generated_metrics.yaml.stub` with `generated_metrics.yaml contents outdated; run ./mach crash-ping-metrics`, in an objdir built before `toolkit/crashreporter/CrashAnnotations.yaml` last changed upstream. It reads as a tree problem, because `git log` shows the tracked `generated_metrics.yaml` was just touched. The command the message names is a dead end: `./mach crash-ping-metrics` exists but only rewrites the tracked srcdir file, which is already current. The check compares against metrics regenerated from the objdir's `toolkit/crashreporter/annotations/validated.yaml`, and its stale `.stub` keeps make from rebuilding it, so the comparison runs against the old annotations. Workaround: `rm <objdir>/toolkit/crashreporter/annotations/.deps/validated.yaml.stub && ./mach build faster`, then confirm that the mtime of `<objdir>/toolkit/crashreporter/annotations/validated.yaml` moved to now. Don't run the generator by hand or edit the tracked file. Seen 2026-09-22 (objdir from 2026-09-16, annotations change from 2026-09-21); the diagnosis was checked on 2026-09-25, and a build confirmed the workaround on 2026-11-04.

## Another branch's build touched this objdir

Signature: a compile error in *generated* code right after switching branches — `no member named 'dom_fetch_streaming_upload' in namespace 'mozilla::StaticPrefs'` from `dom/bindings/WebIDLPrefs.cpp`, or a cbindgen header (`*_rust_generated.h`) missing a type. Cause: another session ran `./mach build` or `./mach configure` in this objdir at a different revision, so the generated headers carry that revision's content with mtimes newer than your sources, and make treats them as current. No mtime trick on the sources repairs it (a 03:00 backdate of identical sources still leaves the 13:30 foreign headers in place). Diagnose with `find <objdir> -type f -newermt '<their build time>' ! -newermt '<+10min>'`; the fix is `./mach clobber && ./mach build` (47 min at -j6 on an 8-core host for a full non-artifact build, measured 2026-09-20).

**`Glean.<category>.<metric> is undefined` at startup, often with `[fluent] Missing message` lines.** In an artifact build, metrics added since the downloaded artifacts reach JS only through `jogfile.json`, which `./mach build` regenerates. If any earlier generator fails (seen on an autoland tip: `generated_metrics.yaml contents outdated; run ./mach crash-ping-metrics`, then `AssertionError: consistent options` in `run_glean_parser.py jog_file`), make stops before writing it, and the `./mach build` wrapper can still return 0. The product then throws wherever it records the new metric: SessionStore's initialization failed, so every session store test hung. Read `artifacts/build.log` for ` E ` lines, not the exit code; for the crash-ping failure, apply the `validated.yaml.stub` workaround above and rebuild. For `consistent options`, the objdir's `toolkit/components/glean/*_yamls.cached` pickles disagree on `expire_by_version`: a milestone bump rebuilt `metrics_yamls.cached` but not `pings_yamls.cached`, because the version is not one of its make dependencies. Print each file's options with `python3 -c 'import pickle,sys; print(pickle.load(open(sys.argv[1],"rb"))[1])' <file>`, delete the stale one and its `.deps/<name>.stub`, and rebuild. Confirmed by a build on 2026-11-04, when the `validated.yaml.stub` workaround was confirmed too.
