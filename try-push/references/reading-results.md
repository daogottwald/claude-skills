# Reading a try result without being lied to

Why every signal other than the jobs API can report a push that does not exist, the
Treeherder endpoints if you need them without `try-status.py`, and how to count failures so
the denominator means something. `try-push` carries the poll cadence and the one-line version
of each trap.

## Why the other signals lie

- **`mach try` printing "try submission success"** and a URL like
  `https://treeherder.mozilla.org/jobs?repo=try&landoInstance=...&landoCommitID=78723`
  proves only that **Lando accepted the request**. The push may never
  materialize. This is the single most common false confidence.
- **Lando's own status.** `GET https://api.lando.services.mozilla.com/landing_jobs/<landoCommitID>`
  (header `Authorization: Bearer <access_token>` from
  `~/.mozbuild/lando_auth0_user_token.json`) returns
  `{"commit_id", "id", "status"}`. `status: "LANDED"` appears even when no try
  push, no decision task and no jobs exist. `commit_id` is Lando-internal and
  never equals the Treeherder revision, is not a local git object either, so
  looking it up via `?revision=` always reads as "not ingested" and proves
  nothing. `/landings/<id>` does not exist (404).
- **A landoCommitID cannot be mapped to a push at all.** Match your
  `mach try -m` message text against `revisions[].comments` instead. Worse, a
  landoCommitID is numerically indistinguishable from a Treeherder **push id**,
  and the id spaces overlap: `78723` is both a real Lando job and a real 2016
  push. The script therefore reads bare numbers as Lando ids, flags the
  collision, and requires `--push-id` for a push id. Do not paste a number into
  `?id=` and believe the answer.
- **`treeherder-cli <revision|landoCommitID>`** prints a failure summary, and
  `--json` returns **only failing jobs** (`{revision, push_id, jobs:[...]}`), so
  it cannot tell you whether a push finished. `--fetch-logs --pattern <regex>
  --filter <job-name-regex> --cache-dir <dir>` downloads logs for **failing jobs
  only**. Its message "No jobs found matching the specified criteria" is
  ambiguous between "no such push" and "the decision task has not run yet" - the
  pushes API distinguishes them. For a Lando-submitted push, `treeherder-cli
  <landoCommitID>` may never resolve at all: it flaps between that message and
  `Error: No push found for revision` long after the jobs run, even after they
  finish. Both strings mean it cannot resolve the push, not that nothing is
  scheduled; take the real revision from the pushes API (`?author=<email>`) and
  pass that.
- **Treeherder's own `push/health/` endpoint** reports a `status` block whose
  counts disagree with the jobs API (it excludes some classified failures).
  Count from the jobs API.

## The APIs, if you need them directly

Treeherder rejects the default `urllib` User-Agent with **403**; send any
`User-Agent`. It also resets connections under concurrency, so retry.

- Pushes: `GET https://treeherder.mozilla.org/api/project/try/push/?count=200&push_timestamp__gte=<epoch>`
  -> `{"results":[{id, revision, author, push_timestamp, revision_count, revisions:[{comments,...}]}]}`.
  Also accepts `?revision=<12 or 40 hex>`, `?id=<push_id>`, `?author=<email>`.
  Always use a `push_timestamp__gte` window and match the `-m` text against
  `revisions[].comments`; "the newest one" is not a safe way to pick. Note
  `?lando_commit_id=` is accepted but **ignored** - it echoes the filter and
  returns an unfiltered page.
- Jobs: `GET .../api/project/try/jobs/?push_id=<id>&count=2000` ->
  `{"results":[{...}]}` with full job objects (`state`, `result`,
  `job_type_name`, `platform`, `platform_option`, `task_id`, `retry_id`,
  `duration`). A page caps at 2000 rows; page with `&offset=`. `state` is
  `completed` / `running` / `pending` / `unscheduled`; `result` is only
  meaningful once `completed` (otherwise `unknown`).
- Per-job failure lines: `GET .../api/project/try/jobs/<job_id>/bug_suggestions/`
  -> entries with `search` (the full `TEST-UNEXPECTED-*` line) and `path_end`
  (the test file). Far cheaper than downloading logs, and it is how the script
  groups failures. Entries whose `path_end` is not a test path are infra noise
  (`[taskcluster:error] exit status 1` appears on nearly every failing job).
