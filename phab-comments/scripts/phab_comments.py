#!/usr/bin/env python3
"""Read Phabricator review comments, including suggested edits, on revisions or a stack.

Everything comes from Conduit. `differential.revision.search` finds the revisions
(with --stack, a whole stack), `transaction.search` carries each comment with its
anchor and `suggestionText`, and `differential.querydiffs` supplies the lines a
suggestion replaces, so each suggested edit prints as a small diff. `--summary`
prints one verdict line per revision instead of every comment.
"""

import argparse
import difflib
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
from datetime import datetime, timezone

HOST = "https://phabricator.services.mozilla.com"
UA = "phab-comments/2.0"
# Lines of the file printed either side of a suggested edit.
CONTEXT = 2
# Revision search caps a page at 100 constraint values.
BULK = 100
# Who wrote a comment: a human other than the revision author, the author, or an
# application such as Herald.
KINDS = ("reviewer", "self", "bot")


class Fail(Exception):
    pass


class Transient(Fail):
    """Rate limiting or a server-side hiccup; the request may work later."""


RETRY_CODES = (429, 502, 503, 504)
RETRY_DELAYS = (5, 15, 45)
# Mozilla's Fastly edge throttles Conduit by TLS fingerprint, not by API token:
# urllib (which sends no ALPN) and curl land in separate buckets, and moz-phab's
# urllib3 in a third. Once urllib draws a 429, every later call in the process
# goes through curl instead of feeding the throttled bucket. Requests are still
# spaced out, since every urllib script on the host shares the one bucket.
DEFAULT_GAP = 4.0
USE_CURL = False
# Methods that change state, so a 5xx (which may arrive after the write landed)
# is never retried. A 429 is: the edge refused the request before Phabricator saw it.
WRITE_METHODS = {"differential.createinline", "differential.createcomment"}


def is_write(method):
    return method.endswith(".edit") or method in WRITE_METHODS


class Pacer:
    """Keep at least `gap` seconds between requests, across every endpoint."""

    def __init__(self, gap=DEFAULT_GAP):
        self.gap = gap
        self._last = 0.0

    def wait(self):
        if self.gap <= 0:
            return
        due = self._last + self.gap - time.monotonic()
        if due > 0:
            time.sleep(due)
        self._last = time.monotonic()


PACER = Pacer()


