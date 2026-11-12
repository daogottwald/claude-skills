#!/usr/bin/env python3
"""Digest a mochitest log into the parts worth reading.

Infra signatures are checked first and set the verdict, so a run that never
started can't be read as a pile of test failures. Every finding cites the line
it came from, so anything surprising is one Read away in the log itself.

Usage: mochitest-summary.py <log> [<log> ...] [--fails N]
"""

import argparse
import re
import sys
from pathlib import Path

# Fatal: the environment failed, so test results mean nothing.
INFRA = [
    ("app timed out, no output", r"application timed out after [\d.]+ seconds"),
    ("Xvfb never started", r"Xvfb failed to start"),
    ("crash", r"\bCRASH:|Crash reason:"),
    ("stale objdir (uniffi ABI)", r"LowerRustArgs: Incorrect argument length"),
    ("stale objdir (missing URLs)", r"Missing chrome or resource URLs?:"),
    # Built markup and .ftl files are copies, not symlinks, so after the tree
    # moves they can disagree and the markup names an id nothing defines; the
    # browser window then throws a bare `uncaught exception: undefined` on
    # every open, failing most tests in a way that reads as a regression.
    ("stale objdir (missing Fluent string)",
     r"\[fluent\] Missing message in locale en-US:"),
    ("mochitest server never started", r"Timed out while waiting for server startup"),
    ("web server failed", r"Failed to start web server|Address already in use"),
    ("harness error", r"ERROR \| (?:automation|runtests)"),
]

# Worth surfacing, but does not invalidate the run.
NOTES = [
    ("another firefox was running (usual on a shared host)",
     r"Found '[^']+' running before starting test browser"),
    ("port 8888 was busy, fell back to a random port",
     r"is busy, switching to|falling back to a random port"),
]

COUNT_RE = re.compile(r"^\s*(?:[\d:.]+\s+INFO\s+)?(Passed|Failed|Todo):\s+(\d+)", re.M)
UNEXPECTED_RE = re.compile(r"^Unexpected results:\s+(\d+)", re.M)
RAN_RE = re.compile(r"^Ran (\d+) checks", re.M)
FILE_START_RE = re.compile(r"TEST_START:\s+(\S+)")
# The summary block repeats every failure, grouped under a bare path line, and
# that grouping is authoritative: it is the only place a failure is tied to its
# file. Without reading it, the repeats land on whichever file started last.
SUMMARY_START_RE = re.compile(r"^\s*Error Summary\s*$")
SUMMARY_FILE_RE = re.compile(r"^\s*(\S+\.(?:js|html|xhtml))\s*$")
# Local harness: " 0:50.63 FAIL <subject> - <message>", repeated in the summary
# block as "  FAIL <subject> - <message>". Structured runs use TEST-UNEXPECTED-*.
INLINE_FAIL_RE = re.compile(
    r"^\s*(?:[\d:.]+\s+)?(?:FAIL|TIMEOUT)\s+(?P<subject>.+?)(?:\s+-\s+(?P<msg>.*))?$")
STRUCTURED_FAIL_RE = re.compile(
    r"TEST-UNEXPECTED-(?:FAIL|TIMEOUT)\s*\|\s*(?P<subject>\S+)\s*\|\s*(?P<msg>.*)$")
TEST_END_BAD_RE = re.compile(r"TEST_END:\s+(TIMEOUT|ERROR|CRASH)")