- Raw log: `https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/<task_id>/runs/<retry_id>/artifacts/public/logs/live_backing.log`.
  A 12 MB browser-chrome log fetches in about 1.5 s. Every line is prefixed by
  the harness (`[task ...] 16:12:46 INFO - TEST-PASS | ...`), so never anchor a
  marker regex at the start of the line.
- **Autoland revisions are not the local SHAs.** A checkout tracking the github
  mirror (`github.com/mozilla-firefox/firefox`) holds `autoland` commits with
  different hashes from the revisions Treeherder indexes, so `treeherder-cli
  <local-sha> --repo autoland` answers `No push found for revision` at any SHA
  length. Map by commit subject instead, then pass the Treeherder revision on:

  ```
  curl -s -A x 'https://treeherder.mozilla.org/api/project/autoland/push/?count=5' |
    python3 -c "import json,sys;[print(p['revision'], p['revisions'][0]['comments'].splitlines()[0][:70]) for p in json.load(sys.stdin)['results']]"
  ```

  Try pushes are unaffected. The Taskcluster index carries routes for both SHA
  spaces, so `gecko.v2.autoland.revision.<local-sha>.firefox.<platform>` resolves.

## Harvesting every job's log

To measure something across a whole push (how long a helper's waits take, say),
add a `dump()` line, push, and grep every job's log. `dump()` from a privileged
`.sys.mjs` reaches the CI log prefixed `GECKO(pid) | `; in a talos log, browser
output appears as `PID <n> | <line>` and page boundaries as `Cycle a(b): loaded
<url> (next: ...)`. tp5o pages run in the `tp5n.zip` manifest order (alphabetical:
163.com, 56.com, aljazeera.net, ...), not `tests/tp5o.html` order.

`treeherder-cli --fetch-logs` covers failing jobs only, and `--filter` does not
widen it, so bypass it:

```
~/.claude/skills/try-push/scripts/try-grep-logs PUSH_ID REGEX [--failed-only] [--name TASK_REGEX]
```

streams every completed job's `live_backing.log` and prints the distinct matching
lines with job counts (630 failing browser-chrome logs took a few minutes with 12
threads, as of 2026-09-20). By hand: take `task_id`/`retry_id` per job from the jobs
API, then stream
`https://firefoxci.taskcluster-artifacts.net/<task_id>/<retry_id>/public/logs/live_backing.log`
line by line with a few threads rather than reading whole logs.

## A job pending for days

If a macOS job goes pending for days, read the real reason from Taskcluster rather
than Treeherder's status:
`curl -s https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/<taskId>/status`.
An `exception` with `reasonResolved=deadline-exceeded` means the task was scheduled
and never claimed. Take the task id from a job whose `job_type_name` starts with
`test-`; filtering on "macos" alone also matches `build-macosx64/opt`, which
completes normally and misleads.

## Counting failures honestly

- A retried job appears **twice** in the jobs API: once with `result: "retry"`
  and again as the rerun, sharing one `task_id`. Count distinct `task_id`s or
  your denominator is inflated.
- **A green job can still hold the failure you are looking for.** mochitest reruns
  a failed browser-chrome test ("Not taking screenshot here: screenshot will be
  taken on retry if the test still fails"), and when the rerun passes the job is
  `success`. The failure then shows nowhere in Treeherder, nor with
  `treeherder-cli --include-intermittent`; only the log has it, as a `TEST-FAIL`
  line and a nonzero `Retried:` count. When a run has to prove nothing broke, grep
  every job's log (above) for the failure signature and for `Retried:`, and treat
  a green push as inconclusive until you have.
- Pick the representative failure line deliberately. Mochitest emits
  `finished in Nms`, `profile uploaded in ...`, `leaked N window(s)` for many
  failures; taking the first line alphabetically surfaces that noise instead of
  the actual assertion or timeout.
- `x1/6` versus `x6/6` is the whole point of `--rebuild`: the first is an
  intermittent, the second is your patch.
- **Before blaming your patch for a deterministic failure, search Bugzilla for the
  test name** — and search the bare name, not `Intermittent <name>`. A test broken
  on the tree gets a bug titled `Perma …` or `Perma [tier 2] …`, so an
  intermittent-only search misses exactly the failures that are *not* yours. A
  `Perma` bug resolved after the base revision you pushed from explains a 2/2 or
  3/3 failure completely. `short_desc` + `short_desc_type=allwordssubstr` on
  `/rest/bug` is the query; also check whether an open intermittent bug names it,
  which is the answer for a 1/N.
