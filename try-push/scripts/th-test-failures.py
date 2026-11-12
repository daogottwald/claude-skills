#!/usr/bin/env python3
"""Sweep recent Treeherder pushes for failures naming given tests, or print
the result of named jobs on those pushes.

  th-test-failures.py REPO [-n COUNT] [--job-filter SUBSTR] NAME...
  th-test-failures.py REPO [-n COUNT] --jobs JOB_TYPE_NAME...
"""
import argparse, datetime, json, time, urllib.error, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))

def jobs(th, push_id, extra=""):
    out, offset = [], 0
    while True:
        page = get(th + f"jobs/?push_id={push_id}&count=2000&offset={offset}{extra}")["results"]
        out += page
        if len(page) < 2000:
            return out
        offset += 2000

ap = argparse.ArgumentParser()
ap.add_argument("repo")
ap.add_argument("-n", "--count", type=int, default=4)
ap.add_argument("--push-id", type=int, help="sweep this push only")
ap.add_argument("--job-filter", default="", help="substring the job_type_name must contain")
ap.add_argument("--jobs", action="store_true", help="NAMEs are job_type_names; print their results")
ap.add_argument("names", nargs="+")
a = ap.parse_args()
th = f"https://treeherder.mozilla.org/api/project/{a.repo}/"
pushes = get(th + (f"push/?id={a.push_id}" if a.push_id else f"push/?count={a.count}"))["results"]
for p in pushes:
    when = datetime.datetime.fromtimestamp(p["push_timestamp"]).strftime("%m-%d %H:%M")
    subj = p["revisions"][0]["comments"].split("\n")[0][:60] if p.get("revisions") else ""
    if a.jobs:
        all_jobs = jobs(th, p["id"])
        print(f"{p['revision'][:12]} {when} jobs={len(all_jobs)} {subj}")
        for j in all_jobs:
            if j["job_type_name"] in a.names:
                print(f"    {j['result']:10} {j['state']:10} {j['job_type_name']}")
        continue
    failed = [j for j in jobs(th, p["id"], "&result=testfailed")
            if a.job_filter in j["job_type_name"]]
    hits = set()
    for j in failed:
        for s in get(th + f"jobs/{j['id']}/bug_suggestions/"):
            line = s.get("search", "")
            if any(n in line for n in a.names):
                hits.add(f"{j['job_type_name']} {line[:100]}")
    print(f"{p['revision'][:12]} {when} failed-jobs={len(failed)} {subj}")
    for h in sorted(hits):
        print("   ", h)
