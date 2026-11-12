#!/usr/bin/env python3
"""Mozilla Phabricator Conduit helper: read revisions, and edit them dry-run-first.

Replaces hand-rolled curl + python round trips for the Conduit work the
submit-single / submit-stack skills need (stack edges moz-phab leaves unset,
update notes, WIP flips, reviewer changes).

Reads are immediate. **Every write is a dry run by default**: it prints the exact
Conduit method and the fully-expanded transaction payload it would POST, and
sends nothing until you pass --yes. After a real write it re-reads the object and
prints the resulting state, because a Conduit edit can return OK and silently
no-op (a mis-shaped transaction value is accepted and ignored).

  phab.py show D318401 [D318450 ...]        # status, title, bug, reviewers, parents
  phab.py stack D318450                     # dependency graph, resolved to D-numbers
  phab.py link --child D318450 --parent D318401
  phab.py unlink --child D318450 --parent D318401
  phab.py comment D318450 -m "..."
  phab.py wip D318450                       # -> Changes Planned
  phab.py request-review D318450
  phab.py abandon D318450 [-m "superseded by D..."]
  phab.py reviewers D318450 --add someuser --remove other --add '#urlbar-reviewers'
  phab.py title D318450 -m "..."
  phab.py summary D318450 -m "..."

Notes on the API, so they aren't re-derived:
  - Token comes from ~/.arcrc (hosts.<url>.token, first host); that host key
    already ends in /api/.
  - Parent edges live in the revision's `stackGraph` field (revision PHID -> list
    of PARENT PHIDs). There is no `parentRevisionIDs` field -- reading a guessed
    name returns None whether or not an edge exists, which reads as "not
    threaded" and invites a redundant parents.add.
  - Reviewers are NOT in `fields`; they need attachments[reviewers]=1.
  - reviewers.set clobbers group reviewers -- only .add / .remove are used here.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ARCRC = os.path.expanduser("~/.arcrc")
UA = "phab.py/1.0"
RETRY_CODES = (429, 502, 503, 504)
RETRY_DELAYS = (5, 15, 45)
# Methods that change state, so a 5xx (which may arrive after the write landed)
# is never retried. A 429 is retried: the edge refused it before Phabricator saw it.
WRITE_METHODS = {"differential.createinline", "differential.createcomment"}
# Fastly throttles by TLS fingerprint, not by token: urllib (no ALPN) and curl
# land in separate buckets. Once urllib draws a 429, stay on curl for the rest
# of the process instead of feeding the throttled bucket on every call.
use_curl = False

# Transaction types whose value is a list; everything else is a scalar.
LIST_VALUED = {"parents.add", "parents.remove", "reviewers.add", "reviewers.remove"}


def api_base():
    hosts = json.load(open(ARCRC))["hosts"]
    url = next(iter(hosts))
    return url if url.endswith("/") else url + "/"


def token():
    hosts = json.load(open(ARCRC))["hosts"]
    return next(iter(hosts.values()))["token"]


def flatten(params):
    """Expand nested params into Conduit's indexed form (transactions[0][type], ...).

    A flattener that stringifies a list-of-dicts instead makes the edit a silent
    no-op, so the payload printed by a dry run is produced by this same function.
    """
    flat = {}

    def add(prefix, val):
        if isinstance(val, dict):
            for k, v in val.items():
                add(f"{prefix}[{k}]", v)
        elif isinstance(val, list):
            for i, v in enumerate(val):
                add(f"{prefix}[{i}]", v)
        elif isinstance(val, bool):
            flat[prefix] = "true" if val else "false"
        else:
            flat[prefix] = val

    for k, v in params.items():
        add(k, v)
    return flat


def curl_post(url, data):
    """POST via the curl binary, returning the decoded body.

    A 429 that persists for urllib has been observed to clear immediately for
    curl against the same endpoint with the same token, so curl is worth one
    attempt before giving up on a retryable status.
    """
    if not shutil.which("curl"):
        return None
    proc = subprocess.run(
        ["curl", "-sS", "--compressed", "--max-time", "90", "-A", UA,
         "-w", "\n%{http_code}", "--data-binary", "@-", url],
        input=data,
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    body, _, status = proc.stdout.decode("utf-8", "replace").rpartition("\n")
    return body if status.strip() == "200" else None


def is_write(method):
    return method.endswith(".edit") or method in WRITE_METHODS


def conduit(method, params):
    global use_curl
    flat = flatten(params)
    flat["api.token"] = token()
    data = urllib.parse.urlencode(flat).encode()
    url = api_base() + method
    for attempt in range(len(RETRY_DELAYS) + 1):
        if use_curl:
            body = curl_post(url, data)
            if body is None:
                sys.exit(f"Conduit {method}: the curl fallback failed -- rate "
                         "limited, try again later.")
            resp = json.loads(body)
            break
        req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=90))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                print(f"note: {method} got HTTP 429; using curl from here on",
                      file=sys.stderr)
                use_curl = True
                continue
            if exc.code not in RETRY_CODES:
                sys.exit(f"Conduit {method} failed: HTTP {exc.code}")
            if is_write(method):
                sys.exit(f"Conduit {method}: HTTP {exc.code}. The write may have "
                         "landed; check the revision before retrying.")
            if attempt == len(RETRY_DELAYS):
                sys.exit(
                    f"Conduit {method}: HTTP {exc.code} after {attempt + 1} "
                    "attempts -- try again later."
                )
            delay = RETRY_DELAYS[attempt]
            print(f"note: Conduit {method} got HTTP {exc.code}; retrying in {delay}s",
                  file=sys.stderr)
            time.sleep(delay)
    if resp.get("error_info"):
        sys.exit(f"Conduit error ({method}): {resp['error_info']}")
    return resp["result"]


def parse_rev(s):
    m = re.search(r"D?(\d+)/?$", str(s))
    if not m:
        sys.exit(f"Not a revision identifier: {s}")
    return int(m.group(1))


def search(ids=None, phids=None, reviewers=False):
    constraints = {}
    if ids:
        constraints["ids"] = list(ids)
    if phids:
        constraints["phids"] = list(phids)
    params = {"constraints": constraints}
    if reviewers:
        params["attachments"] = {"reviewers": True}
    return conduit("differential.revision.search", params)["data"]


def d_names(phids):
    """Map revision PHIDs -> 'D<id>' so output is human-usable."""
    phids = [p for p in dict.fromkeys(phids) if p]
    if not phids:
        return {}
    return {r["phid"]: f"D{r['id']}" for r in search(phids=phids)}


def phid_names(phids):
    """Resolve arbitrary PHIDs (users, projects) to their monogram/name."""
    phids = [p for p in dict.fromkeys(phids) if p]
    if not phids:
        return {}
    res = conduit("phid.query", {"phids": phids})
    out = {}
    for p, v in res.items():
        name = v.get("name") or v.get("fullName") or p
        out[p] = f"#{name}" if p.startswith("PHID-PROJ") else name
    return out


def rev_phid(rev):
    data = search(ids=[rev])
    if not data:
        sys.exit(f"No such revision: D{rev}")
    return data[0]["phid"]


def parents_of(revision):
    """Parent PHIDs of a revision dict, read from stackGraph."""
    graph = revision["fields"].get("stackGraph") or {}
    return list(graph.get(revision["phid"]) or [])


def print_revisions(revs):
    names = d_names([p for r in revs for p in parents_of(r)])
    reviewer_phids = []
    for r in revs:
        for rv in (r.get("attachments", {}).get("reviewers", {}) or {}).get(
            "reviewers", []
        ):
            reviewer_phids.append(rv["reviewerPHID"])
    who = phid_names(reviewer_phids)
    for r in revs:
        f = r["fields"]
        status = f.get("status") or {}
        print(f"D{r['id']}  {f.get('title')}")
        print(f"  status:    {status.get('name')} ({status.get('value')})")
        print(f"  bug:       {f.get('bugzilla.bug-id') or '-'}")
        print(f"  diffID:    {f.get('diffID') or '-'}")
        rvs = (r.get("attachments", {}).get("reviewers", {}) or {}).get("reviewers", [])
        if rvs:
            desc = ", ".join(
                "{}{} [{}]".format(
                    who.get(rv["reviewerPHID"], rv["reviewerPHID"]),
                    "!" if rv.get("isBlocking") else "",
                    rv.get("status"),
                )
                for rv in rvs
            )
        else:
            desc = "-"
        print(f"  reviewers: {desc}")
        parents = parents_of(r)
        print(
            "  parents:   "
            + (", ".join(names.get(p, p) for p in parents) if parents else "-")
        )
        print(f"  uri:       {f.get('uri') or ''}")
    print("(differential.revision.search)")


def cmd_show(args):
    ids = [parse_rev(s) for s in args.revisions]
    revs = search(ids=ids, reviewers=True)
    found = {r["id"] for r in revs}
    for missing in [i for i in ids if i not in found]:
        print(f"D{missing}: not found")
    print_revisions(sorted(revs, key=lambda r: ids.index(r["id"])))


def cmd_stack(args):
    rev = parse_rev(args.revision)
    data = search(ids=[rev])
    if not data:
        sys.exit(f"No such revision: D{rev}")
    graph = data[0]["fields"].get("stackGraph") or {}
    if not graph:
        print(f"D{rev}: no stackGraph (standalone revision)")
        return
    revs = search(phids=list(graph))
    info = {r["phid"]: r for r in revs}
    name = {p: f"D{r['id']}" for p, r in info.items()}

    def label(p):
        r = info.get(p)
        if not r:
            return p
        f = r["fields"]
        st = (f.get("status") or {}).get("name")
        return f"D{r['id']} [{st}] {f.get('title')}"

    children = {p: [] for p in graph}
    for child, parents in graph.items():
        for p in parents:
            children.setdefault(p, []).append(child)
    roots = [p for p, parents in graph.items() if not parents]
    print(f"stack of D{rev} ({len(graph)} revisions), bottom -> top:")
    seen = set()

    def walk(p, depth):
        if p in seen:
            print("  " * (depth + 1) + f"{name.get(p, p)} (already shown)")
            return
        seen.add(p)
        marker = " <-- queried" if info.get(p, {}).get("id") == rev else ""
        print("  " * (depth + 1) + label(p) + marker)
        for c in sorted(children.get(p, []), key=lambda x: info.get(x, {}).get("id", 0)):
            walk(c, depth + 1)

    for r in roots or list(graph):
        walk(r, 0)
    print("\nedges (child -> parents):")
    for child in sorted(graph, key=lambda p: info.get(p, {}).get("id", 0)):
        parents = graph[child] or []
        print(
            f"  {name.get(child, child)} -> "
            + (", ".join(name.get(p, p) for p in parents) if parents else "NONE")
        )
    print("(differential.revision.search, field stackGraph)")


def submit_edit(rev, transactions, args, what):
    """Dry-run-by-default differential.revision.edit."""
    for t in transactions:
        if t["type"] in LIST_VALUED and not isinstance(t["value"], list):
            t["value"] = [t["value"]]
    params = {"objectIdentifier": f"D{rev}", "transactions": transactions}
    print(f"{what}")
    print("method: differential.revision.edit")
    for k, v in flatten(params).items():
        print(f"  {k}={v}")
    if not args.yes:
        print("\nDRY RUN -- nothing sent. Re-run with --yes to send.")
        return
    print("\nsending...")
    conduit("differential.revision.edit", params)
    print("\nresulting state:")
    print_revisions(search(ids=[rev], reviewers=True))


def cmd_link(args):
    child, parent = parse_rev(args.child), parse_rev(args.parent)
    data = search(ids=[child])
    if not data:
        sys.exit(f"No such revision: D{child}")
    parent_phid = rev_phid(parent)
    if parent_phid in parents_of(data[0]):
        print(f"D{child} already has parent D{parent} (stackGraph) -- nothing to do.")
        return
    submit_edit(
        child,
        [{"type": "parents.add", "value": [parent_phid]}],
        args,
        f"add parent D{parent} ({parent_phid}) to D{child}",
    )


def cmd_unlink(args):
    child, parent = parse_rev(args.child), parse_rev(args.parent)
    data = search(ids=[child])
    if not data:
        sys.exit(f"No such revision: D{child}")
    parent_phid = rev_phid(parent)
    if parent_phid not in parents_of(data[0]):
        print(f"D{child} has no parent D{parent} (stackGraph) -- nothing to do.")
        return
    submit_edit(
        child,
        [{"type": "parents.remove", "value": [parent_phid]}],
        args,
        f"remove parent D{parent} ({parent_phid}) from D{child}",
    )


def cmd_comment(args):
    rev = parse_rev(args.revision)
    submit_edit(
        rev, [{"type": "comment", "value": args.message}], args, f"comment on D{rev}"
    )


def cmd_status(args):
    rev = parse_rev(args.revision)
    txns = [{"type": args._type, "value": True}]
    if getattr(args, "message", None):
        txns.insert(0, {"type": "comment", "value": args.message})
    submit_edit(rev, txns, args, f"{args._type} on D{rev}")


def cmd_field(args):
    rev = parse_rev(args.revision)
    submit_edit(
        rev,
        [{"type": args._type, "value": args.message}],
        args,
        f"set {args._type} of D{rev}",
    )


def resolve_reviewer(name):
    """'#group' -> project PHID, otherwise a username -> user PHID.

    A trailing '!' (the commit message's blocking marker) is stripped by the caller.
    """
    if name.startswith("#"):
        data = conduit("project.search", {"constraints": {"slugs": [name[1:]]}})["data"]
        if not data:
            sys.exit(f"No such project/group: {name}")
        return data[0]["phid"]
    data = conduit("user.search", {"constraints": {"usernames": [name]}})["data"]
    if not data:
        sys.exit(f"No such user: {name}")
    return data[0]["phid"]


def cmd_reviewers(args):
    rev = parse_rev(args.revision)
    if not args.add and not args.remove:
        sys.exit("Pass --add and/or --remove (reviewers.set clobbers group reviewers).")
    txns = []
    for name in args.add or []:
        blocking = name.endswith("!")
        phid = resolve_reviewer(name.rstrip("!"))
        txns.append({"type": "reviewers.add",
                     "value": [f"blocking({phid})" if blocking else phid]})
    for name in args.remove or []:
        txns.append({"type": "reviewers.remove", "value": [resolve_reviewer(name.rstrip("!"))]})
    added = " ".join(args.add or []) or "-"
    removed = " ".join(args.remove or []) or "-"
    submit_edit(rev, txns, args, f"D{rev} reviewers add: {added}  remove: {removed}")


def main():
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Writes are dry-run by default; pass --yes to actually send.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def writer(name, help):
        sp = sub.add_parser(name, help=help)
        sp.add_argument("--yes", action="store_true", help="actually send the edit")
        return sp

    s = sub.add_parser("show", help="status, title, bug, reviewers, parents")
    s.add_argument("revisions", nargs="+")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("stack", help="dependency graph resolved to D-numbers")
    s.add_argument("revision")
    s.set_defaults(func=cmd_stack)

    s = writer("link", "add a stack dependency edge (parents.add)")
    s.add_argument("--child", required=True)
    s.add_argument("--parent", required=True)
    s.set_defaults(func=cmd_link)

    s = writer("unlink", "drop a stack dependency edge (parents.remove)")
    s.add_argument("--child", required=True)
    s.add_argument("--parent", required=True)
    s.set_defaults(func=cmd_unlink)

    s = writer("comment", "post a comment")
    s.add_argument("revision")
    s.add_argument("-m", "--message", required=True)
    s.set_defaults(func=cmd_comment)

    s = writer("wip", "set Changes Planned (plan-changes)")
    s.add_argument("revision")
    s.add_argument("-m", "--message")
    s.set_defaults(func=cmd_status, _type="plan-changes")

    s = writer("request-review", "request review")
    s.add_argument("revision")
    s.add_argument("-m", "--message")
    s.set_defaults(func=cmd_status, _type="request-review")

    s = writer("abandon", "abandon the revision")
    s.add_argument("revision")
    s.add_argument("-m", "--message", help="comment posted with the abandon")
    s.set_defaults(func=cmd_status, _type="abandon")

    s = writer("title", "set the revision title")
    s.add_argument("revision")
    s.add_argument("-m", "--message", required=True)
    s.set_defaults(func=cmd_field, _type="title")

    s = writer("summary", "set the revision summary")
    s.add_argument("revision")
    s.add_argument("-m", "--message", required=True)
    s.set_defaults(func=cmd_field, _type="summary")

    s = writer("reviewers", "add/remove reviewers (never .set)")
    s.add_argument("revision")
    s.add_argument("--add", action="append", metavar="USER[!]|#group[!]",
                   help="'!' suffix makes the review blocking, as in a commit message")
    s.add_argument("--remove", action="append", metavar="USER|#group")
    s.set_defaults(func=cmd_reviewers)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
