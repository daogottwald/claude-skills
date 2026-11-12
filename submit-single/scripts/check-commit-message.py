#!/usr/bin/env python3
"""Check commit messages against the commit-message rules.

Mechanical checks only -- the judgement calls stay with the author. Two
severities: `error` findings are objective (paragraph and word ceilings,
bullets, hard-wrapped paragraphs, a banned phrase, filler framing) and are what a hook blocks on; `check` findings are
prompts to re-read a sentence, too false-positive-prone to gate on.

Usage:
  check-commit-message.py [REV | RANGE ...]      # default HEAD
  check-commit-message.py --stdin                # message on stdin
  check-commit-message.py --errors-only
Exit status: 2 if any error-level finding, 0 otherwise.
"""

import argparse
import re
import subprocess
import sys

PARAGRAPH_CEILING = 3
WORD_CEILING = 110
SYMBOLS_PER_PARAGRAPH = 5
# A continuation line at least this long is a wrap column, not a deliberate break.
WRAP_COLUMN_MIN = 50
SHINGLE = 6

TRAILER = re.compile(
    r"^(Differential Revision|Reviewed by|Depends on|MozReview-Commit-ID|"
    r"Signed-off-by|Co-authored-by|Change-Id)\b",
    re.I,
)
SKIP_SUBJECT = re.compile(r"^(fixup!|squash!|amend!)")
BULLET = re.compile(r"^\s*[-*+]\s+\S")
COUNTED_INVENTORY = re.compile(
    r"\b(two|three|four|five|six)\s+(places|callers|call sites|files|sites|spots|"
    r"consumers|methods|tests|paths|instances)\b",
    re.I,
)
ORDINAL = re.compile(r"^(First|Second|Third|Fourth|Finally|Next|Lastly)\s*,", re.M)
SYMBOL = re.compile(r"`[^`]+`|\b[\w/.-]+\.(?:mjs|js|jsm|sys\.mjs|css|cpp|h|py|toml|ini)\b")
BANNED = ["load-bearing", "whose"]
# A lead-in that only announces the point instead of making it. A short body is
# scannable on its own, so the emphasis is filler.
FILLER_FRAMING = [
    re.compile(r"\bworth (checking|knowing|noting)\b", re.I),
    re.compile(r"\b(?:the\s+)?(?:pieces?|things?|bits?|parts?|one[^.:]{0,40}?)\s+to check\b", re.I),
    re.compile(r"\bwhat to (?:check|look at|verify)\b", re.I),
    re.compile(r"\bthe thing worth\b", re.I),
]
ARGUMENT_TELLS = [
    "as opposed to",
    "which can only",
    "the alternative",
    "arguably",
    "one might",
    "of course",
    "needless to say",
]
COMMENT_START = ("//", "*", "/*", "#", "<!--", ";;", "--")


def sh(args, cwd=None):
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, check=False
    ).stdout


def body_of(message):
    lines = message.splitlines()
    body_lines = [l for l in lines[1:] if not TRAILER.match(l)]
    return "\n".join(body_lines).strip()


