#!/usr/bin/env python3
"""Fetch, diff and replace a bugzilla.mozilla.org comment, printing only a verdict.

  bz-comment.py get <bug> [--count N] [-o FILE]
  bz-comment.py diff <bug> <file> [--count N]
  bz-comment.py put <bug> <file> [--count N]

<bug> takes a number or an alias. --count is the 0-based comment number shown in
the UI and defaults to 0, the description. `put` reads the stored text back and
compares it to what it sent, exiting non-zero on any mismatch; neither it nor
`diff` prints the comment body, so a verification costs one line of output.
"""

import argparse, configparser, difflib, json, os, sys, urllib.error, urllib.request

BASE = "https://bugzilla.mozilla.org/rest"
UA = {"User-Agent": "bz-comment"}


def key():
    c = configparser.ConfigParser()
    c.read(os.path.expanduser("~/.config/python-bugzilla/bugzillarc"))
    keys = [c[s]["api_key"] for s in c.sections() if "api_key" in c[s]]
    if not keys:
        sys.exit("no api_key in ~/.config/python-bugzilla/bugzillarc")
    return keys[0]


def req(url, data=None, method=None, auth=False):
    h = dict(UA)
    if data is not None:
        h["Content-Type"] = "application/json"
    if auth:
        h["X-BUGZILLA-API-KEY"] = key()
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        sys.exit(f"HTTP {e.code} from {method or 'GET'} {url}\n{body}")


def comment(bug, count):
    """Return (id, text) for a comment, resolving an alias and the count->id map."""
    bugs = req(f"{BASE}/bug/{bug}/comment")["bugs"]
    cs = next(iter(bugs.values()))["comments"]
    for c in cs:
        if c["count"] == count:
            return c["id"], c["text"]
    sys.exit(f"bug {bug} has no comment {count} ({len(cs)} comments)")


def lines(t):
    return [l.rstrip() for l in t.replace("\r\n", "\n").rstrip().split("\n")]


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("get", "diff", "put"):
        s = sub.add_parser(name)
        s.add_argument("bug")
        s.add_argument("--count", type=int, default=0)
        if name == "get":
            s.add_argument("-o", "--output")
        else:
            s.add_argument("file")
    a = p.parse_args()

    cid, current = comment(a.bug, a.count)

    if a.cmd == "get":
        if a.output:
            open(a.output, "w").write(current.rstrip() + "\n")
            print(f"bug {a.bug} comment {a.count} (id {cid}) -> {a.output} "
                  f"({len(lines(current))} lines, {len(current)} bytes)")
        else:
            sys.stdout.write(current)
        return

    new = open(a.file).read().rstrip() + "\n"
    if lines(new) == lines(current):
        print(f"bug {a.bug} comment {a.count}: IDENTICAL, nothing to send")
        return

    d = list(difflib.unified_diff(lines(current), lines(new),
                                  f"bug {a.bug} comment {a.count}", a.file,
                                  lineterm="", n=2))
    if a.cmd == "diff":
        print("\n".join(d))
        return

    req(f"{BASE}/editcomments/comment/{cid}",
        data=json.dumps({"new_comment": new}).encode(), method="PUT", auth=True)

    _, stored = comment(a.bug, a.count)
    if lines(stored) != lines(new):
        drift = sum(1 for l in difflib.unified_diff(lines(new), lines(stored), lineterm="")
                    if l[:1] in "+-" and l[:3] not in ("+++", "---"))
        sys.exit(f"bug {a.bug} comment {a.count}: MISMATCH after write, "
                 f"{drift} lines differ from what was sent")
    changed = sum(1 for l in d if l[:1] in "+-" and l[:3] not in ("+++", "---"))
    print(f"bug {a.bug} comment {a.count} (id {cid}): VERIFIED, "
          f"{changed} lines changed, {len(lines(stored))} lines stored")


if __name__ == "__main__":
    main()