def scan(path, limit):
    # A single streaming pass: a mochitest log runs to tens of MB, and holding
    # it as text plus a line list costs an order of magnitude more than that.
    infra_rx = [(label, re.compile(p)) for label, p in INFRA]
    notes_rx = [(label, re.compile(p)) for label, p in NOTES]
    infra_hit, notes_hit = {}, {}

    counts, unexpected, ran, wedged = {}, None, None, None
    # Track which file is running so task-level failures land under it. The same
    # failure appears inline and in the summary: dedupe.
    files, fails, seen, current = [], {}, set(), None
    in_summary = False

    with open(path, errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\n")

            for label, rx in infra_rx:
                if label not in infra_hit and rx.search(line):
                    infra_hit[label] = (n, line.strip()[:150])
            for label, rx in notes_rx:
                if label not in notes_hit and rx.search(line):
                    notes_hit[label] = (n, line.strip()[:150])

            m = COUNT_RE.match(line)
            if m:
                counts[m.group(1)] = int(m.group(2))
            if unexpected is None:
                m = UNEXPECTED_RE.match(line)
                if m:
                    unexpected = int(m.group(1))
            if ran is None:
                m = RAN_RE.match(line)
                if m:
                    ran = int(m.group(1))
            if wedged is None:
                m = TEST_END_BAD_RE.search(line)
                if m:
                    wedged = m.group(1)

            m = FILE_START_RE.search(line)
            if m:
                current = Path(m.group(1)).name
                if current not in files:
                    files.append(current)
                continue
            if not in_summary and SUMMARY_START_RE.match(line):
                in_summary = True
                current = None
                continue
            if in_summary:
                m = SUMMARY_FILE_RE.match(line)
                if m:
                    # Only the owner: `files` counts what the run started.
                    current = Path(m.group(1)).name
                    continue
            m = STRUCTURED_FAIL_RE.search(line) or (
                INLINE_FAIL_RE.match(line) if re.match(r"^\s*(?:[\d:.]+\s+)?(FAIL|TIMEOUT)\s", line)
                else None)
            if not m:
                continue
            subject = (m.group("subject") or "").strip()
            msg = (m.group("msg") or "").strip()
            if "profile uploaded in" in msg or "Not taking screenshot" in line:
                continue
            # A subject that is a path names the file; otherwise it's a task in it.
            names_file = subject.endswith((".js", ".html", ".xhtml"))
            owner = (Path(subject).name if names_file else current) or "(unknown file)"
            # A file-level line adds nothing over the task-level one it summarizes.
            detail = msg if (names_file or not subject) else f"{subject} — {msg}"
            key = (owner, detail)
            if key in seen or any(d.endswith(detail) for d, _ in fails.get(owner, {}).get("msgs", ())):
                continue
            seen.add(key)
            entry = fails.setdefault(owner, {"count": 0, "msgs": []})
            entry["count"] += 1
            if len(entry["msgs"]) < limit:
                entry["msgs"].append((detail, n))

    infra = [(label, *infra_hit[label]) for label, _ in INFRA if label in infra_hit]
    notes = [(label, *notes_hit[label]) for label, _ in NOTES if label in notes_hit]
    return dict(path=path, infra=infra, notes=notes, counts=counts,
                unexpected=unexpected, ran=ran, files=files, fails=fails,
                wedged=wedged)


def verdict_of(r):
    if r["infra"] or r["ran"] == 0:
        return "infra-error"
    if r["fails"] or r["counts"].get("Failed") or (r["unexpected"] or 0) > 0:
        return "fail" + (f" ({r['wedged'].lower()})" if r["wedged"] else "")
    if r["counts"].get("Passed"):
        return "pass"
    return "unknown — no counts in the log, did the run finish?"


def report(r, limit):
    print(r["path"])
    print(f"  VERDICT: {verdict_of(r)}")
    bits = [f"{k} {v}" for k, v in r["counts"].items()]
    if r["unexpected"] is not None:
        bits.append(f"Unexpected {r['unexpected']}")
    if r["ran"] is not None:
        bits.append(f"checks ran {r['ran']}")
    if bits:
        print("  counts:  " + "  ".join(bits))
    for label, n, text in r["infra"]:
        print(f"  INFRA:   {label}  (line {n})")
        print(f"           {text}")
    for name, entry in sorted(r["fails"].items(), key=lambda kv: -kv[1]["count"]):
        print(f"  fail:    {name}  x{entry['count']}")
        for detail, n in entry["msgs"]:
            print(f"             {detail[:150]}  (line {n})")
        if entry["count"] > limit:
            print(f"             … {entry['count'] - limit} more")
    if r["files"]:
        clean = len(r["files"]) - len(r["fails"])
        print(f"  files:   {len(r['files'])} started, {max(clean, 0)} without failures")
        if verdict_of(r).startswith(("fail", "infra")):
            print(f"  last:    {r['files'][-1]} was the last to start")
    for label, n, _ in r["notes"]:
        print(f"  note:    {label}  (line {n})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--fails", type=int, default=3,
                    help="messages to keep per failing file (default 3)")
    args = ap.parse_args()
    for path in args.logs:
        if not Path(path).exists():
            print(f"{path}: missing", file=sys.stderr)
            continue
        report(scan(path, args.fails), args.fails)


if __name__ == "__main__":
    main()
