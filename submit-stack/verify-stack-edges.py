#!/usr/bin/env python3
"""Verify a Phabricator stack's Depends-On edges match the local commit order.

After a split / reorder / mid-stack insert, moz-phab wires deps for *new*
revisions but leaves *existing* revisions' parent edges stale. The classic
misses this catches:

  - the commit *above* an inserted range still depends on its pre-insertion
    parent (it forks off the side of the stack instead of sitting atop the new
    top) -- the "ceiling" edge;
  - a stale interior edge after a reorder.

It walks the local commits bottom -> top, reads each one's `Differential
Revision`, and reports whether every revision's parent on the server is the
revision directly below it in commit order. Run it as the §4 verify step after
ANY stack rewrite -- do not hand-verify a subset, which is exactly how the
ceiling edge gets missed.

It's a safety net, not a linearity mandate: it surfaces every edge that DIFFERS
from the plain commit order so an *accidental* dangler can't hide. Most flagged
edges are bugs to fix, but a *deliberate* non-linear graph (a diamond, a fork,
multiple parents) will show as "DIFFERS" too -- that's expected; judge each one
rather than reflexively linearizing. Exits non-zero when any edge differs, so
you notice; that is a signal to review, not proof of breakage.

The floor's downward edge is a judgment call (it should point at the last
*landed* revision below the open stack, which isn't in the local range), so its
current parent is reported for you to confirm, not asserted.

Usage: verify-stack-edges.py [<git-range>]      # default: origin/autoland..HEAD
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

API = "https://phabricator.services.mozilla.com/api/"


def token():
    hosts = json.load(open(os.path.expanduser("~/.arcrc")))["hosts"]
    return next(iter(hosts.values()))["token"]


def conduit(method, params):
    flat = {"api.token": token()}

    def add(prefix, val):
        if isinstance(val, dict):
            for k, v in val.items():
                add(f"{prefix}[{k}]", v)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                add(f"{prefix}[{i}]", v)
        else:
            flat[prefix] = val

    for k, v in params.items():
        add(k, v)
    data = urllib.parse.urlencode(flat).encode()
    resp = json.load(urllib.request.urlopen(API + method, data))
    if resp.get("error_info"):
        sys.exit("Conduit error: " + resp["error_info"])
    return resp["result"]


def check_bases(stack, revs):
    """Each diff records the commit it was generated on top of. Amending a commit
    that another revision sits on leaves the child's base pointing at a commit that
    no longer carries the parent's content, and the review tool shows nothing.
    Whether that blocks landing is unverified -- report the drift, don't prescribe.
    Compare every base against the commit below it."""
    # diffID comes back as a string; diff.search keys its results by int id.
    diff_of = {r["id"]: int(r["fields"]["diffID"]) for r in revs}
    diffs = conduit(
        "differential.diff.search", {"constraints": {"ids": list(diff_of.values())}}
    )["data"]
    base_of = {}
    for d in diffs:
        for ref in d["fields"].get("refs", []):
            if ref["type"] == "base":
                base_of[d["id"]] = ref["identifier"]

    def same(a, b):
        n = min(len(a), len(b))
        return n >= 7 and a[:n] == b[:n]

    print("\nDiff bases (the commit each diff was generated on):")
    bad = []
    for i, (sha, dnum) in enumerate(stack):
        base = base_of.get(diff_of.get(dnum))
        if not base:
            print(f"D{dnum} base = UNKNOWN -- could not read it, check by hand")
            continue
        if i == 0:
            try:
                upstream = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
                    text=True, stderr=subprocess.DEVNULL).strip()
            except subprocess.CalledProcessError:
                upstream = "origin/autoland"
            ok = subprocess.call(
                ["git", "merge-base", "--is-ancestor", base, upstream],
                stderr=subprocess.DEVNULL) == 0
            print(f"D{dnum} (floor) base = {base}  "
                  + (f"ancestor of {upstream}" if ok
                     else f"NOT an ancestor of {upstream} -- will not apply"))
            if not ok:
                bad.append(dnum)
        else:
            want = stack[i - 1][0]
            ok = same(base, want)
            print(f"D{dnum} base = {base} (commit below: {want[:12]})  "
                  + ("matches" if ok else "DRIFTED from the commit below"))
            if not ok:
                bad.append(dnum)

    if bad:
        print("\nDrifted bases: " + ", ".join(f"D{d}" for d in bad)
              + ".\nThis is an inconsistency, not a known blocker -- Lando may "
                "rebase a diff whose base is a real ancestor. If you do correct "
                "one, re-push from a branch where its commit is the tip so "
                "--single does not rewrite the commits above it.")
    return bad


def main():
    rng = sys.argv[1] if len(sys.argv) > 1 else "origin/autoland..HEAD"
    shas = subprocess.check_output(
        ["git", "log", "--reverse", "--format=%H", rng], text=True
    ).split()

    stack = []  # (sha, revision-id) bottom -> top, only commits with a revision
    for sha in shas:
        body = subprocess.check_output(
            ["git", "log", "-1", "--format=%B", sha], text=True
        )
        m = re.search(r"Differential Revision:\s*\S*/D(\d+)", body)
        if m:
            stack.append((sha, int(m.group(1))))

    if len(stack) < 1:
        print(f"No revisions with Differential trailers in {rng}.")
        return

    ids = [d for _, d in stack]
    revs = conduit(
        "differential.revision.search", {"constraints": {"ids": ids}}
    )["data"]
    phid = {r["id"]: r["phid"] for r in revs}
    rid = {r["phid"]: r["id"] for r in revs}

    # One edge.search for every revision's parent, not one call per revision --
    # O(N) round-trips times out on a real-sized stack.
    edges = conduit(
        "edge.search",
        {"types": ["revision.parent"], "sourcePHIDs": [p for p in phid.values()]},
    )["data"]
    parent_of = {}
    for e in edges:
        parent_of.setdefault(e["sourcePHID"], e["destinationPHID"])

    def parent_phid(p):
        return parent_of.get(p)

    def name(p):
        if not p:
            return "NONE"
        if p not in rid:  # e.g. the floor's landed predecessor, outside the range
            found = conduit(
                "differential.revision.search", {"constraints": {"phids": [p]}}
            )["data"]
            rid[p] = found[0]["id"] if found else None
        return f"D{rid[p]}" if rid.get(p) else p

    fd = stack[0][1]
    fp = parent_phid(phid[fd])
    print(f"D{fd} (floor) parent = {name(fp)}  -- confirm this is the intended "
          "landed predecessor")

    diverged = []
    for (_, below), (_, above) in zip(stack, stack[1:]):
        actual = parent_phid(phid[above])
        matches = actual == phid[below]
        if not matches:
            diverged.append(above)
        print(f"D{above} parent = {name(actual)} (commit order: D{below})  "
              f"{'matches' if matches else 'DIFFERS -- intended?'}")

    if diverged:
        print("\nDiffers from commit order: "
              + ", ".join(f"D{d}" for d in diverged) + "."
              "\nFix accidental danglers -- e.g. a ceiling left on its "
              "pre-insertion parent after a mid-stack insert -- with "
              "differential.revision.edit parents.set (§3). Leave any edges "
              "that are deliberately non-linear (a diamond, a fork) as-is.")
    else:
        print("\nEvery edge matches commit order.")

    stale = check_bases(stack, revs)

    if diverged or stale:
        sys.exit(1)


if __name__ == "__main__":
    main()
