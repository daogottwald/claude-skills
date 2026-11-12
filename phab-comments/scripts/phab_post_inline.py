#!/usr/bin/env python3
"""Post an inline comment on a Phabricator revision, anchored to a file and line.

`differential.createinline` only creates a *draft*: it is invisible to
`transaction.search` and to everyone else until something publishes it, and
`differential.revision.edit` (what `phab.py comment` sends) does not.
`differential.createcomment` with `attach_inlines` does, and with an empty
message it publishes the drafts without adding a general comment.

The line number is the one on the revision's *current diff* (the one
Phabricator holds), not on the local checkout, and a published inline can be
neither moved nor deleted. So before creating the draft the script fetches that
diff, prints the text of the anchored line when it lies within a hunk, and
refuses to post until `--expect` names a snippet of it (or `--force` skips the
check). A line outside every hunk cannot be verified and posts with a warning.

`--reply-to <inline id>` takes the anchor from a published inline instead. An
inline left on the current diff is answered at its own line. An inline left on
an older diff is stored in that diff's numbering and shown ported onto the
current one, and no model of that port has held up: an offset port misplaced two
replies on D328788 and a changed-stretch port one on D327251. So the script
stops, prints its guess, and asks for `--at <line>`, the line the web UI shows
the thread on, because a published inline cannot be moved.

`--request-changes` then sets the review to Request Changes (a `reject`
transaction).

The comment body is read from stdin.
"""

import argparse
import difflib
import re
import sys
import time

from phab_comments import Fail, conduit, conduit_token, parse_revision


def create_draft(rev, path, line, length, is_new, body, token):
    params = {
        "revisionID": rev,
        "filePath": path,
        "isNewFile": 1 if is_new else 0,
        "lineNumber": line,
        "content": body,
    }
    if length:
        params["lineLength"] = length
    return conduit("differential.createinline", params, token)


def current_diff_id(rev, token):
    result = conduit(
        "differential.revision.search", {"constraints": {"ids": [rev]}}, token
    )
    data = result.get("data") or []
    if not data:
        raise Fail(f"D{rev} not found")
    diff_phid = data[0]["fields"]["diffPHID"]
    diffs = conduit(
        "differential.diff.search", {"constraints": {"phids": [diff_phid]}}, token
    )
    return diffs["data"][0]["id"]


def anchored_line(raw, path, line, is_new):
    """Text of `line` on one side of `path` in a raw unified diff, or None when
    the line falls outside the diff's hunks (or the file is not in the diff)."""
    in_file = False
    old = new = None
    for row in raw.splitlines():
        if row.startswith("diff --git "):
            in_file = row.endswith(f" b/{path}")
            old = new = None
            continue
        if not in_file:
            continue
        if row.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
            old, new = int(m.group(1)), int(m.group(2))
            continue
        if old is None or row.startswith("\\"):
            continue
        tag, text = row[:1], row[1:]
        if tag == "+":
            if is_new and new == line:
                return text
            new += 1
        elif tag == "-":
            if not is_new and old == line:
                return text
            old += 1
        else:
            if (new if is_new else old) == line:
                return text
            old += 1
            new += 1
    return None


def side_lines(raw, path, is_new):
    """[(line, text)] for one side of `path` in a raw unified diff, hunk lines
    only."""
    out = []
    in_file = False
    old = new = None
    for row in raw.splitlines():
        if row.startswith("diff --git "):
            in_file = row.endswith(f" b/{path}")
            old = new = None
            continue
        if not in_file:
            continue
        if row.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
            old, new = int(m.group(1)), int(m.group(2))
            continue
        if old is None or row.startswith("\\"):
            continue
        tag, text = row[:1], row[1:]
        if tag == "+":
            if is_new:
                out.append((new, text))
            new += 1
        elif tag == "-":
            if not is_new:
                out.append((old, text))
            old += 1
        else:
            out.append((new if is_new else old, text))
            old += 1
            new += 1
    return out


