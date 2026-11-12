#!/usr/bin/env python3
"""Answer the four questions that recur on every Firefox try push.

  1. Does the push exist?      (Lando accepting a submission proves nothing)
  2. Is it complete?           (counts by state and result, platform coverage)
  3. What failed?              (grouped per test file, deduped across chunks/rebuilds)
  4. Did test X pass?          (--test, read from a real job log)

Usage:
  try-status.py <subject-substring | revision | landoCommitID> [options]

Every line of output cites where it came from. Read-only; creates no push.
"""

import argparse
import collections
import concurrent.futures
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TH = "https://treeherder.mozilla.org"
LANDO = "https://api.lando.services.mozilla.com"
TC = "https://firefox-ci-tc.services.mozilla.com/api/queue/v1"
TOKEN_FILE = os.path.expanduser("~/.mozbuild/lando_auth0_user_token.json")

# Treeherder 403s the default urllib User-Agent.
UA = "try-status.py (+local dev tool)"

# Log lines are prefixed by the harness ("[task ...] 16:12:46  INFO - "), so the
# marker is matched anywhere in the line rather than anchored.
TEST_LINE = re.compile(
    r"(TEST-PASS|TEST-OK|TEST-UNEXPECTED-[A-Z-]+|TEST-KNOWN-FAIL|TEST-START)\b.*"
)