def paragraphs(body):
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def shingles(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i : i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def added_comment_text(sha, cwd):
    diff = sh(["git", "show", "--format=", "-U0", sha], cwd=cwd)
    out = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        stripped = line[1:].strip()
        if stripped.startswith(COMMENT_START):
            out.append(stripped.lstrip("/*<!-#;- "))
    return " ".join(out)


def check(message, sha=None, cwd=None):
    """Return a list of (severity, rule, detail)."""
    subject = message.splitlines()[0] if message.strip() else ""
    if SKIP_SUBJECT.match(subject):
        return []
    body = body_of(message)
    findings = []
    for phrase in BANNED:
        if phrase in f"{subject}\n{body}".lower():
            findings.append(("error", "banned-phrase", f'avoid "{phrase}"'))
    if not body:
        return findings

    paras = paragraphs(body)
    words = len(body.split())
    if len(paras) > PARAGRAPH_CEILING:
        findings.append(
            ("error", "paragraphs", f"body has {len(paras)} paragraphs; ceiling is {PARAGRAPH_CEILING}")
        )
    if words > WORD_CEILING:
        findings.append(
            ("error", "length", f"body is {words} words; most changes stay under ~{WORD_CEILING}")
        )
    bullets = [l for l in body.splitlines() if BULLET.match(l)]
    if bullets:
        findings.append(
            ("error", "bullets", f"{len(bullets)} bullet line(s); a message is prose, not an inventory")
        )
    for pat in FILLER_FRAMING:
        m = pat.search(body)
        if m:
            findings.append(
                ("error", "filler-framing", f'"{m.group(0)}" announces the point '
                 "instead of making it; state the fact directly")
            )

    for i, para in enumerate(paragraphs(body), 1):
        lines = para.splitlines()
        if len(lines) < 2:
            continue
        if any(BULLET.match(l) or l[:1].isspace() for l in lines):
            continue  # a list or an indented block wraps on purpose
        if all(len(l) >= WRAP_COLUMN_MIN for l in lines[:-1]):
            findings.append(
                ("error", "prewrapped", f"paragraph {i} is hard-wrapped across "
                 f"{len(lines)} lines; keep each paragraph on one line and let "
                 "the renderer wrap it")
            )

    for i, para in enumerate(paras, 1):
        symbols = {m.group(0) for m in SYMBOL.finditer(para)}
        if len(symbols) >= SYMBOLS_PER_PARAGRAPH:
            findings.append(
                ("check", "inventory", f"paragraph {i} names {len(symbols)} symbols/paths; "
                 "the reviewer reads extent from the diff")
            )
    m = COUNTED_INVENTORY.search(body)
    if m:
        findings.append(("check", "inventory", f'"{m.group(0)}" enumerates the hunks'))
    m = ORDINAL.search(body)
    if m:
        findings.append(("check", "inventory", f'"{m.group(0).strip()}" walks through the change'))
    for tell in ARGUMENT_TELLS:
        if tell in body.lower():
            findings.append(("check", "argument", f'"{tell}" defends the change rather than stating it'))

    if sha:
        overlap = shingles(body) & shingles(added_comment_text(sha, cwd))
        if overlap:
            longest = max(overlap, key=len)
            findings.append(
                ("check", "duplicates-comment",
                 f'body and a comment added here share "{longest}"; a shared fact is '
                 "fine, a shared sentence means the message is restating the code")
            )
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("revs", nargs="*", default=[])
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--errors-only", action="store_true")
    ap.add_argument("--cwd", default=None)
    args = ap.parse_args()

    targets = []  # (label, message, sha)
    if args.stdin:
        targets.append(("(stdin)", sys.stdin.read(), None))
    else:
        revs = args.revs or ["HEAD"]
        shas = []
        for rev in revs:
            if ".." in rev:
                shas += sh(["git", "rev-list", "--no-merges", "--reverse", rev], cwd=args.cwd).split()
            else:
                shas.append(sh(["git", "rev-parse", rev], cwd=args.cwd).strip())
        for sha in [s for s in shas if s]:
            if len(sh(["git", "rev-list", "--parents", "-1", sha], cwd=args.cwd).split()) > 2:
                continue  # merge
            msg = sh(["git", "log", "-1", "--format=%B", sha], cwd=args.cwd)
            subject = msg.splitlines()[0] if msg.strip() else ""
            targets.append((f"{sha[:12]} {subject[:56]}", msg, sha))

    worst = 0
    for label, msg, sha in targets:
        findings = check(msg, sha=sha, cwd=args.cwd)
        if args.errors_only:
            findings = [f for f in findings if f[0] == "error"]
        if not findings:
            continue
        print(label)
        for severity, rule, detail in findings:
            mark = "ERROR" if severity == "error" else "check"
            print(f"  {mark}  {rule}: {detail}")
            worst = max(worst, 2 if severity == "error" else 1)
    sys.exit(2 if worst == 2 else 0)


if __name__ == "__main__":
    main()
