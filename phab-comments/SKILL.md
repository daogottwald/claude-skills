---
name: phab-comments
description: Read Mozilla Phabricator review comments, on one revision or a whole stack, including the "Suggest Edit" inline replacements, printed as diffs against the lines they replace. Use when reading or fetching review comments or reviewer feedback, when asked "what did the reviewer say", when handed a bare D-number like D319190, when a reviewer left a suggested edit to apply, before applying review feedback to a patch, or to sweep a stack — "any new review comments on my stack", "did anyone review my series". Also when posting a review comment, an anchored inline comment, or a reply to one.
---

# Phabricator review comments, including suggested edits

A Mozilla Phabricator inline comment can carry a **suggested edit**: a replacement block the
reviewer types, often with the comment text left **empty** so the whole point of the comment
sits in the suggestion. Conduit's `transaction.search` returns it as `suggestionText` on the
inline, and `phab_comments.py` prints each one as a diff against the lines it replaces. The
`moz` MCP tool `mcp__moz__get_phabricator_revision` shows the suggestion as a bare
replacement block, without the lines it replaces, and reads one revision at a time.

## Usage

Python 3, standard library only. The script lives in
`~/.claude/skills/phab-comments/scripts/`. Each revision argument may be `D321220`,
`321220`, or a revision URL.

```bash
CMT=~/.claude/skills/phab-comments/scripts/phab_comments.py

python3 $CMT D319190                                # every comment on one revision
python3 $CMT D321220 D321216 D321000                # ... on several
python3 $CMT D319190 --inline 1710705
python3 $CMT --stack --summary D321221              # one line per revision of the stack
python3 $CMT --stack --summary D321221 --json
```

| Flag | Effect |
| --- | --- |
| `--stack` | Expand each argument to its whole stack, parents first, from the `stackGraph` the revision search already returns (one extra request). |
| `--summary` | One verdict line per revision plus the general comments' text, instead of every comment. |
| `--json` | Structured output. |
| `--delay N` | Minimum seconds between requests (default 4). Raise it on a 429. |

Reading comments (the default):

| Flag | Effect |
| --- | --- |
| `--skip-done` | Hide inlines the author marked done. They are shown by default, in both modes: a done inline is where an earlier answer sits, and a sweep that hides it answers the reviewer twice. |
| `--inline ID` | Show only that inline comment id; suppresses general comments. |

With `--summary`:

| Flag | Effect |
| --- | --- |
| `--include-self` | Count the revision author's own comments as feedback. |
| `--include-bots` | Count bot comments (Herald, Harbormaster) as feedback. |
| `--quiet-general` | Suppress the general comment text. |

Exit status is 0, 2 if a revision was missing or unreadable, 3 if Phabricator
throttled the run.

**Give several revisions to one invocation.** The script paces itself across every
revision it is given; a shell loop starts a fresh process per revision and paces
nothing, which is what trips the rate limiter.

**For a stack, start with `--stack --summary`.** It costs the same requests as reading
everything and shows which revisions hold feedback; it ends by printing the command
that reads the ones with inline comments. Run that as printed.

## Setup and throttling

A Conduit API token is required, and `~/.arcrc` (which `moz-phab` writes) supplies it, so on a
machine set up for `moz-phab` there is nothing to do. No web session is needed; the token
reads whatever revisions your account can, restricted ones included.

**Mozilla's Fastly edge throttles Conduit by TLS fingerprint, not by API token.** Plain
`urllib`, curl and `moz-phab`'s urllib3 each count against a separate bucket, so a sweep that
trips it does not block `moz-phab submit`, but every `urllib` script on the host (both of this
skill's, `phab.py`, `bot-watch.py`, other sessions' runs) shares one. The scripts pace
themselves (`--delay`), and on the first 429 they send every later call through curl. A write
(`createinline`, `revision.edit`) is never retried on a 5xx, which can arrive after it landed.
If curl is throttled too, the run stops (exit 3); wait several minutes rather than retrying in
a tight loop.

`references/setup-and-throttling.md` covers minting a token, the curl fallback, how to probe
whether the token is throttled, and what a run costs in requests.

## What counts as a comment

The summary's verdict reads the transaction **`type`**, never the comment text.
`transaction.search` returns `type: "comment"` for a general comment and
`type: "inline"` for an inline one; other transaction types come back with a null
type and carry no comments.

**A text-less inline is a suggestion-only comment, not an empty one** -- the reviewer
put everything in the suggested edit. Never report one as blank, and never filter on
comment text to decide whether a revision has feedback: that skips exactly the
comments whose whole content is the edit.

Comments are bucketed by who wrote them, and the verdict counts only the `reviewer`
bucket by default:

- **reviewer** -- a human who is not the revision author. This is the feedback you
  came for, general or inline, done or not.
- **self** -- the revision author's own notes ("Address review: ..."), which
  Phabricator records as comments on every update. Shown, not counted;
  `--include-self` counts them.