def port_line(old_raw, new_raw, path, line, is_new):
    """A guess at where Phabricator shows a line of an older diff on the
    current one, judged from the two diffs' text; only a hint beside --at. A
    line in an unchanged stretch keeps its offset, which held on D327677 and
    misplaced two replies on D328788. The i-th line of a replaced stretch maps
    to the i-th line of its replacement (D327251: a three-line replacement kept
    the thread on its line), the last one when the replacement is shorter; a
    line of a deleted stretch maps to the first line after it. Returns (line,
    changed), or None when the line is outside the older diff's hunks."""
    old_side = side_lines(old_raw, path, is_new)
    new_side = side_lines(new_raw, path, is_new)
    idx = next((i for i, (n, _) in enumerate(old_side) if n == line), None)
    if idx is None:
        return None
    matcher = difflib.SequenceMatcher(
        None, [t for _, t in old_side], [t for _, t in new_side], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if i1 <= idx < i2:
            if tag == "equal":
                j, changed = j1 + (idx - i1), False
            elif j2 > j1:
                j, changed = min(j1 + (idx - i1), j2 - 1), True
            else:
                j, changed = j2, True
            break
    else:
        return None
    if j < len(new_side):
        return new_side[j][0], changed
    return (new_side[-1][0] + 1, changed) if new_side else None


def find_inline(rev, inline_id, token):
    """path, line, length, isNewFile and diff id of a published inline."""
    result = conduit(
        "transaction.search", {"objectIdentifier": f"D{rev}"}, token
    )
    for txn in result.get("data", []):
        if txn.get("type") != "inline":
            continue
        for comment in txn.get("comments") or []:
            if str(comment.get("id")) == str(inline_id):
                f = txn.get("fields") or {}
                return {
                    "path": f.get("path"),
                    "line": f.get("line"),
                    "length": (f.get("length") or 1) - 1,
                    "isNewFile": f.get("isNewFile", True),
                    "diff": (f.get("diff") or {}).get("id"),
                    "id": inline_id,
                }
    raise Fail(f"inline {inline_id} not found on D{rev}")


def reply_anchor(rev, inline, token, at=None):
    """The anchor a reply to `inline` needs on the current diff. An inline left
    on an older diff is stored in that diff's line numbers and displayed ported
    onto the current one. The port is Phabricator's and the model here has
    misplaced replies both where the thread's lines changed (D327251) and where
    only lines above them did (D328788), so the script stops until `at` names
    the line the web UI shows the thread on, and offers the model's line only
    as a guess to check against the UI."""
    diff_id = current_diff_id(rev, token)
    if inline["diff"] == diff_id:
        return inline["line"], inline["length"]
    old_raw = conduit(
        "differential.getrawdiff", {"diffID": str(inline["diff"])}, token
    )
    new_raw = conduit("differential.getrawdiff", {"diffID": str(diff_id)}, token)
    last = inline["line"] + inline["length"]
    first = port_line(old_raw, new_raw, inline["path"], inline["line"], inline["isNewFile"])
    ported_last = port_line(old_raw, new_raw, inline["path"], last, inline["isNewFile"])
    if first is None or ported_last is None:
        guess, length = None, inline["length"]
    else:
        (guess, _), (ported_last, _) = first, ported_last
        length = max(ported_last - guess, 0)
    if at is None:
        raise Fail(
            f"inline {inline['id']} sits on diff {inline['diff']} at line "
            f"{inline['line']}, older than the current diff {diff_id}; "
            + (f"the model guesses line {guess}, " if guess else "")
            + "but it has misplaced replies before and a published inline "
            "cannot be moved. Open the revision, read the line the thread is "
            "shown on, and re-run with --at <line>"
        )
    print(
        f"inline sits on diff {inline['diff']} at line {inline['line']}; "
        f"anchoring at {at} as given"
        + (f" (the model guessed {guess})" if guess else "")
    )
    return at, length


def check_anchor(rev, path, line, is_new, expect, force, token):
    diff_id = current_diff_id(rev, token)
    raw = conduit("differential.getrawdiff", {"diffID": str(diff_id)}, token)
    text = anchored_line(raw, path, line, is_new)
    if text is None:
        if force:
            print(
                f"warning: {path}:{line} is outside diff {diff_id}'s hunks; "
                f"posting unverified"
            )
            return
        raise Fail(
            f"{path}:{line} is outside diff {diff_id}'s hunks, so the line is "
            f"probably numbered off another diff (local HEAD?); a reply to a "
            f"reviewer's inline sits on a changed line. --force posts anyway"
        )
    print(f"diff {diff_id} {path}:{line}: {text.strip()}")
    if expect:
        if expect not in text:
            raise Fail(
                f"--expect {expect!r} is not on that line; the line number "
                f"belongs to another diff (local HEAD?)"
            )
    elif not force:
        raise Fail(
            "confirm the anchor: re-run with --expect '<snippet of that line>' "
            "(or --force to post regardless)"
        )


def publish(rev, message, token):
    return conduit(
        "differential.createcomment",
        {"revision_id": rev, "message": message, "attach_inlines": 1},
        token,
    )


def request_changes(rev, token):
    conduit(
        "differential.revision.edit",
        {
            "objectIdentifier": f"D{rev}",
            "transactions": [{"type": "reject", "value": True}],
        },
        token,
    )
    data = conduit(
        "differential.revision.search", {"constraints": {"ids": [rev]}}, token
    )["data"]
    return data[0]["fields"]["status"]["value"]


def published_inlines(rev, token):
    result = conduit(
        "transaction.search", {"objectIdentifier": f"D{rev}"}, token
    )
    out = []
    for txn in result.get("data", []):
        if txn.get("type") != "inline":
            continue
        fields = txn.get("fields") or {}
        comments = txn.get("comments") or [{}]
        out.append(
            {
                "path": fields.get("path"),
                "line": fields.get("line"),
                "id": comments[0].get("id"),
                "raw": ((comments[0].get("content") or {}).get("raw") or ""),
            }
        )
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("revision", help="D321758, 321758 or a revision URL")
    ap.add_argument(
        "path", nargs="?", help="repo-relative path, as the diff shows it"
    )
    ap.add_argument(
        "line",
        nargs="?",
        help="line number in the file, or a FIRST-LAST range as the reader prints it",
    )
    ap.add_argument(
        "--reply-to",
        metavar="INLINE_ID",
        help="anchor where the current diff shows this published inline "
        "(the id the reader prints); replaces path and line",
    )
    ap.add_argument(
        "--at",
        type=int,
        metavar="LINE",
        help="with --reply-to: the line the web UI shows the thread on, "
        "required when the inline was left on an older diff",
    )
    ap.add_argument(
        "--old",
        action="store_true",
        help="anchor on the left-hand (pre-change) side instead of the new file",
    )
    ap.add_argument(
        "--length",
        type=int,
        default=0,
        help="extra lines to span (0 anchors one line); implied by a FIRST-LAST line",
    )
    ap.add_argument(
        "-m",
        "--message",
        default="",
        help="general comment to publish alongside; empty posts only the inline",
    )
    ap.add_argument(
        "--expect",
        default="",
        help="snippet the anchored line must contain on the revision's current diff",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="post without confirming the anchored line's text",
    )
    ap.add_argument(
        "--request-changes",
        action="store_true",
        help="after publishing, set the review to Request Changes",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent and exit",
    )
    args = ap.parse_args()
    rev = parse_revision(args.revision)
    token = conduit_token()
    if args.reply_to:
        if args.path or args.line:
            raise Fail("--reply-to takes the anchor from the inline; drop path and line")
        inline = find_inline(rev, args.reply_to, token)
        args.path = inline["path"]
        args.old = not inline["isNewFile"]
        args.line, args.length = reply_anchor(rev, inline, token, args.at)
    elif not (args.path and args.line):
        raise Fail("path and line are required without --reply-to")
    elif "-" in args.line:
        first, last = (int(x) for x in args.line.split("-", 1))
        args.line, args.length = first, last - first
    else:
        args.line = int(args.line)

    body = sys.stdin.read().strip()
    if not body:
        raise Fail("Empty comment body on stdin")

    side = "old" if args.old else "new"
    check_anchor(
        rev, args.path, args.line, not args.old, args.expect, args.force, token
    )
    if args.dry_run:
        print(f"D{rev} {args.path}:{args.line} ({side}, length {args.length})")
        print(body)
        if args.message:
            print("\n-- general comment --\n" + args.message)
        if args.request_changes:
            print("\n-- then Request Changes --")
        return 0

    draft = create_draft(
        rev, args.path, args.line, args.length, not args.old, body, token
    )
    print(f"draft inline {draft.get('id')} on diff {draft.get('diffID')}")

    # attach_inlines publishes *every* pending draft of yours on this revision,
    # including ones typed in the web UI and never submitted.
    publish(rev, args.message, token)

    time.sleep(2)
    head = body.splitlines()[0][:40]
    for inline in published_inlines(rev, token):
        if (
            inline["path"] == args.path
            and str(inline["line"]) == str(args.line)
            and inline["raw"].startswith(head)
        ):
            print(f"published inline {inline['id']} at {args.path}:{args.line}")
            if args.request_changes:
                print(f"requested changes; status {request_changes(rev, token)}")
            return 0
    print(
        f"ERROR: no published inline at {args.path}:{args.line} -- the draft is "
        f"still a draft; publish it from the web UI",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
