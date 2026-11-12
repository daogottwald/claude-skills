---
name: stack-assign-reviewers
description: Distribute a multi-commit stack's review requests across a named set of reviewers by what each has actually reviewed before, keeping the load roughly even, then rewrite every commit's `r?` line safely and update any revisions already up for review. Use when asked to assign or distribute reviewers across a stack or series, to split review load between two or more people, or to replace a blanket group reviewer with named individuals.
---

# Assigning reviewers across a stack

One reviewer per commit, chosen from what that person has demonstrably reviewed
before, with the count roughly even. Rewriting the `r?` lines is local work and
notifies nobody; delivering it to the review server is a separate decision, and for
revisions that already exist it is a reviewer edit rather than a re-submit.

Which group or owner a single *file* has is a different question, and whatever the
repo offers for it is the better tool. What carries over here is the rule that
matters most: **never invent a name.** Take every name from the user, from
`./mach file-info reviewers <paths>`, or from history — `moz-phab` rejects a submit
containing an unknown reviewer, and the amend-and-resubmit costs a round trip.
(`reviewers` is the subcommand that answers which groups a path needs, per file;
`reviewer-groups` only dumps the list of every group that exists.)

## 1. Gather the evidence per candidate

What a reviewer has *landed* is the signal; a pending `r?` proves nothing. Search
the directories the stack touches, one candidate at a time:

```
git log --since="10 months ago" --format='%s' --grep="r=.*<name>" -- <dirs>
```

Read the subjects as **clusters of concern**, not as a count: one candidate comes
back full of process-boundary and gating work, another full of providers, views
and focus semantics. Those clusters are what you match commits against. A
candidate with no hits in these directories is a guess — say so rather than
assigning to them.

## 2. Map each commit to a reviewer

Match the commit's *concern* to a cluster, not its file paths — two commits in one
file can belong to different reviewers. Then:

- **Keep a specialist.** A commit reaching another module keeps that module's
  owner as a blocking co-reviewer (`r?carol!,bob!` for a docshell change driven
  from the address bar).
- **Keep a group where the directory demands one**, and drop the blanket group
  elsewhere — replacing `#foo-reviewers` with a named individual is usually the
  point of the exercise.
- **Balance last.** Count the assignments at the end and use balance only to break
  ties between two plausible reviewers; never to move a commit away from the
  person who owns it.
- Preserve the blocking convention: `!` suffixes an individual or group that must
  approve, `#` prefixes a group.

Show the mapping with one reason per commit before rewriting anything. The reason
is what the user checks; the name alone tells them nothing.

## 3. Rewrite the `r?` lines

Interactive rebase is unavailable, and each commit needs a *different* message, so
drive one subject-keyed script over the range:

```
cat > /tmp/reviewers.json <<'EOF'
[["recent searches", "r?alice!"], ["form history", "r?bob!"]]
EOF
git rebase <base> --exec "~/.claude/skills/stack-assign-reviewers/scripts/assign-reviewers.py /tmp/reviewers.json"
```

Key the mapping on **distinctive subject text**, not commit hashes, which the
rebase invalidates as it goes. The script replaces the whole trailing `r?…` token,
so a subject already carrying `r?carol!,#group!` is rewritten rather than appended
to, and it leaves a subject with no `r?` alone.

**It edits only the subject line, and that is the point.** A rewrite that re-types
the body silently drops the `Differential Revision` trailer, which unlinks the
commit from its revision. The tree and the log look right, and the damage only
surfaces in the submit preview as `(New)` beside a commit that already had a
revision — after which submitting creates a duplicate and orphans the original.
Never hand-roll this step with a heredoc.

## 4. Verify before it reaches anyone

```
python3 ~/.claude/skills/submit-single/scripts/check-commit-message.py <base>..<tip>
printf 'no\n' | moz-phab submit <base>
```

The declined preview is the real check. Confirm **every** commit shows `(D…)` and
not `(New)`, and read the `!!` lines: a `"Changes Planned" will change to "Request
Review"` on each commit means this submit is the ping, so it needs approval that
says so. Declining the prompt leaves the server untouched; step 5 is what reaches
anyone, so stop here unless the user asked for both.

## 5. Put the assignment on the server

The commit rewrite is only half of it: **a revision's title keeps its old `r?`
until you edit it**, so the server still shows the previous reviewer while your log
reads correctly. `moz-phab submit` would fix that, but it re-uploads every diff —
noise on revisions whose content did not change. When the revisions already exist and only
the reviewers changed, edit them through the bundled helper instead, two calls per revision:

```
PHAB=~/.claude/skills/submit-single/scripts/phab.py

python3 $PHAB reviewers D321254 --add 'kcochrane!' --remove '#tabbrowser-reviewers'
python3 $PHAB title D321254 -m "<that commit's own subject>"
```

Both print the exact transactions and send nothing until you pass `--yes`. The helper adds
and removes rather than setting, because **`reviewers.set` replaces the whole list** and a
blocking group Herald attached — `#frontend-codestyle-reviewers` on a lint-config file —
would silently disappear; a `!` suffix makes that review blocking, the same marker the
commit message uses. It also resolves names and groups to PHIDs, and checks `error_code` on
every response, since Conduit answers HTTP 200 on failure.

Two things it does not do for you:

- **Take each title from the local commit's own `%B` subject**, keyed by the
  `Differential Revision` trailer in its message, so the two cannot drift. Build
  that map from `git log --format='%H%x00%B%x1e' <base>..<tip>`.
- **Confirm the username resolved to the right account.** The helper takes the first
  `user.search` hit, and the trap is a person with two accounts, an abandoned one and a
  current one. Read the PHID in the dry run against the account whose reviews step 1
  actually found, and check its `roles` contain `activated`.

**After sending, re-read them** — `python3 $PHAB show D321254 D321255 …` prints the title,
the resolved reviewer names with their blocking marker, and the `diffID` — and confirm each
`diffID` is
**unchanged**: that is the proof this was a reviewer edit and not a re-diff.

`moz-phab submit` is still the right tool when the diffs themselves changed, or the
revisions do not exist yet — scoping the range, the update note and re-threading
the dependency edges are then the `submit-stack` skill.