- **bot** -- an application PHID (`PHID-APPS-...`). Herald leaves a testing-policy
  notice on nearly every Mozilla revision, so counting bots would flag the entire
  stack and defeat the summary. Shown, not counted; `--include-bots` counts them.
  `reviewbot` has a user PHID and lands in `reviewer`.

Inlines already marked done are counted by default and reported as `(N done)`, on the
grounds that a sweep auditing a full review wants to see them. `--skip-done` drops
them from the verdict.

## Reading the summary output

```
Summary: 10 revision(s), 6 with comments, 5 with inline comments.
  INLINE  D100004  reviewer alice: 2 inline; self you: 2 general; bot Herald: 1 general
  INLINE  D100003  reviewer alice: 4 inline (2 done), 1 general; self you: 2 general
  read   D100001  reviewer alice: 1 general; self you: 4 general; bot Herald: 1 general
    --    D100002  self you: 1 general; bot Herald: 1 general
```

- `INLINE` -- has inline comments, which may carry suggested edits. Read it without
  `--summary`.
- `read` -- has reviewer feedback, but only general comments. Their full text is
  already printed below the table, because the same `transaction.search` returns it.
- `--` -- no reviewer feedback.

A bucket with no comments is omitted from the line, so `--` next to a `bot Herald`
note means the only thing on the revision was Herald's notice.

`--json` gives, per revision, `hasComments`, `hasInlines`, `lastCommentDate` (of the
counted buckets only), per-bucket counts with author names, and the general comment
text; plus a top-level `inline` list and a `missing` list.

**Read the buckets; don't hand-roll a filter over the JSON.** On a repeat sweep the
temptation is to narrow to the reviewer you are corresponding with, or to comments
newer than the last pass. Both hide the thing you are sweeping for: an author filter
loses the second reviewer who commented once, and a timestamp filter loses their
*older* comment that you never read. Done that, reported "no other feedback", and
been wrong -- a reply from a second reviewer sat unread on a revision I had already
declared clear. The `reviewer` / `self` / `bot` split is what that filtering was
reaching for: your own update notes are the noise, and they are already separated.

## Reading the full output

```
browser/components/tabbrowser/docs/gbrowser.md:13  [done, suggested edit]
  sthompson (Stephen Thompson)  2026-08-19 02:18 UTC  id 1710710  diff 1351682
  (no comment text -- this is a suggestion-only inline)
  Suggested edit:
     ## Relationship between browser and gBrowser
     
    -... and `gBrowser` owns the whole set.
    +... and `gBrowser` owns all of the browsers.
```

- Header line: `path:line` (or `path:first-last` for a multi-line anchor) plus
  flags -- `done`, `reply`, `old side` (anchored to the left-hand file),
  `suggested edit`.
- Second line: author, UTC timestamp, inline comment id (pass it to `--inline`), and
  the diff the comment is anchored to.
- The suggestion is a **unified diff** of the anchored lines: `-` lines are the
  reviewer's "before", `+` lines their replacement, leading-space lines are two lines
  of surrounding context that are not part of the edit. Apply only the `-`/`+` part.
  The "before" lines are read from the diff the comment was left on, so when the
  patch has changed since, locate them in the current file by content rather than by
  the line number.
- `Suggested edit (replaces line N; the original lines are not in the diff)` means
  the diff was uploaded with reduced context, so only the replacement is shown; the
  anchor names the lines it replaces.

Inlines are sorted by path then line; general comments are chronological. Comments
already marked done are shown by default; `--skip-done` hides them and prints
`N done (hidden by --skip-done)` in the count line.

`--json` carries each comment's `bucket`, and each inline's `suggestion` as `text`
(the raw replacement), `before` (the replaced lines, or null) and `diff` (the
rendered rows), or null when the inline has none.

## Applying a comment

A comment or suggested edit is a claim about the code, not a settled instruction. Before
editing, load the skill for the subject of the file it anchors to (a design token, a
color, a layout change, a test) and verify the claim with that skill's checks, as you would
your own change. A reviewer's "this line doesn't do much" is a value-neutrality claim to
measure, and "doesn't do much" is not "does nothing".

## Replying or posting

Before replying to any comment or posting an inline, read `references/posting.md`. A published inline can be neither moved nor deleted, and a reply to an inline is itself an inline, anchored with `phab_post_inline.py --reply-to <inline id>`.

## Don't reach for these

- **Bulk-reading comments.** `differential.getrevisioncomments` takes an `ids` array and
  answers in one request, but on Mozilla's install it cannot tell a suggestion-only inline
  from a reviewer-status change, and so reports false positives on most of a stack. One
  `transaction.search` per revision is the floor, which is what the script spends.
- **Marking an inline done.** Web UI only -- Conduit exposes no write for it, so a sweep can
  report what is outstanding but never clear it. The read side (`isDone`) does work, which is
  why the script reports done state.

`references/internals.md` works through both, explains how the suggestion diff is assembled,
and carries the probe that re-enumerates what `differential.revision.edit` accepts in case the
surface has grown.

**A `warning: ... transaction.search returned inlines without hasSuggestion` line means
Phabricator changed and the script needs updating** -- say so instead of reporting the review
as complete.