def curl_fetch(url, data=None, timeout=90):
    """Fetch via the curl binary, raising Fail on a non-200 status.

    A 429 that persists for urllib has been observed to clear immediately for
    curl against the same endpoint with the same token, so curl is worth one
    attempt before giving up on a retryable status.
    """
    cmd = ["curl", "-sS", "--compressed", "--max-time", str(timeout),
           "-A", UA, "-w", "\n%{http_code}"]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    proc = subprocess.run(cmd + [url], input=data, capture_output=True)
    if proc.returncode != 0:
        raise Fail(f"curl {url} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    body, _, status = proc.stdout.decode("utf-8", "replace").rpartition("\n")
    status = status.strip()
    if status != "200":
        raise Fail(f"curl {url}: HTTP {status}")
    return body


def curl_fallback(url, what, attempts, data=None):
    """Send a request through curl, once urllib has drawn a 429."""
    exhausted = Transient(
        f"{what}: rate limited after {attempts} attempts -- try again later."
    )
    if not shutil.which("curl"):
        raise exhausted
    try:
        return curl_fetch(url, data=data)
    except Fail as exc:
        raise Transient(
            f"{what}: rate limited via urllib, and curl also failed ({exc}) -- "
            "try again later."
        ) from exc


def retry_after(exc, attempt):
    """Seconds to wait before the next attempt, honouring Retry-After."""
    header = getattr(exc, "headers", None)
    if header:
        raw = header.get("Retry-After")
        if raw and raw.isdigit():
            return min(int(raw), 120)
    return RETRY_DELAYS[attempt]


# --------------------------------------------------------------------------- #
# Conduit
# --------------------------------------------------------------------------- #


def conduit_token():
    for var in ("PHABRICATOR_API_TOKEN", "CONDUIT_TOKEN", "PHABRICATOR_TOKEN"):
        if os.environ.get(var):
            return os.environ[var]
    path = os.path.expanduser("~/.arcrc")
    try:
        with open(path) as fh:
            arc = json.load(fh)
    except FileNotFoundError:
        raise Fail(
            "No Conduit API token.\n"
            "  Set PHABRICATOR_API_TOKEN, or create ~/.arcrc via `moz-phab install-certificate`.\n"
            f"  Get a token at {HOST}/settings/panel/apitokens/"
        )
    except (OSError, ValueError) as exc:
        raise Fail(f"Cannot read ~/.arcrc: {exc}")
    for key, val in (arc.get("hosts") or {}).items():
        if "phabricator.services.mozilla.com" in key and val.get("token"):
            return val["token"]
    raise Fail("~/.arcrc has no token for phabricator.services.mozilla.com")


def conduit(method, params, token):
    payload = dict(params)
    payload["__conduit__"] = {"token": token}
    data = urllib.parse.urlencode(
        {"params": json.dumps(payload), "output": "json", "__conduit__": "1"}
    ).encode()
    global USE_CURL
    for attempt in range(len(RETRY_DELAYS) + 1):
        PACER.wait()
        if USE_CURL:
            body = json.loads(
                curl_fallback(f"{HOST}/api/{method}", f"Conduit {method}", attempt, data=data)
            )
            break
        req = urllib.request.Request(f"{HOST}/api/{method}", data=data)
        req.add_header("User-Agent", UA)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                print(f"note: Conduit {method} got HTTP 429; using curl from here on",
                      file=sys.stderr)
                USE_CURL = True
                continue
            if exc.code in RETRY_CODES and is_write(method):
                raise Fail(
                    f"Conduit {method}: HTTP {exc.code}. The write may have landed; "
                    "check the revision's drafts before retrying."
                )
            if exc.code not in RETRY_CODES or attempt == len(RETRY_DELAYS):
                raise Fail(f"Conduit {method} failed: HTTP {exc.code}")
            delay = retry_after(exc, attempt)
            print(
                f"note: Conduit {method} got HTTP {exc.code}; retrying in {delay}s",
                file=sys.stderr,
            )
            time.sleep(delay)
        except urllib.error.URLError as exc:
            raise Fail(f"Conduit {method} failed: {exc}")
    if body.get("error_code"):
        raise Fail(f"Conduit {method}: {body['error_code']} {body.get('error_info', '')}")
    return body["result"]


def conduit_all(method, params, token):
    """Follow Conduit's `after` cursor and return every page's data."""
    out = []
    after = None
    while True:
        page = conduit(method, dict(params, after=after), token)
        out.extend(page["data"])
        after = (page.get("cursor") or {}).get("after")
        if not after:
            return out


# --------------------------------------------------------------------------- #
# Revisions
# --------------------------------------------------------------------------- #


def parse_revision(arg):
    m = re.fullmatch(r"[Dd]?(\d+)", arg.strip()) or re.search(r"/D(\d+)", arg)
    if not m:
        raise Fail(f"Cannot parse revision {arg!r}; expected D319190 or 319190")
    return int(m.group(1))


def lookup(ids, token):
    """{id: revision} for every id Conduit can see, BULK ids per request."""
    out = {}
    for i in range(0, len(ids), BULK):
        for rev in conduit_all(
            "differential.revision.search",
            {"constraints": {"ids": ids[i:i + BULK]}},
            token,
        ):
            out[rev["id"]] = rev
    return out


def expand_stack(seed, token):
    """Every revision in `seed`'s stack, parents before children, id as tiebreak."""
    # stackGraph maps each revision's PHID to the PHIDs it depends on.
    graph = seed["fields"].get("stackGraph") or {}
    if not graph:
        return [seed]
    by_phid = {}
    phids = sorted(graph)
    for i in range(0, len(phids), BULK):
        for rev in conduit_all(
            "differential.revision.search",
            {"constraints": {"phids": phids[i:i + BULK]}},
            token,
        ):
            by_phid[rev["phid"]] = rev

    def by_id(phid):
        return by_phid[phid]["id"]

    pending = {p: {q for q in graph.get(p, []) if q in by_phid} for p in by_phid}
    order = []
    while pending:
        ready = sorted((p for p, deps in pending.items() if not deps), key=by_id)
        if not ready:  # a cycle should not happen; emit the rest by id
            ready = sorted(pending, key=by_id)
        take = ready[0]
        order.append(by_phid[take])
        del pending[take]
        for deps in pending.values():
            deps.discard(take)
    return order


# --------------------------------------------------------------------------- #
# Comments
# --------------------------------------------------------------------------- #


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Names:
    """Resolve author PHIDs to (username, real name), fetching each PHID once.

    A stack's revisions share their authors, so after the first revision most
    lookups cost no request at all.
    """

    def __init__(self):
        self.known = {}

    def resolve(self, phids, token):
        todo = sorted(set(phids) - set(self.known))
        if not todo:
            return
        for user in conduit_all(
            "user.search", {"constraints": {"phids": todo}}, token
        ):
            self.known[user["phid"]] = (
                user["fields"]["username"],
                user["fields"].get("realName") or "",
            )
        # Bots and applications (Herald, Diffusion) are not users.
        rest = [p for p in todo if p not in self.known]
        if rest:
            for phid, info in (conduit("phid.query", {"phids": rest}, token) or {}).items():
                self.known[phid] = (info.get("name") or phid, info.get("fullName") or "")
        for phid in todo:
            self.known.setdefault(phid, (phid, ""))

    def get(self, phid):
        return self.known.get(phid, (phid, ""))


def collect(rev, token, names):
    """Every comment on one revision, general and inline, tagged by who wrote it."""
    rev_id = rev["id"]
    author = rev["fields"].get("authorPHID")
    txns = conduit_all("transaction.search", {"objectIdentifier": f"D{rev_id}"}, token)

    general, inlines, phids = [], [], set()
    saw_suggestion_field = False
    for txn in txns:
        kind = txn.get("type")
        # Other transaction types come back with a null type and carry no comments.
        if kind not in ("comment", "inline"):
            continue
        comments = [c for c in txn.get("comments") or [] if not c.get("removed")]
        if not comments:
            continue
        comment = comments[0]
        who = comment["authorPHID"]
        phids.add(who)
        base = {
            "id": comment["id"],
            "authorPHID": who,
            "bucket": "bot" if "-APPS-" in who else "self" if who == author else "reviewer",
            "epoch": comment["dateCreated"],
            "date": iso(comment["dateCreated"]),
            "text": (comment.get("content") or {}).get("raw", ""),
        }
        if kind == "comment":
            general.append(base)
            continue
        f = txn["fields"]
        saw_suggestion_field |= "hasSuggestion" in f
        suggestion = None
        if f.get("hasSuggestion"):
            suggestion = {"text": f.get("suggestionText") or "",
                          "before": None, "diff": None}
        base.update(
            {
                "path": f.get("path"),
                "line": f.get("line"),
                "length": f.get("length") or 1,
                "isNewFile": f.get("isNewFile", True),
                "diff": (f.get("diff") or {}).get("id"),
                "isDone": bool(f.get("isDone")),
                "isReply": bool(f.get("replyToCommentPHID")),
                "suggestion": suggestion,
            }
        )
        inlines.append(base)
    if inlines and not saw_suggestion_field:
        print(
            f"warning: D{rev_id}: transaction.search returned inlines without "
            "hasSuggestion, so suggested edits cannot be read. Phabricator may "
            "have changed -- the script needs an update.",
            file=sys.stderr,
        )

    names.resolve(phids, token)
    for item in general + inlines:
        item["authorName"], item["authorRealName"] = names.get(item["authorPHID"])

    general.sort(key=lambda c: c["epoch"])
    inlines.sort(key=lambda c: (c["path"] or "", c["line"] or 0, c["epoch"]))
    return {
        "revision": {
            "id": rev_id,
            "name": f"D{rev_id}",
            "title": rev["fields"].get("title", ""),
            "uri": f"{HOST}/D{rev_id}",
            "status": (rev["fields"].get("status") or {}).get("value"),
        },
        "general": general,
        "inlines": inlines,
    }


# --------------------------------------------------------------------------- #
# Suggested edits
# --------------------------------------------------------------------------- #


def hunk_lines(change, new_side):
    """Map line number -> text for one side of a changed file, from its hunks.

    `differential.querydiffs` returns each hunk with the context moz-phab uploaded
    (the whole file unless it ran with `--less-context`), so an anchored line is
    almost always present. `differential.getrawdiff` trims context to a few lines
    and is not a substitute.
    """
    lines = {}
    own = "+" if new_side else "-"
    for hunk in change.get("hunks") or []:
        n = int(hunk["newOffset" if new_side else "oldOffset"])
        for raw in hunk["corpus"].split("\n"):
            # An empty string is the split's trailing remnant; a backslash starts
            # the "\ No newline at end of file" marker. Neither is a line.
            if not raw or raw[0] == "\\":
                continue
            if raw[0] in (" ", own):
                lines[n] = raw[1:]
                n += 1
    return lines


def before_lines(changes, item):
    """(context before, replaced lines, context after) for one inline, or None
    when the anchored range is not in the diff."""
    new_side = item["isNewFile"]
    key = "currentPath" if new_side else "oldPath"
    change = next((c for c in changes if c.get(key) == item["path"]), None)
    if change is None:
        change = next(
            (c for c in changes
             if item["path"] in (c.get("currentPath"), c.get("oldPath"))),
            None,
        )
    if change is None:
        return None
    lines = hunk_lines(change, new_side)
    first, last = item["line"], item["line"] + item["length"] - 1
    if any(n not in lines for n in range(first, last + 1)):
        return None
    pre = [lines[n] for n in range(first - CONTEXT, first) if n in lines]
    post = [lines[n] for n in range(last + 1, last + 1 + CONTEXT) if n in lines]
    return pre, [lines[n] for n in range(first, last + 1)], post


def split_lines(text):
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def suggestion_diff(before, after):
    """Diff rows: leading-space context, `-` original lines, `+` replacement."""
    if before is None:
        return [f"+{line}" for line in after]
    pre, old, post = before
    rows = [f" {line}" for line in pre]
    matcher = difflib.SequenceMatcher(a=old, b=after, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            rows += [f" {line}" for line in old[i1:i2]]
        else:
            rows += [f"-{line}" for line in old[i1:i2]]
            rows += [f"+{line}" for line in after[j1:j2]]
    rows += [f" {line}" for line in post]
    return rows


def attach_before(data, token):
    """Fetch every diff that carries a suggestion, once, and render each
    suggestion as a diff against the lines it replaces."""
    wanted = sorted({c["diff"] for c in data["inlines"] if c["suggestion"] and c["diff"]})
    if not wanted:
        return
    diffs = conduit("differential.querydiffs", {"ids": wanted}, token) or {}
    for c in data["inlines"]:
        s = c["suggestion"]
        if not s:
            continue
        changes = (diffs.get(str(c["diff"])) or {}).get("changes") or []
        before = before_lines(changes, c)
        s["before"] = None if before is None else before[1]
        s["diff"] = "\n".join(suggestion_diff(before, split_lines(s["text"])))


# --------------------------------------------------------------------------- #
# Full output
# --------------------------------------------------------------------------- #


def shape(data, args):
    """Apply --inline / --skip-done filtering; return (data, hidden count)."""
    if args.inline:
        want = str(args.inline)
        data["general"] = []
        data["inlines"] = [c for c in data["inlines"] if str(c["id"]) == want]
        return data, 0
    hidden = sum(1 for c in data["inlines"] if c["isDone"])
    if not args.skip_done:
        return data, 0
    data["inlines"] = [c for c in data["inlines"] if not c["isDone"]]
    return data, hidden


def indent(text, pad):
    return "\n".join(pad + line if line else pad.rstrip() for line in text.split("\n"))


def who(c):
    real = c.get("authorRealName")
    return f"{c['authorName']} ({real})" if real else c["authorName"]


def render(data, hidden):
    """Text for one revision."""
    rev = data["revision"]
    out = [f"{rev['name']} {rev['title']}", rev["uri"], ""]
    counts = [f"{len(data['general'])} general", f"{len(data['inlines'])} inline"]
    suggested = sum(1 for c in data["inlines"] if c["suggestion"])
    if suggested:
        counts.append(f"{suggested} with a suggested edit")
    if hidden:
        counts.append(f"{hidden} done (hidden by --skip-done)")
    out.append("Comments: " + ", ".join(counts))

    if data["general"]:
        out += ["", "=" * 72, "GENERAL COMMENTS", "=" * 72]
        for c in data["general"]:
            out += ["", f"{who(c)}  {c['date']}", indent(c["text"].strip(), "  ")]

    if data["inlines"]:
        out += ["", "=" * 72, "INLINE COMMENTS", "=" * 72]
        for c in data["inlines"]:
            out += render_inline(c)
    return "\n".join(out) + "\n"


def render_inline(c):
    span = str(c["line"])
    if (c["length"] or 1) > 1:
        span += f"-{c['line'] + c['length'] - 1}"
    flags = []
    if c["isDone"]:
        flags.append("done")
    if c["isReply"]:
        flags.append("reply")
    if not c["isNewFile"]:
        flags.append("old side")
    if c["suggestion"]:
        flags.append("suggested edit")
    tail = ("  [" + ", ".join(flags) + "]") if flags else ""
    out = [
        "",
        f"{c['path']}:{span}{tail}",
        f"  {who(c)}  {c['date']}  id {c['id']}  diff {c['diff']}",
    ]
    text = (c["text"] or "").strip()
    if text:
        out.append(indent(text, "  "))
    elif c["suggestion"]:
        out.append("  (no comment text -- this is a suggestion-only inline)")
    else:
        out.append("  (no comment text)")
    if c["suggestion"]:
        if c["suggestion"]["before"] is None:
            out.append(f"  Suggested edit (replaces line {span}; the original "
                       "lines are not in the diff):")
        else:
            out.append("  Suggested edit:")
        out.append(indent(c["suggestion"]["diff"], "    "))
    return out


# --------------------------------------------------------------------------- #
# Summary output
# --------------------------------------------------------------------------- #


def counted_kinds(args):
    """Which author buckets the summary verdict counts."""
    return (
        ["reviewer"]
        + (["self"] if args.include_self else [])
        + (["bot"] if args.include_bots else [])
    )


def summarize(data, args):
    """Per-bucket counts and the verdict for one revision."""
    counts = {
        k: {"general": 0, "inline": 0, "done": 0, "latest": 0, "authors": set()}
        for k in KINDS
    }
    for c in data["general"]:
        b = counts[c["bucket"]]
        b["general"] += 1
        b["authors"].add(c["authorName"])
        b["latest"] = max(b["latest"], c["epoch"])
    for c in data["inlines"]:
        b = counts[c["bucket"]]
        b["inline"] += 1
        b["done"] += c["isDone"]
        b["authors"].add(c["authorName"])
        b["latest"] = max(b["latest"], c["epoch"])

    kinds = counted_kinds(args)
    inline = general = 0
    for k in kinds:
        general += counts[k]["general"]
        inline += counts[k]["inline"] - (counts[k]["done"] if args.skip_done else 0)
    latest = max([counts[k]["latest"] for k in kinds] or [0])
    return {
        **data["revision"],
        "lastCommentDate": iso(latest) if latest else None,
        "counts": {
            k: {"general": b["general"], "inline": b["inline"], "done": b["done"],
                "authors": sorted(b["authors"])}
            for k, b in counts.items()
        },
        "general": [
            {"author": c["authorName"], "date": c["date"], "epoch": c["epoch"],
             "text": c["text"], "bucket": c["bucket"]}
            for c in data["general"]
        ],
        "hasComments": bool(inline or general),
        "hasInlines": bool(inline),
        "countedInline": inline,
        "countedGeneral": general,
    }


def summary_line(s):
    parts = []
    for kind in KINDS:
        b = s["counts"][kind]
        if not (b["general"] or b["inline"]):
            continue
        bits = []
        if b["inline"]:
            bits.append(f"{b['inline']} inline"
                        + (f" ({b['done']} done)" if b["done"] else ""))
        if b["general"]:
            bits.append(f"{b['general']} general")
        parts.append(f"{kind} {','.join(b['authors'])}: " + ", ".join(bits))
    tail = "; ".join(parts) or "no comments"
    flag = "INLINE" if s["hasInlines"] else "read " if s["hasComments"] else "  --  "
    return f"  {flag}  {s['name']}  {tail}"


def render_summary(summaries, missing, args):
    inline = [s["name"] for s in summaries if s["hasInlines"]]
    out = [
        f"Summary: {len(summaries)} revision(s), "
        f"{sum(1 for s in summaries if s['hasComments'])} with comments, "
        f"{len(inline)} with inline comments."
    ]
    out += [summary_line(s) for s in summaries]
    if missing:
        out.append("  missing (no such revision, or not visible): "
                   + ", ".join(f"D{i}" for i in missing))

    if not args.quiet_general:
        kinds = counted_kinds(args)
        for s in summaries:
            shown = [g for g in s["general"] if g["bucket"] in kinds]
            if not shown:
                continue
            out += ["", f"{s['name']} {s['title']}", s["uri"]]
            for g in shown:
                out.append(f"  {g['author']}  {g['date']}")
                out += ["    " + line for line in g["text"].strip().split("\n")]

    if inline:
        out += [
            "",
            "Inline comments, with any suggested edits, print with one run",
            "(a shell loop per revision paces nothing):",
            f"  python3 {os.path.abspath(sys.argv[0])} " + " ".join(inline),
        ]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Read Phabricator review comments, including suggested edits."
    )
    ap.add_argument("revision", nargs="+", help="D-numbers, ids, or revision URLs")
    ap.add_argument("--stack", action="store_true",
                    help="expand each argument to its whole stack (one extra request)")
    ap.add_argument("--summary", action="store_true",
                    help="one verdict line per revision plus the general comments, "
                         "instead of every comment")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--delay", type=float, default=DEFAULT_GAP, metavar="SECONDS",
                    help=f"minimum gap between requests (default {DEFAULT_GAP}); "
                         "raise it if Phabricator rate-limits a long sweep")
    full = ap.add_argument_group("reading comments (the default)")
    full.add_argument("--include-done", action="store_true",
                      help=argparse.SUPPRESS)
    full.add_argument("--inline", metavar="ID", help="show only this inline comment id")
    ap.add_argument("--skip-done", action="store_true",
                    help="hide inlines the author already marked done (shown by default, "
                         "so an already-answered comment is never answered twice)")
    summ = ap.add_argument_group("--summary")
    summ.add_argument("--include-self", action="store_true",
                      help="count the revision author's own comments as feedback")
    summ.add_argument("--include-bots", action="store_true",
                      help="count bot comments (Herald's testing-policy notice, Harbormaster)")
    summ.add_argument("--quiet-general", action="store_true",
                      help="do not print the general comments' text")
    args = ap.parse_args(argv)

    PACER.gap = args.delay
    try:
        token = conduit_token()
        ids = list(dict.fromkeys(parse_revision(spec) for spec in args.revision))
        found = lookup(ids, token)
        missing = [i for i in ids if i not in found]
        revs = {}
        for i in ids:
            if i not in found:
                continue
            for rev in expand_stack(found[i], token) if args.stack else [found[i]]:
                revs.setdefault(rev["id"], rev)
    except Transient as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for i in missing:
        print(f"error: D{i}: no such revision (or you cannot see it)", file=sys.stderr)

    names = Names()
    results, failed, throttled = [], False, False
    revs = list(revs.values())
    for n, rev in enumerate(revs):
        try:
            data = collect(rev, token, names)
            if args.summary:
                results.append(summarize(data, args))
                continue
            data, hidden = shape(data, args)
            attach_before(data, token)
            if not args.as_json:
                if results:
                    print()
                print(render(data, hidden), end="", flush=True)
            results.append(data)
        except Transient as exc:
            # Once the server is throttling, the remaining revisions will only
            # add requests to the penalty. Stop rather than hammer.
            print(f"error: D{rev['id']}: {exc}", file=sys.stderr)
            remaining = [f"D{r['id']}" for r in revs[n + 1:]]
            if remaining:
                print(
                    "error: skipping " + ", ".join(remaining)
                    + " -- wait for the rate limit to clear, then rerun.",
                    file=sys.stderr,
                )
            throttled = True
            break
        except Fail as exc:
            print(f"error: D{rev['id']}: {exc}", file=sys.stderr)
            failed = True

    if args.summary:
        if args.as_json:
            inline = [s["name"] for s in results if s["hasInlines"]]
            print(json.dumps({"revisions": results, "inline": inline, "missing": missing},
                             indent=2, ensure_ascii=False))
        else:
            print(render_summary(results, missing, args), end="")
    elif args.as_json:
        print(json.dumps(results if len(results) != 1 else results[0],
                         indent=2, ensure_ascii=False))
    return 3 if throttled else 2 if (missing or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
