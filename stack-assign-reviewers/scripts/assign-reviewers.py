#!/usr/bin/env python3
"""Rewrite the `r?` line of the commit at HEAD from a subject-keyed mapping.

  git rebase <base> --exec "assign-reviewers.py <mapping.json>"

<mapping.json> is a list of [subject-substring, reviewers] pairs, e.g.

  [["recent searches", "r?alice!"],
   ["form history",    "r?bob!"]]

Key on distinctive subject text, not commit hashes, which the rebase invalidates
as it goes. A commit matching no key, or whose subject carries no `r?` token, is
left exactly as it is.
"""

import json, re, subprocess, sys

R_TOKEN = re.compile(r"\s+r[?=]\S+$")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    mapping = json.load(open(sys.argv[1]))

    # %B is the full message. Only lines[0] may be edited: re-typing the body
    # drops the Differential Revision trailer and unlinks the revision.
    msg = subprocess.run(["git", "log", "-1", "--format=%B"],
                         capture_output=True, text=True, check=True).stdout.rstrip("\n")
    lines = msg.split("\n")

    for key, reviewers in mapping:
        if key not in lines[0]:
            continue
        if not R_TOKEN.search(lines[0]):
            print(f"  no r? token, left alone: {lines[0]}")
            return 0
        lines[0] = R_TOKEN.sub(" " + reviewers, lines[0])
        subprocess.run(["git", "commit", "--amend", "-q", "-F", "-"],
                       input="\n".join(lines) + "\n", text=True, check=True)
        print("  " + lines[0])
        return 0

    print(f"  no mapping key matched: {lines[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