def fetch(url, headers=None, timeout=90, raw=False, tries=3):
    """GET with retries: Treeherder resets connections under concurrency."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            return data if raw else json.loads(data)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            if attempt == tries - 1:
                raise
            time.sleep(0.5 * 2**attempt)


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def parse_since(s):
    m = re.fullmatch(r"(\d+)\s*([hdmw])", s.strip().lower())
    if not m:
        die(f"--since must look like 4h, 90m, 3d, 2w (got {s!r})")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]


def ago(ts):
    d = int(datetime.datetime.now().timestamp() - ts)
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h{(d % 3600) // 60:02d}m ago"
    return f"{d // 86400}d{(d % 86400) // 3600:02d}h ago"


def when(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


# --- target resolution ------------------------------------------------------


def default_author():
    try:
        return json.load(open(TOKEN_FILE))["email"]
    except Exception:
        return None


def lando_status(lando_id):
    """Return Lando's own view of a landing job, or None if unavailable.

    Lando's commit_id is internal and never equals the Treeherder revision, so
    this can confirm only that Lando accepted the request -- never that a push,
    a decision task or any job exists.
    """
    try:
        token = json.load(open(TOKEN_FILE))["access_token"]
    except Exception as e:
        return {"error": f"no Lando token ({TOKEN_FILE}): {e}"}
    url = f"{LANDO}/landing_jobs/{lando_id}"
    try:
        d = fetch(url, headers={"Authorization": f"Bearer {token}"})
    except urllib.error.HTTPError as e:
        return {"error": f"{url} -> HTTP {e.code}"}
    d["url"] = url
    return d


def pushes_in_window(since_s, author=None, count=200, repo="try"):
    gte = int(datetime.datetime.now().timestamp() - since_s)
    q = {"count": count, "push_timestamp__gte": gte}
    if author:
        q["author"] = author
    url = f"{TH}/api/project/{repo}/push/?{urllib.parse.urlencode(q)}"
    return fetch(url)["results"], url


def push_by_revision(rev, repo="try"):
    url = f"{TH}/api/project/{repo}/push/?revision={rev}"
    return fetch(url)["results"], url


def push_by_id(push_id, repo="try"):
    url = f"{TH}/api/project/{repo}/push/?id={push_id}"
    try:
        return fetch(url)["results"], url
    except Exception:
        return [], url


def push_comments(push):
    return "\n".join(r.get("comments", "") for r in push.get("revisions", []))


def resolve(target, since_s, author, repo):
    """Resolve a target to (pushes, source-url, notes, lando).

    An empty push list means "could not resolve" -- never a guess. A
    landoCommitID in particular cannot be mapped to a push, so it yields the
    Lando status plus the candidate list, and leaves the choice to the reader.
    """
    notes, lando = [], None

    if re.fullmatch(r"\d{1,9}", target) and not re.fullmatch(r"[0-9a-f]{12,40}", target):
        lando = lando_status(target)
        notes.append(
            f"read {target} as a landoCommitID (for a Treeherder push id, pass "
            "--push-id). Lando's commit_id is internal and never matches a "
            "Treeherder revision, so no push can be derived from it: match your "
            "`mach try -m` message against the candidates below instead."
        )
        collision, _ = push_by_id(target, repo)
        if collision:
            notes.append(
                f"  (a push with id {target} also exists -- "
                f"{collision[0]['revision'][:12]} by {collision[0]['author']}, "
                f"{when(collision[0]['push_timestamp'])} -- it is unrelated)"
            )
        cands, url = pushes_in_window(since_s, author, repo=repo)
        for p in sorted(cands, key=lambda p: -p["push_timestamp"])[:8]:
            first = push_comments(p).strip().splitlines()
            notes.append(
                f"  candidate {p['revision'][:12]} {when(p['push_timestamp'])} "
                f"{(first[0] if first else '')[:60]}"
            )
        return [], url, notes, lando

    if re.fullmatch(r"[0-9a-f]{12,40}", target):
        pushes, url = push_by_revision(target, repo)
        if pushes:
            return pushes, url, notes, lando
        notes.append(f"no push with revision {target}; trying a subject search")

    pushes, url = pushes_in_window(since_s, author, repo=repo)
    needle = target.lower()
    hits = [p for p in pushes if needle in push_comments(p).lower()]
    if hits:
        notes.append(f"matched {len(hits)} push(es) on subject {target!r}")
        return hits, url, notes, lando
    notes.append(
        f"no push in the last window matches {target!r} "
        f"(searched {len(pushes)} push(es)"
        + (f" by {author}" if author else "")
        + "); widen --since or drop --author"
    )
    return [], url, notes, lando


# --- jobs -------------------------------------------------------------------


def jobs_for(push_id, repo="try"):
    """All jobs for a push. The API caps a page at 2000 rows, so page it."""
    out, offset = [], 0
    while True:
        url = (
            f"{TH}/api/project/{repo}/jobs/?push_id={push_id}"
            f"&count=2000&offset={offset}"
        )
        rows = fetch(url)["results"]
        # Rows are full objects here. Some Treeherder endpoints return compact
        # rows plus a job_property_names list; zip those if we ever see them.
        out += rows
        if len(rows) < 2000:
            return out, url
        offset += len(rows)


def is_failure(job):
    return job.get("result") in ("testfailed", "busted", "exception")


def never_scheduled(jobs):
    """Unscheduled jobs once nothing is pending or running, each with the failed
    task that blocks it. Taskcluster schedules a task only after every dependency
    succeeded, so such a job will never run and the push is as done as it gets.
    Returns None while anything can still move: in a push's first minutes every
    build waits on toolchain, fetch and docker-image tasks that Treeherder does
    not list, so an unscheduled job with no failed task behind it is pending."""
    states = collections.Counter(j["state"] for j in jobs)
    if states["running"] or states["pending"] or not states["unscheduled"]:
        return None
    by_id = {j["task_id"]: j for j in jobs if j.get("task_id")}
    tasks, statuses = {}, {}

    def task(tid):
        if tid not in tasks:
            try:
                t = fetch(f"{TC}/task/{tid}")
                tasks[tid] = (t["metadata"]["name"], t.get("dependencies", []))
            except Exception:
                tasks[tid] = (tid, [])
        return tasks[tid]

    def status(tid):
        if tid not in statuses:
            try:
                statuses[tid] = fetch(f"{TC}/task/{tid}/status")["status"]["state"]
            except Exception:
                statuses[tid] = "unknown"
        return statuses[tid]

    def blockers(tid, seen):
        found = []
        for dep in task(tid)[1]:
            if dep in seen:
                continue
            seen.add(dep)
            j = by_id.get(dep)
            if j is None:
                # Not a Treeherder job (e.g. a signing or fetch task): ask TC.
                st = status(dep)
                if st in ("failed", "exception"):
                    found.append(f"{task(dep)[0]} ({st})")
                elif st == "unscheduled":
                    found += blockers(dep, seen)
            elif is_failure(j):
                found.append(f"{j['job_type_name']} ({j['result']})")
            elif j["state"] == "unscheduled":
                found += blockers(dep, seen)
        return found

    stuck = [j for j in jobs if j["state"] == "unscheduled"]
    found = {}
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        futures = {ex.submit(blockers, j["task_id"], set()): j for j in stuck}
        for f in concurrent.futures.as_completed(futures):
            b = f.result()
            if not b:
                for other in futures:
                    other.cancel()
                return None
            found[futures[f]["task_id"]] = sorted(set(b))
    return [
        {"job": j["job_type_name"], "blocked_by": found[j["task_id"]]}
        for j in stuck
    ]


def bug_suggestions(job, repo="try"):
    url = f"{TH}/api/project/{repo}/jobs/{job['id']}/bug_suggestions/"
    try:
        return job, fetch(url), url
    except Exception as e:
        return job, [{"search": f"[could not read bug_suggestions: {e}]"}], url


TEST_PATH = re.compile(r"\.(js|html|xhtml|py|cpp|toml|ini|json|md|jsm|mjs|sjs)$")

# Lines mochitest emits for every test whether or not it is the real failure.
BORING = re.compile(
    r"^(finished in \d|took \d|profile uploaded|leaked|Shutdown - leaked|"
    r"Assertion count|Longest test|Buffered messages)", re.I
)


def rank_msg(m):
    """Lower sorts first: prefer the line that names the actual failure."""
    return (1 if BORING.search(m) else 0, -len(m))


def group_failures(jobs, repo="try", workers=6):
    """Group failure lines per test file, deduped across chunks and rebuilds.

    Returns (test_groups, infra_lines, urls). A test file that failed the same
    way in six --rebuild repeats becomes one entry counting six task runs.
    Counting is per task, not per job row: a job that Treeherder retried appears
    twice (result "retry", then the rerun), which would otherwise double-count.
    """
    failing = [j for j in jobs if is_failure(j)]
    groups = collections.defaultdict(
        lambda: {"tasks": set(), "jobs": set(), "types": set(), "platforms": set(), "msgs": set()}
    )
    infra = collections.defaultdict(set)
    urls = []
    if not failing:
        return groups, infra, urls

    with concurrent.futures.ThreadPoolExecutor(workers) as ex:
        for job, sugs, url in ex.map(lambda j: bug_suggestions(j, repo), failing):
            urls.append(url)
            for s in sugs:
                path = s.get("path_end") or ""
                search = s.get("search", "")
                if not path or not TEST_PATH.search(path):
                    infra[search.strip()[:110]].add(job["task_id"])
                    continue
                g = groups[path]
                g["tasks"].add(job["task_id"])
                g["jobs"].add(job["id"])
                g["types"].add(job["job_type_name"])
                g["platforms"].add(f"{job['platform']}/{job['platform_option']}")
                tail = search.split("|")[-1].strip()
                g["msgs"].add(tail[:110])
    return groups, infra, urls


def runs_of_types(jobs, types):
    """How many times these job types actually ran (tasks, not job rows)."""
    return len({j["task_id"] for j in jobs if j["job_type_name"] in types})


# --- did test X pass --------------------------------------------------------


def log_url(job):
    return (
        f"{TC}/task/{job['task_id']}/runs/{job.get('retry_id', 0)}"
        "/artifacts/public/logs/live_backing.log"
    )


def grep_log_for_test(job, name, max_lines=6):
    """Stream a job's live_backing.log and pull out that test's result lines."""
    url = log_url(job)
    try:
        blob = fetch(url, raw=True, timeout=180)
    except Exception as e:
        return url, None, [f"[log unavailable: {e}]"]
    counts = collections.Counter()
    lines = []
    for raw in blob.decode("utf-8", "replace").splitlines():
        if name not in raw:
            continue
        m = TEST_LINE.search(raw)
        if not m:
            continue
        kind = m.group(1)
        counts[kind] += 1
        if kind not in ("TEST-PASS", "TEST-START") and len(lines) < max_lines:
            lines.append(m.group(0)[:160])
    return url, counts, lines


def pick_test_jobs(jobs, groups, name, limit):
    """Jobs most likely to have run `name`: ones where it failed, else failures.

    One job per job type, so a --rebuild push does not spend the budget reading
    several repeats of the same chunk.
    """
    pool = [j for j in jobs if is_failure(j)]
    for path, g in groups.items():
        if name in path:
            pool = [j for j in jobs if j["id"] in g["jobs"]]
            break
    by_type = collections.defaultdict(list)
    for j in pool:
        by_type[j["job_type_name"]].append(j)
    ordered = sorted(by_type.values(), key=len, reverse=True)
    return [group[0] for group in ordered][:limit]


# --- report -----------------------------------------------------------------


def report(push, jobs, jobs_url, push_url, groups, infra, args, notes, lando, repo):
    out = []
    rev = push["revision"]
    th = f"{TH}/jobs?repo={repo}&revision={rev}"

    if lando:
        if "error" in lando:
            out.append(f"LANDO      {lando['error']}")
        else:
            out.append(
                f"LANDO      job {lando['id']} status={lando['status']} "
                f"commit_id={lando['commit_id'][:12]} (internal; not a revision)"
                f"  [{lando['url']}]"
            )
    for n in notes:
        out.append(f"NOTE       {n}")

    out.append(
        f"PUSH       {rev[:12]}  id={push['id']}  {when(push['push_timestamp'])} "
        f"({ago(push['push_timestamp'])})  {push['author']}"
    )
    subject = push_comments(push).strip().splitlines()
    out.append(f"           {subject[0][:96] if subject else '(no message)'}")
    out.append(f"           {th}")

    if not jobs:
        old = datetime.datetime.now().timestamp() - push["push_timestamp"] > 7 * 86400
        why = (
            "Treeherder no longer holds jobs this old, or none were ever ingested"
            if old
            else "the decision task has not reported yet"
        )
        out.append(
            f"STATE      no jobs: {why} "
            f"(the push itself exists, so the submission did materialize)  [{jobs_url}]"
        )
        return "\n".join(out)

    states = collections.Counter(j["state"] for j in jobs)
    results = collections.Counter(j["result"] for j in jobs if j["state"] == "completed")
    stranded = never_scheduled(jobs)
    pending = 0 if stranded else (
        states["running"] + states["pending"] + states["unscheduled"]
    )
    only_decision = {j["job_type_name"] for j in jobs} == {"Gecko Decision Task"}
    verdict = "COMPLETE" if not pending else f"IN PROGRESS ({pending} job(s) to go)"
    if stranded:
        verdict += f" ({len(stranded)} job(s) never scheduled: a dependency failed)"
    if only_decision:
        verdict += " -- only the decision task exists so far"
    out.append(f"STATE      {verdict}  ({len(jobs)} jobs)  [{jobs_url}]")
    for s in stranded or []:
        out.append(f"           never scheduled: {s['job']}  <- {', '.join(s['blocked_by'])}")
    out.append(
        "           state:  "
        + "  ".join(f"{k}={v}" for k, v in sorted(states.items()))
    )
    out.append(
        "           result: "
        + "  ".join(f"{k}={v}" for k, v in sorted(results.items()))
    )
    plats = collections.Counter(
        f"{j['platform']}/{j['platform_option']}"
        for j in jobs
        if j["platform"] not in ("gecko-decision",)
    )
    items = [f"{k} x{v}" for k, v in sorted(plats.items())]
    out.append(f"PLATFORMS  {len(plats)}: " + ", ".join(items[:4]))
    for i in range(4, len(items), 4):
        out.append("           " + ", ".join(items[i:i + 4]))

    if not groups and not infra:
        out.append("FAILED     nothing" if not pending else "FAILED     nothing so far")
    else:
        ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]["tasks"]))
        nfail = len({j["task_id"] for j in jobs if is_failure(j)})
        out.append(f"FAILED     {len(ranked)} test file(s) in {nfail} failing task(s)")
        for path, g in ranked:
            denom = runs_of_types(jobs, g["types"])
            plat = sorted(g["platforms"])
            msg = sorted(g["msgs"], key=rank_msg)[0]
            out.append(
                f"  x{len(g['tasks'])}/{denom}  {path}"
                f"  [{', '.join(plat[:2])}{'+' + str(len(plat) - 2) if len(plat) > 2 else ''}]"
            )
            out.append(f"           {msg}")
        if infra and args.verbose:
            for line, ids in sorted(infra.items(), key=lambda kv: -len(kv[1]))[:3]:
                out.append(f"  x{len(ids)}      (infra) {line}")

    if args.test:
        out.append(f"TEST       {args.test}")
        hit = [p for p in groups if args.test in p]
        if hit:
            for p in hit:
                out.append(
                    f"           FAILED in {len(groups[p]['jobs'])} job(s): {p}"
                )
        picked = pick_test_jobs(jobs, groups, args.test, args.max_logs)
        if not picked:
            out.append("           no job log to read (no failing job on this push)")
        for j in picked:
            url, counts, lines = grep_log_for_test(j, args.test)
            label = f"{j['job_type_name']} ({j['task_id'][:8]})"
            if counts is None:
                out.append(f"           {label}: {lines[0]}")
                continue
            if not counts:
                out.append(
                    f"           {label}: test not present in this log "
                    "(did not run in this chunk)"
                )
            else:
                summary = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                verdict = "PASS" if not any(
                    k.startswith("TEST-UNEXPECTED") for k in counts
                ) else "FAIL"
                out.append(f"           {label}: {verdict}  {summary}")
                for line in lines:
                    out.append(f"             {line}")
            out.append(f"             log: {url}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(
        description="Report whether a Firefox try push exists, finished, and what failed."
    )
    ap.add_argument(
        "target",
        nargs="?",
        default="",
        help="subject substring, revision (12 or 40 hex), or landoCommitID",
    )
    ap.add_argument("--since", default="4h", help="search window for subject/lando lookups (default 4h)")
    ap.add_argument("--test", help="report this test's result lines from a job log")
    ap.add_argument("--max-logs", type=int, default=2, help="job logs to read for --test (default 2)")
    ap.add_argument("--author", default=None, help="filter pushes by author (default: your Lando email)")
    ap.add_argument("--all-authors", action="store_true", help="do not filter by author")
    ap.add_argument("--push-id", help="resolve this Treeherder push id (numbers are otherwise read as landoCommitIDs)")
    ap.add_argument("--repo", default="try", help="Treeherder project (default try)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--verbose", action="store_true", help="also list infra/taskcluster failure lines")
    args = ap.parse_args()

    since_s = parse_since(args.since)
    author = None if args.all_authors else (args.author or default_author())

    if args.push_id:
        pushes, push_url = push_by_id(args.push_id, args.repo)
        notes, lando = [f"resolved push id {args.push_id}"], None
        if not pushes:
            notes.append(f"no push with id {args.push_id} in {args.repo}")
    else:
        pushes, push_url, notes, lando = resolve(args.target, since_s, author, args.repo)

    if not pushes:
        if args.json:
            print(json.dumps({"pushes": [], "notes": notes, "lando": lando, "source": push_url}, indent=1))
        else:
            for n in notes:
                print(f"NOTE       {n}")
            if lando:
                print(f"LANDO      {json.dumps(lando)}")
            print(f"PUSH       NONE FOUND  [{push_url}]")
            print("           Lando accepting a submission does not mean a push was ever created.")
        sys.exit(1)

    if len(pushes) > 1:
        pushes = sorted(pushes, key=lambda p: -p["push_timestamp"])
        notes.append(
            f"{len(pushes)} candidate pushes; reporting the newest. Others: "
            + ", ".join(f"{p['revision'][:12]}@{when(p['push_timestamp'])}" for p in pushes[1:5])
        )

    push = pushes[0]
    jobs, jobs_url = jobs_for(push["id"], args.repo)
    groups, infra, _ = group_failures(jobs, args.repo)

    if args.json:
        print(json.dumps(
            {
                "notes": notes,
                "lando": lando,
                "push": {
                    "revision": push["revision"],
                    "id": push["id"],
                    "author": push["author"],
                    "push_timestamp": push["push_timestamp"],
                    "treeherder": f"{TH}/jobs?repo={args.repo}&revision={push['revision']}",
                    "source": push_url,
                },
                "jobs": {
                    "total": len(jobs),
                    "never_scheduled": never_scheduled(jobs),
                    "by_state": collections.Counter(j["state"] for j in jobs),
                    "by_result": collections.Counter(
                        j["result"] for j in jobs if j["state"] == "completed"
                    ),
                    "platforms": collections.Counter(
                        f"{j['platform']}/{j['platform_option']}" for j in jobs
                    ),
                    "source": jobs_url,
                },
                "failures": [
                    {
                        "test": p,
                        "failed_tasks": len(g["tasks"]),
                        "total_runs": runs_of_types(jobs, g["types"]),
                        "platforms": sorted(g["platforms"]),
                        "job_types": sorted(g["types"]),
                        "messages": sorted(g["msgs"])[:3],
                    }
                    for p, g in sorted(groups.items(), key=lambda kv: -len(kv[1]["jobs"]))
                ],
                "infra": {k: len(v) for k, v in infra.items()},
            },
            indent=1,
            default=str,
        ))
        return

    print(report(push, jobs, jobs_url, push_url, groups, infra, args, notes, lando, args.repo))


if __name__ == "__main__":
    main()
